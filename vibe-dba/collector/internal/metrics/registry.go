package metrics

import "mariadb-collector/internal/collector"

var Registry collector.Registry

func Register(c collector.Collector) {
	Registry.Register(c)
}
