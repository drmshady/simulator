"""Verification, read routes (contracts/studio-api.md "Verification"; FR-035, FR-036).

- `GET /api/verification/cases`: each case file with its quantities, expected values, tolerances, required flag and
  tolerance history.
- `GET /api/verification/reports/{id}`: a stored report, as the Physics page links to it.

Running the suite from the studio (`POST /api/verification/run`) arrives with Story 5 (task T127); until then the
owner runs `dentalsim verify` on the main PC.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from dentalsim.errors import NotFound
from dentalsim.verification.cases import load_cases
from dentalsim_studio.api.deps import ReposDep

router = APIRouter(prefix="/api/verification", tags=["verification"])


@router.get("/cases")
def cases() -> list[dict[str, Any]]:
    return [
        {**loaded.case.model_dump(mode="json", by_alias=True), "file": loaded.path.name, "sha256": loaded.sha256}
        for loaded in load_cases()
    ]


@router.get("/reports/{report_id}")
def report(report_id: str, repos: ReposDep) -> dict[str, Any]:
    found = repos.verification.get_report(report_id)
    if found is None:
        raise NotFound(f"there is no verification report {report_id}")
    return found
