# CLAUDE.md — Editing Guidelines for MariaDB Skills

## Repository structure

Each skill lives in its own directory with a single `SKILL.md` file. The file has YAML frontmatter (`name`, `description`) followed by the skill content.

## Before editing

- **Check MariaDB documentation** at [mariadb.com/docs](https://mariadb.com/docs) before adding or changing any technical claim. Do not rely on issue reports, memory, or training data alone.
- **Verify minimum version numbers** from official docs or release notes — not from issues or third-party sources.
- **Cross-reference existing skills** — topics span multiple skills (e.g. system-versioned tables appear in `mariadb-features` and `mariadb-system-versioned-tables`). Keep them consistent; avoid duplicating detailed content.

## Content conventions

- **Version tags are mandatory**: Every feature or behavior must include its minimum MariaDB version as a suffix, e.g. `(10.5+)`, `(11.8+)`. This is the most important convention — agents use these to filter advice for the user's version.
- **Default baseline is 11.8 LTS**: Assume MariaDB 11.8 LTS unless stated otherwise. Features from 12.x/13.0 may be mentioned but must be explicitly tagged as not available on 11.8.
- **"What LLMs Get Wrong" table**: Every skill starts with a wrong/right table of common AI misconceptions. New entries go here when the issue is a misconception, not a missing feature.
- **MDEV references**: Include MDEV ticket numbers for non-obvious behavior changes, e.g. `(11.6+, MDEV-19123)`.
- **Documentation links**: Always link to `mariadb.com/docs`. The old Knowledge Base at `mariadb.com/kb/en/` no longer exists.
- **Tool names**: Use current MariaDB names (`mariadb`, `mariadb-dump`, `mariadb-binlog`), never the deprecated MySQL-era names (`mysql`, `mysqldump`, `mysqlbinlog`).
- **Default paths**: Use MariaDB defaults (`/var/lib/mysql/`, `mariadb-bin.*`), not MySQL defaults (`/var/log/mysql/`, `mysql-bin.*`).

## Style

- Concise — these are briefings for AI agents, not tutorials. Every sentence should change what an agent generates.
- No marketing language — state what the feature does and when to use it.
- Code examples must be copy-pasteable and correct for the stated version.
- Use `|` tables for comparisons, `>` blockquotes for important context, fenced code blocks for SQL/shell.
- Update `*Last updated: YYYY-MM-DD*` below the main heading on every edit.

## Commits

- Reference GitHub issue numbers in commits (`Fixes #11`) so they auto-close.
- One commit per logical change (may batch related small fixes in one skill file).
