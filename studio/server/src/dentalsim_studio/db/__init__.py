"""The studio database (SQLite, research R12): the system of record shared by the studio and the command line.

`open_repos(data_dir)` is what `dentalsim.persistence.open_repos` calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path

from dentalsim.persistence import PublicationRepo, Repos, RunRepo, ScenarioRepo, VerificationRepo
from dentalsim_studio.db.connection import Database, connect, db_path
from dentalsim_studio.db.repos.capabilities import SqliteCapabilityRepo
from dentalsim_studio.db.repos.events import SqliteEventRepo
from dentalsim_studio.db.repos.jobs import SqliteJobRepo
from dentalsim_studio.db.repos.library import SqliteLibraryRepo
from dentalsim_studio.db.repos.settings import SqliteSettingsRepo


class SqliteRepos:
    """Every repository over one connection. Satisfies `dentalsim.persistence.Repos`."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @cached_property
    def library(self) -> SqliteLibraryRepo:
        return SqliteLibraryRepo(self.db)

    @cached_property
    def capabilities(self) -> SqliteCapabilityRepo:
        return SqliteCapabilityRepo(self.db)

    @cached_property
    def jobs(self) -> SqliteJobRepo:
        return SqliteJobRepo(self.db)

    @cached_property
    def events(self) -> SqliteEventRepo:
        return SqliteEventRepo(self.db)

    @cached_property
    def settings(self) -> SqliteSettingsRepo:
        return SqliteSettingsRepo(self.db)

    @property
    def scenarios(self) -> ScenarioRepo:
        raise NotImplementedError("the scenario repository arrives in task T084")

    @property
    def runs(self) -> RunRepo:
        raise NotImplementedError("the run repository arrives in task T084")

    @property
    def verification(self) -> VerificationRepo:
        raise NotImplementedError("the verification repository arrives in task T071")

    @property
    def publications(self) -> PublicationRepo:
        raise NotImplementedError("the publication repository arrives in task T150")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.db.transaction():
            yield

    def close(self) -> None:
        self.db.close()


def open_repos(data_dir: Path) -> SqliteRepos:
    return SqliteRepos(connect(db_path(data_dir)))


def _conforms(repos: SqliteRepos) -> Repos:
    """Never called: lets mypy check that `SqliteRepos` satisfies the engine's `Repos` protocol."""
    return repos
