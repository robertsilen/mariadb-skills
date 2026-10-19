# Vibe DBA collector as a MariaDB Shell plugin.
#
# Install by linking this directory into the shell's plugin path:
#   ln -s <skill-dir>/collector/shell-plugin/vibe_dba ~/.mariadb-shell/plugins/vibe_dba

import datetime
import os

from mysqlsh.plugin_manager import plugin, plugin_function
from mysqlsh import globals, Error

from vibe_dba import common, environment, metrics


@plugin
class vibeDba:
    """Vibe DBA health-check data collection.

    Collects the data the Vibe DBA skill turns into a health-check report. Both
    functions are read-only: SELECT and SHOW statements against the connected
    server, plus reads of operating-system files and tools when the server runs
    on the same host as the shell.

    * collectEnvironment: a one-off snapshot of configuration, schema, accounts
      and hardware.
    * collectMetrics: counters sampled every second over a time window.

    Generate the report from the output with collector/scripts/mariadb-report.py.
    """


def _prepare(base_dir, kind, name, host_info, verbose):
    session = common.open_collection_session(globals.shell)
    host_info = common.resolve_host_info(globals.shell, session, host_info)
    ctx = common.Context(session, None, host_info, verbose)
    name = name or common.server_hostname(ctx)
    stamp = common.datestamp() if kind == "environment" else common.timestamp()
    ctx.output_dir = os.path.join(base_dir or ".", f"{name}_{kind}_{stamp}")
    os.makedirs(ctx.output_dir, exist_ok=True)
    return ctx


@plugin_function("vibeDba.collectEnvironment", cli=True)
def collect_environment(**options):
    """Collect a one-off snapshot of the server and its host.

    Writes <outputDir>/<hostname>_environment_<date>/ and, by default, packages
    it as a .tgz beside it.

    Args:
        **options (dict): Optional arguments.

    Keyword Args:
        outputDir (str): Directory to write the collection under. Default: the
            current directory.
        hostInfo (bool): Collect operating-system data (hardware, configuration
            files, error log). Only possible when the server runs on this host;
            skipped otherwise. Default true.
        package (bool): Write a .tgz of the collection. Default true.
        cleanup (bool): Remove the collection directory afterwards, keeping
            only the .tgz. Default false.
        verbose (bool): Print each collector as it runs. Default false.
    """
    ctx = _prepare(options.get("outputDir"), "environment", None,
                   options.get("hostInfo", True), options.get("verbose", False))
    print(f"Collecting environment into {ctx.output_dir}")
    try:
        common.write_provenance(ctx, globals.shell, "collectEnvironment")
        ctx.write("collection_start", common.timestamp() + "\n")
        common.run_collectors(ctx, environment.collectors(ctx))
        ctx.write("collection_stop", common.timestamp() + "\n")
    finally:
        ctx.session.close()
    if os.path.exists(ctx.path("collector-errors.log")):
        print("Some collectors failed; see collector-errors.log in the output.")
    common.finalize(ctx.output_dir, options.get("package", True),
                    options.get("cleanup", False))


@plugin_function("vibeDba.collectMetrics", cli=True)
def collect_metrics(**options):
    """Sample server and host counters over a time window.

    Writes <outputDir>/<name>_metrics_<timestamp>/ and, by default, packages it
    as a .tgz beside it. Global status and InnoDB metrics are sampled every
    second, InnoDB status and the process list every minute.

    The window must contain the behaviour being investigated: if the problem
    happens at 17:00, collect across 17:00. Under 5 minutes is too short to
    characterise a workload.

    Args:
        **options (dict): Optional arguments.

    Keyword Args:
        duration (str): How long to collect: 10m, 1h30m, 90s, a plain number of
            minutes, or ctrlc to run until interrupted. ctrlc works from the
            shell's Python mode, not from the command-line integration (--),
            which does not pass Ctrl-C to plugins. Default 5m.
        outputDir (str): Directory to write the collection under. Default: the
            current directory.
        name (str): Name used in the output directory. Default: the server's
            hostname.
        hostInfo (bool): Also sample vmstat, mpstat, iostat and /proc/diskstats.
            Only possible when the server runs on this host; skipped otherwise.
            Default true.
        replication (bool): Also sample SHOW SLAVE STATUS every second.
            Default false.
        package (bool): Write a .tgz of the collection. Default true.
        cleanup (bool): Remove the collection directory afterwards, keeping
            only the .tgz. Default false.
        verbose (bool): Print each collector as it runs. Default false.
    """
    try:
        seconds = metrics.parse_duration(options.get("duration", "5m"))
    except ValueError as error:
        raise Error(str(error))
    ctx = _prepare(options.get("outputDir"), "metrics", options.get("name"),
                   options.get("hostInfo", True), options.get("verbose", False))
    window = "until Ctrl-C" if seconds is None else f"for {options.get('duration', '5m')}"
    print(f"Writing metrics to {ctx.output_dir} {window}")
    try:
        common.write_provenance(ctx, globals.shell, "collectMetrics")
        ctx.write("collection_start", _now() + "\n")
        interrupted = metrics.collect(ctx, seconds, options.get("replication", False))
        ctx.write("collection_stop", _now() + "\n")
    finally:
        ctx.session.close()
    if interrupted and seconds is not None:
        print("Interrupted before the window ended; the shorter window is kept.")
    common.finalize(ctx.output_dir, options.get("package", True),
                    options.get("cleanup", False))


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
