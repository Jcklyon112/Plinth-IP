"""Shared pytest fixtures and sys.path setup.

The backend modules (`app.*`) and the repo-root grid module
(`data.grid.*`) need to be importable from anywhere the suite runs.
Adding both here means tests work whether you invoke pytest from the
backend dir, the repo root, or via an IDE.
"""
from __future__ import annotations

import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_BACKEND = _THIS.parents[1]            # plinth-sip/backend/
_PROJECT_ROOT = _BACKEND.parent        # plinth-sip/

for p in (_BACKEND, _PROJECT_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
