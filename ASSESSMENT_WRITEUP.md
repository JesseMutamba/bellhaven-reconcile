# Bellhaven assessment writeup

**Matching approach.** The scraper starts at the homepage and follows listings, pagination, and detail pages to collect each facility’s name, address, city, state, ZIP, and care offerings. This captures locations such as Findlay that are missing from the main directory.

Matching uses normalized addresses across the full CRM, including accounts under other parents. A unique address match supports a proposed correction; fuzzy names, shared campuses, and competing records require investigation. Results distinguish confident matches, updates, parent corrections, new facilities, duplicates, and accounts missing from the website. Website affiliation is evidence for review, not proof of legal ownership.

When an ownership change involves revenue history and positive outstanding AR, the proposal creates a successor account while preserving the historical account and its balances. Website absence only proposes a review flag. Every CRM change requires approval, with supporting evidence and before/after values shown in the app.

SQLite preserves decisions and write progress, so unchanged reruns do not reopen decided proposals. Interrupted writes are checked before further action. Daily cron configuration is included but not activated. Validation covered 35 facilities and 121 CRM accounts; a repeat scan added zero proposals. All 47 tests passed, and one approved parent correction was verified through the assessment API.

**AI tools.** I used OpenAI Codex for substantial implementation assistance with the scraper, matching logic, interface, API integration, tests, documentation, and security review. I supplied the requirements, directed the scope, and reviewed a CRM proposal. Validation included executed tests and API read-back checks. The running application uses deterministic rules, not an LLM, to match facilities.

**What I would build next.** I would add tools to resolve ambiguous matches, evaluate accuracy against labeled examples, and improve crawl monitoring, backups, and scheduled-run alerts. I would also use server-supported idempotency and conditional updates if the CRM exposes them; the current integration cannot guarantee atomic updates against external edits.
