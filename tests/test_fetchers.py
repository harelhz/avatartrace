"""Avatar fetchers and CDN resolvers against mocked HTTP responses."""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import respx

from avatartrace.config import Settings
from avatartrace.extraction import default_registry
from avatartrace.extraction.cdn import CdnResolver
from avatartrace.models import Account
from avatartrace.net import HttpClient
from avatartrace.verification import AvatarFetcher, AvatarResult
from avatartrace.verification import html as htmlx
from avatartrace.verification.fetchers import json_path
from tests.conftest import make_settings

REGISTRY = default_registry()
Bytes = Callable[[str], bytes]


def account(url: str) -> Account:
    found = REGISTRY.extract(url)
    assert found is not None, url
    return found


async def fetch(url: str, settings: Settings | None = None) -> AvatarResult:
    settings = settings or make_settings()
    async with HttpClient(settings) as http:
        return await AvatarFetcher(REGISTRY, http, settings).fetch(account(url))


def page(head: str) -> str:
    return f"<!doctype html><html><head><title>t</title>{head}</head><body><p>hello</p></body></html>"


# ------------------------------------------------------------------ HTML extractors


def test_og_image_variants() -> None:
    base = "https://site.example/u/jdoe"
    assert (
        htmlx.og_image(page('<meta property="og:image" content="https://cdn.example/a.jpg">'), base)
        == "https://cdn.example/a.jpg"
    )
    assert (
        htmlx.og_image(page("<meta content='/img/a.jpg' property='og:image' />"), base)
        == "https://site.example/img/a.jpg"
    )
    assert (
        htmlx.og_image(page('<meta name="twitter:image" content="//cdn.example/t.png">'), base)
        == "https://cdn.example/t.png"
    )
    assert (
        htmlx.og_image(page('<link rel="image_src" href="https://cdn.example/l.png">'), base)
        == "https://cdn.example/l.png"
    )
    assert (
        htmlx.og_image(page('<meta property="og:image" content="https://a.example/x.jpg?a=1&amp;b=2">'), base)
        == "https://a.example/x.jpg?a=1&b=2"
    )
    both = page(
        '<meta name="twitter:image" content="https://cdn.example/tw.png">'
        '<meta property="og:image" content="https://cdn.example/og.png">'
        '<meta property="og:image:secure_url" content="https://cdn.example/secure.png">'
    )
    assert htmlx.og_image(both, base) == "https://cdn.example/secure.png"
    assert htmlx.og_image(page('<meta property="og:title" content="x">'), base) is None
    assert htmlx.og_image(page('<meta property="og:image" content="">'), base) is None
    assert htmlx.og_image("<<<not html at all", base) is None


def test_ld_json_profile_page_and_person() -> None:
    base = "https://site.example/jdoe"
    profile = page(
        '<script type="application/ld+json">{"@context":"https://schema.org/","@type":"ProfilePage",'
        '"mainEntity":{"@type":"Person","name":"J","image":{"@type":"ImageObject","contentUrl":"https://i.example/280.jpg"}}}</script>'
    )
    assert htmlx.ld_json_image(profile, base) == "https://i.example/280.jpg"
    person = page(
        '<script type="application/ld+json">[{"@type":"WebSite"},{"@type":"Person","image":"/me.png"}]</script>'
    )
    assert htmlx.ld_json_image(person, base) == "https://site.example/me.png"
    graph = page(
        '<script type="application/ld+json">{"@graph":[{"@type":["Thing","MusicGroup"],"image":["https://i.example/band.jpg"]}]}</script>'
    )
    assert htmlx.ld_json_image(graph, base) == "https://i.example/band.jpg"
    article = page(
        '<script type="application/ld+json">{"@type":"Article","image":"https://i.example/post.jpg"}</script>'
    )
    assert htmlx.ld_json_image(article, base) is None
    broken = page('<script type="application/ld+json">{not json</script>')
    assert htmlx.ld_json_image(broken, base) is None


def test_regex_image_unescapes_json_and_html() -> None:
    pattern = re.compile(r'"avatar":"(https?:[^"]+)"')
    base = "https://site.example/"
    assert (
        htmlx.regex_image(r'{"avatar":"https:\/\/c.example\/a.png?x=1&y=2"}', pattern, base)
        == "https://c.example/a.png?x=1&y=2"
    )
    assert (
        htmlx.regex_image('{"avatar":"https://c.example/a.png?x=1&amp;y=2"}', pattern, base)
        == "https://c.example/a.png?x=1&y=2"
    )
    assert htmlx.regex_image('{"other":"x"}', pattern, base) is None
    assert htmlx.regex_image("nothing", re.compile("no-group"), base) is None


def test_json_path() -> None:
    data = {"a": {"b": [{"c": "x"}, {"c": "y"}]}, "list": ["z"], "n": None}
    assert json_path(data, "a.b[1].c") == "y"
    assert json_path([{"u": "first"}], "[0].u") == "first"
    assert json_path(data, "list[0]") == "z"
    assert json_path(data, "a.b[5].c") is None
    assert json_path(data, "a.missing.c") is None
    assert json_path(data, "n.x") is None
    assert json_path(data, "list.key") is None
    assert json_path("scalar", "a") is None


# ------------------------------------------------------------------ JSON API sources


async def test_github_api_avatar(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    api = mock_http.get("https://api.github.com/users/octocat").respond(
        200, json={"login": "octocat", "id": 583231, "avatar_url": "https://avatars.githubusercontent.com/u/583231?v=4"}
    )
    mock_http.get("https://avatars.githubusercontent.com/u/583231?v=4").respond(200, content=fixture_bytes("logo.png"))
    result = await fetch("https://github.com/octocat")
    assert result.status == "ok"
    assert result.image_url == "https://avatars.githubusercontent.com/u/583231?v=4"
    assert result.data == fixture_bytes("logo.png")
    assert "authorization" not in api.calls.last.request.headers

    await fetch("https://github.com/octocat", make_settings(github_token="ghp_test"))
    assert api.calls.last.request.headers["authorization"] == "Bearer ghp_test"


async def test_github_missing_account_and_rate_limit(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://api.github.com/users/ghost-user").respond(404, json={"message": "Not Found"})
    assert (await fetch("https://github.com/ghost-user")).status == "not_found"
    mock_http.get("https://api.github.com/users/limited").respond(403, json={"message": "API rate limit exceeded"})
    result = await fetch("https://github.com/limited")
    assert result.status == "unavailable"
    assert "GITHUB_TOKEN" in (result.reason or "")


async def test_bluesky_and_missing_handle(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    api = mock_http.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile")
    api.respond(
        200,
        json={
            "did": "did:plc:abc",
            "handle": "jdoe.bsky.social",
            "avatar": "https://cdn.bsky.app/img/avatar/plain/did:plc:abc/x@jpeg",
        },
    )
    mock_http.get("https://cdn.bsky.app/img/avatar/plain/did:plc:abc/x@jpeg").respond(
        200, content=fixture_bytes("logo.png")
    )
    result = await fetch("https://bsky.app/profile/jdoe.bsky.social")
    assert result.status == "ok"
    assert parse_qs(urlsplit(str(api.calls.last.request.url)).query) == {"actor": ["jdoe.bsky.social"]}

    api.respond(400, json={"error": "InvalidRequest", "message": "Profile not found"})
    assert (await fetch("https://bsky.app/profile/nobody.bsky.social")).status == "not_found"

    api.respond(200, json={"did": "did:plc:abc", "handle": "noavatar.bsky.social"})
    result = await fetch("https://bsky.app/profile/noavatar.bsky.social")
    assert result.status == "unavailable"
    assert "no profile picture" in (result.reason or "")


async def test_mastodon_uses_the_accounts_own_instance(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    lookup = mock_http.get("https://hachyderm.io/api/v1/accounts/lookup").respond(
        200,
        json={
            "id": "1",
            "username": "jdoe",
            "avatar": "https://media.hachyderm.io/a.gif",
            "avatar_static": "https://media.hachyderm.io/a.png",
        },
    )
    mock_http.get("https://media.hachyderm.io/a.png").respond(200, content=fixture_bytes("logo.png"))
    result = await fetch("https://mastodon.social/@jdoe@hachyderm.io")
    assert result.status == "ok"
    assert result.image_url == "https://media.hachyderm.io/a.png"
    assert parse_qs(urlsplit(str(lookup.calls.last.request.url)).query) == {"acct": ["jdoe"]}


async def test_mastodon_default_avatar_is_not_compared(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://mastodon.social/api/v1/accounts/lookup").respond(
        200,
        json={
            "username": "new",
            "avatar": "https://mastodon.social/avatars/original/missing.png",
            "avatar_static": "https://mastodon.social/avatars/original/missing.png",
        },
    )
    result = await fetch("https://mastodon.social/@new")
    assert result.status == "unavailable"
    assert "placeholder" in (result.reason or "")


async def test_generic_fediverse_host_that_is_not_mastodon_is_not_found(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://blog.example.org/api/v1/accounts/lookup").respond(404, text="Not Found")
    assert (await fetch("https://blog.example.org/@someone")).status == "not_found"


async def test_gitlab_list_response_and_relative_urls(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://gitlab.com/api/v4/users").mock(
        side_effect=[
            httpx.Response(
                200, json=[{"username": "jdoe", "avatar_url": "/uploads/-/system/user/avatar/7/avatar.png"}]
            ),
            httpx.Response(200, json=[]),
        ]
    )
    mock_http.get("https://gitlab.com/uploads/-/system/user/avatar/7/avatar.png").respond(
        200, content=fixture_bytes("logo.png")
    )
    result = await fetch("https://gitlab.com/jdoe")
    assert result.image_url == "https://gitlab.com/uploads/-/system/user/avatar/7/avatar.png"
    assert (await fetch("https://gitlab.com/jdoe")).status == "unavailable"


async def test_dynamic_json_key_uses_the_username(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://api.freecodecamp.org/users/get-public-profile").respond(
        200, json={"entities": {"user": {"jdoe": {"picture": "https://img.example/fcc.png"}}}, "result": "jdoe"}
    )
    mock_http.get("https://img.example/fcc.png").respond(200, content=fixture_bytes("logo.png"))
    assert (await fetch("https://www.freecodecamp.org/JDoe")).image_url == "https://img.example/fcc.png"


async def test_json_alternatives_are_tried_in_order(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://api.mixcloud.com/jdoe/").respond(
        200, json={"pictures": {"medium": "https://m.example/100.png", "large": "https://m.example/300.png"}}
    )
    mock_http.get("https://m.example/300.png").respond(200, content=fixture_bytes("logo.png"))
    assert (await fetch("https://www.mixcloud.com/jdoe/")).image_url == "https://m.example/300.png"


# ------------------------------------------------------------------ HTML sources


async def test_og_image_from_public_profile(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://www.instagram.com/john.doe/").respond(
        200, html=page('<meta property="og:image" content="https://scontent.cdninstagram.com/v/t51/p.jpg?a=1&amp;b=2">')
    )
    image = mock_http.get("https://scontent.cdninstagram.com/v/t51/p.jpg?a=1&b=2").respond(
        200, content=fixture_bytes("face_a.jpg")
    )
    result = await fetch("https://www.instagram.com/john.doe/")
    assert result.status == "ok"
    assert image.called


async def test_login_wall_blocks_and_missing_pictures_are_unavailable(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://www.instagram.com/private.person/").respond(
        302, headers={"location": "https://www.instagram.com/accounts/login/?next=%2Fprivate.person%2F"}
    )
    mock_http.get("https://www.instagram.com/accounts/login/").respond(200, html=page(""))
    result = await fetch("https://www.instagram.com/private.person/")
    assert result.status == "unavailable"
    assert "login wall" in (result.reason or "")

    mock_http.get("https://www.linkedin.com/in/someone-123/").respond(999)
    result = await fetch("https://www.linkedin.com/in/someone-123/")
    assert result.status == "unavailable"
    assert "999" in (result.reason or "")

    mock_http.get("https://www.tiktok.com/@someone").respond(200, html="<html><script>challenge()</script></html>")
    result = await fetch("https://www.tiktok.com/@someone")
    assert result.status == "unavailable"
    assert "does not expose" in (result.reason or "")

    mock_http.get("https://t.me/emptyuser").respond(
        200, html=page('<meta property="og:image" content="https://telegram.org/img/t_logo_2x.png">')
    )
    result = await fetch("https://t.me/emptyuser")
    assert result.status == "unavailable"
    assert "placeholder" in (result.reason or "")

    mock_http.get("https://www.instagram.com/gone.user/").respond(404)
    assert (await fetch("https://www.instagram.com/gone.user/")).status == "not_found"

    mock_http.get("https://www.twitch.tv/limited").respond(429)
    assert "rate limited" in ((await fetch("https://www.twitch.tv/limited")).reason or "")


async def test_schema_org_profile_image(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://www.pinterest.com/jdoe/").respond(
        200,
        html=page(
            '<meta property="og:image" content="https://s.pinimg.com/images/default_open_graph_1200.png">'
            '<script type="application/ld+json">{"@type":"ProfilePage","mainEntity":{"@type":"Person",'
            '"image":{"@type":"ImageObject","contentUrl":"https://i.pinimg.com/280x280_RS/aa/bb/cc.jpg"}}}</script>'
        ),
    )
    mock_http.get("https://i.pinimg.com/280x280_RS/aa/bb/cc.jpg").respond(200, content=fixture_bytes("logo.png"))
    assert (await fetch("https://www.pinterest.com/jdoe/")).image_url == "https://i.pinimg.com/280x280_RS/aa/bb/cc.jpg"


async def test_x_preload_hint_is_upgraded_to_full_size(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    html = page(
        '<link rel="preload" as="image" imageSrcSet="https://pbs.twimg.com/profile_banners/1/2/600x200 600w"/>'
        '<link rel="preload" as="image" imageSrcSet="https://pbs.twimg.com/profile_images/195/OSHY3ewP_mini.jpg 24w, '
        'https://pbs.twimg.com/profile_images/195/OSHY3ewP_normal.jpg 48w"/>'
        '<meta property="og:image" content="https://pbs.twimg.com/profile_banners/1/2">'
    )
    mock_http.get("https://x.com/johndoe").respond(200, html=html)
    mock_http.get("https://pbs.twimg.com/profile_images/195/OSHY3ewP_400x400.jpg").respond(
        200, content=fixture_bytes("face_a.jpg")
    )
    result = await fetch("https://twitter.com/JohnDoe")
    assert result.status == "ok"
    assert result.image_url == "https://pbs.twimg.com/profile_images/195/OSHY3ewP_400x400.jpg"


async def test_linktree_embedded_json_and_steam_xml(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://linktr.ee/jdoe").respond(
        200,
        html=page(
            '<script id="__NEXT_DATA__" type="application/json">{"props":{"profilePictureUrl":"https:\\u002F\\u002Fugc.production.linktr.ee\\u002Fabc.png"}}</script>'
        ),
    )
    mock_http.get("https://ugc.production.linktr.ee/abc.png").respond(200, content=fixture_bytes("logo.png"))
    assert (await fetch("https://linktr.ee/jdoe")).image_url == "https://ugc.production.linktr.ee/abc.png"

    xml = mock_http.get("https://steamcommunity.com/id/jdoe?xml=1").respond(
        200,
        text="<profile><steamID64>1</steamID64><avatarFull><![CDATA[https://avatars.fastly.steamstatic.com/abc_full.jpg]]></avatarFull></profile>",
    )
    mock_http.get("https://avatars.fastly.steamstatic.com/abc_full.jpg").respond(200, content=fixture_bytes("logo.png"))
    result = await fetch("https://steamcommunity.com/id/jdoe")
    assert result.status == "ok"
    assert xml.called

    mock_http.get("https://steamcommunity.com/id/nobody?xml=1").respond(
        200, text="<response><error>The specified profile could not be found.</error></response>"
    )
    assert (await fetch("https://steamcommunity.com/id/nobody")).status == "unavailable"


async def test_direct_image_source(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://api.tumblr.com/v2/blog/jdoe.tumblr.com/avatar/512").respond(
        200, content=fixture_bytes("logo.png")
    )
    result = await fetch("https://jdoe.tumblr.com/")
    assert result.status == "ok"
    mock_http.get("https://api.tumblr.com/v2/blog/gone.tumblr.com/avatar/512").respond(
        404, json={"meta": {"status": 404}}
    )
    result = await fetch("https://gone.tumblr.com/")
    assert result.status == "unavailable"
    assert "could not download" in (result.reason or "")


async def test_platform_without_any_source_reports_its_note() -> None:
    result = await fetch("https://dribbble.com/jdoe")
    assert result.status == "unavailable"
    assert "Dribbble" in (result.reason or "")


async def test_picture_download_failure_is_unavailable(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://api.github.com/users/jdoe").respond(
        200, json={"avatar_url": "https://avatars.githubusercontent.com/u/1"}
    )
    mock_http.get("https://avatars.githubusercontent.com/u/1").respond(403)
    result = await fetch("https://github.com/jdoe")
    assert result.status == "unavailable"
    assert "could not download the picture" in (result.reason or "")


# ------------------------------------------------------------------ keyed official APIs


async def test_youtube_api_is_used_only_with_a_key(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    api = mock_http.get("https://www.googleapis.com/youtube/v3/channels").respond(
        200,
        json={
            "items": [
                {
                    "snippet": {
                        "thumbnails": {
                            "default": {"url": "https://yt3.ggpht.com/d.jpg"},
                            "high": {"url": "https://yt3.ggpht.com/h.jpg"},
                        }
                    }
                }
            ]
        },
    )
    mock_http.get("https://yt3.ggpht.com/h.jpg").respond(200, content=fixture_bytes("logo.png"))
    page_route = mock_http.get("https://www.youtube.com/@johndoe").respond(
        200, html=page('<meta property="og:image" content="https://yt3.googleusercontent.com/og.jpg">')
    )
    mock_http.get("https://yt3.googleusercontent.com/og.jpg").respond(200, content=fixture_bytes("logo.png"))

    without_key = await fetch("https://www.youtube.com/@JohnDoe")
    assert without_key.image_url == "https://yt3.googleusercontent.com/og.jpg"
    assert not api.called

    keyed = make_settings(youtube_api_key="yt-key")
    with_key = await fetch("https://www.youtube.com/@JohnDoe", keyed)
    assert with_key.image_url == "https://yt3.ggpht.com/h.jpg"
    request = api.calls.last.request
    assert request.headers["x-goog-api-key"] == "yt-key"
    assert parse_qs(urlsplit(str(request.url)).query) == {"part": ["snippet"], "forHandle": ["@johndoe"]}
    assert page_route.call_count == 1

    await fetch("https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw", keyed)
    assert parse_qs(urlsplit(str(api.calls.last.request.url)).query)["id"] == ["UCuAXFkgsw1L7xaCfnd5JJOw"]

    api.respond(200, json={"items": []})
    assert (await fetch("https://www.youtube.com/@JohnDoe", keyed)).status == "not_found"


async def test_youtube_api_failure_falls_back_to_the_public_page(
    mock_http: respx.MockRouter, fixture_bytes: Bytes
) -> None:
    mock_http.get("https://www.googleapis.com/youtube/v3/channels").respond(403, json={"error": {"message": "quota"}})
    mock_http.get("https://www.youtube.com/@johndoe").respond(
        200, html=page('<meta property="og:image" content="https://yt3.googleusercontent.com/og.jpg">')
    )
    mock_http.get("https://yt3.googleusercontent.com/og.jpg").respond(200, content=fixture_bytes("logo.png"))
    result = await fetch("https://www.youtube.com/@JohnDoe", make_settings(youtube_api_key="yt-key"))
    assert result.status == "ok"
    assert result.image_url == "https://yt3.googleusercontent.com/og.jpg"


async def test_twitch_api_with_app_token(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    token = mock_http.post("https://id.twitch.tv/oauth2/token").respond(
        200, json={"access_token": "app-token", "expires_in": 5000}
    )
    users = mock_http.get("https://api.twitch.tv/helix/users").respond(
        200,
        json={
            "data": [
                {
                    "login": "johndoe",
                    "profile_image_url": "https://static-cdn.jtvnw.net/jtv_user_pictures/x-300x300.png",
                }
            ]
        },
    )
    mock_http.get("https://static-cdn.jtvnw.net/jtv_user_pictures/x-300x300.png").respond(
        200, content=fixture_bytes("logo.png")
    )
    settings = make_settings(twitch_client_id="cid", twitch_client_secret="csecret")
    async with HttpClient(settings) as http:
        fetcher = AvatarFetcher(REGISTRY, http, settings)
        first = await fetcher.fetch(account("https://www.twitch.tv/johndoe"))
        second = await fetcher.fetch(account("https://www.twitch.tv/johndoe"))
    assert first.status == second.status == "ok"
    assert token.call_count == 1, "the app token is cached for the run"
    assert b"grant_type=client_credentials" in token.calls.last.request.content
    request = users.calls.last.request
    assert request.headers["authorization"] == "Bearer app-token"
    assert request.headers["client-id"] == "cid"

    users.respond(200, json={"data": []})
    assert (await fetch("https://www.twitch.tv/nobody_here", settings)).status == "not_found"


async def test_x_api_with_bearer_token(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    api = mock_http.get("https://api.x.com/2/users/by/username/johndoe").respond(
        200,
        json={
            "data": {
                "id": "1",
                "username": "johndoe",
                "profile_image_url": "https://pbs.twimg.com/profile_images/9/a_normal.jpg",
            }
        },
    )
    mock_http.get("https://pbs.twimg.com/profile_images/9/a_400x400.jpg").respond(
        200, content=fixture_bytes("logo.png")
    )
    settings = make_settings(x_bearer_token="xb")
    result = await fetch("https://x.com/johndoe", settings)
    assert result.image_url == "https://pbs.twimg.com/profile_images/9/a_400x400.jpg"
    assert api.calls.last.request.headers["authorization"] == "Bearer xb"

    api.respond(200, json={"errors": [{"title": "Not Found Error", "detail": "Could not find user"}]})
    assert (await fetch("https://x.com/johndoe", settings)).status == "not_found"


async def test_reddit_api_with_app_credentials(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    token = mock_http.post("https://www.reddit.com/api/v1/access_token").respond(
        200, json={"access_token": "rt", "token_type": "bearer"}
    )
    about = mock_http.get("https://oauth.reddit.com/user/jdoe_99/about").respond(
        200,
        json={
            "kind": "t2",
            "data": {
                "name": "jdoe_99",
                "snoovatar_img": "",
                "icon_img": "https://styles.redditmedia.com/t5_x/styles/profileIcon_a.png?width=256&amp;s=abc",
            },
        },
    )
    mock_http.get("https://styles.redditmedia.com/t5_x/styles/profileIcon_a.png?width=256&s=abc").respond(
        200, content=fixture_bytes("logo.png")
    )
    settings = make_settings(reddit_client_id="rid", reddit_client_secret="rsecret")
    result = await fetch("https://www.reddit.com/user/jdoe_99/", settings)
    assert result.status == "ok"
    expected = "Basic " + base64.b64encode(b"rid:rsecret").decode()
    assert token.calls.last.request.headers["authorization"] == expected
    assert about.calls.last.request.headers["authorization"] == "Bearer rt"


async def test_reddit_without_credentials_is_unavailable(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://www.reddit.com/user/jdoe_99/").respond(200, html="<html><title>Reddit</title></html>")
    result = await fetch("https://www.reddit.com/user/jdoe_99/")
    assert result.status == "unavailable"


async def test_failed_api_login_falls_through(mock_http: respx.MockRouter) -> None:
    mock_http.post("https://www.reddit.com/api/v1/access_token").respond(401, json={"error": "invalid_grant"})
    mock_http.get("https://www.reddit.com/user/jdoe_99/").respond(403)
    settings = make_settings(reddit_client_id="rid", reddit_client_secret="bad")
    result = await fetch("https://www.reddit.com/user/jdoe_99/", settings)
    assert result.status == "unavailable"
    assert "login failed" in (result.reason or "")


# ------------------------------------------------------------------ avatar CDN -> account


async def resolve(url: str) -> Account | None:
    settings = make_settings()
    async with HttpClient(settings) as http:
        return await CdnResolver(REGISTRY, http, settings).resolve(url)


async def test_cdn_github_id_is_resolved_and_cached(mock_http: respx.MockRouter) -> None:
    api = mock_http.get("https://api.github.com/user/583231").respond(200, json={"login": "octocat", "id": 583231})
    settings = make_settings()
    async with HttpClient(settings) as http:
        resolver = CdnResolver(REGISTRY, http, settings)
        first = await resolver.resolve("https://avatars.githubusercontent.com/u/583231?v=4")
        second = await resolver.resolve("https://avatars2.githubusercontent.com/u/583231?s=64")
    assert first is not None
    assert first.key == ("github", "", "octocat")
    assert second == first
    assert api.call_count == 1


async def test_cdn_lookup_failures_give_nothing(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://api.github.com/user/1").respond(404, json={"message": "Not Found"})
    mock_http.get("https://api.github.com/user/2").respond(200, json={"unexpected": True})
    assert await resolve("https://avatars.githubusercontent.com/u/1") is None
    assert await resolve("https://avatars.githubusercontent.com/u/2") is None
    assert await resolve("https://example.com/avatar.png") is None


async def test_cdn_direct_mappings_need_no_request(mock_http: respx.MockRouter) -> None:
    by_login = await resolve("https://avatars.githubusercontent.com/octocat")
    by_did = await resolve("https://cdn.bsky.app/img/avatar/plain/did:plc:z72i7hdynmk6r22z27h6tvur/bafk@jpeg")
    assert by_login is not None
    assert by_login.username == "octocat"
    assert by_did is not None
    assert by_did.platform == "bluesky"
    assert not mock_http.calls


async def test_cdn_mastodon_and_gravatar(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://mastodon.social/api/v1/accounts/1").respond(
        200, json={"id": "1", "username": "Gargron", "acct": "Gargron", "url": "https://mastodon.social/@Gargron"}
    )
    found = await resolve("https://files.mastodon.social/accounts/avatars/000/000/001/original/6b.png")
    assert found is not None
    assert found.username == "gargron@mastodon.social"

    digest = "ab" * 32
    mock_http.get(f"https://api.gravatar.com/v3/profiles/{digest}").respond(
        200, json={"profile_url": "https://gravatar.com/jdoe", "avatar_url": "x"}
    )
    found = await resolve(f"https://secure.gravatar.com/avatar/{digest}?s=128")
    assert found is not None
    assert found.key == ("gravatar", "", "jdoe")
