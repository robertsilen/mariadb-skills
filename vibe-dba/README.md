# Vibe DBA

*Last updated: 2026-08-08*

**Work in progress.** Collect data from a MariaDB server and turn it into a report
you can read.

The other skills in this repository are briefings — they tell an AI agent what it
gets wrong about MariaDB. Vibe DBA is different: it runs against a real server.

| Layer | What it does | Status |
|---|---|---|
| **1. Collect** | Two Go binaries: a snapshot of the server, and metrics sampled over time | working |
| **2. Report** | A Python script turns the collected data into an HTML report | working |
| **3. Analyse** | `SKILL.md` — an AI agent reads the report and adds analysis | first version |

**No AI is involved in layers 1 and 2.** The collector runs on the database server;
the collected files are moved to wherever you want to analyse them. You can run both
and read the result yourself.

## Using it with an AI agent

The skill is `SKILL.md` in this directory. It drives the whole process — asks what
you need, picks a collection window, runs the collectors, generates the report, adds
its analysis, and hands you the file.

Unlike the other skills in this repository, **this one needs the collector next to
it**, so install the whole directory rather than the single file. A symlink is the
easiest way, and means edits take effect immediately:

```sh
ln -s "$(pwd)/vibe-dba" ~/.claude/skills/vibe-dba     # run from the repository root
```

Other agents use different locations — OpenAI Codex reads `~/.agents/skills/`.

Then start the agent from anywhere and ask for what you want:

```
claude "check my mariadb database"
claude "my site is slow every Monday at 5pm, I think it is the database"
claude "dba"
```

Expect it to ask a couple of questions before collecting — when the database is
busy, whether something specific is wrong. That is deliberate: **the collection
window decides how useful the report is**, and a badly timed run cannot be salvaged
afterwards. If your problem happens on Monday afternoon, the skill should tell you
to collect on Monday afternoon, even if that means waiting.

What it will not do: query your database directly, guess from the schema, or produce
an assessment without collected data. Without a collection there is no audit.

To run the tools yourself with no agent involved, carry on below.

## Quick start

Needs Go 1.24+ to build, Python 3.9+ to report (standard library only), and a
MariaDB user with read access.

```sh
cd collector
go build ./cmd/mariadb-envcollect ; go build ./cmd/mariadb-metrics

./mariadb-envcollect -out /tmp/collect/env -package=false -cleanup=false
./mariadb-metrics    -out /tmp/collect     -duration 5m -package=false -cleanup=false

python3 scripts/mariadb-report.py /tmp/collect -o report.html
```

The metrics run blocks for its full duration. Add `--text` to also get a plain-text
version alongside the HTML, for reading in a terminal. The report script takes the parent
directory and finds both collections underneath it.

## Collecting

**`mariadb-envcollect`** takes a one-off snapshot: version, configuration, schema,
accounts, sizes. Seconds to run.

**`mariadb-metrics`** samples counters over a window. This is the one that matters.

> **Collect across the period you care about.** A counter read once tells you almost
> nothing, and a quiet window tells you nothing about a busy one. If the problem
> happens Monday at 17:00, collect Monday 16:30 to 17:30. Under five minutes and the
> report will say so rather than pretend otherwise.

The collectors shell out to the `mariadb` client, so they pick up your usual
configuration. Point them somewhere else with `-mariadb-conn`,
`-mariadb-defaults-file`, or `-mariadb-host` / `-mariadb-user` / `-mariadb-password`;
the matching `MARIADB_*` environment variables work too. Run `-h` for the full list.

Collecting over the network rather than on the server — a managed instance, or no
shell access — add `-rds` to skip the operating-system data that only works locally:

```sh
./mariadb-metrics -rds -duration 10m -mariadb-host db.example.com -mariadb-user collector
```

Add `-package` to bundle the output into a `.tgz` for transfer. The report script
reads a `.tgz` directly, so a real two-machine run is:

```sh
# on the server
./mariadb-metrics -out /tmp -duration 10m -package -cleanup
# on your laptop
scp server:/tmp/*_metrics_*.tgz .
python3 scripts/mariadb-report.py *_metrics_*.tgz -o report.html
```

## Reading the report

A single self-contained HTML file — nine sections plus appendices, charts drawn as
inline SVG, nothing loaded from the network except the logo.

- **Light by default**, with a dark mode button. Your choice is remembered.
- **Print or save as PDF** and it always comes out light, with sections, tables and
  charts kept whole across page breaks.
- **Charts use real clock time** from the collector's timestamps, so you can line a
  spike up against something you remember happening. Counter rates are computed from
  the actual elapsed time between samples, which keeps them honest when sampling
  slips on a loaded server.
- **Every number is measured, not inferred.** Where something could not be collected
  — Performance Schema switched off, no metrics window — the report says so instead
  of leaving a gap.

### Who wrote what

Everything is mechanically generated unless it is labelled `AI ANALYSIS` or
`DBA NOTE`. Marked blocks are additions on top of the measured data: remove them and
the report still stands.

Annotations live in a separate JSON file rather than being edited into the HTML, so
regenerating the report never loses them:

```sh
python3 scripts/mariadb-report.py /tmp/collect --annotations notes.json -o report.html
```

```json
{"annotations": [
  {"section": "schema", "source": "human", "author": "Robert Silén",
   "timestamp": "2026-08-08", "body": "load_test is synthetic. Ignore it in sizing."}
]}
```

`section` is one of `summary`, `identity`, `innodb`, `connections`, `performance`,
`schema`, `security`, `features`, `replication`. `source` is `ai` or `human`.

## What is collected

Read-only throughout: `SELECT` and `SHOW` statements, plus reads of configuration
and system files. Nothing is written and no setting is changed.

**Snapshot** — version and variables, global status, InnoDB status, engines and
plugins, replication status, schema structure (`mariadb-dump --no-data`),
configuration files, sizes by database and table, tables missing primary or
secondary indexes, auto-increment headroom, MariaDB feature usage, accounts and
grants, and hardware identity (CPU, cores, RAM, disk — on Linux and macOS).

**Sampled over the window** — global status and InnoDB metrics every second, InnoDB
status and processlist every minute, plus `vmstat`, `mpstat`, `iostat` and
`/proc/diskstats` on Linux.

Output is plain text, one file per query, and gzipped streams with `#TS` timestamp
markers. Readable without this tool.

## Known gaps

- **The analysis is a first version.** The skill drives the process reliably; the
  depth of its judgement is the next thing to work on.
- **macOS** — hardware identity (CPU, cores, RAM, disk) is collected, but the
  sampled OS metrics (`vmstat`, `mpstat`, `iostat`, `/proc/diskstats`) are Linux-only
  and produce nothing on a Mac. MariaDB collection is unaffected.

## Credits

Developed by [@robertsilen](https://github.com/robertsilen) based on DBA skills by
[@lefred](https://github.com/lefred) and an idea by
[@kajarnocom](https://github.com/kajarnocom).

Install:

```sh
git clone https://github.com/MariaDB/skills.git
cd skills && claude "dba"
```
