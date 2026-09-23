"""Copy canonical shared assets into distributable packages; --check detects drift."""

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for source in (root / "shared").glob("*.json"):
    for target in [
        root / "packages/typescript/src/data" / source.name,
        root / "packages/python/src/jev_moderation/data" / source.name,
    ]:
        if "--check" in sys.argv:
            if not target.exists() or target.read_bytes() != source.read_bytes():
                raise SystemExit(f"Asset drift: {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
