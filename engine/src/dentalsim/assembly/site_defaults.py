"""Site-sized defaults for newly added blocks.

A design setting marked `Design(site_width_offset_mm=...)` (a crown's or a pontic's mesiodistal width) starts, in a
block newly added to a scenario, at the mean width of the tooth of the jaw site it stands on plus that offset,
rounded to 0.1 mm and kept within the setting's limits. The site is the nearest socket up the block's attachment
chain whose properties give `tooth_width_mm`. The fixed default (a molar's width) would not fit a premolar site.

Only blocks new to the scenario, and only settings they leave unset, are filled: a saved block keeps what it has,
so filling never changes an existing scenario's results.
"""

from __future__ import annotations

from collections.abc import Collection

from dentalsim.assembly.placement import Assembly, place
from dentalsim.errors import NotFound, Refused
from dentalsim.model.scenario import Scenario
from dentalsim.model.settings import DesignValue

TOOTH_WIDTH = "tooth_width_mm"


def site_tooth_width(assembly: Assembly, instance_id: str) -> float | None:
    """The mean tooth width of the jaw site `instance_id` stands on, directly or through the blocks below it."""
    current: str | None = instance_id
    while current is not None:
        placed = assembly.block(current)
        if placed.context is None or placed.context.parent is None:
            return None
        width = placed.context.parent.socket.properties.get(TOOTH_WIDTH)
        if isinstance(width, int | float):
            return float(width)
        current = placed.parent_id
    return None


def with_site_defaults(scenario: Scenario, new_blocks: Collection[str]) -> Scenario:
    """`scenario` with the unset site-sized settings of the blocks in `new_blocks` filled from their sites."""
    if not new_blocks:
        return scenario
    try:
        assembly = place(scenario)
    except (Refused, NotFound):
        return scenario
    blocks = []
    for block in scenario.blocks:
        if block.instance_id in new_blocks:
            placed = assembly.block(block.instance_id)
            specs = [
                s
                for s in placed.block_type.setting_specs()
                if s.site_width_offset_mm is not None and s.id not in block.settings
            ]
            width = site_tooth_width(assembly, block.instance_id) if specs else None
            if width is not None:
                filled = dict(block.settings)
                for spec in specs:
                    value = round(width + (spec.site_width_offset_mm or 0.0), 1)
                    if spec.minimum is not None:
                        value = max(value, spec.minimum)
                    if spec.maximum is not None:
                        value = min(value, spec.maximum)
                    filled[spec.id] = DesignValue(value=value)
                block = block.model_copy(update={"settings": filled})
        blocks.append(block)
    return scenario.model_copy(update={"blocks": blocks})
