# sentiencedb

A database abstraction layer for Python: a fluent query builder, per-engine SQL dialects, DB-API adapters, schema
introspection and a small result API, for **SQLite**, **PostgreSQL**, **MySQL** and **MariaDB**.

sentiencedb is a Python port of the PHP package
[sentience/database](https://github.com/Sentience-Framework/database) (v1). The API follows the PHP one with
Python naming (`whereEquals` became `where_equals`). Version 2.0.0 is a rewrite; see [CHANGELOG.md](CHANGELOG.md)
for the changes since 1.x.

- Python 3.11 or newer, fully typed (`mypy --strict`).
- SQLite works out of the box with the standard library `sqlite3` module.
- PostgreSQL uses [psycopg 3](https://www.psycopg.org/psycopg3/); MySQL and MariaDB use
  [mysql-connector-python](https://dev.mysql.com/doc/connector-python/en/). Both are installed with the package.

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Connecting](#connecting)
- [Raw SQL](#raw-sql)
- [Query building basics](#query-building-basics)
- [Creating tables](#creating-tables)
- [Inserting rows](#inserting-rows)
- [Selecting rows](#selecting-rows)
- [Conditions](#conditions)
- [Joins](#joins)
- [Upserts](#upserts)
- [RETURNING](#returning)
- [Updating and deleting](#updating-and-deleting)
- [Altering and dropping tables](#altering-and-dropping-tables)
- [Indexes](#indexes)
- [Transactions and savepoints](#transactions-and-savepoints)
- [Results](#results)
- [Table shortcuts: `db.table()`](#table-shortcuts-dbtable)
- [Schema introspection](#schema-introspection)
- [Threading](#threading)
- [Errors](#errors)
- [Dialect differences](#dialect-differences)
- [Version encoding](#version-encoding)
- [Development](#development)
- [About this port](#about-this-port)
- [License](#license)

## Installation

```bash
pip install sentiencedb
```

This installs every engine: `psycopg[binary] >= 3.1` (PostgreSQL) and `mysql-connector-python >= 9.0` (MySQL and
MariaDB) are regular dependencies; SQLite uses the standard library. The `postgres` and `mysql` extras of earlier
versions still exist but are empty, so `pip install "sentiencedb[postgres,mysql]"` keeps working.

Driver packages are imported lazily: `import sentiencedb` does not load them; connecting to that engine does.

## Quick start

```python
from sentiencedb import Database

db = Database.connect_sqlite(":memory:")

db.create_table("users").identity("id").varchar("name", not_null=True).integer("age").execute()

db.insert("users").values({"name": "Alice", "age": 31}, {"name": "Bob", "age": 27}).execute()

rows = db.select("users").where_greater_than("age", 30).execute().fetch_dicts()
print(rows)  # [{'id': 1, 'name': 'Alice', 'age': 31}]

db.close()
```

Everything public is importable from the top-level `sentiencedb` package.

## Connecting

### `Database.connect()`

```text
Database.connect(
    driver: Driver,                 # Driver.SQLITE, Driver.PGSQL, Driver.MYSQL or Driver.MARIADB
    name: str,                      # database name, or the SQLite file path / ":memory:"
    socket: SocketABC | None = None,  # where the server is; required for every engine except SQLite
    queries: Sequence[str] = (),    # startup statements, run on every new connection
    options: Options | None = None, # see the options table below
    debug: DebugCallback | None = None,
) -> Database
```

`connect()` opens a connection straight away, reads the server version and picks the dialect for that version, so
configuration errors surface here as `ConnectionFailedError` (or `AdapterError` for a missing socket).

```python
from sentiencedb import Database, Driver

db = Database.connect(Driver.SQLITE, ":memory:")
print(db.dialect.driver, db.dialect.version > 0)  # sqlite True
db.close()
```

```python
# Requires a PostgreSQL server (docker compose up -d postgres).
from sentiencedb import Database, Driver, NetworkSocket

db = Database.connect(
    Driver.PGSQL,
    "postgres",
    NetworkSocket("localhost", 5432, username="postgres", password=None),
    queries=["SET TIME ZONE 'UTC'"],
    options={"search_path": "public"},
)
print(db.query("SHOW TIME ZONE").scalar())  # UTC
db.close()
```

### Shortcut constructors

| Method | Signature |
|---|---|
| `Database.connect_sqlite` | `(name=":memory:", *, queries=(), options=None, debug=None)` |
| `Database.connect_postgresql` | `(name, *, host="localhost", port=5432, username=None, password=None, unix_socket=None, queries=(), options=None, debug=None)` |
| `Database.connect_mysql` | same as PostgreSQL, `port=3306` |
| `Database.connect_mariadb` | same as PostgreSQL, `port=3306` |

```python
# Requires a MySQL server (docker compose up -d mysql) with a "sentiencedb" database.
from sentiencedb import Database

db = Database.connect_mysql("sentiencedb", host="127.0.0.1", username="root", password="")
print(db.dialect.driver)  # mysql
db.close()
```

### Sockets

Server engines need a socket. The `connect_*` helpers build one for you: a `UnixSocket` when `unix_socket` is given,
otherwise a `NetworkSocket(host, port, username, password)`.

- `NetworkSocket(host, port, username=None, password=None)`: TCP.
- `UnixSocket(path, username=None, password=None)`: for PostgreSQL `path` is the socket **directory** (as libpq
  expects); for MySQL and MariaDB it is the socket **file**.

```python
# Requires a local PostgreSQL server listening on /var/run/postgresql.
from sentiencedb import Database, Driver, UnixSocket

db = Database.connect(Driver.PGSQL, "app", UnixSocket("/var/run/postgresql", username="app"))
db = Database.connect_postgresql("app", unix_socket="/var/run/postgresql", username="app")
```

### Startup queries

`queries` run on **every** new connection, after the adapter's own setup (`SET NAMES`, `search_path`, PRAGMAs).
Since each thread gets its own connection (see [Threading](#threading)), put per-session settings here rather than
running them once after connecting.

```python
with Database.connect_sqlite(queries=["PRAGMA foreign_keys = ON"]) as db:
    print(db.query("PRAGMA foreign_keys").scalar())  # 1
```

`Database` is a context manager; leaving the block calls `close()`, which closes the connections of every thread.

### Options

`options` is a flat dict (`Options`, a `TypedDict`) read by both the adapter and the dialect. Every key is optional.

| Key | Engine | Effect |
|---|---|---|
| `max_connections: int` | all | Cap on open connections (one per thread). Unlimited when absent. |
| `acquire_timeout: float` | all | Seconds a thread waits for a free slot before `ConnectionLimitError`. Waits forever when absent. |
| `charset: str` | MySQL, MariaDB | Connection character set, passed to `mysql.connector.connect()`. |
| `collation: str` | MySQL, MariaDB | Connection collation, passed to `mysql.connector.connect()`; works with or without `charset`. |
| `engine: str` | MySQL, MariaDB | `SET SESSION default_storage_engine = <engine>`. |
| `ssl_mode`, `ssl_cert`, `ssl_key`, `ssl_root_cert`, `ssl_crl: str` | PostgreSQL | libpq `sslmode`, `sslcert`, `sslkey`, `sslrootcert`, `sslcrl`. |
| `client_encoding: str` | PostgreSQL | libpq `client_encoding`. |
| `search_path: str` | PostgreSQL | `SET search_path TO <search_path>` (inserted verbatim). |
| `read_only: bool` | SQLite | Open the file with `mode=ro`. |
| `encryption_key: str` | SQLite | `PRAGMA key` (SQLCipher builds only). |
| `busy_timeout: int` | SQLite | Milliseconds to wait on a locked database (default 5000). |
| `encoding: str` | SQLite | `PRAGMA encoding`. |
| `journal_mode: str` | SQLite | `PRAGMA journal_mode`, e.g. `"WAL"`. |
| `foreign_keys: bool` | SQLite | `PRAGMA foreign_keys = ON`. |
| `deprecated_case_sensitive_like: bool` | SQLite | `PRAGMA case_sensitive_like = ON`. |
| `create_functions: Mapping[str, Callable]` | SQLite | Extra SQL functions, registered on every connection. May override `REGEXP` and `regexp_like`. |
| `optimize: bool` | SQLite | `PRAGMA optimize` before a connection closes. |
| `use_regexp: bool` | MySQL, SQLite | Regex conditions use the `REGEXP` operator instead of `regexp_like()`. |
| `use_tilde_regex: bool` | PostgreSQL | Regex conditions use `~` / `!~` instead of `regexp_like()` (PostgreSQL 15+). |
| `use_serials: bool` | PostgreSQL | Auto increment columns are `SERIAL`/`BIGSERIAL` (default `True`). `False` gives `GENERATED BY DEFAULT AS IDENTITY` on PostgreSQL 10+. |
| `strict: bool` | SQLite | Append `STRICT` to `CREATE TABLE`. |

### Debug callback

`debug(query, start, error)` is called after every statement, including failed ones and startup queries. `query` is
the SQL with parameters interpolated (for display only; the statement itself was sent with bound parameters),
`start` is the `time.time()` at which the statement started and `error` is the driver's message or `None`.
Parameters that have no SQL literal (`inf`, `nan`, driver specific types) are shown after the SQL as
`-- params: [...]` instead. The callback never breaks a statement: an exception it raises is reported as a
`RuntimeWarning`.

```python
import time

from sentiencedb import Database


def debug(query: str, start: float, error: str | None) -> None:
    status = f"failed: {error}" if error else "ok"
    print(f"{query} [{status}, {(time.time() - start) * 1000:.0f} ms]")


db = Database.connect_sqlite(debug=debug)
db.prepared("SELECT ? AS answer", [42])  # prints: SELECT 42 AS answer [ok, 0 ms]
db.close()
```

## Raw SQL

| Method | Description |
|---|---|
| `db.exec(sql)` | Run SQL, discard any result. |
| `db.query(sql) -> ResultABC` | Run SQL without parameters. The text is sent as is, so `%` and `?` need no escaping. |
| `db.prepared(sql, params=(), emulate_prepare=False) -> ResultABC` | `?` placeholders with a sequence, or `:name` placeholders with a mapping (keys with or without the colon). |
| `db.query_with_params(QueryWithParams(sql, params), emulate_prepare=False)` | Same, taking a `QueryWithParams`. |

A mismatch between placeholders and parameters raises `QueryWithParamsError`. With `emulate_prepare=True` the
parameters are rendered as SQL literals by the dialect and the statement is sent as plain SQL, for servers or poolers
that cannot handle prepared statements.

**Where placeholders are found.** A `?` or `:name` is a placeholder unless it sits inside a string, a quoted
identifier or a comment, following the lexical rules of the connected engine; `::type` casts are never named
parameters.

| Engine | Skipped |
|---|---|
| SQLite | `'...'`, `"..."`, `` `...` ``, `[...]`, `-- ...`, `/* ... */` |
| PostgreSQL | `'...'`, `E'...'` (backslash escapes), `$$...$$` and `$tag$...$tag$`, `"..."`, `-- ...`, nested `/* /* */ */` |
| MySQL, MariaDB | `'...'` and `"..."` with backslash escapes, `` `...` ``, `# ...`, `-- ...` (with a space after `--`), `/* ... */` |

Limitations and workarounds:

- PostgreSQL's `?`, `?|` and `?&` jsonb operators read as placeholders once a statement has parameters. Use the
  equivalent functions `jsonb_exists(col, ?)`, `jsonb_exists_any(col, ?)` and `jsonb_exists_all(col, ?)`, or run
  the statement without parameters (`db.query()` sends it verbatim).
- The tokenizer assumes the default server settings: backslash escapes in MySQL strings (no
  `NO_BACKSLASH_ESCAPES` `sql_mode`) and `standard_conforming_strings = on` on PostgreSQL. With other settings a
  string ending in a backslash can hide or expose a `?`; avoid such literals in statements with parameters, or
  pass them as parameters.
- MySQL's executable comments (`/*! ... */`) are skipped like any other comment.

```python
from sentiencedb import Database, QueryWithParams

db = Database.connect_sqlite()
db.exec("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
db.prepared("INSERT INTO notes (body) VALUES (?), (?)", ["first", "100% done"])

print(db.prepared("SELECT body FROM notes WHERE id = :id", {"id": 2}).scalar())  # 100% done
print(db.query("SELECT count(*) AS n FROM notes").fetch_dict())  # {'n': 2}
print(db.query_with_params(QueryWithParams("SELECT ? || '?' AS s", ["why"])).scalar())  # why?
print(db.prepared("SELECT ? AS n", [7], emulate_prepare=True).scalar())  # 7
db.close()
```

## Query building basics

Builders are created from the database (`db.select(...)`, `db.insert(...)`, ...), configured with chained methods
that return the builder, and run with `execute()`. Builders only hold state; the dialect turns that state into SQL.

| Method | Description |
|---|---|
| `execute(emulate_prepare=False)` | Run the statement and return a result. |
| `to_query_with_params()` | The SQL with `?` placeholders plus its parameters, as a `QueryWithParams(query, params)`. |
| `to_sql()` | The SQL with every parameter interpolated as a literal, for logging and debugging. |

**Identifiers.** A string is one identifier, quoted for the dialect. A list is a qualified path whose parts are
quoted separately: `["users", "id"]` becomes `"users"."id"`. A dotted string is *not* split: `"users.id"` becomes
`"users.id"`, a single (probably non-existent) column. Any `Sql` object (see below) is emitted as is.

**Values.** `None`, `bool`, `int`, `float`, `Decimal`, `str`, `bytes`, `date` and `datetime` are bound as
parameters (booleans and datetimes converted to what the dialect stores); `Enum` members are bound by value.
Timezone-aware datetimes keep their offset on PostgreSQL; MySQL, MariaDB and SQLite have no time zone type, so the
dialect converts them to UTC and they are stored without offset (naive datetimes are stored as they are). Bound
values, `to_sql()` and `emulate_prepare` all use the same conversion. SQLite stores datetimes at second precision.
On MySQL and MariaDB finite floats are bound as exact decimals (mysql-connector would otherwise send them as single
precision floats), so a float selected straight back (`SELECT ?`) returns a `Decimal`. A `Sql` object is inlined
together with its own parameters, and a `SelectQuery` becomes a parenthesised sub-select. Where a `Sql` object
stands in an identifier position (a column, a table, `GROUP BY`, a default) its parameters are interpolated as
literals instead.

Helpers that create `Sql` objects and other values:

| Helper | Result |
|---|---|
| `raw(sql)` | Verbatim SQL. |
| `identifier(name_or_path)` | A quoted identifier; use it to compare a column with another column. |
| `alias(identifier, alias)` | `<identifier> AS <alias>`. |
| `expression(sql, params=())` | SQL with `?` placeholders and a list of values to bind, or `:name` placeholders and a mapping. |
| `expressionf(format, *values)` | printf style SQL: `%s` string, `%d` integer, `%f` / `%.2f` float, `%b` bool, `%%` a literal `%`. Plain values are bound; `Sql` values (e.g. `identifier(...)`) are inlined. |
| `current_timestamp(precision=None)` | `CURRENT_TIMESTAMP[(precision)]`. |
| `sub_query(select_query, alias)` | `(<select>) AS <alias>`. |
| `now()` | `datetime.now()`. |
| `escape_ansi(string, chars)` / `escape_backslash(string, chars)` | Escape `chars` by doubling them / by prefixing a backslash. |

The rest of this guide builds on one in-memory SQLite database:

```python
from sentiencedb import Database, alias, expression, expressionf, identifier, raw

db = Database.connect_sqlite(options={"foreign_keys": True})
db.exec("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")

query = (
    db.select("demo")
    .columns(["id", alias(raw("upper(name)"), "shout")])
    .where_equals("name", expression("lower(?)", ["ALICE"]))
    .wheref("%s > %d", identifier("id"), 0)
)
qwp = query.to_query_with_params()
print(qwp.query)  # SELECT "id", upper(name) AS "shout" FROM "demo" WHERE "name" = lower(?) AND ("id" > ?)
print(qwp.params)  # ['ALICE', 0]
print(query.to_sql())  # SELECT "id", upper(name) AS "shout" FROM "demo" WHERE "name" = lower('ALICE') AND ("id" > 0)
print(expressionf("%.2f%%", 12.345).raw_sql(db.dialect))  # 12.35%
```

## Creating tables

`db.create_table(table)` returns a `CreateTableQuery`. Columns are added with portable helpers that map to the right
type for each engine:

| Method | Column |
|---|---|
| `identity(name, bits=64, primary_key=True)` | Auto incrementing `NOT NULL` integer, added to the primary key. Alias: `auto_increment`. |
| `boolean(name, not_null=False, default=None)` | Boolean. |
| `integer(name, bits=64, not_null=False, default=None, generated_by_default_as_identity=False)` | `INTEGER`, or `BIGINT` above 32 bits. |
| `float(name, bits=64, not_null=False, default=None)` | Single precision up to 32 bits, double above. |
| `varchar(name, length=255, not_null=False, default=None)` | `VARCHAR(length)`. |
| `text(name, length=None, not_null=False, default=None)` | `TEXT`; on MySQL `length` picks `MEDIUMTEXT` / `LONGTEXT`. |
| `datetime(name, precision=6, not_null=False, default=None)` | Date and time with `precision` fractional second digits where supported. |
| `column(name, type, not_null=False, default=None, generated_by_default_as_identity=False)` | Any column; `type` is a raw SQL type string or a portable `Type(TypeEnum.X, size)`. |

Defaults are rendered as SQL literals; pass a `Sql` object (such as `current_timestamp()` or `raw("...")`) for an
expression. Table level methods:

| Method | Description |
|---|---|
| `primary_keys(columns)` | Replace the primary key (a column name or a list for a composite key). |
| `unique_constraint(columns, name=None)` | `[CONSTRAINT name] UNIQUE (...)`. |
| `foreign_key_constraint(columns, reference_table, reference_columns, name=None, on_update=None, on_delete=None)` | Foreign key; actions are `ReferentialActionEnum` members (`CASCADE`, `SET_NULL`, `SET_DEFAULT`, `RESTRICT`, `NO_ACTION`) or strings. |
| `constraint(sql)` | A raw table constraint, e.g. `"CHECK (age >= 0)"`. |
| `if_not_exists()` | `CREATE TABLE IF NOT EXISTS`. |
| `emulate_if_not_exists()` | Check for the table through introspection instead (see [below](#if-not-exists-emulation)). |

```python
from sentiencedb import ReferentialActionEnum, current_timestamp

create_users = (
    db.create_table("users")
    .identity("id")
    .varchar("name", 100, not_null=True)
    .varchar("email", not_null=True)
    .integer("age", bits=32)
    .boolean("active", not_null=True, default=True)
    .datetime("created_at", default=current_timestamp())
    .unique_constraint(["email"], name="uq_users_email")
    .constraint("CHECK (age >= 0)")
)
print(create_users.to_sql())
# CREATE TABLE "users" ("id" INTEGER PRIMARY KEY AUTOINCREMENT, "name" VARCHAR(100) NOT NULL, "email" VARCHAR(255) NOT NULL, "age" INTEGER, "active" BOOLEAN NOT NULL DEFAULT 1, "created_at" DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE ("email"), CHECK (age >= 0))
create_users.execute()

(
    db.create_table("posts")
    .if_not_exists()
    .identity("id")
    .integer("user_id", not_null=True)
    .varchar("title", not_null=True)
    .text("body")
    .float("rating", bits=32)
    .foreign_key_constraint("user_id", "users", "id", on_delete=ReferentialActionEnum.CASCADE)
    .execute()
)
```

The same `users` builder on the other engines (SQLite drops constraint names; it has no syntax for them):

```text
MySQL / MariaDB:  `id` BIGINT NOT NULL AUTO_INCREMENT, ..., `active` TINYINT(1) NOT NULL DEFAULT 1,
                  `created_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), PRIMARY KEY (`id`),
                  CONSTRAINT `uq_users_email` UNIQUE (`email`), ...
PostgreSQL:       "id" BIGSERIAL NOT NULL, ..., "active" BOOLEAN NOT NULL DEFAULT TRUE,
                  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY ("id"),
                  CONSTRAINT "uq_users_email" UNIQUE ("email"), ...
```

Portable types per engine (`TypeEnum` member and size):

| `TypeEnum` | MySQL / MariaDB | PostgreSQL | SQLite |
|---|---|---|---|
| `BOOLEAN` | `TINYINT(1)` (the width marks it as a boolean for introspection) | `BOOLEAN` | `BOOLEAN` (stores `1`/`0`) |
| `INTEGER` (bits <= 32 / > 32) | `INTEGER` / `BIGINT` | `INTEGER` / `BIGINT` | `INTEGER` / `BIGINT` |
| `FLOAT` (bits <= 32 / > 32) | `FLOAT` / `DOUBLE` | `REAL` / `DOUBLE PRECISION` | `REAL` |
| `VARCHAR` (length, default 255) | `VARCHAR(n)` | `VARCHAR(n)` | `VARCHAR(n)` |
| `TEXT` (max length) | `TEXT`, `MEDIUMTEXT` (> 65535), `LONGTEXT` (> 16777215) | `TEXT` | `TEXT` |
| `DATETIME` (precision) | `DATETIME(p)`, `DATETIME` when p is 0 | `TIMESTAMP(p)` below 6, else `TIMESTAMP` | `DATETIME` (second precision) |

Identity columns per engine:

- **MySQL / MariaDB**: `AUTO_INCREMENT`; the column is always added to the primary key, as MySQL requires.
- **PostgreSQL**: `SERIAL` / `BIGSERIAL` by default. With `options={"use_serials": False}` on PostgreSQL 10 and
  newer they become `GENERATED BY DEFAULT AS IDENTITY`.
- **SQLite**: `INTEGER PRIMARY KEY AUTOINCREMENT`, whatever the declared size; the column leaves the separate
  `PRIMARY KEY (...)` clause.

## Inserting rows

`db.insert(table).values(*rows)` takes one or more `{column: value}` mappings; calling `values()` again appends.
The column list is the union of all rows' keys, and a row that lacks a column inserts `DEFAULT` there (SQLite has no
`DEFAULT` keyword inside `VALUES`, so there every row must have the same columns).

```python
db.insert("users").values(
    {"name": "Alice", "email": "alice@example.com", "age": 31},
    {"name": "Bob", "email": "bob@example.com", "age": 27},
).execute()
print(db.last_insert_id())  # 2

db.insert("posts").values(
    {"user_id": 1, "title": "Hello", "body": "First post", "rating": 4.5},
    {"user_id": 1, "title": "Again", "body": "Second post", "rating": 3.0},
    {"user_id": 2, "title": "Hi", "body": None, "rating": 5.0},
).execute()
```

`db.last_insert_id(name=None)` returns the id generated by the calling thread's last insert, or `None`. On
PostgreSQL `name` is the sequence (`currval(name)`); without it `lastval()` is used.

## Selecting rows

`db.select(table)` returns a `SelectQuery`. `table` may be a name, a path, an `alias(...)`, a `sub_query(...)` or
any `Sql`. `db.select_table(table, alias=None)` and `db.select_sub_query(select_query, alias)` are shortcuts.

| Method | Description |
|---|---|
| `columns(columns)` | Replace the select list (empty selects `*`). A list of identifiers, `alias(...)`, `Sql` and sub-selects, or a `{alias: column}` mapping. |
| `distinct(on=())` | `SELECT DISTINCT`, or `DISTINCT ON (...)` with columns (PostgreSQL only; `QueryError` elsewhere). |
| `group_by(columns)` | Replace the `GROUP BY` columns. |
| `order_by_asc(column)` / `order_by_desc(column)` | Append a sort column. |
| `limit(limit)` | `LIMIT`; a negative number removes it. |
| `offset(offset)` | `OFFSET`; only emitted together with a limit. |
| `union(select_query)` / `union_all(select_query)` | Append a `UNION [ALL]`. |
| `count(emulate_prepare=False) -> int` | Run `SELECT count(*) FROM (<this select>) AS "count"`. The inner select lists `1` where its columns cannot change the row count (no `DISTINCT`, `UNION`, `HAVING` or ungrouped SQL expressions), so a joined `SELECT *` also counts on MySQL. |

```python
from sentiencedb import sub_query

adults = db.select("users").columns(["name"]).where_greater_than_or_equals("age", 18).order_by_asc("name")
print(adults.execute().fetch_dicts())  # [{'name': 'Alice'}, {'name': 'Bob'}]
print(adults.count())  # 2

ratings = (
    db.select("posts")
    .columns({"author": "user_id", "posts": raw("count(*)"), "best": raw("max(rating)")})
    .group_by(["user_id"])
    .order_by_desc("best")
    .limit(10)
)
print(ratings.execute().fetch_dicts())
# [{'author': 2, 'posts': 1, 'best': 5.0}, {'author': 1, 'posts': 2, 'best': 4.5}]

names = db.select("users").columns(["name"]).where_equals("id", 1).union_all(
    db.select("users").columns(["name"]).where_equals("id", 2)
)
print([row["name"] for row in names.execute()])  # ['Alice', 'Bob']

top = db.select(sub_query(db.select("posts").where_greater_than("rating", 4), "top")).columns([raw("count(*)")])
print(top.execute().scalar())  # 2
print(db.select_table("users", "u").columns([["u", "name"]]).limit(1).execute().fetch_dicts())  # [{'name': 'Alice'}]
```

## Conditions

`SelectQuery`, `UpdateQuery`, `DeleteQuery` and `CreateIndexQuery` (partial indexes) have `where_*` methods; join
callbacks and `where_group` callbacks get the same methods. Conditions are chained with `AND`; **every method has an
`or_` twin** that chains with `OR` (`or_where_equals`, `or_where_not_glob`, `or_wheref`, ...). `SelectQuery` has the
same set for `HAVING` with `having` in the name (`having_equals`, `or_having_in`, `having_group`, `havingf`,
`having(sql, values)`, ...), whose group callbacks receive a `HavingGroup`.

| Method | SQL (ANSI form; see the notes below for engine differences) |
|---|---|
| `where_equals(column, value, cast=False)` | `column = ?`; `None` gives `IS NULL`. `cast=True` compares both sides cast to the value's type. |
| `where_not_equals(column, value, cast=False)` | `column <> ?`; `None` gives `IS NOT NULL`. |
| `where_is_null(column)` / `where_is_not_null(column)` | `column IS [NOT] NULL` |
| `where_less_than`, `where_less_than_or_equals`, `where_greater_than`, `where_greater_than_or_equals` `(column, value)` | `<`, `<=`, `>`, `>=` |
| `where_between(column, min, max)` / `where_not_between(...)` | `column [NOT] BETWEEN ? AND ?` |
| `where_in(column, values)` / `where_not_in(...)` | `column [NOT] IN (?, ...)`; `values` may be a `SelectQuery`. An empty list gives `1 = 0` (`1 = 1` for `NOT IN`). |
| `where_like(column, pattern, case_insensitive=False)` / `where_not_like(...)` | `LIKE` with your own `%` / `_` wildcards. |
| `where_starts_with`, `where_ends_with`, `where_contains`, `where_not_contains` `(column, value, case_insensitive=False)` | `LIKE` with `value` matched literally (wildcards in it are escaped). |
| `where_glob(column, pattern, case_insensitive=False)` / `where_not_glob(...)` | `GLOB` on SQLite. Elsewhere the pattern becomes a `LIKE` pattern, or, when it has a character class such as `[a-z]`, an anchored regex. |
| `where_regex(column, pattern, flags="")` / `where_not_regex(...)` | Regular expression match. `flags` is any combination of `i` (ignore case), `m` (`^` and `$` match at line breaks) and `s` (`.` matches a newline); other flags raise `QueryError`. |
| `where_empty(column)` / `where_not_empty(column)` | `NULL`, `0` or `''` / none of those (zero is compared as `'0'`, which every engine coerces to the column type). |
| `where_exists(select_query)` / `where_not_exists(...)` | `[NOT] EXISTS (<select>)` |
| `where_group(callback)` / `where_not_group(callback)` | A parenthesised `( ... )` / `NOT ( ... )` group. The callback receives a `WhereGroup`; returning it is optional. Empty groups are dropped. |
| `where_operator(column, operator, value)` | `column <operator> ?` with the operator emitted verbatim; a list value renders `(?, ?, ...)`. |
| `wheref(format, *values)` | printf style condition (see `expressionf`). |
| `where(sql, values=())` | Raw `(sql)` with `?` placeholders and a list, or `:name` placeholders and a mapping. |

Column arguments accept everything an identifier does, so `raw("lower(name)")` or `["users", "age"]` work as well.

```python
from sentiencedb import identifier

def names(query):
    return [row["name"] for row in query.columns(["name"]).execute()]

print(names(db.select("users").where_in("id", [1, 2]).where_not_equals("name", "Bob")))  # ['Alice']
print(names(db.select("users").where_starts_with("email", "BOB", case_insensitive=True)))  # ['Bob']
print(names(db.select("users").where_between("age", 18, 30)))  # ['Bob']
print(names(db.select("users").where_regex("name", "^a", "i")))  # ['Alice']
print(names(db.select("users").where_is_null("age").or_where_less_than("age", 30)))  # ['Bob']
print(names(db.select("users").where("age > :min", {"min": 30})))  # ['Alice']
print(names(db.select("users").where_operator("name", "<>", "Alice")))  # ['Bob']

grouped = db.select("users").where_equals("active", True).where_group(
    lambda group: group.where_greater_than("age", 30).or_where_like("email", "bob@%")
)
print(grouped.to_sql())
# SELECT * FROM "users" WHERE "active" = 1 AND ("age" > 30 OR "email" GLOB 'bob@*')
print(names(grouped))  # ['Alice', 'Bob']
```

```python
with_posts = db.select("users").where_exists(
    db.select("posts").where_equals(["posts", "user_id"], identifier(["users", "id"])).where_is_null("body")
)
print(names(with_posts))  # ['Bob']

prolific = db.select("posts").columns(["user_id"]).group_by(["user_id"]).having_greater_than(raw("count(*)"), 1)
print(names(db.select("users").where_in("id", prolific)))  # ['Alice']
```

Engine notes:

- **LIKE.** Case sensitive: PostgreSQL `LIKE`, MySQL `LIKE BINARY`, SQLite `GLOB` (SQLite's `LIKE` ignores ASCII case,
  so the pattern is translated). Case insensitive: PostgreSQL `ILIKE`; MySQL
  `lower(convert(col USING utf8mb4)) LIKE lower(?)`, which also ignores case on `_bin` / `_cs` collations and binary
  columns; SQLite `LIKE ... ESCAPE '\'` (compared through `lower()` when the `deprecated_case_sensitive_like` option
  makes `LIKE` case sensitive). Patterns are always bound as parameters.
- **GLOB** outside SQLite: `*` becomes `%`, `?` becomes `_` and a one-character class such as `[*]` the literal
  character, in a `LIKE` with the case rules above. Any other character class (`[a-z]`, `[!0-9]`) turns the whole
  pattern into an anchored regex.
- **Regex.** PostgreSQL 15+ and MySQL 8+ use `regexp_like(column, pattern, flags)`; older PostgreSQL uses `~` / `!~`
  and MariaDB and older MySQL use `REGEXP`, with flags as a `(?flags)` prefix. The `use_tilde_regex` and
  `use_regexp` options force the operator form. The flags `i`, `m` and `s` mean the same everywhere: without `s`,
  `.` does not match a newline, also on PostgreSQL (whose own default differs; the dialect translates the flags into
  its newline modes), and matching is case sensitive unless the flags contain `i`, also on MySQL and MariaDB, whose
  collations usually ignore case: `regexp_like()` gets match type `c` before the flags (`s` is spelled `n` there;
  the match type is inlined as a literal, as MySQL rejects a bound one) and `REGEXP` a `(?-i)` prefix. MySQL before
  8.0 accepts no flag but `s`. SQLite has no regex support of its own: the adapter registers
  `regexp_like(value, pattern, flags)` and `REGEXP` on every connection, implemented with Python's `re`.
- **`cast=True`** casts to `UNSIGNED`, `SIGNED`, `DECIMAL` or `CHAR` on MySQL, and to the portable types elsewhere.

## Joins

Each join kind comes in three forms. `on` is an optional callback that receives a `Join`; it has `on(left, right)`
(compare two columns), `or_on(...)` and every `where_*` method. A join without conditions renders `ON TRUE`
(`ON 1` on engines without boolean literals); cross joins have no `ON`.

| Kind | Methods |
|---|---|
| `LEFT JOIN` | `left_join(table, on=None)`, `left_join_table(table, on=None, alias=None)`, `left_join_sub_query(select_query, alias, on=None)` |
| `INNER JOIN` | `inner_join`, `inner_join_table`, `inner_join_sub_query` (same parameters) |
| `CROSS JOIN` | `cross_join(table)`, `cross_join_table(table, alias=None)`, `cross_join_sub_query(select_query, alias)` |
| `LEFT JOIN LATERAL` | `left_join_lateral`, `left_join_lateral_table`, `left_join_lateral_sub_query`; also named `outer_apply*` |
| `INNER JOIN LATERAL` | `inner_join_lateral`, `inner_join_lateral_table`, `inner_join_lateral_sub_query`; also named `cross_apply*` |
| `CROSS JOIN LATERAL` | `cross_join_lateral`, `cross_join_lateral_table`, `cross_join_lateral_sub_query` |
| raw | `join(sql)`, `joinf(format, *values)` |

Lateral joins need PostgreSQL 9.3+ or MySQL 8.0.14+; MariaDB and SQLite raise `QueryError`.

```python
per_user = (
    db.select_table("users", "u")
    .columns([["u", "name"], alias(raw("count(p.id)"), "posts")])
    .left_join_table("posts", lambda join: join.on(["p", "user_id"], ["u", "id"]), alias="p")
    .group_by([["u", "name"]])
    .order_by_asc(["u", "name"])
)
print(per_user.to_sql())
# SELECT "u"."name", count(p.id) AS "posts" FROM "users" AS "u" LEFT JOIN "posts" AS "p" ON "p"."user_id" = "u"."id" GROUP BY "u"."name" ORDER BY "u"."name" ASC
print(per_user.execute().fetch_dicts())  # [{'name': 'Alice', 'posts': 2}, {'name': 'Bob', 'posts': 1}]

good_posts = (
    db.select("users")
    .columns([["users", "name"], ["posts", "title"]])
    .inner_join(
        "posts",
        lambda join: join.on(["posts", "user_id"], ["users", "id"]).where_greater_than(["posts", "rating"], 4),
    )
)
print(good_posts.execute().fetch_dicts())  # [{'name': 'Alice', 'title': 'Hello'}, {'name': 'Bob', 'title': 'Hi'}]

raw_join = db.select("users").columns(["name"]).join('INNER JOIN "posts" ON "posts"."user_id" = "users"."id"')
print(raw_join.count())  # 3
```

## Upserts

| Method | Description |
|---|---|
| `on_conflict_do_nothing(conflict)` | Skip rows that conflict. Alias: `insert_ignore`. |
| `on_conflict_do_update(conflict, updates=None)` | Update the conflicting row with `updates`; without `updates` every inserted column is overwritten with its new value. Alias: `on_duplicate_key_update`. |
| `last_insert_id(column)` | Name the auto increment column (needed by MySQL upserts and `RETURNING` emulation). |
| `emulate_on_conflict(last_insert_id)` | Emulate the upsert even where it is supported natively (see below). |

`conflict` is a list of the unique columns, or a constraint name as a string (PostgreSQL only; SQLite raises
`QueryError`, MySQL ignores the target because `ON DUPLICATE KEY UPDATE` reacts to every unique key).

```python
upsert = (
    db.insert("users")
    .values({"name": "Alice Cooper", "email": "alice@example.com", "age": 32})
    .on_conflict_do_update(["email"])
)
print(upsert.to_sql())
# INSERT INTO "users" ("name", "email", "age") VALUES ('Alice Cooper', 'alice@example.com', 32) ON CONFLICT ("email") DO UPDATE SET "name" = excluded."name", "email" = excluded."email", "age" = excluded."age"
upsert.execute()

(
    db.insert("users")
    .values({"name": "Ignored", "email": "bob@example.com", "age": 99})
    .on_conflict_do_update(["email"], {"age": expressionf("%s + 1", identifier(["users", "age"]))})
    .execute()
)
db.insert("users").values({"name": "Bobby", "email": "bob@example.com"}).on_conflict_do_nothing(["email"]).execute()
print(db.select("users").columns(["name", "age"]).execute().fetch_dicts())
# [{'name': 'Alice Cooper', 'age': 32}, {'name': 'Bob', 'age': 28}]
```

The generated SQL per engine:

| Engine | `on_conflict_do_nothing` | `on_conflict_do_update` |
|---|---|---|
| PostgreSQL 9.5+, SQLite 3.24+ | `ON CONFLICT (cols) DO NOTHING` | `ON CONFLICT (cols) DO UPDATE SET c = excluded.c, ...` |
| MySQL, MariaDB | `ON DUPLICATE KEY UPDATE c = c` on the first conflict column (else the first inserted column), or `id = last_insert_id(id)` when `last_insert_id` is set | `ON DUPLICATE KEY UPDATE c = values(c), ...` (plus `id = last_insert_id(id)` when `last_insert_id` is set, so `LAST_INSERT_ID()` returns the existing row's id) |

On MySQL and MariaDB a do-nothing upsert is an update that changes nothing, rather than `INSERT IGNORE`, which also
turns `NOT NULL`, truncation and foreign key errors into warnings: here only duplicate keys are skipped.

Refer to columns of the existing row qualified with the table name (`identifier(["users", "age"])` above): an
unqualified column in `DO UPDATE SET` is ambiguous on PostgreSQL.

**Emulation.** On engines without native upserts, or after `emulate_on_conflict(...)`, `execute()` handles each row
separately: it selects the existing row by the conflict columns and then inserts, updates or skips it. A row with a
`NULL` conflict value is always inserted, because a unique key never matches `NULL`. Every row is validated before any
statement runs: the emulation needs a list of conflict columns that every row contains, and at least one row
(otherwise `QueryError`). It raises `QueryError` when more than one existing row matches. All statements run inside
`db.transaction()`, which becomes a savepoint inside an open transaction, so a failing row leaves no partial writes.
The emulation is still not safe against concurrent writers: a row another connection inserts between the select and
the insert raises `UniqueViolationError`. `updates` must be valid in a plain `UPDATE`: `excluded.c` and `values(c)`
only work natively.

When `RETURNING` has to be emulated (see below), an upsert is emulated as well, because a native upsert does not say
which row it touched. The same goes for `on_conflict_do_nothing` with `returning()` on MySQL and MariaDB, where
`ON DUPLICATE KEY UPDATE ... RETURNING` would return the skipped row.

```python
emulated = (
    db.insert("users")
    .values({"name": "Carol", "email": "carol@example.com", "age": 45}, {"name": "Bob B.", "email": "bob@example.com"})
    .on_conflict_do_update(["email"])
    .emulate_on_conflict("id")
    .returning(["id", "name"])
)
print(emulated.execute().fetch_dicts())  # [{'id': 6, 'name': 'Carol'}, {'id': 2, 'name': 'Bob B.'}]
```

(Ids 3 to 5 were used up by the conflicting inserts above; SQLite's `AUTOINCREMENT` does not reuse them.)

## RETURNING

`returning(columns=())` on `InsertQuery`, `UpdateQuery` and `DeleteQuery` returns the affected rows; no columns means
`RETURNING *`.

An `INSERT` behaves the same on every engine and path, native or emulated, following PostgreSQL: rows the statement
inserted or updated come back with exactly the requested columns, and a row skipped by `on_conflict_do_nothing`
returns nothing.

| Engine | `INSERT` | `UPDATE` | `DELETE` |
|---|---|---|---|
| PostgreSQL 8.2+ | native | native | native |
| SQLite 3.35+ | native | native | native |
| MariaDB 10.5+ | native | `QueryError` | native |
| MySQL, older MariaDB | emulated | `QueryError` | `QueryError` |

**INSERT emulation.** Where `RETURNING` is not supported, or after `emulate_returning(last_insert_id)`, the insert
runs without it and each row is then selected back with `SELECT <columns> FROM table WHERE ... LIMIT 1`. The row is
found by the value it supplies for the `last_insert_id` column, or, in an upsert, by its conflict columns, or else by
the id the database generated (`db.last_insert_id()`). An updated row is found by its id. The column name comes from
`last_insert_id("id")` or `emulate_returning("id")`. A plain insert without it raises `QueryError`, because nothing
identifies the new row. A multi-row insert is then run as one insert (and select) per row, inside a transaction, so
every row comes back.

**UPDATE and DELETE** are not emulated: where the engine lacks `RETURNING`, `returning()` raises `QueryError`.

```python
inserted = db.insert("users").values({"name": "Dave", "email": "dave@example.com"}).returning(["id", "name"])
print(inserted.execute().fetch_dicts())  # [{'id': 7, 'name': 'Dave'}]

emulated = (
    db.insert("users")
    .values({"name": "Erin", "email": "erin@example.com"})
    .returning(["id", "email"])
    .emulate_returning("id")
)
print(emulated.to_sql())  # INSERT INTO "users" ("name", "email") VALUES ('Erin', 'erin@example.com')
print(emulated.execute().fetch_dicts())  # [{'id': 8, 'email': 'erin@example.com'}]
```

## Updating and deleting

`db.update(table).set(updates)` replaces the `{column: value}` updates; values may be `Sql` objects or sub-selects.
`db.delete(table)` deletes. Both take every `where_*` method and `returning()`. Without conditions they affect every
row. An empty `set()` raises `QueryError`.

```python
updated = (
    db.update("users")
    .set({"age": expressionf("%s + %d", identifier("age"), 1), "active": False})
    .where_equals("email", "carol@example.com")
    .returning(["name", "age"])
)
print(updated.to_sql())
# UPDATE "users" SET "age" = "age" + 1, "active" = 0 WHERE "email" = 'carol@example.com' RETURNING "name", "age"
print(updated.execute().fetch_dicts())  # [{'name': 'Carol', 'age': 46}]

deleted = db.delete("users").where_in("name", ["Dave", "Erin"]).returning(["id"])
print(deleted.execute().fetch_dicts())  # [{'id': 7}, {'id': 8}]
```

## Altering and dropping tables

`db.alter_table(table)` returns an `AlterTableQuery`. Each operation becomes its own `ALTER TABLE` statement, so
`to_sql()` returns a list of strings and `execute()` a list of results (one per operation, run in order).

| Method | Description |
|---|---|
| `add_column(name, type, not_null=False, default=None, generated_by_default_as_identity=False)` | `ADD COLUMN`. |
| `add_boolean`, `add_integer`, `add_float`, `add_varchar`, `add_text`, `add_datetime` | Same parameters as the `CREATE TABLE` helpers. |
| `add_identity(name, bits=64, add_primary_key=True)` | Auto increment column (+ `ADD PRIMARY KEY`). Alias: `add_auto_increment`. |
| `alter_column(column, sql)` | `ALTER COLUMN column <sql>`; MySQL uses `MODIFY column <sql>` (a full column definition). |
| `rename_column(old, new)` | `RENAME COLUMN old TO new`. |
| `drop_column(column)` | `DROP COLUMN`. |
| `add_primary_keys(columns)` | `ADD PRIMARY KEY (...)`. |
| `add_unique_constraint(columns, name=None)` | `ADD [CONSTRAINT name] UNIQUE (...)`. |
| `add_foreign_key_constraint(columns, reference_table, reference_columns, name=None, on_update=None, on_delete=None)` | `ADD [CONSTRAINT name] FOREIGN KEY ...`. |
| `drop_constraint(name)` | `DROP CONSTRAINT`; MySQL uses `DROP INDEX`. |
| `alter(sql)` | A raw operation after `ALTER TABLE <table>`. |

SQLite cannot alter columns, add or drop constraints or add primary keys this way; renames need SQLite 3.25 and
`drop_column` 3.35. Those operations raise `QueryError` before any statement runs.

```python
alter = db.alter_table("posts").add_varchar("slug", 50).add_datetime("published_at").rename_column("body", "content")
print(alter.to_sql())
# ['ALTER TABLE "posts" ADD COLUMN "slug" VARCHAR(50)', 'ALTER TABLE "posts" ADD COLUMN "published_at" DATETIME', 'ALTER TABLE "posts" RENAME COLUMN "body" TO "content"']
alter.execute()
db.alter_table("posts").drop_column("published_at").execute()

db.create_table("scratch").integer("n").execute()
db.drop_table("scratch").execute()
db.drop_table("scratch").if_exists().execute()  # no error: the table is gone
```

`db.drop_table(table)` returns a `DropTableQuery` with `if_exists()` and `emulate_if_exists()`.

### IF [NOT] EXISTS emulation

`if_not_exists()` / `if_exists()` on tables and indexes are emitted natively where the engine supports them (see the
[matrix](#dialect-differences)). Elsewhere, or after `emulate_if_not_exists()` / `emulate_if_exists()`, the builder
asks the database first (`information_schema_table_exists()` / `information_schema_index_exists()`, see
[introspection](#schema-introspection)) and returns an empty `Result` without running anything when there is nothing
to do. A `["schema", "table"]` path is looked up in that schema.

## Indexes

```python
index = db.create_index("posts", "idx_posts_user").columns(["user_id", "rating"])
print(index.to_sql())  # CREATE INDEX "idx_posts_user" ON "posts" ("user_id", "rating")
index.execute()

partial = (
    db.create_index("posts", "uq_posts_slug")
    .unique()
    .if_not_exists()
    .columns(["slug"])
    .where_is_not_null("slug")
)
print(partial.to_sql())
# CREATE UNIQUE INDEX IF NOT EXISTS "uq_posts_slug" ON "posts" ("slug") WHERE "slug" IS NOT NULL
partial.execute()

db.drop_index("posts", "idx_posts_user").if_exists().execute()
```

`db.create_index(table, name)` has `columns(columns)`, `unique()`, `if_not_exists()`, `emulate_if_not_exists()` and
every `where_*` method for a partial index (its values are inlined as literals, since DDL cannot take bound
parameters). MySQL and MariaDB have no partial indexes (`QueryError`). `db.drop_index(table, name)` has `if_exists()`
and `emulate_if_exists()`; on MySQL it renders `DROP INDEX name ON table`, elsewhere `table` is only used by the
emulation. The emulated checks look for the index name on `table` on MySQL and MariaDB, where index names are per
table, and on every table of the schema elsewhere.

## Transactions and savepoints

Connections run in autocommit mode; a transaction starts only when you begin one.

`db.transaction(callback, release_savepoints=False, name=None)` begins a transaction, calls `callback(db)`, commits
when it returns and rolls back and re-raises when it raises. It returns the callback's return value. Nested calls use
savepoints, so an inner failure only undoes the inner work if you catch it.

```python
def rename_all(tx: Database) -> int:
    tx.update("users").set({"active": True}).execute()
    return tx.select("users").count()

print(db.transaction(rename_all))  # 3

def failing(tx: Database) -> None:
    tx.delete("posts").execute()
    raise RuntimeError("abort")

try:
    db.transaction(failing)
except RuntimeError:
    pass
print(db.select("posts").count())  # 3
```

The manual API:

| Method | Outside a transaction | Inside a transaction |
|---|---|---|
| `begin_transaction(name=None)` | `BEGIN` (`START TRANSACTION` on MySQL) | `SAVEPOINT <name>` (default `savepoint_<n>`) |
| `commit_transaction(release_savepoints=False, name=None)` | no-op | `RELEASE SAVEPOINT` of the savepoint `name` (default the innermost one) and the savepoints opened after it, or `COMMIT` when there is none or `release_savepoints=True` |
| `rollback_transaction(release_savepoints=False, name=None)` | no-op | `ROLLBACK TO` the savepoint `name` (default the innermost one), dropping the savepoints opened after it, or `ROLLBACK` when there is none or `release_savepoints=True` |
| `in_transaction()` | `False` | `True` |

```python
db.begin_transaction()
db.insert("users").values({"name": "Frank", "email": "frank@example.com"}).execute()
db.begin_transaction("before_grace")  # SAVEPOINT "before_grace"
db.insert("users").values({"name": "Grace", "email": "grace@example.com"}).execute()
db.rollback_transaction()  # ROLLBACK TO "before_grace"
db.commit_transaction()  # COMMIT
print(db.in_transaction())  # False
print(db.select("users").columns(["name"]).where_in("name", ["Frank", "Grace"]).execute().fetch_dicts())
# [{'name': 'Frank'}]
```

An unknown savepoint `name` raises `AdapterError`, and so does beginning a nested transaction on a dialect without
savepoints. On PostgreSQL a transaction aborted by an earlier error cannot commit: `commit_transaction()` rolls it
back (to the savepoint) and raises `OperationalError`. `transaction()` always ends exactly the level it opened,
including savepoints the callback left open; when its rollback fails as well, that error is added to the original
exception as a note.

Transactions belong to the calling thread's connection (except for SQLite in-memory databases, see
[Threading](#threading)). MySQL and MariaDB commit implicitly on DDL statements.

## Results

`execute()`, `query()` and `prepared()` return a `ResultABC`.

| Method | Returns |
|---|---|
| `fetch_dict()` | The next row as a dict, or `None`. |
| `fetch_dicts()` | All remaining rows. |
| `fetch_object(cls=SimpleNamespace, *args, **kwargs)` | The next row as `cls(*args, **kwargs)` with each column set as an attribute, or `None`. |
| `fetch_objects(cls=SimpleNamespace, *args, **kwargs)` | All remaining rows as objects. |
| `scalar(column=None)` | One value of the next row: `column`, or the first column. `None` when there is no row. |
| `columns()` | `{column: native type name}` in select order (`None` when the driver cannot tell). |
| iteration | `for row in result:` yields the remaining rows as dicts. |

Driver results are **single pass** and tied to the connection of the thread that ran them: every row is returned once,
by whichever method reaches it first. Values come back as the driver produces them, without conversion: SQLite and
MySQL return booleans as `1`/`0` and SQLite returns datetimes as text. `db.dialect.parse_bool(value)` and
`db.dialect.parse_datetime(text)` convert them. Statements without a result set have no columns and no rows.

`Result` is an in-memory result: `Result.from_result(result)` drains another result into a snapshot (for example to
hand it to another thread), `Result(columns, rows)` builds one, and `len(result)` is the number of rows not yet
fetched.

```python
from dataclasses import dataclass

from sentiencedb import Result


@dataclass
class User:
    id: int = 0
    name: str = ""


result = db.select("users").columns(["id", "name"]).order_by_asc("id").execute()
print(result.columns())  # {'id': 'INTEGER', 'name': 'TEXT'}
print(result.fetch_dict())  # {'id': 1, 'name': 'Alice Cooper'}
print(result.fetch_object())  # namespace(id=2, name='Bob B.')
print(result.fetch_objects(User))  # [User(id=6, name='Carol'), User(id=9, name='Frank')]
print(result.fetch_dict())  # None

snapshot = Result.from_result(db.select("users").columns(["name"]).execute())
print(len(snapshot), snapshot.scalar())  # 4 Alice Cooper

active = db.select("users").columns(["active"]).limit(1).execute().scalar()
print(active, db.dialect.parse_bool(active))  # 1 True
```

## Table shortcuts: `db.table()`

`db.table(table)` returns a `QueryFactory` with builders and introspection bound to one table:

| Method | Equivalent |
|---|---|
| `select(columns=())` | `db.select(table).columns(columns)` |
| `insert(*rows)` | `db.insert(table).values(*rows)` |
| `update(updates)` | `db.update(table).set(updates)` |
| `delete()` | `db.delete(table)` |
| `create()` / `alter()` / `drop()` | `db.create_table(table)` / `db.alter_table(table)` / `db.drop_table(table)` |
| `create_index(name)` / `drop_index(name)` | `db.create_index(table, name)` / `db.drop_index(table, name)` |
| `columns()`, `primary_keys()`, `unique_constraints()`, `foreign_key_constraints()`, `indexes()` | `db.information_schema_*(name)` |

Introspection covers the current database / schema: an `identifier(...)` table is looked up by its name and any
other `Sql` by its SQL, while a schema-qualified `["schema", "table"]` path raises `QueryError`.

```python
users = db.table("users")
users.insert({"name": "Heidi", "email": "heidi@example.com", "age": 52}).execute()
users.update({"age": 53}).where_equals("name", "Heidi").execute()
print(users.select(["name", "age"]).where_equals("name", "Heidi").execute().fetch_dict())  # {'name': 'Heidi', 'age': 53}
users.delete().where_equals("name", "Heidi").execute()
```

## Schema introspection

`Database` reads the structure of the connected database, returning the same value objects the DDL builders use
(importable from `sentiencedb.queries.objects`):

| Method | Returns |
|---|---|
| `information_schema_schemas()` | Schema names (SQLite: the file of every attached database). |
| `information_schema_tables(schema=None)` | Base table names in `schema` (default: the current database / schema), without internal tables. |
| `information_schema_table_exists(table, schema=None)` | Whether the base table exists in `schema`. One catalog query; `pg_catalog` on PostgreSQL (also sees tables the role cannot read), case insensitive on SQLite. |
| `information_schema_index_exists(table, name, schema=None)` | Whether an index `name` exists where it would clash with a new index on `table`: on `table` itself on MySQL and MariaDB (index names are per table), anywhere in the schema on PostgreSQL and SQLite. |
| `information_schema_columns(table)` | `list[Column]` in ordinal order. `type` is a portable `Type` where one fits, else the raw SQL type; `default` is the default expression as `Raw(sql)`, or `None`. |
| `information_schema_primary_keys(table)` | Primary key column names in key order. |
| `information_schema_unique_constraints(table)` | `list[UniqueConstraint]`. On MySQL and MariaDB this includes unique indexes, which MySQL does not tell apart from constraints. |
| `information_schema_foreign_key_constraints(table)` | `list[ForeignKeyConstraint]` with the referenced table and columns and the referential actions. |
| `information_schema_indexes(table)` | `list[Index]` (`name`, `columns`, `unique`), without the primary key. Expression indexes are skipped and partial indexes are returned without their predicate. On SQLite the indexes behind `UNIQUE` constraints (`sqlite_autoindex_*`) are included. |

`table` is an unqualified name. `schema` is a schema on PostgreSQL, a database on MySQL and MariaDB, and an attached
database (`main`, `temp`, ...) on SQLite; the per-table methods only cover the current one. Because defaults come
back as `Raw` SQL, introspected columns can be fed straight back into a `CREATE TABLE`.

```python
print("posts" in db.information_schema_tables())  # True
print(db.information_schema_primary_keys("users"))  # ['id']
print([column.name for column in db.information_schema_columns("posts")])
# ['id', 'user_id', 'title', 'content', 'rating', 'slug']
foreign_key = db.table("posts").foreign_key_constraints()[0]
print(foreign_key.columns, foreign_key.reference_table, foreign_key.reference_columns)  # ['user_id'] users ['id']
print([index.name for index in db.table("posts").indexes()])  # ['uq_posts_slug']

copy = db.create_table("users_copy")
for column in db.information_schema_columns("users"):
    copy.column(column.name, column.type, column.not_null, column.default, column.generated_by_default_as_identity)
print(copy.to_sql())
# CREATE TABLE "users_copy" ("id" INTEGER PRIMARY KEY AUTOINCREMENT, "name" VARCHAR(100) NOT NULL, "email" VARCHAR(255) NOT NULL, "age" INTEGER, "active" BOOLEAN NOT NULL DEFAULT 1, "created_at" DATETIME DEFAULT CURRENT_TIMESTAMP)
copy.execute()
```

## Threading

A `Database` can be shared between threads; query builders and results cannot.

- **One connection per thread.** Every thread opens its own connection the first time it runs a statement and keeps
  it until the thread exits, it releases it, or `db.close()` closes them all (using the database afterwards raises
  `AdapterError`). Startup `queries` and the adapter's session setup run on each new connection. `connect()`
  connects immediately to validate the configuration; with `max_connections` set it releases that connection right
  away, so the connecting thread does not hold a slot.
- **Releasing a connection.** `db.release_connection()` closes the calling thread's connection and frees its slot;
  the thread's next statement opens a new one. It raises `AdapterError` inside a transaction. `with db.connection():`
  releases the connection when the block ends, unless a transaction is still open; nested blocks release on the
  outermost exit. Session state (temporary tables, `SET`, `last_insert_id()`) does not survive a release.
- **Connection cap.** `max_connections` limits open connections; a thread that cannot get a slot within
  `acquire_timeout` seconds (forever when absent) raises `ConnectionLimitError`. A thread holds its slot for as long
  as it keeps its connection, so wrap thread pool tasks in `with db.connection():`. Otherwise every live worker
  holds a slot, and the cap must cover all of them.
- **Transactions are per thread**, like the connection they run on; so is the savepoint stack.
- **SQLite in-memory databases** (`":memory:"`, `""`, `file:` URIs with `mode=memory`) exist per connection, so all
  threads share one connection that is never released, and they run one statement at a time. A transaction holds
  that connection for its thread from `BEGIN` until it ends, so the other threads wait instead of running inside it.
  `in_transaction()` is only `True` in the thread that began the transaction.
- **Results** are read completely when the statement runs, so they hold no connection or lock. Consume them in the
  thread that ran them, or snapshot them with `Result.from_result()` to hand them to another thread.

```python
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

path = os.path.join(tempfile.mkdtemp(), "app.db")
shared = Database.connect_sqlite(path, options={"journal_mode": "WAL", "max_connections": 2, "acquire_timeout": 10})
shared.create_table("hits").identity("id").integer("worker").execute()


def work(worker: int) -> None:
    with shared.connection():  # gives the slot back when the task is done
        shared.insert("hits").values({"worker": worker}).execute()


with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(work, range(20)))

print(shared.select("hits").count())  # 20
shared.close()
```

## Errors

Every exception derives from `DatabaseError`:

```text
DatabaseError
├── AdapterError                  misuse or misconfiguration (missing socket, closed database, ...)
│   └── ConnectionLimitError      no connection slot within acquire_timeout
├── QueryError                    the query cannot be built for this dialect (unsupported feature, no values, ...)
│   └── QueryWithParamsError      placeholders and parameters do not match
└── DriverError                   raised by the driver or the server
    ├── ConnectionFailedError     connecting or running a startup statement failed
    ├── OperationalError
    ├── ProgrammingError
    ├── DataError
    ├── NotSupportedError
    ├── InternalError
    └── IntegrityError
        ├── UniqueViolationError
        ├── ForeignKeyViolationError
        ├── NotNullViolationError
        └── CheckViolationError
```

`QueryError` is raised while building, before anything is sent. Driver exceptions are wrapped in the `DriverError`
subclass that matches their PEP 249 class, with the original chained as `__cause__`. A `DriverError` has `query`
(the failing SQL), `sqlstate` (PostgreSQL and MySQL) and `code` (MySQL `errno`, SQLite extended result code).
Integrity errors are refined by code:

| | PostgreSQL `sqlstate` | MySQL `errno` | SQLite extended code |
|---|---|---|---|
| `UniqueViolationError` | `23505` | `1062`, `1586` | `2067`, `1555` |
| `ForeignKeyViolationError` | `23503` | `1216`, `1217`, `1451`, `1452` | `787` |
| `NotNullViolationError` | `23502` | `1048`, `1263`, `1364` | `1299` |
| `CheckViolationError` | `23514` | `3819`, `4025` (MariaDB) | `275` |

These codes decide the class even when the driver does not raise them as an integrity error (MySQL raises `3819` as a
plain `DatabaseError`); MySQL's `1292` and `1366` (invalid values) are `DataError`s. Otherwise the class follows the
driver's own classification, falling back to the SQLSTATE class, and it differs per engine: a syntax error is a
`ProgrammingError` on PostgreSQL and MySQL but an `OperationalError` on SQLite.

```python
from sentiencedb import CheckViolationError, DriverError, QueryError, UniqueViolationError

try:
    db.insert("users").values({"name": "Copy", "email": "bob@example.com"}).execute()
except UniqueViolationError as error:
    print(error.code, error.query)  # 2067 INSERT INTO "users" ("name", "email") VALUES (?, ?)

try:
    db.insert("users").values({"name": "Young", "email": "young@example.com", "age": -1}).execute()
except CheckViolationError as error:
    print(type(error.__cause__).__name__)  # IntegrityError

try:
    db.query("SELEC 1")
except DriverError as error:
    print(type(error).__name__, error)  # OperationalError near "SELEC": syntax error

try:
    db.insert("users").values({"email": "x@example.com"}).on_conflict_do_nothing("uq_users_email").execute()
except QueryError as error:
    print(error)  # SQLite does not support named constraints
```

## Dialect differences

The dialect is chosen from the driver and the server version reported at connect time; `db.dialect` exposes it.
Version thresholds below are in `major.minor.patch` form.

| Feature | MySQL | MariaDB | PostgreSQL | SQLite |
|---|---|---|---|---|
| Identifier quote | `` ` `` | `` ` `` | `"` | `"` |
| String literals | backslash escapes | backslash escapes | ANSI (`''`) | ANSI (`''`) |
| Booleans (bound / literal) | `1` / `0` | `1` / `0` | `TRUE` / `FALSE` | `1` / `0` |
| `BOOLEAN` column | `TINYINT(1)` | `TINYINT(1)` | `BOOLEAN` | `BOOLEAN` |
| Datetime literals (aware datetimes) | microseconds (in UTC) | microseconds (in UTC) | microseconds + UTC offset | seconds (in UTC) |
| Placeholder lexing | backslash escapes, `#` comments | same as MySQL | `E''`, `$$`, nested comments | `[..]` identifiers |
| `DISTINCT ON` | no | no | 7.2+ | no |
| `LATERAL` joins | 8.0.14+ | no | 9.3+ | no |
| Native upsert | 4.1+ (`ON DUPLICATE KEY UPDATE`) | yes | 9.5+ (`ON CONFLICT`) | 3.24+ (`ON CONFLICT`, no named constraints) |
| `RETURNING` on `INSERT` / `DELETE` | no (`INSERT` emulated) | 10.5+ | 8.2+ | 3.35+ |
| `RETURNING` on `UPDATE` | no | no | 8.2+ | 3.35+ |
| `IF [NOT] EXISTS` on tables | 3.23.0+ | yes | 9.1+ | 3.3+ |
| `IF [NOT] EXISTS` on indexes | emulated | emulated | 9.5+ | 3.3+ |
| Partial indexes | no | no | yes | yes |
| `DEFAULT` in multi-row `VALUES` | yes | yes | yes | no |
| Auto increment | `AUTO_INCREMENT` | `AUTO_INCREMENT` | `SERIAL` / `BIGSERIAL`; identity with `use_serials=False` (10+) | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| Case-sensitive / -insensitive `LIKE` | `LIKE BINARY` / `lower(convert(.. USING utf8mb4)) LIKE lower(..)` | same as MySQL | `LIKE` / `ILIKE` | `GLOB` / `LIKE ... ESCAPE '\'` |
| `GLOB` | `LIKE` or regex | `LIKE` or regex | `LIKE` or regex | native |
| Regex | `regexp_like()` (8.0+), else `REGEXP` | `REGEXP` | `regexp_like()` (15+), else `~` | `regexp_like()` (Python function) or `REGEXP` |
| Regex flags | `i`, `m`, `s` (8.0+), else `s` | `i`, `m`, `s` | `i`, `m`, `s` | `i`, `m`, `s` |
| `alter_column` | `MODIFY` | `MODIFY` | `ALTER COLUMN` | `QueryError` |
| `rename_column` | yes | yes | yes | 3.25+ |
| `drop_column` | yes | yes | yes | 3.35+ |
| Add primary key / constraints, `drop_constraint` | yes (`drop_constraint` is `DROP INDEX`) | same as MySQL | yes | `QueryError` |
| `DROP INDEX` | `... ON table` | `... ON table` | plain | plain |
| `BEGIN` statement | `START TRANSACTION` | `START TRANSACTION` | `BEGIN TRANSACTION` | `BEGIN TRANSACTION` |
| Savepoints | yes | yes | yes | yes |
| `STRICT` tables | – | – | – | option `strict` |

String literals the dialects write are valid whatever the `sql_mode` or `standard_conforming_strings`: MySQL quotes
identifiers with backticks and strings with `'` (so `ANSI_QUOTES` does not matter) and doubles quotes, and PostgreSQL
writes a string with a backslash as an `E'...'` string. The placeholder tokenizer does assume the defaults; see
[Raw SQL](#raw-sql).

## Version encoding

Server versions are encoded as one integer, `major * 10000 + minor * 100 + patch`, parsed from the first
`major[.minor[.patch]]` run in the version string (vendor suffixes are ignored, missing parts count as 0, unparseable
input gives 0). `db.dialect.version` holds the encoded value; `parse_version` is in `sentiencedb.dialects`.

```python
from sentiencedb.dialects import parse_version

print(parse_version("3.45.1"))  # 34501
print(parse_version("16.2 (Debian 16.2-1)"))  # 160200
print(parse_version("8.0.34"))  # 80034
print(parse_version("10.11.6-MariaDB-1:10.11.6+maria"))  # 101106
```

You can pick a dialect for any version without a connection, which is handy for checking generated SQL:

```python
from sentiencedb import Driver

print(Driver.PGSQL.dialect("9.4").on_conflict, Driver.PGSQL.dialect("9.5").on_conflict)  # False True
print(Driver.MARIADB.dialect("10.5.0").returning, Driver.MYSQL.dialect("8.4.0").returning)  # True False
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

docker compose up -d                      # optional: PostgreSQL, MySQL and MariaDB test servers

.venv/bin/python -m pytest -q              # all tests; server tests skip when the server is unreachable
.venv/bin/python -m pytest -q -m integration   # only the tests that need a server
.venv/bin/python -m mypy src/sentiencedb   # strict
.venv/bin/python -m ruff check src/sentiencedb tests
.venv/bin/python -m ruff format --check src/sentiencedb tests
.venv/bin/python main.py                   # example script (SQLite; see its docstring for PostgreSQL / MySQL)
```

`docker-compose.yml` starts PostgreSQL on `localhost:5432` (user `postgres`, trust authentication), MySQL on
`localhost:3306` and MariaDB on `localhost:3307` (both user `root` with an empty password). The tests for each server
skip when it is unreachable; they create (and drop) their own `sentiencedb_*` databases and schemas, and `main.py`
creates a `sentiencedb` database on MySQL and MariaDB.

## About this port

sentiencedb is a port of the PHP package [sentience/database](https://github.com/Sentience-Framework/database), which
was written by hand. The Python code of both 1.x and this 2.0 rewrite was written with substantial help from AI coding
agents, working from the PHP source, and reviewed and directed by the maintainer. Behaviour that differs from the PHP
package on purpose (bug fixes, Python conventions) is listed in [CHANGELOG.md](CHANGELOG.md).

## License

MIT; see [LICENSE](LICENSE).
