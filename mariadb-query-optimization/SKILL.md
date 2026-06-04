---
name: mariadb-query-optimization
description: "Best practices for query optimization in MariaDB — indexing strategies, EXPLAIN analysis, pagination, histogram statistics, and MariaDB-specific optimizer settings. Use when diagnosing slow queries, designing indexes, reviewing schema or query performance, or when queries involve large tables, pagination, GROUP BY, or ORDER BY. Also use when the user asks about MariaDB query performance, EXPLAIN output, or optimizer behavior."
---

# MariaDB Query Optimization

*Last updated: 2026-05-25*

> **Requires:** MariaDB 10.1+ for `ANALYZE` and histograms; optimizer improvements through **11.8 LTS** (GA May 2025) form the baseline below.
>
> **Default context:** Assume MariaDB **11.8 LTS** unless the user states another version. Features marked **12.x** may be suggested when relevant (including as upgrade options), but always state the minimum version — do not present them as available on 11.8.

## What LLMs Get Wrong

| Pattern | What to do instead |
|---|---|
| `SELECT * FROM table LIMIT 10 OFFSET 50000` | Use cursor-based pagination — `OFFSET` scans all skipped rows |
| Blanket rule "functions on indexed columns kill indexes" | Outdated on MariaDB 11.1+/11.3+ for many cases. `YEAR(col) = const` and `UPPER(col) = const` on case-insensitive columns can now use indexes — see [Functions on indexed columns](#functions-on-indexed-columns) below |
| Adding an index to a low-cardinality column (boolean, status with 2-3 values) | Optimizer skips indexes with low selectivity and does a table scan anyway |
| Not running `ANALYZE TABLE` after bulk inserts | Histogram statistics become stale; optimizer makes poor plan choices |
| Composite index `(a, b, c)` used in `WHERE b = 1 AND c = 2` | Leftmost prefix rule: this skips `a`, so the index is not used |
| `SELECT *` in queries with JOINs | Name only the columns needed — prevents accidentally blocking covering indexes |
| `ALTER TABLE t ALTER INDEX idx INVISIBLE` to disable an index | That's MySQL syntax. MariaDB uses `IGNORED` — see [Ignored Indexes](#ignored-indexes-not-invisible) below |

## Reading EXPLAIN

Run `EXPLAIN` before tuning anything:

```sql
EXPLAIN SELECT * FROM orders WHERE customer_id = 42 ORDER BY created_at DESC LIMIT 10;
```

**Red flags in the output:**

| Field | Red flag | What it means |
|---|---|---|
| `type` | `ALL` | Full table scan — missing index or index not used |
| `key` | `NULL` | No index used despite one existing — check for function on column or type mismatch |
| `rows` | Very high number | Optimizer estimates scanning many rows |
| `Extra` | `Using filesort` | Expensive sort not covered by an index |
| `Extra` | `Using temporary` | Temp table created — often from `GROUP BY` or `DISTINCT` |
| `Extra` | `Using index` | ✅ Good — covering index, no table row access needed |

**`ANALYZE`** statement (MariaDB 10.1+) actually executes the query and shows real row counts vs. estimates — more reliable than `EXPLAIN` alone. Note: MariaDB uses `ANALYZE`, not `EXPLAIN ANALYZE`:

```sql
ANALYZE SELECT * FROM orders WHERE customer_id = 42;
```

**Optimizer Trace** shows the optimizer's full decision process. Since MariaDB 12.1 the trace can include full table and view definitions (`optimizer_record_context` system variable). Since 13.0 it also includes the specific statistics (histograms, index stats) used for cardinality estimates — together they're powerful for diagnosing surprising `rows` estimates:

```sql
SET optimizer_trace = 'enabled=on';
SELECT * FROM orders WHERE customer_id = 42;
SELECT * FROM INFORMATION_SCHEMA.OPTIMIZER_TRACE\G
SET optimizer_trace = 'enabled=off';
```

## Indexing Rules

### The Leftmost Prefix Rule

For a composite index `(a, b, c)`, MariaDB can use:
- `WHERE a = 1` ✅
- `WHERE a = 1 AND b = 2` ✅
- `WHERE a = 1 AND b = 2 AND c = 3` ✅
- `WHERE b = 2` ✗ — skips `a`, index not used
- `WHERE a = 1 AND c = 3` — only `a` part is used

Put the most selective equality conditions first, then range conditions last:
```sql
-- Query: WHERE status = 'active' AND created_at > '2025-01-01' ORDER BY created_at
INDEX (status, created_at)  -- ✅ equality first, range last
INDEX (created_at, status)  -- ✗ range first breaks the prefix for status
```

### Covering Indexes

A covering index includes all columns needed by the query — no table row access needed (`Using index` in EXPLAIN):

```sql
-- Query fetches id, status, created_at for a customer
-- Covering index includes all three:
CREATE INDEX idx_customer_cover ON orders (customer_id, status, created_at);
-- Now EXPLAIN shows: Extra = Using index
```

### When NOT to Add an Index

- **Low-cardinality columns**: a `status` column with values `active`/`inactive` affects 50% of rows — the optimizer prefers a table scan. Index useful only when combined with other high-selectivity columns.
- **Small tables** (< a few thousand rows): full scans are faster than index lookups for tiny tables.
- **Write-heavy columns**: every index slows `INSERT`, `UPDATE`, `DELETE` — don't index columns that are rarely queried.

### Ignored Indexes (not INVISIBLE)

To make the optimizer skip an index without dropping it — useful for testing whether an index is actually needed before removing it — MariaDB uses `IGNORED`, **not** MySQL's `INVISIBLE`:

```sql
-- ✅ MariaDB syntax (10.6+):
ALTER TABLE demo ALTER INDEX index_name IGNORED;
ALTER TABLE demo ALTER INDEX index_name NOT IGNORED;  -- re-enable

-- ✗ MySQL syntax — fails on MariaDB:
ALTER TABLE demo ALTER INDEX index_name INVISIBLE;
```

The index is still maintained on writes; it's just hidden from the optimizer. A primary key cannot be ignored. See [Ignored Indexes](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/optimization-and-indexes/ignored-indexes).

### Functions on Indexed Columns

The classic rule "any function on an indexed column disables the index" is **outdated for MariaDB 11.1+ and 11.4 LTS**. The optimizer can now use indexes for a number of common function patterns:

| Pattern | Works on the index? | Since |
|---|---|---|
| `WHERE YEAR(col) = 2025` | ✅ — sargable, picks the right range | 11.1+ (MDEV-8320) |
| `WHERE DATE(col) <= '2025-12-31'` | ✅ — sargable | 11.1+ (MDEV-8320) |
| `WHERE UPPER(varchar_col) = '...'` on a case-insensitive collation (e.g. `utf8mb4_uca1400_ai_ci`) | ✅ — `sargable_casefold=ON` is the default | 11.3+ (MDEV-31496) |
| `WHERE SUBSTR(col, 1, n) = 'abc'` | ✅ — leading-prefix `SUBSTR` is optimized | 11.8+ (MDEV-34911) |
| `WHERE LOWER(case_sensitive_col) = '...'` | ✗ — index not used (collation isn't case-insensitive) | — |
| `WHERE CAST(col AS UNSIGNED) = 1` or other type-changing transforms | ✗ — index not used | — |

For cases that the optimizer still can't sargabilize, the rewrite-to-range pattern remains valid:

```sql
WHERE created_at >= '2025-01-01' AND created_at < '2026-01-01'
```

Verify with `EXPLAIN` rather than assuming: on 11.4+ many "won't use the index" rewrites are now no-ops. If `EXPLAIN` still shows `type=ALL` for a sargable pattern, check `@@optimizer_switch` for `sargable_casefold` and confirm the column's collation is `_ci`.

## Pagination: Cursor-Based Instead of OFFSET

`OFFSET` is a hidden performance trap. `LIMIT 10 OFFSET 50000` scans and discards 50,000 rows on every page load.

```sql
-- ✗ Slow — scans 50,000 rows to skip them:
SELECT id, title FROM posts ORDER BY id DESC LIMIT 10 OFFSET 50000;

-- ✅ Fast — index seek directly to the cursor position:
-- First page:
SELECT id, title FROM posts ORDER BY id DESC LIMIT 10;

-- Next page (pass last id from previous result as $last_id):
SELECT id, title FROM posts WHERE id < $last_id ORDER BY id DESC LIMIT 10;
```

For filtered queries, include the filter column in the index alongside id:

```sql
-- Query: WHERE category = 'news' ORDER BY id DESC
CREATE INDEX idx_cat_id ON posts (category, id);
-- Cursor query:
SELECT id, title FROM posts WHERE category = 'news' AND id < $last_id ORDER BY id DESC LIMIT 10;
```

To detect whether another page exists, fetch `LIMIT 11` and check if the 11th row appears.

## Histogram Statistics

Histograms let the optimizer understand data distribution on non-indexed columns — critical for query plan quality on complex queries. Without them, the optimizer assumes uniform distribution and can choose wrong join orders.

```sql
-- Collect histograms for a table (requires a full scan — run during low traffic):
ANALYZE TABLE orders;

-- Verify histograms were collected:
SELECT * FROM mysql.column_stats WHERE table_name = 'orders';
```

**When to run `ANALYZE TABLE`:**
- After bulk inserts or large data changes
- When `EXPLAIN` shows unexpectedly high `rows` estimates
- After initially creating a table and loading data

**Tune histogram granularity** for tables with highly skewed data distributions:
```sql
SET histogram_size = 100;  -- default is 0 (disabled) in older versions, 254 in 10.4.3+
ANALYZE TABLE orders;
```

Histograms are collected per-column automatically when using `ANALYZE TABLE` with `histogram_size > 0`. They are stored in `mysql.column_stats` and consulted when `optimizer_use_condition_selectivity >= 4` (default in 10.4.1+).

## MariaDB Optimizer Switches

MariaDB's optimizer has more tunable flags than MySQL. The most useful for developers:

```sql
-- See current settings:
SELECT @@optimizer_switch\G

-- Disable a specific optimization for a session (useful for debugging):
SET optimizer_switch = 'derived_merge=off';

-- Re-enable:
SET optimizer_switch = 'derived_merge=on';
```

**Most impactful flags:**

| Flag | Default | Effect |
|---|---|---|
| `derived_merge` | on | Merges derived tables into outer query — usually faster |
| `semijoin` | on | Optimizes `IN`/`EXISTS` subqueries — disable to debug unexpected plans |
| `subquery_cache` | on | Caches correlated subquery results — big win for repeated subqueries |
| `rowid_filter` | on | Pre-filters rowids before fetching rows — helps range queries |
| `mrr` | off | Multi-Range Read — enable for large range scans on spinning disks |

Turn flags off one at a time to isolate which optimization is causing a bad plan, then report via JIRA if a default setting produces a worse plan than the alternative.

### Optimizer Improvements in the 10.7–10.11 LTS Window

The 10.11 LTS line bundles features that arrived in the 10.7–10.10 short-term releases:

- **JSON-format histograms** (10.8+, MDEV-21130, MDEV-26519) — histogram statistics are stored in JSON and are more precise than the older binary format. Just running `ANALYZE TABLE` on 10.8+ gives the optimizer better cardinality estimates.
- **Descending indexes** (10.8+, MDEV-13756) — `CREATE INDEX idx ON t (a ASC, b DESC)` is supported; useful for composite `ORDER BY a, b DESC` patterns and for `MIN()`/`MAX()` on descending indexes.
- **`SHOW ANALYZE [FORMAT=JSON]`** (10.9+, MDEV-27021) — get the optimizer plan and runtime stats for a query running in another connection without intrusion. `EXPLAIN FOR CONNECTION` syntax also supported (MDEV-10000).
- **Improved optimization for joins with many `eq_ref` tables** (10.10+, MDEV-28852, MDEV-26278) — large star-schema-style joins plan dramatically better.
- **`ANALYZE FORMAT=JSON` reports time spent in the optimizer itself** (10.11+, MDEV-28926) — separates planning time from execution time.

### Optimizer Improvements in 11.4 LTS

The 11.4 LTS line continues the overhaul:

- **New cost-based cost model** (11.0+) — replaces the older rule-based heuristics with a tuned model aware of SSDs and per-engine characteristics. `EXPLAIN` and join-order choices in 10.6 vs. 11.4 can differ noticeably on the same query. If you have manual `optimizer_adjust_secondary_key_costs` settings from 10.x, remove them — they're no-ops on 11.4+.
- **Semi-join optimization for single-table `UPDATE`/`DELETE`** (11.1+, MDEV-7487) — subqueries inside `UPDATE`/`DELETE` can now use the same subquery rewrites that `SELECT` uses (materialization, semi-join, etc.). Often a large speedup, no rewrite needed.
- **Sargable `DATE`/`YEAR` comparisons against constants** (11.1+, MDEV-8320) — see [Functions on Indexed Columns](#functions-on-indexed-columns) above.
- **Sargable case-folding** (11.3+, MDEV-31496, `sargable_casefold` on by default) — `UCASE`/`LCASE`/`UPPER`/`LOWER` on a column with a case-insensitive collation can use the index.

### Optimizer Improvements in 11.5–11.8 LTS

These are part of the current LTS baseline — useful for understanding what the optimizer can do today:

- **Index Condition Pushdown on partitioned tables** (11.5+, MDEV-12404) — previously partitioned tables couldn't use ICP; now they do, often a large speedup on partitioned schemas
- **`ANALYZE` shows selectivity of pushed index condition** (11.5+, MDEV-18478) — useful when diagnosing whether ICP is helping
- **Charset Narrowing Optimization on by default** (11.8+, MDEV-34380) — eliminates unnecessary character set conversions in WHERE clauses
- **`SUBSTR(col, 1, n) = const_str` optimization** (11.8+, MDEV-34911) — the optimizer can now use a column index even when the condition is a leading-prefix `SUBSTR`
- **Virtual column support in the optimizer** (11.8+, MDEV-35616) — see [Virtual Column Support in the Optimizer](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/query-optimizations/virtual-column-support-in-the-optimizer); previously, virtual columns were largely invisible to the optimizer
- **Cost-based subquery strategy for single-table `UPDATE`/`DELETE`** (11.8+, MDEV-25008) — the optimizer now picks between subquery strategies by cost

### Optimizer Improvements in MariaDB 12.x

Several further limitations were lifted in the 12.x rolling releases:

- **Rowid filtering on reverse-ordered scans** (12.0+) — previously `ORDER BY ... DESC` queries couldn't benefit from rowid filtering; now they can
- **Index Condition Pushdown on reverse-ordered scans** (12.0+) — same fix for ICP
- **Loose Index Scan ("Use index for group-by") works with `DESC` key parts** (12.0+) — previously required `ASC` indexes
- **GROUP BY / ORDER BY can use indexes on virtual columns** (12.1+)
- **Reorderable LEFT JOIN optimization** (12.3+) — the optimizer can now reorder more `LEFT JOIN` combinations
- **Distinct GROUP BY column inference** (12.2+) — derived tables with `GROUP BY` are recognized as having distinct group keys, enabling more optimizations downstream

If you target the 11.8 LTS baseline and see a plan that looks needlessly slow on a reverse-ordered or virtual-column query, it may be one of these — verify by running the same query on a 12.x version.

## Optimizer Hints

MariaDB 12.0 introduced a comprehensive MySQL-8-style optimizer hints framework (MDEV-35504), with additional hints added through 12.1 and 12.2. Hints go in a `/*+ ... */` comment right after `SELECT` and override the optimizer for one query without changing session settings:

```sql
SELECT /*+ JOIN_ORDER(o, c) */ *
FROM orders o JOIN customers c ON c.id = o.customer_id;
```

**Available hints:**

| Hint | Since | Purpose |
|---|---|---|
| `QB_NAME(name)` | 12.0 | Name a query block so other hints can target it from outside |
| `JOIN_FIXED_ORDER` / `JOIN_ORDER(t1, t2, ...)` | 12.0 | Force a join order (`JOIN_FIXED_ORDER` is similar to `STRAIGHT_JOIN`) |
| `JOIN_PREFIX(t1, ...)` / `JOIN_SUFFIX(t1, ...)` | 12.0 | Force specific tables to be first or last in the join order |
| `MAX_EXECUTION_TIME(ms)` | 12.0 | Abort the query if it runs longer than the timeout |
| `[NO_]MRR` / `[NO_]BKA` / `[NO_]BNL` | 12.0 | Toggle Multi-Range Read, Batched Key Access, Block Nested Loop |
| `[NO_]ICP` | 12.0 | Toggle Index Condition Pushdown |
| `[NO_]RANGE_OPTIMIZATION` | 12.0 | Toggle range optimizer |
| `SEMIJOIN(strategy, ...)` / `SUBQUERY(strategy)` | 12.0 | Pick subquery rewrite strategy |
| `[NO_]INDEX(t idx, ...)` / `[NO_]JOIN_INDEX` / `[NO_]GROUP_INDEX` / `[NO_]ORDER_INDEX` | 12.1 | Force / forbid specific index usage by purpose |
| `[NO_]SPLIT_MATERIALIZED` / `[NO_]DERIVED_CONDITION_PUSHDOWN` / `[NO_]MERGE` | 12.1 | Control subquery / derived-table optimizations |
| `[NO_]ROWID_FILTER` / `[NO_]INDEX_MERGE` | 12.2 | Toggle rowid filtering and index merge |

**`QB_NAME()` example** — name a subquery so an outer hint can target it:

```sql
SELECT /*+ NO_MERGE(@sub) */ *
FROM (
    SELECT /*+ QB_NAME(sub) */ customer_id, COUNT(*) AS n
    FROM orders
    GROUP BY customer_id
) t
WHERE n > 10;
```

Hints are more targeted than `SET optimizer_switch` because they apply only to the query they're in, not the whole session.

## Quick Wins Checklist

Before adding indexes or rewriting queries, check these first:

1. `EXPLAIN` the slow query — confirm where the time actually is
2. `ANALYZE TABLE` — stale statistics cause bad plans
3. Check for functions on indexed columns in `WHERE` — note many cases are now sargable on 11.4+ (`YEAR()`, `DATE()`, `UPPER()` on `_ci` collations)
4. Check for `OFFSET` in pagination queries
5. Verify composite index column order matches query predicates (leftmost prefix)
6. Check `EXPLAIN` Extra column for `Using filesort` or `Using temporary` — these often point to a missing or misordered index

## Sources

- [Query Optimizations — MariaDB KB](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/query-optimizations)
- [EXPLAIN — MariaDB KB](https://mariadb.com/docs/server/reference/sql-statements/administrative-sql-statements/analyze-and-explain-statements/explain)
- [optimizer_switch — MariaDB KB](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/query-optimizations/optimizer-switch)
- [Getting Started with Indexes — MariaDB KB](https://mariadb.com/docs/server/mariadb-quickstart-guides/mariadb-indexes-guide)
- [Building the Best Index for a Given SELECT — MariaDB Docs](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/optimization-and-indexes/building-the-best-index-for-a-given-select)
- [Histogram-Based Statistics — MariaDB KB](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/query-optimizations/statistics-for-optimizing-queries/histogram-based-statistics)
- [Pagination Optimization — MariaDB KB](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/query-optimizations/pagination-optimization)

*For topics not covered here, see the official MariaDB documentation at [mariadb.com/docs](https://mariadb.com/docs).*
