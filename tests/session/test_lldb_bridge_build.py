"""LLDB bridge discovery and launch selection."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from gdb_mcp.session import lldb_bridge_build as build
from gdb_mcp.session.lldb_bridge_types import BridgeError


def test_configured_prefix_selects_its_versioned_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "19.1.7"
    library = prefix / "lib" / "liblldb.so.19"
    library.parent.mkdir(parents=True)
    library.touch()
    for name in ("LLDB_BRIDGE_EXECUTABLE", "LLDB_BRIDGE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLDB_PREFIX", str(prefix))

    install = build.discover_lldb()

    assert install.prefix == prefix
    assert install.liblldb == library
    assert install.version == 19


def test_prebuilt_launch_bypasses_compiler_and_install_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / "bridge"
    bridge.touch()
    layers = tmp_path / "layers"
    monkeypatch.setenv("LLDB_BRIDGE_EXECUTABLE", str(bridge))
    monkeypatch.setenv("LLDB_BRIDGE_LAYER_DIR", str(layers))
    discover = Mock(side_effect=AssertionError("prebuilt bridge must bypass discovery"))
    monkeypatch.setattr(build, "discover_lldb", discover)

    launch = build.prepare_bridge()

    assert launch.executable == bridge
    assert launch.layer_dir == layers
    assert launch.environment == dict(os.environ)
    discover.assert_not_called()


def test_missing_prebuilt_directory_keeps_its_lookup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLDB_BRIDGE_EXECUTABLE", raising=False)
    monkeypatch.setenv("LLDB_BRIDGE_DIR", str(tmp_path))

    with pytest.raises(BridgeError) as caught:
        build.discover_lldb()

    assert caught.value.code == "not_found"
    assert str(tmp_path) in str(caught.value)


def test_compiler_resolves_a_configured_name_through_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiler = tmp_path / "g++"
    monkeypatch.delenv("LLDB_BRIDGE_CXX", raising=False)
    monkeypatch.setenv("CXX", "g++")
    monkeypatch.setattr(shutil, "which", lambda name: str(compiler) if name == "g++" else None)

    assert build._compiler() == [str(compiler)]


def test_layer_cache_key_includes_the_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = build.LldbInstall(
        prefix=tmp_path / "lldb-18.1.8",
        lib_dir=tmp_path / "lib",
        liblldb=tmp_path / "lib" / "liblldb.so",
        bin_dir=tmp_path / "bin",
        include_dir=None,
        version=18,
    )
    captured: dict[str, str] = {}

    def capture(paths: list[Path], extra: str = "") -> str:
        captured["extra"] = extra
        return "digest"

    monkeypatch.setattr(build, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(build, "_compiler", lambda: ["/toolchains/a/c++"])
    monkeypatch.setattr(build, "_digest", capture)

    build._build_layers(install, 18)

    assert "/toolchains/a/c++" in captured["extra"]


def test_bridge_executable_name_is_platform_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows bridge artifacts are executables; POSIX ones have no suffix."""

    monkeypatch.setattr(sys, "platform", "linux")
    assert build._bridge_filename() == "gdb-mcp-lldb-bridge"
    monkeypatch.setattr(sys, "platform", "win32")
    assert build._bridge_filename() == "gdb-mcp-lldb-bridge.exe"


def test_windows_prefix_finds_the_runtime_dll_beside_the_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows toolchains put liblldb.dll under bin and the import library under lib."""

    monkeypatch.setattr(sys, "platform", "win32")
    prefix = tmp_path / "lldb"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "lib").mkdir()
    library = prefix / "bin" / "liblldb.dll"
    library.touch()

    install = build._find_install_from_prefix(prefix)

    assert install is not None
    assert install.liblldb == library
    assert install.lib_dir == prefix / "lib"
