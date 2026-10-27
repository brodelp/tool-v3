"""The frontend's generated label list matches config.LABELS (stage 3 F12)."""

import importlib.util
from types import ModuleType

from tests.helpers import ROOT


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_frontend_labels", ROOT / "scripts" / "generate_frontend_labels.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_labels_are_current() -> None:
    generator = _generator()
    assert generator.TARGET.read_text(encoding="utf-8") == generator.render(), (
        "frontend/src/generated/labels.ts is stale; run `make labels`."
    )
