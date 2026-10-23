"""User-facing error types.

Every exception here carries a message that is already fit for the console:
no tracebacks, no errno numbers without an explanation.
"""

from __future__ import annotations


class TransferError(Exception):
    """Base class: a problem the user can act on."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return f"{self.message} ({self.hint})" if self.hint else self.message


class UsageError(TransferError):
    """Bad command line."""

    exit_code = 2


class SaveNotFoundError(TransferError):
    """Input file does not exist or is unreadable."""


class UnknownMagicError(TransferError):
    """Neither GVAS nor the sealed Steam magic."""


class LockedFileError(TransferError):
    """File is held open by the game / Xbox app, or access is denied."""


class SlotBusyError(TransferError):
    """Requested slot already has a save and --force was not given."""


class SteamDirNotFoundError(TransferError):
    """No ``%APPDATA%\\Sega\\P3R\\Steam`` (or no SteamID folder inside)."""
