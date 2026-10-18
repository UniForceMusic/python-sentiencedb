# sentiencedb — Agent Instructions

A Python database abstraction layer for SQLite, PostgreSQL, MySQL and MariaDB: a port of the PHP package
`sentience/database` (branch `v1`). Version 2.0.0 is a from-scratch rewrite (branch `rewrite/v2`); the
`README.md` still describes 1.x and will be rewritten.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,postgres,mysql]'
```

## Commands

| Command | Purpose |
|---------|---------|
| `.venv/bin/python -m pytest -q` | All tests (integration tests skip when their server is unreachable) |
| `.venv/bin/python -m mypy src/sentiencedb` | Type check (strict) |
| `.venv/bin/python -m ruff check src/sentiencedb tests` | Lint |
| `.venv/bin/python -m ruff format src/sentiencedb tests` | Format (line length 120) |
| `docker compose up -d postgres mysql` | Servers for integration tests (PG `localhost:5432` user `postgres` trust auth; MySQL `localhost:3306` root, empty password) |

All four gates must pass before handing work back.

## Layout (`src/sentiencedb/`)

Mirrors the PHP package; one public class per module, module name = snake_case of the class.

- `database.py` (`Database`, facade) / `database_abc.py` (`DatabaseABC`)
- `driver.py` — `Driver` enum (`MARIADB`, `MYSQL`, `PGSQL`, `SQLITE`) creating adapter, dialect and schema
- `options.py` — `Options` (flat `TypedDict` of connection + dialect options), `DebugCallback`
- `exceptions.py` — `DatabaseError` hierarchy; driver errors are wrapped in `DriverError` subclasses
- `sockets/` — `NetworkSocket(host, port, ...)`, `UnixSocket(path, ...)`
- `adapters/` — `AdapterABC`; `SQLite3Adapter` (stdlib), `PsycopgAdapter` (psycopg 3), `MySQLConnectorAdapter`
- `dialects/` — `DialectABC` + `parse_version`; `SQLDialect` and `MySQLDialect`, `PgSQLDialect`, `SQLiteDialect`
- `schemas/` — `SchemaABC`; introspection per engine, held by `Database` (never part of a dialect)
- `results/` — `ResultABC`, `Result` (in memory), one driver result per adapter
- `queries/` — builders (`SelectQuery`, `InsertQuery`, ...), `QueryFactory`, `query.py` (`Query` base),
  `helpers.py` (`raw`, `identifier`, `alias`, `expression`, `expressionf`, ...), `enums.py`, `type_aliases.py`,
  `objects/` (value objects, `QueryWithParams`), `mixins/` (the PHP traits)

## Conventions

- Python >= 3.11, no runtime dependencies; `from __future__ import annotations` everywhere; relative imports.
- Interfaces are `abc.ABC` subclasses named `...ABC`. Fluent builder methods return `Self`.
- Driver packages are imported lazily inside functions; `import sentiencedb` must work without them.
- Builders hold state only. They hand plain data (`queries/objects`) to the dialect through keyword-only statement
  methods (`dialect.select(table=..., where=..., ...)`); dialects never import builders. A `Query` used as a value
  is a sub-select.
- Dialects emit `?` placeholders; adapters convert them (`QueryWithParams.question_marks_to_percent_s()` for psycopg;
  SQLite and MySQL prepared cursors use `?` natively).
- Qualified identifiers are lists (`["schema", "table"]`); a dotted string is a single identifier. Use `raw(...)` for
  verbatim SQL and `identifier(...)` for an escaped identifier in a value position.
- Server versions are encoded as `major * 10000 + minor * 100 + patch` (`"16.2"` -> `160200`).
- Value objects are frozen dataclasses; use `dataclasses.replace` for modified copies.

## Reference

- PHP source: `/Users/koen/Documents/Github/Sentience-Framework/database` (`src/`, branch `v1`).
- `docker-compose.yml` — MySQL and PostgreSQL services for the integration tests.
