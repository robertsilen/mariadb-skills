# Vibe DBA

*Last updated: 2026-08-08*

**Work in progress.** Collect data from a MariaDB server and turn it into a report
you can read.

The other skills in this repository are briefings — they tell an AI agent what it
gets wrong about MariaDB. Vibe DBA is different: it runs against a real server and
reports on what it finds.

It has three layers, and today the first two exist:

| Layer | What it does | Status |
|---|---|---|
| **1. Collect** | Two Go binaries gather environment data and sample metrics over time | working |
| **2. Report** | A Python script turns sampled metrics into an HTML report with charts | partial — metrics only |
| **3. Analyse** | A `SKILL.md` for an AI agent to read the report and advise | not started |

The collector runs on (or against) the database server with no AI involved. The
collected data is then moved to wherever you want to analyse it. Nothing about
layers 1 and 2 requires an AI — you can run both and read the result yourself.

## Requirements

- Go 1.24+ to build the collector
- Python 3.9+ to generate the report (standard library only, no packages to install)
- A MariaDB user with read access

## Build

```sh
cd collector
go build ./cmd/mariadb-envcollect
go build ./cmd/mariadb-metrics
```

Two self-contained binaries with no runtime dependencies. Cross-compile for the
target server with `GOOS=linux GOARCH=amd64 go build ./cmd/mariadb-metrics`.

## Collect

**Environment** — a one-off snapshot of the server, its configuration, and the
schema. Static things that don't change minute to minute:

```sh
./mariadb-envcollect -out /tmp/collect
```

**Metrics** — samples counters over a window. This is the one that matters:

```sh
./mariadb-metrics -out /tmp/collect -duration 5m
```

> **Run it across the period you care about.** A counter read once tells you almost
> nothing, and a quiet window tells you nothing about a busy one. If the problem
> happens Monday at 17:00, collect Monday from 16:30 to 17:30.

Connection options — the collector shells out to the `mariadb` client, so it picks
up your usual configuration:

```sh
./mariadb-metrics -mariadb-conn mariadb://user:pass@host:3306
./mariadb-metrics -mariadb-defaults-file /root/.my.cnf
./mariadb-metrics -mariadb-host db01 -mariadb-user collector
```

Both commands also read `MARIADB_HOST`, `MARIADB_USER`, `MARIADB_PASSWORD`,
`MARIADB_SOCKET`, `MARIADB_DEFAULTS_FILE`, and `MARIADB_CONN`.

Running from your laptop instead of on the server? Add `-rds` to skip the
operating-system collection that only works locally:

```sh
./mariadb-metrics -rds -duration 5m -mariadb-host db.example.com -mariadb-user collector
```

Use `-package` to bundle the output into a `.tgz` for transfer.

## Report

```sh
python3 scripts/mariadb-metrics-report.py /tmp/collect/<host>_metrics_<timestamp>
```

Writes a self-contained HTML file — inline SVG charts, no JavaScript, no external
resources. It accepts either a collected directory or a `.tgz` package, and `-o`
sets the output path.

Charts are plotted against real clock time taken from the collector's timestamps,
so you can line a spike up against something you remember happening. Counters are
converted to per-second rates using the actual elapsed time between samples, which
keeps rates honest when sampling slips on a loaded server.

## What is collected

Everything is read-only. No `SET`, no writes, no schema changes.

**Environment:** server version and variables, global status, InnoDB status,
plugins and engines, replication status, schema structure (`mariadb-dump --no-data`),
configuration files, dataset and per-schema sizes, largest tables, tables without
primary keys, row formats, auto-increment usage, partitions, account grants,
duplicate indexes, and operating-system details.

**Metrics, sampled over the window:** global status (1s), InnoDB metrics (1s),
InnoDB status (60s), processlist (60s), plus `vmstat`, `mpstat`, `iostat`, and
`/proc/diskstats` on Linux.

The environment collector writes plain text files, one per query or command. The
metrics collector writes gzipped streams with `#TS` timestamp markers.

## Known gaps

- **Only metrics are reported.** The environment collection has no report generator
  yet, so the schema, configuration, and security data is collected but not
  rendered.
- **No findings.** The report draws charts; it doesn't tell you what is wrong.
- **No MariaDB feature inventory** — what the server could be using and isn't.
- **macOS** — the operating-system collectors are Linux-only, so `vmstat`, `mpstat`,
  and similar produce nothing when run on a Mac. MariaDB collection works fine.

## Credits

The collector was written by Frédéric "lefred" Descamps, who also shaped the
approach this skill is built on.
