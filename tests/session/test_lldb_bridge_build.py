"""LLDB bridge discovery and launch selection."""

from __future__ import annotations

import os
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
