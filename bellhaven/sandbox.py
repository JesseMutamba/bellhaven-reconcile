"""Adapter for the authenticated Bellhaven assessment account schema."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .crm import HTTPCRM, CRMError
from .store import digest, encode

CARE_TYPES = {
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing", "memory support": "Memory Care",
    "memory care": "Memory Care", "assisted living": "Assisted Living",
    "independent living": "Independent Living",
}


class WriteRejected(CRMError):
    """The API explicitly rejected the request without accepting a mutation."""


class UncertainWrite(CRMError):
    """A mutation might have happened; reconcile, never blindly repeat it."""


class SandboxCRM(HTTPCRM):
    def list_accounts(self):
        result, ids, expected_total = [], set(), None
        for page_number in range(1, 1001):
            page = self.request("GET", f"/accounts?page={page_number}&page_size=50")
            if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                raise CRMError("Unexpected assessment account-list response")
            total = page.get("total")
            if not isinstance(total, int) or total < 0 or page.get("page") != page_number:
                raise CRMError("Invalid assessment pagination metadata")
            if expected_total is not None and total != expected_total:
                raise CRMError("CRM account count changed during pagination; run the scan again")
            expected_total = total
            for record in page["data"]:
                normalized = self.normalize(record)
                if normalized["id"] in ids:
                    raise CRMError("CRM pagination repeated an account; refusing an incomplete snapshot")
                ids.add(normalized["id"])
                result.append(normalized)
            if len(result) == total:
                return result
            if len(result) > total or not page["data"]:
                raise CRMError("CRM pagination did not return its declared total")
        raise CRMError("Assessment pagination exceeded the limit")

    @staticmethod
    def normalize(record):
        if not isinstance(record, dict) or not all(isinstance(record.get(k), str) and record[k] for k in ("account_id", "name")):
            raise CRMError("Account response lacks account_id/name")
        care = record.get("care_type", "")
        if not isinstance(care, str):
            raise CRMError("Expected a single primary care_type string")
        return {"id": record["account_id"], "name": record["name"],
                "address": record.get("billing_street", ""), "city": record.get("billing_city", ""),
                "state": record.get("billing_state", ""), "zip": record.get("billing_zip", ""),
                "parent_id": record.get("parent_id") or None, "parent_name": record.get("parent_name", ""),
                "account_kind": "corporate" if record["name"].endswith("(Parent Account)") else "facility",
                "care_offerings": [care] if care else [], "care_model": "primary",
                "status": record.get("status"), "phone": record.get("phone", ""),
                "lifetime_revenue": record.get("lifetime_revenue"), "outstanding_ar": record.get("outstanding_ar"),
                "chow_current_account": record.get("chow_current_account") or None,
                "duplicate_of_account": record.get("duplicate_of_account") or None,
                "note": record.get("note") or "", "version": digest(record), "_raw": record}

    def get(self, account_id):
        return self.normalize(super().get(account_id))

    @staticmethod
    def payload(fields, current=None):
        mapping = {"name": "name", "address": "billing_street", "city": "billing_city",
                   "state": "billing_state", "zip": "billing_zip", "parent_id": "parent_id",
                   "status": "status", "note": "note", "phone": "phone",
                   "duplicate_of_account": "duplicate_of_account", "chow_current_account": "chow_current_account"}
        if set(fields) - (set(mapping) | {"care_offerings"}):
            raise CRMError("Refusing to send unknown or financial account fields")
        payload = {mapping[k]: (v if v is not None else "") for k, v in fields.items() if k in mapping}
        if "care_offerings" in fields:
            try:
                choices = {CARE_TYPES[item.strip().lower()] for item in fields["care_offerings"]}
            except (KeyError, AttributeError, TypeError):
                raise CRMError("Unrecognized website care offering; investigate before updating primary care type") from None
            if not choices:
                raise CRMError("Refusing to erase primary care type from an empty website value")
            previous = (current or {}).get("_raw", {}).get("care_type")
            # Keep a compatible existing primary. Prefer Assisted Living for a new
            # dual-offering location, then nursing, memory, and independent living.
            payload["care_type"] = previous if previous in choices else next(
                item for item in ("Assisted Living", "Skilled Nursing", "Memory Care", "Independent Living") if item in choices)
        return payload

    def write(self, method, account_id, payload):
        """One attempt only. The caller must journal and authorize the exact operation."""
        from urllib.parse import quote
        path = "/accounts" + ("/" + quote(account_id, safe="") if account_id else "")
        request = Request(self.base_url + path, data=encode(payload).encode(), method=method,
                          headers={"Accept": "application/json", "Content-Type": "application/json",
                                   "Authorization": "Bearer " + self.token})
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise UncertainWrite("CRM mutation response was too large; check the result before resuming")
                return json.loads(raw)
        except HTTPError as exc:
            detail = ""
            try:
                detail = str(json.loads(exc.read(4096)).get("detail", ""))[:500]
            except (ValueError, AttributeError):
                pass
            detail = detail.replace(self.token, "[redacted]") if self.token else detail
            if 400 <= exc.code < 500 and exc.code != 408:
                raise WriteRejected(f"CRM rejected the write (HTTP {exc.code}): {detail}") from None
            raise UncertainWrite(f"CRM write returned HTTP {exc.code}; verify its result before resuming") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise UncertainWrite("CRM write outcome is unknown. Check its result; do not repeat the request.") from None

    def create(self, fields, key):
        raise CRMError("Use an approved, journaled operation to create assessment accounts")

    def update(self, account_id, fields, version, key):
        raise CRMError("Use an approved, journaled operation to update assessment accounts")
