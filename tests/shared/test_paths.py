"""shared.paths - where the apps keep their data on each platform."""

import sys
from pathlib import Path

from shared import paths
from shared.constants import APP_DATA_DIR_NAME


def test_windows_uses_programdata(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMDATA", r"C:\PD")
    assert paths.default_shared_data_dir().name == APP_DATA_DIR_NAME
    assert "PD" in str(paths.default_shared_data_dir())


def test_macos_uses_application_support(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    expected = Path.home() / "Library" / "Application Support" / APP_DATA_DIR_NAME
    assert paths.default_shared_data_dir() == expected


def test_linux_honours_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.default_shared_data_dir() == tmp_path / APP_DATA_DIR_NAME
    monkeypatch.delenv("XDG_DATA_HOME")
    assert paths.default_shared_data_dir() == Path.home() / ".local" / "share" / APP_DATA_DIR_NAME


def test_default_dir_is_pure_and_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nope"))
    paths.default_shared_data_dir()
    assert not (tmp_path / "nope").exists()


def test_not_frozen_has_no_exe_adjacent_config(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert paths._exe_adjacent_config_path() is None


def test_frozen_windows_looks_beside_the_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    exe = tmp_path / "POS_1.exe"
    exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths._exe_adjacent_config_path() == tmp_path.resolve() / "config.json"


def test_frozen_macos_looks_beside_the_app_bundle_not_inside_it(monkeypatch, tmp_path):
    macos_dir = tmp_path / "GPUSA-POS.app" / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    exe = macos_dir / "GPUSA-POS"
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths._exe_adjacent_config_path() == tmp_path.resolve() / "config.json"


def test_frozen_macos_outside_a_bundle_falls_back_to_the_exe_folder(monkeypatch, tmp_path):
    exe = tmp_path / "tool"
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths._exe_adjacent_config_path() == tmp_path.resolve() / "config.json"
