"""Conventions 1.1.0: every field the engine reads is required, and the rule fields admit only what the engine
implements (`dentalsim conventions check` fails otherwise)."""

import re
from pathlib import Path

import pytest

from dentalsim.config import CONVENTIONS_PATH, get_settings
from dentalsim.conventions.loader import parse_conventions
from dentalsim.errors import ConventionsMissing


def _text() -> str:
    root = get_settings().repo_root
    assert root is not None
    return (root / CONVENTIONS_PATH).read_text(encoding="utf-8")


def _parse(text: str) -> object:
    return parse_conventions(text, Path("conventions.md"))


REQUIRED_LINES = [
    ("  anatomical_quadrants: [maxillary_right, mandibular_left]\n", "anatomical_quadrants"),
    ("  other_quadrants: mirrored\n", "other_quadrants"),
    ("  angle_range_deg: [0, 90]\n", "angle_range_deg"),
    ("  crown_stats_exclude_radius_mm: 2.0\n", "crown_stats_exclude_radius_mm"),
    ("  selection: face_centroid_within_radius\n", "selection"),
    ("  application: uniform_traction\n", "application"),
    ("  source: integration_points\n", "source"),
    ("  ip_volume: jacobian_times_gauss_weight\n", "ip_volume"),
    ("  percentile_method: smallest_value_with_cumulative_volume_fraction_at_least_p\n", "percentile_method"),
    ("    implant_surface: core_radius\n", "implant_surface"),
    ("    crest_plane: {normal: implant_axis, through: axis_crest_intersection}\n", "crest_plane"),
    ("    sector_boundary: first_included\n", "sector_boundary"),
    ("    sector_direction: projected_to_occlusal_plane\n", "sector_direction"),
    ("moment_tolerance: 1.0e-5\n", "moment_tolerance"),
]


@pytest.mark.parametrize(("line", "field"), REQUIRED_LINES, ids=[f for _, f in REQUIRED_LINES])
def test_a_missing_field_fails_the_check(line: str, field: str) -> None:
    text = _text()
    assert line in text
    with pytest.raises(ConventionsMissing, match=field):
        _parse(text.replace(line, "", 1))


@pytest.mark.parametrize("section", ["analysis", "detail_levels", "mesh_check"])
def test_a_missing_section_fails_the_check(section: str) -> None:
    text = re.sub(rf"(?m)^{section}:\n(?:  .*\n)+", "", _text(), count=1)
    with pytest.raises(ConventionsMissing, match=section):
        _parse(text)


def test_the_formula_is_required() -> None:
    text = re.sub(r'(?m)^  formula: ".*"\n', "", _text(), count=1)
    with pytest.raises(ConventionsMissing, match="formula"):
        _parse(text)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("  angle_range_deg: [0, 90]\n", "  angle_range_deg: [-90, 90]\n", "angle_range_deg"),
        ("  angle_range_deg: [0, 90]\n", "  angle_range_deg: [0, 120]\n", "angle_range_deg"),
        ("  other_quadrants: mirrored\n", "  other_quadrants: native\n", "other_quadrants"),
        (
            "  anatomical_quadrants: [maxillary_right, mandibular_left]\n",
            "  anatomical_quadrants: [maxillary_left, mandibular_left]\n",
            "anatomical_quadrants",
        ),  # fmt: skip
        (
            "  effective_poisson_ratio: material_nu\n",
            "  effective_poisson_ratio: material\n",
            "effective_poisson_ratio",
        ),
        ("  contact: none\n", "  contact: frictional\n", "contact"),
        ("  vary: mesh_density_only\n", "  vary: geometry_and_mesh\n", "vary"),
        ("    - {region: pontic,", "    - {region: bridge,", "not defined: bridge"),
    ],
)
def test_rules_the_engine_does_not_implement_are_refused(old: str, new: str, message: str) -> None:
    text = _text()
    assert old in text
    with pytest.raises(ConventionsMissing, match=message):
        _parse(text.replace(old, new, 1))


def test_a_number_is_still_accepted_for_the_effective_poisson_ratio() -> None:
    text = _text().replace("  effective_poisson_ratio: material_nu\n", "  effective_poisson_ratio: 0.5\n", 1)
    loaded = parse_conventions(text, Path("conventions.md"))
    assert loaded.conventions.strain_measure.effective_poisson_ratio == 0.5


def test_the_version_is_quoted_and_1_1_0() -> None:
    assert 'version: "1.1.0"\n' in _text()


def test_tooth_tissues_are_not_compared_and_use_the_material_unit_rows() -> None:
    loaded = parse_conventions(_text(), Path("conventions.md"))
    c = loaded.conventions
    for tissue in ("enamel", "dentin", "pdl", "gutta_percha"):
        assert c.material_class_of(tissue) == "not_compared"
        assert c.library_unit_for("material", f"material.{tissue}.E") is not None
        assert c.library_unit_for("material", f"material.{tissue}.nu") is not None
