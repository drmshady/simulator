"""Generate the interface's colour-scale module from the conventions block (research R11, Principle VII).

`python -m dentalsim.conventions.codegen` (what `npm --prefix studio/ui run gen:conventions` runs) writes
studio/ui/src/viewer/colourScales.generated.ts, so the studio, the figures and the website show the same Frost
categories, continuous scale, units and teaching notice. `dentalsim conventions check` refuses when the file is
out of date.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from dentalsim.config import CONVENTIONS_PATH, get_settings
from dentalsim.conventions.loader import LoadedConventions, get_loaded_conventions
from dentalsim.errors import ConventionsMissing

GENERATED_TS_PATH = Path("studio") / "ui" / "src" / "viewer" / "colourScales.generated.ts"
REGENERATE_COMMAND = "npm --prefix studio/ui run gen:conventions"

UNIT_SYMBOLS = {"microstrain": "µε"}  # µε


def generated_typescript_path() -> Path:
    root = get_settings().repo_root
    if root is None:
        raise ConventionsMissing(f"cannot find {GENERATED_TS_PATH.as_posix()}: no repository root was found")
    return root / GENERATED_TS_PATH


def _ts_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )
    return f"'{escaped}'"


def _ts_number(value: float) -> str:
    return repr(int(value)) if float(value).is_integer() else repr(float(value))


def _lines(items: Iterable[str], indent: str) -> str:
    return "".join(f"{indent}{item},\n" for item in items)


def render_typescript(loaded: LoadedConventions) -> str:
    """The generated module's text. Pure and deterministic: the same conventions always give the same text."""
    c = loaded.conventions
    units = c.units
    frost = c.frost_scale
    categories = _lines(
        (
            f"{{ id: {_ts_string(cat.id)}, label: {_ts_string(cat.label)}, colour: {_ts_string(cat.colour)} }}"
            for cat in frost.categories
        ),
        "    ",
    )
    edges = _lines((_ts_string(edge) for edge in frost.edges), "    ")
    stops = _lines((f"[{_ts_number(pos)}, {_ts_string(colour)}]" for pos, colour in c.continuous_scale.stops), "    ")
    strain_symbol = UNIT_SYMBOLS.get(units.strain, units.strain)
    return (
        f"// Generated from {CONVENTIONS_PATH.as_posix()} version {c.version} (hash {loaded.content_hash}).\n"
        f"// Do not edit: change the conventions block, then run `{REGENERATE_COMMAND}`.\n"
        "\n"
        f"export const CONVENTIONS_VERSION: string = {_ts_string(c.version)};\n"
        f"export const CONVENTIONS_HASH: string = {_ts_string(loaded.content_hash)};\n"
        "\n"
        "export const UNITS: {\n"
        "  readonly length: string;\n"
        "  readonly force: string;\n"
        "  readonly stress: string;\n"
        "  readonly strain: string;\n"
        "  readonly strainSymbol: string;\n"
        "} = {\n"
        f"  length: {_ts_string(units.length)},\n"
        f"  force: {_ts_string(units.force)},\n"
        f"  stress: {_ts_string(units.stress)},\n"
        f"  strain: {_ts_string(units.strain)},\n"
        f"  strainSymbol: {_ts_string(strain_symbol)},\n"
        "} as const;\n"
        "\n"
        f"export const TEACHING_NOTICE: string = {_ts_string(c.teaching_notice)};\n"
        "\n"
        "export const FROST_SCALE: {\n"
        "  readonly label: string;\n"
        "  readonly measure: string;\n"
        "  readonly categories: readonly { readonly id: string; readonly label: string; readonly colour: string }[];\n"
        "  readonly edges: readonly string[];\n"
        "} = {\n"
        f"  label: {_ts_string(frost.label)},\n"
        f"  measure: {_ts_string(c.strain_measure.frost)},\n"
        "  categories: [\n"
        f"{categories}"
        "  ],\n"
        "  edges: [\n"
        f"{edges}"
        "  ],\n"
        "} as const;\n"
        "\n"
        "export const CONTINUOUS_SCALE: {\n"
        "  readonly name: string;\n"
        "  readonly stops: readonly (readonly [number, string])[];\n"
        "} = {\n"
        f"  name: {_ts_string(c.continuous_scale.name)},\n"
        "  stops: [\n"
        f"{stops}"
        "  ],\n"
        "} as const;\n"
    )


def typescript_is_current(loaded: LoadedConventions | None = None, path: Path | None = None) -> bool:
    """True when the generated file exists and matches the conventions (line endings aside)."""
    path = path or generated_typescript_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return text == render_typescript(loaded or get_loaded_conventions())


def write_typescript(path: Path | None = None, loaded: LoadedConventions | None = None) -> Path:
    """Write the generated module, unless it is already current, and return its path."""
    path = path or generated_typescript_path()
    loaded = loaded or get_loaded_conventions()
    if not typescript_is_current(loaded, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_typescript(loaded), encoding="utf-8", newline="\n")
    return path


def main() -> int:
    try:
        loaded = get_loaded_conventions()
        path = write_typescript(loaded=loaded)
    except ConventionsMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{path} is current with conventions {loaded.version} (hash {loaded.content_hash[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
