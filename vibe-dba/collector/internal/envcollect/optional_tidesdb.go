//go:build tidesdb

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
		ID:        "310-optional-tidesdb",
		IfEnabled: func(ctx *collector.Context) bool { return true },
		Run:       collectTidesDB,
	})
}

func collectTidesDB(ctx *collector.Context) error {
	tidesdb, err := exec.LookPath("tidesdb")
	if err != nil {
		return ctx.WriteFile("tidesdb", []byte("tidesdb binary not found\n"))
	}

	var b strings.Builder
	for _, args := range [][]string{{"--version"}, {"version"}, {"--help"}} {
		out, runErr := ctx.CommandOutput(tidesdb, args...)
		fmt.Fprintf(&b, "##### tidesdb %s #####\n%s", strings.Join(args, " "), out)
		if runErr != nil {
			fmt.Fprintf(&b, "ERROR: %v\n", runErr)
		}
		b.WriteString("\n")
	}

	for _, path := range splitTidesEnvList(os.Getenv("TIDESDB_PATHS")) {
		fmt.Fprintf(&b, "##### path %s #####\n", path)
		if info, statErr := os.Stat(path); statErr == nil {
			fmt.Fprintf(&b, "mode=%s size=%d modtime=%s\n", info.Mode(), info.Size(), info.ModTime().Format("2006-01-02 15:04:05"))
		} else {
			fmt.Fprintf(&b, "ERROR: %v\n", statErr)
		}
	}
	return ctx.WriteFile("tidesdb", []byte(b.String()))
}

func splitTidesEnvList(value string) []string {
	var out []string
	for _, item := range strings.Split(value, ",") {
		item = strings.TrimSpace(item)
		if item != "" {
			out = append(out, item)
		}
	}
	return out
}
