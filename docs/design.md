# Design decisions

## Evidence and identity

The website is evidence of affiliation, not proof that a legal property owner and an operating company are identical. Every mutation requires review. Source HTML, its hash, the normalized facility, and the CRM snapshot are preserved with each run/proposal.

Normalization expands a small known set of street-direction/suffix aliases, case, whitespace, and punctuation; it preserves street numbers and unit text. Matching uses the complete street/city/state/ZIP key. A single address match can support a rebrand proposal. Shared-campus source records, conflicting candidates, and name-only similarities become non-executable investigations. The starter does not infer a sold property's new owner from website absence.

The same-address/same-name duplicate path is deliberately narrow: the records must already share Bellhaven's parent; the losing record must have no revenue or AR. Financial history wins when choosing the survivor. All other suspected duplicates need investigation. A reviewer can reject any proposed duplicate; no automatic merges exist.

## Billing

`lifetime_revenue != 0 and outstanding_ar > 0` conservatively represents revenue history plus unpaid AR. Missing, invalid, or non-finite values are unknown, never zero. A CHOW creates a new account and then changes only the old record's successor pointer. Its name, status, parent, notes, revenue, and AR remain as originally recorded. This interprets the brief's "leave exactly as is" as permitting only the explicitly required pointer update (plus the CRM's own version metadata).

The reviewed snapshot is compared to fresh CRM data immediately before execution. The demo CRM enforces conditional updates atomically. New/successor creates check for newly appearing address candidates first. The server serializes scans and decisions using a local file lock, including across a scheduled process sharing the same database.

## Durable decisions and failures

Proposal fingerprints exclude crawl timestamps, source HTML hashes, and version metadata. They include the source identity, action, desired fields, and material prior CRM state. Cosmetic source-page changes do not reopen rejected decisions. Changed business evidence can produce a new proposal while retaining prior history.

States are `pending`, `applying`, `applied`, `rejected`, `reviewed`, `superseded`, `stale`, `retryable`, or `uncertain`. Approval is distinct from application. Audit entries record approval/retry and the outcome. Investigation items can record a reviewer note but cannot execute an invented mutation. A failed crawl does not replace the previous queue.

Demo idempotency keys are per proposal and step, and are stored in the demo CRM transaction with the corresponding mutation. The application persists successor IDs between CHOW steps. If a response is lost, retrying recovers the same mutation. An unrelated change after partial creation stops in `uncertain`; future scans remain blocked until that partial operation is reconciled. That recovery requires an operator at this stage; the starter intentionally has no "clear error and forget" action.

Decided proposals are retained indefinitely. Data retention, backup rotation, schema migration beyond version 1, multi-user auth, contact migration, and a UI for manually resolving ambiguous identity are not yet implemented.

## Source completeness

For the known website, crawl the homepage and every linked `/communities` directory/detail page, including pagination. The homepage's Findlay link matters because the directory advertises 34 communities while the homepage advertises 35. The read-only scrape command checks for at least 35. HTTP/parsing errors and crawl limits fail the whole run. HTTP 404 for `robots.txt` means no published robots policy; other retrieval failures stop the crawl.

Live absence proposals require an explicit `SCRAPE_ALLOW_ABSENCE_REVIEW=true` after verifying full coverage. A minimum count alone is not a completeness proof. A future site redesign, JavaScript-only portfolio, or unlinked community needs a revised source adapter.

## Local UI

The interface is plain HTML/CSS/JavaScript served by the Python API. It displays field differences, billing context, source evidence, review history, and explicit approve/reject controls. External strings are HTML-escaped. The server binds to loopback, checks Host/Origin, protects UI mutations with a process token, and keeps the internal demo CRM token server-side.

Optional WebMCP tools list proposals and open a review detail. They never approve changes. Browser support is feature-detected. No supported WebMCP browser context was available in this setup, so these registrations have not been browser-verified.

The local Python backend and SQLite database are required. Static publishing of `dist` alone is insufficient. No site or cloud resources have been created.
