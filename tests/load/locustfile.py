"""Locust load test for the governance proxy hot paths.

Run:
    locust -f tests/load/locustfile.py --host http://localhost:8080 -u 50 -r 10 -t 2m

What we're pressure-testing:
- Create → Approve → Apply end-to-end, because the `apply` path takes locks,
  writes audit rows, and serializes on the idempotency table — this is where
  contention shows up first.
- Read-heavy list endpoints at 5x the write rate to approximate a dashboard user.

We expect p95 < 150ms on reads and < 300ms on writes on a single replica backed
by Postgres. If you see p95 > 1s, look at audit insert latency before anything else.
"""

from __future__ import annotations

import os
import uuid

from locust import HttpUser, between, task
from jose import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "test-secret")


def _token(sub: str = "load-test") -> str:
    return jwt.encode({"sub": sub, "tenant": "default"}, JWT_SECRET, algorithm="HS256")


class GovernanceUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self):
        self.client.headers.update({"Authorization": f"Bearer {_token()}"})

    @task(5)
    def list_audit(self):
        self.client.get("/v1/audit?limit=50", name="GET /v1/audit")

    @task(5)
    def list_change_requests(self):
        self.client.get("/v1/change-requests?limit=50", name="GET /v1/change-requests")

    @task(1)
    def end_to_end_change(self):
        flag = f"load-{uuid.uuid4().hex[:8]}"
        r = self.client.post(
            f"/v1/flags/{flag}/change-request",
            name="POST /v1/flags/{flag}/change-request",
            json={
                "project_id": "default",
                "desired_changes": {"description": "load", "enabled": True, "type": "release"},
                "environment": "default",
            },
        )
        if r.status_code != 201:
            return
        cr_id = r.json()["id"]
        self.client.post(
            f"/v1/change-requests/{cr_id}/approve",
            name="POST /v1/change-requests/{id}/approve",
        )
        self.client.post(
            f"/v1/change-requests/{cr_id}/apply",
            name="POST /v1/change-requests/{id}/apply",
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )

    @task(2)
    def ai_status(self):
        self.client.get("/v1/ai/status/public", name="GET /v1/ai/status/public")
