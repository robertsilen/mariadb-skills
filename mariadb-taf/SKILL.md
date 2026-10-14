---
name: mariadb-taf
description: "Run reproducible MariaDB benchmarks with TAF, the MariaDB Foundation's Test Automation Framework. Use when the user wants to measure rather than guess: 'will upgrading MariaDB make us faster or slower', 'does this my.cnf change help', 'how many concurrent connections can this box take', 'InnoDB vs MyRocks for our writes', 'MariaDB vs MySQL vs PostgreSQL on the same hardware', 'benchmark MariaDB', 'run sysbench', 'run HammerDB', 'TPROC-C', 'TPROC-H', 'reproduce a performance regression', 'did this commit regress', 'flamegraph of the server under load'. Produces a properties file, runs it, compares results. Do NOT use for health-checking an existing server (use vibe-dba) or for benchmarking a production server — TAF installs and drives its own server instance."
---

# MariaDB Benchmarking with TAF

*Last updated: 2026-10-14*

> **Requires:** a dedicated **Linux** host (the docs' examples are x86_64), Perl 5.30+, `cmake` and a C toolchain (TAF builds its sysbench client), the MariaDB packages to test (bintar `.tar.gz` or RPMs), and a few GB of disk for each install and data set. Optional: `perf` for profiling, Java for the results backend.
>
> **Checked against:** [github.com/MariaDB/taf](https://github.com/MariaDB/taf) commit `95f15ee` (2026-10-09, `taf.pl` framework version 4). TAF has no tagged releases and moves fast. If a flag here fails, run `perl taf.pl --help` and trust that output over this file.
>
> **Templates:** `templates/` **next to this file** has one properties file per use case. Find that path before copying anything. Do not assume the current working directory.

TAF is a Perl framework that installs a database, initializes and starts it, loads a standard workload, runs it at several thread counts and iterations, and archives the results with the exact configuration that produced them. The unit of work is a **properties file**. A run is reproducible because the file *is* the run.

This skill helps someone **run** TAF to answer one concrete question. Writing new TAF plugins is out of scope; point contributors to the repo.

## What Agents Get Wrong

| Pattern | What to do instead |
|---|---|
| A hand-written `sysbench ... run` loop, with numbers pasted into chat | A TAF properties file. Anyone can rerun it, and TAF archives it with the results |
| One run, one thread count, then "X is 12% faster" | `taf.iterations` ≥ 3 and a thread sweep. Report the spread across iterations, not just the mean. A difference smaller than the spread is noise |
| Changing the version **and** the config **and** the dataset between two runs | Change **exactly one** variable. Keep two properties files that are identical except for that variable, and give each a distinct `taf.test_case_tag` |
| Comparing versions with each version's default config | Pin the config with an inline `[db_config_start]…[db_config_end]` block, used unchanged on both sides. Defaults change between versions: `innodb_snapshot_isolation` became `ON` by default in 11.6.2, for example |
| Reusing one my.cnf across an upgrade comparison without checking it | Every variable must exist on **both** versions, or the older server will not start. `innodb_snapshot_isolation` only exists from 10.6.18 / 10.11.8 / 11.4.2. TAF's own `mariadb_simple_2gbp.cnf` sets it |
| Pointing TAF at a production server or a shared host | TAF installs, initializes, starts and stops **its own** instance and saturates the host. Use a dedicated machine with nothing else running |
| A 10,000-row dataset, then conclusions about disk I/O | Size `sysbench_lua.number_of_tables` × `number_of_rows` against `innodb_buffer_pool_size`. Say whether the test is in-memory or I/O-bound, because that changes what it measures |
| Assuming `taf.duration` is always seconds | sysbench-lua uses **seconds**, HammerDB TPROC-C uses **minutes**. `taf.duration=300` for TPROC-C is five hours per iteration |
| Editing `properties/default/*.properties` | Never edit the defaults. Override in your own file; the defaults are the shared baseline |
| SSL settings in the database config | TAF exits immediately if the config contains SSL directives. Use `taf.db_ssl_*` / `--db-ssl-*` |
| Calling HammerDB TPROC-C results "TPC-C" or quoting them as tpmC | TPROC-C is *derived from* TPC-C. It is not an audited TPC result. Say "HammerDB TPROC-C NOPM" |
| Running the profiler in only one of the two runs being compared | Profiling adds overhead. Either profile both sides with identical `taf.profiler_*` settings, or keep the throughput runs and the profile runs separate |
| Reading `results/` while a run is still going | Each finished run is archived under `archive/`, in a directory named after the host, test and timestamp. Read it there |

## Step 1 — Pin down the question

Ask before writing anything. A benchmark without a question produces numbers nobody can act on.

1. **What decision does this inform?** Map it to a use case below. If the honest answer is "I want to know whether my server is healthy", stop and use `vibe-dba`.
2. **What is the one variable?** For example a version (11.4.8 → 11.8.3), a config option, a storage engine, a database product, or a host.
3. **What workload is closest to the real one?** Point lookups, mixed read/write OLTP, or analytics (see the workload table).
4. **What hardware?** It must be a dedicated Linux host. Get the cores, RAM and disk type, because they set the dataset size and thread counts.
5. **Where will it run?** If you have no shell on the benchmark host, write the properties file and the exact commands, and the user runs them. Results come back as the archived directory.

## Step 2 — Pick the use case and the template

| Use case | The question | Template (`templates/`) | Also in the TAF repo |
|---|---|---|---|
| **Upgrade check** | Will moving from version A to version B change throughput or latency? | `upgrade-check.properties` | `scripts/mariadb/mdevs/MariadbMdevReleaseTesting.sh` (same cases, run per release) |
| **Config change** | Does this my.cnf change help, hurt, or do nothing? | `config-change.properties` | `properties/mariadb/mdevs/MDEV32750_AHI_ON.properties` / `_AHI_OFF` (an A/B on one option) |
| **Capacity curve** | At how many concurrent clients does throughput stop scaling on this host? Is a bigger instance worth it? | `capacity-curve.properties` | `properties/mariadb/sysbench_lua.properties` |
| **Storage engine choice** | InnoDB or MyRocks (or another engine) for this read/write mix? | Start from `config-change.properties` | `properties/mariadb/sysbench_tidesdb10_innodb_rocks_compare.properties`, `database_config_files/mariadb/rocks_compare*.cnf` |
| **Analytics queries** | How do reporting-style queries perform, query by query? | — | `properties/mariadb/hammerdb_tproch.properties` |
| **Cross-database** | MariaDB vs MySQL vs PostgreSQL on the same hardware and workload | — | `properties/{mariadb,mysql,postgresql}/`, plus the paired `database_config_files/*/…_comparable_4vcpu_16gb.cnf` / `…_16vcpu_32gb.cnf` |
| **Regression hunt** | Did a commit or release regress performance? Reproduce a performance MDEV | — | `scripts/build/MariaDBCommitBuilder.sh`, `properties/mariadb/mdevs/`, `scripts/mariadb/mdevs/` |
| **Explain a difference** | *Why* is B slower? Where does the CPU go? | Any template, with `taf.profiler_*` | `help/TAF_Profiling_Lifecycle_Quick‑Start_Guide.pdf` |

### Workloads

| Suite (`taf.test_suite`) | Models | Duration unit | Typical `taf.tests` |
|---|---|---|---|
| `sysbench-lua` | OLTP micro-benchmarks | seconds | `POINT_SELECT`, `OLTP_RO`, `OLTP_RW`, `UPDATE_KEY`, `UPDATE_NO_KEY`, `SELECT_SIMPLE_RANGES` … (`--list-test-suites-tests` lists them all) |
| `hammerdb-tprocc` | Order-entry OLTP, derived from TPC-C | **minutes** | `tprocc`. Size with `hammerdb_tprocc.number_of_warehouses` |
| `hammerdb-tproch` | Analytics / decision support, derived from TPC-H | per query set | Size with `hammerdb_tproch.scale` |

## Step 3 — Set up TAF (once per host)

```bash
git clone https://github.com/MariaDB/taf.git
cd taf
perl -v                     # 5.30 or newer
perl taf.pl --version
perl taf.pl --list-test-suites-tests
```

The clone is large because it bundles HammerDB and a sysbench kit.

Install each MariaDB version under test into TAF's own directory. The bintar is the `linux-systemd-x86_64.tar.gz` package from [mariadb.org/download](https://mariadb.org/download/):

```bash
perl taf.pl --db-software-install \
  --db-software-install-packages=/path/to/mariadb-11.8.3-linux-systemd-x86_64.tar.gz
perl taf.pl --list-database-software-installs     # [ACTIVE] marks the one runs will use
```

**The active install** is what every run uses. Switch it explicitly before each side of a comparison, with `--db-software-install-dir=<path from the list>` or `--set-active-database-software-install`. Never assume which install is active; check with `--list-active-db-install`.

## Step 4 — Write the properties file

Copy the template into the TAF tree, for example `properties/local/<name>.properties`, and fill in every `CHANGE_ME`. Precedence, highest first: **command-line options → your properties file → `properties/default/`**. Within the database config, the order is **CLI → inline `[db_config_start]` block → `taf.db_config_file`**.

Prefer the **inline config block** over `taf.db_config_file`. TAF writes the resolved config into each archived result, so a reader never needs a separate .cnf to know what ran.

Before a long run, **estimate the runtime** and tell the user:

```
tests × thread counts × iterations × duration  (+ data load + warm-up per test)
```

For example, 3 tests × 6 thread counts × 3 iterations × 300 s ≈ 4.5 hours before setup. Cut the scope or confirm before starting anything over about an hour.

## Step 5 — Smoke test, then run

Always do a short smoke run first. It catches build, permission and config errors in minutes rather than hours:

```bash
perl taf.pl --properties-file=./properties/local/upgrade-check.properties \
  --action=init-start-db-build-client-run-tests \
  --tests=POINT_SELECT --threads=4 --iterations=1 --duration=20 --warmup-duration=10
```

`--prop=` works too, as an abbreviation. The first run on each install needs an action that **builds the client** (`…-build-client-…`), because sysbench-lua is compiled against the active install. After that, `init-start-db-run-tests` (fresh datadir) or `run-tests` (server already running) is enough.

For the full run, drop the overrides and let the properties file speak:

```bash
perl taf.pl --properties-file=./properties/local/upgrade-check.properties
```

Long runs should survive a dropped SSH session: run them under `tmux` or `nohup`. Exit code 0 means a clean run. The log is under `logs/`.

## Step 6 — Compare

Each run is archived under `archive/`, in a directory named after the host, test and timestamp, such as `archive/HOST_POINT_SELECT_2026_02_09_113915/`. This holds a `README.txt` with the autogenerated properties and the resolved db config, a `*.raw.txt` per result set if `taf_res_raw_text.pm` was in `taf.report_plugin`, and the HTML/text reports.

To overlay two or more runs in one HTML report:

```bash
find archive reports -name '*.raw.txt'
cd scripts          # the script loads ../libs, so run it from here
perl ResultsCompareRaw.pl ../<path-A>.raw.txt ../<path-B>.raw.txt ../reports/compare-A-vs-B
```

For TPROC-H use `ResultsCompareTprochRaw.pl`. For a single run, `ResultsReportRaw.pl <file>.raw.txt <out_dir>`.

## Step 7 — Report back

Report back in this order, and claim nothing beyond it:

1. **The question and the one variable**, plus the `test_case_tag`s of the runs.
2. **The result per thread count**: mean, plus the spread across iterations. Mark where the difference is within noise.
3. **Where the curves cross or flatten.** A change can help at 16 threads and hurt at 256.
4. **What the test does not cover**: the workload is synthetic, so this is not the user's application, and you should say whether the data set fit in memory. Results on this host do not transfer to other hardware.
5. **The artefacts**: the properties file(s) and archive paths, so the run can be repeated.

## Related skills

- `vibe-dba`: audits a server that is already running. TAF tests a change *before* it reaches that server.
- `mariadb-query-optimization`: once TAF shows *that* something is slower, use this to find *which query* and *why*.

## References

- [TAF project page](https://mariadb.org/projects/taf/), [github.com/MariaDB/taf](https://github.com/MariaDB/taf)
- In the repo: `help/taf_usage.txt` (every option), `TAF-PERL_QuickStart.pdf`, `help/TAF-PERL_Sysbench_QuickStart.pdf`, `help/TAF-PERL_HammerDB-TPROCC_QuickStart.pdf`, `help/TAF-PERL_DatabaseSoftwareInstallsQuickStart.pdf`
- [innodb_snapshot_isolation](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-system-variables): introduction versions and the 11.6.2 default change
