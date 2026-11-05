"""Engine settings from the environment, and the data folder layout (research R10, R12)."""

from __future__ import annotations

import functools
import ipaddress
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, IPvAnyNetwork, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CONVENTIONS_PATH = Path("docs") / "conventions.md"
DATA_SUBFOLDERS = ("store", "exports", "published-media", "logs", "backups", "certs", "work", "estimate")


def find_repo_root(start: Path) -> Path | None:
    """The nearest folder at or above `start` that holds docs/conventions.md."""
    start = start.resolve()
    for folder in (start, *start.parents):
        if (folder / CONVENTIONS_PATH).is_file():
            return folder
    return None


def _default_repo_root() -> Path | None:
    return find_repo_root(Path.cwd()) or find_repo_root(Path(__file__).parent)


class Settings(BaseSettings):
    """Read from `DENTALSIM_*` environment variables.

    `home_subnets` is a comma-separated list such as `192.168.1.0/24`. When it is empty, only loopback connections
    are accepted.
    """

    model_config = SettingsConfigDict(env_prefix="DENTALSIM_", extra="ignore", frozen=True, populate_by_name=True)

    data_dir: Path = Field(default=Path(r"D:\DentalSimData"), validation_alias="DENTALSIM_DATA")
    ccx: Path | None = None
    repo_root: Path | None = Field(default_factory=_default_repo_root)
    home_subnets: Annotated[tuple[IPvAnyNetwork, ...], NoDecode] = ()
    port: int = Field(default=8443, ge=1, le=65535)

    @field_validator("home_subnets", mode="before")
    @classmethod
    def _parse_subnets(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list | tuple):
            # Accept a host address with a prefix, such as the main PC's own 192.168.1.20/24.
            return tuple(ipaddress.ip_network(part, strict=False) if isinstance(part, str) else part for part in value)
        return value


@functools.cache
def get_settings() -> Settings:
    return Settings()


def ensure_data_folder(settings: Settings | None = None) -> Path:
    """Create the data folder and its subfolders if they are missing, and return it."""
    data_dir = (settings or get_settings()).data_dir
    for name in DATA_SUBFOLDERS:
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    return data_dir
