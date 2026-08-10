# Analysis reference

*Last updated: 2026-08-10*

Read this when writing the analysis in step 6 of `SKILL.md`. It covers how to weigh
a finding, what order to recommend fixes in, which correlations are worth attempting,
and which widely repeated advice is wrong on modern MariaDB.

Assume **11.8 LTS** unless the report says otherwise. The report states the actual
version — use it.

> **Read this file in full.** Do not `head` it; the constraints below are load-bearing.

## Hard limits — never conclude these

- **Nothing from a window the report called too short.** Say what you would need.
- **Nothing about query performance with Performance Schema off**, beyond what the
  aggregate counters support.
- **No OS or disk conclusion when the collection ran with `-rds`** or on macOS.
  Those collectors produce nothing, and their absence is not evidence of health.
- **Never a clean bill of health from a quiet window.** "Nothing alarming appeared in
  this 10-minute window, which was idle" is honest; "your database is healthy" is not.
- **No recommendation whose variable is missing from Appendix A.**

## Before recommending any variable change

**Check the variable exists in Appendix A of the report.** If it is absent from the
collected variables, it does not exist on that server, and recommending it makes the
whole audit look untrustworthy.

This matters more than usual here, because most published MySQL and MariaDB tuning
advice — including everything written before about 2020 — refers to variables that
modern MariaDB has removed.

### Advice that is dead on modern MariaDB

| Old advice | Reality |
|---|---|
| "Set `innodb_buffer_pool_instances` to 2–8 to reduce contention" | Deprecated and ignored from **10.5.1**, removed in **10.6.0**. The buffer pool is a single instance regardless of size. Never recommend it |
| "Convert tables to the Barracuda file format" | `innodb_file_format` no longer exists. Antelope/Barracuda is gone; row format is set per table |
| "Tune `innodb_log_files_in_group`" | Removed. There is one redo log |
| "Set `innodb_change_buffering=all`" | Default became `none` in **10.5.15**, deprecated **10.9**, removed in **11.0.0** |
| "Disable `innodb_adaptive_hash_index`" | Already `OFF` by default since **10.5**. Check before recommending |
| "Set `innodb_stats_on_metadata=OFF`" | Already `OFF` by default. Check before recommending |
| "Disable the query cache" | Still exists in MariaDB (unlike MySQL 8.0, which removed it), but `query_cache_type` already defaults to `OFF`. Only a finding if someone switched it on |

> Verify anything version-dependent against [mariadb.com/docs](https://mariadb.com/docs).
> The consulting material this tool draws on is MySQL 5.5/5.6-era and much of its
> tuning advice is now either default behaviour or impossible.

## Weighing a finding

Severity is not a property of the setting. It is:

**impact if it bites × likelihood it bites here × how much it costs to fix**

The same value is a different finding on different servers. `innodb_buffer_pool_size`
at 128 MB is critical on a 200 GB dataset serving production traffic, and irrelevant
on a 600 MB development database. **State the context you are judging against**, then
judge.

### Context that changes severity

| Signal in the report | Effect on judgement |
|---|---|
| Dataset size vs buffer pool size | The single most useful ratio. A pool larger than the data means most buffer-pool advice is moot |
| Peak connections vs `max_connections` | 4% used means connection tuning is noise; 90% means it is urgent |
| Uptime | A server up 3 days has counters that mean little. Say so |
| Read/write mix | Durability and redo-log findings matter far more on write-heavy systems |
| Window activity level | A quiet window cannot support a workload conclusion, whatever else you saw |
| Development vs production | Downgrade practical risk, but say the habit matters — that is how it reaches production |

Do not invent a severity scale for performance findings. The report already ranks
**security** findings as CRITICAL/HIGH/MEDIUM/LOW; reuse those, and for everything
else express priority through the order of your Next Steps.

## Order of recommendations

Some fixes make things worse in the wrong order. Getting the sequence right is a
large part of what the analysis is for.

**Known chains:**

1. **Buffer pool before redo log.** Enlarging `innodb_log_file_size` while the buffer
   pool stays small increases flushing rather than reducing it. Pool first, then log.
2. **Storage and filesystem before flush tuning.** `innodb_flush_neighbors=0` and
   raised `innodb_io_capacity` assume the storage can take it. On slow or shared
   disks they make things worse.
3. **Schema and indexes before memory tuning.** A table doing full scans because it
   lacks an index will consume any buffer pool you give it. Fix the scan, then size
   the cache — see `mariadb-query-optimization`.
4. **Performance Schema before query-level work.** Without it there is no statement
   profiling, so any claim about *which* queries are slow is guesswork. Enabling it
   needs `performance_schema=ON` in the configuration file and a **restart**.
5. **A verified backup before anything risky.** If the report shows no binary log and
   no evidence of backups, that comes before tuning.
6. **Version upgrade before features that need it.** Do not recommend `VECTOR`
   (11.7+) or `UUID` columns (10.10+) to a server on 10.6 without saying an upgrade
   comes first.

**Always state the cost:**

- Dynamic and reversible — `SET GLOBAL`, testable immediately, low risk. Prefer these.
- Needs a restart — `performance_schema`, `innodb_buffer_pool_size` on older
  versions, most `my.cnf` work. Say a maintenance window is required.
- Needs a rebuild or migration — row format changes, engine changes, adding indexes
  to large tables. Give the rough cost and point at online DDL options.

## Correlations worth attempting

This is the work no rule engine can do. The collection contains several artefacts
that nothing in the report joins together — look across them.

| Pattern | How to spot it |
|---|---|
| **A periodic spike caused by a scheduled job** | A metric spiking at regular intervals, matched against `crontabs` in the collection. A batch job every 30 minutes explains a great deal, and users often do not connect the two |
| **Slow queries caused by a missing index** | High `Select_scan` or `Handler_read_rnd_next` alongside an entry in *Tables with no secondary index* naming a large table |
| **Temp tables spilling to disk** | `Created_tmp_disk_tables` as a share of `Created_tmp_tables`, cross-checked against `tmp_table_size` and `max_heap_table_size` in Appendix A, and against the statement digests if Performance Schema is on |
| **Connection storms** | `Threads_connected` climbing in the charts while `Threads_running` stays high, plus `Aborted_connects` in section 4. If `skip_name_resolve` is OFF, slow reverse DNS is a candidate cause |
| **A cache too small for the data** | Dataset size from section 6 against buffer pool size from section 3, plus the read hit ratio and buffer pool reads in the charts |
| **Checkpoint pressure** | Redo log size against the write rate visible in the InnoDB IO chart |
| **Something restarted mid-window** | A counter series breaking into a gap — the report draws these as breaks rather than zeros |
| **An event the error log explains** | `error_log_tail` timestamps against a spike in the charts |

When a correlation is suggestive but not proven, **say so and ask**. "InnoDB lock
waits peak every 30 minutes, which lines up with the `backup.sh` entry in cron — is
that job writing to these tables?" is a better contribution than a confident
misattribution.

## Writing the Next Steps

A numbered, ordered plan. Each step:

1. **The action**, in the imperative.
2. **The command or setting**, exactly, so it can be copied.
3. **The section** holding the evidence.
4. **The cost** — dynamic, restart, or rebuild.

Order by dependency first, then by impact. If step 3 must follow step 1, say why.

Split what the user can do now from what needs a dedicated piece of work — a query
optimisation pass, a schema redesign, a version upgrade — and point at the relevant
skill rather than attempting it inline.

### Worked shape

> **The dominant problem is schema design, not configuration.** Two tables over
> 100 MB carry only a primary key, and the full-scan counters track their access
> pattern. Configuration changes will not fix that.
>
> 1. Add a secondary index to `app.events(created_at)` — section 6 shows 30,906 rows
>    with no secondary index, and section 5 shows `Select_scan` at 12/s during the
>    peak. Online DDL, no downtime; see `mariadb-query-optimization`.
> 2. **Then** raise `innodb_buffer_pool_size` to 4G (section 3: currently 128 MB
>    against 3.2 GB of data). Do this after step 1, or the pool will simply cache
>    scanned rows. Restart required.
> 3. Enable Performance Schema — `performance_schema=ON` plus restart — so the next
>    audit can say which queries are responsible rather than inferring it.
>
> **What is fine:** durability is fully ACID with `innodb_flush_log_at_trx_commit=1`
> and `sync_binlog=1`, the binary log is enabled, and no account is reachable from a
> non-local host. Leave these alone.

## A note on the source material

The consulting toolchain this project draws on encoded almost no thresholds. Its
report modules read a value, print a table, and select a pre-written paragraph by
exact match — `innodb_flush_log_at_trx_commit == 1` picks one text, `== 2` another.
The judgement about whether that value was *a problem on this server* was never in
the code. It lived in the consultant's head and appears only in the finished audits.

So there is no inherited rule set to copy here. The thresholds above come from the
collected data itself, from MariaDB's documented defaults, and from reasoning about
the specific server. Treat any number in this file as a starting point to be argued
with, not a constant.
