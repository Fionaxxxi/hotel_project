"""Reusable real-HTTP synthetic scenarios. No recorded or fake channel effects."""

import copy
import os
import time
from datetime import date, timedelta
from uuid import uuid4

import httpx

from services.core.config import Settings
from tests.g1_helpers import approve_mappings, ok
from tests.g2_helpers import files


class Scenario:
    def __init__(self, days=30, base_url="http://127.0.0.1:8000"):
        self.settings = Settings.load()
        self.days = days
        self.client = httpx.Client(base_url=base_url, trust_env=False, timeout=20)
        self.tokens = {
            role: ok(
                self.client.post(
                    "/dev/token",
                    json={"subject": "a." + role, "password": os.environ["DEV_LOGIN_PASSWORD"]},
                )
            )["access_token"]
            for role in ["operator", "reviewer", "tenant_admin", "auditor"]
        }
        self.dates = [(date(2027, 1, 10) + timedelta(days=i)).isoformat() for i in range(days)]

    def close(self):
        self.client.close()

    def headers(self, role="operator", key=None):
        return {
            "Authorization": "Bearer " + self.tokens[role],
            **({"Idempotency-Key": key} if key else {}),
        }

    def get(self, path, role="operator"):
        return ok(self.client.get(path, headers=self.headers(role)))

    def post(self, path, body=None, role="operator", key=None):
        return ok(self.client.post(path, headers=self.headers(role, key), json=body or {}))

    def wait(self, path, predicate, seconds=120):
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            value = self.get(path)
            if predicate(value):
                return value
            time.sleep(0.2)
        raise RuntimeError("HTTP_SCENARIO_TIMEOUT:" + path)

    def upload(self, code, sequence, price="100.00"):
        def edit(data, manifest):
            manifest["scope"]["stay_date_to_exclusive"] = (
                date(2027, 1, 10) + timedelta(days=self.days)
            ).isoformat()
            for kind in ["inventory", "rates"]:
                first = data[kind][0]["stay_date"]
                templates = [r for r in data[kind] if r["stay_date"] == first]
                data[kind] = [
                    {**copy.deepcopy(r), "stay_date": day} for r in templates for day in self.dates
                ]
            for row in data["rates"]:
                row["net_amount"] = price

        batch = ok(
            self.client.post(
                "/v2/imports",
                headers=self.headers(),
                files=files(fmt="json", code=code, sequence=sequence, edit=edit),
            )
        )
        return self.post("/v2/imports/" + batch["id"] + "/runs")

    def initialize(self, code=None):
        code = code or "G5-" + uuid4().hex[:8]
        run = self.upload(code, 1)
        ready = self.wait(
            "/v2/processing-runs/" + run["id"], lambda r: r["status"] == "READY_TO_ACTIVATE"
        )
        gen = self.post(
            "/v2/processing-runs/" + run["id"] + "/activate",
            {
                "expected_run_version": ready["version"],
                "expected_versions": ready["expected_versions"],
                "selected_dates": self.dates,
                "partial_confirmed": False,
            },
            key=str(uuid4()),
        )
        scenario = self

        class Reviewer:
            def get(self, path, **kwargs):
                return scenario.client.get(
                    path, headers=scenario.headers("reviewer"), params=kwargs.get("params")
                )

            def post(self, path, **kwargs):
                return scenario.client.post(
                    path, headers=scenario.headers("reviewer"), json=kwargs["json"]
                )

        approve_mappings(Reviewer(), self.settings, gen["activation_id"])
        self.post("/v3/sources/" + gen["activation_id"] + "/adopt")
        alloc = self.post(
            "/v3/allocations",
            {
                "activation_id": gen["activation_id"],
                "expected_version": 0,
                "buffer": 1,
                "amounts": {"SIM-A": 2, "SIM-B": 1},
            },
        )
        snapshots = []
        for channel in ["SIM-A", "SIM-B"]:
            snap = self.post(
                "/v3/snapshots",
                {"allocation_id": alloc["id"], "channel": channel, "plan_codes": ["NONREF"]},
            )
            snap = self.post(
                "/v3/snapshots/" + snap["id"] + "/decisions",
                {
                    "expected_version": snap["version"],
                    "decision": "APPROVED",
                    "reason": "G5 independent human baseline",
                },
                "reviewer",
            )
            snapshots.append(snap)
        policy = self.post(
            "/v4/policies",
            {
                "baseline_ids": [s["id"] for s in snapshots],
                "settings": {"auto_activate": True, "auto_publish": True},
            },
            "tenant_admin",
        )
        for action, role in [
            ("submit", "tenant_admin"),
            ("approve", "reviewer"),
            ("enable", "tenant_admin"),
        ]:
            policy = self.post(
                "/v4/policies/" + policy["id"] + "/commands",
                {
                    "action": action,
                    "expected_version": policy["version"],
                    "reason": "G5 explicit bounded synthetic source",
                },
                role,
            )
        sub = self.post("/v4/subscriptions", {"policy_id": policy["id"]}, "tenant_admin")
        return {
            "code": code,
            "policy": policy,
            "subscription": sub,
            "baselines": snapshots,
            "initial_run": run,
        }

    def update(self, bundle, sequence, price="105.00"):
        started = time.monotonic()
        run = self.upload(bundle["code"], sequence, price)
        overview = self.wait(
            "/v4/overview",
            lambda d: any(
                r["processing_id"] == run["id"] and r["state"] == "COMPLETED" for r in d["runs"]
            ),
        )
        done = next(r for r in overview["runs"] if r["processing_id"] == run["id"])
        plan = self.get("/v3/plans/" + done["plan_id"])
        assert plan["state"] == "VERIFIED"
        return {
            "code": bundle["code"],
            "sequence": sequence,
            "received_run": run["id"],
            "workflow": done,
            "plan": plan,
            "seconds": time.monotonic() - started,
            "status": "VERIFIED",
        }

    def stop(self, plan):
        started = time.monotonic()
        results = []
        retries = 0
        for branch in plan["branches"]:
            retries += self.request_stop(branch["listing_id"], self.dates)
        for branch in plan["branches"]:
            li = self.wait(
                "/v3/listings/" + branch["listing_id"], lambda r: r["state"] == "STOPPED"
            )
            assert li["desired_revision"] == li["observed_revision"]
            results.append(
                {
                    "id": li["id"],
                    "state": li["state"],
                    "desired": li["desired_revision"],
                    "observed": li["observed_revision"],
                }
            )
        return {"seconds": time.monotonic() - started, "results": results, "cas_retries": retries}

    def request_stop(self, listing_id, dates=None):
        for attempt in range(8):
            li = self.get("/v3/listings/" + listing_id)
            if li["state"] == "STOPPED":
                return attempt
            selected = dates or sorted({x["date"] for x in li["intent"]["payload"]["cells"]})
            response = self.client.post("/v3/listings/" + listing_id + "/stop", headers=self.headers("tenant_admin", str(uuid4())), json={"expected_version": li["version"], "dates": selected, "reason": "G5 controlled acceptance stop; exact current version"})
            if response.status_code == 409 and response.json().get("code") == "VERSION_CONFLICT":
                time.sleep(0.1)
                continue
            ok(response)
            return attempt
        raise RuntimeError("STOP_CAS_RETRY_BUDGET_EXHAUSTED")
