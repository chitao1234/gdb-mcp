"""Discover LLDB installations and build/cache the native SB API bridge.

The core uses the vendored 4.0.1 SB API and links through an unversioned
liblldb stub. Optional newer APIs are compiled as separately loaded layers.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .lldb_bridge_types import BridgeError, BridgeLaunch

_NATIVE_DIR = Path(__file__).with_name("lldb") / "native"
_VENDOR_INCLUDE = Path(__file__).with_name("lldb") / "vendor" / "include"
_BRIDGE_NAME = "gdb-mcp-lldb-bridge"
_LAYER_NAMES: dict[int, str] = {
    7: "errors",
    8: "args",
    16: "siginfo",
    18: "watchpoints",
    19: "address",
}
_LAYER_SOURCES: dict[int, str] = {
    7: "layer7.cpp",
    8: "layer8.cpp",
    16: "layer16.cpp",
    18: "layer18.cpp",
    19: "layer19.cpp",
}


@dataclass(frozen=True, slots=True)
class LldbInstall:
    """One discovered LLDB installation."""

    prefix: Path
    lib_dir: Path
    liblldb: Path
    bin_dir: Path
    include_dir: Path | None
    version: int | None

    @property
    def environment(self) -> dict[str, str]:
        """Return loader-path variables that select this liblldb."""

        env = dict(os.environ)
        if sys.platform == "darwin":
            key = "DYLD_LIBRARY_PATH"
        elif sys.platform == "win32":
            key = "PATH"
        else:
            key = "LD_LIBRARY_PATH"
        parts = [str(self.lib_dir)]
        if sys.platform == "win32" and self.bin_dir.is_dir():
            parts.append(str(self.bin_dir))
        existing = env.get(key)
        if existing:
            parts.append(existing)
        env[key] = os.pathsep.join(parts)
        debugserver = self.bin_dir / (
            "lldb-server.exe" if sys.platform == "win32" else "lldb-server"
        )
        if debugserver.is_file():
            env.setdefault("LLDB_DEBUGSERVER_PATH", str(debugserver))
        return env


def _version_from_path(path: Path) -> int | None:
    for part in reversed(path.parts):
        match = re.search(r"(?<!\d)(\d{1,2})(?:\.\d+){1,2}(?!\d)", part)
        if match:
            return int(match.group(1))
        if part.isdigit() and 4 <= int(part) <= 99:
            return int(part)
    return None


def _lib_name() -> str:
    if sys.platform == "darwin":
        return "liblldb.dylib"
    if sys.platform == "win32":
        return "liblldb.dll"
    return "liblldb.so"


def _find_install_from_prefix(prefix: Path) -> LldbInstall | None:
    lib_dir = prefix / "lib"
    liblldb = lib_dir / _lib_name()
    if not liblldb.exists():
        # Nix and some distro layouts keep the versioned library only.
        matches = sorted(lib_dir.glob("liblldb.so*")) if lib_dir.is_dir() else []
        if not matches:
            matches = sorted(lib_dir.glob("liblldb.*.dylib")) if lib_dir.is_dir() else []
        if not matches:
            return None
        liblldb = matches[0]
    candidate_include = prefix / "include"
    include_dir: Path | None = (
        candidate_include if (candidate_include / "lldb" / "API").is_dir() else None
    )
    return LldbInstall(
        prefix=prefix,
        lib_dir=lib_dir,
        liblldb=liblldb,
        bin_dir=prefix / "bin",
        include_dir=include_dir,
        version=_version_from_path(prefix),
    )


def _candidate_prefixes(debugger_path: str | None) -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("LLDB_PREFIX") or os.environ.get("LLDB_PATH")
    if configured:
        path = Path(configured).expanduser()
        if path.name.startswith("lldb") and path.parent.name == "bin":
            path = path.parent.parent
        candidates.append(path)
    library = os.environ.get("LLDB_LIBRARY")
    if library:
        path = Path(library).expanduser()
        candidates.append(path.parent.parent if path.name.startswith("liblldb") else path)
    if debugger_path:
        path = Path(debugger_path).expanduser()
        if path.is_dir():
            candidates.append(path)
        elif path.name.startswith("liblldb"):
            candidates.append(path.parent.parent)
        else:
            resolved_text = shutil.which(debugger_path) or str(path)
            if resolved_text:
                candidates.append(Path(resolved_text).resolve().parent.parent)
    for name in ("lldb", "lldb-23", "lldb-22", "lldb-21", "lldb-20", "lldb-19"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found).resolve().parent.parent)
    candidates.extend(
        (
            Path("/opt/lldb/current"),
            Path("/opt/lldb"),
            Path("/usr/local/opt/llvm"),
            Path("/usr/local"),
            Path("/usr"),
        )
    )
    if Path("/opt/lldb").is_dir():
        candidates.extend(sorted(Path("/opt/lldb").glob("*"), reverse=True))
    if Path("/usr/lib").is_dir():
        candidates.extend(sorted(Path("/usr/lib").glob("llvm-*"), reverse=True))
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Library/Developer/CommandLineTools/usr"),
                Path("/Applications/Xcode.app/Contents/Developer/usr"),
            ]
        )
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved_path = candidate.resolve()
        except OSError:
            continue
        if resolved_path not in seen:
            seen.add(resolved_path)
            unique.append(candidate)
    return unique


def discover_lldb(debugger_path: str | None = None) -> LldbInstall:
    """Find one usable LLDB installation.

    ``LLDB_BRIDGE_EXECUTABLE`` and ``LLDB_BRIDGE_DIR`` bypass discovery for a
    prebuilt bridge. Otherwise ``LLDB_LIBRARY``, ``LLDB_PREFIX``/``LLDB_PATH``,
    the selected ``lldb`` binary, and common LLVM installation roots are tried.
    """

    prebuilt = os.environ.get("LLDB_BRIDGE_EXECUTABLE")
    if prebuilt:
        path = Path(prebuilt).expanduser().resolve()
        if not path.is_file():
            raise BridgeError(f"LLDB_BRIDGE_EXECUTABLE is not a file: {path}", "not_found")
    prebuilt_dir = os.environ.get("LLDB_BRIDGE_DIR")
    if prebuilt_dir:
        path = Path(prebuilt_dir).expanduser() / _BRIDGE_NAME
        if path.is_file():
            return LldbInstall(
                prefix=path.parent,
                lib_dir=path.parent,
                liblldb=path,
                bin_dir=path.parent,
                include_dir=None,
                version=None,
            )
        raise BridgeError(f"LLDB_BRIDGE_DIR does not contain {_BRIDGE_NAME}: {path}", "not_found")

    tried: list[str] = []
    for prefix in _candidate_prefixes(debugger_path):
        tried.append(str(prefix))
        install = _find_install_from_prefix(prefix)
        if install is not None:
            return install
    raise BridgeError(
        "Could not find liblldb. Set LLDB_LIBRARY (for example /opt/lldb/19/lib/liblldb.so), "
        "LLDB_PREFIX, or LLDB_BRIDGE_EXECUTABLE. Tried: " + ", ".join(tried[:12]),
        "not_found",
    )


def _compiler() -> list[str]:
    configured = os.environ.get("LLDB_BRIDGE_CXX") or os.environ.get("CXX")
    if configured:
        return [configured]
    for name in ("c++", "g++", "clang++"):
        found = shutil.which(name)
        if found:
            return [found]
    raise BridgeError(
        "No C++ compiler found to build the LLDB bridge. Set LLDB_BRIDGE_CXX or "
        "LLDB_BRIDGE_EXECUTABLE.",
        "not_found",
    )


def _cache_root() -> Path:
    configured = os.environ.get("LLDB_BRIDGE_CACHE")
    if configured:
        root = Path(configured).expanduser()
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        root = (
            Path(base).expanduser() / "gdb-mcp" / "lldb"
            if base
            else Path.home() / ".cache" / "gdb-mcp" / "lldb"
        )
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            root = Path(tempfile.gettempdir()) / "gdb-mcp-lldb"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _digest(paths: list[Path], extra: str = "") -> str:
    digest = hashlib.sha256(extra.encode())
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _run(
    command: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env or os.environ),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise BridgeError(f"Could not run {command[0]}: {exc}", "not_found") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BridgeError(
            f"LLDB bridge build failed: {' '.join(command)}\n{detail}",
            "build_failed",
        )


def _undefined_lldb_symbols(object_file: Path) -> list[str]:
    result = subprocess.run(
        ["nm", "-u", str(object_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BridgeError(f"nm failed for {object_file}: {result.stderr.strip()}", "build_failed")
    symbols: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and "lldb" in parts[-1]:
            symbols.append(parts[-1])
    return sorted(set(symbols))


def _write_stub(compiler: list[str], object_file: Path, output: Path) -> None:
    symbols = _undefined_lldb_symbols(object_file)
    if not symbols:
        raise BridgeError(f"No liblldb symbols found in {object_file}", "build_failed")
    assembly = [".text"]
    for symbol in symbols:
        assembly.append(f".globl {symbol}")
        if sys.platform != "darwin":
            assembly.append(f".type {symbol},@function")
        assembly.extend([f"{symbol}:", "  ret"])
    source = output.with_suffix(".s")
    source.write_text("\n".join(assembly) + "\n", encoding="utf-8")
    if sys.platform == "darwin":
        flags = ["-dynamiclib", "-Wl,-install_name,liblldb.dylib"]
    else:
        flags = ["-shared", "-Wl,-soname,liblldb.so"]
    _run(compiler + ["-fPIC"] + flags + ["-x", "assembler", str(source), "-o", str(output)])


def _build_core(install: LldbInstall) -> Path:
    cache = _cache_root()
    key = _digest(
        [_NATIVE_DIR / "core.cpp", _NATIVE_DIR / "layer_api.h"],
        f"core:{sys.platform}:{_compiler()}",
    )
    build = cache / f"core-{key}"
    bridge = build / _BRIDGE_NAME
    if bridge.is_file():
        return bridge
    build.mkdir(parents=True, exist_ok=True)
    compiler = _compiler()
    object_file = build / "core.o"
    _run(
        compiler
        + [
            "-std=c++17",
            "-O2",
            "-fPIC",
            "-pthread",
            f"-I{_VENDOR_INCLUDE}",
            f"-I{_NATIVE_DIR}",
            "-c",
            str(_NATIVE_DIR / "core.cpp"),
            "-o",
            str(object_file),
        ]
    )
    stub = build / _lib_name()
    _write_stub(compiler, object_file, stub)
    if sys.platform == "darwin":
        rpath = "@loader_path"
        flags: list[str] = []
    elif sys.platform == "win32":
        raise BridgeError(
            "Building the LLDB bridge on Windows requires a prebuilt bridge; "
            "set LLDB_BRIDGE_EXECUTABLE.",
            "build_failed",
        )
    else:
        rpath = "$ORIGIN"
        flags: list[str] = []
    _run(
        compiler
        + flags
        + [
            "-O2",
            "-o",
            str(bridge),
            str(object_file),
            f"-L{build}",
            "-llldb",
            f"-Wl,-rpath,{rpath}",
            "-pthread",
        ]
        + (["-ldl"] if sys.platform.startswith("linux") else [])
    )
    return bridge


def _build_layers(install: LldbInstall, runtime_version: int | None) -> Path:
    cache = _cache_root()
    compiler = _compiler()
    layer_dir = cache / (
        f"layers-{install.version or 'unknown'}-"
        f"{_digest([_NATIVE_DIR / 'layer_api.h', *(_NATIVE_DIR / name for name in _LAYER_SOURCES.values())], str(install.prefix))}"
    )
    layer_dir.mkdir(parents=True, exist_ok=True)
    include = install.include_dir
    if include is None:
        return layer_dir
    for version, source_name in _LAYER_SOURCES.items():
        minimum_version = {7: 7, 8: 8, 16: 14, 18: 17, 19: 19}[version]
        if runtime_version is not None and runtime_version < minimum_version:
            continue
        output = (
            layer_dir
            / f"lldb_layer{version}_{_LAYER_NAMES[version]}{'.dylib' if sys.platform == 'darwin' else '.so'}"
        )
        if output.is_file():
            continue
        build = layer_dir / f"build{version}"
        build.mkdir(exist_ok=True)
        object_file = build / "layer.o"
        try:
            _run(
                compiler
                + [
                    "-std=c++17",
                    "-O2",
                    "-fPIC",
                    f"-I{include}",
                    f"-I{_NATIVE_DIR}",
                    "-c",
                    str(_NATIVE_DIR / source_name),
                    "-o",
                    str(object_file),
                ]
            )
            stub = build / _lib_name()
            _write_stub(compiler, object_file, stub)
            flags = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared"]
            _run(
                compiler
                + flags
                + [
                    "-O2",
                    "-o",
                    str(output),
                    str(object_file),
                    f"-L{build}",
                    "-llldb",
                ]
            )
        except BridgeError:
            # A layer whose API is not in this LLDB release simply stays absent.
            continue
    return layer_dir


def prepare_bridge(debugger_path: str | None = None) -> BridgeLaunch:
    """Locate or build the bridge for the selected LLDB installation."""

    prebuilt = os.environ.get("LLDB_BRIDGE_EXECUTABLE")
    if prebuilt:
        executable = Path(prebuilt).expanduser().resolve()
        layer_dir = (
            Path(os.environ["LLDB_BRIDGE_LAYER_DIR"]).expanduser()
            if os.environ.get("LLDB_BRIDGE_LAYER_DIR")
            else None
        )
        return BridgeLaunch(
            executable=executable, layer_dir=layer_dir, environment=dict(os.environ)
        )

    install = discover_lldb(debugger_path)
    if install.liblldb == install.prefix / _BRIDGE_NAME:
        return BridgeLaunch(
            executable=install.liblldb, layer_dir=None, environment=dict(os.environ)
        )
    bridge = _build_core(install)
    layers = _build_layers(install, install.version)
    environment = install.environment
    if layers.is_dir() and any(layers.glob("lldb_layer*")):
        environment["LLDB_BRIDGE_LAYER_DIR"] = str(layers)
    return BridgeLaunch(executable=bridge, layer_dir=layers, environment=environment)
