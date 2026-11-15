# Security and misuse

## Reporting a vulnerability

Please report security problems privately through GitHub's
[private vulnerability reporting](https://github.com/harelhz/avatartrace/security/advisories/new)
rather than in a public issue. Include the version, what you did and what happened.

Things that count as vulnerabilities here:

- a way to make avatartrace send the photo, or anything derived from it, somewhere the user did not agree to;
- a way for a web page, a search result or a fetched profile to run code, read local files, or reach
  private network addresses through the tool or its local web UI;
- a way for a report (HTML, CSV) to execute content taken from the web;
- leaking of API keys into logs, error messages, URLs or reports.

## Reporting misuse

avatartrace must not be used to stalk, harass, dox or identify strangers
(see [Ethics & safety](README.md#ethics--safety)).

- If someone is using it, or any tool, to harass or threaten you, contact local law enforcement and the
  platforms involved; they can act on accounts and preserve evidence.
- If you find a fork or a service built on avatartrace that removes its safeguards or advertises it for
  locating people, report it to the hosting platform and open an issue here so users can be warned.
- Pull requests that add logins, block evasion, bulk processing or tracking of individuals are declined.

## What the tool does to limit harm

One photo per run, no bulk mode; explicit consent before any upload; EXIF stripped from uploads; public,
logged-out data only; an honest User-Agent and per-site rate limits; no retries after a refusal; nothing about
third parties written to disk unless `--keep-cache` is given; every result labelled as a lead, not proof.
