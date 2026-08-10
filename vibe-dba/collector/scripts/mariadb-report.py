#!/usr/bin/env python3
"""
Generate a dark, Grafana-like HTML report from mariadb-metrics output.

The input can be either a collected metrics directory or a .tar/.tgz package.
No third-party Python packages are required.

Time handling
-------------
Every sampled stream is anchored to real wall-clock time:

* Streams written by the collector's periodic sampler carry ``#TS`` marker lines
  and are plotted at their true timestamps, so an uneven or stalled sample
  interval shows up as a stretched gap instead of being silently smoothed away.
* Streams produced by external tools (vmstat, mpstat, iostat) have no embedded
  timestamps. They are spread evenly across the collection window recorded in
  ``collection_start`` / ``collection_stop`` and are labelled as approximate.
* Counter deltas are divided by the real elapsed seconds between samples, so
  rates stay correct even when sampling slips -- which is exactly what happens
  on a loaded server.
"""

from __future__ import annotations

import argparse
import gzip
import html
import math
import re
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


# Number of series colours defined per theme in CSS as --series-1 .. --series-N.
SERIES_COLORS = 8

# Timestamp formats written by the collector.
TS_FORMATS = ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d %H:%M:%S")

# MariaDB Foundation logo. Two variants: the standard one for the light theme and
# the inverted one (white wordmark) for dark, swapped by CSS. Loaded from the web
# so no binary asset is carried in this repository; the alt text keeps the header
# readable when the report is opened offline.
LOGO_BASE = "https://raw.githubusercontent.com/MariaDB/.github/main/assets/logos/png"
LOGO_URL_LIGHT = f"{LOGO_BASE}/mariadb_org_rgb_h.png"
LOGO_URL_DARK = f"{LOGO_BASE}/mariadb_org_inv_rgb_h.png"
LOGO_ALT = "MariaDB Foundation"

REPORT_TITLE = "MariaDB Foundation AI DBA Server Inventory"
INSTALL_COMMANDS = 'git clone https://github.com/MariaDB/skills.git\ncd skills && claude "dba"'

# How a chart's time axis was derived.
TIME_EXACT = "exact"  # from #TS markers in the stream
TIME_EVEN = "even"  # spread evenly across the collection window
TIME_NONE = "none"  # no time information available


@dataclass
class Samples:
    """A parsed stream: one timestamp and one key/value mapping per sample."""

    times: list[datetime | None] = field(default_factory=list)
    rows: list[dict[str, float]] = field(default_factory=list)
    time_source: str = TIME_NONE

    def __len__(self) -> int:
        return len(self.rows)

    def __bool__(self) -> bool:
        return bool(self.rows)


@dataclass
class Chart:
    title: str
    unit: str
    series: dict[str, list[float | None]]
    times: list[datetime | None] = field(default_factory=list)
    time_source: str = TIME_NONE
    kind: str = "line"
    description: str = ""


@dataclass
class Window:
    start: datetime | None = None
    stop: datetime | None = None

    @property
    def duration_s(self) -> float | None:
        if self.start and self.stop:
            return (self.stop - self.start).total_seconds()
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a dark HTML graph report from mariadb-metrics data."
    )
    parser.add_argument(
        "paths", nargs="*",
        help="collection directories or .tar/.tgz packages (environment and/or metrics)")
    parser.add_argument("-o", "--out", help="output HTML file")
    parser.add_argument("--max-points", type=int, default=900, help="max points per series")
    parser.add_argument(
        "--text", action="store_true",
        help="also write a plain-text version of the report next to the HTML")
    parser.add_argument(
        "--annotations",
        help="JSON file of AI or human annotations to merge into the report")
    args = parser.parse_args()

    raw_paths = args.paths or [input("Collection directory or package path: ").strip()]
    inputs = [Path(p) for p in raw_paths if p]
    for path in inputs:
        if not path.exists():
            raise SystemExit(f"input does not exist: {path}")

    workspaces: list[str] = []
    try:
        resolved: list[Path] = []
        for path in inputs:
            if path.is_file() and tarfile.is_tarfile(path):
                workspace = tempfile.mkdtemp(prefix="mariadb-report-")
                workspaces.append(workspace)
                resolved.append(extract_package(path, Path(workspace)))
            else:
                resolved.append(path)

        env_dir = next((d for d in (find_env_dir(p) for p in resolved) if d), None)
        metrics_dir = next((d for d in (find_metrics_dir(p) for p in resolved) if d), None)

        if env_dir is None and metrics_dir is None:
            raise SystemExit(
                "no collection data found — expected a directory containing "
                "mariadb_variables (environment) or global_status.out.gz (metrics)")

        env = load_env(env_dir)
        window = read_window(metrics_dir) if metrics_dir else Window()
        charts = build_charts(metrics_dir, window, args.max_points) if metrics_dir else []
        annotations = load_annotations(Path(args.annotations) if args.annotations else None)

        output = Path(args.out) if args.out else default_output_path(inputs[0], env_dir or metrics_dir)
        output.write_text(
            render_html(inputs[0], env_dir, metrics_dir, env, window, charts, annotations),
            encoding="utf-8")
        print(f"Wrote {output}")
        if args.text:
            text_path = output.with_suffix(".txt")
            text_path.write_text(html_to_text(output.read_text(encoding="utf-8")),
                                 encoding="utf-8")
            print(f"Wrote {text_path}")
        if env_dir is None:
            print("No environment data found; the static sections were skipped.", file=sys.stderr)
        if metrics_dir is None:
            print("No metrics data found; the sampled charts were skipped.", file=sys.stderr)
        return 0
    finally:
        for workspace in workspaces:
            shutil.rmtree(workspace, ignore_errors=True)


def extract_package(package: Path, dest: Path) -> Path:
    with tarfile.open(package) as tar:
        safe_extract(tar, dest)
    dirs = [p for p in dest.iterdir() if p.is_dir()]
    if len(dirs) == 1:
        return dirs[0]
    return dest


def safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    dest_real = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest_real)):
            raise SystemExit(f"unsafe tar member path: {member.name}")
    tar.extractall(dest)


def default_output_path(input_path: Path, metrics_dir: Path) -> Path:
    if input_path.is_dir():
        return input_path / "metrics-report.html"
    base = input_path.name
    for suffix in (".tar.gz", ".tgz", ".tar"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return Path.cwd() / f"{base}-report.html"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def parse_timestamp(text: str) -> datetime | None:
    text = text.strip()
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def read_window(metrics_dir: Path) -> Window:
    """Read the collection window the collector recorded."""
    window = Window()
    start = find_metric(metrics_dir, "collection_start")
    stop = find_metric(metrics_dir, "collection_stop")
    if start and start.exists():
        window.start = parse_timestamp(start.read_text(errors="replace"))
    if stop and stop.exists():
        window.stop = parse_timestamp(stop.read_text(errors="replace"))
    return window


def spread_evenly(count: int, window: Window) -> tuple[list[datetime | None], str]:
    """Timestamps for streams that carry none of their own.

    External tools such as vmstat print no timestamps, so the best available
    anchor is the recorded collection window. This is an approximation and is
    labelled as one in the report.
    """
    if count <= 0:
        return [], TIME_NONE
    if not (window.start and window.stop) or window.stop <= window.start:
        return [None] * count, TIME_NONE
    if count == 1:
        return [window.start], TIME_EVEN
    span = (window.stop - window.start).total_seconds()
    step = span / (count - 1)
    return (
        [window.start + timedelta_seconds(step * i) for i in range(count)],
        TIME_EVEN,
    )


def timedelta_seconds(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)


def elapsed_seconds(before: datetime | None, after: datetime | None) -> float | None:
    if before is None or after is None:
        return None
    delta = (after - before).total_seconds()
    return delta if delta > 0 else None


def format_window(window: Window) -> str:
    if not window.start:
        return "collection window unknown"
    start = window.start.strftime("%Y-%m-%d %H:%M:%S")
    if not window.stop:
        return f"from {start}"
    same_day = window.start.date() == window.stop.date()
    stop = window.stop.strftime("%H:%M:%S" if same_day else "%Y-%m-%d %H:%M:%S")
    return f"{start} to {stop} ({format_duration(window.duration_s)})"


def format_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "unknown duration"
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# ---------------------------------------------------------------------------
# Chart building
# ---------------------------------------------------------------------------


def build_charts(metrics_dir: Path, window: Window, max_points: int) -> list[Chart]:
    charts: list[Chart] = []

    vmstat = parse_vmstat(find_metric(metrics_dir, "vmstat.out.gz", "vmstat.out"), window)
    charts.extend(vmstat_charts(vmstat))

    mpstat = parse_mpstat(find_metric(metrics_dir, "mpstat.out.gz", "mpstat.out"), window)
    charts.extend(mpstat_charts(mpstat))

    status = parse_key_value_samples(
        find_metric(
            metrics_dir,
            "global_status.out.gz",
            "global_status.out",
            "mariadb-admin.out.gz",
            "mariadb-admin.out",
            "mysqladmin.out.gz",
            "mysqladmin.out",
        ),
        window,
    )
    charts.extend(mariadb_status_charts(status))

    innodb_metrics = parse_innodb_metrics(
        find_metric(metrics_dir, "innodb_metrics.out.gz", "innodb_metrics.out"), window
    )
    charts.extend(innodb_metric_charts(innodb_metrics))

    diskstats = parse_diskstats(
        find_metric(metrics_dir, "diskstats.out.gz", "diskstats.out"), window
    )
    charts.extend(diskstats_charts(diskstats))

    return [downsample_chart(chart, max_points) for chart in charts if useful(chart)]


def find_metric(root: Path, *names: str) -> Path | None:
    for name in names:
        direct = root / name
        if direct.exists():
            return direct
    wanted = set(names)
    for path in root.rglob("*"):
        if path.name in wanted:
            return path
    return None


def open_text(path: Path | None) -> Iterable[str]:
    if path is None or not path.exists():
        return []
    if path.suffix == ".gz":
        return (line.decode("utf-8", "replace") for line in gzip.open(path, "rb"))
    return path.open("r", encoding="utf-8", errors="replace")


def parse_vmstat(path: Path | None, window: Window) -> Samples:
    rows: list[dict[str, float]] = []
    headers: list[str] = []
    for line in open_text(path):
        parts = line.split()
        if not parts:
            continue
        if parts[:2] == ["r", "b"]:
            headers = parts
            continue
        if not headers or not is_number(parts[0]) or len(parts) < len(headers):
            continue
        row = {}
        for key, value in zip(headers, parts):
            if is_number(value):
                row[key] = float(value)
        if row:
            rows.append(row)
    times, source = spread_evenly(len(rows), window)
    return Samples(times=times, rows=rows, time_source=source)


def vmstat_charts(samples: Samples) -> list[Chart]:
    if not samples:
        return []
    return [
        chart_from_samples("System CPU", "%", samples, ["us", "sy", "id", "wa", "st"]),
        chart_from_samples("System Memory", "KB", samples, ["free", "buff", "cache"]),
        chart_from_samples("System Processes", "count", samples, ["r", "b"]),
        chart_from_samples("System Swap", "KB/s", samples, ["si", "so"]),
        chart_from_samples(
            "System Interrupts and Context Switches", "/s", samples, ["in", "cs"]
        ),
    ]


def parse_mpstat(path: Path | None, window: Window) -> Samples:
    rows: list[dict[str, float]] = []
    headers: list[str] = []
    for line in open_text(path):
        parts = line.split()
        if not parts:
            continue
        if "CPU" in parts and any(p.startswith("%") for p in parts):
            cpu_idx = parts.index("CPU")
            headers = parts[cpu_idx:]
            continue
        if not headers or "all" not in [p.lower() for p in parts]:
            continue
        cpu_idx = next((i for i, p in enumerate(parts) if p.lower() == "all"), -1)
        if cpu_idx < 0:
            continue
        values = parts[cpu_idx : cpu_idx + len(headers)]
        row = {}
        for key, value in zip(headers, values):
            if key == "CPU":
                continue
            key = key.lstrip("%")
            if is_number(value):
                row[key] = float(value)
        if row:
            rows.append(row)
    times, source = spread_evenly(len(rows), window)
    return Samples(times=times, rows=rows, time_source=source)


def mpstat_charts(samples: Samples) -> list[Chart]:
    if not samples:
        return []
    return [
        chart_from_samples(
            "CPU Average",
            "%",
            samples,
            ["usr", "user", "sys", "system", "iowait", "irq", "soft", "idle"],
        ),
        chart_from_samples("CPU Interrupts", "/s", samples, ["intr/s"]),
    ]


def parse_key_value_samples(path: Path | None, window: Window) -> Samples:
    times: list[datetime | None] = []
    rows: list[dict[str, float]] = []
    current: dict[str, float] = {}
    current_ts: datetime | None = None
    pending_ts: datetime | None = None
    saw_marker = False

    for line in open_text(path):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#TS"):
            saw_marker = True
            if current:
                times.append(current_ts)
                rows.append(current)
                current = {}
            current_ts = parse_timestamp(stripped[3:])
            pending_ts = current_ts
            continue
        if stripped.startswith("+") or "Variable_name" in stripped:
            continue

        key = ""
        value = ""
        if "\t" in stripped:
            parts = stripped.split("\t")
            if len(parts) >= 2:
                key, value = parts[0], parts[1]
        elif "|" in stripped:
            parts = [p.strip() for p in stripped.strip("|").split("|")]
            if len(parts) >= 2:
                key, value = parts[0], parts[1]
        else:
            parts = stripped.split()
            if len(parts) == 2:
                key, value = parts
        if key and is_number(value):
            current[key] = float(value)
            current_ts = pending_ts
    if current:
        times.append(current_ts)
        rows.append(current)

    if saw_marker and any(t is not None for t in times):
        return Samples(times=times, rows=rows, time_source=TIME_EXACT)
    times, source = spread_evenly(len(rows), window)
    return Samples(times=times, rows=rows, time_source=source)


def mariadb_status_charts(samples: Samples) -> list[Chart]:
    if not samples:
        return []
    delta = deltas(samples)
    charts = [
        chart_from_samples("MariaDB Query Traffic", "/s", delta, ["Queries", "Questions"]),
        chart_from_samples(
            "MariaDB Connections",
            "/s",
            delta,
            ["Connections", "Aborted_connects", "Aborted_clients"],
        ),
        chart_from_samples(
            "MariaDB Threads",
            "count",
            samples,
            ["Threads_connected", "Threads_running", "Threads_cached", "Threads_created"],
        ),
        chart_from_samples(
            "MariaDB Statements",
            "/s",
            delta,
            ["Com_select", "Com_insert", "Com_update", "Com_delete", "Com_replace"],
        ),
        chart_from_samples(
            "Temporary Tables",
            "/s",
            delta,
            ["Created_tmp_tables", "Created_tmp_disk_tables"],
        ),
        chart_from_samples(
            "InnoDB Buffer Pool Reads",
            "/s",
            delta,
            ["Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads"],
        ),
        chart_from_samples(
            "InnoDB Row Operations",
            "/s",
            delta,
            [
                "Innodb_rows_read",
                "Innodb_rows_inserted",
                "Innodb_rows_updated",
                "Innodb_rows_deleted",
            ],
        ),
        chart_from_samples(
            "InnoDB IO",
            "/s",
            delta,
            [
                "Innodb_data_reads",
                "Innodb_data_writes",
                "Innodb_os_log_fsyncs",
                "Innodb_log_writes",
            ],
        ),
        chart_from_samples(
            "InnoDB Log Waits and Locks",
            "/s",
            delta,
            ["Innodb_log_waits", "Innodb_row_lock_waits", "Innodb_deadlocks"],
        ),
        chart_from_samples(
            "Open Tables and Files",
            "count",
            samples,
            ["Open_tables", "Open_files", "Opened_tables"],
        ),
        chart_from_samples(
            "Table Cache",
            "/s",
            delta,
            [
                "Table_open_cache_hits",
                "Table_open_cache_misses",
                "Table_open_cache_overflows",
            ],
        ),
        chart_from_samples(
            "Galera Traffic",
            "/s",
            delta,
            [
                "wsrep_replicated",
                "wsrep_received",
                "wsrep_replicated_bytes",
                "wsrep_received_bytes",
            ],
        ),
        chart_from_samples(
            "Galera Flow Control",
            "/s",
            delta,
            [
                "wsrep_flow_control_sent",
                "wsrep_flow_control_recv",
                "wsrep_flow_control_paused_ns",
            ],
        ),
    ]
    return charts


def parse_innodb_metrics(path: Path | None, window: Window) -> Samples:
    times: list[datetime | None] = []
    rows: list[dict[str, float]] = []
    current: dict[str, float] = {}
    current_ts: datetime | None = None
    saw_marker = False

    for line in open_text(path):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#TS"):
            saw_marker = True
            if current:
                times.append(current_ts)
                rows.append(current)
                current = {}
            current_ts = parse_timestamp(stripped[3:])
            continue
        if stripped.lower().startswith("name\t") or stripped.lower().startswith("name "):
            continue
        parts = stripped.split("\t") if "\t" in stripped else stripped.split()
        if len(parts) >= 3 and is_number(parts[2]):
            current[parts[0]] = float(parts[2])
    if current:
        times.append(current_ts)
        rows.append(current)

    if saw_marker and any(t is not None for t in times):
        return Samples(times=times, rows=rows, time_source=TIME_EXACT)
    times, source = spread_evenly(len(rows), window)
    return Samples(times=times, rows=rows, time_source=source)


def innodb_metric_charts(samples: Samples) -> list[Chart]:
    if not samples:
        return []
    delta = deltas(samples)
    return [
        chart_from_samples(
            "InnoDB Metrics: Buffer",
            "/s",
            delta,
            [
                "buffer_page_read",
                "buffer_page_written",
                "buffer_pool_reads",
                "buffer_pool_read_requests",
            ],
        ),
        chart_from_samples(
            "InnoDB Metrics: DML",
            "/s",
            delta,
            ["dml_reads", "dml_inserts", "dml_deletes", "dml_updates"],
        ),
        chart_from_samples(
            "InnoDB Metrics: Locks",
            "/s",
            delta,
            ["lock_deadlocks", "lock_timeouts", "lock_row_lock_waits"],
        ),
        chart_from_samples(
            "InnoDB Metrics: Log",
            "/s",
            delta,
            [
                "log_lsn_current",
                "log_lsn_checkpoint_age",
                "log_write_requests",
                "log_writes",
            ],
        ),
    ]


def parse_diskstats(
    path: Path | None, window: Window
) -> dict[str, Samples]:
    times: list[datetime | None] = []
    samples: list[dict[str, list[float]]] = []
    current: dict[str, list[float]] = {}
    current_ts: datetime | None = None
    saw_marker = False

    for line in open_text(path):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#TS") or stripped.startswith("TS "):
            saw_marker = True
            if current:
                times.append(current_ts)
                samples.append(current)
                current = {}
            current_ts = parse_timestamp(stripped[3:])
            continue
        parts = stripped.split()
        if len(parts) < 14 or not parts[0].isdigit():
            continue
        disk = parts[2]
        if ignore_disk(disk):
            continue
        values = [float(v) for v in parts[3:] if is_number(v)]
        if len(values) >= 11:
            current[disk] = values
    if current:
        times.append(current_ts)
        samples.append(current)

    if not (saw_marker and any(t is not None for t in times)):
        times, _ = spread_evenly(len(samples), window)
        source = TIME_EVEN if any(t is not None for t in times) else TIME_NONE
    else:
        source = TIME_EXACT

    by_disk: dict[str, Samples] = {}
    for idx in range(1, len(samples)):
        prev, cur = samples[idx - 1], samples[idx]
        seconds = elapsed_seconds(times[idx - 1], times[idx]) if times else None
        if seconds is None:
            seconds = 1.0
        for disk, now in cur.items():
            before = prev.get(disk)
            if not before:
                continue
            rd_ios = per_second(now[0], before[0], seconds)
            rd_merges = per_second(now[1], before[1], seconds)
            rd_sectors = per_second(now[2], before[2], seconds)
            rd_ms = per_second(now[3], before[3], seconds)
            wr_ios = per_second(now[4], before[4], seconds)
            wr_merges = per_second(now[5], before[5], seconds)
            wr_sectors = per_second(now[6], before[6], seconds)
            wr_ms = per_second(now[7], before[7], seconds)
            io_ms = per_second(now[9], before[9], seconds)
            weighted_ms = per_second(now[10], before[10], seconds)
            row = {
                "read_iops": rd_ios,
                "write_iops": wr_ios,
                "read_mib_s": rd_sectors * 512 / 1024 / 1024,
                "write_mib_s": wr_sectors * 512 / 1024 / 1024,
                "read_merges": rd_merges,
                "write_merges": wr_merges,
                "read_latency_ms": rd_ms / rd_ios if rd_ios > 0 else 0,
                "write_latency_ms": wr_ms / wr_ios if wr_ios > 0 else 0,
                "util_pct": min(io_ms / 10, 100),
                "queue_depth": weighted_ms / 1000,
            }
            entry = by_disk.setdefault(disk, Samples(time_source=source))
            entry.times.append(times[idx] if times else None)
            entry.rows.append(row)
    return by_disk


def diskstats_charts(disks: dict[str, Samples]) -> list[Chart]:
    charts: list[Chart] = []
    for disk in sorted(disks)[:8]:
        samples = disks[disk]
        charts.extend(
            [
                chart_from_samples(
                    f"Disk {disk} IOPS", "ops/s", samples, ["read_iops", "write_iops"]
                ),
                chart_from_samples(
                    f"Disk {disk} Throughput",
                    "MiB/s",
                    samples,
                    ["read_mib_s", "write_mib_s"],
                ),
                chart_from_samples(
                    f"Disk {disk} Latency",
                    "ms",
                    samples,
                    ["read_latency_ms", "write_latency_ms"],
                ),
                chart_from_samples(
                    f"Disk {disk} Utilization",
                    "% / depth",
                    samples,
                    ["util_pct", "queue_depth"],
                ),
            ]
        )
    return charts


def ignore_disk(name: str) -> bool:
    return bool(re.match(r"^(loop|ram|fd|sr|dm-\d+$)", name))


def per_second(now: float, before: float, seconds: float) -> float:
    """Counter difference converted to a per-second rate."""
    diff = now - before
    if diff < 0 or seconds <= 0:
        return 0
    return diff / seconds


def deltas(samples: Samples) -> Samples:
    """Convert cumulative counters into per-second rates using real elapsed time.

    Sample i covers the interval (t[i-1], t[i]] and is labelled with t[i]. A
    counter that goes backwards means the server restarted or the counter
    wrapped, so the point becomes a gap rather than a fake zero.
    """
    times: list[datetime | None] = []
    rows: list[dict[str, float | None]] = []
    for idx in range(1, len(samples.rows)):
        prev, cur = samples.rows[idx - 1], samples.rows[idx]
        seconds = elapsed_seconds(samples.times[idx - 1], samples.times[idx])
        if seconds is None:
            seconds = 1.0
        row: dict[str, float | None] = {}
        for key, value in cur.items():
            if key not in prev:
                continue
            diff = value - prev[key]
            row[key] = None if diff < 0 else diff / seconds
        times.append(samples.times[idx])
        rows.append(row)
    return Samples(times=times, rows=rows, time_source=samples.time_source)


def chart_from_samples(
    title: str, unit: str, samples: Samples, keys: list[str]
) -> Chart:
    series: dict[str, list[float | None]] = {}
    for wanted in keys:
        for key in match_keys(samples.rows, wanted):
            values = [row.get(key) for row in samples.rows]
            if any(v not in (None, 0) for v in values):
                series[key] = values
    return Chart(
        title=title,
        unit=unit,
        series=series,
        times=list(samples.times),
        time_source=samples.time_source,
    )


def match_keys(rows: list[dict[str, float]], pattern: str) -> list[str]:
    keys = sorted({key for row in rows for key in row})
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return [key for key in keys if key.startswith(prefix)]
    if pattern in keys:
        return [pattern]
    return []


def useful(chart: Chart) -> bool:
    return bool(chart.series) and any(
        any(value not in (None, 0) for value in values) for values in chart.series.values()
    )


def downsample_chart(chart: Chart, max_points: int) -> Chart:
    if max_points <= 0:
        return chart
    length = max((len(v) for v in chart.series.values()), default=0)
    if length <= max_points:
        return chart
    step = max(1, math.ceil(length / max_points))
    return Chart(
        title=chart.title,
        unit=chart.unit,
        kind=chart.kind,
        description=chart.description,
        series={name: values[::step] for name, values in chart.series.items()},
        times=chart.times[::step],
        time_source=chart.time_source,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_html(
    input_path: Path,
    env_dir: Path | None,
    metrics_dir: Path | None,
    env: Env,
    window: Window,
    charts: list[Chart],
    annotations: list[dict[str, str]],
) -> str:
    cards = "\n".join(render_chart(chart, idx) for idx, chart in enumerate(charts))
    approx = any(chart.time_source == TIME_EVEN for chart in charts)
    chart_note = (
        '<p class="caveat">Charts marked <em>approximate time</em> come from tools that do not '
        "timestamp their output (vmstat, mpstat, iostat); their points are spread evenly across "
        "the collection window.</p>"
        if approx
        else ""
    )
    charts_html = (f'<section class="grid">{cards}</section>{chart_note}') if cards else ""

    def ann(anchor: str) -> str:
        return render_annotations(annotations, anchor)

    if env:
        sections = [
            section("1", "Executive Summary",
                    "What this server is, and what stands out. The overview is measured; any "
                    "interpretation is marked.",
                    ann("summary")
                    + "<h3>Overview</h3>" + render_overview(env, window)
                    + "<h3>Workload profile</h3>" + render_workload_profile(env)),
            section("2", "Server Identity and Environment",
                    "Version, host and uptime. Every other number in this report is read in "
                    "the light of these.",
                    render_identity(env) + ann("identity")),
            section("3", "InnoDB Configuration and Health",
                    "InnoDB is MariaDB's default storage engine. It decides how data is cached, "
                    "written and protected against crashes.",
                    render_innodb(env) + ann("innodb")),
            section("4", "Connections and Threading",
                    "Every client connection costs memory and a thread.",
                    render_connections(env) + ann("connections")),
            section("5", "Query Performance Indicators",
                    "How the server is being asked to work, and whether it is coping.",
                    render_query_indicators(env, charts_html, window) + ann("performance")),
            section("6", "Schema Analysis",
                    "The physical shape of the data: what exists, how big it is, and where "
                    "indexes are missing.",
                    render_schema(env) + ann("schema")),
            section("7", "Security",
                    "Who can connect, from where, and with what privileges.",
                    render_security(env) + ann("security")),
            section("8", "MariaDB Features",
                    "MariaDB includes capabilities that applications often reimplement in code "
                    "or miss entirely. This is an inventory of which are in use.",
                    render_features(env) + ann("features")),
            section("9", "Replication",
                    "Whether copies of this database exist, for availability or recovery.",
                    render_replication(env) + ann("replication")),
            section("", "Appendix A: Raw Configuration",
                    "Reference values, for verifying an observation or spotting something the "
                    "automated checks do not cover.",
                    render_raw_config(env)),
            section("", "Appendix B: Methodology",
                    "", render_methodology(env, window)),
        ]
        body = "".join(sections)
        title_line = f"{env.var('version', 'MariaDB')} · {env.var('hostname', '')}"
    else:
        body = section("", "Sampled Metrics",
                       "No environment collection was supplied, so only sampled metrics are shown.",
                       window_quality_note(window, env) + charts_html)
        title_line = ""

    summary = render_summary(window, charts, env)
    appendix = render_credits(datetime.now())
    note = ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MariaDB Metrics Report</title>
<style>
/* Light is the default: reports get printed, exported to PDF and forwarded.
   Dark is available through the toggle and through the OS preference. */
:root {{
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --panel2: #fbfcfd;
  --text: #1a2028;
  --muted: #5a6875;
  --grid: #d9dee5;
  --accent: #b45309;
  --border: #e0e5ea;
  --header-bg: rgba(255, 255, 255, .95);
  --code-bg: rgba(0, 0, 0, .05);
  --row-hover: rgba(0, 0, 0, .025);
  --warn-tint: rgba(180, 83, 9, .06);
  --ai-tint: rgba(2, 132, 199, .07);
  --ai-line: #0284c7;
  --ai-chip-bg: #0284c7;
  --ai-chip-fg: #ffffff;
  --human-tint: rgba(21, 128, 61, .07);
  --human-line: #15803d;
  --human-chip-bg: #15803d;
  --human-chip-fg: #ffffff;
  --sev-critical-bg: #fee2e2; --sev-critical-fg: #991b1b;
  --sev-high-bg: #ffedd5;     --sev-high-fg: #9a3412;
  --sev-medium-bg: #fef3c7;   --sev-medium-fg: #92400e;
  --sev-low-bg: #dbeafe;      --sev-low-fg: #1e40af;
  --series-1: #0369a1; --series-2: #15803d; --series-3: #b45309; --series-4: #be185d;
  --series-5: #6d28d9; --series-6: #be123c; --series-7: #047857; --series-8: #c2410c;
  --logo-light: inline-block;
  --logo-dark: none;
}}

@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --bg: #0b0f14;
    --panel: #111827;
    --panel2: #0f172a;
    --text: #d8dee9;
    --muted: #8b98a8;
    --grid: #263241;
    --accent: #f59e0b;
    --border: #223044;
    --header-bg: rgba(11, 15, 20, .94);
    --code-bg: rgba(255, 255, 255, .05);
    --row-hover: rgba(255, 255, 255, .02);
    --warn-tint: rgba(245, 158, 11, .06);
    --ai-tint: rgba(125, 211, 252, .07);
    --ai-line: #7dd3fc;
    --ai-chip-bg: #7dd3fc;
    --ai-chip-fg: #06243a;
    --human-tint: rgba(134, 239, 172, .07);
    --human-line: #86efac;
    --human-chip-bg: #86efac;
    --human-chip-fg: #06301a;
    --sev-critical-bg: #7f1d1d; --sev-critical-fg: #fecaca;
    --sev-high-bg: #7c2d12;     --sev-high-fg: #fed7aa;
    --sev-medium-bg: #78350f;   --sev-medium-fg: #fde68a;
    --sev-low-bg: #1e3a5f;      --sev-low-fg: #bfdbfe;
    --series-1: #7dd3fc; --series-2: #86efac; --series-3: #fbbf24; --series-4: #f472b6;
    --series-5: #c4b5fd; --series-6: #fb7185; --series-7: #34d399; --series-8: #f97316;
    --logo-light: none;
    --logo-dark: inline-block;
  }}
}}

:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg: #0b0f14;
  --panel: #111827;
  --panel2: #0f172a;
  --text: #d8dee9;
  --muted: #8b98a8;
  --grid: #263241;
  --accent: #f59e0b;
  --border: #223044;
  --header-bg: rgba(11, 15, 20, .94);
  --code-bg: rgba(255, 255, 255, .05);
  --row-hover: rgba(255, 255, 255, .02);
  --warn-tint: rgba(245, 158, 11, .06);
  --ai-tint: rgba(125, 211, 252, .07);
  --ai-line: #7dd3fc;
  --ai-chip-bg: #7dd3fc;
  --ai-chip-fg: #06243a;
  --human-tint: rgba(134, 239, 172, .07);
  --human-line: #86efac;
  --human-chip-bg: #86efac;
  --human-chip-fg: #06301a;
  --sev-critical-bg: #7f1d1d; --sev-critical-fg: #fecaca;
  --sev-high-bg: #7c2d12;     --sev-high-fg: #fed7aa;
  --sev-medium-bg: #78350f;   --sev-medium-fg: #fde68a;
  --sev-low-bg: #1e3a5f;      --sev-low-fg: #bfdbfe;
  --series-1: #7dd3fc; --series-2: #86efac; --series-3: #fbbf24; --series-4: #f472b6;
  --series-5: #c4b5fd; --series-6: #fb7185; --series-7: #34d399; --series-8: #f97316;
  --logo-light: none;
  --logo-dark: inline-block;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 13px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
header {{
  position: sticky;
  top: 0;
  z-index: 5;
  background: var(--header-bg);
  border-bottom: 1px solid var(--border);
  padding: 18px 24px;
}}
h1 {{ margin: 0; font-size: 22px; font-weight: 650; letter-spacing: 0; }}
.sub {{ margin-top: 4px; color: var(--muted); font-size: 12px; }}
.window {{ margin-top: 6px; color: var(--accent); font-size: 13px; }}
.logo {{
  height: 40px;
  width: auto;
  max-width: 220px;
  display: block;
  margin-bottom: 12px;
  color: var(--text);
  font-size: 15px;
  font-weight: 600;
}}
main {{ padding: 20px 24px 36px; }}
.note {{ color: var(--muted); font-size: 12px; margin-bottom: 14px; }}
.appendix {{
  margin-top: 28px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
}}
.appendix h2 {{
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}}
.appendix p {{ margin: 3px 0; }}
.appendix pre {{
  background: var(--panel2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  color: var(--text);
  font-size: 12px;
  margin: 8px 0 0;
}}
.summary {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}}
.stat, .card {{
  background: linear-gradient(180deg, var(--panel), var(--panel2));
  border: 1px solid var(--border);
  border-radius: 8px;
}}
.stat {{ padding: 14px 16px; }}
.stat .label {{ color: var(--muted); font-size: 12px; }}
.stat .value {{ font-size: 24px; margin-top: 6px; color: var(--text); }}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(520px, 1fr));
  gap: 16px;
}}
.card {{ padding: 14px; min-width: 0; }}
.card h2 {{ margin: 0 0 10px; font-size: 15px; font-weight: 600; }}
.approx {{ color: var(--muted); font-size: 11px; font-weight: 400; }}
.legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: var(--muted);
  font-size: 12px;
  margin-top: 8px;
}}
.swatch {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; }}
.key {{ margin-top: 8px; color: var(--muted); font-size: 12px; }}
.report-section {{
  background: linear-gradient(180deg, var(--panel), var(--panel2));
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 20px;
  margin-bottom: 16px;
}}
.report-section h2 {{ margin: 0 0 6px; font-size: 17px; font-weight: 650; }}
.report-section h3 {{ margin: 20px 0 8px; font-size: 14px; font-weight: 600; color: var(--text); }}
.report-section h3:first-of-type {{ margin-top: 14px; }}
.lead {{ color: var(--muted); font-size: 12.5px; margin: 0 0 10px; max-width: 78ch; }}
.caveat {{
  color: var(--muted);
  font-size: 12px;
  border-left: 2px solid var(--accent);
  padding: 6px 0 6px 10px;
  margin: 10px 0;
  max-width: 82ch;
}}
.unavailable {{
  color: var(--muted);
  font-size: 12.5px;
  background: var(--warn-tint);
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  max-width: 82ch;
}}
.none {{ color: var(--muted); font-size: 12.5px; font-style: italic; }}
.tablewrap {{ overflow-x: auto; margin: 8px 0 4px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; }}
th, td {{
  text-align: left;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}}
th {{ color: var(--muted); font-weight: 600; white-space: nowrap; }}
td:first-child {{ white-space: nowrap; }}
tbody tr:hover {{ background: var(--row-hover); }}
code {{
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
  background: var(--code-bg);
  padding: 1px 5px;
  border-radius: 3px;
}}
pre {{
  background: var(--panel2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 11.5px;
}}
.sev {{
  display: inline-block;
  padding: 1px 7px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .02em;
}}
.sev-critical {{ background: var(--sev-critical-bg); color: var(--sev-critical-fg); }}
.sev-high {{ background: var(--sev-high-bg); color: var(--sev-high-fg); }}
.sev-medium {{ background: var(--sev-medium-bg); color: var(--sev-medium-fg); }}
.sev-low {{ background: var(--sev-low-bg); color: var(--sev-low-fg); }}

/* Provenance. Mechanical output is the unmarked default; additions are marked. */
.prov {{
  border-radius: 6px;
  padding: 12px 14px;
  margin: 14px 0;
  max-width: 88ch;
  font-size: 12.5px;
}}
.prov p {{ margin: 6px 0; }}
.prov h4 {{ margin: 2px 0 8px; font-size: 13px; font-weight: 650; color: var(--text); }}
.prov-ai {{
  background: var(--ai-tint);
  border-left: 4px solid var(--ai-line);
}}
.prov-human {{
  background: var(--human-tint);
  border-left: 4px solid var(--human-line);
}}
.prov-meta {{ margin-bottom: 6px; }}
.prov-attr {{ color: var(--muted); font-size: 11px; margin-left: 8px; }}
.prov-chip {{
  display: inline-block;
  padding: 1px 7px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .05em;
}}
span.prov-ai {{ background: var(--ai-chip-bg); color: var(--ai-chip-fg); border: 0; }}
span.prov-human {{ background: var(--human-chip-bg); color: var(--human-chip-fg); border: 0; }}
svg {{ width: 100%; height: 260px; display: block; }}
.axis {{ stroke: var(--grid); stroke-width: 1; }}
.vgrid {{ stroke: var(--grid); stroke-width: 1; stroke-dasharray: 2 4; }}
.tick {{ fill: var(--muted); font-size: 10px; }}
.xtick {{ fill: var(--muted); font-size: 10px; text-anchor: middle; }}
.line {{ fill: none; stroke-width: 1.8; vector-effect: non-scaling-stroke; }}
.empty {{ padding: 28px; color: var(--muted); }}
@media (max-width: 720px) {{
  main, header {{ padding-left: 12px; padding-right: 12px; }}
  .grid {{ grid-template-columns: 1fr; }}
  svg {{ height: 220px; }}
}}

/* Theme toggle */
.theme-toggle {{
  position: absolute;
  top: 18px;
  right: 24px;
  background: var(--panel);
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 11px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}}
.theme-toggle:hover {{ color: var(--text); }}
.logo-light {{ display: var(--logo-light); }}
.logo-dark {{ display: var(--logo-dark); }}

/* Print: always light, no sticky header, keep blocks whole. */
@media print {{
  :root, :root[data-theme="dark"] {{
    color-scheme: light;
    --bg: #ffffff;
    --panel: #ffffff;
    --panel2: #ffffff;
    --text: #000000;
    --muted: #444444;
    --grid: #cccccc;
    --border: #bbbbbb;
    --code-bg: #f2f2f2;
    --row-hover: transparent;
    --logo-light: inline-block;
    --logo-dark: none;
    --series-1: #0369a1; --series-2: #15803d; --series-3: #b45309; --series-4: #be185d;
    --series-5: #6d28d9; --series-6: #be123c; --series-7: #047857; --series-8: #c2410c;
  }}
  body {{ background: #fff; }}
  header {{ position: static; border-bottom: 1px solid #bbb; }}
  .theme-toggle {{ display: none; }}
  .stat, .card, .report-section {{ background: none; border: 1px solid #ddd; }}
  .report-section, .card, .prov, .tablewrap {{ break-inside: avoid; page-break-inside: avoid; }}
  h2, h3 {{ break-after: avoid; page-break-after: avoid; }}
  a {{ color: inherit; text-decoration: none; }}
}}
</style>
</head>
<body>
<header>
  <button class="theme-toggle" type="button" onclick="toggleTheme()" id="theme-toggle">Dark mode</button>
  <img class="logo logo-light" src="{LOGO_URL_LIGHT}" alt="{LOGO_ALT}">
  <img class="logo logo-dark" src="{LOGO_URL_DARK}" alt="{LOGO_ALT}">
  <h1>{html.escape(REPORT_TITLE)}</h1>
  <div class="window">{html.escape(title_line)}</div>
  <div class="sub">Metrics window: {html.escape(format_window(window))}</div>
  <div class="key">
    Everything here is mechanically generated from collected data unless labelled
    <span class="prov-chip prov-ai">AI ANALYSIS</span> or
    <span class="prov-chip prov-human">DBA NOTE</span>.
  </div>
</header>
<main>
{summary}
{note}
{body}
{appendix}
</main>
<script>
// Theme choice is remembered; the OS preference decides when nothing is stored.
function applyTheme(t) {{
  if (t) {{ document.documentElement.setAttribute('data-theme', t); }}
  else {{ document.documentElement.removeAttribute('data-theme'); }}
  var dark = t === 'dark' || (!t && matchMedia('(prefers-color-scheme: dark)').matches);
  var b = document.getElementById('theme-toggle');
  if (b) {{ b.textContent = dark ? 'Light mode' : 'Dark mode'; }}
}}
function toggleTheme() {{
  var cur = document.documentElement.getAttribute('data-theme');
  if (!cur) {{ cur = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; }}
  var next = cur === 'dark' ? 'light' : 'dark';
  try {{ localStorage.setItem('vibe-dba-theme', next); }} catch (e) {{}}
  applyTheme(next);
}}
try {{ applyTheme(localStorage.getItem('vibe-dba-theme')); }} catch (e) {{ applyTheme(null); }}
</script>
</body>
</html>
"""


def render_summary(window: Window, charts: list[Chart], env: Env) -> str:
    counts = {r.get("object"): r.get("count", "?") for r in env.table("object_counts")}
    dataset = env.table("dataset")
    size = dataset[0].get("TOTAL_SIZE", "?") if dataset else "?"
    findings = security_findings(env) if env else []
    worst = findings[0][0] if findings else "none"
    stats = [
        ("MariaDB", env.var("version", "unknown").split("-")[0] or "unknown"),
        ("Databases", counts.get("schemas", "?")),
        ("Tables", counts.get("base_tables", "?")),
        ("Data size", size),
        ("Metrics window", format_duration(window.duration_s) if window.duration_s else "none"),
        ("Security findings", f"{len(findings)} ({worst})" if findings else "none"),
    ]
    cards = "".join(
        f'<div class="stat"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(str(value))}</div></div>'
        for label, value in stats
    )
    return f'<section class="summary">{cards}</section>'


# ---------------------------------------------------------------------------
# Environment (static) data
# ---------------------------------------------------------------------------


@dataclass
class Env:
    """Parsed output of mariadb-envcollect."""

    root: Path | None = None
    variables: dict[str, str] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    hardware: dict[str, str] = field(default_factory=dict)
    tables: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.variables or self.tables)

    def var(self, name: str, default: str = "") -> str:
        return self.variables.get(name, default)

    def num(self, name: str, source: str = "status") -> float:
        raw = (self.status if source == "status" else self.variables).get(name, "")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    def table(self, name: str) -> list[dict[str, str]]:
        return self.tables.get(name, [])

    def text(self, name: str) -> str:
        return self.files.get(name, "").strip()


# Files that hold a bare key/value listing rather than a query result.
ENV_KV_FILES = {
    "mariadb_variables": "variables",
    "mariadb_global_status": "status",
    "hardware": "hardware",
}

# Files worth keeping as raw text.
ENV_TEXT_FILES = (
    "mariadb_version",
    "datadir",
    "uname",
    "lscpu",
    "free",
    "hostnamectl",
    "mariadb_replica_status",
    "mariadb_master_status",
    "native-system-summary",
    "native-mariadb-summary",
    "collection_start",
)


def load_env(root: Path | None) -> Env:
    """Read an environment collection directory.

    Analysis-query files start with a `-----<SQL>=====` marker line, then a
    tab-separated result set. Everything else is kept as raw text.
    """
    env = Env(root=root)
    if root is None or not root.is_dir():
        return env

    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if name in ENV_KV_FILES:
            target = {"variables": env.variables, "status": env.status,
                      "hardware": env.hardware}[ENV_KV_FILES[name]]
            for line in raw.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    target[parts[0]] = parts[1]
            continue

        if raw.startswith("-----"):
            rows = parse_result_table(raw)
            if rows is not None:
                env.tables[name] = rows
                continue

        if name in ENV_TEXT_FILES or len(raw) < 20000:
            env.files[name] = raw

    return env


def parse_result_table(raw: str) -> list[dict[str, str]] | None:
    """Parse the `-----SQL=====` + tab-separated result written by the collector."""
    marker = raw.find("=====")
    if marker < 0:
        return None
    body = raw[marker + 5 :].lstrip("\n")
    lines = [line for line in body.splitlines() if line.strip()]
    if not lines:
        return []
    if lines[0].startswith("ERROR"):
        return []
    header = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        if line.startswith("ERROR"):
            continue
        values = line.split("\t")
        if len(values) < len(header):
            values += [""] * (len(header) - len(values))
        rows.append(dict(zip(header, values)))
    return rows


def find_env_dir(path: Path) -> Path | None:
    """Locate an environment collection directory at or below `path`."""
    if (path / "mariadb_variables").exists():
        return path
    for candidate in sorted(path.rglob("mariadb_variables")):
        return candidate.parent
    return None


def find_metrics_dir(path: Path) -> Path | None:
    for name in ("global_status.out.gz", "global_status.out"):
        if (path / name).exists():
            return path
        for candidate in sorted(path.rglob(name)):
            return candidate.parent
    return None


# ---------------------------------------------------------------------------
# Formatting helpers for the static sections
# ---------------------------------------------------------------------------


def human_bytes(value: float) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < step or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TB"


def human_count(value: float) -> str:
    return f"{int(value):,}"


def human_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return ", ".join(parts)


def pct(part: float, whole: float, digits: int = 1) -> str:
    if not whole:
        return "n/a"
    value = part / whole * 100
    if 0 < value < 0.01:
        return "~0%"
    return f"{value:.{digits}f}%"


def table_html(headers: list[str], rows: list[list[str]], empty: str = "") -> str:
    if not rows:
        return f'<p class="none">{html.escape(empty or "Nothing found.")}</p>'
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td>{c}</td>" for c in row)
        body.append(f"<tr>{cells}</tr>")
    return (
        '<div class="tablewrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def code(text: str) -> str:
    return f"<code>{html.escape(text)}</code>"


def section(number: str, title: str, lead: str, body: str) -> str:
    heading = f"{number}. {title}" if number else title
    lead_html = f'<p class="lead">{html.escape(lead)}</p>' if lead else ""
    return f"""
<section class="report-section" id="section-{html.escape(number or title).lower().replace(' ', '-')}">
  <h2>{html.escape(heading)}</h2>
  {lead_html}
  {body}
</section>
"""


# MariaDB capabilities the feature section reports on. The collector returns
# only what is in use; anything here that is absent is reported as available.
# Version tags follow the repository convention.
ADOPTABLE_FEATURES: list[tuple[str, str, str]] = [
    ("System-versioned tables", "10.3+", "row history without triggers or audit tables"),
    ("Sequences", "10.3+", "shared, gap-free counters independent of AUTO_INCREMENT"),
    ("VECTOR columns", "11.7+", "native vector storage for embeddings and semantic search"),
    ("Vector indexes", "11.7+", "approximate nearest-neighbour search"),
    ("INET4/INET6/UUID columns", "10.5+ / 10.10+", "native types instead of VARCHAR"),
    ("JSON columns", "10.2+", "validated JSON with a json_valid() constraint"),
    ("CHECK constraints", "10.2+", "value rules enforced by the server"),
    ("Generated columns", "5.2+", "computed values stored or derived on read"),
    ("Invisible columns", "10.3+", "columns hidden from SELECT *"),
    ("Full-text indexes", "5.1+", "text search without an external engine"),
    ("Spatial indexes", "5.3+", "geometry search"),
    ("Partitioned tables", "5.1+", "large tables split for pruning and retention"),
    ("Page compression", "10.1+", "on-disk compression for InnoDB"),
    ("Triggers", "5.0+", "automatic action on insert, update or delete"),
    ("Views", "5.0+", "stored queries presented as tables"),
    ("Stored routines", "5.0+", "procedures and functions in the server"),
    ("Events", "5.1+", "scheduled jobs in the server"),
]

# Configuration values reported verbatim in Appendix A.
RAW_CONFIG_KEYS = [
    "version", "innodb_buffer_pool_size", "innodb_buffer_pool_instances",
    "innodb_log_file_size", "innodb_log_buffer_size", "innodb_flush_log_at_trx_commit",
    "innodb_flush_method", "innodb_doublewrite", "innodb_file_per_table",
    "innodb_io_capacity", "innodb_io_capacity_max", "innodb_flush_neighbors",
    "innodb_adaptive_hash_index", "sync_binlog", "binlog_format", "log_bin",
    "max_connections", "thread_cache_size", "table_open_cache", "open_files_limit",
    "tmp_table_size", "max_heap_table_size", "sort_buffer_size", "join_buffer_size",
    "read_buffer_size", "read_rnd_buffer_size", "skip_name_resolve", "local_infile",
    "require_secure_transport", "performance_schema", "slow_query_log",
    "long_query_time", "log_queries_not_using_indexes", "character_set_server",
    "collation_server", "sql_mode",
]

BYTE_VARS = {
    "innodb_buffer_pool_size", "innodb_log_file_size", "innodb_log_buffer_size",
    "tmp_table_size", "max_heap_table_size", "sort_buffer_size", "join_buffer_size",
    "read_buffer_size", "read_rnd_buffer_size",
}


def total_ram_bytes(env: Env) -> float:
    """Physical memory, from the collector's normalised hardware facts."""
    raw = env.hardware.get("memory_bytes", "")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    for key in ("native-system-summary", "free", "lscpu"):
        text = env.text(key)
        if not text:
            continue
        match = re.search(r"(?im)^\s*(?:MemTotal|Total memory|RAM)\s*[:|]?\s*([\d.]+)\s*(\w+)", text)
        if match:
            value, unit = float(match.group(1)), match.group(2).lower()
            factor = {"b": 1, "kb": 1024, "k": 1024, "mb": 1024**2, "m": 1024**2,
                      "gb": 1024**3, "g": 1024**3, "tb": 1024**4}.get(unit, 1)
            return value * factor
    return 0.0


def render_overview(env: Env, window: Window) -> str:
    uptime = env.num("Uptime")
    schemas = {r.get("object"): r.get("count", "0") for r in env.table("object_counts")}
    dataset = env.table("dataset")
    total_size = dataset[0].get("TOTAL_SIZE", "?") if dataset else "?"
    bp = env.num("innodb_buffer_pool_size", "variables")
    ram = total_ram_bytes(env)
    reads = env.num("Innodb_buffer_pool_read_requests")
    disk = env.num("Innodb_buffer_pool_reads")
    hit = pct(reads - disk, reads) if reads else "n/a"
    findings = security_findings(env)
    counts: dict[str, int] = {}
    for sev, _ in findings:
        counts[sev] = counts.get(sev, 0) + 1
    sev_text = ", ".join(f"{n} {s}" for s, n in counts.items()) or "none"

    rows = [
        ["Version", html.escape(env.var("version", "unknown"))],
        ["Uptime", html.escape(human_uptime(uptime)) if uptime else "unknown"],
        ["Databases", f"{schemas.get('schemas','?')} user databases, {html.escape(total_size)} total"],
        ["Tables", f"{schemas.get('base_tables','?')} tables, ~{html.escape(human_count(float(schemas.get('estimated_rows', 0) or 0)))} rows"],
        ["Connections", f"peak {human_count(env.num('Max_used_connections'))} of {html.escape(env.var('max_connections','?'))} configured"],
        ["InnoDB buffer pool",
         f"{human_bytes(bp)}" + (f" ({pct(bp, ram)} of RAM)" if ram else "") + f"; {hit} read hit ratio"],
        ["Security findings", html.escape(sev_text)],
        ["Metrics window", html.escape(format_window(window))],
    ]
    return table_html(["Area", "Observation"], rows)


def render_workload_profile(env: Env) -> str:
    reads = env.num("Com_select")
    writes = env.num("Com_insert") + env.num("Com_update") + env.num("Com_delete") + env.num("Com_replace")
    total = reads + writes
    if not total:
        return '<p class="none">No statement counters available.</p>'
    rows = [
        ["Reads", f"{human_count(reads)} SELECT", pct(reads, total)],
        ["Writes",
         f"{human_count(env.num('Com_insert'))} INSERT + {human_count(env.num('Com_update'))} UPDATE + "
         f"{human_count(env.num('Com_delete'))} DELETE", pct(writes, total)],
    ]
    note = (
        '<p class="caveat">These are cumulative counters since the last restart, not a '
        "measurement of current behaviour. Use the sampled charts in section 5 for that.</p>"
    )
    return table_html(["Mix", "Statements", "Share"], rows) + note


def render_identity(env: Env) -> str:
    summary = env.text("native-mariadb-summary")
    def from_summary(key: str) -> str:
        match = re.search(rf"(?m)^{re.escape(key)}\t(.*)$", summary)
        return match.group(1).strip() if match else ""

    ram = total_ram_bytes(env)
    rows = [
        ["Version", html.escape(env.var("version", "unknown"))],
        ["Version comment", html.escape(env.var("version_comment", ""))],
        ["Hostname", html.escape(env.var("hostname", from_summary("hostname")))],
        ["Port / socket", html.escape(f"{env.var('port','?')} / {env.var('socket','?')}")],
        ["Data directory", code(env.var("datadir", env.text("datadir")))],
        ["Uptime", html.escape(human_uptime(env.num("Uptime")))],
        ["Server character set", html.escape(f"{env.var('character_set_server','?')} / {env.var('collation_server','?')}")],
    ]
    hw = env.hardware
    os_label = " ".join(x for x in (hw.get("os_name", ""), hw.get("os_version", "")) if x)
    if not os_label:
        uname = env.text("uname")
        if uname and "ERROR" not in uname:
            os_label = uname.splitlines()[0][:180]
    if os_label:
        rows.append(["Operating system", html.escape(os_label)])
    if hw.get("kernel"):
        rows.append(["Kernel", html.escape(hw["kernel"])])
    if hw.get("cpu_model") or hw.get("cpu_cores"):
        cpu = hw.get("cpu_model", "unknown")
        if hw.get("cpu_cores"):
            cpu += f" ({hw['cpu_cores']} cores)"
        rows.append(["CPU", html.escape(cpu)])
    if ram:
        rows.append(["RAM", html.escape(human_bytes(ram))])
    if hw.get("disk_root_bytes"):
        try:
            disk = human_bytes(float(hw["disk_root_bytes"]))
        except ValueError:
            disk = hw["disk_root_bytes"]
        if hw.get("disk_root_ssd") == "yes":
            disk += " (solid state)"
        rows.append(["Root filesystem", html.escape(disk)])
    return table_html(["Item", "Value"], rows)


def render_innodb(env: Env) -> str:
    bp = env.num("innodb_buffer_pool_size", "variables")
    ram = total_ram_bytes(env)
    reads = env.num("Innodb_buffer_pool_read_requests")
    disk = env.num("Innodb_buffer_pool_reads")
    pool = [
        ["Size", html.escape(human_bytes(bp) + (f" ({pct(bp, ram)} of total RAM)" if ram else "")),
         "Memory allocated to cache data and index pages"],
        ["Read hit ratio", pct(reads - disk, reads) if reads else "n/a",
         "Share of read requests served from memory rather than disk"],
        ["Pages: total / free / dirty",
         f"{human_count(env.num('Innodb_buffer_pool_pages_total'))} / "
         f"{human_count(env.num('Innodb_buffer_pool_pages_free'))} / "
         f"{human_count(env.num('Innodb_buffer_pool_pages_dirty'))}",
         "16 KB pages; dirty pages are modified and not yet written to disk"],
        ["Dump at shutdown / load at startup",
         html.escape(f"{env.var('innodb_buffer_pool_dump_at_shutdown','?')} / {env.var('innodb_buffer_pool_load_at_startup','?')}"),
         "Whether the pool is saved and restored across restarts to avoid a cold cache"],
    ]
    durability = [
        [code("innodb_flush_log_at_trx_commit"), html.escape(env.var("innodb_flush_log_at_trx_commit", "?")),
         "1 flushes on every commit (full ACID); 2 can lose up to a second on OS crash"],
        [code("sync_binlog"), html.escape(env.var("sync_binlog", "?")),
         "How often the binary log is synced to disk; 1 is safest"],
        [code("innodb_flush_method"), html.escape(env.var("innodb_flush_method", "?")),
         "How InnoDB writes data and log files"],
        [code("innodb_doublewrite"), html.escape(env.var("innodb_doublewrite", "?")),
         "Guards against partially written pages during a crash"],
        [code("innodb_log_file_size"), html.escape(human_bytes(env.num("innodb_log_file_size", "variables"))),
         "Redo log size; too small forces frequent checkpoint flushing"],
        [code("innodb_file_per_table"), html.escape(env.var("innodb_file_per_table", "?")),
         "Whether each table gets its own tablespace file"],
    ]
    return (
        "<h3>Buffer pool</h3>"
        + table_html(["Setting / metric", "Value", "What it means"], pool)
        + "<h3>Durability and logging</h3>"
        + table_html(["Setting", "Value", "What it means"], durability)
    )


def render_connections(env: Env) -> str:
    max_conn = env.num("max_connections", "variables")
    peak = env.num("Max_used_connections")
    uptime_days = max(env.num("Uptime") / 86400, 1 / 24)
    rows = [
        ["Current connections", human_count(env.num("Threads_connected")),
         "Connections open when the collection ran"],
        ["Peak connections", f"{human_count(peak)}" + (f" ({pct(peak, max_conn)} of the limit)" if max_conn else ""),
         "Highest simultaneous connections since the last restart"],
        [code("max_connections"), html.escape(env.var("max_connections", "?")),
         "Configured connection limit"],
        [code("thread_cache_size"), html.escape(env.var("thread_cache_size", "?")),
         "Threads kept for reuse instead of being created per connection"],
        ["Aborted connects", f"{human_count(env.num('Aborted_connects'))} ({env.num('Aborted_connects')/uptime_days:.1f}/day)",
         "Failed connection attempts — authentication failures, timeouts, or network issues"],
        ["Aborted clients", f"{human_count(env.num('Aborted_clients'))} ({env.num('Aborted_clients')/uptime_days:.1f}/day)",
         "Connections closed without a clean disconnect"],
        [code("skip_name_resolve"), html.escape(env.var("skip_name_resolve", "?")),
         "OFF makes the server do a reverse DNS lookup on every new connection"],
    ]
    return table_html(["Setting / metric", "Value", "What it means"], rows)


def render_query_indicators(env: Env, charts_html: str, window: Window) -> str:
    uptime = max(env.num("Uptime"), 1)
    tmp = env.num("Created_tmp_tables")
    tmp_disk = env.num("Created_tmp_disk_tables")
    counters = [
        ["Questions", human_count(env.num("Questions")), f"{env.num('Questions')/uptime:.4f}/s",
         "Statements received since the last restart"],
        ["Slow queries", human_count(env.num("Slow_queries")), f"{env.num('Slow_queries')/uptime:.4f}/s",
         "Statements exceeding long_query_time"],
        [code("Select_scan"), human_count(env.num("Select_scan")), f"{env.num('Select_scan')/uptime:.4f}/s",
         "Joins that did a full scan of the first table"],
        [code("Select_full_join"), human_count(env.num("Select_full_join")), f"{env.num('Select_full_join')/uptime:.4f}/s",
         "Joins with no usable index — usually a missing index"],
        [code("Sort_merge_passes"), human_count(env.num("Sort_merge_passes")), f"{env.num('Sort_merge_passes')/uptime:.4f}/s",
         "Sorts that spilled to disk; raising sort_buffer_size may help"],
        [code("Created_tmp_tables"), human_count(tmp), f"{tmp/uptime:.4f}/s",
         "Temporary tables built in memory"],
        [code("Created_tmp_disk_tables"),
         f"{human_count(tmp_disk)} ({pct(tmp_disk, tmp)} of all temp tables)", f"{tmp_disk/uptime:.4f}/s",
         "Temporary tables that spilled to disk; raise tmp_table_size and max_heap_table_size"],
    ]
    slow_log = [
        [code("slow_query_log"), html.escape(env.var("slow_query_log", "?")),
         "Whether slow statements are recorded at all"],
        [code("long_query_time"), html.escape(env.var("long_query_time", "?")) + " seconds",
         "Threshold above which a statement is logged as slow"],
        [code("log_queries_not_using_indexes"), html.escape(env.var("log_queries_not_using_indexes", "?")),
         "Whether unindexed statements are logged regardless of duration"],
    ]

    caveat = (
        '<p class="caveat">The counters above are cumulative since the last restart '
        f'({html.escape(human_uptime(env.num("Uptime")))}). They describe the whole life of the '
        "server, not the present. Only the sampled charts below describe behaviour during the "
        "collection window.</p>"
    )

    pfs = env.var("performance_schema", "OFF")
    if pfs.upper() in ("ON", "1"):
        digest = env.table("pfs_tmp_to_disk")
        rows = [[html.escape(r.get("schema_name", "")), code(r.get("statement", "")),
                 human_count(float(r.get("count_star", 0) or 0)),
                 human_count(float(r.get("sum_created_tmp_disk_tables", 0) or 0))] for r in digest[:15]]
        digest_html = table_html(
            ["Schema", "Statement", "Executions", "Temp tables to disk"], rows,
            "Performance Schema is on but reported no statements using temporary tables.")
    else:
        digest_html = (
            '<p class="unavailable"><strong>Unavailable.</strong> Performance Schema is OFF, so '
            "there is no statement-level profiling: which query patterns consume the most time, "
            "which indexes are used, and which tables generate the most I/O are all invisible. "
            "Enable it with " + code("performance_schema = ON") + " in the configuration file and "
            "restart. Overhead is modest.</p>"
        )

    return (
        "<h3>Global counters</h3>" + caveat
        + table_html(["Metric", "Total", "Rate (lifetime)", "What it means"], counters)
        + "<h3>Sampled metrics</h3>" + window_quality_note(window, env)
        + (charts_html or '<p class="none">No sampled metrics were provided.</p>')
        + "<h3>Slow query log</h3>"
        + table_html(["Setting", "Value", "What it means"], slow_log)
        + "<h3>Statement digests</h3>" + digest_html
    )


def window_quality_note(window: Window, env: Env) -> str:
    """State plainly what the collection window can and cannot support."""
    duration = window.duration_s
    if not duration:
        return (
            '<p class="unavailable"><strong>No metrics were sampled.</strong> Run '
            + code("mariadb-metrics -duration 10m")
            + " across a period you care about to get workload analysis.</p>"
        )
    if duration < 300:
        return (
            f'<p class="caveat"><strong>Short window: {html.escape(format_duration(duration))}.</strong> '
            "This is too short to characterise a workload. Treat the charts as a spot check, not "
            "evidence. Re-run for at least 10 minutes, timed to cover the period you are asking "
            "about.</p>"
        )
    return (
        f'<p class="caveat">Sampled over {html.escape(format_duration(duration))}. Conclusions apply '
        "to this window only. If the behaviour you care about happens at another time, collect "
        "then.</p>"
    )


def render_schema(env: Env) -> str:
    schemas = [[html.escape(r.get("schema_name", "")), r.get("tables", "0"),
                html.escape(r.get("default_character_set_name", "")),
                html.escape(r.get("default_collation_name", ""))]
               for r in env.table("schemas_all")]
    sizes = [[html.escape(r.get("table_schema", "")), r.get("total_gb", ""), r.get("data_gb", ""), r.get("index_gb", "")]
             for r in env.table("data_usage_by_schema")]
    engines = [[html.escape(r.get("engine", "")), r.get("tables", ""), r.get("total_gb", "")]
               for r in env.table("data_usage_by_storage_engine") if r.get("engine")]

    top_rows = env.table("top10_large_tables")
    total_bytes = sum(float(r.get("total_gb", 0) or 0) for r in top_rows)
    top = [[code(r.get("tbl", "")), html.escape(r.get("engine", "")),
            human_count(float(r.get("table_rows", 0) or 0)), r.get("data_gb", ""), r.get("index_gb", ""),
            r.get("total_gb", ""), pct(float(r.get("total_gb", 0) or 0), total_bytes)]
           for r in top_rows]

    nopk = [[html.escape(r.get("table_schema", "")), code(r.get("table_name", "")),
             human_count(float(r.get("table_rows", 0) or 0))]
            for r in env.table("tables_without_primarykey")]
    nosec = [[html.escape(r.get("table_schema", "")), code(r.get("table_name", "")),
              human_count(float(r.get("table_rows", 0) or 0)), r.get("size_mb", "")]
             for r in env.table("tables_without_secondary_index")]

    auto = []
    for r in env.table("mariadb_autoincrement_fill"):
        col_type = r.get("column_type", "").lower()
        limit = (9223372036854775807 if "bigint" in col_type else
                 2147483647 if "int(" in col_type or col_type.startswith("int") else
                 8388607 if "mediumint" in col_type else
                 32767 if "smallint" in col_type else 127 if "tinyint" in col_type else 0)
        if "unsigned" in col_type and limit:
            limit = limit * 2 + 1
        current = float(r.get("auto_increment", 0) or 0)
        auto.append([html.escape(r.get("table_schema", "")), code(r.get("table_name", "")),
                     code(r.get("column_name", "")), html.escape(col_type),
                     human_count(current), pct(current, limit) if limit else "n/a"])

    return (
        "<h3>Databases</h3>"
        + table_html(["Database", "Tables", "Character set", "Collation"], schemas)
        + "<h3>Size by database</h3>"
        + table_html(["Database", "Total (GB)", "Data (GB)", "Index (GB)"], sizes)
        + "<h3>Storage engines</h3>"
        + table_html(["Engine", "Tables", "Total (GB)"], engines)
        + "<h3>Largest tables</h3>"
        + table_html(["Table", "Engine", "Rows", "Data (GB)", "Index (GB)", "Total (GB)", "Share"], top)
        + "<h3>Tables without a primary key</h3>"
        + '<p class="lead">InnoDB clusters rows on the primary key. Without one it creates a hidden '
          "6-byte row ID that is shared across all such tables, which becomes a write contention point.</p>"
        + table_html(["Database", "Table", "Rows"], nopk, "No InnoDB tables are missing a primary key.")
        + "<h3>Tables with no secondary index</h3>"
        + '<p class="lead">Tables over 10,000 rows carrying only a primary key. Any query filtering on '
          "another column scans the whole table.</p>"
        + table_html(["Database", "Table", "Rows", "Size (MB)"], nosec,
                     "Every large table has at least one secondary index.")
        + "<h3>Auto-increment headroom</h3>"
        + table_html(["Database", "Table", "Column", "Type", "Current value", "Used"], auto,
                     "No auto-increment columns found.")
    )


def security_findings(env: Env) -> list[tuple[str, str]]:
    """Severity-ranked security findings derived from the collected data."""
    findings: list[tuple[str, str]] = []

    anon = [r for r in env.table("anonymous_accounts") if r.get("User", r.get("user", "")) == ""]
    if anon:
        names = ", ".join(f"''@'{r.get('Host', r.get('host',''))}'" for r in anon)
        findings.append(("CRITICAL",
                         f"Anonymous accounts exist ({html.escape(names)}) — anyone matching the host "
                         f"pattern can connect without credentials. Remove with {code('DROP USER ' + chr(39) + chr(39) + '@' + chr(39) + 'localhost' + chr(39) + ';')}"))

    remote_root = env.table("non_local_root_accounts")
    if remote_root:
        names = ", ".join(f"root@'{r.get('host', r.get('Host',''))}'" for r in remote_root)
        findings.append(("HIGH", f"root is reachable from a non-local host ({html.escape(names)})."))

    for r in env.table("security_shared_passwords"):
        findings.append(("MEDIUM",
                         f"{r.get('sharing','?')} accounts share the same password hash: "
                         f"{html.escape(r.get('accounts',''))}."))

    for r in env.table("security_admin_grants"):
        findings.append(("MEDIUM",
                         f"Non-root account {code(r.get('User', r.get('user','')) + '@' + r.get('Host', r.get('host','')))}"
                         f" holds administrative privileges: {html.escape(r.get('admin_privileges',''))}."))

    if env.var("require_secure_transport", "OFF").upper() in ("OFF", "0"):
        findings.append(("MEDIUM", f"{code('require_secure_transport')} is OFF — clients may connect unencrypted."))

    if env.var("local_infile", "OFF").upper() in ("ON", "1"):
        findings.append(("LOW", f"{code('local_infile')} is ON — clients can load local files into the server."))

    if any(r.get("schema_name") == "test" for r in env.table("schemas_all")):
        findings.append(("LOW", "A database named 'test' exists; default test databases should not be present in production."))

    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    findings.sort(key=lambda f: order.get(f[0], 9))
    return findings


def render_security(env: Env) -> str:
    findings = security_findings(env)
    rows = [[str(i + 1), f'<span class="sev sev-{sev.lower()}">{sev}</span>', text]
            for i, (sev, text) in enumerate(findings)]
    accounts = [[code(f"{r.get('User', r.get('user',''))}@{r.get('Host', r.get('host',''))}"),
                 html.escape(r.get("plugin", "")),
                 html.escape(r.get("is_role", "")), html.escape(r.get("password_expired", ""))]
                for r in env.table("security_accounts")]
    return (
        table_html(["#", "Severity", "Finding"], rows, "No security findings.")
        + "<h3>Accounts</h3>"
        + table_html(["Account", "Auth plugin", "Is role", "Password expired"], accounts)
    )


def render_features(env: Env) -> str:
    in_use: dict[str, list[str]] = {}
    for row in env.table("mariadb_features_in_use"):
        in_use.setdefault(row.get("feature", ""), []).append(row.get("location", ""))

    used_rows = []
    for name, locations in sorted(in_use.items()):
        shown = ", ".join(code(loc) for loc in locations[:6])
        if len(locations) > 6:
            shown += f" and {len(locations) - 6} more"
        used_rows.append([html.escape(name), str(len(locations)), shown])

    unused_rows = [[html.escape(name), html.escape(version), html.escape(note)]
                   for name, version, note in ADOPTABLE_FEATURES if name not in in_use]

    return (
        "<h3>In use</h3>"
        + table_html(["Feature", "Count", "Where"], used_rows,
                     "No MariaDB-specific features were detected.")
        + "<h3>Available but not in use</h3>"
        + '<p class="lead">Capabilities this server supports that nothing currently uses. Not every one '
          "is worth adopting — this is an inventory, not a recommendation.</p>"
        + table_html(["Feature", "Available since", "What it gives you"], unused_rows,
                     "Every tracked feature is in use.")
    )


def render_replication(env: Env) -> str:
    replica = env.text("mariadb_replica_status")
    master = env.text("mariadb_master_status")
    parts = []
    if replica and "Slave_IO_State" in replica:
        parts.append("<h3>This server is a replica</h3><pre>" + html.escape(replica[:4000]) + "</pre>")
    else:
        parts.append('<p class="none">Not configured as a replica.</p>')
    if master and "File" in master:
        parts.append("<h3>Binary log position</h3><pre>" + html.escape(master[:2000]) + "</pre>")
    elif env.var("log_bin", "OFF").upper() in ("OFF", "0"):
        parts.append(
            '<p class="caveat">The binary log is disabled. Without it there is no point-in-time '
            "recovery and no replication.</p>")
    return "".join(parts)


def render_raw_config(env: Env) -> str:
    rows = []
    for key in RAW_CONFIG_KEYS:
        if key not in env.variables:
            continue
        raw = env.variables[key]
        if key in BYTE_VARS:
            try:
                shown = f"{html.escape(raw)} ({human_bytes(float(raw))})"
            except ValueError:
                shown = html.escape(raw)
        else:
            shown = html.escape(raw) if raw else "<em>empty</em>"
        rows.append([code(key), shown])
    return (
        table_html(["Variable", "Value"], rows, "No configuration variables were collected.")
        + f'<p class="lead">{len(env.variables)} variables were collected in total; the full set is in '
          f'the collector output as {code("mariadb_variables")}.</p>'
    )


def render_methodology(env: Env, window: Window) -> str:
    started = env.text("collection_start")
    return f"""
<p>Every value in this report was gathered with read-only statements — {code('SELECT')} and
{code('SHOW')} only — plus reads of configuration files and system files. Nothing was written to
the server and no setting was changed.</p>
<p>Environment data was collected once{' at ' + html.escape(started) if started else ''} by
{code('mariadb-envcollect')}. Sampled metrics were collected by {code('mariadb-metrics')}
over {html.escape(format_window(window))}.</p>
<p>Counter rates are computed from the real elapsed time between samples rather than an assumed
interval. Values labelled <em>lifetime</em> are cumulative since the server last restarted and
describe the whole life of the server, not the collection window.</p>
<p>The exact query set is in the collector source, in
{code('internal/envcollect')} and {code('internal/metrics')}.</p>
<p><strong>Provenance.</strong> Everything in this report is mechanically generated unless it
carries an <span class="prov-chip prov-ai">AI ANALYSIS</span> or
<span class="prov-chip prov-human">DBA NOTE</span> label. Marked blocks are additions on top of the
measured data; remove them and the report still stands.</p>
"""


# ---------------------------------------------------------------------------
# Annotations — AI and human additions, kept separate from mechanical output
# ---------------------------------------------------------------------------


def load_annotations(path: Path | None) -> list[dict[str, str]]:
    """Load AI/human annotations to merge into the report.

    The generator owns the HTML; annotations are a separate keyed input, so an
    agent contributes structured data and cannot restyle mechanical content to
    look like a measurement.
    """
    if path is None or not path.exists():
        return []
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"warning: could not read annotations: {exc}", file=sys.stderr)
        return []
    if isinstance(data, dict):
        data = data.get("annotations", [])
    return [a for a in data if isinstance(a, dict)]


def render_annotations(annotations: list[dict[str, str]], anchor: str) -> str:
    out = []
    for note in annotations:
        if note.get("section", "") != anchor:
            continue
        source = note.get("source", "ai").lower()
        kind = "human" if source in ("human", "dba") else "ai"
        label = "DBA NOTE" if kind == "human" else "AI ANALYSIS"
        author = note.get("author", "Claude" if kind == "ai" else "")
        stamp = note.get("timestamp", "")
        meta = " · ".join(x for x in (label, author, stamp) if x)
        body = note.get("body", "")
        paragraphs = "".join(
            f"<p>{html.escape(p.strip())}</p>" for p in body.split("\n\n") if p.strip()
        )
        title = note.get("title", "")
        heading = f"<h4>{html.escape(title)}</h4>" if title else ""
        out.append(
            f'<div class="prov prov-{kind}">'
            f'<div class="prov-meta"><span class="prov-chip prov-{kind}">{html.escape(label)}</span>'
            f'<span class="prov-attr">{html.escape(meta[len(label):].lstrip(" ·"))}</span></div>'
            f"{heading}{paragraphs}</div>"
        )
    return "".join(out)


def render_credits(generated: datetime) -> str:
    stamp = generated.strftime("%Y-%m-%d %H:%M")
    return f"""
<section class="appendix">
  <h2>Credits</h2>
  <p>{html.escape(REPORT_TITLE)} created {html.escape(stamp)}</p>
  <p>Auditor: Claude Code with Vibe DBA skill</p>
  <p>Developed by @robertsilen based on DBA skills by @lefred and an idea by @kajarnocom</p>
  <p style="margin-top:10px">Install:</p>
  <pre>{html.escape(INSTALL_COMMANDS)}</pre>
</section>
"""


def x_tick_format(span_seconds: float) -> str:
    if span_seconds <= 600:
        return "%H:%M:%S"
    if span_seconds <= 86400:
        return "%H:%M"
    return "%m-%d %H:%M"


def build_x_axis(
    chart: Chart, left: int, plot_w: int
) -> tuple[list[float], list[tuple[float, str]]]:
    """Return per-point x positions and the (x, label) pairs for the axis.

    Points are placed at their true position in time, so an interval that
    stalled shows as a visibly wider gap.
    """
    count = max((len(v) for v in chart.series.values()), default=0)
    times = chart.times[:count] if chart.times else []
    known = [t for t in times if t is not None]

    if len(times) == count and len(known) >= 2 and known[-1] > known[0]:
        first, last = known[0], known[-1]
        span = (last - first).total_seconds()
        positions = []
        for idx in range(count):
            stamp = times[idx]
            if stamp is None:
                positions.append(left + plot_w * idx / max(count - 1, 1))
            else:
                positions.append(left + plot_w * (stamp - first).total_seconds() / span)
        fmt = x_tick_format(span)
        ticks = []
        tick_count = 5
        for i in range(tick_count + 1):
            x = left + plot_w * i / tick_count
            moment = first + timedelta_seconds(span * i / tick_count)
            ticks.append((x, moment.strftime(fmt)))
        return positions, ticks

    positions = [left + plot_w * i / max(count - 1, 1) for i in range(count)]
    return positions, []


def render_chart(chart: Chart, idx: int) -> str:
    width, height = 900, 260
    left, right, top, bottom = 52, 14, 14, 34
    plot_w = width - left - right
    plot_h = height - top - bottom
    values = [v for series in chart.series.values() for v in series if v is not None]
    ymin = min(values) if values else 0
    ymax = max(values) if values else 1
    if ymin > 0:
        ymin = 0
    if ymax == ymin:
        ymax = ymin + 1

    y_ticks = []
    for i in range(5):
        val = ymin + (ymax - ymin) * i / 4
        y = top + plot_h - (plot_h * i / 4)
        y_ticks.append((y, val))

    x_positions, x_ticks = build_x_axis(chart, left, plot_w)

    polylines = []
    legend = []
    for sidx, (name, raw_values) in enumerate(chart.series.items()):
        color = f"var(--series-{sidx % SERIES_COLORS + 1})"
        pts = []
        for i, value in enumerate(raw_values):
            if value is None or i >= len(x_positions):
                continue
            x = x_positions[i]
            y = top + plot_h - ((value - ymin) / (ymax - ymin) * plot_h)
            pts.append(f"{x:.1f},{y:.1f}")
        if pts:
            polylines.append(
                f'<polyline class="line" stroke="{color}" points="{" ".join(pts)}" />'
            )
            legend.append(
                f'<span><span class="swatch" style="background:{color}"></span>{html.escape(name)}</span>'
            )

    grid = []
    labels = []
    for y, val in y_ticks:
        grid.append(
            f'<line class="axis" x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" />'
        )
        labels.append(
            f'<text class="tick" x="8" y="{y+3:.1f}">{html.escape(format_number(val))}</text>'
        )
    for x, label in x_ticks:
        grid.append(
            f'<line class="vgrid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" />'
        )
        labels.append(
            f'<text class="xtick" x="{x:.1f}" y="{height-bottom+15}">{html.escape(label)}</text>'
        )
    grid.append(
        f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" />'
    )
    grid.append(
        f'<line class="axis" x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" />'
    )

    approx = (
        ' <span class="approx">approximate time</span>'
        if chart.time_source == TIME_EVEN
        else ""
    )

    return f"""
<article class="card" id="chart-{idx}">
  <h2>{html.escape(chart.title)} <span class="sub">({html.escape(chart.unit)})</span>{approx}</h2>
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(chart.title)}">
    {"".join(grid)}
    {"".join(labels)}
    {"".join(polylines)}
  </svg>
  <div class="legend">{"".join(legend)}</div>
</article>
"""


def html_to_text(document: str) -> str:
    """Plain-text rendering of the report, for reading in a terminal or by an agent.

    The HTML is the deliverable; this is a convenience view so nobody has to write
    their own parser to read the numbers.
    """
    text = re.sub(r"<script.*?</script>", "", document, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<svg.*?</svg>", "[chart]", text, flags=re.S)
    text = re.sub(r"<h1[^>]*>", "\n\n# ", text)
    text = re.sub(r"<h2[^>]*>", "\n\n## ", text)
    text = re.sub(r"<h3[^>]*>", "\n\n### ", text)
    text = re.sub(r"<h4[^>]*>", "\n\n#### ", text)
    text = re.sub(r'<div class="prov prov-(\w+)"[^>]*>', r"\n\n[\1 annotation]\n", text)
    text = re.sub(r"<(p|div|li|tr|br)[^>]*>", "\n", text)
    text = re.sub(r"</t[dh]>", " | ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\| *\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def format_number(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}k"
    if abs_value >= 10:
        return f"{value:.0f}"
    if abs_value >= 1:
        return f"{value:.1f}"
    return f"{value:.2f}"


def is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
