"""Best-effort reader for a few gameplay values stored inside a GVAS save.

Persona 3 Reload keeps the whole game state in one flat list of unnamed
``SaveDataArea`` records (a *sparse* ``UInt32Property`` array: every record
carries its own index).  The indices below come from the public tables used by
Luckyseer/p3r-save-editor and HonestDh/p3r-save-editor; they are **not**
documented by the game and differ by four between save-game format versions.

Nothing here is required for conversion.  If the container uses a property
type this parser does not know, or the values fail a sanity check, every field
stays ``None`` and the CLI prints ``n/a``.  Metadata is never guessed.

Property layout follows the retail-verified UE 4.27 flavour used by the game
(``size(u32) index(u32) pad(1) value`` for scalars, not the stock GVAS
``size(u8) pad(8) value``).
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

log = logging.getLogger(__name__)

TICKS_PER_SECOND = 30
PLAY_TIME_WORD = 12832
LEVEL_WORD = 13070 + 17  # protagonist HP word + level field offset
DIFFICULTY_WORD = 384
DIFFICULTY_MASK = 0x0003E000
DIFFICULTIES = {
    0x00002000: "Peaceful",
    0x00004000: "Easy",
    0x00008000: "Normal",
    0x00010000: "Hard",
    0x00020000: "Merciless",
}
_MAX_PLAYTIME_SECONDS = 1000 * 3600
_MAX_STRING = 1 << 20
_MAX_ITEMS = 1 << 20
_MAX_PROPERTIES = 1 << 18


class _ParseError(Exception):
    """Internal: the buffer is not laid out the way we expect."""


@dataclass(frozen=True)
class SaveMetadata:
    """Values we managed to read; ``None`` means "not found", never "zero"."""

    level: int | None = None
    play_time_seconds: int | None = None
    play_time_raw: int | None = None
    difficulty: str | None = None
    format_version: int | None = None
    parsed: bool = False


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def read(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise _ParseError(f"неожиданный конец данных на 0x{self.pos:X}")
        chunk = self.data[self.pos : self.pos + count]
        self.pos += count
        return chunk

    def skip(self, count: int) -> None:
        self.read(count)

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return self.unpack("<H")

    def u32(self) -> int:
        return self.unpack("<I")

    def i32(self) -> int:
        return self.unpack("<i")

    def fstring(self) -> str:
        length = self.i32()
        if length == 0:
            return ""
        if length > 0:
            if length > _MAX_STRING:
                raise _ParseError("слишком длинная строка")
            return self.read(length)[:-1].decode("utf-8", "replace")
        if length < -_MAX_STRING:
            raise _ParseError("слишком длинная строка")
        return self.read(-length * 2).decode("utf-16-le", "replace").rstrip("\x00")


def _read_header(reader: _Reader) -> int:
    if reader.read(4) != b"GVAS":
        raise _ParseError("нет магии GVAS")
    save_game_version = reader.i32()
    reader.i32()  # package version
    if save_game_version >= 3:
        reader.fstring()  # engine version
        reader.i32()  # engine build
    else:
        reader.skip(6)  # major/minor/patch engine version
        reader.i32()  # engine build
    reader.fstring()  # engine branch
    custom_format = reader.i32()
    if not 0 <= custom_format <= 1024:
        raise _ParseError("подозрительный custom version format")
    count = reader.i32()
    if not 0 <= count <= 1024:
        raise _ParseError("подозрительное число custom versions")
    reader.skip(20 * count)  # guid(16) + version(i32)
    reader.fstring()  # save game class name
    return save_game_version


def _read_scalar(reader: _Reader, fmt: str) -> None:
    """``size(u32) index(u32) pad(1) value``."""
    reader.u32()
    reader.u32()
    reader.u8()
    reader.read(struct.calcsize(fmt))


def _read_property(reader: _Reader, words: dict[int, int]) -> bool:
    """Parse one top-level property; ``False`` means the terminator was hit."""
    name = reader.fstring()
    if name == "None":
        reader.fstring()
        return False
    ptype = reader.fstring()

    if ptype == "UInt32Property":
        reader.u32()  # size, always 4
        index = reader.u32()
        reader.u8()
        value = reader.u32()
        if name == "SaveDataArea":
            words[index] = value
    elif ptype in _SCALAR_FORMATS:
        _read_scalar(reader, _SCALAR_FORMATS[ptype])
    elif ptype == "BoolProperty":
        reader.skip(8)
        reader.u8()
        reader.u8()
    elif ptype in {"StrProperty", "NameProperty"}:
        reader.u8()  # flag
        reader.skip(8)
        reader.fstring()
    elif ptype == "EnumProperty":
        reader.u32()
        reader.skip(4)
        reader.fstring()  # enum type
        reader.u8()
        reader.fstring()  # value
    elif ptype in {"StructProperty", "ArrayProperty"}:
        # Both carry a byte length we can trust and no state we need, so jump
        # over the payload instead of recursing into unknown nested types.
        size = reader.u32()
        if size > len(reader.data):
            raise _ParseError("подозрительный размер вложенного свойства")
        reader.skip(4)
        reader.fstring()  # struct / item type
        if ptype == "StructProperty":
            reader.skip(16)  # struct guid
            reader.u8()
            reader.skip(size)
        else:
            reader.u8()
            count = reader.i32()
            if not 0 <= count <= _MAX_ITEMS:
                raise _ParseError("подозрительное число элементов массива")
            reader.skip(size)
    else:
        raise _ParseError(f"неизвестный тип свойства: {ptype}")
    return True


_SCALAR_FORMATS = {
    "IntProperty": "<i",
    "Int8Property": "<b",
    "Int64Property": "<q",
    "UInt16Property": "<H",
    "FloatProperty": "<f",
}


def _read_properties(reader: _Reader, words: dict[int, int]) -> None:
    for _ in range(_MAX_PROPERTIES):
        if not _read_property(reader, words):
            return
    raise _ParseError("слишком много свойств")


def _difficulty_from(words: dict[int, int], base: int) -> str | None:
    preferred = DIFFICULTY_WORD + base
    for index in [preferred, *sorted(words)]:
        word = words.get(index)
        if word is None:
            continue
        flag = word & DIFFICULTY_MASK
        name = DIFFICULTIES.get(flag)
        if name is not None:
            return name
    return None


def header_version(gvas: bytes) -> int | None:
    """Save-game format version from the GVAS header, or ``None`` if unreadable.

    Deliberately shallow: it only depends on the engine header, which is the
    same in every UE 4.27 title, so a failure here really does mean a broken
    container.
    """
    try:
        reader = _Reader(gvas)
        return _read_header(reader)
    except (_ParseError, struct.error, UnicodeDecodeError) as exc:
        log.debug("Заголовок GVAS не разобран: %s", exc)
        return None


def read_metadata(gvas: bytes) -> SaveMetadata:
    """Read what we can from a **plain** GVAS buffer.  Never raises."""
    words: dict[int, int] = {}
    try:
        reader = _Reader(gvas)
        version = _read_header(reader)
        _read_properties(reader, words)
    except (_ParseError, struct.error, UnicodeDecodeError) as exc:
        log.debug("Метаданные недоступны: %s", exc)
        return SaveMetadata()

    base = 4 if version >= 2 else 0
    raw_play = words.get(PLAY_TIME_WORD + base)
    seconds = None
    if raw_play is not None:
        candidate = raw_play // TICKS_PER_SECOND
        if 0 <= candidate <= _MAX_PLAYTIME_SECONDS:
            seconds = candidate

    level = words.get(LEVEL_WORD + base)
    if level is not None:
        level &= 0xFFFF
        if not 1 <= level <= 99:
            level = None

    return SaveMetadata(
        level=level,
        play_time_seconds=seconds,
        play_time_raw=raw_play,
        difficulty=_difficulty_from(words, base),
        format_version=version,
        parsed=True,
    )
