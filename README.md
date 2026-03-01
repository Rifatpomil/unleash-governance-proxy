# Unleash Governance Proxy

A **policy and audit layer** for [Unleash](https://www.getunleash.io/) feature flags.

**Author:** Rifat Alam Pomil Enforces authorization (OpenFGA or local policy), immutable audit logging, and a change request workflow.

## Features

- **Change Request Workflow**: Create → Approve → Apply
- **JWT Authentication**: All endpoints require a valid Bearer token
- **Authorization**: OpenFGA or local YAML allowlist (`can_edit_feature`) with hot-reload
- **Audit Logging**: Append-only Postgres table with actor, action, before/after payload
- **Idempotency**: `Idempotency-Key` header for safe retries; expired keys purged hourly
- **Production-ready**: Structured logging (structlog), Prometheus metrics, rate limiting
- **Unleash client**: Retries with exponential backoff for 5xx and network errors

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL
- Unleash instance (optional for testing)

### Run with Docker Compose

```bash
cd unleash-governance-proxy
docker-compose up -d
```

The proxy runs in the foreground on port 8080. Point it to your Unleash instance:

```bash
export UNLEASH_BASE_URL=http://your-unleash:4242
export UNLEASH_API_TOKEN=your-admin-api-token
docker-compose up -d
```

### Run Locally

```bash
# Create virtualenv and install
python -m venv .venv
.venv/bin/activate  # or: .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Set env vars
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/unleash_governance
export JWT_SECRET=your-secret
export UNLEASH_BASE_URL=http://localhost:4242
export UNLEASH_API_TOKEN=your-admin-token

# Run migrations (optional - init_db creates tables)
alembic upgrade head

# Start server
uvicorn app.main:app --reload --port 8080
```

## API Endpoints

### 1. Create Change Request

```bash
curl -X POST http://localhost:8080/v1/flags/my-feature/change-request \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "default",
    "desired_changes": {
      "description": "Enable for beta users",
      "enabled": true,
      "type": "release"
    },
    "environment": "default",
    "strategies": [
      {
        "name": "flexibleRollout",
        "parameters": {"rollout": "50", "stickiness": "default"}
      }
    ]
  }'
```

### 2. Approve Change Request

```bash
curl -X POST http://localhost:8080/v1/change-requests/{id}/approve \
  -H "Authorization: Bearer $JWT"
```

### 3. Apply Change Request

```bash
curl -X POST http://localhost:8080/v1/change-requests/{id}/apply \
  -H "Authorization: Bearer $JWT" \
  -H "Idempotency-Key: unique-key-123"
```

### 4. List Change Requests

```bash
curl "http://localhost:8080/v1/change-requests?status=pending&limit=20" \
  -H "Authorization: Bearer $JWT"
```

### 5. List Audit Logs

```bash
curl "http://localhost:8080/v1/audit?actor=alice&action=change_request_applied&limit=50" \
  -H "Authorization: Bearer $JWT"
```

### 6. Metrics (Prometheus)

```bash
curl http://localhost:8080/metrics
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/unleash_governance` | PostgreSQL connection |
| `JWT_SECRET` | (required) | Secret for JWT verification |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `UNLEASH_BASE_URL` | `http://localhost:4242` | Unleash server URL |
| `UNLEASH_API_TOKEN` | (empty) | Unleash Admin API token |
| `OPENFGA_API_URL` | (none) | OpenFGA URL for authz |
| `OPENFGA_STORE_ID` | (none) | OpenFGA store ID |
| `POLICY_FILE_PATH` | `policies/allowlist.yaml` | Local policy fallback (hot-reload on change) |
| `RATE_LIMIT_PER_MINUTE` | `60` | Max requests/min per IP (0 = high limit) |
| `LOG_FORMAT` | (human) | Set to `json` for structured JSON logs |
| `LOG_LEVEL` | `INFO` | Logging level |

## Local Policy (Fallback)

When OpenFGA is not configured, use `policies/allowlist.yaml`:

```yaml
allow_all: false

allowlist:
  - user: alice
    tenant: acme
    feature: "*"
  - user: admin
    tenant: "*"
    feature: "*"
```

## Tests

```bash
# Default: SQLite (fast, no external deps)
pytest -v

# Or with Postgres
docker-compose up -d postgres
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/unleash_governance pytest -v
```

## Architecture

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────┐
│   Client    │────▶│  Governance Proxy    │────▶│ Unleash │
│  (JWT)     │     │  - Auth (JWT)        │     │  Admin  │
└─────────────┘     │  - Authz (OpenFGA)  │     └─────────┘
                    │  - Audit (Postgres)  │
                    │  - Idempotency      │
                    └──────────────────────┘
                              │
                              ▼
                    ┌─────────────────────────────────┐
                    │  Postgres (change_requests,      │
                    │  audit_logs, idempotency_keys)   │
                    └─────────────────────────────────┘
```

## License

MIT
