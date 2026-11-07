"""The `backup` and `restore` job handlers (task T121; FR-034).

Both run in the heavy lane, so a restore never overlaps a run. The outcome is kept in the settings as `last_backup`
or `last_restore` (shown by `GET /api/system`), and announced with `backup.finished` or `restore.finished`.

- **backup**: payload `{to}` (a folder on the main PC, or null for `<data>/backups`)
- **restore**: payload `{archive}`; refused, with nothing changed, when any file does not match the manifest
"""

from __future__ import annotations

from pathlib import Path

from dentalsim.backup import LAST_BACKUP, LAST_RESTORE, create_backup, restore_backup
from dentalsim.config import ensure_data_folder
from dentalsim_studio.events import bus
from dentalsim_studio.worker.handlers import JobContext, register


@register("backup")
def backup_job(ctx: JobContext) -> None:
    to = ctx.payload.get("to")
    result = create_backup(
        ctx.repos,
        ensure_data_folder(),
        Path(to) if to else None,
        finished_job_id=ctx.job_id,
        progress=lambda percent, stage: ctx.progress(percent, stage),
        should_stop=ctx.should_stop,
    )
    ctx.repos.settings.set(LAST_BACKUP, result.model_dump(mode="json"))
    bus.emit(ctx.repos, "backup.finished", {"job_id": ctx.job_id, "archive": result.archive})


@register("restore")
def restore_job(ctx: JobContext) -> None:
    result = restore_backup(
        ctx.repos,
        ensure_data_folder(),
        Path(ctx.payload["archive"]),
        keep_job_id=ctx.job_id,
        progress=lambda percent, stage: ctx.progress(percent, stage),
    )
    ctx.repos.settings.set(LAST_RESTORE, result.model_dump(mode="json"))
    bus.emit(ctx.repos, "restore.finished", {"job_id": ctx.job_id, "archive": result.archive})
