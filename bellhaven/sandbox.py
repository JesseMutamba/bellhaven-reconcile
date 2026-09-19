"""Read-only integration until authenticated field/write semantics can be verified."""
from .crm import HTTPCRM, CRMError


class SandboxCRM(HTTPCRM):
    def list_accounts(self):
        result, ids = [], set()
        for page_number in range(1, 1001):
            page = self.request("GET", f"/accounts?page={page_number}&page_size=50")
            if isinstance(page, list):
                records, total = page, None
            elif isinstance(page, dict):
                records = next((page[k] for k in ("accounts", "items", "data") if isinstance(page.get(k), list)), None)
                total = page.get("total")
            else:
                records, total = None, None
            if records is None:
                raise CRMError("Unrecognized sandbox pagination envelope; inspect an authenticated response before adapting")
            for record in records:
                normalized = self.normalize(record)
                if normalized["id"] in ids:
                    raise CRMError("Sandbox pagination repeated an account; refusing a partial snapshot")
                ids.add(normalized["id"])
                result.append(normalized)
            if isinstance(total, int) and len(result) >= total:
                if len(result) != total:
                    raise CRMError("Sandbox pagination count does not match its reported total")
                return result
            if not records:
                if total is not None and len(result) != total:
                    raise CRMError("Sandbox returned an incomplete account snapshot")
                return result
        raise CRMError("Sandbox pagination exceeded the limit")

    @staticmethod
    def normalize(record):
        if not isinstance(record, dict) or not all(record.get(k) for k in ("id", "name")):
            raise CRMError("Account response lacks id/name; verify the authenticated schema")
        result = dict(record)
        if "address" not in result and "street" in result:
            result["address"] = result["street"]
        result.setdefault("version", None)
        result.setdefault("care_offerings", [])
        result.setdefault("note", "")
        return result

    def get(self, account_id):
        return self.normalize(super().get(account_id))

    def create(self, fields, key):
        raise CRMError("Live writes are disabled until the sandbox's authenticated write contract is verified")

    def update(self, account_id, fields, version, key):
        raise CRMError("Live writes are disabled until the sandbox's authenticated write contract is verified")
