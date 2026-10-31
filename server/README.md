# GPUSA server (cloud API)

FastAPI + PostgreSQL (psycopg 3). First step of the AWS plan: skeleton with
health checks and safe configuration. Desktop apps don't use it yet; without
`GPUSA_API_URL` they keep working on local SQLite exactly as before.

## Run locally

```bash
cd server
python -m pip install -r requirements.txt
cp .env.example .env            # edit DATABASE_URL; .env is git-ignored
set -a; . ./.env; set +a
uvicorn gpusa_server.main:app --reload
curl localhost:8000/health      # {"status":"ok",...}
curl localhost:8000/health/db   # 200 if PostgreSQL answers, 503 otherwise
```

Tests: `python -m pytest -q server` (from the repo root). Set
`GPUSA_TEST_DATABASE_URL` to also test against a real PostgreSQL; CI does.

## Configuration (environment variables only)

| Variable | Used for |
|---|---|
| `DATABASE_URL` | Local development / tests only. Never on a server. |
| `GPUSA_DB_HOST` | RDS endpoint (AWS mode) |
| `GPUSA_DB_PORT` | Default `5432` |
| `GPUSA_DB_NAME` | e.g. `gpusa` |
| `GPUSA_DB_SECRET_ARN` | Secrets Manager secret with `{"username","password"}` (RDS-managed format). Read at runtime via the EC2 instance role, cached 5 min. Only used when set. |
| `GPUSA_DB_SSLMODE` | Default `require`; later `verify-full` with the RDS CA bundle |

Setting both `DATABASE_URL` and `GPUSA_DB_HOST` is an error on purpose.
Passwords are masked in logs and never returned by the health endpoints.

## Endpoints

- `GET /health`: liveness, never touches the database (use for the load balancer).
- `GET /health/db`: readiness, runs `SELECT 1`; 503 when unreachable or not configured.
