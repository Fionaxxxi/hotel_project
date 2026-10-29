import json

import pytest

from infra.manage import ROOT
from services.core.g1 import matching
from services.core.g1.common import digest
from services.core.g4 import ai

CASES = json.loads((ROOT / "datasets/g4/hard-conflicts-v1.json").read_text(encoding="utf-8"))[
    "cases"
]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_frozen_hard_conflict_survives_high_model_score(case, monkeypatch):
    monkeypatch.setattr(matching, "parent_target", lambda c, s: case["parent"])
    conflicts = matching.conflicts(None, case["source"], case["target"])
    assert case["expected"] in conflicts
    inputs = {
        "source": case["source"]["data"],
        "source_hash": digest(case["source"]["data"]),
        "candidates": [
            {
                "id": case["id"],
                "version": "frozen-v1",
                "facts": case["target"],
                "rule_score": 0,
                "hard_conflicts": conflicts,
            }
        ],
    }
    ranked = ai.validate(inputs, [0.999999])
    assert ranked[0]["decision_hint"] == "CONFLICT"
    assert case["expected"] in ranked[0]["hard_conflicts"]
