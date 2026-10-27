"""Write frontend/src/generated/labels.ts from config.LABELS (stage 3 spec §4.3, F12).

Usage: python scripts/generate_frontend_labels.py   (or `make labels`)

config.py stays the single source of the label set; tests/test_frontend_labels.py fails when the
generated file is out of date.
"""

import json
from pathlib import Path

from scat_validator.config import LABELS

TARGET = Path(__file__).resolve().parents[1] / "frontend" / "src" / "generated" / "labels.ts"


def render() -> str:
    entries = "".join(f"  {json.dumps(label)},\n" for label in LABELS)
    return (
        "// Generated from src/scat_validator/config.py by scripts/generate_frontend_labels.py.\n"
        "// Do not edit; run `make labels` after changing LABELS.\n\n"
        "/** Configured label keys in canonical order. */\n"
        f"export const LABELS = [\n{entries}] as const;\n\n"
        "export type Label = (typeof LABELS)[number];\n"
    )


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
