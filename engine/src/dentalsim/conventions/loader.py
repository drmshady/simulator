"""Read the `yaml conventions` block from docs/conventions.md (FR-050, research R6).

`get_conventions()` is what the engine and the studio call. It raises `ConventionsMissing` when the document or
its block is missing or invalid, so nothing runs without valid conventions.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from dentalsim.config import CONVENTIONS_PATH, get_settings
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import ConventionsMissing

BLOCK = re.compile(r"^```yaml conventions[ \t]*\r?\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)


class LoadedConventions(BaseModel):
    """The validated block, its content hash and where it was read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conventions: Conventions
    content_hash: str
    path: Path

    @property
    def version(self) -> str:
        return self.conventions.version


def conventions_path() -> Path:
    root = get_settings().repo_root
    if root is None:
        raise ConventionsMissing(f"cannot find {CONVENTIONS_PATH.as_posix()}: no repository root was found")
    return root / CONVENTIONS_PATH


def extract_block(text: str) -> str:
    """The text of the single fenced `yaml conventions` block."""
    blocks = BLOCK.findall(text)
    if not blocks:
        raise ConventionsMissing("docs/conventions.md has no ```yaml conventions``` block")
    if len(blocks) > 1:
        raise ConventionsMissing("docs/conventions.md has more than one ```yaml conventions``` block")
    return str(blocks[0])


def content_hash(data: Any) -> str:
    """SHA-256 of the parsed block as canonical JSON, so formatting and comments do not change it."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_conventions(text: str, path: Path) -> LoadedConventions:
    try:
        data = yaml.safe_load(extract_block(text))
    except yaml.YAMLError as exc:
        raise ConventionsMissing(f"the conventions block is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConventionsMissing("the conventions block must be a YAML mapping")
    try:
        conventions = Conventions.model_validate(data)
    except ValidationError as exc:
        raise ConventionsMissing(f"the conventions block is invalid:\n{exc}") from exc
    return LoadedConventions(conventions=conventions, content_hash=content_hash(data), path=path)


def load_conventions(path: Path | None = None) -> LoadedConventions:
    path = path or conventions_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConventionsMissing(f"cannot read {path}: {exc.strerror or exc}") from exc
    return parse_conventions(text, path)


@functools.cache
def get_loaded_conventions() -> LoadedConventions:
    return load_conventions()


def get_conventions() -> Conventions:
    """The current conventions. Cached; tests call `get_loaded_conventions.cache_clear()` after changing them."""
    return get_loaded_conventions().conventions
