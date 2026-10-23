"""Synthetic save builders for the tests.

Everything here is generated at test time: the repository contains no real
save files and no game assets.  The layout mirrors the retail UE 4.27 flavour
of the P3R container (``size(u32) index(u32) pad(1) value`` for scalars).
"""

from __future__ import annotations

import struct

from p3r_transfer import codec, meta

DIFFICULTY_NORMAL_WITH_NOISE = 0x1F008000  # mask 0x3E000 -> 0x8000 (Normal)


def fstring(text: str) -> bytes:
    if not text:
        return struct.pack("<i", 0)
    raw = text.encode("ascii")
    return struct.pack("<i", len(raw) + 1) + raw + b"\x00"


def u32(value: int) -> bytes:
    return struct.pack("<I", value)


def header(
    version: int = 2, package: int = 522, custom: list[tuple[bytes, int]] | None = None
) -> bytes:
    out = b"GVAS" + struct.pack("<ii", version, package)
    if version >= 3:
        out += fstring("4.27.2") + struct.pack("<i", 0)
    else:
        out += struct.pack("<HHH", 4, 27, 2) + struct.pack("<i", 0)
    out += fstring("++UE4+Release-4.27")
    out += struct.pack("<i", 3)
    custom = custom or []
    out += struct.pack("<i", len(custom))
    for guid, number in custom:
        out += guid + struct.pack("<i", number)
    out += fstring("P3RSaveGame")
    return out


def uint32_prop(name: str, index: int, value: int) -> bytes:
    return (
        fstring(name)
        + fstring("UInt32Property")
        + u32(4)
        + u32(index)
        + b"\x00"
        + u32(value & 0xFFFFFFFF)
    )


def struct_prop(name: str, struct_type: str, payload: bytes) -> bytes:
    return (
        fstring(name)
        + fstring("StructProperty")
        + u32(len(payload))
        + b"\x00" * 4
        + fstring(struct_type)
        + b"\x00" * 16
        + b"\x00"
        + payload
    )


def array_prop(name: str, item_type: str, items: bytes, count: int) -> bytes:
    return (
        fstring(name)
        + fstring("ArrayProperty")
        + u32(len(items))
        + b"\x00" * 4
        + fstring(item_type)
        + b"\x00"
        + struct.pack("<i", count)
        + items
    )


def save(
    words: dict[int, int] | None = None,
    *,
    version: int = 2,
    extra: bytes = b"",
    terminator: bool = True,
    trailer: bytes = b"\x00\x00\x00\x00",
) -> bytes:
    body = header(version)
    for index, value in sorted((words or {}).items()):
        body += uint32_prop("SaveDataArea", index, value)
    body += extra
    if terminator:
        body += fstring("None") + fstring("None")
    return body + trailer


def word_index(name: str, version: int = 2) -> int:
    """Public index -> index for this save-game format version."""
    base = 4 if version >= 2 else 0
    return {
        "play_time": meta.PLAY_TIME_WORD + base,
        "level": meta.LEVEL_WORD + base,
        "difficulty": meta.DIFFICULTY_WORD + base,
    }[name]


def full_save(version: int = 2) -> bytes:
    return full_save_with_extra(b"", version=version)


def full_save_with_extra(extra: bytes, version: int = 2) -> bytes:
    words = {
        word_index("play_time", version): 3 * 3600 * meta.TICKS_PER_SECOND,
        word_index("level", version): 12,
        word_index("difficulty", version): DIFFICULTY_NORMAL_WITH_NOISE,
    }
    return save(words, version=version, extra=extra)


def fstring_utf16(text: str) -> bytes:
    raw = text.encode("utf-16-le") + b"\x00\x00"
    return struct.pack("<i", -(len(raw) // 2)) + raw


def sealed_save(words: dict[int, int] | None = None, **kwargs) -> bytes:
    return codec.encrypt(save(words, **kwargs))
