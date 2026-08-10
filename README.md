# MariaDB Agent Skills

Skills for AI coding agents — so they get right what they often get wrong about MariaDB (MySQL compatibility, vectors, replication, Oracle migration, and more), and so they can audit a running server. Skills install on the **AI agent**, not on your MariaDB server.

## What is a skill?

A skill is a curated markdown file the agent reads when a topic matches. Think of it as a briefing note: MariaDB-specific guidance, common LLM mistakes, and pointers to [mariadb.com/docs](https://mariadb.com/docs).

Skills work with Claude Code, GitHub Copilot, Cursor, OpenAI Codex, and 20+ other tools. Learn more at [agentskills.io](https://agentskills.io) and browse community skills at [skills.sh](https://www.skills.sh).

## Skills for MariaDB

There are two kinds at **[github.com/mariadb/skills](https://github.com/mariadb/skills)**. Install and usage instructions follow below.

**Corrective skills** are briefings. The agent reads one when your question matches its topic, and applies the guidance for that session. They use wrong/right pairs, version annotations, and links to official documentation so agents can tailor advice to the MariaDB version you run. They never touch a database.

**Process skills** do work. They run a defined procedure — collecting data, producing an artefact — and involve tools beyond the SKILL.md file itself.

## Process skills

### [vibe-dba](blob/main/vibe-dba/README.md)

Audit a running MariaDB server. A dependency-free Go collector gathers an environment snapshot plus metrics sampled over a window — on the database server, with no AI involved. A Python script turns that into an HTML report: nine sections, charts on real clock time, security findings by severity, and an inventory of which MariaDB features are and are not in use. The agent then reads the report and adds correlation and prioritisation on top, marked so you can see which parts are measured and which are judgement.

Collection is read-only, and the report is worth reading on its own without any AI involvement.

## Corrective skills

### [mysql-to-mariadb](https://github.com/MariaDB/skills/blob/main/mysql-to-mariadb/SKILL.md)

For developers migrating from MySQL, or where MySQL habits cause unexpected behavior in MariaDB. Covers authentication, JSON differences, GTID incompatibility with MySQL, and features MariaDB has that MySQL does not.

### [mariadb-features](https://github.com/MariaDB/skills/blob/main/mariadb-features/SKILL.md)

For developers who want to use MariaDB well or evaluate a migration. System-versioned tables, `RETURNING`, sequences, instant DDL, native IP types, Oracle compatibility mode, Galera, ColumnStore, and more — the things agents rarely suggest unprompted.

### [oracle-to-mariadb](https://github.com/MariaDB/skills/blob/main/oracle-to-mariadb/SKILL.md)

For developers migrating from Oracle Database. `sql_mode=ORACLE`, PL/SQL compatibility, data type mapping, what needs manual rewriting, and migration tools.

### [mariadb-query-optimization](https://github.com/MariaDB/skills/blob/main/mariadb-query-optimization/SKILL.md)

For diagnosing slow queries and designing indexes. EXPLAIN, composite index rules, cursor-based pagination, histograms, and MariaDB-specific optimizer behavior.

### [mariadb-replication-and-ha](https://github.com/MariaDB/skills/blob/main/mariadb-replication-and-ha/SKILL.md)

For replicated and HA setups — async replication, GTID, semi-sync, parallel replication, Galera Cluster, and application constraints that matter in production.

### [mariadb-system-versioned-tables](https://github.com/MariaDB/skills/blob/main/mariadb-system-versioned-tables/SKILL.md)

For automatic row history without triggers or audit tables — `FOR SYSTEM_TIME` queries, partitioning, and gotchas.

### [mariadb-vector](https://github.com/MariaDB/skills/blob/main/mariadb-vector/SKILL.md)

For AI applications, RAG, and semantic search. Native vector support since MariaDB 11.7 — no extension or plugin to install.

### [mariadb-mcp](https://github.com/MariaDB/skills/blob/main/mariadb-mcp/SKILL.md)

For connecting agents to a MariaDB database via the Model Context Protocol — schemas, read-only SQL, optional semantic search, and secure setup.

## How to install

### Interactive install with Node.js

Use [npx skills](https://github.com/vercel-labs/skills) for an interactive install — choose which MariaDB skills to install and which agents on your system (Claude, Cursor, Codex, etc.):

```
npx skills add mariadb/skills
```

Or install one skill:

```
npx skills add mariadb/skills/<skill-name>
```

Replace `<skill-name>` with e.g. `mariadb-vector` or `mysql-to-mariadb`. Installs are counted on [skills.sh](https://www.skills.sh).

### Installing without Node.js

Download `SKILL.md` into the agent's skill folder, for example:

```bash
mkdir -p ~/.claude/skills/<skill-name>
curl -o ~/.claude/skills/<skill-name>/SKILL.md \
  https://raw.githubusercontent.com/mariadb/skills/main/<skill-name>/SKILL.md
```

- Claude Code and Claude Desktop: `~/.claude/skills/<skill-name>/SKILL.md`
- OpenAI Codex: `~/.agents/skills/<skill-name>/SKILL.md`

This works for the corrective skills, which are a single file. **`vibe-dba` also ships a collector and a report generator**, so it needs the whole directory rather than one file — clone the repository and link it into place:

```bash
git clone https://github.com/mariadb/skills.git
ln -s "$(pwd)/skills/vibe-dba" ~/.claude/skills/vibe-dba
```

### Updating installed skills

MariaDB skills may be updated over time. If you installed with `npx skills`, refresh your local copies periodically:

```
npx skills check
npx skills update
```

Run `check` first to see what's outdated; `update` pulls the latest from this repo. If you installed manually with `curl`, re-run the download commands above.

## Using MariaDB skills

Once installed, skills activate automatically. When your question matches a skill's topic, the agent reads it and applies the guidance for that session. No manual activation is needed.

For the corrective skills that means better answers, with no visible step. For `vibe-dba` it means the agent starts a procedure: it will ask a couple of questions, run the collector against your server, and hand you a report. Ask it something like *"check the health of my MariaDB"* — see the [vibe-dba README](vibe-dba/README.md) for what it collects and how.

## Contributions and Improvements

Open for improvements and more skills. Contributions and feedback welcome via pull requests.
