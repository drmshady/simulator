"""The job queue service used by the API and the worker (research R9, FR-019, FR-056).

A thin layer over `JobRepo`: it picks the lane for a job kind, summarises the queue for `GET /api/system`, and
emits a `job.failed` event when a job fails, in the same transaction as the status change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dentalsim.persistence import JobKind, JobLane, JobRecord, JobStatus, Repos
from dentalsim_studio.db.repos.jobs import ACTIVE_STATUSES, LANES
from dentalsim_studio.events import bus

PREVIEW_KINDS: frozenset[JobKind] = frozenset({"preview"})


def lane_for(kind: JobKind) -> JobLane:
    """Previews run in the preview lane; every other kind is heavy work."""
    return "preview" if kind in PREVIEW_KINDS else "heavy"


def enqueue(
    repos: Repos,
    kind: JobKind,
    payload: Mapping[str, Any],
    *,
    target_id: str | None = None,
    lane: JobLane | None = None,
    front: bool = False,
) -> JobRecord:
    """Queue a job at the back of its lane (default: `lane_for(kind)`), or before every queued job with `front`."""
    return repos.jobs.enqueue(kind, lane or lane_for(kind), payload, target_id=target_id, front=front)


def claim(repos: Repos, lane: JobLane) -> JobRecord | None:
    """The next job of the lane, now running, or None while the lane is busy or empty."""
    return repos.jobs.claim(lane)


def finish(repos: Repos, job_id: str, status: JobStatus, *, failure_reason: str | None = None) -> JobRecord:
    """End a job; a failure also emits `job.failed {job_id, kind, target_id, reason}`."""
    with repos.transaction():
        repos.jobs.finish(job_id, status, failure_reason=failure_reason)
        job = repos.jobs.get(job_id)
        assert job is not None
        if status == "failed":
            bus.emit(
                repos,
                "job.failed",
                {"job_id": job.id, "kind": job.kind, "target_id": job.target_id, "reason": failure_reason},
            )
        return job


def cancel(repos: Repos, job_id: str) -> JobRecord:
    """Cancel a queued job at once, or ask the worker to stop a running one. Raises `NotFound` for an unknown id."""
    return repos.jobs.request_cancel(job_id)


def requeue_interrupted(repos: Repos) -> list[JobRecord]:
    """Start-up recovery: jobs left running go back to the front of their lanes (FR-056)."""
    return repos.jobs.requeue_interrupted()


def active_jobs(repos: Repos, lane: JobLane | None = None) -> list[JobRecord]:
    """Queued and running jobs in queue order, by lane then position (`GET /api/queue`)."""
    return repos.jobs.list_jobs(lane=lane, statuses=ACTIVE_STATUSES)


def has_work(repos: Repos) -> bool:
    return bool(active_jobs(repos))


def queue_summary(repos: Repos) -> dict[str, dict[str, int]]:
    """`{"heavy": {"queued": n, "running": n}, "preview": {...}}`."""
    summary: dict[str, dict[str, int]] = {lane: {status: 0 for status in ACTIVE_STATUSES} for lane in LANES}
    for job in active_jobs(repos):
        summary[job.lane][job.status] += 1
    return summary
