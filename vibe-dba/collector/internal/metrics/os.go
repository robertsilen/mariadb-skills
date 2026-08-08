package metrics

import (
	"os"
	"time"

	"mariadb-collector/internal/collector"
)

func init() {
	Register(collector.FuncCollector{
		ID:        "010-os-streams",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeOS && !ctx.RDS },
		Run:       collectOSStreams,
	})
}

func collectOSStreams(ctx *collector.Context) error {
	tasks := []func(){
		func() { _ = collector.RunStreamingCommand(ctx.Context, ctx.Path("mpstat.out.gz"), "mpstat", "1") },
		func() { _ = collector.RunStreamingCommand(ctx.Context, ctx.Path("vmstat.out.gz"), "vmstat", "1") },
		func() {
			_ = collector.RunStreamingCommand(ctx.Context, ctx.Path("iostat.out.gz"), "iostat", "-mx", "1")
		},
		func() {
			_ = collector.RunPeriodic(ctx, collector.StreamTask{
				Name:     "diskstats",
				Every:    time.Second,
				FileName: "diskstats.out.gz",
				Run: func(ctx *collector.Context) ([]byte, error) {
					return os.ReadFile("/proc/diskstats")
				},
			})
		},
	}
	for _, task := range tasks {
		go task()
	}
	return nil
}
