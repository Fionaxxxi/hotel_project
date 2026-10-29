"""Authored synthetic hard conflicts; expectations are independent of matching code."""

import copy
import json

from infra.manage import ROOT


def generate():
    cases = []

    def add(source, target, expected, parent=None):
        cases.append(
            {
                "id": f"HC-{len(cases) + 1:02d}",
                "source": source,
                "target": target,
                "expected": expected,
                "parent": parent,
            }
        )

    for country in ["JP", "US", "SG", "GB", "AU"]:
        add(
            {"kind": "property", "data": {"country_code": "CN", "city": "Same City"}},
            {"country_code": country, "city": "Same City"},
            "LOCATION_CONFLICT",
        )
    for a, b in [
        ("Xiamen", "Shanghai"),
        ("Shanghai", "Beijing"),
        ("Hangzhou", "Suzhou"),
        ("Nanjing", "Wuhan"),
        ("Xiamen", "Quanzhou"),
    ]:
        add(
            {"kind": "property", "data": {"country_code": "CN", "city": a}},
            {"country_code": "CN", "city": b},
            "LOCATION_CONFLICT",
        )
    for occupancy in [2, 3]:
        for a, b in [
            ("KING", "TWIN"),
            ("TWIN", "KING"),
            ("UNKNOWN", "KING"),
            ("UNKNOWN", "TWIN"),
            ("KING", "UNKNOWN"),
        ]:
            add(
                {"kind": "room", "data": {"bed_type": a, "max_adults": str(occupancy)}},
                {"property_id": "hotel-1", "bed_type": b, "max_adults": occupancy},
                "BED_CONFLICT",
                "hotel-1",
            )
    for a, b in [(1, 2), (2, 1), (2, 3), (3, 2), (3, 4), (4, 3)]:
        add(
            {"kind": "room", "data": {"bed_type": "KING", "max_adults": str(a)}},
            {"property_id": "hotel-1", "bed_type": "KING", "max_adults": b},
            "OCCUPANCY_CONFLICT",
            "hotel-1",
        )
    for parent in ["hotel-2", "hotel-3", "hotel-4", "hotel-5"]:
        add(
            {"kind": "room", "data": {"bed_type": "KING", "max_adults": "2"}},
            {"property_id": parent, "bed_type": "KING", "max_adults": 2},
            "PARENT_CONFLICT",
            "hotel-1",
        )
    nonref = {"kind": "NONREF", "deadline_hours": 0, "penalty_basis": "stay_total"}
    base = {
        "meal": "BREAKFAST_2",
        "payment": "AT_PROPERTY",
        "tax_basis": "NET_EXCLUSIVE",
        "cancellation": nonref,
    }

    def rate(field, a, b, expected):
        source = copy.deepcopy(base)
        target = copy.deepcopy(base)
        source[field] = a
        target[field] = b
        if isinstance(source["cancellation"], dict):
            source["cancellation"] = json.dumps(source["cancellation"], sort_keys=True)
        add({"kind": "rate", "data": source}, {**target, "plan_id": None}, expected, "room-1")

    for hours in [24, 48, 72, 96, 120]:
        flex = {"kind": "FLEX", "deadline_hours": hours, "penalty_basis": "stay_total"}
        rate("cancellation", nonref, flex, "CANCELLATION_CONFLICT")
        rate("cancellation", flex, nonref, "CANCELLATION_CONFLICT")
    for a, b in [
        ("NONE", "BREAKFAST_1"),
        ("NONE", "BREAKFAST_2"),
        ("BREAKFAST_1", "NONE"),
        ("BREAKFAST_2", "NONE"),
        ("BREAKFAST_1", "BREAKFAST_2"),
        ("UNKNOWN", "NONE"),
    ]:
        rate("meal", a, b, "MEAL_CONFLICT")
    for a, b in [
        ("AT_PROPERTY", "PREPAY"),
        ("PREPAY", "AT_PROPERTY"),
        ("UNKNOWN", "AT_PROPERTY"),
        ("UNKNOWN", "PREPAY"),
        ("AT_PROPERTY", "UNKNOWN"),
        ("PREPAY", "UNKNOWN"),
    ]:
        rate("payment", a, b, "PAYMENT_CONFLICT")
    for a, b in [
        ("NET_EXCLUSIVE", "GROSS_INCLUSIVE"),
        ("GROSS_INCLUSIVE", "NET_EXCLUSIVE"),
        ("UNKNOWN", "NET_EXCLUSIVE"),
        ("UNKNOWN", "GROSS_INCLUSIVE"),
        ("NET_EXCLUSIVE", "UNKNOWN"),
        ("GROSS_INCLUSIVE", "UNKNOWN"),
    ]:
        rate("tax_basis", a, b, "TAX_BASIS_CONFLICT")
    for malformed in ["{", "unknown"]:
        rate("cancellation", malformed, nonref, "CANCELLATION_UNKNOWN")
    assert len(cases) == 60
    assert (
        len({json.dumps({k: v for k, v in c.items() if k != "id"}, sort_keys=True) for c in cases})
        == 60
    )
    return cases


def main():
    target = ROOT / "datasets/g4/hard-conflicts-v1.json"
    value = (
        json.dumps(
            {
                "version": "g4-hard-conflicts-v1",
                "synthetic": True,
                "independent_human_adjudication": False,
                "cases": generate(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    if target.exists() and target.read_text(encoding="utf-8") != value:
        raise RuntimeError("Frozen hard-conflict data changed")
    target.write_text(value, encoding="utf-8")
    print("Frozen 60 distinct authored synthetic conflict pairs")


if __name__ == "__main__":
    main()
