"""Reproducible synthetic packages, schemas and local secret initialization."""

import csv
import hashlib
import json
import secrets
import shutil
from pathlib import Path

from services.core.file_contract import REQUIRED

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "supplier_code",
            "source_sequence",
            "generated_at",
            "mode",
            "scope",
            "files",
            "contract_version",
        ],
        "properties": {
            "schema_version": {"const": "1.0"},
            "supplier_code": {"type": "string", "minLength": 1},
            "source_sequence": {"type": "integer", "minimum": 1},
            "generated_at": {"type": "string", "format": "date-time"},
            "mode": {"enum": ["snapshot", "delta"]},
            "contract_version": {"type": "string", "minLength": 1},
            "scope": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "property_codes",
                    "entities",
                    "stay_date_from",
                    "stay_date_to_exclusive",
                ],
                "properties": {
                    "property_codes": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "entities": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"enum": list(REQUIRED)},
                    },
                    "stay_date_from": {"type": "string", "format": "date"},
                    "stay_date_to_exclusive": {"type": "string", "format": "date"},
                },
            },
            "files": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "sha256"],
                    "properties": {
                        "name": {"enum": [f"{k}.csv" for k in REQUIRED]},
                        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    },
                },
            },
        },
    }
    write_json(ROOT / "contracts/files/v1/manifest.schema.json", schema)
    base = ROOT / "datasets/fixtures/F00-valid"
    base.mkdir(parents=True, exist_ok=True)

    def policy(kind):
        return json.dumps(
            {
                "kind": kind,
                "deadline_hours": 72 if kind == "FLEX" else 0,
                "penalty_basis": "stay_total",
            }
        )

    data = {
        "properties": [
            [
                "001",
                "海岚滨海酒店 / Hailan Bay Hotel",
                "CN",
                "厦门",
                "虚构海湾路1号",
                "Asia/Shanghai",
            ]
        ],
        "rooms": [
            ["001", "R1", "Deluxe King", "2", "KING"],
            ["001", "R2", "Deluxe Twin", "2", "TWIN"],
        ],
        "rate_plans": [
            ["001", "R1", kind, "BREAKFAST_2", policy(kind), "AT_PROPERTY", "NET_EXCLUSIVE"]
            for kind in ["FLEX", "NONREF"]
        ],
        "inventory": [
            ["001", "R1", "POOL-1", f"2027-01-{day}", "4", "false", "2027-01-01T02:00:00Z"]
            for day in [10, 11, 12]
        ],
        "rates": [
            ["001", "R1", kind, "POOL-1", f"2027-01-{day}", "2", "USD", "100.00"]
            for kind in ["FLEX", "NONREF"]
            for day in [10, 11, 12]
        ],
    }
    for kind, rows in data.items():
        with (base / f"{kind}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(REQUIRED[kind])
            writer.writerows(rows)
    manifest = {
        "schema_version": "1.0",
        "supplier_code": "S-A",
        "source_sequence": 1,
        "generated_at": "2027-01-01T02:00:00Z",
        "mode": "snapshot",
        "contract_version": "supplier-demo-v1",
        "scope": {
            "property_codes": ["001"],
            "entities": list(REQUIRED),
            "stay_date_from": "2027-01-10",
            "stay_date_to_exclusive": "2027-01-13",
        },
        "files": [],
    }

    def hashes(folder, man):
        man["files"] = [
            {
                "name": f"{k}.csv",
                "sha256": hashlib.sha256((folder / f"{k}.csv").read_bytes()).hexdigest(),
            }
            for k in REQUIRED
        ]
        write_json(folder / "manifest.json", man)

    hashes(base, manifest)
    variants = {
        "F01-invalid-inventory": "INVALID_VALUE",
        "F02-missing-parent": "PARENT_NOT_FOUND",
        "F03-conflicting-key": "DUPLICATE_NATURAL_KEY",
        "F04-invalid-scope": "MANIFEST_SCHEMA",
        "F05-hash-mismatch": "HASH_MISMATCH",
    }
    for name, code in variants.items():
        folder = base.parent / name
        shutil.copytree(base, folder, dirs_exist_ok=True)
        man = json.loads(json.dumps(manifest))
        if name.startswith("F01"):
            p = folder / "inventory.csv"
            p.write_text(
                p.read_text(encoding="utf-8").replace(",4,false", ",-1,false", 1),
                encoding="utf-8",
                newline="",
            )
        elif name.startswith("F02"):
            p = folder / "rooms.csv"
            p.write_text(
                p.read_text(encoding="utf-8").replace("001,R2", "999,R2"),
                encoding="utf-8",
                newline="",
            )
        elif name.startswith("F03"):
            p = folder / "inventory.csv"
            content = p.read_text(encoding="utf-8")
            p.write_text(
                content + content.splitlines()[1].replace(",4,false", ",8,false") + "\n",
                encoding="utf-8",
                newline="",
            )
        elif name.startswith("F04"):
            del man["scope"]["stay_date_to_exclusive"]
        hashes(folder, man)
        if name.startswith("F05"):
            with (folder / "inventory.csv").open("a", encoding="utf-8") as f:
                f.write("\n")
        write_json(folder / "expected.json", {"valid": False, "required_error": code})
    write_json(base / "expected.json", {"valid": True, "required_error": None})
    seed_dir = ROOT / "datasets/seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    from infra.manage import uid

    write_json(
        seed_dir / "identities.json",
        {
            label: {
                "tenant_id": str(uid(f"tenant-{label}")),
                "property_id": str(uid(f"property-{label}")),
                "subjects": [
                    f"{label.lower()}.{role}"
                    for role in ["tenant_admin", "operator", "reviewer", "auditor"]
                ],
            }
            for label in ["A", "B"]
        },
    )
    print("Schemas and F00-F05 fixtures generated")


def init_env():
    path = ROOT / ".env"
    if path.exists():
        print("Existing .env retained")
        return
    admin, migrator, app, login = [secrets.token_urlsafe(24) for _ in range(4)]
    path.write_text(
        "\n".join(
            [
                "APP_ENV=development",
                "DEV_AUTH_ENABLED=true",
                f"DEV_LOGIN_PASSWORD={login}",
                f"JWT_SECRET={secrets.token_urlsafe(48)}",
                f"HOTEL_POSTGRES_PASSWORD={admin}",
                f"MIGRATION_PASSWORD={migrator}",
                f"APP_DB_PASSWORD={app}",
                f"BOOTSTRAP_DATABASE_URL=postgresql://postgres:{admin}@127.0.0.1:55432/postgres",
                f"MIGRATION_DATABASE_URL=postgresql://hotel_migrator:{migrator}@127.0.0.1:55432/hotel_project",
                f"DATABASE_URL=postgresql://hotel_app:{app}@127.0.0.1:55432/hotel_project",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print("Local .env generated; secrets are not printed")


if __name__ == "__main__":
    init_env()
    generate()
