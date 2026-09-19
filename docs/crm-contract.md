# Assessment API: verified facts and remaining integration

Sources inspected September 18, 2026:

- [Interactive documentation](https://analyst-assessment-production.up.railway.app/api/docs#/)
- [OpenAPI JSON](https://analyst-assessment-production.up.railway.app/api/openapi.json)
- [Identity endpoint](https://analyst-assessment-production.up.railway.app/api/v1/me)

The base URL is `https://analyst-assessment-production.up.railway.app/api/v1`.
Authentication is `Authorization: Bearer <candidate token>`, confirmed by the live 401 response. The token is not included in this project.

| Resource | Methods | Documented query parameters |
|---|---|---|
| `/accounts` | GET, POST | `q`, `city`, `state`, `zip`, `street`, `parent_id`, `page`, `page_size` |
| `/accounts/{account_id}` | GET, PATCH | None |
| `/contacts` | GET, POST | `account_id`, `q`, `page`, `page_size` |
| `/contacts/{contact_id}` | GET, PATCH | None |
| `/me` | GET | None |

Page defaults are 1 and 50. Account creation documents HTTP 201; reads and updates document HTTP 200. The published success schemas are empty, and request bodies are not described. No credentials were available, so authenticated account data has not been inspected. Contacts are outside this ownership-reconciliation starter's scope.

## Before enabling real writes

1. Authenticate `/me`; inspect one account page and an account by its returned ID. Confirm the candidate's sandbox and Bellhaven's parent ID.
2. Verify response envelopes, pagination completion, field names/types, missing values, and whether address is `street` or `address`. The read adapter accepts list, `accounts`, `items`, or `data` list envelopes; it fails closed on unexpected shapes. This is defensive support, not a claim that every envelope was observed.
3. Confirm POST/PATCH body semantics and allowed fields, including `parent_id`, `status`, `note`, `duplicate_of_account`, and `chow_current_account`. Preserve unknown fields and financial history.
4. Determine whether the service supports conditional updates, revisions/ETags, idempotency keys, and lookup by a durable operation marker. The documentation does not promise any of these. Do not assume the demo's `If-Match` and `Idempotency-Key` headers work on the real service.
5. Implement those confirmed semantics in `SandboxCRM`, then add adapter contract tests and test the billing transfer workflow against the provided candidate sandbox. Until then, its write methods and the review service's live-write gate remain disabled.
6. If the API has no idempotency or conditional-write support, document the narrower guarantees: re-read before mutation, serialize this application's writers, record intended operations durably, reconcile uncertain creates using verified lookup fields, and stop for manual investigation after ambiguous responses. Do not automatically repeat an uncertain create.
7. Run the real pipeline, review each proposal, and apply the supported corrections through the app. The assessment asks for corrected CRM data as well as code; the local demo does not complete that submission requirement.

## Local demo contract

The internal demo API uses `GET /accounts` → `{accounts: [...], next_cursor: null}` and `GET /accounts/{id}` → account. POST creates a facility, and PATCH updates only submitted fields. Mutation calls require a durable `Idempotency-Key`; PATCH also requires `If-Match` with the account's integer version. Replaying the identical operation returns the original result; reusing a key for different input or writing a stale version returns HTTP 409.

Canonical demo account fields are `id`, `version`, `account_kind`, `name`, `address`, `city`, `state`, `zip`, `care_offerings`, `parent_id`, `status`, `note`, `lifetime_revenue`, `outstanding_ar`, `duplicate_of_account`, and `chow_current_account`. New accounts start with zero demo balances; historical ledger values are never copied.
