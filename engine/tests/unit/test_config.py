"""Settings, the data folder and the shared test fixtures."""

from ipaddress import ip_network
from pathlib import Path

import pytest
from ulid import ULID

from dentalsim import clock
from dentalsim.config import DATA_SUBFOLDERS, Settings, find_repo_root, get_settings
from dentalsim.testing import FIXED_TIME, FixedClock, FixedUlids


def test_data_folder_fixture_creates_every_subfolder(data_dir: Path) -> None:
    assert get_settings().data_dir == data_dir
    assert sorted(p.name for p in data_dir.iterdir()) == sorted(DATA_SUBFOLDERS)


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DENTALSIM_CCX", r"D:\DentalSimTools\calculix\ccx.exe")
    monkeypatch.setenv("DENTALSIM_HOME_SUBNETS", "192.168.1.20/24, 10.0.0.0/8")
    monkeypatch.setenv("DENTALSIM_PORT", "9443")
    settings = Settings()
    assert settings.ccx == Path(r"D:\DentalSimTools\calculix\ccx.exe")
    assert settings.home_subnets == (ip_network("192.168.1.0/24"), ip_network("10.0.0.0/8"))
    assert settings.port == 9443


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DENTALSIM_DATA", "DENTALSIM_CCX", "DENTALSIM_HOME_SUBNETS", "DENTALSIM_PORT"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.data_dir == Path(r"D:\DentalSimData")
    assert settings.ccx is None
    assert settings.home_subnets == ()
    assert settings.port == 8443


def test_repo_root_is_the_folder_holding_the_conventions(tmp_path: Path) -> None:
    nested = tmp_path / "engine" / "src"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) is None
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "conventions.md").write_text("# Conventions\n")
    assert find_repo_root(nested) == tmp_path.resolve()


def test_fixed_time(fixed_time: FixedClock) -> None:
    assert clock.utc_now() == FIXED_TIME
    fixed_time.advance(minutes=5)
    assert (clock.utc_now() - FIXED_TIME).total_seconds() == 300


def test_fixed_ulids(fixed_ulids: FixedUlids) -> None:
    first, second = clock.new_ulid(), clock.new_ulid()
    assert first < second
    assert ULID.from_str(first).datetime == FIXED_TIME
    replay = FixedUlids()
    assert [str(replay()), str(replay())] == [first, second]
