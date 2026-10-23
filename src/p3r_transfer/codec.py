"""Byte-level obfuscation used by Persona 3 Reload save files.

The Steam build stores the Unreal ``GVAS`` container wrapped in a fixed,
key-less obfuscation.  Every byte is transformed independently:

    swap(x)  = ((x & 0x03) << 4) | ((x >> 4) & 0x03) | (x & 0xCC)
    encrypt: swap(x) ^ key[i % 31]
    decrypt: swap(x ^ key[i % 31])

The swap exchanges bit pairs 0-1 and 4-5 and leaves bits 2, 3, 6 and 7 alone,
so it is an involution.  The two directions therefore differ only in whether
the XOR happens before or after the swap (XOR last when sealing, first when
opening).  File size is unchanged.

Bit-level behaviour matches illusionyy/P3R-Save-EnDecryptor byte for byte
(see ``tests/test_codec.py::test_matches_reference_c_expressions``).
"""

from __future__ import annotations

from enum import Enum
from typing import Final

KEY: Final[bytes] = b"ae5zeitaix1joowooNgie3fahP5Ohph"
KEY_LEN: Final[int] = len(KEY)  # 31

GVAS_MAGIC: Final[bytes] = b"GVAS"
GVAS_MAGIC_LE: Final[int] = 0x53415647  # "GVAS" read as a little-endian uint32
OBFUSCATED_MAGIC_LE: Final[int] = 0x0B650015  # sealed "GVAS"

#: Smallest buffer we are willing to call a save.  The GVAS header alone is
#: ~60 bytes; anything shorter cannot be a usable save.
MIN_SAVE_SIZE: Final[int] = 32


class SaveKind(str, Enum):
    """What a buffer looks like on disk."""

    GVAS = "gvas"  # plain Unreal container (Microsoft Store / Xbox)
    OBFUSCATED = "obfuscated"  # sealed container (Steam)
    UNKNOWN = "unknown"


def swap_bits(byte: int) -> int:
    """Exchange bit pairs 0-1 and 4-5, keep bits 2, 3, 6, 7."""
    return ((byte & 0x03) << 4) | ((byte >> 4) & 0x03) | (byte & 0xCC)


def _tables(sealing: bool) -> tuple[bytes, ...]:
    """Per-key-phase translation tables (one per key byte)."""
    tables = []
    for key_byte in KEY:
        if sealing:
            tables.append(bytes(swap_bits(x) ^ key_byte for x in range(256)))
        else:
            tables.append(bytes(swap_bits(x ^ key_byte) for x in range(256)))
    return tuple(tables)


_SEAL_TABLES: Final[tuple[bytes, ...]] = _tables(True)
_OPEN_TABLES: Final[tuple[bytes, ...]] = _tables(False)


def _apply(data: bytes, tables: tuple[bytes, ...]) -> bytes:
    """Apply one table per key phase.

    Because the key repeats every 31 bytes, byte *i* always uses
    ``KEY[i % 31]``; slicing with a 31-byte stride and translating each slice
    is therefore equivalent to the naive byte loop, only much faster.
    """
    if not data:
        return b""
    out = bytearray(len(data))
    for offset, table in enumerate(tables):
        chunk = data[offset::KEY_LEN]
        if chunk:
            out[offset::KEY_LEN] = chunk.translate(table)
    return bytes(out)


def encrypt(gvas: bytes) -> bytes:
    """Seal a plain GVAS buffer into the on-disk Steam representation."""
    return _apply(gvas, _SEAL_TABLES)


def decrypt(sealed: bytes) -> bytes:
    """Open an on-disk Steam save into a plain GVAS buffer."""
    return _apply(sealed, _OPEN_TABLES)


def magic_of(data: bytes) -> int | None:
    """Return the first four bytes as a little-endian uint32, or None."""
    if len(data) < 4:
        return None
    return int.from_bytes(data[:4], "little")


def detect_kind(data: bytes) -> SaveKind:
    """Classify a buffer by its magic only (size is checked separately)."""
    if len(data) < 4:
        return SaveKind.UNKNOWN
    magic = int.from_bytes(data[:4], "little")
    if magic == GVAS_MAGIC_LE:
        return SaveKind.GVAS
    if magic == OBFUSCATED_MAGIC_LE:
        return SaveKind.OBFUSCATED
    return SaveKind.UNKNOWN


def format_magic(data: bytes) -> str:
    """Human-readable magic for error messages: ``0x0B650015``."""
    magic = magic_of(data)
    if magic is None:
        return "<файл короче 4 байт>"
    return f"0x{magic:08X}"
