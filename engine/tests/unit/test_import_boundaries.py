"""Generic code never depends on a specific block (SC-012, contracts/scenario-format.md "Block type contract").

- No module of engine/src/dentalsim outside blocks/ and templates/ imports a block module or names a block
  `type_id` in a string literal.
- No module of studio/server/src imports a block module.

Block modules are the modules of dentalsim/blocks/ other than the generic framework (`__init__`, `base` and
`registry`, task T060). The type ids are read from the block modules with `ast`: a module- or class-level
`type_id = "..."` (annotated or not), or a `type_id="..."` keyword in a call.
"""

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENGINE_SRC = REPO_ROOT / "engine" / "src"
ENGINE_PACKAGE = ENGINE_SRC / "dentalsim"
BLOCKS = ENGINE_PACKAGE / "blocks"
TEMPLATES = ENGINE_PACKAGE / "templates"
SERVER_SRC = REPO_ROOT / "studio" / "server" / "src"

BLOCKS_PACKAGE = "dentalsim.blocks"
FRAMEWORK_MODULES = frozenset({"__init__", "base", "registry"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


# --- what the block modules are ----------------------------------------------------------------------------------


def block_module_names(blocks: Path = BLOCKS) -> set[str]:
    """Names of the block modules: plain modules or packages directly under blocks/, except the framework."""
    names: set[str] = set()
    for path in blocks.iterdir():
        if path.is_file() and path.suffix == ".py" and path.stem not in FRAMEWORK_MODULES:
            names.add(path.stem)
        elif path.is_dir() and (path / "__init__.py").is_file() and path.name not in FRAMEWORK_MODULES:
            names.add(path.name)
    return names


def block_module_files(blocks: Path = BLOCKS) -> list[Path]:
    files: list[Path] = []
    for name in sorted(block_module_names(blocks)):
        module = blocks / f"{name}.py"
        files.extend([module] if module.is_file() else _python_files(blocks / name))
    return files


def _string(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def type_ids_in(source: str) -> set[str]:
    """The `type_id` string values declared in one block module's source."""
    tree = ast.parse(source)
    ids: set[str] = set()

    def declares_type_id(node: ast.stmt) -> bool:
        if isinstance(node, ast.Assign):
            return any(isinstance(t, ast.Name) and t.id == "type_id" for t in node.targets)
        return isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "type_id"

    def scan(body: Iterable[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                scan(node.body)
            elif declares_type_id(node) and (value := _string(getattr(node, "value", None))) is not None:
                ids.add(value)

    scan(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            ids.update(v for kw in node.keywords if kw.arg == "type_id" and (v := _string(kw.value)) is not None)
    return ids


def block_type_ids(blocks: Path = BLOCKS) -> set[str]:
    return {tid for path in block_module_files(blocks) for tid in type_ids_in(path.read_text(encoding="utf-8"))}


# --- the scanner -------------------------------------------------------------------------------------------------


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def engine_sources() -> list[Path]:
    return [p for p in _python_files(ENGINE_PACKAGE) if not (p.is_relative_to(BLOCKS) or p.is_relative_to(TEMPLATES))]


def server_sources() -> list[Path]:
    return _python_files(SERVER_SRC)


def module_of(path: Path, src_root: Path) -> tuple[str, bool]:
    """The dotted module name of a source file, and whether it is a package's `__init__`."""
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        return ".".join(parts[:-1]), True
    return ".".join(parts), False


def _resolve(module: str, is_package: bool, level: int, target: str | None) -> str:
    """The absolute module named by `from <level dots><target> import ...` inside `module`."""
    if level == 0:
        return target or ""
    package = module.split(".") if is_package else module.split(".")[:-1]
    base = package[: len(package) - (level - 1)]
    return ".".join([*base, *([target] if target else [])])


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (prefix := _dotted(node.value)) is not None:
        return f"{prefix}.{node.attr}"
    return None


def find_violations(
    source: str,
    module: str,
    *,
    is_package: bool = False,
    block_modules: set[str],
    type_ids: set[str],
    path: str = "<source>",
) -> list[Violation]:
    """Imports of block modules (in any form) and string literals equal to a block type id, in one source file."""
    tree = ast.parse(source, filename=path)
    blocked = [f"{BLOCKS_PACKAGE}.{name}" for name in sorted(block_modules)]

    def block_of(dotted: str) -> str | None:
        return next((b for b in blocked if dotted == b or dotted.startswith(b + ".")), None)

    found: list[Violation] = []

    def report(node: ast.AST, message: str) -> None:
        found.append(Violation(path, getattr(node, "lineno", 0), message))

    # Local names bound by imports, so that `from dentalsim import blocks` then `blocks.implant` is caught too.
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update({alias.asname: alias.name for alias in node.names if alias.asname})
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, is_package, node.level, node.module)
            aliases.update({alias.asname or alias.name: f"{base}.{alias.name}".lstrip(".") for alias in node.names})

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if hit := block_of(alias.name):
                    report(node, f"imports block module {hit}")
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, is_package, node.level, node.module)
            if hit := block_of(base):
                report(node, f"imports from block module {hit}")
            elif base == BLOCKS_PACKAGE:
                for alias in node.names:
                    if alias.name in block_modules:
                        report(node, f"imports block module {BLOCKS_PACKAGE}.{alias.name}")
        elif isinstance(node, ast.Call):
            # importlib.import_module(".implant", "dentalsim.blocks") and the package= keyword form
            name = _string(node.args[0]) if node.args else None
            package = _string(node.args[1]) if len(node.args) > 1 else None
            package = next((_string(kw.value) for kw in node.keywords if kw.arg == "package"), package)
            if name and name.startswith(".") and package:
                level = len(name) - len(name.lstrip("."))
                resolved = _resolve(package, True, level, name.lstrip(".") or None)
                if hit := block_of(resolved):
                    report(node, f"imports block module {hit} by name")
        elif isinstance(node, ast.Attribute) and (dotted := _dotted(node)) is not None:
            head, _, rest = dotted.partition(".")
            full = aliases.get(head, head) + (f".{rest}" if rest else "")
            if (hit := block_of(full)) and full == hit:
                report(node, f"uses block module {hit} as an attribute")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if hit := block_of(node.value):
                report(node, f"names block module {hit} in a string")
            elif node.value in type_ids:
                report(node, f"names block type id {node.value!r}")
    return found


def scan(files: Iterable[Path], src_root: Path, *, block_modules: set[str], type_ids: set[str]) -> list[Violation]:
    violations: list[Violation] = []
    for path in files:
        module, is_package = module_of(path, src_root)
        violations += find_violations(
            path.read_text(encoding="utf-8"),
            module,
            is_package=is_package,
            block_modules=block_modules,
            type_ids=type_ids,
            path=path.relative_to(REPO_ROOT).as_posix(),
        )
    return violations


# --- the rules ---------------------------------------------------------------------------------------------------


def test_the_scan_covers_the_expected_source_folders() -> None:
    engine = {p.relative_to(ENGINE_PACKAGE).as_posix() for p in engine_sources()}
    server = {p.relative_to(SERVER_SRC).as_posix() for p in server_sources()}
    for expected in ("cli.py", "conventions/loader.py", "conventions/regions.py", "commands/conventions.py"):
        assert expected in engine
    for folder in ("model/", "post/", "exports/", "publish/", "assembly/"):
        assert any(name.startswith(folder) for name in engine), folder
    assert not any(name.startswith(("blocks/", "templates/")) for name in engine)
    assert "dentalsim_studio/__init__.py" in server
    assert any(name.startswith("dentalsim_studio/db/") for name in server)


def test_type_ids_are_found_in_every_block_module() -> None:
    modules = block_module_names()
    if modules:
        assert block_type_ids(), (
            "block modules exist but no type_id was found in them: teach type_ids_in() how the block framework "
            "(task T060) declares a type id"
        )


def test_generic_engine_code_imports_no_block_module_and_names_no_block_type() -> None:
    violations = scan(engine_sources(), ENGINE_SRC, block_modules=block_module_names(), type_ids=block_type_ids())
    assert not violations, "generic engine code depends on a specific block:\n" + "\n".join(map(str, violations))


def test_studio_server_imports_no_block_module() -> None:
    violations = scan(server_sources(), SERVER_SRC, block_modules=block_module_names(), type_ids=set())
    assert not violations, "the studio server depends on a specific block:\n" + "\n".join(map(str, violations))


# --- the scanner itself ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import dentalsim.blocks.implant",
        "import dentalsim.blocks.implant as imp",
        "import dentalsim.blocks.implant.threads",
        "from dentalsim.blocks import implant",
        "from dentalsim.blocks import registry, implant as imp",
        "from dentalsim.blocks.implant import ImplantBlock",
        "from ..blocks import implant",
        "from ..blocks.implant import ImplantBlock",
        "from .. import blocks\nblocks.implant.build()",
        "from dentalsim import blocks as b\nb.implant.build()",
        "import importlib\nimportlib.import_module('dentalsim.blocks.implant')",
        "import importlib\nimportlib.import_module('.implant', 'dentalsim.blocks')",
        "import importlib\nimportlib.import_module('.implant', package='dentalsim.blocks')",
        "import dentalsim.blocks\ndentalsim.blocks.implant",
        "KIND = 'implant_body'",
        "def pick(kind: str) -> bool:\n    return kind == 'implant_body'",
        "from typing import Literal\nKind = Literal['implant_body', 'other']",
    ],
)
def test_the_scanner_catches_a_violation(source: str) -> None:
    violations = find_violations(source, "dentalsim.post.report", block_modules={"implant"}, type_ids={"implant_body"})
    assert violations, source


@pytest.mark.parametrize(
    "source",
    [
        "from dentalsim.blocks import registry",
        "from dentalsim.blocks.base import BlockType",
        "import dentalsim.blocks.registry\ndentalsim.blocks.registry.all_block_types()",
        "from ..blocks import base",
        "from dentalsim import blocks\nblocks.registry.all_block_types()",
        "TEXT = 'an implant_body is generic prose here'",
        "import dentalsim.implantology",
        "NAME = 'dentalsim.blocks'",
    ],
)
def test_the_scanner_allows_the_framework_and_unrelated_strings(source: str) -> None:
    assert find_violations(source, "dentalsim.post.report", block_modules={"implant"}, type_ids={"implant_body"}) == []


def test_relative_imports_resolve_against_the_package() -> None:
    assert _resolve("dentalsim.post.report", False, 2, "blocks") == "dentalsim.blocks"
    assert _resolve("dentalsim.post", True, 2, "blocks") == "dentalsim.blocks"
    assert _resolve("dentalsim.post", True, 1, "stats") == "dentalsim.post.stats"
    assert _resolve("dentalsim.post.report", False, 1, None) == "dentalsim.post"
    source = "from . import implant"
    assert find_violations(source, "dentalsim.blocks", is_package=True, block_modules={"implant"}, type_ids=set())
    assert not find_violations(source, "dentalsim.post", is_package=True, block_modules={"implant"}, type_ids=set())


def test_type_ids_are_read_from_each_declaration_style() -> None:
    source = (
        "type_id = 'jaw_segment'\n"
        "class Implant:\n"
        "    type_id: str = 'implant'\n"
        "    class Inner:\n"
        "        type_id = 'inner'\n"
        "def make():\n"
        "    type_id = 'not_declared'\n"
        "    return BlockType(type_id='crown', version='1.0.0')\n"
    )
    assert type_ids_in(source) == {"jaw_segment", "implant", "inner", "crown"}


def test_block_modules_exclude_the_framework(tmp_path: Path) -> None:
    for name in ("__init__.py", "base.py", "registry.py", "implant.py", "crown.py"):
        (tmp_path / name).write_text("type_id = 'x'\n" if name == "crown.py" else "")
    (tmp_path / "jaw_segment").mkdir()
    (tmp_path / "jaw_segment" / "__init__.py").write_text("BLOCK = make(type_id='jaw_segment')\n")
    (tmp_path / "notes").mkdir()
    assert block_module_names(tmp_path) == {"implant", "crown", "jaw_segment"}
    assert block_type_ids(tmp_path) == {"x", "jaw_segment"}
