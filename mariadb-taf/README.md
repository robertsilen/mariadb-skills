# mariadb-taf: measure it instead of guessing

*Last updated: 2026-10-14*

A process skill that helps you run [TAF](https://mariadb.org/projects/taf/), the MariaDB Foundation's open-source Test Automation Framework, to answer one performance question with numbers you can repeat and share.

TAF installs the database versions you want to test on a dedicated Linux host. It runs standard workloads (sysbench, HammerDB TPROC-C and TPROC-H) at a range of concurrency levels, repeats each one, and archives the results together with the exact configuration that produced them. The skill does the part people skip: it pins down the question, changes one variable at a time, sizes the run, and compares the results honestly.

## Pick your problem

Each entry follows the same pattern: **the problem you have → how TAF helps → the template to start from → what to ask your agent.**

### "We're planning an upgrade. Will it make us slower?"
*DBAs, platform teams, application owners*

- **How TAF helps:** install the old and new versions side by side and run the same workload with the same pinned config against each. The only thing that differs is the version. You get throughput and latency at each concurrency level, three runs each, and an overlay chart of A against B.
- **Template:** [`templates/upgrade-check.properties`](templates/upgrade-check.properties)
- **Ask your agent:** *"Use TAF to compare MariaDB 11.4 and 11.8 on this box before we upgrade."*

### "Someone says this my.cnf change will help. Does it?"
*DBAs, SREs*

- **How TAF helps:** two runs, identical except for one setting, each starting from a fresh data directory. You find out whether the gain is bigger than the run-to-run noise, at which concurrency it appears, and whether it turns into a loss at higher load.
- **Template:** [`templates/config-change.properties`](templates/config-change.properties). The TAF repo has a worked example: [`MDEV32750_AHI_ON` / `_AHI_OFF`](https://github.com/MariaDB/taf/tree/main/properties/mariadb/mdevs), which tests the adaptive hash index on and off.
- **Ask your agent:** *"Benchmark thread pool on vs off with TAF and tell me if it's worth it."*

### "How much load can this server take? Do we need a bigger instance?"
*DBAs, architects, capacity planners*

- **How TAF helps:** a concurrency sweep from 1 to well past the core count shows where throughput stops scaling and where latency takes off. Run the same file on two instance sizes to see whether the bigger one is worth paying for.
- **Template:** [`templates/capacity-curve.properties`](templates/capacity-curve.properties)
- **Ask your agent:** *"Find where throughput flattens on this 16-core host."*

### "Which storage engine fits our write-heavy workload?"
*Architects, application developers designing a new service*

- **How TAF helps:** the same workload runs against InnoDB, MyRocks or another engine, each with a config tuned for that engine, so you can compare read-heavy and write-heavy mixes separately.
- **Template:** start from `config-change.properties`. The TAF repo has [engine comparison runs](https://github.com/MariaDB/taf/tree/main/properties/mariadb) and their [configs](https://github.com/MariaDB/taf/tree/main/database_config_files/mariadb).
- **Ask your agent:** *"Compare InnoDB and MyRocks for an update-heavy workload with TAF."*

### "How do our reporting and analytics queries hold up?"
*Data and BI engineers*

- **How TAF helps:** HammerDB TPROC-H gives timings query by query on an analytics schema at a scale you choose, so a change shows up on the query it affects instead of being averaged away.
- **Template:** TAF's [`hammerdb_tproch.properties`](https://github.com/MariaDB/taf/blob/main/properties/mariadb/hammerdb_tproch.properties)
- **Ask your agent:** *"Run TPROC-H at scale 10 on 11.8 and show per-query times."*

### "Is MariaDB the right choice compared with MySQL or PostgreSQL?"
*Teams choosing a database, technical evaluators*

- **How TAF helps:** the same workloads, the same host, and matched configurations sized for the same hardware (TAF ships paired `…_comparable_4vcpu_16gb` and `…_16vcpu_32gb` configs). The vendor's defaults never decide the result.
- **Template:** TAF's [`properties/mariadb`](https://github.com/MariaDB/taf/tree/main/properties/mariadb), [`mysql`](https://github.com/MariaDB/taf/tree/main/properties/mysql) and [`postgresql`](https://github.com/MariaDB/taf/tree/main/properties/postgresql) directories
- **Ask your agent:** *"Set up a fair MariaDB vs PostgreSQL TPROC-C comparison with TAF."*

### "Did this commit or release regress performance?"
*MariaDB contributors, packagers, release testers*

- **How TAF helps:** build a server at any commit, run a library of known performance cases against it, and tag each run with the release or commit. Turn on `perf` with flamegraphs to see where the CPU time moved.
- **Template:** TAF's [`MariaDBCommitBuilder.sh`](https://github.com/MariaDB/taf/blob/main/scripts/build/MariaDBCommitBuilder.sh), the [MDEV case library](https://github.com/MariaDB/taf/tree/main/properties/mariadb/mdevs) and [`MariadbMdevReleaseTesting.sh`](https://github.com/MariaDB/taf/blob/main/scripts/mariadb/mdevs/MariadbMdevReleaseTesting.sh)
- **Ask your agent:** *"Reproduce MDEV-32750 with TAF on 13.0 and profile both sides."*

## What TAF is not for

- **Checking the health of a running server.** Use [`vibe-dba`](../vibe-dba/README.md). TAF tests a change *before* it reaches that server.
- **Benchmarking production.** TAF installs, starts and saturates its own server on a dedicated host.
- **Predicting your application's exact throughput.** The workloads are standard and synthetic. They tell you how a change *moves* performance, and in which direction, on that hardware. They do not tell you what your application will do.

## Install

Like `vibe-dba`, this skill ships files beyond `SKILL.md` (the templates), so install the whole directory:

```bash
git clone https://github.com/mariadb/skills.git
ln -s "$(pwd)/skills/mariadb-taf" ~/.claude/skills/mariadb-taf
```

TAF itself is cloned separately on the benchmark host: [github.com/MariaDB/taf](https://github.com/MariaDB/taf). It needs Linux and Perl 5.30+.
