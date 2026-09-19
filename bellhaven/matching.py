from collections import Counter
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import re
import unicodedata

from .store import digest

SUFFIXES = {"street": "st", "road": "rd", "avenue": "ave", "drive": "dr", "boulevard": "blvd",
            "lane": "ln", "court": "ct", "pike": "pk", "north": "n", "south": "s", "east": "e", "west": "w",
            "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se"}


def normal(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", str(value or "")).lower()).split())


def address_key(record):
    street = " ".join(SUFFIXES.get(part, part) for part in normal(record.get("address")).split())
    return "|".join([street, normal(record.get("city")), normal(record.get("state")), str(record.get("zip", ""))[:5]])


def financials(record):
    try:
        values = [Decimal(str(record[k])) for k in ("lifetime_revenue", "outstanding_ar")]
        if not all(v.is_finite() for v in values):
            raise ValueError("Financial values are not finite")
        return values
    except (KeyError, InvalidOperation, TypeError):
        raise ValueError("Revenue history and outstanding AR must both be known") from None


def needs_chow(record):
    revenue, ar = financials(record)
    return revenue != 0 and ar > 0


def material(value):
    if isinstance(value, dict):
        return {k: material(v) for k, v in value.items()
                if k not in {"version", "updated_at", "source_sha256", "source_url", "_raw", "parent_name"}}
    if isinstance(value, list):
        return [material(v) for v in value]
    return value


def proposal(kind, facility, before, changes, reason, candidates=None, dependencies=None):
    candidates = sorted(candidates or [], key=lambda a: a["id"])
    dependencies = sorted(dependencies or [], key=lambda a: a["id"])
    plan = {"facility": facility, "before": before, "changes": changes,
            "evidence": reason, "candidates": candidates or [], "dependencies": dependencies or []}
    key = digest({"kind": kind, "subject": facility["key"], "before": material(before),
                  "changes": material(changes), "candidates": material(candidates or []),
                  "dependencies": material(dependencies or [])})
    return {"id": key, "kind": kind, "title": facility["name"], "plan": plan}


def facility_fields(facility, parent_id):
    result = {k: facility[k] for k in ("name", "address", "city", "state", "zip")}
    result.update(parent_id=parent_id, status="Active")
    if facility.get("care_offerings") is not None:
        result["care_offerings"] = facility["care_offerings"]
    return result


def match(facilities, accounts, parent_id, absence_allowed):
    proposals, matched, used = [], [], set()
    accounts = [a for a in accounts if a["id"] != parent_id and a.get("account_kind") != "corporate"]
    active = [a for a in accounts if not a.get("duplicate_of_account") and not a.get("chow_current_account")]
    address_counts = Counter(address_key(f) for f in facilities)
    for facility in facilities:
        exact = [a for a in active if address_key(a) == address_key(facility)]
        zip_correction = False
        if not exact and facility.get("phone"):
            same_place = [a for a in active if address_key(a).rsplit("|", 1)[0] == address_key(facility).rsplit("|", 1)[0]
                          and normal(a["name"]) == normal(facility["name"])
                          and re.sub(r"\D", "", a.get("phone", "")) == re.sub(r"\D", "", facility["phone"])]
            if len(same_place) == 1:
                exact, zip_correction = same_place, True
        # Names alone never authorize a match; fuzzy candidates are investigation-only.
        near = [a for a in active if normal(a.get("state")) == normal(facility["state"])
                and (normal(a.get("city")) == normal(facility["city"]) or str(a.get("zip", ""))[:5] == facility["zip"][:5])
                and SequenceMatcher(None, normal(a["name"]), normal(facility["name"])).ratio() >= .6]
        candidates = exact or near
        used.update(a["id"] for a in candidates)
        if not candidates:
            proposals.append(proposal("create", facility, None, facility_fields(facility, parent_id),
                                      ["No address match or plausible name match found across the CRM snapshot."]))
            continue
        duplicate_set = (len(exact) > 1 and address_counts[address_key(facility)] == 1
                         and len({normal(a["name"]) for a in exact}) == 1)
        if len(exact) != 1 and not duplicate_set or address_counts[address_key(facility)] > 1:
            proposals.append(proposal("investigate", facility, None, {},
                                      ["Multiple candidates, a shared campus, or a name-only match requires manual investigation."], candidates))
            continue
        if duplicate_set:
            try:
                ranked = sorted(exact, key=lambda a: (-int(financials(a)[0] != 0), -int(financials(a)[1] > 0),
                                                     -int(a.get("parent_id") == parent_id), a["id"]))
            except ValueError:
                proposals.append(proposal("investigate", facility, None, {}, ["Potential duplicates have unknown financial values."], exact))
                continue
            account, losers = ranked[0], ranked[1:]
            if any(any(financials(a)) for a in losers) or any(a.get("parent_id") != parent_id for a in exact):
                proposals.append(proposal("investigate", facility, None, {},
                                          ["Potential duplicates cross owners or carry billing history; choose the surviving record manually."], exact))
                continue
            for loser in losers:
                changes = {"status": "Inactive", "duplicate_of_account": account["id"],
                           "note": append_note(loser, f"Confirmed duplicate of {account['id']} after Bellhaven review.")}
                proposals.append(proposal("duplicate", facility, loser, changes,
                                          ["Same normalized name and full address. Losing record has no billing history or balance."],
                                          dependencies=[account]))
        else:
            account = exact[0]
        changes = {}
        for key in ("name", "address", "city", "state", "zip"):
            if normal(account.get(key)) != normal(facility[key]):
                if key == "address" and address_key(account) == address_key(facility):
                    continue
                changes[key] = facility[key]
        if facility.get("care_offerings") is not None:
            aliases = {"short term rehabilitation nursing": "skilled nursing", "memory support": "memory care"}
            care_key = lambda x: aliases.get(normal(x), normal(x))
            current_care = {care_key(x) for x in account.get("care_offerings", [])}
            source_care = {care_key(x) for x in facility["care_offerings"]}
            compatible_primary = account.get("care_model") == "primary" and bool(current_care) and current_care <= source_care
            if current_care != source_care and not compatible_primary:
                changes["care_offerings"] = facility["care_offerings"]
        kind = "update"
        evidence = ["Full normalized street, city, state, and ZIP match.", "Website affiliation is evidence for reviewer confirmation of ownership."]
        if zip_correction:
            evidence[0] = "Exact name, normalized street, city, state, and phone match; the CRM ZIP differs from the website."
        if account.get("status") == "Inactive":
            proposals.append(proposal("investigate", facility, account, {}, ["Matching CRM account is inactive; investigate before reactivation."]))
            continue
        if account.get("parent_id") != parent_id:
            try:
                special = needs_chow(account)
            except ValueError:
                proposals.append(proposal("investigate", facility, account, {}, ["Ownership differs, but billing fields are missing or invalid."]))
                continue
            changes["parent_id"] = parent_id
            kind = "chow" if special else "reparent"
            if special:
                changes = facility_fields(facility, parent_id)
                evidence.append("Revenue history and positive outstanding AR: preserve the old account and create a successor.")
            else:
                evidence.append("Billing SOP permits moving this existing account to the correct parent.")
        if changes:
            proposals.append(proposal(kind, facility, account, changes, evidence))
        else:
            matched.append({"facility": facility, "account_id": account["id"], "classification": "confident_match"})
    if absence_allowed:
        for account in active:
            if account["id"] not in used and account.get("parent_id") == parent_id and account.get("status") != "Inactive":
                marker = "Absent from the verified Bellhaven website snapshot; ownership requires investigation."
                if account.get("status") == "Needs Review" and marker in account.get("note", ""):
                    continue
                facility = {**account, "key": "crm:" + account["id"], "source_url": None}
                proposals.append(proposal("missing", facility, account,
                                          {"status": "Needs Review", "note": append_note(account, marker)},
                                          ["Not found in the complete configured portfolio crawl. This flags investigation without changing ownership."]))
    return proposals, matched


def append_note(account, text):
    return "\n".join(filter(None, [account.get("note", ""), text]))
