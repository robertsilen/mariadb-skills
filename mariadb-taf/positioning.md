# TAF: positioning and messaging

*Last updated: 2026-10-14. Working draft, not part of the skill the agent reads.*

The source for posts (Reddit, forums, newsletters, talks) and for the skill descriptions. Each message uses the same shape as the [README](README.md): **your problem → how TAF helps → the template → the skill**.

## One line

> **TAF turns "I think it's faster" into a benchmark anyone can rerun.** It's an open-source framework from the MariaDB Foundation that installs the database versions you want to compare, runs standard workloads at every concurrency level, and archives the result together with the exact config that produced it.

## The core argument

Most database performance claims can't be reproduced. Someone ran sysbench once, on a laptop, with default settings, and posted one number. TAF's answer is that **the run is a file**. A properties file states the workload, the thread counts, the repetitions and the server config, and TAF archives it next to the results. Anyone can rerun your benchmark, or argue with it, by running the same file.

The agent skill adds the discipline: one variable at a time, repeated runs, the noise reported next to the difference, and a clear statement of what the test doesn't cover.

## By audience

| Audience | Problem they say out loud | What TAF gives them | Lead with |
|---|---|---|---|
| **DBA / SRE** | "Will the upgrade slow us down?" "Does this setting actually help?" | A/B runs with one variable changed, repeated, compared per concurrency level | `upgrade-check`, `config-change` |
| **Architect / capacity planner** | "How big a box do we need?" "Which engine for this workload?" | A concurrency sweep showing where scaling stops, and engine comparisons on the same host | `capacity-curve`, the engine comparisons |
| **Application developer** | "Should we use MariaDB, MySQL or Postgres?" "Is our DB choice the bottleneck?" | A fair cross-database comparison with matched configs on the same hardware, without vendor defaults | the cross-database properties |
| **Data / BI engineer** | "Why are reports slow after the upgrade?" | Per-query TPROC-H timings | `hammerdb_tproch` |
| **MariaDB contributor / packager** | "Did this commit regress?" | Build at a commit, run the MDEV case library, profile with perf and flamegraphs | `MariaDBCommitBuilder.sh`, `mdevs/` |

## Claims we can make, and claims we must not

| ✅ Say | ❌ Don't say |
|---|---|
| Open source (GPLv2+), maintained under the MariaDB Foundation | "Official benchmark" or "certified results". TAF's own docs say it does not certify results |
| Supports MariaDB, MySQL, and PostgreSQL (initial support) | That it supports any database. Each one needs a plugin, a SQL dialect and a client |
| HammerDB **TPROC-C** / **TPROC-H** | "TPC-C" / "TPC-H" / "tpmC". Those are audited TPC benchmarks, and these are derived from them |
| Shows how a change *moves* performance on given hardware | That it predicts your application's throughput |
| Runs on Linux, needs Perl 5.30+ | "Zero setup". The sysbench client is compiled, you supply database packages, and you need a dedicated host |
| Under active development | Stable or 1.0. There are no tagged releases yet |

## Sample Reddit post (r/mariadb, r/Database, r/dataengineering)

> **How do you check whether a MariaDB upgrade will slow you down?**
>
> We kept seeing benchmark claims that nobody could reproduce: one sysbench run, default config, a single number. So the MariaDB Foundation has been building TAF, an open-source framework where the benchmark *is a file*. You describe the workload, thread counts, repetitions and server config in a properties file. TAF installs both versions side by side, runs everything three times at each concurrency level, and archives the result together with the exact config.
>
> For an upgrade check, two files that differ in one line give you a chart of version A against version B, plus the run-to-run noise, so you can tell a real regression from jitter.
>
> There's also an agent skill (for Claude Code, Cursor, Codex and others) that sets this up for you: it asks what you're trying to decide, picks the template, estimates how long the run will take, and compares the results.
>
> - TAF: github.com/MariaDB/taf
> - Skill and templates: github.com/mariadb/skills/tree/main/mariadb-taf
>
> It's Linux-only, it needs a dedicated box, and it uses synthetic workloads (sysbench, HammerDB). What would you want it to test?

## Short versions, one per use case

- **Upgrade:** "Two properties files, one line different. Find out whether 11.8 is faster than 11.4 on *your* hardware, three runs per data point."
- **Config:** "Stop cargo-culting my.cnf. Change one setting, run it three times, and see whether the difference beats the noise."
- **Capacity:** "Where does your server stop scaling? Sweep from 1 to 256 clients and find the knee."
- **Engines:** "InnoDB vs MyRocks on your read/write mix, on the same host, each with its own tuned config."
- **Cross-database:** "MariaDB vs MySQL vs Postgres with matched configs. No vendor defaults deciding the winner."
- **Regressions:** "Build any commit, run the MDEV case library, and get a flamegraph of where the time went."
