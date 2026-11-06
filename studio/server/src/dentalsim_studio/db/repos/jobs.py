"""The `jobs` table: the worker queue (research R9, FR-019, FR-056).

Each lane (`heavy`, `preview`) is ordered by `position`: a new job goes to the back of its lane, or with
`front=True` before every queued job of the lane. A lane runs one job at a time, and the database enforces it:
`claim` checks for a running job of the lane inside the same `BEGIN IMMEDIATE` transaction that marks the next job
running, so two worker processes cannot run two jobs of one lane at once.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any, get_args

from dentalsim.clock import new_ulid, utc_now
from dentalsim.errors import Conflict, NotFound
from dentalsim.persistence import JobKind, JobLane, JobRecord, JobStatus
from dentalsim_studio.db.connection import Database, from_json, from_time, to_json, to_time

FINISHED_STATUSES: tuple[JobStatus, ...] = ("succeeded", "failed", "cancelled")
ACTIVE_STATUSES: tuple[JobStatus, ...] = ("queued", "running")
LANES: tuple[JobLane, ...] = get_args(JobLane)


def _job(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        lane=row["lane"],
        kind=row["kind"],
        payload=from_json(row["payload"]),
        target_id=row["target_id"],
        position=row["position"],
        status=row["status"],
        pid=row["pid"],
        heartbeat_at=from_time(row["heartbeat_at"]),
        attempts=row["attempts"],
        failure_reason=row["failure_reason"],
        cancel_requested=bool(row["cancel_requested"]),
        created_at=from_time(row["created_at"]),
        started_at=from_time(row["started_at"]),
        finished_at=from_time(row["finished_at"]),
    )


class SqliteJobRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(
        self,
        kind: JobKind,
        lane: JobLane,
        payload: Mapping[str, Any],
        *,
        target_id: str | None = None,
        front: bool = False,
    ) -> JobRecord:
        job_id = new_ulid()
        with self.db.transaction():
            position = self._front_position(lane) if front else None
            if position is None:
                row = self.db.one("SELECT MAX(position) AS last FROM jobs WHERE lane = ?", (lane,))
                position = (row["last"] if row and row["last"] is not None else 0) + 1
            self.db.execute(
                """INSERT INTO jobs (id, lane, kind, payload, target_id, position, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)""",
                (job_id, lane, kind, to_json(dict(payload)), target_id, position, to_time(utc_now())),
            )
            return self._require(job_id)

    def get(self, job_id: str) -> JobRecord | None:
        row = self.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return None if row is None else _job(row)

    def list_jobs(self, *, lane: JobLane | None = None, statuses: Sequence[JobStatus] | None = None) -> list[JobRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if lane is not None:
            clauses.append("lane = ?")
            params.append(lane)
        if statuses is not None:
            if not statuses:
                return []
            clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.all(f"SELECT * FROM jobs {where} ORDER BY lane, position, created_at, id", params)
        return [_job(row) for row in rows]

    def recent_finished(self, *, limit: int, exclude_kinds: Sequence[JobKind] = ()) -> list[JobRecord]:
        excluded = f"AND kind NOT IN ({', '.join('?' for _ in exclude_kinds)})" if exclude_kinds else ""
        rows = self.db.all(
            f"""SELECT * FROM jobs WHERE status IN ('succeeded', 'failed', 'cancelled') {excluded}
                ORDER BY finished_at DESC, id DESC LIMIT ?""",
            [*exclude_kinds, limit],
        )
        return [_job(row) for row in rows]

    def claim(self, lane: JobLane) -> JobRecord | None:
        with self.db.transaction():
            if self.db.one("SELECT 1 FROM jobs WHERE lane = ? AND status = 'running' LIMIT 1", (lane,)) is not None:
                return None
            row = self.db.one(
                "SELECT id FROM jobs WHERE lane = ? AND status = 'queued' ORDER BY position, created_at, id LIMIT 1",
                (lane,),
            )
            if row is None:
                return None
            now = to_time(utc_now())
            self.db.execute(
                """UPDATE jobs SET status = 'running', started_at = ?, heartbeat_at = ?, attempts = attempts + 1,
                       pid = NULL, finished_at = NULL
                   WHERE id = ? AND status = 'queued'""",
                (now, now, row["id"]),
            )
            return self._require(row["id"])

    def heartbeat(self, job_id: str, *, pid: int | None = None) -> None:
        self.db.execute(
            "UPDATE jobs SET heartbeat_at = ?, pid = COALESCE(?, pid) WHERE id = ? AND status = 'running'",
            (to_time(utc_now()), pid, job_id),
        )

    def finish(self, job_id: str, status: JobStatus, *, failure_reason: str | None = None) -> None:
        """End a queued or running job. `failed` needs a reason; `succeeded` takes none."""
        if status not in FINISHED_STATUSES:
            raise ValueError(f"a job can only finish as {', '.join(FINISHED_STATUSES)}, not {status!r}")
        if status == "failed" and not failure_reason:
            raise ValueError("a failed job needs a failure reason")
        if status == "succeeded" and failure_reason:
            raise ValueError("a succeeded job has no failure reason")
        with self.db.transaction():
            job = self._require(job_id)
            if job.status not in ACTIVE_STATUSES:
                raise Conflict(f"job {job_id} has already ended ({job.status})")
            self.db.execute(
                "UPDATE jobs SET status = ?, failure_reason = ?, finished_at = ?, pid = NULL WHERE id = ?",
                (status, failure_reason, to_time(utc_now()), job_id),
            )

    def request_cancel(self, job_id: str) -> JobRecord:
        """A queued job is cancelled at once; a running one is flagged for the worker. An ended job is unchanged."""
        with self.db.transaction():
            job = self._require(job_id)
            if job.status == "running":
                self.db.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
            elif job.status in ("queued", "interrupted"):
                self.db.execute(
                    """UPDATE jobs SET status = 'cancelled', cancel_requested = 1, finished_at = ?, pid = NULL
                       WHERE id = ?""",
                    (to_time(utc_now()), job_id),
                )
            return self._require(job_id)

    def requeue_interrupted(self) -> list[JobRecord]:
        """Jobs left running become interrupted, then queued at the front of their lane in their old order (FR-056).

        Attempts and a pending cancel request are kept: a job whose cancel was asked for before the restart is
        cancelled by its handler as soon as it is claimed again.
        """
        requeued: list[str] = []
        with self.db.transaction():
            for lane in LANES:
                rows = self.db.all(
                    "SELECT id, position FROM jobs WHERE lane = ? AND status = 'running' ORDER BY position, created_at",
                    (lane,),
                )
                if not rows:
                    continue
                ids = [row["id"] for row in rows]
                marks = ", ".join("?" for _ in ids)
                self.db.execute(f"UPDATE jobs SET status = 'interrupted' WHERE id IN ({marks})", ids)
                front = self.db.one(
                    "SELECT MIN(position) AS first FROM jobs WHERE lane = ? AND status = 'queued'", (lane,)
                )
                first = front["first"] if front else None
                for index, row in enumerate(rows):
                    position = row["position"] if first is None else first - len(rows) + index
                    self.db.execute(
                        """UPDATE jobs SET status = 'queued', position = ?, pid = NULL, heartbeat_at = NULL,
                               started_at = NULL
                           WHERE id = ?""",
                        (position, row["id"]),
                    )
                requeued.extend(ids)
            return [self._require(job_id) for job_id in requeued]

    def _front_position(self, lane: JobLane) -> int | None:
        row = self.db.one("SELECT MIN(position) AS first FROM jobs WHERE lane = ? AND status = 'queued'", (lane,))
        return None if row is None or row["first"] is None else int(row["first"]) - 1

    def _require(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job is None:
            raise NotFound(f"no job {job_id}")
        return job
