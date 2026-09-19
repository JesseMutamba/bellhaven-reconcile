# Bellhaven ownership reconciliation

A separate project for the fictional Bellhaven assessment. It has no Lumnia code, dependencies, services, or deployment configuration.

The local demo is runnable now: scrape facilities, compare CRM records, inspect evidence, and approve or reject changes. The public Bellhaven website scraper is implemented. The authenticated assessment CRM adapter is **read-only until its actual account and mutation schemas are verified with your candidate token**. No assessment CRM records have been changed.

## Open in VS Code and run

Open this project folder, or open `Bellhaven.code-workspace`. Requires Python 3.11+ on macOS/Linux. There are no runtime packages to install.

```sh
python3 -m bellhaven serve
```

Open **http://127.0.0.1:8765**. On first launch it creates an isolated local demo CRM and generates the review queue. Enter your reviewer name and approve or reject individual proposals. The demo includes ownership changes with and without outstanding AR, a rebrand, a new facility, duplicates, a shared campus, and an account missing from the website.

The VS Code **Run and Debug → Bellhaven: local demo** configuration is also included. The Python extension is needed only for that debugger; the terminal commands work independently.

## Commands

```sh
# Run a reconciliation without any CRM writes
python3 -m bellhaven scan

# Inspect the latest run and decision counts
python3 -m bellhaven status

# Scrape the actual public assessment website; no candidate token needed
python3 -m bellhaven scrape --output data/website.json

# Run the financial, matching, and retry acceptance tests
python3 -m unittest discover -s tests -v
```

The public scraper starts at the homepage, follows the community directory's pagination, and reads community detail pages. This includes the newly announced Findlay location linked from the homepage, which is absent from the directory. It extracts name, street, city, state, ZIP, and care offerings, and saves source HTML plus SHA-256 hashes. A minimum of 35 facilities is required for the known assessment website. JSON-LD and explicit data-attribute cards are also supported for the local fixtures.

## Connecting the assessment CRM

Copy `.env.example` to `.env`. Add the **candidate bearer token** and the actual Bellhaven parent account ID. Keep the token in `.env`, which is ignored by Git. Do not put it in browser JavaScript or committed files.

Use `APP_MODE=live` and a separate database, such as `DB_PATH=data/assessment.sqlite3`, for authenticated reads. The starter deliberately refuses to reuse a database from another mode or parent account. The adapter handles the documented page-number pagination and validates supported response envelopes. If the authenticated response has a different shape, adapt `bellhaven/sandbox.py` using the actual response.

The public OpenAPI document omits POST/PATCH body schemas, response schemas, conditional writes, and idempotency guarantees. See [the integration checklist](docs/crm-contract.md) before implementing live writes. Review approvals currently write only to the local demo CRM through its HTTP API.

## How decisions work

- Scans read the source and CRM, then persist proposals. They never mutate CRM accounts.
- An approval records the reviewer and plan before the API mutation starts. Rejections and investigation notes do not write to the CRM.
- Full address matching is normalized. Fuzzy names produce investigation items; multiple entities sharing a campus remain ambiguous.
- A changed parent with revenue history **and** positive outstanding AR creates a new account. The old account keeps all fields except `chow_current_account`; its parent and balances remain intact.
- Without both billing conditions, an approved parent change updates the existing account. Missing/invalid financial values require investigation.
- Approved duplicates are marked `Inactive` and linked with `duplicate_of_account`. No merge or delete is performed. Duplicates with uncertain billing history require manual investigation.
- An account absent from a complete source crawl is proposed as `Needs Review`, with a note. Absence alone never changes its parent or deactivates it. This inference is disabled by default in live mode.
- SQLite stores decisions, source snapshots, write progress, and audit events. Identical decided proposals remain decided on reruns. Material changes can produce new proposals.
- Changed CRM data makes an unstarted proposal stale. A fresh scan prepares another review. Interrupted demo writes reuse durable idempotency keys; concurrent local approvals are serialized.

See [design decisions and limits](docs/design.md). Daily schedule configuration is in [schedule/daily.cron](schedule/daily.cron); it is not installed automatically.

## Project structure

```text
bellhaven/       CLI, scraper, matching, SQLite, HTTP CRM and review server
dist/           Local review interface served by Python
fixtures/       Clearly labeled fictional demo portfolio
tests/          Acceptance tests for consequential behavior
docs/           API integration notes and design decisions
schedule/       Daily scan script and cron example
data/           Ignored local databases, source snapshots, and logs
```

This is a local application. Serve it with the Python process; publishing the static interface alone will not provide its API or database. The review server binds only to localhost and uses origin checks and per-process request tokens. It does not provide multi-user authentication or a public hosting configuration.
