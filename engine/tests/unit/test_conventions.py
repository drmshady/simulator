"""Conventions regression tests (SC-014, FR-050, research R6) and `dentalsim conventions check`.

Broken documents are made by editing a copy of the real docs/conventions.md, so the tests follow the document as it
changes.
"""

import importlib
import importlib.util
import json
import math
from collections.abc import Iterator
from pathlib import Path

import pytest

from dentalsim import cli
from dentalsim.commands import conventions as conventions_command
from dentalsim.config import get_settings
from dentalsim.conventions import codegen
from dentalsim.conventions.frame import force_direction, is_right_handed
from dentalsim.conventions.loader import (
    LoadedConventions,
    get_conventions,
    get_loaded_conventions,
    load_conventions,
    parse_conventions,
)
from dentalsim.conventions.schema import LIBRARY_CATEGORIES, SEMVER, is_parameter_key
from dentalsim.errors import ConventionsMissing

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCUMENT = REPO_ROOT / "docs" / "conventions.md"


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    get_settings.cache_clear()
    get_loaded_conventions.cache_clear()
    yield
    get_settings.cache_clear()
    get_loaded_conventions.cache_clear()


def _text() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


def _edit(old: str, new: str, text: str | None = None) -> str:
    text = _text() if text is None else text
    assert text.count(old) == 1, f"expected {old!r} exactly once in docs/conventions.md"
    return text.replace(old, new)


def _parse(text: str) -> LoadedConventions:
    return parse_conventions(text, DOCUMENT)


# --- the block ---------------------------------------------------------------------------------------------------


def test_the_block_parses_and_has_a_semver_version() -> None:
    loaded = _parse(_text())
    assert SEMVER.match(loaded.version)
    assert len(loaded.content_hash) == 64
    assert get_loaded_conventions().version == loaded.version


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("```yaml conventions\n", "```yaml\n", "has no ```yaml conventions``` block"),
        ("\nunits:\n", "\nunits: [unclosed\n", "not valid YAML"),
        ("  radius_mm: 1.0\n", "  radius_mm: 1.0\n  load_patch_shape: disc\n", "load_patch_shape"),
        ("  length: mm\n", "  length: cm\n", "units.length"),
        ('version: "1.1.0"\n', 'version: "1.1.0-draft"\n', "semantic version"),
        ('version: "1.1.0"\n', 'version: "1.1"\n', "semantic version"),
        ("  limit: 0.05\n", "", "mesh_check.limit"),
    ],
    ids=["missing block", "invalid yaml", "unknown key", "wrong unit", "not semver", "two-part version", "missing"],
)
def test_broken_blocks_are_refused_with_the_reason(old: str, new: str, message: str) -> None:
    with pytest.raises(ConventionsMissing, match=message) as raised:
        _parse(_edit(old, new))
    if message == "load_patch_shape":
        assert "Extra inputs are not permitted" in str(raised.value)


def test_two_blocks_are_refused() -> None:
    with pytest.raises(ConventionsMissing, match="more than one"):
        _parse(_text() + "\n```yaml conventions\nversion: 1.0.0\n```\n")


def test_a_missing_document_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConventionsMissing, match="cannot read"):
        load_conventions(tmp_path / "docs" / "conventions.md")


def test_the_hash_ignores_comments_and_formatting_but_not_values() -> None:
    original = _parse(_text()).content_hash
    reformatted = _edit("  length: mm\n", "  length:   mm   # millimetres\n")
    reformatted = _edit('version: "1.1.0"\n', 'version: "1.1.0"\n# a comment inside the block\n', reformatted)
    reformatted = _edit(
        "      mesial: {centre_deg: 0, half_width_deg: 45}\n",
        "      mesial:\n        half_width_deg: 45\n        centre_deg: 0\n",
        reformatted,
    )
    assert _parse(reformatted).content_hash == original
    assert _parse(_edit("  limit: 0.05\n", "  limit: 0.04\n")).content_hash != original


# --- frame, load angle, regions ----------------------------------------------------------------------------------


def test_the_frame_is_right_handed() -> None:
    assert is_right_handed(get_conventions())


def test_positive_load_angle_tilts_lingually_and_apically() -> None:
    oblique = force_direction(30.0, 90.0)
    assert oblique[1] < 0 and oblique[2] < 0
    assert oblique == pytest.approx([0.0, -math.sin(math.radians(30)), -math.cos(math.radians(30))], abs=1e-12)
    assert force_direction(0.0, 90.0) == pytest.approx([0.0, 0.0, -1.0], abs=1e-15)
    assert float((oblique**2).sum()) == pytest.approx(1.0)


def test_every_region_named_by_a_registered_block_type_exists() -> None:
    # Assumed registry API (task T060): `registry.all_block_types()` returns the registered block types, each with
    # `type_id`, `report_regions` (region ids) and `result_types`, whose items may name `regions` of their own.
    if importlib.util.find_spec("dentalsim.blocks.registry") is None:
        pytest.skip("no block types are registered yet (task T060)")
    registry = importlib.import_module("dentalsim.blocks.registry")
    block_types = list(registry.all_block_types())
    if not block_types:
        pytest.skip("no block types are registered yet (task T060)")
    known = set(get_conventions().regions)
    unknown: dict[str, list[str]] = {}
    for block_type in block_types:
        named = set(block_type.report_regions)
        for result_type in getattr(block_type, "result_types", ()):
            named |= set(getattr(result_type, "regions", ()))
        if named - known:
            unknown[block_type.type_id] = sorted(named - known)
    assert not unknown, f"block types name regions that docs/conventions.md does not define: {unknown}"


def test_library_units_frost_edges_and_strength_rules_fit_together() -> None:
    conventions = get_conventions()
    assert {row.category for row in conventions.library_units} <= set(LIBRARY_CATEGORIES)
    for edge in conventions.frost_scale.edges:
        assert is_parameter_key(edge), edge
        row = conventions.library_unit_for("frost_window", edge)
        assert row is not None, f"no frost_window library unit row matches {edge}"
        assert row.units == conventions.strain_measure.unit
    for material_class, materials in conventions.material_classes.items():
        for material in materials:
            rules = [rule for rule in conventions.strength_rules if rule.material_class == material_class]
            assert len(rules) == 1, f"{material} has {len(rules)} strength rules"
            assert conventions.strength_rule_for(material) == rules[0]


# --- generated colour scales -------------------------------------------------------------------------------------


def test_the_generated_typescript_is_up_to_date() -> None:
    path = REPO_ROOT / codegen.GENERATED_TS_PATH
    assert path.is_file(), f"{path} is missing: run `{codegen.REGENERATE_COMMAND}`"
    expected = codegen.render_typescript(get_loaded_conventions())
    assert path.read_text(encoding="utf-8") == expected, f"{path} is out of date: run `{codegen.REGENERATE_COMMAND}`"


def test_the_generated_typescript_exports_the_scales() -> None:
    loaded = get_loaded_conventions()
    text = codegen.render_typescript(loaded)
    assert text == codegen.render_typescript(loaded)
    exports = [line.split(":")[0] for line in text.splitlines() if line.startswith("export const ")]
    assert exports == [
        "export const CONVENTIONS_VERSION",
        "export const CONVENTIONS_HASH",
        "export const UNITS",
        "export const TEACHING_NOTICE",
        "export const FROST_SCALE",
        "export const CONTINUOUS_SCALE",
    ]
    assert text.startswith(
        f"// Generated from docs/conventions.md version {loaded.version} (hash {loaded.content_hash})"
    )
    assert "strainSymbol: 'µε'" in text
    for category in loaded.conventions.frost_scale.categories:
        assert f"colour: '{category.colour}'" in text


# --- the command -------------------------------------------------------------------------------------------------


def _run(*argv: str) -> int:
    return cli.main(["conventions", *argv], modules=[conventions_command])


def _repo_copy(root: Path, monkeypatch: pytest.MonkeyPatch, text: str | None = None) -> Path:
    """A repository root holding only docs/conventions.md, made the current one."""
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "conventions.md").write_text(_text() if text is None else text, encoding="utf-8")
    monkeypatch.setenv("DENTALSIM_REPO_ROOT", str(root))
    get_settings.cache_clear()
    get_loaded_conventions.cache_clear()
    return root


def test_check_prints_the_version_hash_and_path(capsys: pytest.CaptureFixture[str]) -> None:
    loaded = get_loaded_conventions()
    assert _run("check") == 0
    out = capsys.readouterr().out
    assert f"conventions {loaded.version}" in out
    assert f"hash: {loaded.content_hash}" in out
    assert _run("check", "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["version"] == loaded.version
    assert body["content_hash"] == loaded.content_hash
    assert Path(body["path"]).samefile(DOCUMENT)
    assert body["generated_typescript"]["up_to_date"] is True


def test_check_exits_2_when_the_conventions_are_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo_copy(tmp_path, monkeypatch, _edit("  length: mm\n", "  length: cm\n"))
    assert _run("check") == 2
    err = capsys.readouterr().err
    assert err.startswith("error: the conventions block is invalid")
    assert "units.length" in err
    assert _run("check", "--json") == 2
    assert json.loads(capsys.readouterr().out)["code"] == "error"


def test_check_exits_2_when_the_document_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DENTALSIM_REPO_ROOT", str(tmp_path))
    get_settings.cache_clear()
    assert _run("check") == 2
    assert "cannot read" in capsys.readouterr().err


def test_check_refuses_a_stale_generated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo_copy(tmp_path, monkeypatch)
    assert _run("check") == 1
    err = capsys.readouterr().err
    assert codegen.REGENERATE_COMMAND in err
    assert "is missing" in err

    assert codegen.main() == 0
    assert (root / codegen.GENERATED_TS_PATH).is_file()
    capsys.readouterr()
    assert _run("check") == 0
    assert "is up to date" in capsys.readouterr().out

    old_hash = get_loaded_conventions().content_hash
    changed = _edit("  limit: 0.05\n", "  limit: 0.04\n")
    (root / "docs" / "conventions.md").write_text(changed, encoding="utf-8")
    get_loaded_conventions.cache_clear()
    assert _run("check", "--json") == 1
    body = json.loads(capsys.readouterr().out)
    assert body["code"] == "conventions_codegen_stale"
    assert f"(hash {old_hash[:12]})" in body["problems"][0]["message"]


def test_codegen_main_exits_2_on_invalid_conventions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo_copy(tmp_path, monkeypatch, _edit('version: "1.1.0"\n', "version: one\n"))
    assert codegen.main() == 2
    assert "semantic version" in capsys.readouterr().err
    assert not (root / codegen.GENERATED_TS_PATH).exists()
