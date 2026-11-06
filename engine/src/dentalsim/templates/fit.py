"""Template fit rules: whether a template suits the jaw segment it is used on (FR-011 `template_misfit`).

Each rule names what must change when it fails. The rules read the design values of the scenario's blocks by the
template's instance ids; a rule about a block the owner has since removed is skipped.

- `region_teeth` (`min`, `max`): the jaw segment's region spans `min` to `max` teeth.
- `site_positions` (`positions`, `label`): the site's tooth position (FDI second digit) is one of `positions`.
- `site_space` (`block`, `setting`, `max_excess_mm`): the block's width `setting` exceeds the mean width of the
  site's tooth (the jaw segment's tooth widths) by at most `max_excess_mm`.
- `bone_height` (`implant`, `margin_mm`): the implant, less the crestal bone loss, stays `margin_mm` short of the
  nerve canal (mandible), or of the sinus floor or base of the ridge (maxilla).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from dentalsim.blocks.jaw_segment import TOOTH_WIDTHS_MM, parse_fdi
from dentalsim.blocks.registry import catalogue
from dentalsim.errors import DentalsimError
from dentalsim.model.scenario import BlockInstance, Scenario
from dentalsim.model.validity import Problem
from dentalsim.templates.model import FitRule, ProsthesisTemplate

Values = Mapping[str, Any]
RuleCheck = Callable[[Scenario, BlockInstance, Mapping[str, Any]], str | None]


def design_values(block: BlockInstance) -> dict[str, Any]:
    return catalogue().for_instance(block).design_values(block.settings)


def _block_values(scenario: Scenario, instance_id: str) -> dict[str, Any] | None:
    try:
        return design_values(scenario.block(instance_id))
    except KeyError:
        return None


def _region_teeth(scenario: Scenario, root: BlockInstance, params: Mapping[str, Any]) -> str | None:
    region = list(design_values(root)["region"])
    low, high = int(params["min"]), int(params["max"])
    if low <= len(region) <= high:
        return None
    span = f"{low}" if low == high else f"{low} to {high}"
    teeth = "tooth" if high == 1 else "teeth"
    return (
        f"it needs a jaw segment spanning {span} {teeth}, but the region spans {len(region)} "
        f"({', '.join(region)}); change the region of the jaw segment"
    )


def _site_positions(scenario: Scenario, root: BlockInstance, params: Mapping[str, Any]) -> str | None:
    tooth = parse_fdi(str(design_values(root)["site"]))
    positions = [int(p) for p in params["positions"]]
    if tooth is None or tooth.position in positions:
        return None
    return f"it is for {params['label']} site, and tooth {tooth.fdi} is not one; choose another site"


def _site_space(scenario: Scenario, root: BlockInstance, params: Mapping[str, Any]) -> str | None:
    values = _block_values(scenario, str(params["block"]))
    jaw = design_values(root)
    tooth = parse_fdi(str(jaw["site"]))
    if values is None or tooth is None or tooth.jaw != jaw["jaw"]:
        return None
    setting, excess = str(params["setting"]), float(params["max_excess_mm"])
    space = TOOTH_WIDTHS_MM[tooth.jaw][tooth.position - 1]
    width = float(values[setting])
    if width <= space + excess:
        return None
    return (
        f"block {params['block']!r} is {width:g} mm wide, but tooth {tooth.fdi} has about {space:g} mm of space; "
        f"make it at most {space + excess:g} mm wide or choose a wider site"
    )


def _bone_height(scenario: Scenario, root: BlockInstance, params: Mapping[str, Any]) -> str | None:
    implant = _block_values(scenario, str(params["implant"]))
    if implant is None:
        return None
    jaw = design_values(root)
    margin = float(params["margin_mm"])
    in_bone = float(implant["length_mm"]) - float(jaw["crestal_bone_loss_mm"])
    if jaw["jaw"] == "mandible":
        available, limit = float(jaw["nerve_canal_depth_mm"]), "the nerve canal"
    else:
        available = float(jaw["ridge_height_mm"])
        limit = "the sinus floor" if jaw["sinus"] == "present" else "the base of the ridge"
    if in_bone + margin <= available:
        return None
    return (
        f"its {float(implant['length_mm']):g} mm implant reaches {in_bone:g} mm into the bone, which leaves less "
        f"than {margin:g} mm to {limit} ({available:g} mm below the crest); choose an implant of at most "
        f"{available - margin + float(jaw['crestal_bone_loss_mm']):g} mm or a segment with more bone"
    )


RULES: dict[str, RuleCheck] = {
    "region_teeth": _region_teeth,
    "site_positions": _site_positions,
    "site_space": _site_space,
    "bone_height": _bone_height,
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "region_teeth": ("min", "max"),
    "site_positions": ("positions", "label"),
    "site_space": ("block", "setting", "max_excess_mm"),
    "bone_height": ("implant", "margin_mm"),
}


def check_rule_shape(rule: FitRule, template_id: str) -> None:
    if rule.rule not in RULES:
        raise DentalsimError(f"template {template_id!r} names an unknown fit rule {rule.rule!r}")
    missing = [p for p in REQUIRED[rule.rule] if p not in rule.params()]
    if missing:
        raise DentalsimError(f"template {template_id!r}: fit rule {rule.rule!r} needs {', '.join(missing)}")


def fit_problems(template: ProsthesisTemplate, scenario: Scenario) -> list[Problem]:
    """The `template_misfit` problems of a scenario made from `template`, each saying what must change."""
    roots = [b for b in scenario.blocks if b.attach_to is None]
    if len(roots) != 1:
        return []
    root = roots[0]
    problems = []
    for rule in template.fit_rules:
        check_rule_shape(rule, template.id)
        message = RULES[rule.rule](scenario, root, rule.params())
        if message is not None:
            problems.append(
                Problem(
                    code="template_misfit",
                    message=f"The template {template.name!r} does not fit: {message}.",
                    block=root.instance_id,
                )
            )
    return problems
