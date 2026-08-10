---
name: vibe-dba
description: "Health check or audit a running MariaDB server. Use when the user wants their own server examined — 'health check my database', 'do a health check on my mariadb', 'check my database', 'is my MariaDB healthy', 'why is my server slow', 'audit this server', 'review my database', 'dba'. Collects data with a bundled collector, generates a mechanical report, then analyses it. Requires a real server to collect from; without collected data there is no audit. Do NOT use for general questions about MariaDB features, syntax, or capabilities — the other MariaDB skills cover those."
---

# Vibe DBA

*Last updated: 2026-08-10*

> **Requires:** a real MariaDB server the user can collect from, Go 1.24+ to build the collector (or a prebuilt binary), and Python 3.9+ to generate the report. Assume MariaDB **11.8 LTS** unless the server reports otherwise — the report states the actual version, so use that.
>
> **Where the tools live:** the `collector/` directory **next to this file**, wherever this skill is installed. Work out that path before running anything — do not assume the current working directory. `README.md` beside this file documents every flag; read it rather than guessing.

This skill produces an **audit of a specific server**, not advice about MariaDB in
general. It runs a three-layer pipeline:

```
1. COLLECT (Go binaries) → 2. REPORT (Python) → 3. ANALYSE (you)
   deterministic             deterministic         ~10% of the output
```

Layers 1 and 2 involve no AI. Your job is layer 3: read the generated report and
add correlation, prioritisation and judgement on top of it.

## What Agents Get Wrong

| Pattern | What to do instead |
|---|---|
| Running `SELECT`/`SHOW` against the database yourself — via MCP, a `mariadb` client, or any other connection | **Never query the server directly.** Run the collector. Direct queries throw away the time series, the reproducibility and the audit trail, and produce a snapshot — the exact failure this tool exists to prevent |
| Opening the collected files to analyse them | **Read the report, not the collection.** `global_status.out.gz` alone is ~23,000 lines of repeated samples. Generate the report and read that |
| Writing findings only into the chat reply | **Findings go in `annotations.json`**, then the report is regenerated. The deliverable is a file the user can print, forward and compare next month |
| Editing the report HTML to add commentary | Never touch generated HTML. Annotations are a separate input merged at render time |
| Concluding "the database is healthy" from a short or idle collection window | The report says when a window is too short or quiet. **Never state more confidence than the report supports.** Its measurements are authoritative — never contradict a number |
| Offering a general health assessment when the user cannot run the collector | **No data, no audit.** Explain why the data is needed and help them get it — see [If the collector cannot be run](#if-the-collector-cannot-be-run) |
| Collecting immediately because it is convenient now | If the problem happens Monday at 17:00, **collect Monday at 17:00.** Timing the window is the highest-value decision in this process |
| Answering "does MariaDB support X?" with this skill | Wrong skill. Use `mariadb-features`, `mariadb-query-optimization`, and the others |

## The workflow

1. **Interview** — two or three questions, no more.
2. **Check the collector can run.** If not, help; do not improvise an audit.
3. **Choose the collection window**, and say why.
4. **Collect** — run it, or hand the commands over and wait.
5. **Generate the mechanical report.**
6. **Read the report and write `annotations.json`.**
7. **Regenerate with annotations and hand over the file.**

### 1. Interview

Ask only what changes the collection window or the priority order. Cap it at three
questions. If the user does not engage, state your assumption and continue — never
block on an unanswered question.

Worth asking:

- What does this database do, and roughly how busy is it?
- Is something specifically wrong, or is this precautionary?
- If something is wrong: **when** does it happen?
- Has anything changed recently — schema, traffic, version, hardware?

Most users who say "just check my database" have a half-formed suspicion. One
question usually surfaces it, and it converts a vague request into a targeted one.

### 2. Check the collector can run

Needed: shell access on the server **or** network access from elsewhere, a MariaDB
user with read access, and Go 1.24+ or a prebuilt binary.

```sh
cd <skill-dir>/collector       # every command below is relative to here
go build ./cmd/mariadb-envcollect
go build ./cmd/mariadb-metrics
```

`<skill-dir>` is the directory holding this `SKILL.md`. If the skill is installed as
a symlink, resolve it first — the collector source has to be the real path, not the
link, for `go build` to work.

### 3. Choose the collection window

**This is the highest-leverage decision you make.** If the window does not contain
the phenomenon, nothing downstream recovers it.

| Situation | Window |
|---|---|
| A symptom with a known time ("slow Mondays at 17:00") | Cover it — Monday 16:30 to 17:30. **Say so even if that means waiting days.** |
| A symptom with no known time | Ask when the server is busiest; collect then |
| Precautionary audit | A representative busy period, not an idle one |
| Live incident | Now, at least 10 minutes |

Under 5 minutes produces charts the report itself labels as too short to support a
conclusion. 10 minutes is a reasonable floor; longer is better.

Waiting is a legitimate and often correct recommendation. Say plainly: *"Run this
Monday from 16:30 and bring me the package — collecting now would tell us about a
quiet Tuesday instead."*

### 4. Collect

Environment is a one-off snapshot; metrics sample over the window. Run both.

```sh
./mariadb-envcollect -out /tmp/collect/env -package=false -cleanup=false
./mariadb-metrics    -out /tmp/collect     -duration 10m -package=false -cleanup=false
```

Collecting over the network instead of on the server — add `-rds`, which skips the
operating-system data that only works locally:

```sh
./mariadb-metrics -rds -duration 10m -mariadb-host db.example.com -mariadb-user collector
```

If you cannot reach the server, **hand the commands over and wait.** Ask the user to
add `-package` and return the `.tgz`. Picking the run back up from a package they
provide is normal, not a fallback.

### 5. Generate the report

```sh
python3 scripts/mariadb-report.py /tmp/collect --text -o report.html
```

One path is enough — the script finds both collections underneath it. It also reads
a `.tgz` directly.

`--text` writes `report.txt` alongside the HTML. **Read that** — do not write your
own HTML parser. The HTML remains the deliverable for the user.

### 6. Read the report and write annotations

Read `report.txt` (from `--text` above). Nine sections plus appendices: identity,
InnoDB, connections, query indicators, schema, security, MariaDB features,
replication.

Write your analysis to a JSON file:

```json
{"annotations": [
  {
    "section": "summary",
    "source": "ai",
    "author": "Claude",
    "timestamp": "2026-08-10 14:22",
    "title": "Context and priorities",
    "body": "First paragraph.\n\nSecond paragraph."
  }
]}
```

| Field | Value |
|---|---|
| `section` | `summary`, `identity`, `innodb`, `connections`, `performance`, `schema`, `security`, `features`, `replication` |
| `source` | `ai` (yours) or `human` (a note the user dictates) |
| `author`, `timestamp` | attribution shown on the block |
| `title` | optional heading |
| `body` | plain text; blank lines separate paragraphs. No HTML — it is escaped |

Put the main analysis in `summary`. Attach section-specific observations to their
own sections.

### 7. Regenerate and hand over

```sh
python3 scripts/mariadb-report.py /tmp/collect \
  --annotations annotations.json -o report.html
```

Tell the user where the file is and summarise briefly. The file is the deliverable;
your chat reply is not.

## The analysis brief

> **Read [`references/analysis.md`](references/analysis.md) before writing findings.**
> It covers how to weigh a finding against this server's context, the order fixes
> must go in, which correlations are worth attempting, and — importantly — which
> widely repeated tuning advice refers to variables modern MariaDB has removed.

You contribute four things. Everything else in the report is already measured.

1. **Re-frame around the stated problem.** Lead with the evidence that bears on what
   the user asked. Reordering emphasis is yours to do; changing a number is not.
2. **Correlate** — across sections and across time. The report cannot do this. Look
   for a metric spike lining up with a `crontab` entry, an error-log event, a
   processlist pattern, or another series moving together.
3. **Sequence into a plan.** Numbered, dependencies explicit, each step naming the
   action, the command, and the section holding the evidence.
4. **Say what is good.** Sound durability settings, sane configuration, healthy
   indexes. A report that lists only problems fails the user who asked "what could
   be improved?" about a healthy system.

### How to write findings

- **Name the dominant problem first**, in one sentence, before any list.
- **State prerequisite chains.** Some fixes make things worse in the wrong order —
  a larger redo log without a larger buffer pool means more flushing, not less.
- **Gate every recommendation on the server's actual version**, which the report
  states. Advice that was right for MySQL 5.5 is often wrong on MariaDB 11.8.
- **Give the operational cost.** Which changes are dynamic (`SET GLOBAL`) and
  reversible, and which need a restart and a maintenance window.
- **Explain the mechanism, not just the symptom** — briefly. It is what lets the
  reader generalise.
- **Ask, rather than invent.** An open question ("what generates these admin
  commands?") beats a confident guess.
- **State what you could not see.** Performance Schema off, no OS data, short
  window. The report already flags these; do not quietly work around them.
- **You may disagree with the report's *judgement*, but never with its *numbers*.**
  Severity labels and generic checks are heuristics and can misfire — a "test
  database exists" finding is wrong if that database is real work. When one does,
  say so in an annotation on that section, with the reason. Silently leaving a
  finding you believe is wrong is worse than arguing with it.
- **Write for a developer, not a DBA.** The audience runs the application.
- **Check the variable exists in Appendix A** before recommending a change to it.
  Much published advice names variables MariaDB has removed — `innodb_buffer_pool_instances`
  (gone in 10.6), `innodb_file_format`, `innodb_change_buffering` (gone in 11.0).

## If the collector cannot be run

There is no degraded mode: no substitute SQL, no audit from the schema, no general
advice presented as a server assessment. That is what the other MariaDB skills are
for, and passing it off as an audit is worse than declining.

But **the useful response is help, not refusal.** Do two things:

**Explain why the data is needed**, in a sentence or two. The questions being asked
are about how the server behaves over time; no amount of reasoning about a schema
or a configuration file answers them. A counter read once is a lifetime total
divided by an unknown uptime.

**Then work the obstacle** — most people stop for a practical reason:

| Obstacle | Answer |
|---|---|
| No Go toolchain | Build elsewhere and copy one static binary across; it has no dependencies |
| No shell access on the server | `-rds` collects over the network. Says goodbye to OS data, keeps everything MariaDB |
| Cannot install anything | Nothing is installed. One binary, writing to a directory they choose |
| Worried about impact | Read-only — `SELECT` and `SHOW` only. Appendix B of the report lists exactly what ran |
| Unsure when to run it | That is the interview, and the most valuable help available |

Leave the user with a command they can run.

## Defer to the other skills

Cite them; do not restate them. When the audit surfaces something they cover, name
the skill and move on.

| Finding | Skill |
|---|---|
| Missing or redundant indexes, slow queries, `EXPLAIN` | `mariadb-query-optimization` |
| Replication lag, Galera, GTID, HA topology | `mariadb-replication-and-ha` |
| Hand-rolled audit tables or history triggers | `mariadb-system-versioned-tables` |
| Unused MariaDB capabilities generally | `mariadb-features` |
| `VECTOR` columns, embeddings, semantic search | `mariadb-vector` |
| MySQL-era syntax or assumptions in the schema | `mysql-to-mariadb` |

Verify version claims against [mariadb.com/docs](https://mariadb.com/docs).
