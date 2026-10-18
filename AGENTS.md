# sentiencedb — agent instructions

Python database abstraction layer (SQLite, PostgreSQL, MySQL, MariaDB), a port of the PHP package
`sentience/database` v1. Version 2.0.0 is a rewrite; `README.md` is the user guide, `CHANGELOG.md` lists the changes
since 1.x and the PHP bugs that were deliberately not ported.

## Setup and commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # = pip install -e ".[postgres,mysql,dev]"
docker compose up -d   # optional: PostgreSQL :5432 (postgres, trust), MySQL :3306, MariaDB :3307 (root, no password)
```

| Command | Purpose |
|---|---|
| `.venv/bin/python -m pytest -q` | All tests. Server tests skip when their server is unreachable. |
| `.venv/bin/python -m pytest -q -m integration` | Only the tests that need a live server. |
| `.venv/bin/python -m pytest tests/test_select_query.py -k distinct` | One file / pattern. |
| `.venv/bin/python -m mypy src/sentiencedb` | Type check (strict, configured in `pyproject.toml`). |
| `.venv/bin/python -m ruff check src/sentiencedb tests` | Lint. |
| `.venv/bin/python -m ruff format --check src/sentiencedb tests` | Format check (line length 120). |
| `.venv/bin/python main.py [--postgresql] [--mysql] [--mariadb] [--debug]` | Example script; SQLite only by default. |

All of these must pass before handing work back. No Makefile, CI or pre-commit hooks exist.

## Architecture (`src/sentiencedb/`)

```
Database.connect(driver, name, socket, queries, options, debug)
  adapter = driver.adapter(...)                      # connects eagerly in the calling thread
  dialect = driver.dialect(adapter.version(), options)
  schema  = driver.schema()
db.select("t").where_equals("id", 1).execute()
  builder state -> dialect.select(**state) -> QueryWithParams("... ?", [1]) -> db.query_with_params()
  -> adapter (no params: plain SQL; params: dialect.cast_to_driver + placeholder conversion) -> ResultABC
```

- `database.py` / `database_abc.py`: the `Database` facade (raw SQL, transactions with a per-thread savepoint stack,
  builder factories, `information_schema_*`).
- `driver.py`: `Driver` StrEnum (`MARIADB`, `MYSQL`, `PGSQL`, `SQLITE`) with lazy `adapter()` / `dialect()` /
  `schema()` factories. `options.py`: flat `Options` TypedDict and `DebugCallback`. `sockets/`: `NetworkSocket`,
  `UnixSocket`.
- `queries/`: builders (`SelectQuery`, `InsertQuery`, `UpdateQuery`, `DeleteQuery`, `CreateTableQuery`,
  `AlterTableQuery` (not a `Query`: returns lists), `DropTableQuery`, `CreateIndexQuery`, `DropIndexQuery`,
  `QueryFactory`), `mixins/` (where, having, joins, returning, if-[not]-exists), `objects/` (frozen dataclass value
  objects: `Sql` + `Raw`/`Identifier`/`Expression`/`ExpressionF`/`CurrentTimestamp`, `Alias`, `SubQuery`, clauses,
  DDL objects, `QueryWithParams` and its per-engine placeholder tokenizer), `enums.py`, `helpers.py`,
  `type_aliases.py`. `_introspection.py` splits a builder's table into `(schema, name)` for `QueryFactory` and for
  the IF [NOT] EXISTS emulation, which calls `db.information_schema_table_exists()` / `_index_exists()`.
- `dialects/`: `DialectABC` + `parse_version`; `SQLDialect` (all statement SQL, through `_build_*` hooks) with
  `MySQLDialect` (MySQL and MariaDB, told apart by `driver`), `PgSQLDialect`, `SQLiteDialect`. Feature flags are
  read-only properties (`boolean`, `distinct_on`, `generated_by_default_as_identity`, `index_exists`, `lateral`,
  `on_conflict`, `returning`, `update_returning`, `savepoints`, `table_exists`), mostly version gated.
- `adapters/`: `AdapterABC`; `_dbapi_adapter.py` (shared PEP 249 logic: debug callback, error mapping, placeholder
  validation, transactions); `SQLite3Adapter`, `PsycopgAdapter`, `MySQLConnectorAdapter`; `_thread_local_store.py`
  (one connection per thread, `max_connections` semaphore); `_errors.py` (driver exception -> `DriverError` subclass).
- `results/`: `ResultABC`, in-memory `Result`, `SQLite3Result`, `PsycopgResult`, `MySQLConnectorResult`.
- `schemas/`: `SchemaABC`, `SQLSchema` (`information_schema`), `MySQLSchema`, `PgSQLSchema`, `SQLiteSchema` (PRAGMAs).
  Besides the `information_schema_*` listings they answer `table_exists` / `index_exists` (one catalog query each;
  index names per table on MySQL/MariaDB, per schema on PostgreSQL/SQLite); `tables` and both checks take an
  optional `schema`. Not to be confused with the dialects' `table_exists` / `index_exists` feature flags, which say
  whether `IF [NOT] EXISTS` is native (PHP names both alike).
- `exceptions.py`: `DatabaseError` > `AdapterError`, `QueryError`, `DriverError` (see README for the full tree).

## Conventions

- Python >= 3.11, `from __future__ import annotations` in every file, relative imports inside the package.
- `abc.ABC` interfaces named `*ABC`; one public class per module, module name = snake_case class name; private
  helpers in `_*.py` modules or `_` functions.
- Fluent builder methods return `Self`. Builders hold state only; **all SQL text is produced by dialects**, and
  dialects consume plain value objects only (never builders or mixins). A `Query` used as a value is a sub-select.
- Builders pass every keyword to the dialect statement method (tests record the exact call).
- `psycopg` and `mysql.connector` are imported lazily inside functions (only `TYPE_CHECKING` imports at module level);
  `tests/test_package.py` enforces it. No runtime dependencies.
- Docstrings on public classes and methods, no comments that restate code, no `print` in the package, no `Any` in
  public signatures except row values and `**kwargs`.

## Gotchas

- Dialects always emit `?` placeholders. `PsycopgAdapter` converts them to `%s` (`question_marks_to_percent_s`,
  which also doubles every other `%`); SQLite and MySQL (server-side prepared cursor) keep `?`. Statements without
  params are sent verbatim, so never route parameterless SQL through placeholder conversion.
- `QueryWithParams` finds placeholders with a tokenizer keyed by `dialect.driver` (`_TOKENIZERS`): it skips each
  engine's strings, quoted identifiers and comments (PostgreSQL `E''`, `$tag$`, nested `/* */`; MySQL backslash
  escapes and `#`; SQLite `[..]`). Its conversion methods therefore take the dialect. `:name` params of `where()` /
  `expression()` stay in the `Expression` and are made positional when the dialect renders it.
- `Sql.to_query_with_params(dialect)` is what dialects call to inline a fragment (sql and params in one render).
- Identifiers: a dotted string is one identifier (`"users.id"` -> `"users.id"`); use a list (`["users", "id"]`) for
  a qualified name, and `identifier([...])` to use a column as a value.
- `Sql` objects in identifier positions (columns, defaults, GROUP BY) render via `raw_sql()`, interpolating params.
- DDL has no bound parameters: partial index predicates are inlined with `to_sql()`.
- `CreateTableQuery.float()` / `.datetime()` shadow builtins inside those classes; annotate with `builtins.float` and
  `dt.datetime`.
- Version encoding: `major * 10000 + minor * 100 + patch` (`"16.2"` -> 160200, `"10.11.6-MariaDB"` -> 101106).
- SQLite: no `DEFAULT` in multi-row `VALUES` (rows must share columns); identity columns become `INTEGER PRIMARY KEY
  AUTOINCREMENT`; constraint names are dropped; most `ALTER TABLE` operations raise `QueryError`; in-memory databases
  use one shared connection, which a transaction holds for its thread until it ends. `regexp_like` / `REGEXP` are
  Python functions registered by the adapter.
- PostgreSQL: `use_serials` defaults to `True` (identity columns become `SERIAL`/`BIGSERIAL`).
- Datetimes: `SQLDialect.cast_datetime` converts aware datetimes to UTC (MySQL, MariaDB, SQLite);
  `PgSQLDialect` keeps the offset. Adapters only replace `Enum` members by their value before casting.
- MySQL/MariaDB: `RETURNING` on `UPDATE` is never supported (`update_returning`; `QueryError`, as for any unsupported
  UPDATE/DELETE `RETURNING`); INSERT `RETURNING` is emulated via `last_insert_id`, one row per statement. The
  `regexp_like()` match type is inlined (MySQL rejects a bound one). mysql-connector's C extension reports every
  server-side prepare error as 2006 "server has gone away"; the adapter re-prepares in SQL to raise the real error.
  It also binds floats as single precision, so `MySQLConnectorAdapter._cast` binds finite floats as `Decimal`.
  `on_conflict_do_nothing` is `ON DUPLICATE KEY UPDATE <col> = <col>`, never `INSERT IGNORE`.
- Error classes follow the driver's PEP 249 class: a SQLite syntax error is `OperationalError`, not
  `ProgrammingError`.

## Testing layout (`tests/`)

- `conftest.py`: dialect fixtures per engine, a `recording_dialect` (records the keyword arguments builders pass) and
  a `mock_database` (`create_autospec(DatabaseABC)`), plus `static_query` for sub-selects.
- Dialects: `test_dialect_values.py` (escaping, casting, types, flags), `test_dialect_{sql,mysql,pgsql,sqlite}.py`.
- Builders: `test_select_query.py`, `test_insert_query.py`, `test_update_delete_query.py`, `test_ddl_queries.py`,
  `test_conditions.py`, `test_joins.py`, `test_query_factory.py` (builder -> dialect handoff, emulation paths).
- Objects and results: `test_sql_objects.py`, `test_query_with_params.py`, `test_results.py`,
  `test_driver_results.py`.
- Adapters and facade: `test_sqlite3_adapter.py`, `test_psycopg_adapter.py`, `test_mysql_connector_adapter.py` (mocked
  connection kwargs plus `integration` tests against a live server), `test_database.py`, `test_threading.py`,
  `test_error_mapping.py`.
- Introspection: `test_schema_{sqlite,pgsql,mysql}.py` (PostgreSQL / MySQL live, each in a scratch schema / database).
- Regressions of the review findings, unit and live: `test_regressions_{queries,dialects,adapters}.py`.
- End to end: `test_end_to_end_sqlite.py` (always runs) and
  `test_end_to_end_servers.py`, one suite parametrised over PostgreSQL, MySQL and MariaDB (marker `integration`; each
  backend is skipped when its port is unreachable; it creates and drops a `sentiencedb_e2e` database and resets it
  before every test).
- `test_package.py`: public exports and lazy driver imports.
