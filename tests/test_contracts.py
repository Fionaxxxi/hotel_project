import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from services.core.domain import money, shared_quantity, stay_dates, usd
from services.core.file_contract import validate_package

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "folder", sorted((ROOT / "datasets/fixtures").glob("F*")), ids=lambda p: p.name
)
def test_fixture(folder):
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    issues = validate_package(folder)
    assert (not issues) == expected["valid"]
    if not expected["valid"]:
        assert expected["required_error"] in {i["code"] for i in issues}
        assert all(i["file"] and i["action"] for i in issues)


def test_domain_semantics():
    assert stay_dates(date(2027, 1, 10), date(2027, 1, 13)) == [
        date(2027, 1, d) for d in [10, 11, 12]
    ]
    assert usd(money("100.0050")) == Decimal("100.01")
    assert shared_quantity(["P1", "P1"], {"P1": 4}) == 4
    assert shared_quantity(["P1"], {"P1": None}) is None
    assert shared_quantity(["P1"], {"P1": 0}) == 0
    with pytest.raises(ValueError):
        shared_quantity(["P1", "P2"], {"P1": 4, "P2": 4})
    with pytest.raises(ValueError):
        stay_dates(date(2027, 1, 13), date(2027, 1, 10))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "100000000000000", "0.00001"])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        money(value)


def test_leading_zero():
    import csv

    with (ROOT / "datasets/fixtures/F00-valid/properties.csv").open(encoding="utf-8") as f:
        assert next(csv.DictReader(f))["property_code"] == "001"
