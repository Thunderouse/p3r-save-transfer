"""Desktop shell for p3r-save-transfer.

The GUI is a thin layer: it calls :mod:`p3r_transfer` directly and never
re-implements codec logic.  See ``ui/bridge.py`` for the only entry points the
window uses.
"""

from __future__ import annotations
