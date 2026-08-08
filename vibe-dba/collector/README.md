# MariaDB Collector

Two Go binaries replace the legacy shell collectors:

- `mariadb-envcollect` collects static operating system and MariaDB environment data.
- `mariadb-metrics` collects time-series OS and MariaDB metrics.

The collector definitions live in dedicated Go files under `internal/envcollect` and
`internal/metrics`. New collectors register themselves from `init`, so adding a file
to one of those packages includes it at build time. Optional collectors can use Go
build tags; see `internal/metrics/optional_repl.go`,
`internal/envcollect/optional_duckdb.go`, and
`internal/envcollect/optional_tidesdb.go`.

## Build

```sh
go build ./cmd/mariadb-envcollect
go build ./cmd/mariadb-metrics
```

Include optional replication metrics:

```sh
go build -tags repl ./cmd/mariadb-metrics
```

Include optional DuckDB and TidesDB environment collectors:

```sh
go build -tags duckdb,tidesdb ./cmd/mariadb-envcollect
```

## Usage

```sh
./mariadb-envcollect -mariadb-defaults-file /root/.my.cnf
./mariadb-metrics -out /tmp -duration 60 -mariadb-host db01 -mariadb-user root -mariadb-password secret
./mariadb-metrics -duration ctrlc -rds -os=false -mariadb-host rds.example.net -mariadb-port 3306 -mariadb-user collector
./mariadb-envcollect -mariadb-conn mariadb://fred@127.0.0.1:1123
./mariadb-envcollect -mariadb-conn mysql://fred:pwd@myserver.be
./mariadb-envcollect -mariadb-conn socket:/tmp/maria.socket
./mariadb-envcollect -package -cleanup
./mariadb-metrics -duration 10m -package=false -cleanup=false
```

Both commands also read `MARIADB_OPTIONS`. `-rds` skips local OS/root-only collection.
Direct connection environment variables are also supported: `MARIADB_DEFAULTS_FILE`,
`MARIADB_HOST`, `MARIADB_PORT`, `MARIADB_SOCKET`, `MARIADB_USER`,
`MARIADB_PASSWORD`, `MARIADB_DATABASE`, `MARIADB_SSL`, and `MARIADB_CONN`.
Use `-mariadb-options` for extra raw client options that do not have a dedicated flag.
If no password is supplied and the MariaDB preflight check fails because a password is
required, the collector prompts for it once and retries before collecting data.
If `-package` is omitted, the collector asks `Do you want to package all collected data?`
after collection and writes `<output>.tgz` when accepted. If `-cleanup` is omitted, it
asks whether the raw collected data directory should be removed. Cleanup never removes
the generated tarball.

## Graph Reports

Create a self-contained dark HTML report from a metrics directory or package:

```sh
python3 scripts/mariadb-report.py /tmp/host_metrics_2026-06-26_10-00-00
python3 scripts/mariadb-report.py /tmp/host_metrics_2026-06-26_10-00-00.tgz -o report.html
```

If no path is provided, the script asks for one. It recognizes the collector streams
for `vmstat`, `mpstat`, `diskstats`, `global_status`, `mariadb-admin`, and
`innodb_metrics`, then writes a Grafana-like dark `metrics-report.html`.

DuckDB collection uses the local `duckdb` CLI. Set `DUCKDB_DATABASES` to a
comma-separated list of database files to collect per-database metadata.

TidesDB collection uses the local `tidesdb` CLI. Set `TIDESDB_PATHS` to a
comma-separated list of paths to include basic path metadata.

Percona Toolkit is not required. The former `pt-summary`, `pt-mysql-summary`,
`pt-duplicate-key-checker`, and `pt-show-grants` roles are covered by native Go
collectors that gather system summary data, MariaDB summary data, duplicate or
covered indexes, and account grants.

## Adding Collectors

Create a new file in `internal/envcollect` or `internal/metrics`:

```go
package envcollect

import "mariadb-collector/internal/collector"

func init() {
	Register(collector.FuncCollector{
		ID: "300-my-collector",
		Run: func(ctx *collector.Context) error {
			return ctx.CaptureCommand("my-output", "my-command", "--flag")
		},
	})
}
```

To make it build-time optional:

```go
//go:build mytag
```

Then build with `go build -tags mytag ./cmd/mariadb-envcollect`.
