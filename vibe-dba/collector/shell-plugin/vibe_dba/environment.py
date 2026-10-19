"""One-off environment collection: hardware, OS, configuration, schema, accounts."""

import collections
import os
import platform

from vibe_dba.common import quote_identifier


def collectors(ctx):
    host = ctx.host_info
    return [
        ("005-hardware", host, collect_hardware),
        ("010-system-files", host, collect_system_files),
        ("020-system-commands", host, collect_system_commands),
        ("030-cron", host, collect_cron),
        ("100-mariadb-snapshots", True, collect_mariadb_snapshots),
        ("110-mariadb-schema-and-usage", True, collect_schema_and_usage),
        ("120-mariadb-analysis-queries", True, collect_analysis_queries),
        ("125-mariadb-vector-indexes", True, collect_vector_indexes),
        ("130-mariadb-config-files", host, collect_config_files),
        ("200-native-summary", host, collect_native_system_summary),
        ("210-native-mariadb-summary", True, collect_native_mariadb_summary),
        ("220-native-duplicate-indexes", True, collect_native_duplicate_indexes),
        ("230-native-grants", True, collect_native_grants),
    ]


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------
#
# Hardware identity is normalised at collection time into a single tab-separated
# key/value file, so the report has one parser regardless of the platform the
# collector ran on. Total memory feeds the buffer-pool-to-RAM ratio; without it
# that line silently disappears from the report.

GO_ARCH = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def _trimmed(ctx, args):
    out, error = ctx.command_output(args)
    return "" if error else out.strip()


def collect_hardware(ctx):
    facts = {}
    system = platform.system().lower()
    if system == "darwin":
        _hardware_darwin(ctx, facts)
    else:
        _hardware_linux(ctx, facts)

    if not facts.get("cpu_cores"):
        facts["cpu_cores"] = str(os.cpu_count() or "")
    machine = platform.machine().lower()
    facts["platform"] = f"{system}/{GO_ARCH.get(machine, machine)}"

    keys = ("platform", "os_name", "os_version", "kernel", "cpu_model",
            "cpu_cores", "memory_bytes", "disk_root_bytes", "disk_root_ssd")
    ctx.write("hardware", "".join(
        f"{k}\t{facts[k].strip()}\n" for k in keys if facts.get(k, "").strip()))


def _key_values(text, sep):
    for line in text.splitlines():
        name, found, value = line.partition(sep)
        if found:
            yield name.strip(), value.strip()


def _hardware_darwin(ctx, facts):
    facts["cpu_model"] = _trimmed(ctx, ["sysctl", "-n", "machdep.cpu.brand_string"])
    facts["cpu_cores"] = _trimmed(ctx, ["sysctl", "-n", "hw.ncpu"])
    facts["memory_bytes"] = _trimmed(ctx, ["sysctl", "-n", "hw.memsize"])
    facts["kernel"] = _trimmed(ctx, ["uname", "-sr"])

    for name, value in _key_values(_trimmed(ctx, ["sw_vers"]), ":"):
        if name == "ProductName":
            facts["os_name"] = value
        elif name == "ProductVersion":
            facts["os_version"] = value

    # Root filesystem size and whether it is solid state.
    for name, value in _key_values(_trimmed(ctx, ["diskutil", "info", "/"]), ":"):
        if name in ("Disk Size", "Volume Total Space"):
            size = _bytes_in_parens(value)
            if size and not facts.get("disk_root_bytes"):
                facts["disk_root_bytes"] = size
        elif name == "Solid State":
            facts["disk_root_ssd"] = value.lower()

    # Raw captures, for anyone who wants more than the normalised facts.
    for name, *args in (
        ("sw_vers", "sw_vers"),
        ("vm_stat", "vm_stat"),
        ("diskutil_info", "diskutil", "info", "/"),
        ("system_profiler_hardware", "system_profiler", "SPHardwareDataType"),
        ("sysctl_hw", "sysctl", "hw"),
    ):
        ctx.capture_command(name, args)


def _hardware_linux(ctx, facts):
    # x86 reports "model name"; ARM reports none of that, so count "processor"
    # lines for cores and fall back to lscpu for the model.
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            cpuinfo = f.read()
    except OSError:
        cpuinfo = ""
    cores = 0
    for name, value in _key_values(cpuinfo, ":"):
        if name == "processor":
            cores += 1
        elif name in ("model name", "Model", "Hardware", "cpu model"):
            if not facts.get("cpu_model") and value:
                facts["cpu_model"] = value
    if cores:
        facts["cpu_cores"] = str(cores)

    if not facts.get("cpu_model"):
        for name, value in _key_values(_trimmed(ctx, ["lscpu"]), ":"):
            if name == "Model name":
                if value and value != "-":
                    facts["cpu_model"] = value
                break

    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    fields = line.split()
                    if len(fields) >= 2:
                        facts["memory_bytes"] = str(int(float(fields[1]) * 1024))
                    break
    except (OSError, ValueError):
        pass

    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for name, value in _key_values(f.read(), "="):
                value = value.strip('"')
                if name == "NAME":
                    facts["os_name"] = value
                elif name == "VERSION_ID":
                    facts["os_version"] = value
    except OSError:
        pass

    facts["kernel"] = _trimmed(ctx, ["uname", "-sr"])


def _bytes_in_parens(value):
    """Pull the byte count out of "494.4 GB (494384795648 Bytes) (exactly ...)"."""
    while True:
        start = value.find("(")
        if start < 0:
            return ""
        end = value.find(")", start)
        if end < 0:
            return ""
        fields = value[start + 1:end].split()
        if len(fields) >= 2 and fields[1].lower() == "bytes" and fields[0].isdigit():
            return fields[0]
        value = value[end:]


# ---------------------------------------------------------------------------
# Operating system
# ---------------------------------------------------------------------------

SYSTEM_FILES = {
    "cpuinfo": "/proc/cpuinfo",
    "meminfo": "/proc/meminfo",
    "swappiness": "/proc/sys/vm/swappiness",
    "mounts": "/proc/mounts",
    "diskstats": "/proc/diskstats",
    "interrupts": "/proc/interrupts",
    "softirqs": "/proc/softirqs",
    "cmdline": "/proc/cmdline",
    "os-release": "/etc/os-release",
    "limits.conf": "/etc/security/limits.conf",
    "systemd-limits": "/proc/1/limits",
}

SYSTEM_COMMANDS = (
    ("hostnamectl", ["hostnamectl"]),
    ("uname", ["uname", "-a"]),
    ("uptime", ["uptime"]),
    ("lscpu", ["lscpu"]),
    ("lsblk", ["lsblk", "-a", "-o", "NAME,MAJ:MIN,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,ROTA,SCHED,DISC-MAX"]),
    ("blkid", ["blkid"]),
    ("df", ["df", "-hT"]),
    ("free", ["free", "-m"]),
    ("sysctl", ["sysctl", "-a"]),
    ("dmesg", ["dmesg"]),
    ("lspci", ["lspci"]),
    ("ip_addr", ["ip", "addr"]),
    ("ip_route", ["ip", "route"]),
    ("ss", ["ss", "-s"]),
    ("netstat", ["netstat", "-s"]),
    ("numa", ["numactl", "--hardware"]),
    ("numastat", ["numastat", "-m"]),
    ("dmidecode", ["dmidecode"]),
    ("lvdisplay", ["lvdisplay"]),
    ("vgdisplay", ["vgdisplay"]),
    ("pvdisplay", ["pvdisplay"]),
    ("lvs_segments", ["lvs", "--segments"]),
)


def collect_system_files(ctx):
    for name, path in SYSTEM_FILES.items():
        ctx.capture_file(name, path)


def collect_system_commands(ctx):
    for name, args in SYSTEM_COMMANDS:
        ctx.log(f"capture command {name}")
        ctx.capture_command(name, args)
    if os.path.exists("/dev/mapper"):
        ctx.capture_command("dev_mapper", ["ls", "-alhs", "/dev/mapper"])


def collect_cron(ctx):
    parts = []
    for root in ("/var/spool/cron", "/etc/cron.d"):
        for dirpath, _, files in sorted(os.walk(root)):
            for name in sorted(files):
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        parts.append(f"##### {path} #####\n{f.read()}\n")
                except OSError:
                    pass
    try:
        with open("/etc/crontab", encoding="utf-8", errors="replace") as f:
            parts.append(f"##### /etc/crontab #####\n{f.read()}\n")
    except OSError:
        pass
    ctx.write("crontabs", "".join(parts))


# ---------------------------------------------------------------------------
# MariaDB
# ---------------------------------------------------------------------------


def collect_mariadb_snapshots(ctx):
    for name, sql, mode in (
        ("mariadb_collect_start", "SELECT NOW()", "plain"),
        ("mariadb_version", "SELECT VERSION(), @@version_comment", "batch"),
        ("mariadb_variables", "SHOW GLOBAL VARIABLES", "plain"),
        ("mariadb_global_status", "SHOW GLOBAL STATUS", "plain"),
        ("mariadb_innodb_status", "SHOW ENGINE INNODB STATUS", "vertical"),
        ("mariadb_plugins", "SHOW PLUGINS", "batch"),
        ("mariadb_engines", "SHOW ENGINES", "batch"),
        ("mariadb_replica_status", "SHOW SLAVE STATUS", "vertical"),
        ("mariadb_master_status", "SHOW MASTER STATUS", "vertical"),
        ("mariadb_collect_end", "SELECT NOW()", "plain"),
    ):
        ctx.capture_query(name, sql, mode)


def collect_schema_and_usage(ctx):
    ctx.write("mariadb_schema", schema_ddl(ctx))

    datadir = ctx.scalar("SELECT @@datadir")
    if datadir:
        ctx.write("datadir", datadir + "\n")
        if ctx.host_info:
            ctx.capture_command("data_usage_raw", ["du", "-sh", datadir])
            ctx.capture_command("data_usage_innodb_raw",
                                ["find", datadir, "-name", "*.ibd", "-o", "-name", "ibdata*"])


def schema_ddl(ctx):
    """All object definitions, in place of `mariadb-dump --no-data`.

    Read through the shell's own session so that no client binary is needed on
    the server. Covers what the dump options did: tables, views, sequences,
    triggers, routines and events, for every schema the dump would include.
    """
    out = ["-- Schema definitions collected by the vibeDba MariaDB Shell plugin\n"]

    def show(sql, column=1):
        _, rows = ctx.query(sql)
        return _text_value(rows[0][column]) if rows else ""

    def emit(label, sql, column=1):
        try:
            out.append(show(sql, column).rstrip(";\n") + ";\n\n")
        except Exception as error:
            out.append(f"-- ERROR reading {label}: {error}\n\n")

    _, schemas = ctx.query("SHOW DATABASES")
    for (schema,) in schemas:
        if schema in ("information_schema", "performance_schema", "sys"):
            continue
        q = quote_identifier(schema)
        out.append(f"--\n-- Current Database: {q}\n--\n\n")
        emit(f"database {schema}", f"SHOW CREATE DATABASE {q}")
        out.append(f"USE {q};\n\n")

        _, tables = ctx.query(
            "SELECT table_name, table_type FROM information_schema.tables "
            f"WHERE table_schema = '{_sql_string(schema)}' "
            "ORDER BY table_type = 'VIEW', table_name")
        for name, kind in tables:
            obj = f"{q}.{quote_identifier(name)}"
            if kind == "VIEW":
                emit(f"view {schema}.{name}", f"SHOW CREATE VIEW {obj}")
            elif kind == "SEQUENCE":
                emit(f"sequence {schema}.{name}", f"SHOW CREATE SEQUENCE {obj}")
            else:
                emit(f"table {schema}.{name}", f"SHOW CREATE TABLE {obj}")

        _, triggers = ctx.query(f"SHOW TRIGGERS FROM {q}")
        for row in triggers:
            emit(f"trigger {schema}.{row[0]}",
                 f"SHOW CREATE TRIGGER {q}.{quote_identifier(row[0])}", 2)

        _, routines = ctx.query(
            "SELECT routine_name, routine_type FROM information_schema.routines "
            f"WHERE routine_schema = '{_sql_string(schema)}' ORDER BY routine_type, routine_name")
        for name, kind in routines:
            emit(f"{kind.lower()} {schema}.{name}",
                 f"SHOW CREATE {kind} {q}.{quote_identifier(name)}", 2)

        _, events = ctx.query(
            "SELECT event_name FROM information_schema.events "
            f"WHERE event_schema = '{_sql_string(schema)}' ORDER BY event_name")
        for (name,) in events:
            emit(f"event {schema}.{name}", f"SHOW CREATE EVENT {q}.{quote_identifier(name)}", 3)
    return "".join(out)


def _sql_string(value):
    return str(value).replace("\\", "\\\\").replace("'", "''")


def _text_value(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return "" if value is None else str(value)


def collect_analysis_queries(ctx):
    for name, sql in ANALYSIS_QUERIES:
        ctx.write(name, "-----" + sql + "=====\n" + ctx.query_text(sql, "batch"))


def collect_vector_indexes(ctx):
    # Vector index options (11.7+) are not exposed through information_schema:
    # the per-index DISTANCE appears only in SHOW CREATE TABLE. An index that
    # does not name one silently inherits mhnsw_default_distance, so two indexes
    # in the same server can rank by different metrics — which fails as subtly
    # wrong results rather than as an error. Capture the definitions on their
    # own so the report does not have to dig them out of the schema dump.
    try:
        _, tables = ctx.query(
            "SELECT DISTINCT table_schema, table_name FROM information_schema.statistics "
            "WHERE index_type='VECTOR'")
    except Exception:
        return
    parts = []
    for schema, table in tables:
        try:
            _, rows = ctx.query(
                f"SHOW CREATE TABLE {quote_identifier(schema)}.{quote_identifier(table)}")
        except Exception:
            continue
        parts.append(f"##### {schema}.{table} #####\n")
        for line in _text_value(rows[0][1]).splitlines():
            trimmed = line.strip()
            if "vector(" in trimmed or "VECTOR KEY" in trimmed.upper():
                parts.append(trimmed + "\n")
        parts.append("\n")
    if parts:
        ctx.write("mariadb_vector_indexes", "".join(parts))


def collect_config_files(ctx):
    for candidate in ("/etc/my.cnf", "/etc/mysql/my.cnf", "/etc/mysql/mariadb.cnf"):
        try:
            with open(candidate, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        ctx.write(candidate.strip("/").replace("/", "_"), content)
        _copy_includes(ctx, candidate, content)

    error_log = ctx.scalar("SELECT @@log_error")
    if error_log:
        if not os.path.isabs(error_log):
            error_log = os.path.join(ctx.scalar("SELECT @@datadir"), error_log)
        try:
            with open(error_log, encoding="utf-8", errors="replace") as f:
                tail = collections.deque(f, maxlen=1000)
            ctx.write("error_log_tail", "".join(tail))
        except OSError as error:
            ctx.write("error_log_tail", f"\nERROR: {error}\n")
    ctx.capture_command("tmpdir_lsof_deleted",
                        "pidof mysqld mariadbd | xargs -r lsof -p | grep deleted", shell=True)


def _copy_includes(ctx, base_path, content):
    def capture(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                ctx.write("included_" + path.lstrip("/").replace("/", "_"), f.read())
        except OSError:
            pass

    for line in content.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        if fields[0] == "!include":
            path = fields[1]
            if not path.startswith("/"):
                path = os.path.join(os.path.dirname(base_path), path)
            capture(path)
        elif fields[0] == "!includedir":
            try:
                entries = sorted(os.listdir(fields[1]))
            except OSError:
                continue
            for entry in entries:
                path = os.path.join(fields[1], entry)
                if os.path.isfile(path):
                    capture(path)


# ---------------------------------------------------------------------------
# Summaries that replace the Percona Toolkit tools
# ---------------------------------------------------------------------------


def collect_native_system_summary(ctx):
    sections = (
        ("Kernel", ["uname", "-a"]),
        ("Uptime", ["uptime"]),
        ("CPU", ["lscpu"]),
        ("Memory", ["free", "-m"]),
        ("Filesystems", ["df", "-hT"]),
        ("Block Devices", ["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,ROTA,SCHED"]),
        ("Network Addresses", ["ip", "addr"]),
        ("Network Routes", ["ip", "route"]),
        ("Sysctl VM", ["sysctl", "vm.swappiness", "vm.dirty_ratio",
                       "vm.dirty_background_ratio", "vm.overcommit_memory"]),
    )
    parts = []
    for title, args in sections:
        out, error = ctx.command_output(args)
        parts.append(f"##### {title} #####\n{out}")
        if error:
            parts.append(f"ERROR: {error}\n")
        parts.append("\n")
    ctx.write("native-system-summary", "".join(parts))


def collect_native_mariadb_summary(ctx):
    queries = (
        ("Version", "SELECT VERSION() version, @@version_comment version_comment, @@hostname hostname, @@port port, @@socket socket"),
        ("Important Variables", "SHOW VARIABLES WHERE Variable_name IN ('basedir','datadir','tmpdir','log_error','innodb_buffer_pool_size','innodb_log_file_size','innodb_flush_log_at_trx_commit','sync_binlog','max_connections','table_open_cache','open_files_limit','performance_schema')"),
        ("Important Status", "SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime','Threads_connected','Threads_running','Connections','Max_used_connections','Questions','Queries','Slow_queries','Created_tmp_disk_tables','Created_tmp_tables','Innodb_buffer_pool_reads','Innodb_buffer_pool_read_requests','Innodb_log_waits','Innodb_row_lock_waits')"),
        ("Schema Sizes", "SELECT table_schema, ROUND(SUM(data_length+index_length)/1024/1024/1024,2) total_gb, COUNT(*) tables FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY table_schema ORDER BY total_gb DESC"),
        ("Engine Counts", "SELECT engine, COUNT(*) tables FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY engine ORDER BY tables DESC"),
    )
    ctx.write("native-mariadb-summary", "".join(
        f"##### {title} #####\n{sql}\n{ctx.query_text(sql)}\n" for title, sql in queries))


DUPLICATE_INDEXES_SQL = """
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
ORDER BY s1.table_schema, s1.table_name, s1.index_name
"""


def collect_native_duplicate_indexes(ctx):
    ctx.write("native-duplicate-indexes",
              "----- duplicate or left-prefix covered indexes -----\n"
              + ctx.query_text(DUPLICATE_INDEXES_SQL))


def collect_native_grants(ctx):
    try:
        _, users = ctx.query(
            "SELECT CONCAT(QUOTE(user),'@',QUOTE(host)) FROM mysql.user ORDER BY user, host")
    except Exception as error:
        ctx.write("native-show-grants", f"\nERROR: {error}\n")
        return
    parts = []
    for (account,) in users:
        sql = f"SHOW GRANTS FOR {account}"
        parts.append(f"##### {sql} #####\n{ctx.query_text(sql, 'plain')}\n")
    ctx.write("native-show-grants", "".join(parts))


# ---------------------------------------------------------------------------
# Analysis queries — written as "-----<SQL>=====" followed by a -B result set.
# ---------------------------------------------------------------------------

ANALYSIS_QUERIES = (
    ("dataset", "SELECT CONCAT(ROUND(SUM(data_length)/(1024*1024*1024),2),'G') DATA, CONCAT(ROUND(SUM(index_length)/(1024*1024*1024),2),'G') INDEXES, CONCAT(ROUND((SUM(data_length)+SUM(index_length))/(1024*1024*1024),2),'G') TOTAL_SIZE FROM information_schema.TABLES"),
    ("top10_large_tables", "SELECT CONCAT(table_schema,'.',table_name) AS tbl, engine, table_rows, ROUND(data_length/1024/1024/1024,2) data_gb, ROUND(index_length/1024/1024/1024,2) index_gb, ROUND((data_length+index_length)/1024/1024/1024,2) total_gb FROM information_schema.TABLES ORDER BY data_length+index_length DESC LIMIT 10"),
    ("data_usage_by_schema", "SELECT table_schema, ROUND(SUM(data_length+index_length)/1024/1024/1024,2) total_gb, ROUND(SUM(data_length)/1024/1024/1024,2) data_gb, ROUND(SUM(index_length)/1024/1024/1024,2) index_gb FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys') AND table_type <> 'VIEW' GROUP BY table_schema ORDER BY total_gb DESC"),
    ("data_usage_by_storage_engine", "SELECT engine, COUNT(*) tables, ROUND(SUM(data_length)/1024/1024/1024,2) data_gb, ROUND(SUM(index_length)/1024/1024/1024,2) index_gb, ROUND(SUM(data_length+index_length)/1024/1024/1024,2) total_gb FROM information_schema.TABLES WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY engine"),
    ("tables_without_primarykey", "SELECT t.table_schema, t.table_name, t.table_rows FROM information_schema.tables t LEFT JOIN information_schema.statistics s ON t.table_schema=s.table_schema AND t.table_name=s.table_name AND s.index_name='PRIMARY' WHERE s.table_name IS NULL AND t.table_type='BASE TABLE' AND t.engine='InnoDB'"),
    ("tables_with_partitions", "SELECT COUNT(*) partitions, table_schema, table_name, partition_expression FROM information_schema.PARTITIONS WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY table_schema, table_name, partition_expression HAVING COUNT(*) > 1"),
    ("mariadb_autoincrement_fill", "SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.column_type, t.auto_increment FROM information_schema.columns c JOIN information_schema.tables t USING (table_schema, table_name) WHERE c.extra='auto_increment' AND c.table_schema NOT IN ('mysql','information_schema','performance_schema','sys') ORDER BY t.auto_increment DESC"),
    ("mariadb_full_text", "SELECT table_schema, table_name, column_name FROM information_schema.statistics WHERE index_type='FULLTEXT'"),
    ("mariadb_innodb_row_format", "SELECT table_schema, table_name, row_format FROM information_schema.tables WHERE engine='InnoDB'"),
    ("mariadb_innodb_free_space", "SELECT table_schema, table_name, ROUND(data_free/1024/1024,2) data_free_mb FROM information_schema.tables WHERE engine='InnoDB' AND data_free > 100*1024*1024"),
    ("non_local_root_accounts", "SELECT user, host FROM mysql.user WHERE user='root' AND host NOT IN ('localhost','127.0.0.1','::1')"),
    ("anonymous_accounts", "SELECT user, host FROM mysql.user WHERE user=''"),
    ("accounts_from_any_host", "SELECT user, host FROM mysql.user WHERE host='%' OR host=''"),
    ("pfs_hosts_blocked", "SELECT IP, HOST, COUNT_HOST_BLOCKED_ERRORS FROM performance_schema.host_cache WHERE COUNT_HOST_BLOCKED_ERRORS > 0"),
    ("pfs_tmp_to_disk", "SELECT schema_name, SUBSTR(digest_text,1,120) statement, count_star, sum_created_tmp_disk_tables, sum_created_tmp_tables FROM performance_schema.events_statements_summary_by_digest WHERE sum_created_tmp_disk_tables > 0 OR sum_created_tmp_tables > 0 ORDER BY sum_created_tmp_disk_tables DESC LIMIT 20"),

    # Object inventory. Counts of things the schema dump contains only as DDL
    # text, plus schemas that hold no tables and so never appear in a size query.
    ("object_counts", "SELECT 'schemas' AS object, COUNT(*) AS count FROM information_schema.schemata WHERE schema_name NOT IN ('mysql','information_schema','performance_schema','sys') UNION ALL SELECT 'base_tables', COUNT(*) FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') UNION ALL SELECT 'views', COUNT(*) FROM information_schema.views WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') UNION ALL SELECT 'routines', COUNT(*) FROM information_schema.routines WHERE routine_schema NOT IN ('mysql','sys') UNION ALL SELECT 'triggers', COUNT(*) FROM information_schema.triggers WHERE trigger_schema NOT IN ('mysql','sys') UNION ALL SELECT 'events', COUNT(*) FROM information_schema.events WHERE event_schema NOT IN ('mysql','sys') UNION ALL SELECT 'sequences', COUNT(*) FROM information_schema.tables WHERE table_type='SEQUENCE' UNION ALL SELECT 'user_accounts', COUNT(*) FROM mysql.user UNION ALL SELECT 'estimated_rows', COALESCE(SUM(table_rows),0) FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys')"),
    ("schemas_all", "SELECT s.schema_name, s.default_character_set_name, s.default_collation_name, COUNT(t.table_name) AS tables FROM information_schema.schemata s LEFT JOIN information_schema.tables t ON t.table_schema=s.schema_name AND t.table_type='BASE TABLE' WHERE s.schema_name NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY s.schema_name, s.default_character_set_name, s.default_collation_name ORDER BY s.schema_name"),

    # MariaDB feature adoption. Returns only what is in use; the report derives
    # "available but not in use" by subtracting from the known feature list.
    # JSON columns are stored as LONGTEXT with a json_valid() CHECK constraint,
    # so they cannot be found through information_schema.columns.data_type.
    ("mariadb_features_in_use", "SELECT 'VECTOR columns' AS feature, CONCAT(table_schema,'.',table_name,'.',column_name) AS location FROM information_schema.columns WHERE data_type='vector' "
        "UNION ALL SELECT 'System-versioned tables', CONCAT(table_schema,'.',table_name) FROM information_schema.tables WHERE table_type='SYSTEM VERSIONED' "
        "UNION ALL SELECT 'Sequences', CONCAT(table_schema,'.',table_name) FROM information_schema.tables WHERE table_type='SEQUENCE' "
        "UNION ALL SELECT 'INET4/INET6/UUID columns', CONCAT(table_schema,'.',table_name,'.',column_name) FROM information_schema.columns WHERE data_type IN ('inet4','inet6','uuid') AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'JSON columns', CONCAT(constraint_schema,'.',table_name,'.',constraint_name) FROM information_schema.check_constraints WHERE check_clause LIKE '%json_valid%' AND constraint_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'CHECK constraints', CONCAT(constraint_schema,'.',table_name,'.',constraint_name) FROM information_schema.check_constraints WHERE check_clause NOT LIKE '%json_valid%' AND constraint_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Generated columns', CONCAT(table_schema,'.',table_name,'.',column_name) FROM information_schema.columns WHERE is_generated<>'NEVER' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Invisible columns', CONCAT(table_schema,'.',table_name,'.',column_name) FROM information_schema.columns WHERE extra LIKE '%INVISIBLE%' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Full-text indexes', CONCAT(table_schema,'.',table_name,'.',index_name) FROM information_schema.statistics WHERE index_type='FULLTEXT' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Spatial indexes', CONCAT(table_schema,'.',table_name,'.',index_name) FROM information_schema.statistics WHERE index_type='SPATIAL' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Vector indexes', CONCAT(table_schema,'.',table_name,'.',index_name) FROM information_schema.statistics WHERE index_type='VECTOR' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Triggers', CONCAT(trigger_schema,'.',trigger_name) FROM information_schema.triggers WHERE trigger_schema NOT IN ('mysql','sys') "
        "UNION ALL SELECT 'Views', CONCAT(table_schema,'.',table_name) FROM information_schema.views WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Stored routines', CONCAT(routine_schema,'.',routine_name) FROM information_schema.routines WHERE routine_schema NOT IN ('mysql','sys') "
        "UNION ALL SELECT 'Events', CONCAT(event_schema,'.',event_name) FROM information_schema.events WHERE event_schema NOT IN ('mysql','sys') "
        "UNION ALL SELECT DISTINCT 'Partitioned tables', CONCAT(table_schema,'.',table_name) FROM information_schema.partitions WHERE partition_name IS NOT NULL AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Page compression', CONCAT(table_schema,'.',table_name) FROM information_schema.tables WHERE create_options LIKE '%PAGE_COMPRESSED%' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
        "UNION ALL SELECT 'Non-InnoDB engines', CONCAT(table_schema,'.',table_name,' (',engine,')') FROM information_schema.tables WHERE engine NOT IN ('InnoDB') AND engine IS NOT NULL AND table_type='BASE TABLE' AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys')"),

    # Tables large enough that a missing secondary index means full scans.
    ("tables_without_secondary_index", "SELECT t.table_schema, t.table_name, t.table_rows, ROUND((t.data_length+t.index_length)/1024/1024,1) AS size_mb FROM information_schema.tables t WHERE t.table_type='BASE TABLE' AND t.table_schema NOT IN ('mysql','information_schema','performance_schema','sys') AND t.table_rows > 10000 AND NOT EXISTS (SELECT 1 FROM information_schema.statistics s WHERE s.table_schema=t.table_schema AND s.table_name=t.table_name AND s.index_name<>'PRIMARY') ORDER BY t.table_rows DESC"),

    # Security. mysql.user is a view over global_priv on MariaDB 10.4+.
    ("security_accounts", "SELECT user, host, plugin, password_expired, is_role FROM mysql.user ORDER BY user, host"),
    ("security_admin_grants", "SELECT user, host, CONCAT_WS(',', IF(Super_priv='Y','SUPER',NULL), IF(File_priv='Y','FILE',NULL), IF(Process_priv='Y','PROCESS',NULL), IF(Shutdown_priv='Y','SHUTDOWN',NULL), IF(Reload_priv='Y','RELOAD',NULL), IF(Create_user_priv='Y','CREATE USER',NULL), IF(Grant_priv='Y','GRANT OPTION',NULL)) AS admin_privileges FROM mysql.user WHERE user<>'root' AND is_role='N' AND (Super_priv='Y' OR File_priv='Y' OR Process_priv='Y' OR Shutdown_priv='Y' OR Reload_priv='Y' OR Create_user_priv='Y' OR Grant_priv='Y')"),
    ("security_shared_passwords", "SELECT GROUP_CONCAT(CONCAT(user,'@',host)) AS accounts, COUNT(*) AS sharing FROM mysql.user WHERE authentication_string<>'' AND is_role='N' GROUP BY authentication_string HAVING COUNT(*) > 1"),
)
