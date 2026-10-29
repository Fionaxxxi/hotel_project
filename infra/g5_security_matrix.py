"""Probe the current OpenAPI surface with valid-shaped dummy IDs and no credentials / auditor."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from infra.g5_http import Scenario
from infra.manage import ROOT


def value(schema, document):
    if "$ref" in schema:
        return value(document["components"]["schemas"][schema["$ref"].split("/")[-1]], document)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return value(next(x for x in schema["anyOf"] if x.get("type") != "null"), document)
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    kind = schema.get("type")
    if kind == "object":
        return {key: value(schema.get("properties", {}).get(key, {}), document) for key in schema.get("required", [])}
    if kind == "array":
        return [value(schema.get("items", {}), document) for _ in range(schema.get("minItems", 1))]
    if kind == "integer":
        return max(1, schema.get("minimum", 1))
    if kind == "number":
        return max(1, schema.get("minimum", 1))
    if kind == "boolean":
        return False
    if schema.get("format") == "uuid":
        return str(uuid4())
    if schema.get("format") == "date":
        return "2027-01-10"
    if schema.get("format") == "date-time":
        return "2027-01-01T00:00:00Z"
    pattern = schema.get("pattern", "")
    if "64" in pattern:
        return "0" * 64
    return "G5_SECURITY_PROBE" + "x" * max(0, schema.get("minLength", 0) - 17)


def main():
    s = Scenario(days=3)
    document = s.client.get("/openapi.json").json()
    report = {"at": datetime.now(UTC).isoformat(), "status": "IN_PROGRESS", "probes": [], "scope": "All documented core operations, dummy valid-shaped resource IDs; HTTP authorization probes plus existing real-resource cross-tenant/self-approval tests. Dummy resource 404 alone is not proof of a live-resource write guard."}
    try:
        for path, methods in document["paths"].items():
            if path in {"/", "/dev/token", "/health/live", "/health/ready"}:
                continue
            for method, operation in methods.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                endpoint = path
                params = {}
                request_headers = {}
                for param in operation.get("parameters", []):
                    generated = value(param.get("schema", {}), document)
                    if param["name"] == "kind":
                        generated = "snapshot" if path.startswith("/v5/audit/") else "property"
                    if param["in"] == "path":
                        endpoint = endpoint.replace("{" + param["name"] + "}", str(generated))
                    elif param.get("required") and param["in"] == "query":
                        params[param["name"]] = generated
                    elif param.get("required") and param["in"] == "header":
                        request_headers[param["name"]] = str(generated)
                body = None
                content = operation.get("requestBody", {}).get("content", {})
                if "application/json" in content:
                    body = value(content["application/json"]["schema"], document)
                    if isinstance(body, dict):
                        if "action" in body:
                            body["action"] = "pause" if "/policies/" in path or "/subscriptions/" in path else "resume" if "/workflows/" in path or "/accounts/" in path else "retry"
                        if "decision" in body:
                            body["decision"] = "APPROVED"
                        if "channel" in body:
                            body["channel"] = "SIM-A"
                with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=15) as http:
                    unauth = http.request(method, endpoint, headers=request_headers, params=params, json=body)
                probe = {"path": path, "method": method, "unauthenticated_status": unauth.status_code}
                if method != "get":
                    auditor = s.client.request(method, endpoint, headers={**s.headers("auditor"), **request_headers}, params=params, json=body)
                    probe["auditor_status"] = auditor.status_code
                    if auditor.status_code == 422:
                        probe["validation_errors"] = auditor.json().get("detail")
                report["probes"].append(probe)
        report["unauthenticated_all_denied"] = all(x["unauthenticated_status"] == 401 for x in report["probes"])
        report["unresolved"] = [x for x in report["probes"] if x["unauthenticated_status"] != 401 or x.get("auditor_status", 403) not in {403, 404}]
        report["status"] = "PASSED" if not report["unresolved"] else "REVIEW_REQUIRED"
    finally:
        s.close()
        (ROOT / "docs/acceptance/g5-security-matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "probes": len(report["probes"]), "unresolved": len(report["unresolved"])}))


if __name__ == "__main__":
    main()
