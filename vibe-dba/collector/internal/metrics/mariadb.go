package metrics

import (
	"time"

	"mariadb-collector/internal/collector"
)

func init() {
	Register(collector.FuncCollector{
		ID:        "100-mariadb-admin-status",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectMariaDBAdminStatus,
	})
	Register(collector.FuncCollector{
		ID:        "110-mariadb-periodic-sql",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectMariaDBPeriodicSQL,
	})
}

func collectMariaDBAdminStatus(ctx *collector.Context) error {
	go func() {
		args := append([]string{}, ctx.MariaDBOptions...)
		args = append(args, "extended-status", "--sleep=1", "--relative")
		_ = collector.RunStreamingCommand(ctx.Context, ctx.Path("mariadb-admin.out.gz"), ctx.MariaDBAdminPath, args...)
	}()
	return nil
}

func collectMariaDBPeriodicSQL(ctx *collector.Context) error {
	tasks := []collector.StreamTask{
		{
			Name:     "innodb-status",
			Every:    60 * time.Second,
			FileName: "innodb_status.out.gz",
			Run: func(ctx *collector.Context) ([]byte, error) {
				return ctx.MariaDBRaw("SHOW ENGINE INNODB STATUS\\G")
			},
		},
		{
			Name:     "processlist",
			Every:    60 * time.Second,
			FileName: "processlist.out.gz",
			Run: func(ctx *collector.Context) ([]byte, error) {
				return ctx.MariaDBRaw("SHOW FULL PROCESSLIST\\G")
			},
		},
		{
			Name:     "global-status",
			Every:    time.Second,
			FileName: "global_status.out.gz",
			Run: func(ctx *collector.Context) ([]byte, error) {
				return ctx.MariaDBRaw("SHOW GLOBAL STATUS", "-B", "-N")
			},
		},
		{
			Name:     "innodb-metrics",
			Every:    time.Second,
			FileName: "innodb_metrics.out.gz",
			Run: func(ctx *collector.Context) ([]byte, error) {
				return ctx.MariaDBRaw("SELECT name, subsystem, count, status FROM information_schema.INNODB_METRICS WHERE status='enabled'", "-B")
			},
		},
	}
	for _, task := range tasks {
		task := task
		go func() { _ = collector.RunPeriodic(ctx, task) }()
	}
	return nil
}
