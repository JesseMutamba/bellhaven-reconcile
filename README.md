# Bellhaven ownership reconciliation

A separate project for the fictional Bellhaven assessment. It has no Lumnia code, dependencies, services, or deployment configuration.

Scrape the public Bellhaven portfolio, compare CRM records, inspect evidence, and approve or reject changes. Both a local demo and the authenticated assessment CRM are supported. Approved assessment changes use the real account API, with a durable operation journal and read-back verification. Scans and rejections never change CRM records.

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

Copy `.env.example` to `.env` if it does not already exist. Add the **candidate bearer token** and Bellhaven parent account ID. Keep the token in `.env`, which is ignored by Git. Do not put it in browser JavaScript or committed files.

Use `APP_MODE=live` and a separate database, such as `DB_PATH=data/assessment.sqlite3`. The application refuses to reuse a database from another mode, parent, or candidate token. The adapter maps the observed `account_id`, `billing_*`, and `care_type` fields, and checks complete page-number pagination. Start the app, then choose **Run reconciliation** to load the real review queue.

The public API does not document atomic conditional writes or idempotency keys. The live executor records each operation before sending it, verifies changes with GET, and never blindly repeats an uncertain request. If a response is lost, **Check result / resume approved change** checks the earlier result and may finish the remaining approved steps. Unresolved ambiguity blocks further scans until investigated. See [the API contract and verification limits](docs/crm-contract.md).

## How decisions work

- Scans read the source and CRM, then persist proposals. They never mutate CRM accounts.
- An approval records the reviewer and plan before the API mutation starts. Rejections and investigation notes do not write to the CRM.
- Full address matching is normalized. Fuzzy names produce investigation items; multiple entities sharing a campus remain ambiguous.
- Directional abbreviations and street suffixes are normalized. An exact name, street, locality, and phone match can support a reviewed ZIP correction. Billing PO Boxes remain investigations rather than being silently replaced by physical addresses.
- Website nursing/memory labels are mapped to the CRM's equivalent care labels. The CRM stores one primary care type: retain a compatible existing primary, or choose Assisted Living, then Skilled Nursing, Memory Care, Independent Living for new multi-offering records. All source offerings remain in the evidence.
- A changed parent with revenue history **and** positive outstanding AR creates a new account. The old account keeps all fields except `chow_current_account`; its parent and balances remain intact.
- Without both billing conditions, an approved parent change updates the existing account. Missing/invalid financial values require investigation.
- Approved duplicates are marked `Inactive` and linked with `duplicate_of_account`. No merge or delete is performed. Duplicates with uncertain billing history require manual investigation.
- An account absent from a complete source crawl is proposed as `Needs Review`, with a note. Absence alone never changes its parent or deactivates it. This inference is disabled by default in live mode.
- SQLite stores decisions, source snapshots, write progress, and audit events. Identical decided proposals remain decided on reruns. Material changes can produce new proposals.
- Changed CRM data makes an unstarted proposal stale. A fresh scan prepares another review. Demo writes use server idempotency keys; assessment writes use a durable journal and reconciliation references. All local approvals are serialized. The external API cannot guarantee atomic updates against unrelated external editors.

See [the assessment writeup](ASSESSMENT_WRITEUP.md) for the matching approach, AI assistance, and next steps, and [design decisions and limits](docs/design.md) for implementation details. Daily schedule configuration is in [schedule/daily.cron](schedule/daily.cron); it is not installed automatically.

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

Run it on a trusted personal machine: local processes can access the review app, and the reviewer name is an audit label, not a login. Database files and scan locks are restricted to the current OS user; the CLI and scheduled task create new snapshots and logs with private permissions. Keep the dedicated `data/` directory private and preserve file permissions in backups. CRM redirects are disabled, and website redirects are restricted to the same origin before any redirected request is sent.
