# Vibe DBA

*Last updated: 2026-10-19*

**Audit a running MariaDB server.** Collect data from it, turn that into a report you
can read, and have an AI agent add analysis on top.

The other eight skills in this repository are *briefings* — short documents that
correct what AI agents get wrong about MariaDB. They are knowledge, and they never
touch a database. Vibe DBA is the first of a different kind: it points at a real
server and reports on what it finds.

```
1. COLLECT  ──►  2. REPORT  ──►  3. ANALYSE
   shell plugin     Python           SKILL.md
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

**The collector runs where the AI is not.** It is a MariaDB Shell plugin, with no
network access and no AI involvement. It runs on the database server;
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

No agent required. Needs a MariaDB user with read access,
[MariaDB Shell](https://github.com/mariadb-corporation/mariadb-shell) where the
collector runs, and Python 3.9+ to report (standard library only).

Link the plugin into the shell once, then collect through the shell's connection:

```sh
mkdir -p ~/.mariadb-shell/plugins
ln -s "$(pwd)/collector/shell-plugin/vibe_dba" ~/.mariadb-shell/plugins/vibe_dba

mariadb-shell collector@localhost -- vibeDba collect-environment --outputDir=/tmp/collect
mariadb-shell collector@localhost -- vibeDba collect-metrics --outputDir=/tmp/collect --duration=10m

python3 collector/scripts/mariadb-report.py /tmp/collect -o report.html
```

The metrics run blocks for its full duration. The report script takes the parent
directory and finds both collections underneath it. Add `--text` for a plain-text
version alongside the HTML. [`collector/README.md`](collector/README.md) lists every
option.

## Collecting from a remote server

The realistic case: the database is on a Linux server, and you want the report on your
own machine. The server needs MariaDB Shell, which brings its own Python; nothing else
is installed.

**1. Install the shell and the plugin on the server.** The shell installs per user into
`~/.local`, no root needed:

```sh
scp -r collector/shell-plugin/vibe_dba server:
ssh server
curl -fsSL https://github.com/mariadb-corporation/mariadb-shell/raw/main/install.sh | bash
mkdir -p ~/.mariadb-shell/plugins && ln -s ~/vibe_dba ~/.mariadb-shell/plugins/vibe_dba
```

**2. Collect.** Environment first — it takes seconds — then metrics over your window:

```sh
mariadb-shell collector@localhost -- vibeDba collect-environment --outputDir=/tmp/collect
mariadb-shell collector@localhost -- vibeDba collect-metrics --outputDir=/tmp/collect --duration=10m
```

Run both. Metrics alone gives you charts with none of the schema, configuration,
security or feature sections — which is where most findings come from. Each writes a
`.tgz` beside its directory.

Output is plain text files plus gzipped sample streams. Its size grows with the
length of the window and with the size and activity of the server: on a small test
server a 70-second metrics run packs to about 30 KB and the environment to about
40 KB, but the per-second samples grow with every minute collected. The per-minute
processlist and InnoDB status grow with the number of connections, and the schema
definitions with the number of tables. It has not yet been measured on a large, busy
server.

**3. Bring it back and report:**

```sh
scp 'server:/tmp/collect/*.tgz' collect/
python3 collector/scripts/mariadb-report.py collect -o report.html
```

**4. Clean up** — remove `~/vibe_dba`, the plugin link and `/tmp/collect` on the server,
and the shell too if it was installed only for this.

### The database user

The collector connects as whoever you tell it to and runs only `SELECT` and `SHOW`. A
dedicated read-only account is enough:

```sql
CREATE USER 'collector'@'localhost' IDENTIFIED BY '<password>';
GRANT SELECT, PROCESS, REPLICATION CLIENT, SLAVE MONITOR, SHOW VIEW, SHOW DATABASES
  ON *.* TO 'collector'@'localhost';
```

`PROCESS` is needed for `SHOW ENGINE INNODB STATUS` and the processlist,
`REPLICATION CLIENT` for `SHOW MASTER STATUS`, `SLAVE MONITOR` (10.5+) for
`SHOW SLAVE STATUS`, and `SHOW VIEW` for view definitions. Missing privileges leave
those sections reported as unavailable rather than failing the run.

Verified on 11.8: with exactly these grants the report is complete — schema, security,
features, InnoDB status and replication all present — except that triggers and events
are not visible to the account, so they are missing from the schema definitions.

The plugin uses the shell's connection, so anything `mariadb-shell` accepts works:
URIs, sockets, TLS options, `-p` to prompt, and the shell's credential store, which
keeps passwords out of your shell history.

### No shell access on the server

A managed instance, for example. Run the collector from your own machine against the
server over the network. The plugin notices the server is not local and skips the
operating-system data, which it could only read from the machine it runs on:

```sh
mariadb-shell collector@db.example.com -- vibeDba collect-environment --outputDir=/tmp/collect
mariadb-shell collector@db.example.com -- vibeDba collect-metrics --outputDir=/tmp/collect --duration=10m
```

Everything MariaDB-side is still collected; the report states which sections are
missing and why. Sampling across a network adds a little latency to each sample, which
the report accounts for — rates are computed from real elapsed time, not an assumed
interval.


## What is collected

**Read-only throughout**: `SELECT` and `SHOW` statements, plus reads of configuration
and system files. Nothing is written and no setting is changed. Appendix B of every
report states this, and the full query set is in the collector source.

**Snapshot** — version and variables, global and InnoDB status, engines and plugins,
replication status, schema structure (`SHOW CREATE` for every object), configuration files,
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
- **Distribution** — the plugin is linked into the shell by hand. Shipping it with
  MariaDB Shell, or through the shell's `plugins` repository mechanism, would remove
  that step.

## Credits

Developed by [@robertsilen](https://github.com/robertsilen) based on DBA skills by
[@lefred](https://github.com/lefred) and an idea by
[@kajarnocom](https://github.com/kajarnocom).

The collector was originally written in Go by Frédéric "lefred" Descamps, and ported to
a MariaDB Shell plugin. lefred also shaped the approach:
in particular that a database audit must be built on measurement over time rather than
on a single snapshot.

Install:

```sh
git clone https://github.com/MariaDB/skills.git
cd skills && claude "dba"
```
