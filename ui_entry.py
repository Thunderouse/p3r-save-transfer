"""Single PyInstaller entry point for both executables.

The windowed build and the console build share one ``_internal`` folder, so
they also share one entry script; the dispatch is by executable name, which
PyInstaller sets in ``sys.argv[0]``:

    P3R-Save-Transfer.exe      -> window, no console
    p3r-save-transfer-cli.exe  -> console CLI
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    executable = Path(sys.argv[0]).stem.lower()
    if "cli" in executable:
        from p3r_transfer.cli import run

        run()
        return
    from ui.app import run

    run()


if __name__ == "__main__":
    main()
