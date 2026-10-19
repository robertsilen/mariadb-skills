"""Shared plumbing for the Vibe DBA collector plugin.

Query results are written in the `mariadb` command-line client's formats —
`-B` (tab-separated, header row), `-N -B` (no header) and `\\G` (vertical) — which
is what the report generator (`collector/scripts/mariadb-report.py`) parses, and
what a person reading the raw files expects.
"""

import datetime
import os
import shutil
import socket
import subprocess
import tarfile

from mysqlsh import DBError

COLLECTOR_VERSION = "1.0"


def timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def datestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def quote_identifier(name):
    return "`" + str(name).replace("`", "``") + "`"


# ---------------------------------------------------------------------------
# Rendering result sets the way the mariadb client does
# ---------------------------------------------------------------------------


def _text(value):
    if value is None:
        return "NULL"
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _escape(text):
    # The client escapes these in batch mode unless --raw is given.
    return (text.replace("\\", "\\\\").replace("\0", "\\0")
            .replace("\t", "\\t").replace("\n", "\\n"))


def format_batch(columns, rows, header=True):
    # The client prints nothing at all, not even a header, for an empty result.
    if not rows:
        return ""
    lines = []
    if header:
        lines.append("\t".join(columns))
    for row in rows:
        lines.append("\t".join(
            "NULL" if v is None else _escape(_text(v)) for v in row))
    return "".join(line + "\n" for line in lines)


def format_vertical(columns, rows):
    width = max((len(c) for c in columns), default=0)
    out = []
    for n, row in enumerate(rows, 1):
        out.append("*" * 27 + f" {n}. row " + "*" * 27 + "\n")
        for name, value in zip(columns, row):
            out.append(f"{name:>{width}}: {_text(value)}\n")
    return "".join(out)


def format_error(error):
    if isinstance(error, DBError):
        state = f" ({error.sqlstate})" if getattr(error, "sqlstate", "") else ""
        return f"ERROR {error.code}{state}: {error.msg}\n"
    return f"ERROR: {error}\n"


# ---------------------------------------------------------------------------
# Collection context
# ---------------------------------------------------------------------------


class Context:
    def __init__(self, session, output_dir, host_info, verbose):
        self.session = session
        self.output_dir = output_dir
        self.host_info = host_info
        self.verbose = verbose

    def log(self, message):
        if self.verbose:
            print(message, flush=True)

    def path(self, name):
        # Output names are flat; never let one escape the output directory.
        return os.path.join(self.output_dir, os.path.basename(name))

    def write(self, name, text):
        with open(self.path(name), "w", encoding="utf-8") as f:
            f.write(text)

    def append(self, name, text):
        with open(self.path(name), "a", encoding="utf-8") as f:
            f.write(text)

    def record_error(self, what, error):
        self.append("collector-errors.log", f"{what}: {error}\n")

    # -- SQL ---------------------------------------------------------------

    def query(self, sql):
        """Return (columns, rows) for one statement. Raises on SQL errors."""
        result = self.session.run_sql(sql)
        columns = [c.column_label for c in result.get_columns()]
        return columns, [tuple(row) for row in result.fetch_all()]

    def query_text(self, sql, mode="batch"):
        """Render a statement's result as the mariadb client would.

        mode: "batch" (-B), "plain" (-N -B) or "vertical" (\\G). Errors are
        rendered into the text rather than raised, as the client prints them.
        """
        try:
            columns, rows = self.query(sql)
        except Exception as error:
            return format_error(error)
        if mode == "vertical":
            return format_vertical(columns, rows)
        return format_batch(columns, rows, header=(mode == "batch"))

    def capture_query(self, name, sql, mode="batch"):
        self.write(name, self.query_text(sql, mode))

    def scalar(self, sql):
        try:
            _, rows = self.query(sql)
        except Exception:
            return ""
        if not rows or rows[0][0] is None:
            return ""
        return _text(rows[0][0]).strip()

    # -- Host --------------------------------------------------------------

    def command_output(self, args, shell=False, timeout=300):
        """Run a command; return (combined output, error message or None)."""
        if not shell and shutil.which(args[0]) is None:
            return "", f'executable file not found in $PATH: "{args[0]}"'
        try:
            proc = subprocess.run(args, shell=shell, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return "", str(error)
        out = proc.stdout.decode("utf-8", "replace")
        if proc.returncode != 0:
            return out, f"exit status {proc.returncode}"
        return out, None

    def capture_command(self, name, args, shell=False):
        out, error = self.command_output(args, shell=shell)
        if error:
            out += f"\nERROR: {error}\n"
        self.write(name, out)

    def capture_file(self, name, source):
        try:
            with open(source, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return
        except OSError as error:
            self.record_error(name, error)
            return
        with open(self.path(name), "wb") as f:
            f.write(data)


def run_collectors(ctx, collectors):
    """Run (name, enabled, function) entries in order, isolating failures."""
    for name, enabled, func in collectors:
        if not enabled:
            ctx.log(f"skip {name}")
            continue
        ctx.log(f"collect {name}")
        try:
            func(ctx)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            ctx.record_error(name, error)


# ---------------------------------------------------------------------------
# Session and host checks
# ---------------------------------------------------------------------------


def open_collection_session(shell):
    """A private copy of the global session, so collection never changes the
    user's current schema or session state."""
    from mysqlsh import Error
    if shell.get_session() is None:
        raise Error("Not connected. Start the shell with a connection, for "
                    "example: mariadb-shell root@localhost")
    session = shell.open_session()
    if shell.parse_uri(session.uri).get("scheme") == "mysqlx":
        raise Error("Connect with the classic protocol (mariadb:// or mysql://).")
    return session


def server_is_local(shell, session):
    """True when the server runs on the host the shell runs on.

    Operating-system data is read from the shell's host, so it only describes
    the database server when the two are the same machine.
    """
    uri = shell.parse_uri(session.uri)
    if uri.get("socket"):
        return True
    if uri.get("host", "localhost") in ("localhost", "127.0.0.1", "::1", ""):
        return True
    try:
        server = session.run_sql("SELECT @@hostname").fetch_one()[0]
    except Exception:
        return False
    local = socket.gethostname()
    return bool(server) and server.split(".")[0] == local.split(".")[0]


def resolve_host_info(shell, session, requested):
    if not requested:
        return False
    if server_is_local(shell, session):
        return True
    print("NOTE: the server is not on this host, so operating-system data is "
          "skipped. Run the shell on the database server to include it.")
    return False


def server_hostname(ctx):
    return ctx.scalar("SELECT @@hostname") or socket.gethostname() or "localhost"


def write_provenance(ctx, shell, kind):
    try:
        shell_version = shell.version
    except Exception:
        shell_version = "unknown"
    ctx.write("collector",
              f"vibeDba {kind} {COLLECTOR_VERSION} (MariaDB Shell plugin, "
              f"shell {shell_version}); hostInfo={str(ctx.host_info).lower()}\n")


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def finalize(output_dir, package, cleanup):
    output_dir = os.path.normpath(output_dir)
    package_path = None
    if package:
        package_path = output_dir + ".tgz"
        with tarfile.open(package_path, "w:gz") as tar:
            tar.add(output_dir, arcname=os.path.basename(output_dir))
    if cleanup:
        shutil.rmtree(output_dir)

    if package and cleanup:
        print(f"Wrote {package_path} and removed {output_dir}")
    elif package:
        print(f"Wrote {output_dir} and {package_path}")
    elif cleanup:
        print(f"Removed {output_dir}")
    else:
        print(f"Wrote {output_dir}")
    return package_path or output_dir
