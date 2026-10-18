# Changelog

All notable changes to this project are documented here. The project follows [Semantic Versioning](https://semver.org/).

## 2.0.0

A rewrite from scratch, ported again from the PHP package
[sentience/database](https://github.com/Sentience-Framework/database) (branch `v1`). The public API is new; code
written for 1.x needs to be migrated (see "Breaking changes" below).

### Added

- **MariaDB** as a separate driver (`Driver.MARIADB`, `Database.connect_mariadb`), sharing the MySQL adapter and
  dialect but with its own feature gates (`RETURNING` on 10.5+, no `LATERAL`, `REGEXP`).
- `Database.connect(driver, name, socket, queries, options, debug)` with the `Driver` enum and the `NetworkSocket` /
  `UnixSocket` connection targets; unix sockets for PostgreSQL, MySQL and MariaDB.
- A typed, flat `Options` dict covering the PHP options (SSL, `search_path`, `charset`, SQLite PRAGMAs, `use_regexp`,
  `use_tilde_regex`, `use_serials`, `strict`, ...).
- **Thread safety**: one connection per thread, created lazily and closed when the thread exits; `max_connections`
  and `acquire_timeout` cap them. SQLite in-memory databases share one serialised connection. Savepoint stacks are
  per thread.
- **Error mapping**: driver exceptions become `DriverError` subclasses (`OperationalError`, `ProgrammingError`,
  `IntegrityError` and its `UniqueViolationError`, `ForeignKeyViolationError`, `NotNullViolationError`,
  `CheckViolationError`, ...), with `query`, `sqlstate`, `code` and the original exception as `__cause__`.
  `ConnectionFailedError` for connect and startup failures, `ConnectionLimitError` for the connection cap.
- **Schema introspection**: `information_schema_schemas/tables/columns/primary_keys/unique_constraints/
  foreign_key_constraints/indexes` on `Database`, and the per-table equivalents on `db.table(name)`. They return
  the same `Column`, `UniqueConstraint`, `ForeignKeyConstraint` and `Index` objects the DDL builders use.
- `CREATE INDEX` / `DROP INDEX` builders (`db.create_index`, `db.drop_index`), including unique and partial indexes.
- Emulation of upserts (`emulate_on_conflict`), `INSERT ... RETURNING` (`emulate_returning`) and
  `IF [NOT] EXISTS` (`emulate_if_not_exists`, `emulate_if_exists`) for engines or versions without native support.
- `SelectQuery.count()`, `select_table()`, `select_sub_query()`, lateral joins (`*_lateral*`, `outer_apply*`,
  `cross_apply*`), `joinf()`, `wheref()` / `havingf()`, `where_operator()`, `having_*` mirrors of every condition,
  `or_where_glob` / `or_where_not_glob`.
- `expressionf()` printf style SQL, `current_timestamp(precision)`, `escape_ansi()`, `escape_backslash()`.
- `Result.from_result()` snapshots, `len(Result)`, iteration over any result, `fetch_object(cls, *args, **kwargs)`.
- `Query.to_sql()` for logging, `QueryWithParams` with named (`:name`) placeholder support.
- The package is fully typed (`py.typed`, `mypy --strict`) and has no runtime dependencies.

### Breaking changes

- **Imports.** Everything public is exported from `sentiencedb`. Removed modules and names:
  - `sentiencedb.database.DB`, `Database`, `Table`: use `sentiencedb.Database` and `sentiencedb.QueryFactory`.
  - `sentiencedb.query` became `sentiencedb.queries`, `sentiencedb.query.expressions` became
    `sentiencedb.queries.objects`, `sentiencedb.result` became `sentiencedb.results`.
  - `sentiencedb._helpers` (`raw`, `identifier`, `alias`, ...): import from `sentiencedb`.
  - `snapshot_result(result)`: use `Result.from_result(result)`.
  - Adapters, dialects and results were renamed after their driver: `SQLiteAdapter` is `SQLite3Adapter`,
    `MySQLAdapter` is `MySQLConnectorAdapter`, `PostgresqlDialect` is `PgSQLDialect`, `MySQLResult` is
    `MySQLConnectorResult`.
- **Connecting.** `connect_*` keyword arguments are keyword-only and renamed: `user` is `username` (no default
  `"postgres"`), `startup_queries` is `queries`, `debug_callback` is `debug`. `connect_sqlite` defaults to
  `":memory:"`. `Database.drivers()` and the `adapter` property were removed.
- **Debug callback.** Its second argument is the `time.time()` at which the statement started.
- **Raw SQL.** `prepared(..., emulate=...)` and `query_with_params(..., emulate=...)` take `emulate_prepare`.
  `prepared()` accepts a mapping with `:name` placeholders.
- **Conditions.** `where_raw(sql, values)` is `where(sql, values)` (and `having_raw` is `having`). Regex `flags` is a
  string (`""` by default). `where_starts_with`, `where_ends_with`, `where_contains` and `where_not_contains` take
  `case_insensitive` and always match the value literally (no `escape_backslash` flag).
- **Joins.** Join methods no longer return a `Join` to call `.on()` on; they return the query and take an `on`
  callback: `left_join("posts", lambda join: join.on(["posts", "user_id"], ["users", "id"]))`. The `*_table`
  variants take the alias, the `*_sub_query` variants a select and an alias.
- **Upserts.** `on_conflict_do_update(conflict, updates=None)`: without updates every inserted column is overwritten.
  The `Values` / `Excluded` expressions were removed. `InsertQuery.execute()` always returns one result, also for
  emulated multi-row upserts.
- **Updates.** `UpdateQuery.updates(...)` is `set(...)`.
- **DDL.** Column helpers use ANSI names: `string()` is `varchar()` (with `length`), `date_time()` is `datetime()`,
  and `text()` is a separate type with an optional `length`. On `AlterTableQuery`: `add_bool` is `add_boolean`,
  `add_int` is `add_integer`, `add_string` is `add_varchar`, `add_date_time` is `add_datetime`. `identity(...,
  add_primary_key=)` is `identity(..., primary_key=)`. `TypeEnum` members are `BOOLEAN`, `INTEGER`, `FLOAT`,
  `VARCHAR`, `TEXT`, `DATETIME`.
- **Foreign keys.** `foreign_key_constraint(columns, reference_table, reference_columns, name=None,
  on_update=None, on_delete=None)` takes lists of columns and separate actions; `ReferentialActionEnum` members are
  `NO_ACTION`, `RESTRICT`, `CASCADE`, `SET_NULL`, `SET_DEFAULT` (instead of `ON_DELETE_CASCADE` and so on).
- **PostgreSQL identity columns** are `SERIAL` / `BIGSERIAL` by default; set the `use_serials` option to `False`
  for `GENERATED BY DEFAULT AS IDENTITY`.
- **Results.** `fetch_object()` / `fetch_objects()` return `types.SimpleNamespace` objects by default (was `dict`)
  and pass `*args, **kwargs` to the class instead of a `constructor_args` list. `columns()` values may be `None`.
- **Fluent table reassignment** (`SelectQuery.table(...)`) was removed; create a new query instead.
- **Exceptions.** `QueryWithParamsError` is a subclass of `QueryError`. Driver exceptions no longer leak; catch
  `DriverError` or one of its subclasses.
- **Version encoding** is `major * 10000 + minor * 100 + patch` for every version string (1.x scaled two-part
  versions such as PostgreSQL's `"15.2"` differently from three-part ones).

### Fixed (compared with the PHP package)

- `wheref()` / `havingf()` chained with `OR`; they chain with `AND`.
- `SelectQuery.count()` used an un-aliased sub-select, which MySQL and PostgreSQL before 16 reject.
- Foreign key `REFERENCES` tables were not escaped.
- Cross joins without conditions rendered `ON TRUE`.
- `expressionf()` did not unescape `%%`.
- MySQL string literals used double quotes (broken under `ANSI_QUOTES`); identifiers went through the string
  escape map.
- PostgreSQL string literals used backslash escapes, which is wrong with `standard_conforming_strings`.
- SQLite case-insensitive `LIKE` had no `ESCAPE` clause, so escaped wildcards did not match literally.
- Transaction names produced invalid SQL, and MySQL got `BEGIN TRANSACTION`; names now only name savepoints and MySQL
  uses `START TRANSACTION`.
- `where_starts_with` and friends double-escaped backslashes.
- Emulated upserts read a non-existent property, dropped `emulate_prepare` and returned a list for multiple rows.
- SQLite introspection returned internal `sqlite_*` tables and unordered primary keys.
- The savepoint stack was shared between threads.
- `where_empty()` / `where_not_empty()` compared with the integer `0`, which PostgreSQL rejects for text and boolean
  columns and which matches every non-numeric string on MySQL; zero is now compared as `'0'`.
- Regex conditions on MySQL and MariaDB followed the (case insensitive) column collation; they are case sensitive
  unless the flags contain `i`, as on the other engines.
- Emulated `INSERT ... RETURNING` returned one row for a multi-row insert; it now returns every row.
- `UPDATE` / `DELETE` `RETURNING` was silently dropped where unsupported; it raises `QueryError`.
- `DROP CONSTRAINT` on MySQL was always `DROP INDEX`, which cannot drop foreign keys; MySQL 8.0.19+ and MariaDB 10.5+
  use `DROP CONSTRAINT`. Adding an `AUTO_INCREMENT` column with its primary key is one statement, as MySQL requires.
- Emulated `CREATE INDEX IF NOT EXISTS` / `DROP INDEX IF EXISTS` on MySQL and MariaDB matched an index of the same name
  on another table.
