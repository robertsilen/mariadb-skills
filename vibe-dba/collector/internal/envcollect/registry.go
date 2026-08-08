package envcollect

import "mariadb-collector/internal/collector"

var Registry collector.Registry

func Register(c collector.Collector) {
	Registry.Register(c)
}
