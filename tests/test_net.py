"""The HTTP layer: politeness, limits and uniform errors."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from avatartrace import net
from avatartrace.net import FetchError, HostRateLimiter, HttpClient, is_public_url, site_key
from tests.conftest import make_settings


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a", True),
        ("http://sub.example.co.uk/", True),
        ("https://8.8.8.8/x", True),
        ("https://localhost/x", False),
        ("https://127.0.0.1/x", False),
        ("https://10.0.0.5/x", False),
        ("https://192.168.1.1/x", False),
        ("https://169.254.169.254/latest/meta-data/", False),
        ("https://[::1]/x", False),
        ("https://intranet/x", False),
        ("https://printer.local/x", False),
        ("https://db.internal/x", False),
        ("ftp://example.com/x", False),
        ("file:///etc/passwd", False),
        ("javascript:alert(1)", False),
        ("", False),
        ("https://", False),
    ],
)
def test_is_public_url(url: str, expected: bool) -> None:
    assert is_public_url(url) is expected


def test_site_key_groups_subdomains() -> None:
    assert site_key("api.github.com") == "github.com"
    assert site_key("GitHub.com.") == "github.com"
    assert site_key("8.8.8.8") == "8.8.8.8"
    assert site_key("localhost") == "localhost"


async def test_rate_limiter_spaces_requests_to_the_same_site() -> None:
    limiter = HostRateLimiter(0.05)
    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.gather(*(limiter.wait(host) for host in ("a.example.com", "b.example.com", "example.com")))
    assert loop.time() - start >= 0.095  # three requests to one site: two gaps


async def test_rate_limiter_does_not_delay_different_sites() -> None:
    limiter = HostRateLimiter(0.5)
    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.gather(limiter.wait("one.example"), limiter.wait("two.example"))
    assert loop.time() - start < 0.2


def test_rate_limiter_overrides_only_slow_down() -> None:
    limiter = HostRateLimiter(1.0, {"slow.example": 5.0, "fast.example": 0.1})
    assert limiter.interval_for("www.slow.example") == 5.0
    assert limiter.interval_for("fast.example") == 1.0
    assert HostRateLimiter(0.0, {"slow.example": 5.0}).interval_for("slow.example") == 0.0


async def test_sends_an_honest_user_agent(mock_http: respx.MockRouter) -> None:
    route = mock_http.get("https://example.com/").respond(200, text="ok")
    async with HttpClient(make_settings()) as http:
        await http.fetch("https://example.com/")
    agent = route.calls.last.request.headers["user-agent"]
    assert agent.startswith("avatartrace/")
    assert "github.com" in agent
    assert "Mozilla" not in agent


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (404, "not_found"),
        (410, "not_found"),
        (401, "blocked"),
        (403, "blocked"),
        (999, "blocked"),
        (429, "rate_limited"),
        (418, "error"),
    ],
)
async def test_status_codes_map_to_error_kinds(mock_http: respx.MockRouter, status: int, kind: str) -> None:
    route = mock_http.get("https://example.com/p").respond(status)
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError) as caught:
            await http.fetch("https://example.com/p")
    assert caught.value.kind == kind
    assert caught.value.status == status
    assert route.call_count == 1, "refusals must never be retried"


async def test_server_errors_are_retried_once(mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "RETRY_DELAY", 0.0)
    route = mock_http.get("https://example.com/flaky").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, text="fine")]
    )
    async with HttpClient(make_settings()) as http:
        fetched = await http.fetch("https://example.com/flaky")
    assert fetched.text() == "fine"
    assert route.call_count == 2


async def test_persistent_server_error_is_reported(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(net, "RETRY_DELAY", 0.0)
    route = mock_http.get("https://example.com/down").respond(600)
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError) as caught:
            await http.fetch("https://example.com/down")
    assert caught.value.kind == "server_error"
    assert route.call_count == 2


async def test_posts_are_never_retried(mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "RETRY_DELAY", 0.0)
    route = mock_http.post("https://example.com/api").respond(503)
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError):
            await http.fetch("https://example.com/api", method="POST", json={"a": 1})
    assert route.call_count == 1


async def test_network_errors_are_retried_then_reported(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(net, "RETRY_DELAY", 0.0)
    route = mock_http.get("https://example.com/x").mock(side_effect=httpx.ConnectError("boom"))
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError) as caught:
            await http.fetch("https://example.com/x")
    assert caught.value.kind == "network"
    assert route.call_count == 2
    mock_http.get("https://example.com/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError) as caught:
            await http.fetch("https://example.com/slow")
    assert caught.value.kind == "timeout"


async def test_accepted_error_status_is_returned(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://example.com/api").respond(400, json={"error": "bad key"})
    async with HttpClient(make_settings()) as http:
        fetched = await http.fetch("https://example.com/api", accept_status=frozenset({400}))
    assert fetched.status == 400
    assert fetched.json() == {"error": "bad key"}


async def test_oversized_responses_are_refused(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://example.com/big").respond(200, content=b"x" * 5000)
    mock_http.get("https://example.com/lying").mock(
        return_value=httpx.Response(200, content=b"y" * 5000, headers={"content-length": "10"})
    )
    async with HttpClient(make_settings()) as http:
        for url in ("https://example.com/big", "https://example.com/lying"):
            with pytest.raises(FetchError) as caught:
                await http.fetch(url, max_bytes=1000)
            assert caught.value.kind == "too_large"


async def test_login_redirect_is_reported_as_a_login_wall(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://social.example/@someone").respond(
        302, headers={"location": "https://social.example/login/?next=%2F%40someone"}
    )
    mock_http.get("https://social.example/login/").respond(200, text="<form>sign in</form>")
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError) as caught:
            await http.fetch("https://social.example/@someone")
    assert caught.value.kind == "login_wall"


async def test_a_direct_request_to_a_login_like_url_is_not_a_login_wall(mock_http: respx.MockRouter) -> None:
    mock_http.post("https://id.example.com/oauth2/token").respond(200, json={"access_token": "t"})
    async with HttpClient(make_settings()) as http:
        fetched = await http.fetch("https://id.example.com/oauth2/token", method="POST", data={"a": "b"})
    assert fetched.json() == {"access_token": "t"}


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/x", "http://localhost/x", "file:///etc/passwd", "nope"])
async def test_private_addresses_are_refused_without_a_request(mock_http: respx.MockRouter, url: str) -> None:
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError) as caught:
            await http.fetch(url)
    assert caught.value.kind == "invalid"
    assert not mock_http.calls


async def test_redirects_to_private_addresses_are_refused(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://example.com/r").respond(302, headers={"location": "http://169.254.169.254/latest/"})
    internal = mock_http.get("http://169.254.169.254/latest/").respond(200, text="secret")
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError):
            await http.fetch("https://example.com/r")
    assert internal.call_count == 0


async def test_get_json_rejects_non_json(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://example.com/j").respond(200, text="<html>")
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError) as caught:
            await http.get_json("https://example.com/j")
    assert caught.value.kind == "invalid"


async def test_get_image_rejects_empty_bodies(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://example.com/i.png").respond(200, content=b"")
    async with HttpClient(make_settings()) as http:
        with pytest.raises(FetchError):
            await http.get_image("https://example.com/i.png")


async def test_request_counter(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://example.com/a").respond(200)
    async with HttpClient(make_settings()) as http:
        await http.fetch("https://example.com/a")
        await http.fetch("https://example.com/a")
        assert http.request_count == 2


def test_text_decoding_falls_back_on_unknown_charset() -> None:
    fetched = net.Fetched(200, "https://e.x/", {"content-type": "text/html; charset=does-not-exist"}, "héllo".encode())
    assert fetched.text() == "héllo"
    assert fetched.content_type == "text/html"


# ------------------------------------------------------------------ DNS-level guard


def fake_dns(table: dict[str, list[str]], calls: list[str]):  # type: ignore[no-untyped-def]
    async def resolve(host: str) -> list[str]:
        calls.append(host)
        if host not in table:
            raise OSError("name or service not known")
        return table[host]

    return resolve


async def test_hosts_resolving_to_private_addresses_are_refused(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    table = {
        "public.example": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"],
        "rebind.example": ["127.0.0.1"],
        "mixed.example": ["93.184.216.34", "10.0.0.7"],
        "metadata.example": ["169.254.169.254"],
        "v6local.example": ["::1"],
    }
    monkeypatch.setattr(net, "resolve_host", fake_dns(table, calls))
    public = mock_http.get("https://public.example/a").respond(200, text="ok")
    blocked = [mock_http.get(f"https://{host}/a").respond(200, text="secret") for host in list(table)[1:]]
    async with HttpClient(make_settings(check_dns=True)) as http:
        assert (await http.fetch("https://public.example/a")).text() == "ok"
        await http.fetch("https://public.example/a")
        for host in list(table)[1:]:
            with pytest.raises(FetchError, match="private address") as caught:
                await http.fetch(f"https://{host}/a")
            assert caught.value.kind == "invalid"
        with pytest.raises(FetchError, match="could not resolve"):
            await http.fetch("https://nxdomain.example/a")
    assert public.call_count == 2
    assert calls.count("public.example") == 1, "resolution results are cached for the run"
    assert all(route.call_count == 0 for route in blocked)


async def test_redirect_to_a_host_with_a_private_address_is_refused(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = {"public.example": ["93.184.216.34"], "rebind.example": ["192.168.1.20"]}
    monkeypatch.setattr(net, "resolve_host", fake_dns(table, []))
    mock_http.get("https://public.example/go").respond(302, headers={"location": "https://rebind.example/admin"})
    internal = mock_http.get("https://rebind.example/admin").respond(200, text="secret")
    async with HttpClient(make_settings(check_dns=True)) as http:
        with pytest.raises(FetchError, match="private address"):
            await http.fetch("https://public.example/go")
    assert internal.call_count == 0


async def test_literal_public_addresses_need_no_lookup(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(net, "resolve_host", fake_dns({}, calls))
    mock_http.get("https://93.184.216.34/a").respond(200, text="ok")
    async with HttpClient(make_settings(check_dns=True)) as http:
        assert (await http.fetch("https://93.184.216.34/a")).text() == "ok"
    assert calls == []
