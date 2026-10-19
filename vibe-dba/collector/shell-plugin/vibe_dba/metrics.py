"""Time-series collection over a window: SQL counters, OS streams, disk stats.

Every SQL sample is preceded by a `#TS` marker line so the report can compute
rates from real elapsed time.

The SQL samples share one session and run from a single scheduler loop on the
main thread: shell sessions are not thread-safe, and Ctrl-C reaches Python only
on the main thread. External tools (vmstat, mpstat, iostat) run as
child processes writing plain files, which are compressed when the window ends.
"""

import gzip
import os
import re
import shutil
import subprocess
import time

from vibe_dba.common import timestamp

# (file name, interval in seconds, statement, output mode)
SQL_STREAMS = (
    ("global_status.out.gz", 1, "SHOW GLOBAL STATUS", "plain"),
    # MariaDB exposes ENABLED (0/1); STATUS ('enabled') is the MySQL spelling.
    ("innodb_metrics.out.gz", 1,
     "SELECT name, subsystem, count, enabled FROM information_schema.INNODB_METRICS WHERE enabled=1",
     "batch"),
    ("innodb_status.out.gz", 60, "SHOW ENGINE INNODB STATUS", "vertical"),
    ("processlist.out.gz", 60, "SHOW FULL PROCESSLIST", "vertical"),
)
REPLICATION_STREAM = ("replica_status.out.gz", 1, "SHOW SLAVE STATUS", "plain")

OS_COMMANDS = (
    ("mpstat.out.gz", ["mpstat", "1"]),
    ("vmstat.out.gz", ["vmstat", "1"]),
    ("iostat.out.gz", ["iostat", "-mx", "1"]),
)

_UNITS = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}


def parse_duration(value):
    """Seconds, or None for "ctrlc". Accepts "10m", "1h30m", "90s" or plain
    minutes ("10")."""
    value = str(value).strip().lower()
    if value == "ctrlc":
        return None
    if value.isdigit():
        return int(value) * 60
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value)
    if not parts or "".join(n + u for n, u in parts) != value:
        raise ValueError(f'invalid duration "{value}": use e.g. 10m, 1h30m, 90s or ctrlc')
    return sum(float(n) * _UNITS[u] for n, u in parts)


class _Stream:
    def __init__(self, ctx, name, every, sample):
        self.every = every
        self.sample = sample
        self.file = gzip.open(ctx.path(name), "wt", encoding="utf-8")
        self.due = 0.0

    def write_sample(self, ctx):
        self.file.write(f"#TS {timestamp()}\n")
        try:
            data = self.sample(ctx)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            data = f"ERROR: {error}\n"
        if data:
            self.file.write(data if data.endswith("\n") else data + "\n")


def _sql_sampler(sql, mode):
    return lambda ctx: ctx.query_text(sql, mode)


def _read_diskstats(ctx):
    with open("/proc/diskstats", encoding="utf-8") as f:
        return f.read()


def _start_os_commands(ctx):
    procs = []
    for name, args in OS_COMMANDS:
        if shutil.which(args[0]) is None:
            ctx.record_error(name, f'executable file not found in $PATH: "{args[0]}"')
            continue
        raw = ctx.path(name[:-3])  # vmstat.out, compressed at the end
        out = open(raw, "wb")
        try:
            proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
        except OSError as error:
            out.close()
            ctx.record_error(name, error)
            continue
        procs.append((proc, out, raw))
    return procs


def _stop_os_commands(procs):
    for proc, _, _ in procs:
        proc.terminate()
    for proc, out, raw in procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        out.close()
        with open(raw, "rb") as src, gzip.open(raw + ".gz", "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.remove(raw)


def collect(ctx, seconds, replication):
    """Sample until `seconds` elapse (None: until Ctrl-C). Returns True if the
    window was cut short by Ctrl-C."""
    streams = [_Stream(ctx, name, every, _sql_sampler(sql, mode))
               for name, every, sql, mode in SQL_STREAMS]
    if replication:
        name, every, sql, mode = REPLICATION_STREAM
        streams.append(_Stream(ctx, name, every, _sql_sampler(sql, mode)))

    procs = []
    if ctx.host_info:
        procs = _start_os_commands(ctx)
        if os.path.exists("/proc/diskstats"):
            streams.append(_Stream(ctx, "diskstats.out.gz", 1, _read_diskstats))

    start = time.monotonic()
    end = None if seconds is None else start + seconds
    for stream in streams:
        stream.due = start
    next_progress = start + 60
    interrupted = False

    try:
        while True:
            now = time.monotonic()
            if end is not None and now >= end:
                break
            for stream in streams:
                if now >= stream.due:
                    stream.write_sample(ctx)
                    stream.due += stream.every
                    if stream.due <= now:  # fell behind; do not burst to catch up
                        stream.due = now + stream.every
            if now >= next_progress:
                elapsed = int(now - start) // 60
                total = "" if end is None else f" of {int(seconds) // 60}"
                print(f"  {elapsed}{total} minutes collected", flush=True)
                next_progress += 60
            wake = min(s.due for s in streams)
            if end is not None:
                wake = min(wake, end)
            time.sleep(max(0.0, wake - time.monotonic()))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        for stream in streams:
            stream.file.close()
        _stop_os_commands(procs)
    return interrupted
