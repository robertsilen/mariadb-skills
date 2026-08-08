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


PALETTE = [
    "#7dd3fc",
    "#86efac",
    "#fbbf24",
    "#f472b6",
    "#c4b5fd",
    "#fb7185",
    "#34d399",
    "#f97316",
]

# Timestamp formats written by the collector.
TS_FORMATS = ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d %H:%M:%S")

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
    parser.add_argument("path", nargs="?", help="metrics directory or .tar/.tgz package")
    parser.add_argument("-o", "--out", help="output HTML file")
    parser.add_argument("--max-points", type=int, default=900, help="max points per series")
    args = parser.parse_args()

    input_path = Path(args.path or input("Metrics directory or package path: ").strip())
    if not input_path.exists():
        raise SystemExit(f"input does not exist: {input_path}")

    workspace = None
    try:
        metrics_dir = input_path
        if input_path.is_file() and tarfile.is_tarfile(input_path):
            workspace = tempfile.mkdtemp(prefix="mariadb-metrics-report-")
            metrics_dir = extract_package(input_path, Path(workspace))

        window = read_window(metrics_dir)
        charts = build_charts(metrics_dir, window, args.max_points)
        output = Path(args.out) if args.out else default_output_path(input_path, metrics_dir)
        output.write_text(
            render_html(input_path, metrics_dir, window, charts), encoding="utf-8"
        )
        print(f"Wrote {output}")
        if not charts:
            print("No supported metric streams were found.", file=sys.stderr)
            return 2
        return 0
    finally:
        if workspace:
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
    input_path: Path, metrics_dir: Path, window: Window, charts: list[Chart]
) -> str:
    cards = "\n".join(render_chart(chart, idx) for idx, chart in enumerate(charts))
    summary = render_summary(window, charts)
    approx = any(chart.time_source == TIME_EVEN for chart in charts)
    note = (
        '<div class="note">Charts marked <em>approximate time</em> come from tools '
        "that do not timestamp their output (vmstat, mpstat, iostat); their points "
        "are spread evenly across the collection window.</div>"
        if approx
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MariaDB Metrics Report</title>
<style>
:root {{
  color-scheme: dark;
  --bg: #0b0f14;
  --panel: #111827;
  --panel2: #0f172a;
  --text: #d8dee9;
  --muted: #8b98a8;
  --grid: #263241;
  --accent: #f59e0b;
  --border: #223044;
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
  background: rgba(11, 15, 20, .94);
  border-bottom: 1px solid var(--border);
  padding: 18px 24px;
}}
h1 {{ margin: 0; font-size: 22px; font-weight: 650; letter-spacing: 0; }}
.sub {{ margin-top: 4px; color: var(--muted); font-size: 12px; }}
.window {{ margin-top: 6px; color: var(--accent); font-size: 13px; }}
main {{ padding: 20px 24px 36px; }}
.note {{ color: var(--muted); font-size: 12px; margin-bottom: 14px; }}
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
.stat .value {{ font-size: 24px; margin-top: 6px; color: #fff; }}
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
</style>
</head>
<body>
<header>
  <h1>MariaDB Metrics Report</h1>
  <div class="window">Collection window: {html.escape(format_window(window))}</div>
  <div class="sub">Input: {html.escape(str(input_path))} · Parsed directory: {html.escape(str(metrics_dir))}</div>
</header>
<main>
{summary}
{note}
<section class="grid">
{cards or '<div class="empty card">No supported metric streams found.</div>'}
</section>
</main>
</body>
</html>
"""


def render_summary(window: Window, charts: list[Chart]) -> str:
    points = 0
    series = 0
    for chart in charts:
        series += len(chart.series)
        points += sum(len(values) for values in chart.series.values())
    duration = format_duration(window.duration_s)
    return f"""
<section class="summary">
  <div class="stat"><div class="label">Collection duration</div><div class="value">{html.escape(duration)}</div></div>
  <div class="stat"><div class="label">Charts</div><div class="value">{len(charts)}</div></div>
  <div class="stat"><div class="label">Series</div><div class="value">{series}</div></div>
  <div class="stat"><div class="label">Data Points</div><div class="value">{points}</div></div>
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
        color = PALETTE[sidx % len(PALETTE)]
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
