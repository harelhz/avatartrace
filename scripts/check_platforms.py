"""Live health check: can each platform's profile picture be fetched right now?

For every platform in ``platforms.json`` this fetches the picture of the
platform's ``example`` account (a well-known official or public account) the
same way a scan would: logged out, honest User-Agent, one request per site.

    python scripts/check_platforms.py              # all platforms, plain text
    python scripts/check_platforms.py github x     # only these
    python scripts/check_platforms.py --markdown   # the table used in docs/platforms.md

Results depend on your network: several sites treat data-centre and residential
addresses differently.
"""

from __future__ import annotations

import asyncio
import sys

from avatartrace.config import load_settings
from avatartrace.extraction import Platform, default_registry
from avatartrace.imaging import ImageError, decode_image
from avatartrace.net import HttpClient
from avatartrace.verification import AvatarFetcher

Row = tuple[Platform, str, str]


def _sources(platform: Platform) -> str:
    names = []
    for source in platform.avatar:
        if source.type == "api":
            names.append(f"{source.name} API (needs key)")
        elif source.type == "json":
            names.append("public JSON API")
        elif source.type == "image":
            names.append("public image endpoint")
        else:
            names.append("profile page (" + ", ".join(source.extract) + ")")
    return "; ".join(names) or "none"


async def collect(selected: list[str]) -> list[Row]:
    settings = load_settings()
    registry = default_registry()
    platforms = [p for p in registry.platforms.values() if not selected or p.id in selected]
    async with HttpClient(settings, rate_overrides=registry.rate_overrides()) as http:
        fetcher = AvatarFetcher(registry, http, settings)

        async def check(platform: Platform) -> Row:
            account = registry.build(platform.id, platform.example or "", instance=platform.example_instance)
            if account is None:
                return platform, "ERROR", f"example {platform.example!r} is not a valid username"
            result = await fetcher.fetch(account)
            if result.status == "ok" and result.data:
                try:
                    image = decode_image(result.data)
                except ImageError as exc:
                    return platform, "UNREADABLE", str(exc)
                return platform, "OK", f"{image.size[0]}x{image.size[1]} px"
            return platform, result.status.upper(), result.reason or ""

        return list(await asyncio.gather(*(check(p) for p in platforms)))


def main(arguments: list[str]) -> int:
    markdown = "--markdown" in arguments
    selected = [a for a in arguments if not a.startswith("--")]
    rows = asyncio.run(collect(selected))
    rows.sort(key=lambda row: (row[1] != "OK", row[0].name.lower()))
    ok = sum(1 for row in rows if row[1] == "OK")
    if markdown:
        print("| Platform | Picture source | `--expand` | Logged-out result | Detail |")
        print("| --- | --- | --- | --- | --- |")
        for platform, status, detail in rows:
            verdict = "verified" if status == "OK" else "UNVERIFIED"
            note = detail if status == "OK" else (platform.avatar_note or detail)
            cells = [platform.name, _sources(platform), "yes" if platform.expand else "no", verdict, note]
            print("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    else:
        width = max((len(row[0].id) for row in rows), default=0)
        for platform, status, detail in rows:
            print(f"{platform.id:<{width}}  {status:<11}  {detail[:150]}")
    print(f"\n{ok} of {len(rows)} platforms returned a picture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
