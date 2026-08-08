//go:build duckdb

package envcollect

import (
	"fmt"
	"os"
	"os/exec"
	"strings"

	"mariadb-collector/internal/collector"
)

func init() {
	Register(collector.FuncCollector{
		ID:        "300-optional-duckdb",
		IfEnabled: func(ctx *collector.Context) bool { return true },
		Run:       collectDuckDB,
	})
}

func collectDuckDB(ctx *collector.Context) error {
	duckdb, err := exec.LookPath("duckdb")
	if err != nil {
		return ctx.WriteFile("duckdb", []byte("duckdb binary not found\n"))
	}

	var b strings.Builder
	version, versionErr := ctx.CommandOutput(duckdb, "--version")
	fmt.Fprintf(&b, "##### duckdb --version #####\n%s", version)
	if versionErr != nil {
		fmt.Fprintf(&b, "ERROR: %v\n", versionErr)
	}

	for _, db := range splitEnvList(os.Getenv("DUCKDB_DATABASES")) {
		fmt.Fprintf(&b, "\n##### %s #####\n", db)
		for _, sql := range []string{
			"PRAGMA database_list;",
			"PRAGMA show_tables;",
			"SELECT * FROM duckdb_settings();",
			"SELECT * FROM duckdb_extensions();",
		} {
			out, queryErr := ctx.CommandOutput(duckdb, db, "-c", sql)
			fmt.Fprintf(&b, "----- %s -----\n%s", sql, out)
			if queryErr != nil {
				fmt.Fprintf(&b, "ERROR: %v\n", queryErr)
			}
		}
	}
	return ctx.WriteFile("duckdb", []byte(b.String()))
}

func splitEnvList(value string) []string {
	var out []string
	for _, item := range strings.Split(value, ",") {
		item = strings.TrimSpace(item)
		if item != "" {
			out = append(out, item)
		}
	}
	return out
}
