"""Transfer Persona 3 Reload saves between the Microsoft Store and Steam builds.

Only the wrapper is touched: the GVAS container is unwrapped from (or wrapped
into) the fixed Steam obfuscation.  Save contents are never modified.
"""

from __future__ import annotations

from .version import __version__

__all__ = ["__version__"]
