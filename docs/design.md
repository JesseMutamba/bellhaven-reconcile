# Design and operational decisions

## Source coverage and identity

The scraper begins at the homepage and follows all community-directory and detail links, including pagination. Its verified crawl captured 35 locations from 40 pages. Starting only at the directory would miss the Findlay community announced on the homepage. Raw pages, hashes, timestamps, and all six requested location fields are retained.

Names and addresses are normalized without erasing street numbers or units. Cardinal/diagonal directions and common suffixes, including Pike, are recognized. Exact full-address matches support reviewed name/parent corrections. Exact name, street, locality, and phone can support a ZIP correction. Shared campuses, competing accounts, and physical-vs-billing PO Box differences stay in investigation. Website affiliation is evidence for the reviewer, not proof that an operator is the legal property owner.

The assessment has scalar primary care types. Equivalent nursing/memory labels are normalized and compatible existing primary care is retained; complete website offerings remain in the evidence. This prevents care-label differences from flooding the queue.

Duplicate proposals are deliberately narrow: same normalized name/address and Bellhaven parent, with no financial history on the losing record. Other duplicate/cross-owner candidates require investigation. Inactive duplicates are linked with `duplicate_of_account`; no merge or delete exists.

Absence from a verified full crawl proposes only `Needs Review` and a note, never automatic detachment or deactivation. Live absence review is an explicit configuration setting. It is enabled for this known portfolio after checking all 35 detail records; a count alone is not a general completeness guarantee.

## Billing ownership changes

Revenue history and positive AR require a new successor account. The historical account retains its old parent, name, status, notes, and financials; only the explicitly required successor pointer changes. Unknown financials are not zero. Other approved ownership changes update the original record directly.

The live executor checks the historical snapshot again after creating the successor and immediately before linking. It verifies both the successor and the old account through GET. See [CRM contract](crm-contract.md) for the API's concurrency limits.

## Persistence and reruns

SQLite preserves source runs, proposals, decisions, write progress, operation requests, and audit history. Proposal fingerprints include material evidence and proposed changes but exclude timestamps, HTML hashes, derived parent names, and adapter internals. Candidate/dependency lists are sorted before hashing, so API order changes do not reopen decisions. Materially changed evidence may create a new proposal.

States are pending, applying, applied, rejected, reviewed, superseded, stale, retryable, and uncertain. Approval is recorded before dispatch. Rejected/reviewed/applied proposals stay decided on unchanged reruns. A failed or incomplete crawl leaves the prior queue intact.

The live write journal persists prepared/sent/applied/rejected phases. Sent requests are reconciled instead of resent. Known API rejections can be retried after correction; unresolved outcomes block scans until investigated. The app exposes a check/resume action for approved interrupted work. Fresh checks can stop further writes, while read-only confirmation of an already completed operation remains possible even if the parent later becomes inactive.

The daily cron template calls a read-only scan against the same `.env` and database, preserving decisions. No cron job is installed automatically. The live database is bound to its mode, API origin, parent, and a token digest to avoid mixing candidate copies.

## Local review interface

The review app shows field differences, financial context, candidate accounts, source evidence, reviewer notes, and audit events. It explicitly labels demo and assessment modes. New-account proposals explain that notes contain a reconciliation reference. Approvals in assessment mode affect the candidate CRM; scans and rejections do not.

The Python server binds only to loopback, checks Host/Origin, and protects browser mutations with a process token. The candidate CRM token stays server-side. Authentication for public multi-user hosting is not implemented; publishing static `dist` files alone is insufficient.

Backups/retention automation and an interface for manually resolving uncertain identity remain future work. Investigations can record reviewer notes; the application does not invent a new parent when evidence is inconclusive.
