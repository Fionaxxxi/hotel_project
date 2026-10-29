"""G0 controlled-package contract validator; not an upload/activation service."""

import argparse
import csv
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator, FormatChecker

from services.core.domain import money

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    "properties": ["property_code", "name", "country_code", "city", "address", "timezone"],
    "rooms": ["property_code", "room_code", "name", "max_adults", "bed_type"],
    "rate_plans": [
        "property_code",
        "room_code",
        "plan_code",
        "meal",
        "cancellation",
        "payment",
        "tax_basis",
    ],
    "inventory": [
        "property_code",
        "room_code",
        "pool_code",
        "stay_date",
        "quantity",
        "stop_sell",
        "observed_at",
    ],
    "rates": [
        "property_code",
        "room_code",
        "plan_code",
        "pool_code",
        "stay_date",
        "adults",
        "currency",
        "net_amount",
    ],
}
KEYS = {
    "properties": ["property_code"],
    "rooms": ["property_code", "room_code"],
    "rate_plans": ["property_code", "room_code", "plan_code"],
    "inventory": ["property_code", "room_code", "pool_code", "stay_date"],
    "rates": ["property_code", "room_code", "plan_code", "stay_date", "adults", "currency"],
}


def validate_package(folder: Path):
    issues = []

    def issue(code, file, row=None, field=None, severity="package"):
        issues.append(
            {
                "code": code,
                "file": file,
                "row": row,
                "field": field,
                "severity": severity,
                "action": "Correct input and create a new package",
            }
        )

    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        issue("MANIFEST_INVALID", "manifest.json")
        return issues
    schema = json.loads(
        (ROOT / "contracts/files/v1/manifest.schema.json").read_text(encoding="utf-8")
    )
    for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest):
        issue("MANIFEST_SCHEMA", "manifest.json", field="/".join(map(str, error.path)))
    if issues:
        return issues
    scope = manifest["scope"]
    if scope["stay_date_from"] >= scope["stay_date_to_exclusive"]:
        issue("INVALID_SCOPE", "manifest.json", field="scope")
    records = {}
    names = [f["name"] for f in manifest["files"]]
    if len(names) != len(set(names)):
        issue("DUPLICATE_FILE", "manifest.json", field="files")
    for file in manifest["files"]:
        name = file["name"]
        kind = name.removesuffix(".csv")
        if kind not in REQUIRED or name != f"{kind}.csv":
            issue("UNSUPPORTED_FILE", name)
            continue
        try:
            content = (folder / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != file["sha256"]:
                issue("HASH_MISMATCH", name)
                continue
            with (folder / name).open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                headers = reader.fieldnames or []
                for field in REQUIRED[kind]:
                    if field not in headers:
                        issue("REQUIRED_COLUMN", name, row=1, field=field)
                if len(headers) != len(set(headers)) or set(headers) - set(REQUIRED[kind]):
                    issue("INVALID_COLUMNS", name, row=1)
                records[kind] = list(reader)
        except (OSError, UnicodeError, csv.Error):
            issue("FILE_INVALID", name)
    if issues:
        return issues
    # G0 fixtures are self-contained; partial delta reference resolution is a G1/G2 concern.
    if set(records) != set(REQUIRED):
        issue("INCOMPLETE_PACKAGE", "manifest.json", field="files")
        return issues
    if set(scope["entities"]) != set(records):
        issue("SCOPE_ENTITY_MISMATCH", "manifest.json", field="scope/entities")
    props = {r["property_code"] for r in records["properties"]}
    rooms = {(r["property_code"], r["room_code"]) for r in records["rooms"]}
    plans = {(r["property_code"], r["room_code"], r["plan_code"]) for r in records["rate_plans"]}
    pools = {
        (r["property_code"], r["room_code"], r["pool_code"], r["stay_date"])
        for r in records["inventory"]
    }
    for kind, rows in records.items():
        seen = set()
        for row_number, row in enumerate(rows, 2):
            filename = f"{kind}.csv"
            if None in row:
                issue("COLUMN_COUNT", filename, row_number, severity="partition")
            for field in REQUIRED[kind]:
                if row.get(field) in (None, ""):
                    issue("REQUIRED_VALUE", filename, row_number, field, "partition")
            key = tuple(row.get(k) for k in KEYS[kind])
            if key in seen:
                issue("DUPLICATE_NATURAL_KEY", filename, row_number, severity="partition")
            seen.add(key)
            if kind != "properties" and row.get("property_code") not in props:
                issue("PARENT_NOT_FOUND", filename, row_number, "property_code", "partition")
            if (
                kind in {"rate_plans", "inventory", "rates"}
                and (row.get("property_code"), row.get("room_code")) not in rooms
            ):
                issue("PARENT_NOT_FOUND", filename, row_number, "room_code", "partition")
            if (
                kind == "rates"
                and (row.get("property_code"), row.get("room_code"), row.get("plan_code"))
                not in plans
            ):
                issue("PARENT_NOT_FOUND", filename, row_number, "plan_code", "partition")
            if row.get("property_code") not in scope["property_codes"]:
                issue("OUTSIDE_SCOPE", filename, row_number, "property_code", "partition")
            for field in REQUIRED[kind]:
                value = row.get(field)
                if value is None:
                    continue
                try:
                    if field in {"quantity", "max_adults", "adults"}:
                        if (
                            not value.isascii()
                            or not value.isdecimal()
                            or int(value) < (0 if field == "quantity" else 1)
                        ):
                            raise ValueError()
                    if field == "adults" and value != "2":
                        raise ValueError()
                    if field == "net_amount":
                        money(value)
                    if field == "currency" and value != "USD":
                        raise ValueError()
                    if field == "country_code" and value != "CN":
                        raise ValueError()
                    if field == "bed_type" and value not in {"KING", "TWIN", "UNKNOWN"}:
                        raise ValueError()
                    if field == "stop_sell" and value not in {"true", "false"}:
                        raise ValueError()
                    if field == "timezone":
                        ZoneInfo(value)
                    if field == "stay_date":
                        date.fromisoformat(value)
                        if not scope["stay_date_from"] <= value < scope["stay_date_to_exclusive"]:
                            raise ValueError()
                    if field == "observed_at":
                        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        if parsed.utcoffset() is None:
                            raise ValueError()
                    if field == "cancellation":
                        policy = json.loads(value)
                        if (
                            policy.get("kind") not in {"FLEX", "NONREF"}
                            or policy.get("penalty_basis") != "stay_total"
                        ):
                            raise ValueError()
                        hours = policy.get("deadline_hours")
                        if type(hours) is not int or hours < 0:
                            raise ValueError()
                    if field == "meal" and value not in {"BREAKFAST_2", "NONE", "UNKNOWN"}:
                        raise ValueError()
                    if field == "payment" and value not in {"AT_PROPERTY", "PREPAID", "UNKNOWN"}:
                        raise ValueError()
                    if field == "tax_basis" and value not in {
                        "NET_EXCLUSIVE",
                        "GROSS_INCLUSIVE",
                        "UNKNOWN",
                    }:
                        raise ValueError()
                except (ValueError, ArithmeticError, ZoneInfoNotFoundError, AttributeError):
                    issue("INVALID_VALUE", filename, row_number, field, "partition")
            if (
                kind == "rates"
                and (row["property_code"], row["room_code"], row["pool_code"], row["stay_date"])
                not in pools
            ):
                issue("POOL_NOT_FOUND", filename, row_number, "pool_code", "partition")
    return issues


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    result = validate_package(args.folder)
    print(json.dumps({"valid": not result, "issues": result}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result else 0)
