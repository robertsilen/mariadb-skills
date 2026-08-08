//go:build repl

package metrics

import (
	"time"

	"mariadb-collector/internal/collector"
)

func init() {
	Register(collector.FuncCollector{
		ID:        "120-optional-replication-status",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run: func(ctx *collector.Context) error {
			go func() {
				_ = collector.RunPeriodic(ctx, collector.StreamTask{
					Name:     "replica-status",
					Every:    time.Second,
					FileName: "replica_status.out.gz",
					Run: func(ctx *collector.Context) ([]byte, error) {
						return ctx.MariaDBRaw("SHOW SLAVE STATUS", "-B", "-N")
					},
				})
			}()
			return nil
		},
	})
}
