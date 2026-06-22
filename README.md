# TMS Bridge

HTTPS shim for the legacy HRL TMS protocol. The HappyRobot workflow only ever
speaks JSON over HTTPS to this service; the AS/400-style TCP framing,
fault tolerance, and `MAX_BUY` redaction all live here.

## Endpoints

| Method | Path                              | TMS command   | Auth   |
|--------|-----------------------------------|---------------|--------|
| GET    | `/livez`                          | —             | none   |
| GET    | `/healthz?msg=<probe>`            | `DEBUG_ECHO`  | Bearer |
| POST   | `/loads/query`                    | `LOAD_QUERY`  | Bearer |
| GET    | `/loads/{load_id}`                | `LOAD_GET`    | Bearer |
| POST   | `/loads/book`                     | `LOAD_BOOK`   | Bearer |
| GET    | `/internal/loads/{load_id}/ceiling` | `LOAD_GET` (returns `MAX_BUY`) | Bearer |

`/loads/query` and `/loads/{id}` strip `MAX_BUY` from every record.
The ceiling is only available via the explicit `/internal/...` route — the
voice agent's prompt never sees it.

## Local dev

```bash
cp .env.example .env       # then fill in BRIDGE_TOKEN
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8080
```

```bash
# Smoke test
curl -H "Authorization: Bearer $BRIDGE_TOKEN" \
     "http://localhost:8080/healthz?msg=hello"
```

## Tests

```bash
pytest -q
```

Parser tests cover every transcript shape from `input/docs/tms/spec-*.md`
including the deliberately-blank `NOTES` semantic and the leading-zero `RATE`
encoding. Add fault-injection integration tests against a stub TMS as Phase A
work continues.

## Container

```bash
docker build -t hrl-tms-bridge .
docker run -p 8080:8080 --env-file .env hrl-tms-bridge
```

## Config (env vars)

| Var                       | Required | Default | Notes |
|---------------------------|----------|---------|-------|
| `TMS_HOST`                | yes      | —       | `tramway.proxy.rlwy.net` |
| `TMS_PORT`                | yes      | —       | `17159` |
| `TMS_TOKEN`               | yes      | —       | Per-org auth token; never reaches the workflow |
| `BRIDGE_TOKEN`            | yes      | —       | Bearer auth on every inbound HTTP call |
| `TMS_READ_TIMEOUT_S`      | no       | `30`    | Per spec — the TMS idle-closes at 30s |
| `TMS_RETRY_ATTEMPTS`      | no       | `3`     | Retries for `timeout` and `partial` faults only |
| `TMS_BACKOFF_INITIAL_MS`  | no       | `250`   | Multiplied by 3× each attempt: 250 → 750 → 2250 |
| `LOG_LEVEL`               | no       | `info`  | `debug` is fine for the POC; never log `TMS_TOKEN` |

## Fault model

Per `input/docs/tms/spec-faults.md`, four observable categories. We surface each:

| Wire condition                            | Detection                       | HTTP                  |
|-------------------------------------------|---------------------------------|-----------------------|
| Timeout (no bytes within idle window)     | `asyncio.TimeoutError` → retry  | `504 tms_fault: timeout` |
| Partial response (no `END`)               | EOF before `END\r\n`            | `502 tms_fault: partial` |
| Malformed (extra delims, oversize fields) | `ParseError` on a record line   | `502 tms_fault: malformed` |
| Delayed termination (server holds open)   | Best-effort `wait_closed` skip  | (response still returned) |

`AUTH_FAILED`, `UNKNOWN_CMD`, `MISSING_FIELD`, `UNKNOWN_LOAD`, `ALREADY_BOOKED`,
`INVALID_RATE`, and `MALFORMED` from the TMS itself are surfaced as
`{ "tms_error": "<CODE>", "message": "..." }` with a code-appropriate HTTP
status. Unknown codes default to `502` and the original code is preserved
verbatim.
