# Architecture

This document explains how the governance proxy is structured and, specifically,
what it takes to run it horizontally without losing any of its guarantees.

## Request path

```
Client ──▶ [Load Balancer / TLS] ──▶ N × Governance Proxy replicas ──▶ Postgres
                                              │                    └─▶ Unleash Admin
                                              └─▶ Redis (rate limit + optional cache)
                                              └─▶ OpenAI (async, per-request)
                                              └─▶ OTLP collector (traces)
```

Every replica is **stateless at the process level**. All shared state lives in
Postgres (business + audit + idempotency) or Redis (rate limits).

## Components and their scaling posture

| Component | State | Horizontal-scale notes |
|---|---|---|
| FastAPI workers | None | Scale freely. Use `--workers N` per replica or N replicas. |
| Auth (JWT) | None | HS256 uses shared secret; RS256 uses JWKS with in-proc cache — cache miss triggers a fetch, tolerated across replicas. |
| Authorization | In-proc YAML file-watcher, or OpenFGA RPC | File-watch means each replica reloads independently. For strict consistency prefer OpenFGA. |
| Change-request DB | Postgres | Scales with PG; no in-proc caching. |
| Audit log + hash chain | Postgres | Writes take a short lock pattern (SELECT latest → INSERT) so chain order is serialized per connection. Under very high audit throughput run the write path in a single-writer Postgres primary or batch-write from a queue. |
| Idempotency table | Postgres | Uniqueness on `key`. Concurrent `apply` calls race to insert; losers observe the cached row and return the stored response. Tested by `tests/test_concurrency.py`. |
| Rate limiter | Redis (recommended) or in-proc | With `RATE_LIMIT_STORAGE_URI=redis://...` limits are global. Without it, each replica has its own bucket — fine for dev, wrong for prod. |
| Idempotency cleanup loop | Per-replica, hourly | Safe at-least-once: multiple replicas will delete the same expired rows with no correctness impact. For very large tables, partition by hash and have one replica own each partition. |
| Unleash HTTP client | Per-process singleton | Safe: stateless HTTP client; retries are attempted per call. |
| OpenAI calls | Per-request `httpx.AsyncClient` | Isolation per request; budget counter is per-process (see caveat below). |
| OpenTelemetry | Per-process SDK | Traces exported via OTLP to a shared collector. |

## Known non-stateless items and how to live with them

- **`_spent_usd` budget counter** (in `app/ai/llm.py`) is per-process. Under N
  replicas the effective cap is N × budget. For hard per-tenant budgets move
  the counter to Redis (`INCRBYFLOAT`) keyed by tenant + month.
- **Rate-limit storage default is in-proc.** Set `RATE_LIMIT_STORAGE_URI`.
- **JWKS cache is per-process.** On a key rotation, each replica fetches once.
  Acceptable; aggregate QPS against the IdP is tiny.
- **Idempotency cleanup is best-effort.** Do not rely on it for compliance
  retention — those rules should live in a migration/cron, not a Python task.

## Failure modes

| Failure | Effect | Mitigation |
|---|---|---|
| Postgres primary down | All writes 5xx; reads 5xx if replica is gone | HA Postgres; readiness probe on `/health` |
| Redis down (with URI set) | `slowapi` raises; requests 5xx on limit check | Configure slowapi's `swallow_errors` or fall back to in-proc on init failure |
| OpenAI down / 429 beyond retry | Agent and summarizer return `ok=False` with structured error; heuristic features remain | Heuristic fallbacks are intentional |
| OTLP collector down | BatchSpanProcessor drops on buffer overflow | Alert on dropped spans; traces are not in the critical path |
| Unleash Admin API down | Apply returns 502 with audited failure | Idempotency protects clients from double-apply when service returns |

## Data flow: applying a change request

1. Client POSTs `/v1/change-requests/{id}/apply` with `Idempotency-Key`.
2. Handler checks the idempotency table. Hit → return cached body.
3. Authz check; status must be `approved`.
4. Call Unleash Admin API (httpx + tenacity retries on 5xx/transport).
5. On success: update CR row, write audit row (extends hash chain), store
   idempotency row — all in one transaction, so failure rolls back cleanly.
6. Return response.

Observability: one OTel span for the request, child spans for DB + Unleash + any
LLM calls; Prometheus counters for `change_requests_applied_total`.

## Where the AI layer fits

The AI layer is an auxiliary plane. It never participates in the write path —
risk scoring and the agent are advisory only. Consequences:

- Failure in AI does not affect governance correctness.
- AI outages degrade gracefully (heuristic summaries, heuristic risk score).
- Budget caps and rate limits on AI are process-local; acceptable because the
  blast radius is bounded (one replica, one budget).

If AI ever becomes load-bearing (e.g. "block apply unless risk ≤ medium"), the
budget counter must move to Redis and the call path needs circuit-breaking.

## Deployment shape

- Postgres: HA primary + replica. Alembic runs as a pre-start job.
- Redis: small single-node is fine for rate limits; move to Sentinel/Cluster
  only if the rate limiter becomes critical.
- App: 2+ replicas behind a load balancer; probe `/health`.
- Collector: OTel collector as a sidecar or DaemonSet.
- Unleash: external; the proxy tolerates its unavailability per change request.
