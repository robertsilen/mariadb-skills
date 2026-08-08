package envcollect

import (
	"os"
	"strings"

	"mariadb-collector/internal/collector"
)

func init() {
	Register(collector.FuncCollector{
		ID:        "100-mariadb-snapshots",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectMariaDBSnapshots,
	})
	Register(collector.FuncCollector{
		ID:        "110-mariadb-schema-and-usage",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectMariaDBSchemaAndUsage,
	})
	Register(collector.FuncCollector{
		ID:        "120-mariadb-analysis-queries",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectMariaDBAnalysisQueries,
	})
	Register(collector.FuncCollector{
		ID:        "130-mariadb-config-files",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB && !ctx.RDS },
		Run:       collectMariaDBConfigFiles,
	})
}

func collectMariaDBSnapshots(ctx *collector.Context) error {
	queries := []struct {
		out   string
		query string
		args  []string
	}{
		{"mariadb_collect_start", "SELECT NOW()", []string{"-N", "-B"}},
		{"mariadb_version", "SELECT VERSION(), @@version_comment", []string{"-B"}},
		{"mariadb_variables", "SHOW GLOBAL VARIABLES", []string{"-N", "-B"}},
		{"mariadb_global_status", "SHOW GLOBAL STATUS", []string{"-N", "-B"}},
		{"mariadb_innodb_status", "SHOW ENGINE INNODB STATUS\\G", nil},
		{"mariadb_plugins", "SHOW PLUGINS", []string{"-B"}},
		{"mariadb_engines", "SHOW ENGINES", []string{"-B"}},
	}
	if !ctx.RDS {
		queries = append(queries,
			struct {
				out   string
				query string
				args  []string
			}{"mariadb_replica_status", "SHOW SLAVE STATUS\\G", []string{"-B"}},
			struct {
				out   string
				query string
				args  []string
			}{"mariadb_master_status", "SHOW MASTER STATUS\\G", []string{"-B"}},
		)
	}
	for _, q := range queries {
		_ = ctx.MariaDB(q.out, q.query, q.args...)
	}
	_ = ctx.MariaDB("mariadb_collect_end", "SELECT NOW()", "-N", "-B")
	return nil
}

func collectMariaDBSchemaAndUsage(ctx *collector.Context) error {
	dumpArgs := append([]string{}, ctx.MariaDBOptions...)
	dumpArgs = append(dumpArgs, "-f", "--no-data", "--triggers", "--routines", "--events", "--set-charset", "--all-databases", "--lock-tables=false")
	_ = ctx.CaptureCommand("mariadb_schema", collector.FirstAvailable("mariadb-dump", "mysqldump"), dumpArgs...)

	datadir, err := ctx.MariaDBRaw("SELECT @@datadir", "-N", "-B")
	if err == nil {
		dir := strings.TrimSpace(string(datadir))
		if dir != "" {
			_ = ctx.WriteFile("datadir", []byte(dir+"\n"))
			if !ctx.RDS {
				_ = ctx.CaptureCommand("data_usage_raw", "du", "-sh", dir)
				_ = ctx.CaptureCommand("data_usage_innodb_raw", "find", dir, "-name", "*.ibd", "-o", "-name", "ibdata*")
			}
		}
	}
	return nil
}

func collectMariaDBAnalysisQueries(ctx *collector.Context) error {
	for _, q := range analysisQueries {
		content := "-----" + q.SQL + "=====\n"
		out, err := ctx.MariaDBRaw(q.SQL, "-B")
		content += string(out)
		if err != nil {
			content += "\nERROR: " + err.Error() + "\n"
		}
		_ = ctx.WriteFile(q.Output, []byte(content))
	}
	return nil
}

func collectMariaDBConfigFiles(ctx *collector.Context) error {
	candidates := []string{"/etc/my.cnf", "/etc/mysql/my.cnf", "/etc/mysql/mariadb.cnf"}
	for _, candidate := range candidates {
		if data, err := os.ReadFile(candidate); err == nil {
			name := strings.TrimPrefix(strings.ReplaceAll(candidate, "/", "_"), "_")
			_ = ctx.WriteFile(name, data)
			copyIncludes(ctx, candidate, string(data))
		}
	}
	errorLog, err := ctx.MariaDBRaw("SELECT @@log_error", "-N", "-B")
	if err == nil {
		path := strings.TrimSpace(string(errorLog))
		if path != "" {
			_ = ctx.CaptureCommand("error_log_tail", "tail", "-n", "1000", path)
		}
	}
	_ = ctx.CaptureCommand("tmpdir_lsof_deleted", "sh", "-c", "pidof mysqld mariadbd | xargs -r lsof -p | grep deleted")
	return nil
}

func copyIncludes(ctx *collector.Context, basePath, content string) {
	for _, line := range strings.Split(content, "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 {
			continue
		}
		switch fields[0] {
		case "!include":
			path := fields[1]
			if !strings.HasPrefix(path, "/") {
				path = strings.TrimRight(filepathDir(basePath), "/") + "/" + path
			}
			if data, err := os.ReadFile(path); err == nil {
				_ = ctx.WriteFile("included_"+strings.ReplaceAll(strings.TrimPrefix(path, "/"), "/", "_"), data)
			}
		case "!includedir":
			entries, err := os.ReadDir(fields[1])
			if err != nil {
				continue
			}
			for _, entry := range entries {
				if entry.IsDir() {
					continue
				}
				path := strings.TrimRight(fields[1], "/") + "/" + entry.Name()
				if data, err := os.ReadFile(path); err == nil {
					_ = ctx.WriteFile("included_"+strings.ReplaceAll(strings.TrimPrefix(path, "/"), "/", "_"), data)
				}
			}
		}
	}
}

func filepathDir(path string) string {
	idx := strings.LastIndex(path, "/")
	if idx < 0 {
		return "."
	}
	if idx == 0 {
		return "/"
	}
	return path[:idx]
}

type analysisQuery struct {
	Output string
	SQL    string
}

var analysisQueries = []analysisQuery{
	{"dataset", "SELECT CONCAT(ROUND(SUM(data_length)/(1024*1024*1024),2),'G') DATA, CONCAT(ROUND(SUM(index_length)/(1024*1024*1024),2),'G') INDEXES, CONCAT(ROUND((SUM(data_length)+SUM(index_length))/(1024*1024*1024),2),'G') TOTAL_SIZE FROM information_schema.TABLES"},
	{"top10_large_tables", "SELECT CONCAT(table_schema,'.',table_name) AS tbl, engine, table_rows, ROUND(data_length/1024/1024/1024,2) data_gb, ROUND(index_length/1024/1024/1024,2) index_gb, ROUND((data_length+index_length)/1024/1024/1024,2) total_gb FROM information_schema.TABLES ORDER BY data_length+index_length DESC LIMIT 10"},
	{"data_usage_by_schema", "SELECT table_schema, ROUND(SUM(data_length+index_length)/1024/1024/1024,2) total_gb, ROUND(SUM(data_length)/1024/1024/1024,2) data_gb, ROUND(SUM(index_length)/1024/1024/1024,2) index_gb FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys') AND table_type <> 'VIEW' GROUP BY table_schema ORDER BY total_gb DESC"},
	{"data_usage_by_storage_engine", "SELECT engine, COUNT(*) tables, ROUND(SUM(data_length)/1024/1024/1024,2) data_gb, ROUND(SUM(index_length)/1024/1024/1024,2) index_gb, ROUND(SUM(data_length+index_length)/1024/1024/1024,2) total_gb FROM information_schema.TABLES WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY engine"},
	{"tables_without_primarykey", "SELECT t.table_schema, t.table_name, t.table_rows FROM information_schema.tables t LEFT JOIN information_schema.statistics s ON t.table_schema=s.table_schema AND t.table_name=s.table_name AND s.index_name='PRIMARY' WHERE s.table_name IS NULL AND t.table_type='BASE TABLE' AND t.engine='InnoDB'"},
	{"tables_with_partitions", "SELECT COUNT(*) partitions, table_schema, table_name, partition_expression FROM information_schema.PARTITIONS WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY table_schema, table_name, partition_expression HAVING COUNT(*) > 1"},
	{"mariadb_autoincrement_fill", "SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.column_type, t.auto_increment FROM information_schema.columns c JOIN information_schema.tables t USING (table_schema, table_name) WHERE c.extra='auto_increment' AND c.table_schema NOT IN ('mysql','information_schema','performance_schema','sys') ORDER BY t.auto_increment DESC"},
	{"mariadb_full_text", "SELECT table_schema, table_name, column_name FROM information_schema.statistics WHERE index_type='FULLTEXT'"},
	{"mariadb_innodb_row_format", "SELECT table_schema, table_name, row_format FROM information_schema.tables WHERE engine='InnoDB'"},
	{"mariadb_innodb_free_space", "SELECT table_schema, table_name, ROUND(data_free/1024/1024,2) data_free_mb FROM information_schema.tables WHERE engine='InnoDB' AND data_free > 100*1024*1024"},
	{"non_local_root_accounts", "SELECT user, host FROM mysql.user WHERE user='root' AND host NOT IN ('localhost','127.0.0.1','::1')"},
	{"anonymous_accounts", "SELECT user, host FROM mysql.user WHERE user=''"},
	{"accounts_from_any_host", "SELECT user, host FROM mysql.user WHERE host='%' OR host=''"},
	{"pfs_hosts_blocked", "SELECT IP, HOST, COUNT_HOST_BLOCKED_ERRORS FROM performance_schema.host_cache WHERE COUNT_HOST_BLOCKED_ERRORS > 0"},
	{"pfs_tmp_to_disk", "SELECT schema_name, SUBSTR(digest_text,1,120) statement, count_star, sum_created_tmp_disk_tables, sum_created_tmp_tables FROM performance_schema.events_statements_summary_by_digest WHERE sum_created_tmp_disk_tables > 0 OR sum_created_tmp_tables > 0 ORDER BY sum_created_tmp_disk_tables DESC LIMIT 20"},
}
