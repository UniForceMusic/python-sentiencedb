# Changelog

All notable changes to this project are documented here. The project follows [Semantic Versioning](https://semver.org/).

## 2.0.1

### Changed

- `psycopg[binary] >= 3.1` and `mysql-connector-python >= 9.0` are now regular dependencies, so
  `pip install sentiencedb` supports every engine. The `postgres` and `mysql` extras are kept but empty, so existing
  `pip install "sentiencedb[postgres]"` / `"sentiencedb[mysql]"` commands still work.
- Drivers are still imported lazily. When one cannot be imported, the `AdapterError` now says the installation is
  broken instead of suggesting an extra.

## 2.0.0

A rewrite from scratch, ported again from the PHP package
[sentience/database](https://github.com/Sentience-Framework/database) (branch `v1`). The public API is new; code
written for 1.x needs to be migrated (see "Breaking changes" below). "Behaviour changes" lists where the SQL or the
results differ from 1.x and from the PHP package.

### Added

- **MariaDB** as a separate driver (`Driver.MARIADB`, `Database.connect_mariadb`), sharing the MySQL adapter and
  dialect but with its own feature gates (`RETURNING` on 10.5+, no `LATERAL`, `REGEXP`).
- `Database.connect(driver, name, socket, queries, options, debug)` with the `Driver` enum and the `NetworkSocket` /
  `UnixSocket` connection targets; unix sockets for PostgreSQL, MySQL and MariaDB.
- A typed, flat `Options` dict covering the PHP options (SSL, `search_path`, `charset`, SQLite PRAGMAs, `use_regexp`,
  `use_tilde_regex`, `use_serials`, `strict`, ...).
- **Thread safety**: one connection per thread, created lazily and closed when the thread exits; `max_connections`
  and `acquire_timeout` cap them. `db.release_connection()` and `with db.connection():` give a thread's slot back.
  SQLite in-memory databases share one serialised connection, held by a transaction's thread until it ends.
  Savepoint stacks are per thread.
- **Error mapping**: driver exceptions become `DriverError` subclasses (`OperationalError`, `ProgrammingError`,
  `IntegrityError` and its `UniqueViolationError`, `ForeignKeyViolationError`, `NotNullViolationError`,
  `CheckViolationError`, ...), with `query`, `sqlstate`, `code` and the original exception as `__cause__`.
  `ConnectionFailedError` for connect and startup failures, `ConnectionLimitError` for the connection cap.
- **Schema introspection**: `information_schema_schemas/tables/columns/primary_keys/unique_constraints/
  foreign_key_constraints/indexes` on `Database`, and the per-table equivalents on `db.table(name)`. They return
  the same `Column`, `UniqueConstraint`, `ForeignKeyConstraint` and `Index` objects the DDL builders use.
  `information_schema_table_exists()` and `information_schema_index_exists()` (backed by `SchemaABC.table_exists()`
  / `index_exists()`) answer the emulated `IF [NOT] EXISTS` checks; they and `information_schema_tables()` take an
  optional `schema`.
- `CREATE INDEX` / `DROP INDEX` builders (`db.create_index`, `db.drop_index`), including unique and partial indexes.
- Emulation of upserts (`emulate_on_conflict`), `INSERT ... RETURNING` (`emulate_returning`) and
  `IF [NOT] EXISTS` (`emulate_if_not_exists`, `emulate_if_exists`) for engines or versions without native support.
- `SelectQuery.count()`, `select_table()`, `select_sub_query()`, lateral joins (`*_lateral*`, `outer_apply*`,
  `cross_apply*`), `joinf()`, `wheref()` / `havingf()`, `where_operator()`, `having_*` mirrors of every condition,
  `or_where_glob` / `or_where_not_glob`.
- `expressionf()` printf style SQL, `current_timestamp(precision)`, `escape_ansi()`, `escape_backslash()`.
  `expression()`, `where()` and `having()` accept `:name` placeholders with a mapping.
- `Result.from_result()` snapshots, `len(Result)`, iteration over any result, `fetch_object(cls, *args, **kwargs)`.
- `Query.to_sql()` for logging, `QueryWithParams` with named (`:name`) placeholder support.
- `commit_transaction(name=)` / `rollback_transaction(name=)` end a named savepoint and the ones opened after it.
- The package is fully typed (`py.typed`, `mypy --strict`) and has no runtime dependencies.

### Breaking changes

- **Imports.** Everything public is exported from `sentiencedb`. Removed modules and names:
  - `sentiencedb.database.DB`, `Database`, `Table`: use `sentiencedb.Database` and `sentiencedb.QueryFactory`.
  - `sentiencedb.query` became `sentiencedb.queries`, `sentiencedb.query.expressions` became
    `sentiencedb.queries.objects`, `sentiencedb.result` became `sentiencedb.results`.
  - `sentiencedb._helpers` (`raw`, `identifier`, `alias`, ...): import from `sentiencedb`.
  - `snapshot_result(result)`: use `Result.from_result(result)`.
  - `REGEX_PATTERN`: placeholders are found by a tokenizer per engine; the `QueryWithParams` conversion methods
    (`named_params_to_question_marks`, `question_marks_to_percent_s`, new `count_question_marks`) take the dialect.
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
  string (`""` by default) of `i`, `m` and `s`. `where_starts_with`, `where_ends_with`, `where_contains` and
  `where_not_contains` take `case_insensitive` and always match the value literally (no `escape_backslash` flag).
- **Joins.** Join methods no longer return a `Join` to call `.on()` on; they return the query and take an `on`
  callback: `left_join("posts", lambda join: join.on(["posts", "user_id"], ["users", "id"]))`. The `*_table`
  variants take the alias, the `*_sub_query` variants a select and an alias.
- **Upserts.** `on_conflict_do_update(conflict, updates=None)`: without updates every inserted column is overwritten.
  The `Values` / `Excluded` expressions were removed. `InsertQuery.execute()` always returns one result, also for
  emulated multi-row upserts. `emulate_on_conflict(last_insert_id)` has no `in_transaction` parameter: the
  emulation always runs in a transaction (a savepoint inside an open one).
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

### Behaviour changes compared with 1.x

- **Connections.** Each thread has its own connection (1.x used a single connection for every thread); transactions
  and savepoints are per thread. `connect()` connects eagerly, so configuration errors surface there.
- **MySQL / MariaDB `on_conflict_do_nothing`** is `ON DUPLICATE KEY UPDATE <col> = <col>` instead of
  `INSERT IGNORE`, so `NOT NULL`, truncation and foreign key errors are still raised; only duplicate keys are
  skipped.
- **`GLOB`** works on every engine (1.x raised `QueryError` outside SQLite): a `LIKE`, or an anchored regex for
  patterns with character classes.
- **Case-insensitive `LIKE`** really ignores case everywhere: `ILIKE` on PostgreSQL,
  `lower(convert(col USING utf8mb4)) LIKE lower(?)` on MySQL and MariaDB, `LIKE ... ESCAPE '\'` on SQLite.
  Case-sensitive `LIKE` is `LIKE BINARY` on MySQL and `GLOB` on SQLite.
- **Regex** conditions take the portable flags `i`, `m` and `s` with the same meaning on every engine (others raise
  `QueryError`): matching is case sensitive without `i`, also on MySQL and MariaDB, and `.` only matches a newline
  with `s`, also on PostgreSQL. 1.x passed flags through, or ignored them.
- **`where_in()` with an empty list** renders `1 = 0` (`1 = 1` for `NOT IN`) instead of `IN (NULL)`.
- **Datetimes.** MySQL and MariaDB bind and render microseconds. Timezone-aware datetimes are converted to UTC on
  MySQL, MariaDB and SQLite (bound values, `to_sql()` and `emulate_prepare` alike) and keep their offset on
  PostgreSQL. `parse_datetime()` no longer marks the values it parses as UTC.
- **Floats on MySQL and MariaDB** are bound as exact `Decimal`s (mysql-connector bound them as single precision
  `FLOAT`, storing `9.95` as `9.949999809265137`); a float selected straight back is a `Decimal`.
- **`RETURNING`** follows PostgreSQL everywhere: rows skipped by `on_conflict_do_nothing` return nothing and only
  the requested columns come back. `INSERT ... RETURNING` is emulated where unsupported (1.x raised `QueryError`);
  `UPDATE` / `DELETE` `RETURNING` still raise `QueryError` there.
- **`last_insert_id()`** returns `None` instead of `0` on SQLite, MySQL and MariaDB when the connection has generated
  no id.
- **Placeholders** are found per engine: PostgreSQL `E'..'`, `$tag$..$tag$` and nested comments; MySQL backslash
  escapes and `#` comments; SQLite `[..]` identifiers. A `#` is no longer a comment on PostgreSQL (where it is an
  operator).
- **Debug callback**: values without a SQL literal are shown as `-- params: [...]`, and an exception raised by the
  callback becomes a `RuntimeWarning` instead of failing the statement.

### Behaviour changes compared with the PHP package (bugs not ported)

- `wheref()` / `havingf()` chained with `OR`; they chain with `AND`.
- `SelectQuery.count()` used an un-aliased sub-select, which MySQL and PostgreSQL before 16 reject. It now selects
  `1` where the select list cannot change the row count, so a joined `SELECT *` (duplicate column names) also counts
  on MySQL and MariaDB.
- Foreign key `REFERENCES` tables were not escaped.
- Cross joins without conditions rendered `ON TRUE`.
- `expressionf()` did not unescape `%%`.
- MySQL string literals used double quotes (broken under `ANSI_QUOTES`); identifiers went through the string
  escape map. Literals now double the quote, so they cannot end early under `NO_BACKSLASH_ESCAPES` either.
- PostgreSQL string literals used backslash escapes, which is wrong with `standard_conforming_strings`; a string with
  a backslash is now an `E'..'` string, valid with either setting.
- SQLite case-insensitive `LIKE` had no `ESCAPE` clause, so escaped wildcards did not match literally, and stopped
  ignoring case under `deprecated_case_sensitive_like`.
- MySQL case-insensitive `LIKE` followed the column collation (case sensitive on `_bin`, `_cs` and binary columns).
- MySQL `on_conflict_do_nothing` used `INSERT IGNORE`, which also ignores `NOT NULL`, truncation and foreign key
  errors; it is `ON DUPLICATE KEY UPDATE <col> = <col>`.
- `GLOB` outside SQLite turned character classes into literal text; they become an anchored regex.
- Regex flags were passed through with engine-specific meanings (`s` is an error on MySQL 8, `.` matched newlines
  on PostgreSQL); only `i`, `m` and `s` are accepted, with one meaning.
- MySQL `BOOLEAN` columns were `TINYINT`, which introspection cannot tell from an integer; they are `TINYINT(1)`.
- The placeholder pattern applied MySQL's rules (backslash escapes, `#` comments) to every engine; each engine now
  has its own (see above).
- Timezone-aware datetimes were formatted in their own time zone without the offset; MySQL, MariaDB and SQLite get
  UTC, PostgreSQL keeps the offset.
- Transaction names produced invalid SQL, and MySQL got `BEGIN TRANSACTION`; names now only name savepoints and MySQL
  uses `START TRANSACTION`.
- `where_starts_with` and friends double-escaped backslashes.
- Emulated upserts read a non-existent property, dropped `emulate_prepare` and returned a list for multiple rows.
  They now validate every row first, insert rows whose conflict columns are `NULL` (a unique key never matches
  `NULL`) and run in a transaction.
- Emulated `INSERT ... RETURNING` returned one row for a multi-row insert and could select the wrong row; every
  row now comes back, found by its explicit id, its conflict columns or the generated id. A native upsert is
  emulated whenever `RETURNING` is, as a native one cannot say which row it touched.
- `UPDATE` / `DELETE` `RETURNING` was silently dropped where unsupported; it raises `QueryError`.
- Emulated `IF [NOT] EXISTS` searched `informationSchemaTables()` (ignoring a schema-qualified path) and, for indexes,
  every table's indexes; each check is now one catalog query in the table's schema, and MySQL and MariaDB index
  names are matched on the target table only.
- SQLite introspection returned internal `sqlite_*` tables and unordered primary keys.
- The savepoint stack was shared between threads; `transaction()` now ends exactly the level it opened and never
  replaces the original error with a failing rollback.
- `where_empty()` / `where_not_empty()` compared with the integer `0`, which PostgreSQL rejects for text and boolean
  columns and which matches every non-numeric string on MySQL; zero is now compared as `'0'`.
- Regex conditions on MySQL and MariaDB followed the (case insensitive) column collation; they are case sensitive
  unless the flags contain `i`, as on the other engines.
- `DROP CONSTRAINT` on MySQL was always `DROP INDEX`, which cannot drop foreign keys; MySQL 8.0.19+ and MariaDB 10.5+
  use `DROP CONSTRAINT`. Adding an `AUTO_INCREMENT` column with its primary key is one statement, as MySQL requires.
- MariaDB 10's `5.5.5-` version prefix was parsed as version 5.5.5, disabling `RETURNING` and `DROP CONSTRAINT`.
- `where_equals(cast=True)` cast floats to `DECIMAL` (rounding to an integer) on MySQL and datetimes to a numeric
  type on SQLite.
- `last_insert_id()` returned `0` (or `"0"`) when there was no id; it returns `None`.
