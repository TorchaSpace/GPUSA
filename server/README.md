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

## Database schema (Alembic)

The PostgreSQL tables mirror the desktop SQLite schema (v10). Create or update
them with the same environment variables the API uses:

```bash
cd server
set -a; . ./.env; set +a
alembic upgrade head      # creates all tables
alembic current           # shows the applied version (0001)
```

Never run `alembic downgrade` on a database that holds real data: it drops the
tables. New columns/tables go in a new file under `migrations/versions/`.

## Start everything with one double click (Linux server PC)

```bash
bash server/scripts/make_launcher.sh   # once: puts "GPUSA Sunucusu" on the Desktop
```

Double-clicking it opens a terminal that starts Docker if needed, starts the
Postgres container (`gpusa-db`), waits until it answers, reads `.env`, checks
the database connection, runs `alembic upgrade head`, starts the API on port
8000 and then checks `/health` and `/health/db`, printing the address to type
into Admin > Server (test). Ctrl+C stops the API. Run `bash server/scripts/start_server.sh`
directly to see the same thing in an open terminal. Options: `GPUSA_DB_CONTAINER`,
`GPUSA_PORT`, `GPUSA_VENV`, `GPUSA_SKIP_DOCKER=1`.

## Sign-in and first data

1. Put a secret in `.env` (32+ characters, never commit it):
   `GPUSA_JWT_SECRET=` the output of `python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Create the first administrator (the PIN is typed at a hidden prompt):
   `python -m gpusa_server.create_admin B-001 "Full Name"`
3. Sign in, then read products with the token:

```bash
curl -s localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"badge_id":"B-001","pin":"<PIN>","area":"admin","terminal":"test"}'
curl -s localhost:8000/products -H 'Authorization: Bearer <token>'
```

Same rules as the desktop sign-in: 5 wrong PINs lock the account for 5 minutes,
a wrong PIN and an unknown badge look the same, every attempt is logged in
`auth_events`. Tokens last 12 hours. Accounts copied from the desktop database
keep working: the PIN hash format is the same.

## Copying the desktop data (one time)

Copy `shared_backend.db` from the desktop machine to the server (work on the
copy; the importer only reads it), then:

```bash
cd server
set -a; . ./.env; set +a
python -m gpusa_server.import_sqlite ~/shared_backend.db           # dry run, writes nothing
python -m gpusa_server.import_sqlite ~/shared_backend.db --apply   # writes, in one transaction
```

PostgreSQL must be empty (the admin made by `create_admin` counts); add
`--wipe` to empty it first, after which you sign in with the desktop
administrator's PIN. The stock rule (`stock_quantity = SUM(stock_levels) +
units on the truck`) is re-checked and mismatches are listed.

## Configuration (environment variables only)

| Variable | Used for |
|---|---|
| `DATABASE_URL` | Local development / tests only. Never on a server. |
| `GPUSA_DB_HOST` | RDS endpoint (AWS mode) |
| `GPUSA_DB_PORT` | Default `5432` |
| `GPUSA_DB_NAME` | e.g. `gpusa` |
| `GPUSA_DB_SECRET_ARN` | Secrets Manager secret with `{"username","password"}` (RDS-managed format). Read at runtime via the EC2 instance role, cached 5 min. Only used when set. |
| `GPUSA_JWT_SECRET` | Signs login tokens. Unset: `/auth/login` answers 503. |
| `GPUSA_DB_SSLMODE` | Default `require`; later `verify-full` with the RDS CA bundle |

Setting both `DATABASE_URL` and `GPUSA_DB_HOST` is an error on purpose.
Passwords are masked in logs and never returned by the health endpoints.

## Endpoints

- `GET /health`: liveness, never touches the database (use for the load balancer).
- `POST /auth/login`, `GET /auth/me`: sign in with badge + PIN, check the token.
- `GET /products`: product list (needs a token).
- `GET /dealerships`, `/warehouses`, `/stock-levels` (filters: `location_kind`, `location_code`, `product_barcode`): any signed-in account. `GET /employees`: administrators and depot managers only.
- `POST /products`, `PATCH /products/{barcode}`: administrators signed in to `admin` only. Create may carry a starting `stock_quantity` (booked as not-yet-placed stock with a movement row); update changes only the fields sent (`name`, `price`, `critical_stock_level`, `cost_price`, `is_active`), never stock. Both write an `admin_audit` row.
- `POST /stock-movements` (`direction`: `receive` | `dispatch`, `barcode`, `location_kind`, `location_code`, `quantity`, optional `note`, `reference`, `bin_code`): stock entering or leaving the company; the place's level and the product total move together, never below zero, warehouse capacity respected. Administrators (signed in to `admin` or `depot_console`) at any place; depot managers at warehouses only. `GET /stock-movements` (newest first, filter `product_barcode`): administrators and depot managers.

- `POST /stock/transfers` (one product from `source` to one or more `legs`, all or nothing; administrators), `POST /stock/counts` (set a place's level to what was counted; the difference is a `count` movement), `POST /stock/unassigned/place` and `/stock/unassigned/clear`, `PUT /stock/reorder-levels` (administrators).
- Reads for the screens: `GET /products/{barcode}`, `/stock/products-at`, `/stock/reorder-levels`, `/stock/units-by-location`, `/stock/in-transit`, `/stock/movement-totals?since=`, `/stock/last-moved`; `GET /stock-movements` also filters by `location_kind`, `location_code`, `movement_type`, `since`, `until`.

- `POST /sales` (tills: `admin` or `cashier` signed in to `pos`): one sale, keyed by `client_uuid` (sent twice = stored once, answered with `duplicate: true`). A live sale must match today's prices; one made offline (`sold_at` set) keeps the price paid. Both are refused (409) if the shelf does not hold the units. `GET /sales?since=&until=&dealership_code=`.

Every write re-reads the account inside its own transaction, so a token from someone who has since been switched off or demoted is refused (401) even before it expires.
- `GET /health/db`: readiness, runs `SELECT 1`; 503 when unreachable or not configured.
