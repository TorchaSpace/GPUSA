# GPUSA server

FastAPI + PostgreSQL (Aurora). The tills never talk to the database; they talk
to this API, and only this API holds database access.

## Settings (environment variables, nothing in the repo)

| Variable | Meaning |
|---|---|
| `GPUSA_DB_HOST` | Aurora cluster endpoint (`database-1.cluster-….rds.amazonaws.com`) |
| `GPUSA_DB_REGION` | e.g. `eu-north-1` (needed for IAM auth) |
| `GPUSA_DB_USER` / `GPUSA_DB_NAME` / `GPUSA_DB_PORT` | default `postgres` / `postgres` / `5432` |
| `GPUSA_DB_AUTH` | `iam` (default: a 15-minute token from AWS, no password exists) or `password` |
| `GPUSA_DB_PASSWORD` | only with `GPUSA_DB_AUTH=password` (local dev) |
| `GPUSA_DB_SSLMODE` | default `require` |
| `DATABASE_URL` | local dev shortcut, overrides all of the above |

On the server the IAM role needs `rds-db:connect` for the cluster; locally, `aws configure`
credentials of an IAM user with the same permission are used.

## Run

    python -m venv .venv-server && . .venv-server/bin/activate
    pip install -r server/requirements.txt
    python -m server.migrate          # applies server/migrations/*.sql once each
    uvicorn server.app:app --port 8000

`GET /health` (no database) and `GET /health/db` (one query; wakes a paused Aurora).

## Tests

    pytest tests/server                                   # no database needed
    GPUSA_TEST_DATABASE_URL=postgresql://postgres@localhost/test pytest tests/server
        # also applies the migrations on a throwaway PostgreSQL (it DROPS schema public)

## Next

Port the business tables from `database/schema.sql` (v10) as migrations `0002+`,
then `/v1` auth (device token) and `POST /v1/sync/push` / `GET /v1/sync/pull`.
