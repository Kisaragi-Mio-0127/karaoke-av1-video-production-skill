"""Test the bundled integration directly without installing it first."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "integration" / "strangeutagame"
bundle_path = str(BUNDLE)
if bundle_path in sys.path:
    sys.path.remove(bundle_path)
sys.path.insert(0, bundle_path)
