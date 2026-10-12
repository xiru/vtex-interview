# Catalog consolidation

A marketplace receives product catalogs from many sellers. The same product is usually sold by
several sellers, each describing it slightly differently (`Câmera Canon EOS R6` vs
`Camera Canon EOS R6`, `Apple` vs `apple`). This project consolidates those files into one
catalog **without duplicating products**, while recording **which sellers offer each product**
and how each seller describes it.

It provides:

- a **`catalog` command line tool**: database migrations, imports of seller JSON files, undo of
  imports (and undo of an undo), duplicate detection and merges (and unmerges);
- a **read-only REST API** (FastAPI) to list products, brands, categories, sellers, imports and the
  audit trail, with filters and pagination;
- a **read-only web UI** (Express, server-side rendered) to browse the catalog;
- **PostgreSQL** storage, initialized once from the legacy SQLite catalog (`data/catalog.db`).

How it works — data model, matching rules, undo engine, conflicts — is described in
[DESIGN.md](DESIGN.md). The review history of the plan is in [PLAN.md](PLAN.md).

## Requirements

- Docker with Compose v2 (`docker compose`) and GNU make — enough to build and run everything.
- For local development and running the checks: Python 3.14 and Node.js 24 (if you have any 
  issue trying to configure your local development environment, I would recommend my
  [devenv](https://github.com/xiru/devenv) project).

## Quick start

```bash
make setup         # .venv with hash-verified deps, UI deps, .env from .env.example
make build         # lint + type-check + tests/coverage, then docker images, then a smoke test
make psqlmigrate   # once: load data/catalog.db (legacy SQLite) into PostgreSQL
make up            # PostgreSQL -> migrations -> API -> UI
```

Then open:

- UI: <http://localhost:8080>
- API docs (OpenAPI): <http://localhost:8000/api/v1/docs>

`make bootstrap` runs `build`, `psqlmigrate` and `up` in one go.

**Remember to change the passwords in `.env` before exposing the stack anywhere.**

## Using it

```bash
# import a seller file from any directory (sellers come from each record's SellerName)
make import FILE=/path/to/ProductEntry.json
make import FILE=/path/to/file.json ARGS="--dry-run"

# inspect imports; files are identified by their MD5, never by name
make cli ARGS="import list"
make cli ARGS="import list --seller MegaStore --since 2d --format md5"
make cli ARGS="import show --md5 33c35774dac13280c348cbc2b8365b23"

# undo a whole file (every seller in it), or one seller's part, or a time window
make undo ARGS="--md5 33c35774dac13280c348cbc2b8365b23 --dry-run"
make undo ARGS="--md5 33c35774dac13280c348cbc2b8365b23 --seller TechWorld --yes"
make undo ARGS="--all-files --since 2d --seller MegaStore --yes"

# undo an undo (and a revert can itself be reverted)
make cli ARGS="undo list"
make cli ARGS="undo revert 12 --yes"

# near-duplicates and merges (and unmerges)
make cli ARGS="duplicates list --entity product"
make cli ARGS="merge product --keep 18 --duplicate 19 --dry-run"
make cli ARGS="merge undo 25 --yes"

# sellers
make cli ARGS="seller list"
make cli ARGS="seller rename VTEX 'VTEX Marketplace'"
```

Without Docker (development), the same commands run as `.venv/bin/catalog …` with
`DATABASE_URL` pointing at a PostgreSQL database. `catalog --help` and `catalog <command> --help`
document every option.

API examples (all `GET`, paginated with `limit` (default 100, max 1000) and `offset`):

```bash
curl 'http://localhost:8000/api/v1/products?brand=apple&category=electronics'
curl 'http://localhost:8000/api/v1/products?brand=Black%20%26%20Decker'   # any spelling
curl 'http://localhost:8000/api/v1/sellers/MegaStore/products?limit=10'
curl 'http://localhost:8000/api/v1/categories/15/sellers'
curl 'http://localhost:8000/api/v1/imports?seller=megastore'
```

## Running the checks

| Command | What it does |
|---|---|
| `make check` | lint, type-check, all tests with coverage gates (blocking) + dependency audit (advisory) |
| `make test` | Python and UI tests with coverage; starts and stops a throwaway PostgreSQL (`compose.test.yaml`) |
| `make test-unit` | fast Python unit tests, no database or Docker |
| `make test-api ARGS="-k undo -x"` | Python tests only, with pytest arguments |
| `make test-ui` | UI tests only |
| `make lint` / `make format` | ruff (PEP 8 etc.), eslint, prettier, shellcheck (hadolint if installed) / auto-fix |
| `make typecheck` | mypy |
| `make coverage` | tests, then prints where the HTML reports are |
| `make audit` | pip-audit + npm audit (fails on findings when run alone) |
| `make build` | `check`, then **only if lint/type-check/tests pass**, builds the images and runs `make smoke`; audit findings are shown as a warning and do not block |
| `make smoke` | end-to-end test of the built images in an isolated Compose project |

Coverage gates: Python ≥ 85% (lines + branches), `catalog.validation` ≥ 95%, UI ≥ 85% (lines,
branches, functions, statements). Reports are written to `reports/` (HTML, Cobertura XML, LCOV,
JUnit).

Always build images with `make build`: a bare `docker build` does not run the tests.

## Configuration

Settings come from environment variables; Docker Compose reads them from `.env`
(see [.env.example](.env.example)).

| Variable | Default | Purpose |
|---|---|---|
| `HOUSE_SELLER_NAME` | `VTEX` | name of the house seller (seller id 1, the marketplace's own catalog) |
| `POSTGRES_PASSWORD`, `CATALOG_OWNER_PASSWORD`, `CATALOG_READER_PASSWORD` | — | database credentials (superuser, owner used by migrations/CLI, read-only role used by the API) |
| `API_PORT`, `UI_PORT` | `8000`, `8080` | published ports |
| `IMPORT_DIR` | `./data` | host directory mounted read-only at `/imports` for the CLI |
| `LEGACY_SQLITE` | `./data/catalog.db` | legacy file loaded by `make psqlmigrate` |
| `ID_VALIDATION_MODE` | `lenient` | seller id (UUID) validation: `lenient` or `strict` (see DESIGN.md) |
| `ID_ALLOWED_VERSIONS` | `4,7` | accepted UUID versions; `7` alone + `strict` accepts only real UUIDv7 |
| `ID_V7_MIN_DATE`, `ID_V7_MAX_SKEW` | `2020-01-01`, `5m` | plausibility window for UUIDv7 timestamps (strict mode) |
| `LOG_LEVEL` | `INFO` | logging level |
| `CORS_ORIGINS` | empty | comma separated origins allowed to call the API (GET only) |
| `DATABASE_URL`, `API_DATABASE_URL` | local defaults | connection URLs (set by Compose) |
| `ALLOW_EMPTY_CATALOG` | `0` | allow initializing a database without the legacy load (tests) |

## Repository layout

```
src/catalog/        Python package: CLI, API, services (import, undo, merge), validation, migrations
tests/              pytest: unit/ (no database) and integration/ (PostgreSQL)
ui/                 separate Node.js project: Express + EJS read-only UI, with its own tests
scripts/            the scripts behind the Makefile targets
docker/             PostgreSQL init script (roles)
compose.yaml                   standard runtime (PostgreSQL only)
compose.legacy-migration.yaml  one-time SQLite -> PostgreSQL load (make psqlmigrate)
compose.test.yaml              throwaway PostgreSQL for the tests
data/               legacy SQLite catalog and the sample seller file
```

## Troubleshooting

- **`migrate` exits with "the catalog has not been initialized"**: run `make psqlmigrate` first.
- **`make up` says the images are missing**: run `make build`.
- **Ports already in use**: change `API_PORT` / `UI_PORT` in `.env`.
- **Start over with an empty database**: `make db-reset CONFIRM=yes` (destroys the database
  volume), then `make psqlmigrate`.
- **A command exits with code 3**: another write (import/merge/undo) is running and `--no-wait`
  was used. **Exit code 4**: the undo/merge was refused because of conflicts (details printed).
