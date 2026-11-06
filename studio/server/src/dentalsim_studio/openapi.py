"""Write the studio's OpenAPI document: `python -m dentalsim_studio.openapi <out.json>`.

The interface's `npm run gen:api` turns it into `studio/ui/src/api/schema.d.ts` (task T052). The app is built over
a temporary data folder, so this needs the conventions but touches neither the real data folder nor the database.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dentalsim_studio.app import create_app


def openapi_document() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dentalsim-openapi-") as folder:
        return create_app(data_dir=Path(folder)).openapi()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m dentalsim_studio.openapi <out.json>", file=sys.stderr)
        return 2
    out = Path(args[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(openapi_document(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
