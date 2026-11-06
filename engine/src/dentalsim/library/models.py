"""The literature library (data-model.md "Literature library", research R13).

- A `LiteratureEntry` is a parameter's identity, such as `bone.cancellous.E.D4`.
- A `LiteratureEntryVersion` holds its values. It is immutable once approved; an edit creates a new version
  (FR-016), and only approved versions can be chosen (FR-014).
- A `Source` is a cited paper with a DOI or a PMID, and the result of checking that identifier (FR-054).
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from dentalsim.conventions.schema import LibraryCategory

SourceCheckStatus = Literal["not_checked", "resolved_match", "resolved_mismatch", "unresolved"]
StudyType = Literal["experimental", "clinical", "fea", "review", "standard", "textbook"]
EntryStatus = Literal["proposed", "approved", "rejected", "superseded"]
Origin = Literal["claude_proposal", "owner"]
Resolver = Literal["crossref", "ncbi"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceMatch(_Model):
    """What the resolver returned, and how it compared with the source as cited."""

    first_author: bool
    year: bool
    title_similarity: float = Field(ge=0, le=1)
    resolved_first_author: str | None = None
    resolved_year: int | None = None
    resolved_title: str | None = None


class SourceCheck(_Model):
    status: SourceCheckStatus = "not_checked"
    checked_at: AwareDatetime | None = None
    resolver: Resolver | None = None
    matched: SourceMatch | None = None
    detail: str | None = None


class Source(_Model):
    id: str
    authors: list[str] = Field(min_length=1)
    """First author first; the first author's surname is required."""
    year: int = Field(ge=1800, le=2100)
    title: str = Field(min_length=1)
    journal: str | None = None
    doi: str | None = None
    pmid: str | None = None
    study_type: StudyType
    check: SourceCheck = SourceCheck()

    @model_validator(mode="after")
    def _identifier(self) -> Source:
        if not (self.doi or self.pmid):
            raise ValueError(f"source {self.id!r} needs a DOI or a PMID")
        return self


class LiteratureEntry(_Model):
    id: str
    parameter_key: str
    category: LibraryCategory
    label: str
    current_version: int | None = None
    """The newest approved version, or None while no version is approved."""
    created_at: AwareDatetime


class ValueBySource(_Model):
    source_id: str
    value: float
    note: str | None = None


class LiteratureEntryVersion(_Model):
    entry_id: str
    version: int = Field(ge=1)
    units: str
    typical: float
    range_min: float
    range_max: float
    values_by_source: list[ValueBySource] = Field(min_length=1)
    notes: str | None = None
    status: EntryStatus = "proposed"
    origin: Origin
    proposed_at: AwareDatetime
    approved_at: AwareDatetime | None = None
    rejected_at: AwareDatetime | None = None
    rejected_reason: str | None = None
    superseded_at: AwareDatetime | None = None
    content_hash: str

    @model_validator(mode="after")
    def _range(self) -> LiteratureEntryVersion:
        if not self.range_min <= self.typical <= self.range_max:
            span = f"{self.range_min:g}–{self.range_max:g}"
            raise ValueError(f"typical {self.typical:g} must lie within the range {span}")
        for item in self.values_by_source:
            if not self.range_min <= item.value <= self.range_max:
                raise ValueError(
                    f"the value {item.value:g} from source {item.source_id!r} lies outside the range "
                    f"{self.range_min:g}–{self.range_max:g}"
                )
        return self

    def contains(self, value: float) -> bool:
        return self.range_min <= value <= self.range_max

    def source_value(self, source_id: str) -> float | None:
        return next((v.value for v in self.values_by_source if v.source_id == source_id), None)


def version_content_hash(
    *,
    parameter_key: str,
    category: str,
    units: str,
    typical: float,
    range_min: float,
    range_max: float,
    values_by_source: list[ValueBySource],
    sources: list[Source],
    notes: str | None,
) -> str:
    """SHA-256 over everything that defines a version's values and what they cite (research R14).

    Source check results and status dates are not included: they do not change what a run used.
    """
    by_id = {s.id: s for s in sources}
    cited = []
    for item in values_by_source:
        source = by_id.get(item.source_id)
        identity = {"doi": source.doi, "pmid": source.pmid} if source else {"id": item.source_id}
        cited.append({"source": identity, "value": float(item.value), "note": item.note})
    record = {
        "parameter_key": parameter_key,
        "category": category,
        "units": units,
        "typical": float(typical),
        "range": [float(range_min), float(range_max)],
        "values_by_source": cited,
        "notes": notes,
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
