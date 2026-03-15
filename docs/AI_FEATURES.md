# AI Features

Optional AI-powered capabilities when `OPENAI_API_KEY` is set.

| Feature | Endpoint | Description |
|--------|----------|-------------|
| Status | `GET /v1/ai/status` | Whether LLM is configured |
| Summarize change requests | `GET /v1/ai/summarize/change-requests` | Narrative summary of recent change requests |
| Summarize audit | `GET /v1/ai/summarize/audit` | Narrative summary of audit log |
| Risk score | `GET /v1/ai/risk/{id}` | Risk score and optional LLM explanation for a change request |
| NL query | `POST /v1/ai/nl-query` | Parse natural language (e.g. "last 7 days") and return filters + results |
| Suggest flag name | `POST /v1/ai/suggest/flag-name` | Suggest a flag key from a description |
| Suggest strategy | `POST /v1/ai/suggest/strategy` | Suggest rollout strategy for a flag |
| Anomalies | `GET /v1/ai/anomalies` | Statistical anomaly detection on audit volume |
| Insights | `GET /v1/ai/insights` | Aggregated summaries + anomalies |

Without an API key, heuristics and statistical methods are used where possible (e.g. risk score, anomaly detection, slugified flag names).
