package envcollect

import (
	"fmt"
	"strings"

	"mariadb-collector/internal/collector"
)

func init() {
	Register(collector.FuncCollector{
		ID:        "200-native-summary",
		IfEnabled: func(ctx *collector.Context) bool { return !ctx.RDS },
		Run:       collectNativeSystemSummary,
	})
	Register(collector.FuncCollector{
		ID:        "210-native-mariadb-summary",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectNativeMariaDBSummary,
	})
	Register(collector.FuncCollector{
		ID:        "220-native-duplicate-indexes",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectNativeDuplicateIndexes,
	})
	Register(collector.FuncCollector{
		ID:        "230-native-grants",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectNativeGrants,
	})
}

func collectNativeSystemSummary(ctx *collector.Context) error {
	sections := []struct {
		title string
		cmd   string
		args  []string
	}{
		{"Kernel", "uname", []string{"-a"}},
		{"Uptime", "uptime", nil},
		{"CPU", "lscpu", nil},
		{"Memory", "free", []string{"-m"}},
		{"Filesystems", "df", []string{"-hT"}},
		{"Block Devices", "lsblk", []string{"-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,ROTA,SCHED"}},
		{"Network Addresses", "ip", []string{"addr"}},
		{"Network Routes", "ip", []string{"route"}},
		{"Sysctl VM", "sysctl", []string{"vm.swappiness", "vm.dirty_ratio", "vm.dirty_background_ratio", "vm.overcommit_memory"}},
	}

	var b strings.Builder
	for _, section := range sections {
		fmt.Fprintf(&b, "##### %s #####\n", section.title)
		out, err := ctx.CommandOutput(section.cmd, section.args...)
		b.Write(out)
		if err != nil {
			fmt.Fprintf(&b, "ERROR: %v\n", err)
		}
		b.WriteString("\n")
	}
	return ctx.WriteFile("native-system-summary", []byte(b.String()))
}

func collectNativeMariaDBSummary(ctx *collector.Context) error {
	queries := []struct {
		title string
		sql   string
	}{
		{"Version", "SELECT VERSION() version, @@version_comment version_comment, @@hostname hostname, @@port port, @@socket socket"},
		{"Important Variables", "SHOW VARIABLES WHERE Variable_name IN ('basedir','datadir','tmpdir','log_error','innodb_buffer_pool_size','innodb_log_file_size','innodb_flush_log_at_trx_commit','sync_binlog','max_connections','table_open_cache','open_files_limit','performance_schema')"},
		{"Important Status", "SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime','Threads_connected','Threads_running','Connections','Max_used_connections','Questions','Queries','Slow_queries','Created_tmp_disk_tables','Created_tmp_tables','Innodb_buffer_pool_reads','Innodb_buffer_pool_read_requests','Innodb_log_waits','Innodb_row_lock_waits')"},
		{"Schema Sizes", "SELECT table_schema, ROUND(SUM(data_length+index_length)/1024/1024/1024,2) total_gb, COUNT(*) tables FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY table_schema ORDER BY total_gb DESC"},
		{"Engine Counts", "SELECT engine, COUNT(*) tables FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY engine ORDER BY tables DESC"},
	}

	var b strings.Builder
	for _, q := range queries {
		fmt.Fprintf(&b, "##### %s #####\n%s\n", q.title, q.sql)
		out, err := ctx.MariaDBRaw(q.sql, "-B")
		b.Write(out)
		if err != nil {
			fmt.Fprintf(&b, "ERROR: %v\n", err)
		}
		b.WriteString("\n")
	}
	return ctx.WriteFile("native-mariadb-summary", []byte(b.String()))
}

func collectNativeDuplicateIndexes(ctx *collector.Context) error {
	sql := `
SELECT
  s1.table_schema,
  s1.table_name,
  s1.index_name AS duplicate_index,
  s2.index_name AS covered_by_index,
  GROUP_CONCAT(s1.column_name ORDER BY s1.seq_in_index) AS duplicate_columns
FROM information_schema.statistics s1
JOIN information_schema.statistics s2
  ON s1.table_schema = s2.table_schema
 AND s1.table_name = s2.table_name
 AND s1.seq_in_index = s2.seq_in_index
 AND s1.column_name = s2.column_name
 AND s1.index_name <> s2.index_name
WHERE s1.table_schema NOT IN ('mysql','information_schema','performance_schema','sys')
GROUP BY s1.table_schema, s1.table_name, s1.index_name, s2.index_name
HAVING COUNT(*) = (
  SELECT COUNT(*)
  FROM information_schema.statistics x
  WHERE x.table_schema = s1.table_schema
    AND x.table_name = s1.table_name
    AND x.index_name = s1.index_name
)
ORDER BY s1.table_schema, s1.table_name, s1.index_name;
`
	out, err := ctx.MariaDBRaw(sql, "-B")
	content := "----- duplicate or left-prefix covered indexes -----\n" + string(out)
	if err != nil {
		content += "\nERROR: " + err.Error() + "\n"
	}
	return ctx.WriteFile("native-duplicate-indexes", []byte(content))
}

func collectNativeGrants(ctx *collector.Context) error {
	users, err := ctx.MariaDBRaw("SELECT CONCAT(QUOTE(user),'@',QUOTE(host)) FROM mysql.user ORDER BY user, host", "-N", "-B")
	if err != nil {
		return ctx.WriteFile("native-show-grants", append(users, []byte("\nERROR: "+err.Error()+"\n")...))
	}

	var b strings.Builder
	for _, account := range strings.Split(strings.TrimSpace(string(users)), "\n") {
		account = strings.TrimSpace(account)
		if account == "" {
			continue
		}
		sql := "SHOW GRANTS FOR " + account
		fmt.Fprintf(&b, "##### %s #####\n", sql)
		out, grantErr := ctx.MariaDBRaw(sql, "-N", "-B")
		b.Write(out)
		if grantErr != nil {
			fmt.Fprintf(&b, "ERROR: %v\n", grantErr)
		}
		b.WriteString("\n")
	}
	return ctx.WriteFile("native-show-grants", []byte(b.String()))
}
