"""Checking a scenario's library values against the library (FR-014, FR-052), shared by the studio's scenario routes
and studies (task T123).

`checked_scenario` is what every saved or derived scenario goes through: each library value must point to an approved
version, its `outside_range` is recomputed, and a block whose design values now name another entry (a different
density class or material, say) takes the typical value of that entry.
"""

from __future__ import annotations

from typing import Any

from dentalsim.assembly.placement import place
from dentalsim.blocks.registry import catalogue
from dentalsim.library.service import LibraryService
from dentalsim.model.scenario import BlockInstance, LoadCase, Scenario
from dentalsim.model.settings import LibraryValue, SettingValue


def checked_scenario(scenario: Scenario, library: LibraryService) -> Scenario:
    """The scenario with each library value checked against an approved version (422 otherwise) and its
    `outside_range` recomputed; its blocks must be readable (unknown types, wrong setting kinds: 422). A block's
    missing library values, and those whose parameter key its design values have changed, are filled with the
    typical value of the current approved version."""

    def checked(value: LibraryValue) -> LibraryValue:
        return value.model_copy(update={"outside_range": library.resolve_value(value).outside_range})

    found = catalogue()
    blocks: list[BlockInstance] = []
    for block in scenario.blocks:
        block_type = found.for_instance(block)
        keys = block_type.parameter_keys(block_type.design_values(block.settings))
        settings: dict[str, SettingValue] = {}
        for setting_id, value in block.settings.items():
            settings[setting_id] = checked(value) if isinstance(value, LibraryValue) else value
        for setting_id, key in keys.items():
            # A library setting that is missing, or whose entry no longer matches the design values (another
            # material, say), takes the typical value of the entry the design values now name.
            current = settings.get(setting_id)
            entry = library.get_entry(current.entry_id) if isinstance(current, LibraryValue) else None
            if entry is None or entry.parameter_key != key:
                settings[setting_id] = library.resolve(key).library_value()
        blocks.append(block.model_copy(update={"settings": settings}))
    loads: list[LoadCase] = []
    for load in scenario.loads:
        update: dict[str, Any] = {"force": checked(load.force)}
        if load.cycles is not None:
            update["cycles"] = checked(load.cycles)
        loads.append(load.model_copy(update=update))
    result = scenario.model_copy(update={"blocks": blocks, "loads": loads})
    place(result)
    return result
