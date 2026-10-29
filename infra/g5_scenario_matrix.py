"""Create all fifty plan rows, keeping unproven scenes explicitly pending."""

import json
import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from infra.manage import ROOT

TARGET = ROOT / "docs/acceptance"


def artifact(name, statuses=("PASSED",)):
    path = TARGET / name
    return path.exists() and json.loads(path.read_text(encoding="utf-8")).get("status") in statuses


def main():
    summary = json.loads((TARGET / "g5-acceptance-summary.json").read_text(encoding="utf-8"))
    checks = summary["checks"]
    cases = list(ET.parse(TARGET / "g5-tests.xml").getroot().iter("testcase"))

    def tests(*selectors):
        if len(cases) < 305:
            return False
        for selector in selectors:
            chosen = [t for t in cases if selector in t.get("classname", "") or selector in t.get("name", "")]
            if not chosen or any(t.find("failure") is not None or t.find("error") is not None or t.find("skipped") is not None for t in chosen):
                return False
        return True

    browser = artifact("g5-browser.json")
    setup = artifact("g5-setup-idempotence.json")
    restore = artifact("g5-restore-summary.json")
    backup_failure = artifact("g5-backup-failure-drill.json")
    rules = {
        2: (setup and tests("test_migrate_and_seed_repeat", "test_application_role_and_policies"), ["g5-setup-idempotence.json", "g5-tests.xml"]),
        3: (artifact("g5-security-matrix.json") and tests("test_g5_endpoint_permissions", "test_cross_tenant_roles_and_no_endpoints_injection"), ["g5-security-matrix.json", "g5-tests.xml"]),
        4: (tests("test_cross_tenant_api_and_pool_reuse", "test_ai_and_policy_cross_tenant_hidden", "test_direct_rls_reads_writes_rollback"), ["g5-tests.xml"]),
        5: (tests("test_cross_tenant_api_and_pool_reuse", "test_tenant_metrics_do_not_expose_global_connection_counters"), ["g5-tests.xml"]),
        6: (tests("test_reject_credentials", "test_disabled_membership", "test_development_login", "test_login_rate_shared_database"), ["g5-tests.xml"]),
        7: (tests("test_self_approval_and_stale_source", "test_g4_policy_self_approval_denied", "test_public_suggestion_request_cannot_supply_approval_or_model_output"), ["g5-tests.xml"]),
        8: (tests("test_upload_limits_and_delta", "test_xlsx_archive_guards", "test_windows_xlsx_traversal_rejected", "test_deep_json_rejected"), ["g5-tests.xml"]),
        9: (browser and tests("test_actual_model_treats_hostile_long_text_as_data"), ["g5-browser.json", "g5-tests.xml"]),
        10: (tests("test_quality_csv_actual_export_escapes_leading_control_formula", "test_safe_formula_export"), ["g5-tests.xml"]),
        11: (tests("test_cross_tenant_roles_and_no_endpoints_injection", "test_isolated_profile_cannot_redirect_normal_core", "test_g5_offline"), ["g5-tests.xml"]),
        14: (tests("test_metrics_are_tenant_scoped_and_read_only"), ["g5-tests.xml", "g5-final-integration-supplement.xml"]),
        15: (tests("test_all_operational_risk_kinds", "test_alert_discover_acknowledge_resolve"), ["g5-tests.xml"]),
        16: (tests("test_all_operational_risk_kinds", "test_g3_unknown_and_retry_classification"), ["g5-tests.xml"]),
        17: (tests("test_tenant_metrics_do_not_expose_global_connection_counters", "test_failed_collection_does_not_resolve_risk", "test_all_operational_risk_kinds"), ["g5-tests.xml"]),
        18: (tests("test_failed_collection_does_not_resolve_risk"), ["g5-tests.xml", "g5-final-integration-supplement.xml"]),
        20: (artifact("g5-original-locators.json"), ["g5-original-locators.json"]),
        21: (browser and tests("test_actual_published_audit_graph_and_export"), ["g5-browser.json", "g5-tests.xml"]),
        22: (checks.get("audit_20_stratified", False) and artifact("g5-http-smoke.json"), ["g5-audit-sampling.json", "g5-http-smoke.json"]),
        23: (tests("test_actual_published_audit_graph_and_export"), ["g5-tests.xml", "g5-final-integration-supplement.xml"]),
        24: (tests("test_redaction_and_integrity", "test_actual_published_audit_graph_and_export", "test_g5_endpoint_permissions"), ["g5-tests.xml"]),
        25: (backup_failure and checks.get("daily_backup_action", False) and tests("test_backup_refuses_managed_process_marker"), ["g5-backup-failure-drill.json", "g5-backup-schedule.json", "g5-tests.xml"]),
        26: (backup_failure and tests("test_dump_disk_full_never_creates_complete_backup"), ["g5-backup-failure-drill.json", "g5-tests.xml"]),
        27: (restore and tests("test_recovery_path_guard", "test_restore_namespace_defaults_readonly", "test_dump_disk_full_never_creates_complete_backup"), ["g5-restore-summary.json", "g5-tests.xml"]),
        28: (restore, ["g5-restore-summary.json"]),
        29: (restore and tests("test_recovery_fresh_stop_and_release_retains_stop_fence"), ["g5-restore-summary.json", "g5-tests.xml"]),
        30: (restore and tests("test_recovery_fresh_stop_and_release_retains_stop_fence", "test_recovery_fence_prevents_new_publish"), ["g5-restore-summary.json", "g5-tests.xml"]),
        31: (tests("test_recovery_unknown_read_never_permits_stop_or_release"), ["g5-tests.xml"]),
        33: (browser, ["g5-browser.json"]),
        34: (browser, ["g5-browser.json"]),
        35: (browser, ["g5-browser.json", "g5-browser-desktop.png", "g5-browser-narrow.png"]),
        36: (browser, ["g5-browser.json"]),
        43: (checks.get("complete_regression", False) and checks.get('final_normal_services',False) and tests("test_g5_risk_matrix", "test_g3_relay_crash_preserves_jobs_exactly_once", 'test_distribution_worker_survives_pool_timeout'), ["g5-tests.xml", "g5-environment.json", 'g5-final-health.json']),
        44: (setup and tests("test_g5_process"), ["g5-setup-idempotence.json", "g5-tests.xml"]),
        46: (checks.get("local_isolated_reproduction_twice", False) and tests("test_model_disabled_real_manual_mapping_and_publication", "test_g5_offline"), ["g5-local-reproduction.json", "g5-tests.xml"]),
    }
    evidence = {
        1: ("baseline", ["g5-baseline.xml"]),
        12: ("secret_scan", ["g5-secret-scan.json"]),
        13: ("actual_cross_process_trace", ["g5-trace.json"]),
        19: ("audit_20_stratified", ["g5-audit-sampling.json"]),
        32: ("daily_backup_action", ["g5-backup-schedule.json", "g5-restore-summary.json"]),
        37: ("three_format_parse", ["g5-parse-benchmark.json"]),
        38: ("query_100_samples", ["g5-query-benchmark.json", "g5-protocol-benchmark.json"]),
        39: ("normal_load_50", ["g5-load.json"]),
        40: ("actual_ai_queued", ["g5-load.json"]),
        41: ("continuous_60_minutes", ["g5-soak.json", "g5-soak-closure.json"]),
        42: ("100_duplicate_reorders", ["g5-duplicate-delivery.json"]),
        45: ("local_isolated_reproduction_twice", ["g5-local-reproduction.json"]),
        48: ("handoff_bundle", ["g5-release.json"]),
        49: ("demonstration_recordings", ["g5-video.json"]),
    }
    baseline = list(ET.parse(TARGET / "g5-baseline.xml").getroot().iter("testcase"))
    checks["baseline"] = bool(baseline) and not any(t.find("failure") is not None or t.find("error") is not None or t.find("skipped") is not None for t in baseline)
    rows = []
    for line in (ROOT / "G5阶段详细实施计划.md").read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| (\d{2}) \| (P[01]) \| (.*?) \|", line)
        if not match:
            continue
        ident, priority, description = match.groups()
        num = int(ident)
        key, files = evidence.get(num, ("", []))
        status = "PASS" if key and checks.get(key) else "PENDING"
        note = "按本机范围验证；证据、历史失败和边界见验收报告。" if status == "PASS" else "尚未取得覆盖本行全部要求的最终证据。"
        if num == 47:
            status, note = "DEFERRED_BY_USER", "用户确认暂无独立 Windows 环境，先完成本机范围。"
        if num == 49 and artifact("g5-video.json", ("DEFERRED_BY_USER",)):
            status, note = "DEFERRED_BY_USER", "用户明确选择不录屏，录像列为待补交付项；截图不代替录像。"
        if num in {32, 38, 45}:
            # These rows contain multiple independent conditions, never infer all from one check.
            requirements = {32: ["daily_backup_action", "two_restores_and_rpo_rto"], 38: ["query_100_samples", "protocol_three_sizes"], 45: ["local_isolated_reproduction_twice"]}[num]
            status = "PASS" if all(checks.get(k) for k in requirements) else "PENDING"
        if num in rules:
            passed, files = rules[num]
            status = "PASS" if passed else "PENDING"
        if num == 50:
            status = "PASS" if all(x["status"] == "PASS" for x in rows if x["priority"] == "P0" and x["id"] != "47") and (TARGET / "g5-report.md").exists() else "PENDING"
            files = ["g5-report.md", "g5-acceptance-summary.json"]
        if status == 'PASS':
            note = "按本机范围验证；证据、历史失败和边界见验收报告。"
        rows.append({"id": ident, "priority": priority, "scenario": description, "status": status, "evidence": files, "note": note})
    assert len(rows) == 50
    local_pass = all(r['status']=='PASS' for r in rows if r['id'] not in {'47','49'})
    status = 'PASSED_LOCAL_SCOPE_DEFERRED_ITEMS' if local_pass else 'IN_PROGRESS'
    report = {"at": datetime.now(UTC).isoformat(), "status": status, "scenarios": rows}
    (TARGET / "g5-scenario-matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = ["# G5 全项场景证据矩阵", "", f"状态：{status}。PENDING不算通过；47独立Windows、49录像按用户决定待补，未签原计划全项退出。", "", "| ID | 优先级 | 场景 | 状态 | 证据 / 说明 |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        links = "、".join(f"[{p}]({p})" for p in row["evidence"] if (TARGET / p).exists())
        text.append(f"| {row['id']} | {row['priority']} | {row['scenario']} | {row['status']} | {links or row['note']} |")
    (TARGET / "g5-scenario-matrix.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    print(f"All 50 plan rows recorded: {status}; pending/deferred rows are never counted as PASS")


if __name__ == "__main__":
    main()
