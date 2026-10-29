"""Deterministic, synthetic v2 packages; no database writes."""

import json

from infra.manage import ROOT
from tests.g2_helpers import files


def main():
    for sequence, fmt in enumerate(["csv", "xlsx", "json"], 1):
        folder = ROOT / "datasets/g2/equivalent" / fmt
        folder.mkdir(parents=True, exist_ok=True)
        for _, (name, content, _) in files(fmt=fmt, code="DEMO-G2", sequence=sequence):
            (folder / name).write_bytes(content)

    def change(data, m):
        data["rates"] = [
            r for r in data["rates"] if r["plan_code"] == "FLEX" and r["stay_date"] == "2027-01-11"
        ]
        data["rates"][0]["net_amount"] = "115.00"

    folder = ROOT / "datasets/g2/delta"
    folder.mkdir(parents=True, exist_ok=True)
    for _, (name, content, _) in files(
        fmt="json", code="DEMO-G2", sequence=4, mode="delta", only=["rates"], edit=change
    ):
        (folder / name).write_bytes(content)
    folder = ROOT / "datasets/g2/quality"
    folder.mkdir(parents=True, exist_ok=True)

    def bad(data, m):
        data["inventory"][1]["quantity"] = "-1"

    for _, (name, content, _) in files(code="DEMO-G2-QUALITY", edit=bad):
        (folder / name).write_bytes(content)

    def write_package(relative, **kwargs):
        target = ROOT / "datasets/g2" / relative
        target.mkdir(parents=True, exist_ok=True)
        for _, (name, content, _) in files(**kwargs):
            (target / name).write_bytes(content)

    write_package("snapshot/base", code="DEMO-SNAPSHOT")

    def missing(data, m):
        data["rates"] = data["rates"][1:]

    write_package("snapshot/authority-missing", code="DEMO-SNAPSHOT", sequence=2, edit=missing)
    write_package("ordering/high", code="DEMO-ORDER", sequence=5)
    write_package("ordering/old", code="DEMO-ORDER", sequence=4, mode="delta", only=["rates"])
    write_package(
        "ordering/same-sequence", code="DEMO-ORDER", sequence=5, mode="delta", only=["rates"]
    )

    def tombstone(data, m):
        data["rates"] = [
            r for r in data["rates"] if r["plan_code"] == "FLEX" and r["stay_date"] == "2027-01-12"
        ]
        data["rates"][0]["operation"] = "delete"

    write_package(
        "delta-delete", code="DEMO-G2", sequence=5, mode="delta", only=["rates"], edit=tombstone
    )

    def checkpoint(data, m):
        data["rooms"] = [{**data["rooms"][0], "room_code": f"R{i:05}"} for i in range(1005)]

    write_package("recovery", code="DEMO-CHECKPOINT", edit=checkpoint)
    (ROOT / "datasets/g2/expected.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "equivalent_sequences": [1, 2, 3],
                "delta_sequence": 4,
                "delta_net_amount": "115.0000",
                "delta_sell_amount": "126.50",
                "quality_blocked_day": "2027-01-11",
            },
            indent=2,
        )
        + "\n"
    )
    print(
        "G2 synthetic equivalent, delta, quality, snapshot, ordering and checkpoint packages generated"
    )


if __name__ == "__main__":
    main()
