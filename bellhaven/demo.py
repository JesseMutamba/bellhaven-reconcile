def demo_accounts():
    def account(account_id, name, street="", city="", zip_code="", **extra):
        return {"id": account_id, "name": name, "address": street, "city": city, "state": "OH", "zip": zip_code,
                "account_kind": "facility", "parent_id": "bellhaven-parent", "status": "Active", "version": 1,
                "care_offerings": ["Assisted Living"], "lifetime_revenue": 0, "outstanding_ar": 0,
                "note": "", "duplicate_of_account": None, "chow_current_account": None, **extra}
    return [
        account("bellhaven-parent", "Bellhaven Senior Living", account_kind="corporate", parent_id=None),
        account("harborview-parent", "Harborview Senior Care", account_kind="corporate", parent_id=None),
        account("cedar", "Cedar Grove Senior Residence", "120 Oak Street", "Columbus", "43215",
                parent_id="harborview-parent", lifetime_revenue=124500, outstanding_ar=9500),
        account("maple", "Bellhaven of Maplewood", "210 Maple Rd", "Maplewood", "45340",
                parent_id="harborview-parent", lifetime_revenue=45000),
        account("oak", "Oak Ridge Care Center", "44 Ridge Ave", "Akron", "44301"),
        account("brook", "Bellhaven Brookside", "500 Brook Dr", "Dayton", "45402"),
        account("river", "Bellhaven Riverside", "82 River Ln", "Toledo", "43604", lifetime_revenue=32000),
        account("river-copy", "Bellhaven Riverside", "82 River Lane", "Toledo", "43604"),
        account("meadow-a", "Meadow Campus Assisted Living", "19 Meadow Ct", "Canton", "44702"),
        account("meadow-b", "Meadow Campus Memory Care", "19 Meadow Ct", "Canton", "44702"),
        account("lake", "Bellhaven Lakeside", "18 Lake Dr", "Mansfield", "44902"),
    ]
