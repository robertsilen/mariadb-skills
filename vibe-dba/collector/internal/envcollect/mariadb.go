package envcollect

import (
	"fmt"
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
		ID:        "125-mariadb-vector-indexes",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeDB },
		Run:       collectVectorIndexes,
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

// Vector index options (11.7+) are not exposed through information_schema: the
// per-index DISTANCE appears only in SHOW CREATE TABLE. An index that does not
// name one silently inherits mhnsw_default_distance, so two indexes in the same
// server can rank by different metrics — which fails as subtly wrong results
// rather than as an error. Capture the definitions on their own so the report
// does not have to dig them out of the schema dump.
func collectVectorIndexes(ctx *collector.Context) error {
	out, err := ctx.MariaDBRaw("SELECT DISTINCT CONCAT(table_schema,'.',table_name) FROM information_schema.statistics WHERE index_type='VECTOR'", "-N", "-B")
	if err != nil {
		return nil
	}
	var b strings.Builder
	for _, line := range strings.Split(string(out), "\n") {
		table := strings.TrimSpace(line)
		if table == "" {
			continue
		}
		def, err := ctx.MariaDBRaw("SHOW CREATE TABLE "+table+"\\G", "-B")
		if err != nil {
			continue
		}
		fmt.Fprintf(&b, "##### %s #####\n", table)
		for _, defLine := range strings.Split(string(def), "\n") {
			trimmed := strings.TrimSpace(defLine)
			if strings.Contains(trimmed, "vector(") || strings.Contains(strings.ToUpper(trimmed), "VECTOR KEY") {
				fmt.Fprintf(&b, "%s\n", trimmed)
			}
		}
		b.WriteString("\n")
	}
	if b.Len() == 0 {
		return nil
	}
	return ctx.WriteFile("mariadb_vector_indexes", []byte(b.String()))
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

	// Object inventory. Counts of things the schema dump contains only as DDL
	// text, plus schemas that hold no tables and so never appear in a size query.
	{"object_counts", "SELECT 'schemas' AS object, COUNT(*) AS count FROM information_schema.schemata WHERE schema_name NOT IN ('mysql','information_schema','performance_schema','sys') UNION ALL SELECT 'base_tables', COUNT(*) FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') UNION ALL SELECT 'views', COUNT(*) FROM information_schema.views WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') UNION ALL SELECT 'routines', COUNT(*) FROM information_schema.routines WHERE routine_schema NOT IN ('mysql','sys') UNION ALL SELECT 'triggers', COUNT(*) FROM information_schema.triggers WHERE trigger_schema NOT IN ('mysql','sys') UNION ALL SELECT 'events', COUNT(*) FROM information_schema.events WHERE event_schema NOT IN ('mysql','sys') UNION ALL SELECT 'sequences', COUNT(*) FROM information_schema.tables WHERE table_type='SEQUENCE' UNION ALL SELECT 'user_accounts', COUNT(*) FROM mysql.user UNION ALL SELECT 'estimated_rows', COALESCE(SUM(table_rows),0) FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys')"},
	{"schemas_all", "SELECT s.schema_name, s.default_character_set_name, s.default_collation_name, COUNT(t.table_name) AS tables FROM information_schema.schemata s LEFT JOIN information_schema.tables t ON t.table_schema=s.schema_name AND t.table_type='BASE TABLE' WHERE s.schema_name NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY s.schema_name, s.default_character_set_name, s.default_collation_name ORDER BY s.schema_name"},

	// MariaDB feature adoption. Returns only what is in use; the report derives
	// "available but not in use" by subtracting from the known feature list.
	// JSON columns are stored as LONGTEXT with a json_valid() CHECK constraint,
	// so they cannot be found through information_schema.columns.data_type.
	{"mariadb_features_in_use", "SELECT 'VECTOR columns' AS feature, CONCAT(table_schema,'.',table_name,'.',column_name) AS location FROM information_schema.columns WHERE data_type='vector' " +
		"UNION ALL SELECT 'System-versioned tables', CONCAT(table_schema,'.',table_name) FROM information_schema.tables WHERE table_type='SYSTEM VERSIONED' " +
		"UNION ALL SELECT 'Sequences', CONCAT(table_schema,'.',table_name) FROM information_schema.tables WHERE table_type='SEQUENCE' " +
		"UNION ALL SELECT 'INET4/INET6/UUID columns', CONCAT(table_schema,'.',table_name,'.',column_name) FROM information_schema.columns WHERE data_type IN ('inet4','inet6','uuid') AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'JSON columns', CONCAT(constraint_schema,'.',table_name,'.',constraint_name) FROM information_schema.check_constraints WHERE check_clause LIKE '%json_valid%' AND constraint_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'CHECK constraints', CONCAT(constraint_schema,'.',table_name,'.',constraint_name) FROM information_schema.check_constraints WHERE check_clause NOT LIKE '%json_valid%' AND constraint_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Generated columns', CONCAT(table_schema,'.',table_name,'.',column_name) FROM information_schema.columns WHERE is_generated<>'NEVER' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Invisible columns', CONCAT(table_schema,'.',table_name,'.',column_name) FROM information_schema.columns WHERE extra LIKE '%INVISIBLE%' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Full-text indexes', CONCAT(table_schema,'.',table_name,'.',index_name) FROM information_schema.statistics WHERE index_type='FULLTEXT' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Spatial indexes', CONCAT(table_schema,'.',table_name,'.',index_name) FROM information_schema.statistics WHERE index_type='SPATIAL' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Vector indexes', CONCAT(table_schema,'.',table_name,'.',index_name) FROM information_schema.statistics WHERE index_type='VECTOR' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Triggers', CONCAT(trigger_schema,'.',trigger_name) FROM information_schema.triggers WHERE trigger_schema NOT IN ('mysql','sys') " +
		"UNION ALL SELECT 'Views', CONCAT(table_schema,'.',table_name) FROM information_schema.views WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Stored routines', CONCAT(routine_schema,'.',routine_name) FROM information_schema.routines WHERE routine_schema NOT IN ('mysql','sys') " +
		"UNION ALL SELECT 'Events', CONCAT(event_schema,'.',event_name) FROM information_schema.events WHERE event_schema NOT IN ('mysql','sys') " +
		"UNION ALL SELECT DISTINCT 'Partitioned tables', CONCAT(table_schema,'.',table_name) FROM information_schema.partitions WHERE partition_name IS NOT NULL AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Page compression', CONCAT(table_schema,'.',table_name) FROM information_schema.tables WHERE create_options LIKE '%PAGE_COMPRESSED%' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') " +
		"UNION ALL SELECT 'Non-InnoDB engines', CONCAT(table_schema,'.',table_name,' (',engine,')') FROM information_schema.tables WHERE engine NOT IN ('InnoDB') AND engine IS NOT NULL AND table_type='BASE TABLE' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys')"},

	// Tables large enough that a missing secondary index means full scans.
	{"tables_without_secondary_index", "SELECT t.table_schema, t.table_name, t.table_rows, ROUND((t.data_length+t.index_length)/1024/1024,1) AS size_mb FROM information_schema.tables t WHERE t.table_type='BASE TABLE' AND t.table_schema NOT IN ('mysql','information_schema','performance_schema','sys') AND t.table_rows > 10000 AND NOT EXISTS (SELECT 1 FROM information_schema.statistics s WHERE s.table_schema=t.table_schema AND s.table_name=t.table_name AND s.index_name<>'PRIMARY') ORDER BY t.table_rows DESC"},

	// Security. mysql.user is a view over global_priv on MariaDB 10.4+.
	{"security_accounts", "SELECT user, host, plugin, password_expired, is_role FROM mysql.user ORDER BY user, host"},
	{"security_admin_grants", "SELECT user, host, CONCAT_WS(',', IF(Super_priv='Y','SUPER',NULL), IF(File_priv='Y','FILE',NULL), IF(Process_priv='Y','PROCESS',NULL), IF(Shutdown_priv='Y','SHUTDOWN',NULL), IF(Reload_priv='Y','RELOAD',NULL), IF(Create_user_priv='Y','CREATE USER',NULL), IF(Grant_priv='Y','GRANT OPTION',NULL)) AS admin_privileges FROM mysql.user WHERE user<>'root' AND is_role='N' AND (Super_priv='Y' OR File_priv='Y' OR Process_priv='Y' OR Shutdown_priv='Y' OR Reload_priv='Y' OR Create_user_priv='Y' OR Grant_priv='Y')"},
	{"security_shared_passwords", "SELECT GROUP_CONCAT(CONCAT(user,'@',host)) AS accounts, COUNT(*) AS sharing FROM mysql.user WHERE authentication_string<>'' AND is_role='N' GROUP BY authentication_string HAVING COUNT(*) > 1"},
}
