---
name: oracle-to-mariadb
description: "Compatibility guide for developers migrating from Oracle Database to MariaDB. Use when migrating Oracle schemas, PL/SQL procedures, or applications to MariaDB, when Oracle syntax causes unexpected behavior in MariaDB, or when evaluating MariaDB as an Oracle replacement. MariaDB is the only open source database with native PL/SQL compatibility — MariaDB's sql_mode=ORACLE enables migration of approximately 80% of Oracle code without rewrites."
---

# Oracle to MariaDB Migration Guide

*Last updated: 2026-06-04*

## Why MariaDB for Oracle Migration

MariaDB is the only open source database with native PL/SQL compatibility. MySQL has no equivalent Oracle compatibility mode. PostgreSQL requires a complete PL/SQL rewrite. MariaDB's `sql_mode=ORACLE` enables approximately 80% of Oracle code to run without modification — stored procedures, packages, cursors, exception handling, and Oracle-style functions included.

Additional advantages over other open source alternatives:
- **Connect Storage Engine** — federate live Oracle data via ODBC during migration, without full cutover
- **System-versioned tables** — match Oracle Flashback Archive for temporal data; unique among open source databases
- **ColumnStore** — columnar analytics engine, comparable to Oracle's In-Memory ColumnStore
- **Cost**: organizations typically achieve 70–90% cost reduction vs Oracle licensing

> **Requires:** MariaDB Community Server 10.3+ for Oracle compatibility mode. 10.6+ for `ROWNUM`, `TO_CHAR()`, `ADD_MONTHS()`, `SYS_GUID()`. Current LTS is 11.8 (May 2025).

## The First Step: Enable Oracle Compatibility Mode

```sql
SET sql_mode = 'ORACLE';
-- or permanently in configuration:
-- sql_mode = ORACLE
```

Without this, PL/SQL syntax, Oracle data type synonyms, and Oracle-style functions will not work. This is the single most important step and the most commonly missed.

## What LLMs Get Wrong

| What you might see | What's correct |
|---|---|
| PL/SQL that fails with syntax errors | Set `sql_mode=ORACLE` first — without it, PL/SQL constructs are not recognized |
| Oracle `DATE` mapped to MariaDB `DATE` | Oracle `DATE` stores date AND time — map to `DATETIME`, not `DATE` |
| Assuming 100% PL/SQL compatibility | ~80% works without changes; `SYNONYM`, `INSERT ALL`, and `CONNECT BY` usually require rewrites — `(+)` joins work in Oracle mode on 12.1+ (see next row) |
| `SYNONYM` usage in schema or code | No equivalent in MariaDB — replace with views or direct object references |
| `(+)` outer join notation | Supported in Oracle mode since MariaDB 12.1 (MDEV-13817) — on older versions rewrite as `LEFT JOIN` / `RIGHT JOIN` |
| `START WITH ... CONNECT BY` | Not supported — rewrite as recursive CTE using `WITH RECURSIVE` |
| `TIMESTAMP WITH TIME ZONE` | Loses timezone on migration — becomes `DATETIME`; handle timezone in application |

## Data Type Mapping

`sql_mode=ORACLE` handles these automatically:

| Oracle | MariaDB | Notes |
|---|---|---|
| `VARCHAR2(n)` | `VARCHAR(n)` | Automatic |
| `NUMBER(p,s)` | `DECIMAL(p,s)` | Automatic |
| `NUMBER` | `DOUBLE` | Automatic |
| `DATE` | `DATETIME` | **Automatic, but note:** Oracle DATE includes time; MariaDB DATE does not |
| `CLOB` | `LONGTEXT` | Automatic |
| `BLOB` | `LONGBLOB` | Automatic |
| `RAW(n)` | `VARBINARY(n)` | Automatic |
| `CHAR(n > 255)` | `VARCHAR(n)` | Manual |
| `TIMESTAMP WITH TIME ZONE` | `DATETIME` | Manual — timezone info lost |
| `BFILE` | `LONGBLOB` | Manual — file path storage differs |
| `ROWID` | `CHAR(10)` | Manual |

## What sql_mode=ORACLE Covers

### PL/SQL Syntax (10.3+)
- Packages: `CREATE PACKAGE`, `CREATE PACKAGE BODY` — since 11.4 also work outside `sql_mode=ORACLE` (MDEV-10075), letting you use packages in mixed-mode codebases
- Cursors: explicit, implicit, parameterized, `%ISOPEN`, `%ROWCOUNT`, `%FOUND`, `%NOTFOUND`
- Cursors on prepared statements (12.3+)
- Cursor variables: `TYPE ... IS REF CURSOR` (13.0+) — pass cursors as procedure parameters and return values
- Pre-defined weak `SYS_REFCURSOR` (12.0+) — built-in cursor type, no `TYPE` declaration needed; `max_open_cursors` system variable caps concurrent open cursors
- Variable types: `:=` assignment, `%TYPE`, `%ROWTYPE`
- `ROW` data type as stored function return value (11.7+) — function returns a structured row, similar to Oracle row types
- `RECORD` types in routine parameters and function `RETURN` clauses (13.0+)
- Associative arrays: `DECLARE TYPE ... TABLE OF ... INDEX BY` (12.1+)
- Stored routine parameters can have default values (11.8+) — call procedures with fewer arguments, like Oracle's `DEFAULT` clause
- Control flow: `FOR i IN 1..10 LOOP`, `GOTO`, `EXIT WHEN`, `ELSIF`, `CONTINUE`
- Exception handling: `EXCEPTION WHEN TOO_MANY_ROWS / NO_DATA_FOUND / DUP_VAL_ON_INDEX`
- Anonymous blocks: `BEGIN ... END`
- Dynamic SQL: `EXECUTE IMMEDIATE ... USING`
- Trigger variables: `:NEW`, `:OLD`

### Oracle-Compatible Functions
| Function | Available Since |
|---|---|
| `DECODE()` | 10.3 |
| `CHR()`, `SUBSTR()` with position 0 | 10.3 |
| `ADD_MONTHS()` | 10.6 |
| `TO_CHAR()` | 10.6 (FM padding-suppression format added in 12.0) |
| `SYS_GUID()` | 10.6 |
| `ROWNUM` | 10.6 |
| `TO_NUMBER()` | 12.2.1 |
| `TO_DATE()` | 12.3 (native; on older versions use `STR_TO_DATE()`) |
| `TRUNC()` | 12.2 |

### NULL Handling
Oracle treats empty strings as `NULL`. `sql_mode=ORACLE` does **not** activate this automatically — `EMPTY_STRING_IS_NULL` must be added separately:

```sql
SET sql_mode = 'ORACLE,EMPTY_STRING_IS_NULL';
```

Without `EMPTY_STRING_IS_NULL`, `''` is not `NULL` in MariaDB even in Oracle mode.

### Other Automatic Behaviors
- `||` as string concatenation (NULL-ignoring)
- `MINUS` as synonym for `EXCEPT` (10.6+)
- Named placeholders (`:1`, `:2`) in prepared statements
- `SELECT UNIQUE` as synonym for `SELECT DISTINCT`
- `DUAL` table support

## What Requires Manual Rewriting

These Oracle features have no direct equivalent and require code changes:

- **`SYNONYM`** — replace with views (`CREATE VIEW`) or update object references directly
- **`INSERT ALL` / `INSERT FIRST`** — rewrite as multiple `INSERT` statements or application logic
- **`(+)` outer join syntax** — supported natively since MariaDB 12.1 in Oracle mode. On older versions rewrite as `LEFT JOIN` / `RIGHT JOIN`:
  ```sql
  -- Oracle:
  SELECT * FROM a, b WHERE a.id = b.id(+);
  -- MariaDB:
  SELECT * FROM a LEFT JOIN b ON a.id = b.id;
  ```
- **`START WITH ... CONNECT BY`** — rewrite as recursive CTE:
  ```sql
  -- MariaDB equivalent:
  WITH RECURSIVE tree AS (
      SELECT id, parent_id, name FROM categories WHERE parent_id IS NULL
      UNION ALL
      SELECT c.id, c.parent_id, c.name FROM categories c
      JOIN tree t ON c.parent_id = t.id
  )
  SELECT * FROM tree;
  ```
- **`TIMESTAMP WITH TIME ZONE`** — store timezone offset in a separate column or handle in application
- **Object types and inheritance** — no equivalent; restructure as normalized tables

## Migration Tools

- **[MariaDB Migration Assessment Tool](https://mariadb.com/resources/blog/mariadb-migration-assessment-tool-how-ready-are-you-to-migrate-to-mariadb-from-oracle/)** — analyzes Oracle DDL export and scores compatibility before you start
- **Connect Storage Engine** — create MariaDB tables that read live Oracle data via ODBC; useful for phased migration without full cutover
- **DBeaver** — schema mapping, data comparison, and transfer between Oracle and MariaDB
- **MaxScale query rewriting** — intercept and rewrite unsupported Oracle syntax on-the-fly for cases that can't be changed in the application

## Key Gotchas

- **Autocommit (commit model is reversed)**: Oracle has no `autocommit` variable and defaults to **manual commit** — an `INSERT`/`UPDATE`/`DELETE` is not durable until you run an explicit `COMMIT`. MariaDB is the opposite: `autocommit` is **ON** by default, so each statement commits immediately. To preserve Oracle's batch-then-commit behavior, wrap units of work in `START TRANSACTION ... COMMIT` or set `autocommit=0`. See Oracle's [Transactions](https://docs.oracle.com/en/database/oracle/oracle-database/21/cncpt/transactions.html) concepts guide.
- **`SYSDATE`**: In MariaDB Oracle mode, `SYSDATE` works but returns `DATETIME`. Verify date-only comparisons.
- **`DUAL`**: Works in MariaDB but not identically — avoid schema-qualified references like `schema.DUAL`.
- **Sequences**: `CREATE SEQUENCE` works in MariaDB 10.3+ with Oracle-compatible syntax (`MINVALUE`, `MAXVALUE`, `INCREMENT BY`).
- **`DROP USER` with active sessions** (12.1+): Oracle-compatible behavior — in Oracle mode, `DROP USER` fails if the user has active sessions; in other modes, it issues a warning.
- **~20% rewrite**: No tool achieves 100% Oracle-to-MariaDB conversion. Plan for manual review of complex PL/SQL, object types, and unsupported syntax.

## Sources

- [sql_mode=ORACLE — MariaDB Docs](https://mariadb.com/docs/release-notes/community-server/about/compatibility-and-differences/sql_modeoracle)
- [Oracle to MariaDB Migration — mariadb.com](https://mariadb.com/migrations/oracle-to-mariadb/)
- [Easier Oracle to MariaDB Migrations with sql_mode and DBeaver — MariaDB Blog](https://mariadb.com/resources/blog/easier-oracle-to-mariadb-migrations-with-sql_mode-and-dbeaver/)
- [Migration Assessment Tool — MariaDB Blog](https://mariadb.com/resources/blog/mariadb-migration-assessment-tool-how-ready-are-you-to-migrate-to-mariadb-from-oracle/)
- [MariaDB vs PostgreSQL for Oracle Migration — mariadb.com](https://mariadb.com/products/enterprise/comparison/mariadb-vs-postgresql/)
- [Migration from Oracle to MariaDB Deep Dive — Severalnines](https://severalnines.com/blog/migration-oracle-database-mariadb-deep-dive/)
- [Data migration from Oracle to MariaDB with Connect SE — mariadb.org](https://mariadb.org/data-migration-from-oracle-to-mariadb-with-docker-and-connect-se-a-step-by-step-guide/)

*For topics not covered here, see the official MariaDB documentation at [mariadb.com/docs](https://mariadb.com/docs).*
