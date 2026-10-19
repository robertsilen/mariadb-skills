# MariaDB Collector

The collector is a [MariaDB Shell](https://github.com/mariadb-corporation/mariadb-shell)
plugin, `shell-plugin/vibe_dba/`. It runs on the Python the shell bundles, connects
through the shell's own session, and needs nothing else on the server — no client
tools, no toolchain.

## Install

Install the shell, then link the plugin into the shell's plugin directory:

```sh
curl -fsSL https://github.com/mariadb-corporation/mariadb-shell/raw/main/install.sh | MARIADB_SHELL_TAG=v26.10.2 bash
mkdir -p ~/.mariadb-shell/plugins
ln -s "$(pwd)/shell-plugin/vibe_dba" ~/.mariadb-shell/plugins/vibe_dba
```

The installer writes to `~/.local` and needs no root.

## Usage

The plugin registers a global `vibeDba` object with two functions. The connection is
the shell's own, so credentials, sockets, TLS and the credential store work as usual:

```sh
mariadb-shell collector@localhost -- vibeDba collect-environment --outputDir=/tmp/collect
mariadb-shell collector@localhost -- vibeDba collect-metrics --outputDir=/tmp/collect --duration=10m
```

From the shell's Python mode the same calls are
`vibeDba.collect_environment({'outputDir': '/tmp/collect'})` and
`vibeDba.collect_metrics({'outputDir': '/tmp/collect', 'duration': '10m'})`.
`\? vibeDba` lists every option.

| Option | Default | |
|---|---|---|
| `outputDir` | current directory | Each run writes `<host>_environment_<date>/` or `<name>_metrics_<timestamp>/` under it |
| `duration` | `5m` | Metrics only. `10m`, `1h30m`, `90s`, plain minutes, or `ctrlc` |
| `hostInfo` | `true` | Collect OS data. Skipped automatically when the server is not on this host |
| `replication` | `false` | Metrics only. Also sample `SHOW SLAVE STATUS` every second |
| `name` | server hostname | Metrics only. Name used in the output directory |
| `package` | `true` | Write a `.tgz` beside the directory |
| `cleanup` | `false` | Remove the directory, keeping only the `.tgz` |
| `verbose` | `false` | Print each collector as it runs |

`duration: 'ctrlc'` only works from Python mode: the command-line integration (`--`)
does not pass Ctrl-C to plugins, so give it a fixed duration.

OS data is read from the host the shell runs on, so it only describes the database
server when the two are the same machine. The plugin decides from the connection (a
socket or `localhost`) and by comparing `@@hostname` with this host; when the server is
remote it skips OS data and says so. A `collector` file in each output records the
plugin and shell versions and whether OS data was collected.

## What it collects

`collectEnvironment` runs once: hardware identity, OS files and commands, cron,
configuration files and the error log tail, version, variables, status, engines and
plugins, replication status, every object definition (`SHOW CREATE`), sizes, the
analysis queries, vector index definitions, duplicate indexes and grants.

`collectMetrics` samples over the window: `SHOW GLOBAL STATUS` and
`information_schema.INNODB_METRICS` every second, `SHOW ENGINE INNODB STATUS` and
`SHOW FULL PROCESSLIST` every minute, plus `vmstat`, `mpstat`, `iostat -mx` and
`/proc/diskstats` on Linux. Every SQL sample is preceded by a `#TS` timestamp line.

Query results are written in the `mariadb` client's output formats (`-B`, `-N -B`,
`\G`), one file per query, so the raw files are readable without this tool. Percona
Toolkit is not required: the `pt-summary`, `pt-mysql-summary`,
`pt-duplicate-key-checker` and `pt-show-grants` roles are covered by the
`native-*` collectors.

## Reports

Create a self-contained HTML report from a collection directory or package:

```sh
python3 scripts/mariadb-report.py /tmp/collect -o report.html
python3 scripts/mariadb-report.py /tmp/collect/host_metrics_2026-06-26_10-00-00.tgz -o report.html
```

If no path is provided, the script asks for one. Given a parent directory, it finds
both collections underneath it.

## Adding Collectors

For a one-off collector, add a `(name, enabled, function)` entry to `collectors()` in
`shell-plugin/vibe_dba/environment.py`; entries run in order and a failure is logged to
`collector-errors.log` without stopping the run. For a sampled stream, add an entry to
`SQL_STREAMS` in `shell-plugin/vibe_dba/metrics.py`. Render query results with
`ctx.query_text()`, which matches the `mariadb` client's formats.
