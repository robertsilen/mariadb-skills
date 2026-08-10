# Vibe DBA

*Last updated: 2026-08-10*

**Audit a running MariaDB server.** Collect data from it, turn that into a report you
can read, and have an AI agent add analysis on top.

The other eight skills in this repository are *briefings* — short documents that
correct what AI agents get wrong about MariaDB. They are knowledge, and they never
touch a database. Vibe DBA is the first of a different kind: it points at a real
server and reports on what it finds.

```
1. COLLECT  ──►  2. REPORT  ──►  3. ANALYSE
   Go binaries      Python           SKILL.md
   deterministic    deterministic    ~10% of the output
```

## What you get

A single self-contained HTML file: nine sections plus three appendices, charts drawn
as inline SVG, light by default with a dark toggle, and print rules so it exports to
PDF cleanly.

| Section | Contents |
|---|---|
| 1. Executive summary | Overview, workload profile |
| 2. Server identity | Version, uptime, CPU, RAM, disk |
| 3. InnoDB | Buffer pool, durability, checkpoint health |
| 4. Connections and threading | Limits, peaks, aborted connections |
| 5. Query performance | Counters as rates, sampled charts, slow log, digests |
| 6. Schema | Sizes, largest tables, missing primary and secondary indexes, auto-increment headroom |
| 7. Security | Findings ranked CRITICAL to LOW, accounts and grants |
| 8. MariaDB features | What the server uses, and what it could use and doesn't |
| 9. Replication | Replica status, binary log |
| Appendix A, B, C | Raw configuration · Methodology · Credits |

Section 8 is the part no generic MySQL tool produces: an inventory of MariaDB
capabilities — system-versioned tables, sequences, `VECTOR` columns, `INET6`, invisible
columns, `CHECK` constraints — split into *in use* and *available but not in use*.

## Four design decisions

These are why this is not another tuning script.

**Time analysis, not snapshots.** A status counter read once is a lifetime total
divided by an unknown uptime, and it supports almost no conclusion. The collector
samples over a window, rates are computed from the real elapsed time between samples,
and charts are plotted against wall-clock time so a spike lines up with something you
remember happening. If the window was too short or too idle, the report says so rather
than quietly implying health.

**The collector runs where the AI is not.** It is a static binary with no
dependencies, no network access and no AI involvement. It runs on the database server;
the collected files are then moved to wherever you want to analyse them. Production
database servers have no outbound internet and no API keys, and nobody grants an agent
a connection to one — so the agent never touches the server. It reads a file.

**Roughly 90% of the report is mechanical.** Everything is generated deterministically
from collected data unless it carries an `AI ANALYSIS` or `DBA NOTE` label. Marked
blocks are additions: remove every one and the report still stands as a complete
document. The agent may reorder sections and add commentary, but never changes a
number, a table or a chart.

**Nothing is stated more confidently than the data supports.** Lifetime counters are
labelled as lifetime. Data that could not be collected — Performance Schema switched
off, no metrics window, operating-system collectors unavailable — produces an explicit
note, never a silent gap.

## Using it with an AI agent

The skill is `SKILL.md` in this directory. It runs the whole process: asks what you
need, chooses a collection window, runs the collectors, generates the report, writes
its analysis, and hands you the file.

Unlike the other skills here, **this one needs the collector alongside it**, so install
the whole directory rather than the single file. A symlink is easiest and means edits
take effect immediately:

```sh
ln -s "$(pwd)/vibe-dba" ~/.claude/skills/vibe-dba     # from the repository root
```

Other agents read different locations — OpenAI Codex uses `~/.agents/skills/`.

Then ask for what you want:

```
claude "check the health of my mariadb"
claude "my site is slow every Monday at 5pm, I think it is the database"
```

Expect a couple of questions before it collects. **The collection window decides how
useful the report is**, and a badly timed run cannot be salvaged afterwards — so if
your problem happens on Monday afternoon, it should tell you to collect on Monday
afternoon, even if that means waiting.

It will not query your database directly, guess from the schema, or produce an
assessment without collected data. No collection, no audit.

## Running it yourself

No agent required. Needs Go 1.24+ to build, Python 3.9+ to report (standard library
only), and a MariaDB user with read access.

```sh
cd collector
go build ./cmd/mariadb-envcollect ; go build ./cmd/mariadb-metrics

./mariadb-envcollect -out /tmp/collect/env -package=false -cleanup=false
./mariadb-metrics    -out /tmp/collect     -duration 10m -package=false -cleanup=false

python3 scripts/mariadb-report.py /tmp/collect -o report.html
```

The metrics run blocks for its full duration. The report script takes the parent
directory and finds both collections underneath it. Add `--text` for a plain-text
version alongside the HTML.

**On the server, analysed elsewhere** — the realistic case:

```sh
# on the database server
./mariadb-metrics -out /tmp -duration 10m -package -cleanup

# on your machine
scp server:/tmp/*_metrics_*.tgz .
python3 scripts/mariadb-report.py *_metrics_*.tgz -o report.html
```

`-package` produces a `.tgz`; the report script reads it directly. Cross-compile the
collector for the target with `GOOS=linux GOARCH=amd64 go build ./cmd/mariadb-metrics`.

**No shell access on the server** — a managed instance, for example — collect over the
network with `-rds`, which skips the operating-system data that only works locally:

```sh
./mariadb-metrics -rds -duration 10m -mariadb-host db.example.com -mariadb-user collector
```

The collectors shell out to the `mariadb` client, so they pick up your usual
configuration. Override with `-mariadb-conn`, `-mariadb-defaults-file`, or
`-mariadb-host` / `-mariadb-user` / `-mariadb-password`; the matching `MARIADB_*`
environment variables work too. Run `-h` for the full list.

## What is collected

**Read-only throughout**: `SELECT` and `SHOW` statements, plus reads of configuration
and system files. Nothing is written and no setting is changed. Appendix B of every
report states this, and the full query set is in the collector source.

**Snapshot** — version and variables, global and InnoDB status, engines and plugins,
replication status, schema structure (`mariadb-dump --no-data`), configuration files,
sizes by database and table, tables missing primary or secondary indexes,
auto-increment headroom, MariaDB feature usage including vector index definitions,
accounts and grants, and hardware identity (CPU, cores, RAM, disk — Linux and macOS).

**Sampled over the window** — global status and InnoDB metrics every second, InnoDB
status and processlist every minute, plus `vmstat`, `mpstat`, `iostat` and
`/proc/diskstats` on Linux.

Output is plain text, one file per query, and gzipped streams with `#TS` timestamp
markers. Readable without this tool.

## Who wrote what

Everything in the report is mechanically generated unless labelled `AI ANALYSIS` or
`DBA NOTE`.

Annotations are a separate JSON file merged at render time, never edited into the
HTML. So the agent contributes structured data rather than markup, regenerating the
report never loses the notes, and a human can add their own through the same path:

```sh
python3 scripts/mariadb-report.py /tmp/collect --annotations notes.json -o report.html
```

```json
{"annotations": [
  {"section": "schema", "source": "human", "author": "Robert Silén",
   "timestamp": "2026-08-10", "body": "load_test is synthetic. Ignore it in sizing."}
]}
```

`section` is one of `summary`, `identity`, `innodb`, `connections`, `performance`,
`schema`, `security`, `features`, `replication`. `source` is `ai` or `human`.

## Status and known gaps

Working end to end. The collector and report are stable; the analysis is a first
version and is where the remaining work is.

- **The analysis is new.** It drives the process reliably and its findings have been
  useful in testing, but its depth is the next thing to improve.
- **macOS** — hardware identity is collected, but the sampled operating-system metrics
  (`vmstat`, `mpstat`, `iostat`, `/proc/diskstats`) are Linux-only. MariaDB collection
  is unaffected.
- **Distribution** — the collector currently needs Go to build. Prebuilt binaries as
  release assets would remove that.

## Credits

Developed by [@robertsilen](https://github.com/robertsilen) based on DBA skills by
[@lefred](https://github.com/lefred) and an idea by
[@kajarnocom](https://github.com/kajarnocom).

The collector was written by Frédéric "lefred" Descamps, who also shaped the approach:
in particular that a database audit must be built on measurement over time rather than
on a single snapshot.

Install:

```sh
git clone https://github.com/MariaDB/skills.git
cd skills && claude "dba"
```
