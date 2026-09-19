# Assessment CRM integration

The integration uses [the assessment API](https://analyst-assessment-production.up.railway.app/api/docs#/) at `https://analyst-assessment-production.up.railway.app/api/v1`. Authentication is `Authorization: Bearer <candidate token>`. Tokens remain in the ignored local `.env` and never enter browser code, committed fixtures, or logs.

## Observed authenticated reads

The candidate identity endpoint and all account-list pages were read successfully. The inspected candidate snapshot contains 121 accounts and one Bellhaven parent. No live mutation was needed to inspect those records.

- `GET /accounts?page=1&page_size=50` returns `{data: [...], page, page_size, total}`.
- `GET /accounts/{account_id}` returns a bare account object.
- Account keys include `account_id`, `name`, `parent_id`, `parent_name`, `billing_street`, `billing_city`, `billing_state`, `billing_zip`, `care_type`, `status`, `phone`, `lifetime_revenue`, `outstanding_ar`, `chow_current_account`, `duplicate_of_account`, `note`, `created_by_candidate`, and `updated_at`.
- The adapter reads every page, rejects duplicate IDs or changing totals, and normalizes records into the matching model.

## Mutations and review

Only approval/resumption of an approved proposal reaches the live executor. `POST /accounts` creates a new account; `PATCH /accounts/{account_id}` sends only the approved changed fields. The mapping uses the observed `billing_*` fields and scalar `care_type`. Account IDs, financial values, timestamps, and other server-owned fields are never sent as changes.

The API's public OpenAPI document omits mutation body and response schemas. Payloads are based on the observed account fields and are tested against a local contract fixture. Actual body acceptance and persistent effects are verified on each user-approved request, with API validation errors surfaced in the review app. No claim is made that a live write has succeeded until its GET read-back matches the approved operation.

The CRM supports one primary care type. Website `Short-Term Rehabilitation & Nursing` maps to `Skilled Nursing`; `Memory Support` maps to `Memory Care`. A compatible existing primary is retained. For a new facility with multiple offerings, the explicit priority is Assisted Living, Skilled Nursing, Memory Care, Independent Living. Full scraped offerings remain in proposal evidence.

## Retry and concurrency guarantees

The remote API does not document `If-Match`, server idempotency keys, or transactions. The adapter does not pretend those protections exist.

1. Persist the exact request, approved baseline, and operation phase in SQLite before dispatch.
2. Re-read the affected account and dependencies immediately before each new write.
3. Give each POST a unique reconciliation reference in `note`.
4. Mark a sent request's outcome uncertain if the response is lost, malformed, or cannot be verified.
5. Never resend a `sent` request. Reconcile a POST by a unique reference match across all account pages; reconcile a PATCH by GET of the target and comparison with the approved baseline plus changes.
6. A successful read-back records the operation as applied. Resuming a multi-step CHOW can then send only its remaining approved link request.
7. Definite HTTP 4xx rejections, except request timeout, can be retried explicitly; uncertain outcomes cannot be blindly retried.

For CHOW, the old account is read again after successor creation and before linkage. Only `chow_current_account` changes on the historical record; its parent and financial values are checked as unchanged. The successor is verified before linking.

Local file locks serialize this application's scans and reviewers. An external editor can still race the interval between GET and PATCH because the server has no documented compare-and-swap mechanism. Post-write verification detects mismatches; it cannot make the API atomic. A lost POST without a unique marker match remains uncertain and requires investigation.

## Validation

The acceptance suite exercises the authenticated schema through a local contract double: field mapping, approval-only writes, billing preservation, lost POST/PATCH responses, ambiguous reconciliation, stale records, account order changes, and pagination completeness. The existing demo HTTP tests verify the review endpoints and request guards. Public website extraction and authenticated CRM reads are also checked separately.

Reviewing and applying actual CRM proposals is the final submission step. The app leaves those decisions to the reviewer; providing a token does not automatically approve any changes.
