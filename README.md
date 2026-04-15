# Unleash Governance Proxy

A policy, audit, and AI-assisted governance layer for [Unleash](https://www.getunleash.io/) feature flags.

**Author:** Rifat Alam Pomil

Sits in front of the Unleash Admin API and enforces **authorization**, **immutable audit logging**, an **approval workflow**, and — when an `OPENAI_API_KEY` is set — **AI-assisted investigation** of governance data via a bounded tool-use agent.

## What it actually does

### Core governance
- **Change-request workflow**: create → approve → apply, with each transition audited.
- **JWT auth** on every endpoint except `/health`, `/metrics`, `/`, and the public AI status. Supports HS256 (shared secret) or RS256/ES256 via JWKS (`JWT_JWKS_URL`) with cached key rotation.
- **Authorization**: OpenFGA (preferred) or a local YAML allowlist with file-watch hot-reload.
- **Tamper-evident audit log**: append-only Postgres rows + **SHA-256 hash chain** (`prev_hash`, `row_hash`). `GET /v1/audit/verify` walks the chain and pinpoints the first tampered row. Pair with DB-side `REVOKE UPDATE,DELETE` for defense in depth.
- **Idempotency** via the `Idempotency-Key` header, Postgres-backed, with a test that fires 10 concurrent `apply` calls at the same key and asserts single-shot behavior.
- **Resilient Unleash client**: exponential backoff + tenacity retries for 5xx/transport errors.
- **Distributed rate limiter**: slowapi with optional Redis/Memcached backend (`RATE_LIMIT_STORAGE_URI=redis://...`), so limits hold across replicas.
- **Observability**: structlog JSON logs, Prometheus metrics with **bounded label cardinality** (route template), and **OpenTelemetry traces** (FastAPI + httpx + SQLAlchemy) when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

### AI layer (optional, gated on `OPENAI_API_KEY`)
- **Non-blocking**: all LLM calls use `httpx.AsyncClient` — no sync OpenAI SDK inside async handlers.
- **Bounded**: per-request timeout, capped retry with exponential backoff on 429/5xx only, and a **soft per-process USD budget** that trips calls open when exceeded.
- **Observable**: `governance_llm_calls_total`, `_latency_seconds`, `_tokens_total`, `_cost_usd_total` Prometheus series, labeled by `feature` and `model`. Every call is logged with tokens, cost, latency, prompt version.
- **Structured outputs**: risk, flag-name suggestion, and NL-query use JSON mode with strict parsing + fallback.
- **Versioned prompts** (`app/ai/prompts.py::VERSION`) so evals can pin to a known prompt set.
- **Audit investigator agent** (`POST /v1/ai/agent/investigate`) — OpenAI tool-use over a tiny, typed, **read-only** toolbox (`count_audit_events`, `list_audit_events`, `count_change_requests_by_status`). The agent cannot issue arbitrary SQL; the loop is bounded to 4 turns; every tool call is logged.
- **Streaming agent** (`POST /v1/ai/agent/investigate/stream`) — Server-Sent Events stream of `start` / `tool_call` / `answer` / `end` events so dashboards render progress live.
- **Live eval harness** (`tests/evals/test_agent_live_eval.py`, gated by `RUN_LIVE_EVALS=1`) runs the real model against a golden Q&A YAML and scores (a) tool-call correctness, (b) answer substrings, and (c) an **LLM-as-judge** verdict against a per-case rubric. Pins on `prompt_version` so drift is detectable; judge scores are cached to disk.
- **Heuristic features** (always on): risk score, anomaly z-score on audit volume, flag-name slugifier. These run without an API key.

## Quick start

### Prerequisites
- Python 3.11+
- PostgreSQL
- (Optional) Unleash instance, OpenAI API key, OpenFGA

### Docker Compose

```bash
docker-compose up -d
```

### Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/unleash_governance
export JWT_SECRET=$(openssl rand -hex 32)
export UNLEASH_BASE_URL=http://localhost:4242
export UNLEASH_API_TOKEN=your-admin-token
# Optional AI
export OPENAI_API_KEY=sk-...

alembic upgrade head
uvicorn app.main:app --port 8080
```

> Tables are **not** auto-created at startup anymore. Run `alembic upgrade head` in production. For local dev / tests you can opt in with `AUTO_CREATE_TABLES=1`.

## Key endpoints

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/flags/{key}/change-request` | Create CR |
| POST | `/v1/change-requests/{id}/approve` | Approve CR |
| POST | `/v1/change-requests/{id}/apply` | Apply CR (requires `Idempotency-Key`) |
| GET | `/v1/change-requests` | Paginated list |
| GET | `/v1/audit` | Paginated audit list |
| POST | `/v1/ai/agent/investigate` | Tool-use agent over audit data |
| POST | `/v1/ai/nl-query` | Regex + LLM structured filter extraction |
| GET | `/v1/ai/risk/{id}` | Heuristic score + optional LLM explanation |
| GET | `/v1/ai/insights` | Aggregated summaries + anomalies |
| GET | `/metrics` | Prometheus |
| GET | `/` | Dashboard |

Example — agent:

```bash
curl -X POST http://localhost:8080/v1/ai/agent/investigate \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"question": "Who applied the most change requests in the last 24 hours?"}'
```

Response includes the full tool-call transcript so you can audit the agent.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local PG | Postgres connection |
| `JWT_SECRET` | (required) | JWT HMAC secret |
| `UNLEASH_BASE_URL` / `UNLEASH_API_TOKEN` | — | Unleash Admin API |
| `OPENFGA_API_URL` / `OPENFGA_STORE_ID` | — | Authz backend |
| `POLICY_FILE_PATH` | `policies/allowlist.yaml` | Fallback authz |
| `RATE_LIMIT_PER_MINUTE` | 60 | Per-IP |
| `TRUSTED_PROXY_HOPS` | 0 | # of proxy hops to trust in `X-Forwarded-For`; 0 ignores XFF entirely |
| `OPENAI_API_KEY` | — | Enables LLM features |
| `AI_FEATURES_ENABLED` | true | Kill-switch |
| `LLM_MODEL` | `gpt-4o-mini` | Chat model |
| `LLM_TIMEOUT_SECONDS` | 15 | Per-call timeout |
| `LLM_MAX_RETRIES` | 2 | Retry budget for 429/5xx/transport only |
| `LLM_MAX_OUTPUT_TOKENS` | 400 | Cost guard |
| `LLM_MONTHLY_BUDGET_USD` | 0 (off) | Soft per-process USD cap |
| `AUTO_CREATE_TABLES` | off | Opt-in for dev; use Alembic in prod |
| `RATE_LIMIT_STORAGE_URI` | — | e.g. `redis://redis:6379/0` for cross-replica limits |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | e.g. `http://otel-collector:4318` |
| `OTEL_TRACES_SAMPLER_RATIO` | 1.0 | Parent-based ratio sampler |
| `AUDIT_HASH_CHAIN_ENABLED` | true | SHA-256 chain on audit inserts |
| `JWT_JWKS_URL` | — | Enables RS256/ES256 verification via JWKS |
| `JWT_JWKS_CACHE_SECONDS` | 300 | TTL for JWKS key cache |

## Tests

```bash
# Core + AI unit tests (mocked LLM, no network)
pytest -v

# Golden evals (deterministic regex/agent contract tests)
pytest tests/evals -v -k "not live"

# Live evals (real LLM, gated)
RUN_LIVE_EVALS=1 OPENAI_API_KEY=sk-... pytest tests/evals/test_agent_live_eval.py -v

# Concurrency regression
pytest tests/test_concurrency.py -v

# Load test
locust -f tests/load/locustfile.py --host http://localhost:8080 -u 50 -r 10 -t 2m
```

`tests/evals/test_agent_eval.py` mocks the OpenAI tool-use loop to assert: tools dispatch correctly, unknown tool names don't crash, and the turn limit terminates runaway loops.

## Architecture

```
Client (JWT) ─▶ Governance Proxy ─▶ Unleash Admin
                 │  auth (JWT)
                 │  authz (OpenFGA | YAML)
                 │  audit + idempotency (Postgres, same tx)
                 │  rate-limit (in-proc, per-IP)
                 │  metrics (Prometheus, bounded cardinality)
                 │
                 └─▶ AI layer (optional)
                     ├─ async OpenAI REST (httpx)
                     ├─ JSON-mode structured outputs
                     ├─ tool-use agent (read-only DB tools)
                     └─ token/cost/latency metrics + USD budget
```

## Horizontal scaling

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for what's stateless, what's
per-process, and what to change before going multi-replica (short version: set
`RATE_LIMIT_STORAGE_URI`, use OpenFGA over the YAML allowlist, and move the AI
budget counter to Redis if you need hard per-tenant caps).

## CI

`.github/workflows/ci.yml` runs: unit tests (SQLite + Postgres), golden evals
(no LLM), concurrency + audit-chain tests. A separate `live-eval` job runs the
real-LLM harness when the commit message contains `[live-eval]` or on a cron.

## Known limitations

Written down because the interviewer will find them anyway:

- **Tenancy is shallow.** `tenant` is a string column; cross-tenant isolation is at the application layer only.
- **No streaming / no token-by-token UI** on the agent or summaries. Fine for the current use cases, not for long explanations.
- **Cost estimates are hardcoded.** Pricing drifts; `LLM_COST_USD_TOTAL` is a budget guardrail, not a billing source of truth.
- **The agent's toolbox is deliberately small.** It can count and list audit events, nothing more — by design, but if you want it to compare flags across projects you'll need more tools.
- **Anomaly detection is a z-score**, not an ML model. Intentional: the signal is weak and the cost/complexity of a real model isn't worth it here.

## License

MIT
