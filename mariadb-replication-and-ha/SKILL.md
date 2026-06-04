---
name: mariadb-replication-and-ha
description: "Best practices for MariaDB replication and high availability — Galera Cluster, GTID replication, semi-synchronous replication, parallel replication, and application patterns for HA environments. Use when setting up replication, designing applications for HA, working with Galera Cluster, asking about MariaDB GTID, handling replication lag in application code, or choosing between replication topologies. IMPORTANT — MariaDB GTID format is incompatible with MySQL GTID, and Galera Cluster needs no server plugin to load but does require the separate galera wsrep provider library (galera-4)."
---

# MariaDB Replication and High Availability

*Last updated: 2026-06-04*

MariaDB offers three tiers of replication depending on your consistency and availability requirements:

| Approach | Consistency | Failover | Best for |
|---|---|---|---|
| **Standard async replication** | Eventual | Manual or tool-assisted | Read scaling, backups, low-latency writes |
| **Semi-synchronous replication** | Eventual (same as async) | Manual or tool-assisted | Ensuring a replica *received* each commit before the client is acknowledged — bounds failover loss (lossless only with `AFTER_SYNC`) |
| **Galera Cluster** | Synchronous (multi-primary) | Automatic | Zero-data-loss HA, multi-datacenter writes |

> **Requires:** GTID replication, semi-synchronous replication, and parallel replication (including `optimistic` mode) are all built in and have been available since well before any currently-supported release — assume they are present on the **11.8 LTS** baseline. Current LTS is 11.8 (GA May 2025).
>
> **Default context:** Assume MariaDB **11.8 LTS** unless the user states another version. Features marked **12.x** or **13.0** may be suggested when relevant (including as upgrade options), but always state the minimum version — do not present them as available on 11.8.

## What LLMs Get Wrong

| What you might see | What's correct |
|---|---|
| `CHANGE REPLICATION SOURCE TO`, `SOURCE_*` connection options | MariaDB has no `CHANGE REPLICATION SOURCE TO` — use `CHANGE MASTER TO` with `MASTER_*` options. Since 10.5.1, `START REPLICA` / `SHOW REPLICA STATUS` are canonical; `START SLAVE` / `SHOW SLAVE STATUS` are legacy aliases |
| `replica_parallel_type` with values `DATABASE` or `LOGICAL_CLOCK` | MariaDB uses `slave_parallel_mode` (`optimistic` / `conservative` / `aggressive` / `minimal` / `none`) — a different implementation; MySQL's mode settings do not port over. Pool size: `slave_parallel_threads` |
| MySQL GTID format or `gtid_mode=ON` syntax | MariaDB GTID uses a different format (`domain-server-seq`) and different commands — MySQL and MariaDB GTIDs are **incompatible** |
| MySQL Group Replication or InnoDB Cluster — `START GROUP_REPLICATION`, `group_replication_*` variables, MySQL Shell `dba.createCluster()` | MariaDB has **no Group Replication and no InnoDB Cluster** — Group Replication is [incompatible with MariaDB](https://mariadb.com/docs/release-notes/community-server/about/compatibility-and-differences/mariadb-vs-mysql-compatibility). The synchronous multi-primary equivalent is **Galera Cluster** (built in) |
| "Install the Galera plugin" | There is no Galera *plugin* to load — wsrep support is built into the MariaDB server. But a cluster still requires the separate **Galera wsrep provider library** (`galera-4` / `libgalera_smm.so`, set via `wsrep_provider`); on 12.3+ you must install it yourself (see the Galera Cluster section) |
| Assuming sequential `AUTO_INCREMENT` in Galera | Galera produces gaps in auto-increment sequences across nodes by design — never rely on sequential values |
| `LOCK TABLES` or `GET_LOCK()` in a Galera environment | Not supported in Galera — use transactions instead |
| Treating a replica as a backup | Replication is not a backup — a `DROP TABLE` on the primary replicates immediately to all replicas |
| Tables without primary keys in a Galera cluster | All tables in Galera must have a primary key — `DELETE` fails on keyless tables |

## Standard Async Replication

The foundation: one primary, one or more replicas. The primary writes to the binary log; replicas apply changes asynchronously.

**GTID-based replication is the default since MariaDB 10.10** (MDEV-19801) and remains so on 10.11 LTS, 11.4 LTS, and 11.8 LTS. On a fresh replica start, a `RESET SLAVE`, or a `CHANGE MASTER TO` that omits `MASTER_USE_GTID`, the replica defaults to `slave_pos` instead of legacy file/position. If you have configs that rely on the old behavior, set `MASTER_USE_GTID=no` explicitly.

```sql
-- On replica (10.10+ — MASTER_USE_GTID is optional, slave_pos is the default):
CHANGE MASTER TO
  MASTER_HOST='primary.host',
  MASTER_USER='repl_user',
  MASTER_PASSWORD='password',
  MASTER_USE_GTID = slave_pos;

START SLAVE;
```

**Promoting a replica to primary** — historically `MASTER_USE_GTID=current_pos` was used to include locally-written GTIDs. **`current_pos` is deprecated since 10.10** (MDEV-20122). Use `MASTER_DEMOTE_TO_SLAVE=1` instead: it converts the old primary's `gtid_binlog_pos` into `gtid_slave_pos` so the demoted server can attach to the new primary cleanly without race conditions.

```sql
-- On the former primary, being demoted to a replica (10.10+):
CHANGE MASTER TO
  MASTER_HOST='new_primary.host',
  ...,
  MASTER_DEMOTE_TO_SLAVE=1;
START SLAVE;
```

Since MariaDB 13.0, `CHANGE MASTER` also resets `Master_Server_Id` in `SHOW SLAVES STATUS`. On older versions this field could carry stale values across primary changes — check it explicitly when reconfiguring replication on pre-13.0 servers.

### MariaDB GTID Format

MariaDB GTIDs have three components: `domain_id-server_id-sequence` (e.g., `0-1-247`).

This is **different from MySQL's** `server_uuid:sequence` format. They are not compatible — a MariaDB primary cannot replicate to a MySQL replica using GTIDs, and vice versa.

**Domain IDs** (`gtid_domain_id`) identify independent replication streams. The rule is about **concurrency of writes, not server count** — a common and damaging mistake is to give every server its own domain ID:

- **Single active primary, including simple failover:** leave `gtid_domain_id = 0` on **all** servers. In an `A → B` pair where `B` is later promoted (so it becomes `B → A`), `A` and `B` **share the same** domain ID — do **not** give them different ones.
- **Multiple primaries updated concurrently** (multi-source, or multi-primary within one topology): give **each concurrently-updated primary** its own distinct `gtid_domain_id`, so each stream stays independently ordered and automatic GTID replica-switchover works correctly.

```sql
-- Multi-source / multi-primary ONLY — each concurrently-written primary gets its own domain:
SET GLOBAL gtid_domain_id = 1;   -- on primary A
SET GLOBAL gtid_domain_id = 2;   -- on primary B
```

Assigning a distinct domain ID per server otherwise complicates the GTID position and loses the single ordered binlog stream. See [Global Transaction ID](https://mariadb.com/docs/server/ha-and-performance/standard-replication/gtid).

In multi-source replication, replication commands and variables (`START SLAVE`, `SHOW SLAVE STATUS`, …) act on the connection named by `default_master_connection`. It was session-only — you had to `SET SESSION default_master_connection='name'` in each session before issuing commands for that source. Since MariaDB 13.0 ([MDEV-9247](https://mariadb.com/docs/release-notes/community-server/13.0/mariadb-13.0-changes-and-improvements)) it can also be set **globally**, making a chosen named connection the default for all sessions.

### Parallel Replication

By default, replicas apply events serially. Parallel replication (up to 10× faster on write-heavy workloads) uses a pool of worker threads:

```ini
# my.cnf on replica:
slave_parallel_threads = 4
slave_parallel_mode = optimistic   # default since 10.5.1 — tries parallel, retries on conflict
```

`optimistic` mode applies transactions in parallel and retries on conflict. Use `conservative` for stricter workloads where conflict retries are unacceptable.

> **Different from MySQL:** MariaDB's `slave_parallel_mode` (`optimistic`, `conservative`, etc.) is its own implementation — not equivalent to MySQL's `replica_parallel_type` (`DATABASE` / `LOGICAL_CLOCK`). Copy-pasting a MySQL parallel-replication mode config will not work. Pool size is `slave_parallel_threads` (alias `slave_parallel_workers`).

Since MariaDB 12.1, parallel replication also works when **asynchronously replicating between two Galera clusters** (MDEV-20065) — useful for cross-datacenter or DR setups where one Galera cluster is an async replica of another.

### Replication Improvements in 10.7–10.11 LTS

- **Two-phase `ALTER TABLE` replication** (10.8+, MDEV-11675, `binlog_alter_two_phase`) — opt-in: when enabled, a large `ALTER TABLE` can start on the replica while the primary is still executing it, rather than only after, which can reduce replication lag during schema changes. Off by default. Treat as advanced/experimental — validate thoroughly before relying on it in production rather than enabling it by default.
- **GTID-aware `mariadb-binlog`** (10.8+, MDEV-4989) — `--start-position` and `--stop-position` accept GTID lists, so point-in-time replay tools can target GTIDs directly without needing binlog file/offset pairs. (Separately, `--gtid-strict-mode` — on by default — is only a safeguard: it checks that GTID sequence numbers are monotonic per domain and aborts on out-of-order events; it does not enable GTID targeting.) See [mariadb-binlog](https://mariadb.com/docs/server/clients-and-utilities/logging-tools/mariadb-binlog).
- **`slave_max_statement_time`** (10.10+, MDEV-27161) — caps how long a single statement may run on the replica SQL thread. If a replicated statement exceeds it, the SQL thread **stops with an error** ([error 3024](https://mariadb.com/docs/server/reference/error-codes/mariadb-error-codes-3000-to-3099/e3024)) so a slow or runaway query surfaces instead of lag growing unnoticed. It does **not** skip the statement and continue — replication halts until you investigate and restart it.
- **`mariadb-binlog --do-domain-ids` / `--ignore-domain-ids` / `--ignore-server-ids`** (10.9+, MDEV-20119) — domain/server filtering when extracting binlog events.
- **Multi-source replication CHANNEL syntax** (10.7+, MDEV-26307) — MySQL-style `FOR CHANNEL 'name'` clauses now work in `CHANGE MASTER TO`, `START SLAVE`, etc.

### Replication Improvements in 11.4 LTS

- **Global limit on binary log disk space** (11.4+, MDEV-31404) — `max_binlog_total_size` (alias `binlog_space_limit`, default `0` = no limit) triggers binlog purging when the total size of all binlogs exceeds the threshold. Combine with `--slave-connections-needed-for-purge` (default `1`) so purging won't run if a configured replica is disconnected. New status variable `binlog_disk_use` reports current disk usage.
- **GTID index for the binary log** (11.4+, MDEV-4991) — a new GTID-to-position index lets reconnecting replicas seek straight to their start position without scanning whole binlog files. Controlled by `binlog_gtid_index` (default `ON`), `binlog_gtid_index_page_size`, and `binlog_gtid_index_span_min`. Status variables `binlog_gtid_index_hit` / `binlog_gtid_index_miss` let you confirm it's being used.
- **`SQL_BEFORE_GTIDS` / `SQL_AFTER_GTIDS` for `START SLAVE UNTIL`** (11.4+, MDEV-27247) — finer-grained stopping for staged failover or PITR replay.
- **Detailed replication-lag fields** (11.4+, MDEV-29639) — `SHOW REPLICA STATUS` adds `Master_last_event_time`, `Slave_last_event_time`, `Master_Slave_time_diff` for clearer lag interpretation than `Seconds_Behind_Master` alone (the 11.6 update built on this — see below).

### Binlog Performance Improvements in 11.7

- **Large-transaction commit no longer freezes other transactions** (11.7+, MDEV-32014) — previously, committing a very large transaction while `log_bin` was on would stall all other transactions until the binlog write completed. This bottleneck is gone.
- **Async rollback of prepared transactions during binlog crash recovery** (11.7+, MDEV-33853) — faster startup after a crash with many prepared transactions.
- **`slave_abort_blocking_timeout`** (11.7+, MDEV-34857) — kill long-running queries on a replica when they block replication progress past a threshold. Useful on read replicas that occasionally run long analytical queries.

### Monitoring Replication Lag

```sql
SHOW SLAVE STATUS\G
-- Key fields:
-- Seconds_Behind_Master: estimated lag in seconds
-- Last_SQL_Error: last error stopping the SQL thread
-- Relay_Log_Pos vs Read_Master_Log_Pos: how far behind the relay log is
```

Alert when `Seconds_Behind_Master > 5` for latency-sensitive applications. A value of `NULL` means replication is not running. Note: `Seconds_Behind_Master` can be misleading on idle primaries — use heartbeat tools (e.g., `pt-heartbeat`) for accurate measurement.

Since MariaDB 11.6 (MDEV-33856), the definition of `Seconds_Behind_Master` was refined and three new columns were added to `SHOW ALL REPLICAS STATUS` plus a new Information Schema `SLAVE_STATUS` table, providing more nuanced lag visibility (e.g., separate measurements for IO vs SQL thread lag).

## Semi-Synchronous Replication

The primary writes and fsyncs each transaction to its **own** binary log first — making it durable locally — and only then waits for at least one replica to acknowledge that it has *received* the transaction before reporting the commit complete to the client. The `rpl_semi_sync_master_wait_point` setting controls when that wait happens: with `AFTER_SYNC` the primary waits before the changes become visible, so failover to an acknowledged replica is lossless; with `AFTER_COMMIT` (the MariaDB default) the transaction is already committed and visible before the wait, so a crash in that window can still lose it on failover. See [Semisynchronous Replication](https://mariadb.com/docs/server/ha-and-performance/standard-replication/semisynchronous-replication).

```sql
-- Enable on primary:
SET GLOBAL rpl_semi_sync_master_enabled = 1;

-- Enable on replica:
SET GLOBAL rpl_semi_sync_slave_enabled = 1;
```

If no replica acknowledges within `rpl_semi_sync_master_timeout` (default 10 seconds), the primary falls back to async. Built-in since MariaDB 10.3 — no plugin needed.

Use when: you want at least one replica to have *received* each transaction before the client's commit returns — e.g. to bound failover data loss (use `AFTER_SYNC`). Note that semi-sync only delays commit completion as seen by the client; it does **not** add durability to the primary's own copy, and a transaction lost before any replica receives it is gone regardless of semi-sync.

## Galera Cluster

Multi-primary synchronous replication — all nodes accept reads and writes, changes are certified across the cluster before committing. No single point of failure. Built into MariaDB.

> **Packaging change (12.3+):** The Galera library is no longer included as a server-package dependency or in the MariaDB repositories by default (MDEV-38744). On 12.3+ you must install `galera-4` (or your distro's equivalent) separately when setting up a Galera node. The MariaDB server still understands Galera natively — only the library distribution changed.

### Developer Constraints

These will break in Galera if you're not aware of them:

**All tables must have a primary key:**
```sql
-- ✗ DELETE fails in Galera on keyless tables:
CREATE TABLE logs (message TEXT);

-- ✅ Always define a PK:
CREATE TABLE logs (id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, message TEXT);
```

**AUTO_INCREMENT values have gaps** — Galera uses `auto_increment_increment` and `auto_increment_offset` per node to avoid conflicts, resulting in non-sequential IDs. Never rely on sequential auto-increment in Galera.

**LOCK TABLES, GET_LOCK(), and `FLUSH TABLES {table list} WITH READ LOCK` are not supported** — use transactions. Note: global `FLUSH TABLES WITH READ LOCK` (no table list) IS supported:
```sql
-- ✗ Not supported in Galera:
LOCK TABLES orders WRITE;
-- ✅ Use a transaction instead:
BEGIN;
SELECT ... FOR UPDATE;
UPDATE ...;
COMMIT;
```

**InnoDB only** — Galera replicates only InnoDB tables. MyISAM has experimental support via `wsrep_replicate_myisam` but is not recommended for production.

**Transaction size limits** — default caps: 128K rows (`wsrep_max_ws_rows`) and 2GB (`wsrep_max_ws_size`). Extremely large transactions degrade cluster performance significantly and may require config tuning. Note: in MariaDB 13.0+, the default `binlog_row_event_max_size` is 64 KB (up from older 8 KB) — relevant when sizing replication events for write-heavy workloads.

**Binary log must be ROW format** — do not change `binlog_format` at runtime in a Galera cluster.

**Write-set retry on conflict** (12.1+) — `wsrep_applier_retry_count` controls how many times an applier retries a write set before erroring out. Tune this if your workload sees transient certification conflicts on busy clusters.

**Automatic SST user account management** (11.6+, MDEV-31809) — Galera now manages the dedicated SST (State Snapshot Transfer) user account automatically; you no longer have to create and grant it manually on every node.

**IP allowlist for nodes joining the cluster** (10.10+, MDEV-27246) — `wsrep_allowlist` restricts which IPs can make SST/IST requests, reducing the attack surface on a Galera cluster's intra-node traffic.

**Query cache:** The query cache was removed in later MariaDB versions and was not required in Galera since MariaDB 10.1.2. No action needed on modern installations.

### Stale Reads and Consistency

Galera is "virtually synchronous" — a committed write on one node may not be immediately visible on another without additional synchronization:

```sql
-- Force a sync point before reading (performance cost):
SET SESSION wsrep_sync_wait = 1;
SELECT * FROM orders WHERE id = 42;
```

Use `wsrep_sync_wait` only where strict read-after-write consistency is required. For most reads, eventual consistency across nodes (milliseconds) is acceptable.

### Lost Updates in Galera

Galera does not prevent lost updates in read-modify-write patterns. Use `SELECT ... FOR UPDATE` explicitly:

```sql
-- ✗ Race condition — another node could modify between SELECT and UPDATE:
SELECT balance FROM accounts WHERE id = 1;
-- ... application logic ...
UPDATE accounts SET balance = new_value WHERE id = 1;

-- ✅ Lock the row at read time:
BEGIN;
SELECT balance FROM accounts WHERE id = 1 FOR UPDATE;
UPDATE accounts SET balance = new_value WHERE id = 1;
COMMIT;
```

## Replication is Not a Backup

A `DROP TABLE` or `DELETE FROM table` on the primary replicates to all replicas immediately. Replication protects against hardware failure — not against accidental data changes. Maintain independent backups (`mariadb-dump`, `mariadb-backup`) on a schedule separate from replication.

Delayed replication is one mitigation — intentionally lag a replica by a set time:
```sql
CHANGE MASTER TO MASTER_DELAY = 3600;  -- 1 hour lag
```

This gives a recovery window for accidental changes, but it is still not a substitute for backups.

**Point-in-time recovery (new in 13.0)** — the `innodb_log_archive` variable ([MDEV-37949](https://mariadb.com/docs/release-notes/community-server/13.0/mariadb-13.0-changes-and-improvements)) makes InnoDB preserve the write-ahead log as a continuous sequence of files instead of overwriting the circular redo log. Combined with a base backup, this is intended to enable PITR and incremental backups without relying on the binary log alone. It is **new in the 13.0 rolling release** (not on the 11.8 LTS baseline) — treat it as recent and validate before depending on it for recovery.

## Failover Tools

- **Built-in (no proxy needed)** — Galera failover is automatic: any surviving node keeps accepting writes. For async/GTID replication, MariaDB does controlled primary switchover natively via GTID and `MASTER_DEMOTE_TO_SLAVE` (see [Standard Async Replication](#standard-async-replication) above), typically driven by your own scripts or an orchestrator.
- **ProxySQL** — open-source (GPLv3) proxy, widely used for read-write splitting and connection pooling with MariaDB.
- **MaxScale** — MariaDB Corporation's proxy with automatic failover, read-write splitting, and connection routing (detects primary failure and promotes the most up-to-date replica; requires GTID replication). **Not open source** — recent versions (25.01+) are closed-source commercial, and earlier versions used the source-available Business Source License (BSL), not an OSI open-source license. [mariadb.com/docs/maxscale](https://mariadb.com/docs/maxscale)

For basic Galera HA no proxy is required; ProxySQL or MaxScale add connection routing.

## Sources

- [Replication Overview — MariaDB Docs](https://mariadb.com/docs/server/ha-and-performance/standard-replication/replication-overview)
- [GTID — MariaDB Docs](https://mariadb.com/docs/server/ha-and-performance/standard-replication/gtid)
- [Parallel Replication — MariaDB Docs](https://mariadb.com/docs/server/ha-and-performance/standard-replication/parallel-replication)
- [Semi-Synchronous Replication — MariaDB Docs](https://mariadb.com/docs/server/ha-and-performance/standard-replication/semisynchronous-replication)
- [Galera Cluster Known Limitations — MariaDB Docs](https://mariadb.com/docs/galera-cluster/reference/mariadb-galera-cluster-known-limitations)
- [Auto-Increments in Galera — mariadb.org](https://mariadb.org/auto-increments-in-galera/)
- [Automatic Failover with MariaDB Monitor — MaxScale Docs](https://mariadb.com/docs/maxscale/mariadb-maxscale-tutorials/automatic-failover-with-mariadb-monitor)

*For topics not covered here, see the official MariaDB documentation at [mariadb.com/docs](https://mariadb.com/docs).*
