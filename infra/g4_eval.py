"""Frozen synthetic group-separated comparison; raw predictions retained, no performance claims."""

import hashlib
import json
import math
import os
import statistics
import time
from difflib import SequenceMatcher

import psutil

from infra.manage import ROOT
from services.core.g4.local_model import DIRECTORY, LocalModel


def wilson(correct, total):
    if not total:
        return None
    z = 1.96
    p = correct / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    error = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return [round(center - error, 4), round(center + error, 4)]


def dataset():
    names = [
        ("海岚", "Hailan"),
        ("月湾", "Moon Bay"),
        ("金松", "Golden Pine"),
        ("银湖", "Silver Lake"),
        ("青竹", "Green Bamboo"),
        ("白沙", "White Sand"),
        ("红枫", "Red Maple"),
        ("蓝港", "Blue Harbor"),
        ("星河", "Star River"),
        ("云山", "Cloud Mountain"),
    ]
    rows = []
    for group in range(50):
        zh, en = names[group % 10]
        district = group // 10 + 1
        city = ["Shanghai", "Hangzhou", "Suzhou", "Nanjing", "Ningbo"][group // 10]
        match = group < 35
        candidates = [
            {
                "id": f"p-{group}-{i}",
                "name": f"{en if i == 0 else names[(group + i) % 10][1]} Hotel {'Bay' if i == 0 else 'Airport'} Branch {district} {city}",
            }
            for i in range(5)
        ]
        for variant in range(4):
            query = (
                [
                    f"{zh}海湾酒店 {district}店 {city}",
                    f"{en} Bay Hotel No.{district} {city}",
                    f"{zh}滨海店 {district} {city}",
                    f"{en} seaside accommodation branch {district} {city}",
                ][variant]
                if match
                else f"不存在的{zh}青年旅舍 青年公寓 独立物业{group}-{variant}"
            )
            rows.append(
                {
                    "id": f"q-{group}-{variant}",
                    "group": group,
                    "split": "dev" if group % 2 == 0 else "test",
                    "query": query,
                    "candidates": candidates,
                    "gold": candidates[0]["id"] if match else None,
                    "subset": "cross_language" if variant in {0, 2} else "alias",
                }
            )
    return rows


def metrics(rows, engine, threshold):
    match = [r for r in rows if r["gold"] is not None]
    none = [r for r in rows if r["gold"] is None]
    correct = sum(r[engine][0]["id"] == r["gold"] for r in match)
    false = sum(r[engine][0]["score"] >= threshold for r in none)
    high = [r for r in rows if r[engine][0]["score"] >= threshold]
    high_correct = sum(r["gold"] is not None and r[engine][0]["id"] == r["gold"] for r in high)
    return {
        "total": len(rows),
        "match_count": len(match),
        "none_count": len(none),
        "candidate_recall_at_10": sum(
            any(c["id"] == r["gold"] for c in r["candidates"]) for r in match
        )
        / len(match),
        "candidate_recall_limit": "preconstructed candidate package; not a measured database recall pipeline",
        "top1_correct": correct,
        "top1_accuracy": correct / len(match),
        "top1_wilson95": wilson(correct, len(match)),
        "mrr": statistics.mean(
            next(1 / (i + 1) for i, c in enumerate(r[engine]) if c["id"] == r["gold"])
            for r in match
        ),
        "none_false_suggestions": false,
        "none_false_suggestion_rate": false / len(none),
        "suggestion_threshold": threshold,
        "high_bucket_count": len(high),
        "high_bucket_correct": high_correct,
        "high_bucket_precision": high_correct / len(high) if high else None,
        "high_bucket_coverage": len(high) / len(rows),
        "high_bucket_wilson95": wilson(high_correct, len(high)),
    }


def main():
    os.environ["G4_AI_ENABLED"] = "true"
    target = ROOT / "datasets/g4"
    target.mkdir(parents=True, exist_ok=True)
    rows = dataset()
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path = target / "matching-frozen.json"
    if path.exists() and path.read_text(encoding="utf-8") != serialized:
        raise RuntimeError("Frozen evaluation data changed; create a new dataset version")
    path.write_text(serialized, encoding="utf-8")
    model = LocalModel()
    timings = []
    rss = []
    start = time.monotonic()
    try:
        for row in rows:
            texts = [row["query"], *[c["name"] for c in row["candidates"]]]
            outcome = model.rank(texts)
            timings.append(outcome["wall_seconds"])
            rss.append(psutil.Process(model.process.pid).memory_info().rss)
            row["model"] = sorted(
                [
                    {"id": c["id"], "score": s}
                    for c, s in zip(row["candidates"], outcome["scores"], strict=True)
                ],
                key=lambda x: -x["score"],
            )
            row["rule"] = sorted(
                [
                    {
                        "id": c["id"],
                        "score": SequenceMatcher(
                            None, row["query"].casefold(), c["name"].casefold()
                        ).ratio(),
                    }
                    for c in row["candidates"]
                ],
                key=lambda x: -x["score"],
            )
        dev = [r for r in rows if r["split"] == "dev"]
        thresholds = {}
        for engine in ["model", "rule"]:
            # Calibrate NONE rejection solely on dev; freezes before test summary.
            none_max = max(r[engine][0]["score"] for r in dev if r["gold"] is None)
            thresholds[engine] = min(1.000001, none_max + 0.000001)
        report = {
            "dataset_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "synthetic": True,
            "independent_human_adjudication": False,
            "group_count": 50,
            "query_count": 200,
            "model_manifest": json.loads((DIRECTORY / "manifest.json").read_text()),
            "thresholds_dev_only": thresholds,
            "metrics": {
                split: {
                    engine: metrics(
                        [r for r in rows if r["split"] == split], engine, thresholds[engine]
                    )
                    for engine in ["rule", "model"]
                }
                for split in ["dev", "test"]
            },
            "runtime": {
                "cold_first_seconds": timings[0],
                "warm_sample_count": len(timings) - 1,
                "warm_p50_seconds": statistics.median(timings[1:]),
                "warm_p95_seconds": sorted(timings[1:])[math.ceil(0.95 * (len(timings) - 1)) - 1],
                "warm_max_seconds": max(timings[1:]),
                "peak_model_rss_mib": max(rss) / 1048576,
                "total_seconds": time.monotonic() - start,
                "threads": 2,
                "cache": "disabled for every query",
                "scope": "model subprocess only; excludes PostgreSQL and API",
            },
            "default_ranking": "explicit opt-in advisory; no automatic mapping approval",
            "limitations": [
                "synthetic preconstructed candidates, no real-world generalization claim",
                "group-separated variants, limited invented names and templates",
                "candidate recall is package membership, not an actual search recall benchmark",
                "no independent two-person gold adjudication",
            ],
        }
        acceptance = ROOT / "docs/acceptance"
        (acceptance / "g4-ai-eval.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (acceptance / "g4-model-runs.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
        lines = [
            "# G4 实际本地模型与规则对照",
            "",
            "固定200个合成query，50个物业组，按组奇偶切分dev/test；没有真实酒店数据或独立双人标注。候选为预构建包，Recall仅表示包内覆盖，不冒称数据库召回能力。",
            "",
            "| split | 引擎 | MATCH数 | Top1正确 | Top1准确率 | NONE误建议/分母 | 高分桶覆盖 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for split, engines in report["metrics"].items():
            for engine, m in engines.items():
                lines.append(
                    f"| {split} | {engine} | {m['match_count']} | {m['top1_correct']} | {m['top1_accuracy']:.3f} | {m['none_false_suggestions']}/{m['none_count']} | {m['high_bucket_coverage']:.3f} |"
                )
        lines += [
            "",
            "阈值只用dev的NONE样本校准，随后用于test。分数不是概率；所有映射仍人工。详见JSON中的置信区间、原始预测、资源口径及限制。",
            "",
            f"热推理{len(timings) - 1}次，p95={report['runtime']['warm_p95_seconds']:.4f}秒；模型RSS峰值{report['runtime']['peak_model_rss_mib']:.1f}MiB；不含数据库/API。",
        ]
        (acceptance / "g4-ai-eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("Actual local model evaluation complete: 200 queries / 50 separated property groups")
    finally:
        model.close()


if __name__ == "__main__":
    main()
