"""Per-project background server management for the CLI.

The CLI starts one MCP server per project on first use, remembers its
address and bearer token in a cache-directory cookie, and reuses it for
later commands. Cookies are keyed by the nearest VCS root (or the current
directory) so subdirectories share one server.

Only servers started this way run with an idle timeout; a manually started
``gdb-mcp-server`` never idle-exits.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

import httpx

from gdb_mcp.contracts import TOOL_SESSION_QUERY

from .runtime import invoke_tool

_STATE_DIR_ENV = "GDB_MCP_STATE_DIR"
_IDLE_ENV = "GDB_MCP_DAEMON_IDLE_SEC"
_AUTH_TOKEN_ENV = "GDB_MCP_AUTH_TOKEN"
_DEFAULT_IDLE_SEC = 900.0
_READY_TIMEOUT_SEC = 20.0
_PROBE_TIMEOUT_SEC = 3.0
_STOP_TIMEOUT_SEC = 10.0


class DaemonError(RuntimeError):
    """Raised when the background server cannot be started or reached."""


@dataclass(frozen=True)
class DaemonPaths:
    """Cache paths for one project's background server."""

    root: Path
    cookie: Path
    lock: Path
    ready: Path
    log: Path


@dataclass(frozen=True)
class ResolvedServer:
    """The server endpoint selected for one CLI invocation."""

    url: str
    token: str | None
    spawned: bool
    managed: bool = False


@dataclass(frozen=True)
class StopResult:
    """Outcome of a daemon stop request."""

    stopped: bool
    message: str


def state_dir() -> Path:
    """Return the platform-appropriate per-user cache directory."""

    override = os.environ.get(_STATE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "gdb-mcp"


def project_root(start: Path | None = None) -> Path:
    """Return the nearest VCS root containing the start directory, else itself."""

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / ".hg").exists():
            return candidate
    return current


def daemon_paths(root: Path | None = None) -> DaemonPaths:
    """Return the cookie/lock/log paths for one project root."""

    resolved_root = project_root() if root is None else root
    digest = hashlib.sha256(str(resolved_root).encode("utf-8")).hexdigest()[:16]
    base = state_dir()
    return DaemonPaths(
        root=resolved_root,
        cookie=base / "daemons" / f"{digest}.json",
        lock=base / "locks" / f"{digest}.lock",
        ready=base / "ready" / f"{digest}.json",
        log=base / "logs" / f"{digest}.log",
    )


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive per-project lock across cookie maintenance."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":  # pragma: no cover - Windows only
            import msvcrt

            msvcrt.locking(handle, msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":  # pragma: no cover - Windows only
                import msvcrt

                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


def _read_cookie(paths: DaemonPaths) -> dict[str, object] | None:
    try:
        data = json.loads(paths.cookie.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_cookie(paths: DaemonPaths, data: dict[str, object]) -> None:
    paths.cookie.parent.mkdir(parents=True, exist_ok=True)
    temporary = paths.cookie.with_suffix(paths.cookie.suffix + ".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, paths.cookie)


def _remove_cookie(paths: DaemonPaths) -> None:
    paths.cookie.unlink(missing_ok=True)


async def _server_reachable(url: str, token: str | None) -> bool:
    """Probe one server with a cheap MCP call."""

    try:
        await asyncio.wait_for(
            invoke_tool(url, TOOL_SESSION_QUERY, {"action": "list"}, auth_token=token),
            timeout=_PROBE_TIMEOUT_SEC,
        )
    except Exception:
        return False
    return True


def _split_base(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, f"{parts.scheme}://{parts.netloc}"


def _log_tail(paths: DaemonPaths, lines: int = 20) -> str:
    try:
        content = paths.log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _wait_for_ready(paths: DaemonPaths, process: subprocess.Popen[bytes]) -> dict[str, object]:
    deadline = time.monotonic() + _READY_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if paths.ready.exists():
            try:
                data = json.loads(paths.ready.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict) and "port" in data:
                return data
        exit_code = process.poll()
        if exit_code is not None:
            raise DaemonError(f"background server exited with code {exit_code}\n{_log_tail(paths)}")
        time.sleep(0.05)
    raise DaemonError(f"background server did not become ready\n{_log_tail(paths)}")


async def _spawn(paths: DaemonPaths) -> ResolvedServer:
    token = os.environ.get(_AUTH_TOKEN_ENV) or secrets.token_urlsafe(32)
    idle_sec = os.environ.get(_IDLE_ENV, str(_DEFAULT_IDLE_SEC))
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    paths.ready.unlink(missing_ok=True)

    command = [
        sys.executable,
        "-m",
        "gdb_mcp.server",
        "--transport",
        "streamable-http",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--path",
        "/mcp",
        "--ready-file",
        str(paths.ready),
        "--idle-timeout-sec",
        idle_sec,
    ]
    child_env = dict(os.environ)
    child_env[_AUTH_TOKEN_ENV] = token
    creationflags = 0
    if os.name == "nt":  # pragma: no cover - Windows only
        # Detach from the console so the daemon survives terminal close and
        # does not receive the console's Ctrl-C.
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    with open(paths.log, "ab") as log_handle:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
            close_fds=True,
            env=child_env,
        )

    try:
        ready = _wait_for_ready(paths, process)
    except DaemonError:
        process.kill()
        process.wait(timeout=5)
        raise

    url = f"http://{ready['host']}:{ready['port']}{ready['path']}"
    deadline = time.monotonic() + _READY_TIMEOUT_SEC
    while not await _server_reachable(url, token):
        if process.poll() is not None or time.monotonic() >= deadline:
            process.kill()
            process.wait(timeout=5)
            raise DaemonError(f"background server failed its readiness probe\n{_log_tail(paths)}")
        await asyncio.sleep(0.1)

    _write_cookie(
        paths,
        {
            "pid": process.pid,
            "url": url,
            "token": token,
            "log": str(paths.log),
            "started_at": time.time(),
        },
    )
    return ResolvedServer(url, token, True, True)


async def resolve_server(*, explicit_url: str | None) -> ResolvedServer:
    """Resolve the server for one invocation, starting a daemon when needed."""

    if explicit_url:
        return ResolvedServer(explicit_url, os.environ.get(_AUTH_TOKEN_ENV), False, False)

    paths = daemon_paths()
    with _exclusive_lock(paths.lock):
        cookie = _read_cookie(paths)
        if cookie is not None:
            url = cookie.get("url")
            token = cookie.get("token")
            if isinstance(url, str) and await _server_reachable(
                url, token if isinstance(token, str) else None
            ):
                return ResolvedServer(url, token if isinstance(token, str) else None, False, True)
            _remove_cookie(paths)
        return await _spawn(paths)


def remembered_session_id() -> int | None:
    """Return the session recorded for the current project, if any."""

    cookie = _read_cookie(daemon_paths())
    value = cookie.get("last_session_id") if cookie is not None else None
    return value if isinstance(value, int) else None


def remember_session_id(session_id: int) -> None:
    """Record the project's most recent session in its cookie."""

    paths = daemon_paths()
    with _exclusive_lock(paths.lock):
        cookie = _read_cookie(paths) or {}
        cookie["last_session_id"] = session_id
        _write_cookie(paths, cookie)


def forget_session_id() -> None:
    """Drop the project's remembered session, if any."""

    paths = daemon_paths()
    with _exclusive_lock(paths.lock):
        cookie = _read_cookie(paths)
        if cookie is None or "last_session_id" not in cookie:
            return
        cookie.pop("last_session_id", None)
        _write_cookie(paths, cookie)


async def daemon_status() -> str:
    """Describe the background server for the current project."""

    paths = daemon_paths()
    cookie = _read_cookie(paths)
    if cookie is None:
        return f"no background server for {paths.root}"
    url = cookie.get("url")
    token = cookie.get("token")
    if isinstance(url, str) and await _server_reachable(
        url, token if isinstance(token, str) else None
    ):
        return f"running: {url} (pid {cookie.get('pid')})"
    _remove_cookie(paths)
    return f"stale cookie removed for {paths.root}"


async def stop_daemon(*, force: bool = False) -> StopResult:
    """Ask the background server to exit, with a signal fallback."""

    paths = daemon_paths()
    cookie = _read_cookie(paths)
    if cookie is None:
        return StopResult(True, f"no background server for {paths.root}")

    url = cookie.get("url")
    token = cookie.get("token")
    pid = cookie.get("pid")
    if not isinstance(url, str):
        _remove_cookie(paths)
        return StopResult(True, f"removed invalid cookie for {paths.root}")

    scheme, base = _split_base(url)
    headers = {"Authorization": f"Bearer {token}"} if isinstance(token, str) else {}
    request_error: httpx.HTTPError | None = None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{base}/shutdown",
                params={"force": "1" if force else "0"},
                headers=headers,
                timeout=_STOP_TIMEOUT_SEC,
            )
        except httpx.HTTPError as exc:
            response = None
            request_error = exc

    if response is not None and response.status_code == 409:
        message = response.json().get("message", "background server refused to stop")
        return StopResult(False, str(message))
    if response is not None and response.status_code >= 400:
        return StopResult(False, f"background server returned HTTP {response.status_code}")

    if isinstance(pid, int):
        deadline = time.monotonic() + _STOP_TIMEOUT_SEC
        while time.monotonic() < deadline and _process_alive(pid):
            await asyncio.sleep(0.1)
        if _process_alive(pid) and os.name != "nt":
            os.kill(pid, 15)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and _process_alive(pid):
                await asyncio.sleep(0.1)

    _remove_cookie(paths)
    if request_error is not None and isinstance(pid, int) and _process_alive(pid):
        return StopResult(False, f"could not stop background server: {request_error}")
    return StopResult(True, f"stopped background server for {paths.root}")


def _process_alive(pid: int) -> bool:
    """Return whether one process is still running without signaling it."""

    if os.name == "nt":  # pragma: no cover - Windows only
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            result = kernel32.WaitForSingleObject(handle, 0)
        finally:
            kernel32.CloseHandle(handle)
        return bool(result == wait_timeout)

    if pid <= 0:
        return False
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        if reaped == pid:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        state = stat.rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return True
    return state != "Z"
