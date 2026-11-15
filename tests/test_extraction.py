"""URL -> (platform, username) parsing for every supported platform."""

from __future__ import annotations

import json

import pytest

from avatartrace.extraction import PlatformDataError, PlatformRegistry, default_registry

REGISTRY = default_registry()

P = "profile"
C = "content"

# (url, platform, canonical username, kind)
MATCHES: list[tuple[str, str, str, str]] = [
    # --- Instagram
    ("https://www.instagram.com/john.doe/", "instagram", "john.doe", P),
    ("https://instagram.com/John.Doe?hl=en", "instagram", "john.doe", P),
    ("instagram.com/john.doe", "instagram", "john.doe", P),
    ("//www.instagram.com/john.doe/", "instagram", "john.doe", P),
    ("https://www.instagram.com/john.doe/reels/", "instagram", "john.doe", P),
    ("https://www.instagram.com/_u/john.doe", "instagram", "john.doe", P),
    ("https://www.instagram.com/stories/john.doe/3123456789/", "instagram", "john.doe", C),
    ("https://www.instagram.com/john.doe/p/Cabc123_-/", "instagram", "john.doe", C),
    ("https://l.instagram.com/?u=https%3A%2F%2Fwww.instagram.com%2Fjohn.doe%2F&e=xyz", "instagram", "john.doe", P),
    ("https://www.picuki.com/profile/john.doe", "instagram", "john.doe", P),
    ("https://imginn.com/john.doe/", "instagram", "john.doe", P),
    ("https://socialblade.com/instagram/user/john.doe", "instagram", "john.doe", P),
    ("https://web.archive.org/web/20230101000000/https://www.instagram.com/john.doe/", "instagram", "john.doe", P),
    ("https://web.archive.org/web/2023/https:/www.instagram.com/john.doe/", "instagram", "john.doe", P),
    # --- TikTok
    ("https://www.tiktok.com/@johnd", "tiktok", "johnd", P),
    ("https://www.tiktok.com/@JohnD?lang=en", "tiktok", "johnd", P),
    ("https://m.tiktok.com/@john.d_1", "tiktok", "john.d_1", P),
    ("https://www.tiktok.com/@johnd/video/7234567890123456789", "tiktok", "johnd", C),
    ("https://urlebird.com/user/johnd/", "tiktok", "johnd", P),
    # --- Facebook
    ("https://www.facebook.com/john.doe.37", "facebook", "john.doe.37", P),
    ("https://m.facebook.com/John.Doe.37/", "facebook", "john.doe.37", P),
    ("https://he-il.facebook.com/john.doe.37", "facebook", "john.doe.37", P),
    ("https://fb.com/john.doe.37", "facebook", "john.doe.37", P),
    ("https://www.facebook.com/john.doe.37/about", "facebook", "john.doe.37", P),
    ("https://www.facebook.com/profile.php?id=100001234567890", "facebook", "100001234567890", P),
    ("https://www.facebook.com/profile.php?id=100001234567890&sk=photos", "facebook", "100001234567890", P),
    ("https://www.facebook.com/people/John-Doe/100001234567890/", "facebook", "100001234567890", P),
    ("https://www.facebook.com/pages/Some-Page/123456789012", "facebook", "123456789012", P),
    ("https://www.facebook.com/100001234567890", "facebook", "100001234567890", P),
    ("https://www.facebook.com/john.doe.37/posts/pfbid0abcDEF", "facebook", "john.doe.37", C),
    # --- Threads
    ("https://www.threads.net/@john.doe", "threads", "john.doe", P),
    ("https://www.threads.com/@john.doe", "threads", "john.doe", P),
    ("https://threads.net/@john.doe/replies", "threads", "john.doe", P),
    ("https://www.threads.com/@john.doe/post/Cabc123", "threads", "john.doe", C),
    # --- X / Twitter
    ("https://twitter.com/JohnDoe", "x", "johndoe", P),
    ("https://x.com/johndoe", "x", "johndoe", P),
    ("https://mobile.twitter.com/johndoe/with_replies", "x", "johndoe", P),
    ("https://twitter.com/intent/user?screen_name=JohnDoe", "x", "johndoe", P),
    ("https://twitter.com/#!/johndoe", "x", "johndoe", P),
    ("https://x.com/johndoe#about", "x", "johndoe", P),
    ("https://x.com/i/user/12345", "x", "12345", P),
    ("https://x.com/johndoe/status/1234567890123456789", "x", "johndoe", C),
    ("https://x.com/johndoe/status/1234567890123456789/photo/1", "x", "johndoe", C),
    ("https://nitter.net/johndoe", "x", "johndoe", P),
    ("https://fxtwitter.com/johndoe/status/1", "x", "johndoe", C),
    ("https://l.facebook.com/l.php?u=https%3A%2F%2Fx.com%2Fjohndoe&h=AT0", "x", "johndoe", P),
    # --- GitHub
    ("https://github.com/jdoe", "github", "jdoe", P),
    ("HTTPS://GITHUB.COM/JDoe", "github", "jdoe", P),
    ("https://github.com:443/jdoe", "github", "jdoe", P),
    ("https://github.com./jdoe", "github", "jdoe", P),
    ("https://github.com/jdoe?tab=repositories", "github", "jdoe", P),
    ("https://github.com/orgs/acme/people", "github", "acme", P),
    ("https://github.com/sponsors/jdoe", "github", "jdoe", P),
    ("https://github.com/jdoe.png", "github", "jdoe", P),
    ("https://github.com/jdoe/some-repo", "github", "jdoe", C),
    ("https://github.com/jdoe/some-repo/issues/12", "github", "jdoe", C),
    ("https://gist.github.com/jdoe", "github", "jdoe", P),
    ("https://gist.github.com/jdoe/0123456789abcdef", "github", "jdoe", C),
    ("https://jdoe.github.io/blog/", "github", "jdoe", C),
    ("https://vk.com/away.php?to=https%3A%2F%2Fgithub.com%2Fjdoe", "github", "jdoe", P),
    # --- Reddit
    ("https://www.reddit.com/user/jdoe_99/", "reddit", "jdoe_99", P),
    ("https://old.reddit.com/u/JDoe_99", "reddit", "jdoe_99", P),
    ("https://www.reddit.com/user/jdoe_99/submitted/", "reddit", "jdoe_99", P),
    ("https://www.reddit.com/r/u_jdoe_99/", "reddit", "jdoe_99", P),
    ("https://www.reddit.com/user/jdoe_99/comments/abc123/some_title/", "reddit", "jdoe_99", C),
    # --- YouTube
    ("https://www.youtube.com/@JohnDoe", "youtube", "johndoe", P),
    ("https://m.youtube.com/@JohnDoe/videos", "youtube", "johndoe", P),
    ("https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw", "youtube", "UCuAXFkgsw1L7xaCfnd5JJOw", P),
    ("https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw/about", "youtube", "UCuAXFkgsw1L7xaCfnd5JJOw", P),
    ("https://www.youtube.com/c/JohnDoeVlogs", "youtube", "JohnDoeVlogs", P),
    ("https://www.youtube.com/user/johndoe/videos", "youtube", "johndoe", P),
    ("https://socialblade.com/youtube/handle/johndoe", "youtube", "johndoe", P),
    # --- LinkedIn
    ("https://www.linkedin.com/in/john-doe-1a2b3c4d/", "linkedin", "john-doe-1a2b3c4d", P),
    ("https://il.linkedin.com/in/John-Doe-1a2b3c4d", "linkedin", "john-doe-1a2b3c4d", P),
    ("https://www.linkedin.com/in/john-doe-1a2b3c4d/en", "linkedin", "john-doe-1a2b3c4d", P),
    ("https://www.linkedin.com/in/%D7%99%D7%95%D7%A1%D7%99-123/", "linkedin", "יוסי-123", P),
    ("https://www.linkedin.com/company/acme-inc/", "linkedin", "acme-inc", P),
    ("https://www.linkedin.com/posts/john-doe-1a2b3c4d_hiring-activity-7123-AbCd", "linkedin", "john-doe-1a2b3c4d", C),
    # --- Pinterest
    ("https://www.pinterest.com/johndoe/", "pinterest", "johndoe", P),
    ("https://il.pinterest.com/johndoe/", "pinterest", "johndoe", P),
    ("https://www.pinterest.co.uk/johndoe/_saved/", "pinterest", "johndoe", P),
    ("https://www.pinterest.com/johndoe/my-board/", "pinterest", "johndoe", C),
    # --- Snapchat
    ("https://www.snapchat.com/add/john.doe", "snapchat", "john.doe", P),
    ("https://www.snapchat.com/@john.doe", "snapchat", "john.doe", P),
    ("https://story.snapchat.com/s/john.doe", "snapchat", "john.doe", P),
    # --- Telegram
    ("https://t.me/johndoe", "telegram", "johndoe", P),
    ("https://telegram.me/JohnDoe", "telegram", "johndoe", P),
    ("https://t.me/s/johndoe", "telegram", "johndoe", P),
    ("https://johndoe.t.me", "telegram", "johndoe", P),
    ("https://t.me/johndoe/123", "telegram", "johndoe", C),
    ("https://tgstat.com/channel/@johndoe", "telegram", "johndoe", P),
    # --- VK
    ("https://vk.com/durov", "vk", "durov", P),
    ("https://m.vk.com/durov", "vk", "durov", P),
    ("https://vk.com/id12345", "vk", "12345", P),
    ("https://vk.com/club123", "vk", "123", P),
    # --- Twitch
    ("https://www.twitch.tv/johndoe", "twitch", "johndoe", P),
    ("https://www.twitch.tv/johndoe/videos", "twitch", "johndoe", P),
    ("https://www.twitch.tv/johndoe/clip/FunnyClipName", "twitch", "johndoe", C),
    ("https://twitchtracker.com/johndoe", "twitch", "johndoe", P),
    # --- Bluesky
    ("https://bsky.app/profile/johndoe.bsky.social", "bluesky", "johndoe.bsky.social", P),
    ("https://bsky.app/profile/JohnDoe.com", "bluesky", "johndoe.com", P),
    ("https://bsky.app/profile/did:plc:z72i7hdynmk6r22z27h6tvur", "bluesky", "did:plc:z72i7hdynmk6r22z27h6tvur", P),
    ("https://bsky.app/profile/did%3Aplc%3Az72i7hdynmk6r22z27h6tvur", "bluesky", "did:plc:z72i7hdynmk6r22z27h6tvur", P),
    ("https://johndoe.bsky.social", "bluesky", "johndoe.bsky.social", P),
    ("https://bsky.app/profile/johndoe.com/post/3kabcdefghi2x", "bluesky", "johndoe.com", C),
    # --- Mastodon / fediverse
    ("https://mastodon.social/@johndoe", "mastodon", "johndoe@mastodon.social", P),
    ("https://mastodon.social/users/johndoe", "mastodon", "johndoe@mastodon.social", P),
    ("https://mastodon.social/@johndoe@hachyderm.io", "mastodon", "johndoe@hachyderm.io", P),
    ("https://small-instance.town/@johndoe", "mastodon", "johndoe@small-instance.town", P),
    ("https://fosstodon.org/@johndoe/109123456789012345", "mastodon", "johndoe@fosstodon.org", C),
    # --- Steam
    ("https://steamcommunity.com/id/johndoe", "steam", "johndoe", P),
    ("https://steamcommunity.com/id/johndoe/games/?tab=all", "steam", "johndoe", P),
    ("https://steamcommunity.com/profiles/76561197960287930", "steam", "76561197960287930", P),
    # --- SoundCloud
    ("https://soundcloud.com/johndoe", "soundcloud", "johndoe", P),
    ("https://soundcloud.com/johndoe/tracks", "soundcloud", "johndoe", P),
    ("https://soundcloud.com/johndoe/my-first-track", "soundcloud", "johndoe", C),
    # --- Spotify
    ("https://open.spotify.com/user/31abcDEFghi?si=0123", "spotify", "31abcDEFghi", P),
    ("https://open.spotify.com/intl-de/artist/0TnOYISbd1XYRBk9myaseg", "spotify", "0TnOYISbd1XYRBk9myaseg", P),
    # --- Medium
    ("https://medium.com/@johndoe", "medium", "johndoe", P),
    ("https://johndoe.medium.com/", "medium", "johndoe", P),
    ("https://medium.com/@johndoe/my-post-0123456789ab", "medium", "johndoe", C),
    ("https://johndoe.medium.com/my-post-0123456789ab", "medium", "johndoe", C),
    # --- Linktree
    ("https://linktr.ee/johndoe", "linktree", "johndoe", P),
    # --- a few of the additional platforms
    ("https://gitlab.com/jdoe", "gitlab", "jdoe", P),
    ("https://huggingface.co/jdoe", "huggingface", "jdoe", P),
    ("https://jdoe.tumblr.com/", "tumblr", "jdoe", P),
    ("https://www.tumblr.com/jdoe", "tumblr", "jdoe", P),
    ("https://dev.to/jdoe", "devto", "jdoe", P),
    ("https://www.chess.com/member/JDoe", "chesscom", "jdoe", P),
    ("https://lemmy.world/u/jdoe", "lemmy", "jdoe@lemmy.world", P),
    ("https://www.last.fm/user/jdoe", "lastfm", "jdoe", P),
    ("https://vimeo.com/jdoe", "vimeo", "jdoe", P),
    ("https://www.patreon.com/c/jdoe", "patreon", "jdoe", P),
    ("https://jdoe.substack.com/", "substack", "jdoe", P),
    ("https://substack.com/@jdoe", "substack", "jdoe", P),
]

NOT_PROFILES: list[str] = [
    # --- junk input
    "",
    "   ",
    "not a url",
    "javascript:alert(1)",
    "mailto:someone@example.com",
    "ftp://github.com/jdoe",
    "https://",
    "https://example.com/about",
    "https://192.168.1.10/@admin",
    "https://instagram.com@evil.example/john.doe",
    "https://github.com/" + "a" * 5000,
    # --- Instagram
    "https://www.instagram.com/",
    "https://www.instagram.com/p/CxYz123AbC/",
    "https://www.instagram.com/reel/Cabc123/",
    "https://www.instagram.com/explore/tags/cats/",
    "https://www.instagram.com/accounts/login/?next=%2Fjohn.doe%2F",
    "https://www.instagram.com/explore/",
    "https://help.instagram.com/123456",
    "https://www.instagram.com/this_username_is_far_too_long_to_be_real/",
    # --- TikTok
    "https://www.tiktok.com/tag/funny",
    "https://www.tiktok.com/discover/cats",
    "https://www.tiktok.com/login",
    "https://vm.tiktok.com/ZMabcdef/",
    # --- Facebook
    "https://www.facebook.com/groups/1234567890/",
    "https://www.facebook.com/login.php?next=https%3A%2F%2Fwww.facebook.com%2Fjohn.doe.37",
    "https://www.facebook.com/photo.php?fbid=123",
    "https://www.facebook.com/hashtag/cats",
    "https://www.facebook.com/sharer/sharer.php?u=https%3A%2F%2Fexample.com",
    "https://www.facebook.com/watch/?v=123",
    "https://www.facebook.com/profile.php",
    "https://www.facebook.com/events/123456/",
    "https://developers.facebook.com/docs/graph-api",
    # --- Threads
    "https://www.threads.net/login",
    "https://www.threads.com/search?q=john",
    # --- X / Twitter
    "https://x.com/home",
    "https://x.com/i/flow/login",
    "https://x.com/search?q=john%20doe",
    "https://x.com/hashtag/cats",
    "https://twitter.com/intent/tweet?text=hello",
    "https://twitter.com/this_name_is_way_too_long",
    "https://x.com/settings/profile",
    # --- GitHub
    "https://github.com/",
    "https://github.com/features",
    "https://github.com/login?return_to=%2Fjdoe",
    "https://github.com/-leading-hyphen",
    "https://github.com/topics/osint",
    "https://avatars.githubusercontent.com/u/583231?v=4",
    # --- Reddit
    "https://www.reddit.com/r/pics/comments/abc123/a_post/",
    "https://www.reddit.com/r/pics/",
    "https://www.reddit.com/user/me",
    # --- YouTube
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/abcdefghijk",
    "https://www.youtube.com/results?search_query=john+doe",
    "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/channel/not-a-channel-id",
    "https://www.youtube.com/feed/trending",
    # --- LinkedIn
    "https://www.linkedin.com/feed/",
    "https://www.linkedin.com/jobs/view/123456",
    "https://www.linkedin.com/pulse/some-article-john-doe",
    "https://www.linkedin.com/authwall?trk=bf&sessionRedirect=https%3A%2F%2Fwww.linkedin.com%2Fin%2Fjohn",
    "https://www.linkedin.com/in/",
    # --- Pinterest
    "https://www.pinterest.com/pin/1234567890/",
    "https://www.pinterest.com/ideas/home-decor/935249274030/",
    "https://www.pinterest.com/search/pins/?q=cats",
    "https://pin.it/abcDEF",
    # --- Snapchat
    "https://www.snapchat.com/discover/Some-Show/1234567890",
    "https://www.snapchat.com/spotlight/W7_EDlXWTBiXAEEniNoMPwAAY",
    # --- Telegram
    "https://t.me/joinchat/AAAAAEabcdef",
    "https://t.me/+abcdefGHIJ",
    "https://t.me/addstickers/SomePack",
    "https://t.me/c/1234567890/12",
    "https://t.me/share/url?url=https%3A%2F%2Fexample.com",
    "https://t.me/ab",
    # --- VK
    "https://vk.com/wall-1_123",
    "https://vk.com/photo1_456",
    "https://vk.com/feed",
    "https://vk.com/share.php?url=https%3A%2F%2Fexample.com",
    "https://vk.com/video-1_2",
    # --- Twitch
    "https://www.twitch.tv/directory/game/Chess",
    "https://www.twitch.tv/videos/123456789",
    "https://clips.twitch.tv/FunnyClipName",
    # --- Bluesky
    "https://bsky.app/search?q=john",
    "https://bsky.app/",
    "https://bsky.app/profile/",
    # --- Mastodon
    "https://mastodon.social/explore",
    "https://mastodon.social/tags/osint",
    "https://mastodon.social/about",
    # --- Steam
    "https://steamcommunity.com/app/440",
    "https://steamcommunity.com/sharedfiles/filedetails/?id=123",
    "https://steamcommunity.com/profiles/123",
    # --- SoundCloud
    "https://soundcloud.com/discover",
    "https://soundcloud.com/search?q=john",
    "https://on.soundcloud.com/AbCdE",
    # --- Spotify
    "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
    "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
    "https://www.spotify.com/us/premium/",
    # --- Medium
    "https://medium.com/tag/python",
    "https://help.medium.com/hc/en-us",
    "https://medium.com/towards-data-science/some-post-123",
    # --- Linktree
    "https://linktr.ee/s/about",
    "https://linktr.ee/login",
    "https://linktr.ee/",
]


@pytest.mark.parametrize(("url", "platform", "username", "kind"), MATCHES)
def test_extracts_account(url: str, platform: str, username: str, kind: str) -> None:
    account = REGISTRY.extract(url)
    assert account is not None, f"{url} was not recognised"
    assert (account.platform, account.username, account.kind) == (platform, username, kind)


@pytest.mark.parametrize("url", NOT_PROFILES)
def test_rejects_non_profile(url: str) -> None:
    assert REGISTRY.extract(url) is None


def test_every_required_platform_is_covered() -> None:
    required = {
        "instagram", "tiktok", "facebook", "threads", "x", "github", "reddit", "youtube", "linkedin",
        "pinterest", "snapchat", "telegram", "vk", "twitch", "bluesky", "mastodon", "steam", "soundcloud",
        "spotify", "medium", "linktree",
    }  # fmt: skip
    assert required <= set(REGISTRY.platforms)
    tested = {platform for _, platform, _, _ in MATCHES}
    assert required <= tested


@pytest.mark.parametrize("platform_id", sorted(REGISTRY.platforms))
def test_example_round_trips(platform_id: str) -> None:
    """Building an account and parsing its profile URL must give the same account back."""
    platform = REGISTRY.platforms[platform_id]
    assert platform.example, f"{platform_id} has no example account"
    built = REGISTRY.build(platform_id, platform.example, instance=platform.example_instance)
    assert built is not None
    parsed = REGISTRY.extract(built.profile_url)
    assert parsed is not None, built.profile_url
    assert parsed.key == built.key
    assert parsed.kind == "profile"
    assert parsed.profile_url == built.profile_url


def test_content_urls_can_be_excluded() -> None:
    assert REGISTRY.extract("https://x.com/johndoe/status/1", include_content=False) is None
    assert REGISTRY.extract("https://x.com/johndoe", include_content=False) is not None


def test_profile_urls_and_display_names() -> None:
    cases = {
        "https://www.facebook.com/profile.php?id=100001234567890": (
            "https://www.facebook.com/profile.php?id=100001234567890",
            "id 100001234567890",
        ),
        "https://www.threads.net/@john.doe": ("https://www.threads.com/@john.doe", "@john.doe"),
        "https://twitter.com/JohnDoe": ("https://x.com/johndoe", "@johndoe"),
        "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw": (
            "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw",
            "channel/UCuAXFkgsw1L7xaCfnd5JJOw",
        ),
        "https://vk.com/id12345": ("https://vk.com/id12345", "id12345"),
        "https://mastodon.social/@johndoe@hachyderm.io": ("https://hachyderm.io/@johndoe", "@johndoe@hachyderm.io"),
        "https://www.linkedin.com/in/%D7%99%D7%95%D7%A1%D7%99-123/": (
            "https://www.linkedin.com/in/%D7%99%D7%95%D7%A1%D7%99-123/",
            "יוסי-123",
        ),
        "https://jdoe.substack.com/": ("https://jdoe.substack.com/", "jdoe.substack.com"),
    }
    for url, (profile_url, display) in cases.items():
        account = REGISTRY.extract(url)
        assert account is not None, url
        assert account.profile_url == profile_url
        assert account.display == display


def test_same_account_from_different_urls_shares_a_key() -> None:
    urls = [
        "https://twitter.com/JohnDoe",
        "https://x.com/johndoe/status/1",
        "https://mobile.twitter.com/johndoe/media",
        "https://nitter.net/JohnDoe",
    ]
    keys = {account.key for account in map(REGISTRY.extract, urls) if account}
    assert len(keys) == 1


def test_id_and_vanity_namespaces_do_not_collide() -> None:
    by_handle = REGISTRY.extract("https://www.youtube.com/@johndoe")
    by_user = REGISTRY.extract("https://www.youtube.com/user/johndoe")
    assert by_handle is not None
    assert by_user is not None
    assert by_handle.key != by_user.key


def test_case_sensitive_identifiers_keep_their_case() -> None:
    a = REGISTRY.extract("https://open.spotify.com/user/AbCdEf")
    b = REGISTRY.extract("https://open.spotify.com/user/abcdef")
    assert a is not None
    assert b is not None
    assert a.key != b.key


# ---------------------------------------------------------------- avatar CDNs


def test_cdn_github_numeric_id_needs_a_lookup() -> None:
    match = REGISTRY.match_cdn("https://avatars.githubusercontent.com/u/583231?v=4")
    assert match is not None
    assert (match.resolver, match.identifier) == ("github_user_id", "583231")
    assert match.account is None


def test_cdn_github_login_is_direct() -> None:
    match = REGISTRY.match_cdn("https://avatars.githubusercontent.com/octocat")
    assert match is not None
    assert match.account is not None
    assert match.account.key == ("github", "", "octocat")


def test_cdn_bluesky_did() -> None:
    url = "https://cdn.bsky.app/img/avatar/plain/did:plc:z72i7hdynmk6r22z27h6tvur/bafkreiabc@jpeg"
    match = REGISTRY.match_cdn(url)
    assert match is not None
    assert match.account is not None
    assert match.account.username == "did:plc:z72i7hdynmk6r22z27h6tvur"


def test_cdn_mastodon_account_id() -> None:
    match = REGISTRY.match_cdn("https://files.mastodon.social/accounts/avatars/000/000/001/original/x.png")
    assert match is not None
    assert (match.resolver, match.identifier, match.instance) == (
        "mastodon_account_id",
        "000/000/001/",
        "mastodon.social",
    )
    generic = REGISTRY.match_cdn(
        "https://example.social/system/accounts/avatars/109/123/456/789/012/345/original/a.jpg"
    )
    assert generic is not None
    assert generic.instance is None
    assert generic.host == "example.social"


def test_cdn_gravatar_hash() -> None:
    digest = "a" * 64
    match = REGISTRY.match_cdn(f"https://www.gravatar.com/avatar/{digest}?s=200")
    assert match is not None
    assert (match.resolver, match.identifier) == ("gravatar_hash", digest)


@pytest.mark.parametrize(
    "url",
    [
        "https://pbs.twimg.com/profile_images/1234567890/abc_400x400.jpg",
        "https://scontent.cdninstagram.com/v/t51.2885-19/1.jpg",
        "https://example.com/avatar.png",
        "not a url",
        "https://avatars.githubusercontent.com/u",
    ],
)
def test_cdn_unknown(url: str) -> None:
    assert REGISTRY.match_cdn(url) is None


# ---------------------------------------------------------------- expansion


def test_expansion_covers_at_least_fifty_sites() -> None:
    accounts = list(REGISTRY.expansion_accounts("octocat"))
    assert len(accounts) >= 50
    assert REGISTRY.expansion_site_count() >= 50
    keys = {a.key for a in accounts}
    assert len(keys) == len(accounts), "expansion produced duplicates"
    assert ("bluesky", "", "octocat.bsky.social") in keys
    assert ("mastodon", "", "octocat@mastodon.social") in keys
    assert ("mastodon", "", "octocat@fosstodon.org") in keys


def test_expansion_respects_each_sites_username_rules() -> None:
    names = {a.platform for a in REGISTRY.expansion_accounts("john.doe")}
    assert "instagram" in names  # dots allowed
    assert "x" not in names  # dots are not valid in X handles
    assert "github" not in names
    assert "bluesky" not in names


def test_expansion_seed() -> None:
    def seed(url: str) -> str | None:
        account = REGISTRY.extract(url)
        assert account is not None
        return REGISTRY.expansion_seed(account)

    assert seed("https://github.com/octocat") == "octocat"
    assert seed("https://mastodon.social/@octocat") == "octocat"
    assert seed("https://bsky.app/profile/octocat.bsky.social") == "octocat"
    assert seed("https://bsky.app/profile/octocat.example.com") is None
    assert seed("https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw") is None
    assert seed("https://www.facebook.com/profile.php?id=100001234567890") is None
    assert seed("https://x.com/ab") is None  # too short to be meaningful


# ---------------------------------------------------------------- data file validation


def _minimal(**overrides: object) -> str:
    platform: dict[str, object] = {
        "id": "demo",
        "name": "Demo",
        "hosts": r"demo\.example",
        "username": r"[a-z]{3,10}",
        "profile_url": "https://demo.example/{username}",
        "patterns": [{"path": r"^/(?P<username>{U})/?$"}],
        "avatar": [{"type": "html", "extract": ["og_image"]}],
    }
    platform.update(overrides)
    return json.dumps({"schema": 1, "platforms": [platform]})


def test_minimal_platform_file_loads() -> None:
    registry = PlatformRegistry.from_json(_minimal())
    account = registry.extract("https://demo.example/alice")
    assert account is not None
    assert account.username == "alice"
    assert registry.extract("https://demo.example/al") is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"schema": 2, "platforms": []}),
        _minimal(hosts="("),
        _minimal(patterns=[{"path": "^/nothing$"}]),
        _minimal(patterns=[{"path": r"^/(?P<username>{U})$", "kind": "weird"}]),
        _minimal(avatar=[{"type": "magic"}]),
        _minimal(avatar=[{"type": "json", "url": "https://x.example"}]),
        _minimal(avatar=[{"type": "html", "extract": ["regex"]}]),
    ],
)
def test_malformed_platform_file_is_rejected(text: str) -> None:
    with pytest.raises(PlatformDataError):
        PlatformRegistry.from_json(text)


def test_reserved_words_never_become_usernames() -> None:
    for platform in REGISTRY.platforms.values():
        for word in platform.reserved:
            account = REGISTRY.build(platform.id, word, instance=platform.example_instance)
            assert account is None, f"{platform.id}: reserved word {word!r} produced an account"
