"""Metadata parsing: real fields when present, n/a (never a crash) when not."""

from __future__ import annotations

import struct

import synth
from p3r_transfer import meta


def test_reads_level_playtime_and_difficulty():
    result = meta.read_metadata(synth.full_save())
    assert result.parsed is True
    assert result.format_version == 2
    assert result.level == 12
    assert result.play_time_seconds == 3 * 3600
    assert result.play_time_raw == 3 * 3600 * meta.TICKS_PER_SECOND
    assert result.difficulty == "Normal"


def test_version_one_uses_other_base():
    result = meta.read_metadata(synth.full_save(version=1))
    assert result.format_version == 1
    assert result.level == 12
    assert result.play_time_seconds == 3 * 3600
    assert result.difficulty == "Normal"


def test_missing_words_are_none_but_parse_succeeded():
    result = meta.read_metadata(synth.save({1: 2, 3: 4}))
    assert result.parsed is True
    assert result.level is None
    assert result.play_time_seconds is None
    assert result.difficulty is None


def test_absurd_playtime_is_rejected():
    words = {synth.word_index("play_time"): 0xFFFFFFFF}
    result = meta.read_metadata(synth.save(words))
    assert result.play_time_seconds is None
    assert result.play_time_raw == 0xFFFFFFFF


def test_level_out_of_range_is_rejected():
    for value in (0, 100, 0xFFFF):
        result = meta.read_metadata(synth.save({synth.word_index("level"): value}))
        assert result.level is None


def test_level_uses_low_16_bits():
    result = meta.read_metadata(synth.save({synth.word_index("level"): 0xABCD0007}))
    assert result.level == 7


def test_difficulty_found_anywhere_in_the_area():
    """Version-2 saves may omit word 384; the flag is then found by scanning."""
    words = {9000: 0x00020000}  # Merciless flag somewhere else
    result = meta.read_metadata(synth.save(words))
    assert result.difficulty == "Merciless"


def test_unknown_difficulty_flags_give_none():
    result = meta.read_metadata(synth.save({synth.word_index("difficulty"): 0x00000100}))
    assert result.difficulty is None


def test_struct_and_array_properties_are_skipped():
    extra = synth.struct_prop("SaveDataHeadder", "SaveDataHeadder", b"\x00" * 12 + b"junkjunk")
    extra += synth.array_prop("SomeArray", "IntProperty", struct.pack("<iii", 1, 2, 3), 3)
    result = meta.read_metadata(synth.full_save_with_extra(extra))
    assert result.level == 12
    assert result.difficulty == "Normal"


def test_truncated_file_gives_empty_metadata():
    data = synth.full_save()[:40]
    result = meta.read_metadata(data)
    assert result.parsed is False
    assert result.level is None and result.play_time_seconds is None


def test_wrong_magic_gives_empty_metadata():
    result = meta.read_metadata(b"XXXX" + b"\x00" * 100)
    assert result.parsed is False


def test_unknown_property_type_stops_parsing_without_raising():
    extra = synth.fstring("WeirdProperty") + synth.fstring("WeirdProperty") + b"\x00" * 8
    result = meta.read_metadata(synth.full_save_with_extra(extra))
    assert result.parsed is False


def test_header_version_of_synthetic_save():
    assert meta.header_version(synth.full_save()) == 2
    assert meta.header_version(synth.save(version=3)) == 3
    assert meta.header_version(b"nope") is None


def test_utf16_property_names_are_decoded():
    index = synth.word_index("level")
    record = (
        synth.fstring_utf16("SaveDataArea")
        + synth.fstring("UInt32Property")
        + synth.u32(4)
        + synth.u32(index)
        + b"\x00"
        + synth.u32(33)
    )
    data = synth.header() + record + synth.fstring("None") + synth.fstring("None")
    data += b"\x00" * 4
    assert meta.read_metadata(data).level == 33


def test_custom_versions_in_header_are_skipped():
    header = synth.header(version=3, custom=[(b"\x01" * 16, 5), (b"\x02" * 16, 9)])
    data = header + synth.uint32_prop("SaveDataArea", synth.word_index("level"), 33)
    data += synth.fstring("None") + synth.fstring("None") + b"\x00" * 4
    result = meta.read_metadata(data)
    assert result.level == 33
    assert result.format_version == 3


def test_metadata_never_raises_on_random_bytes():
    import random

    rng = random.Random(11)
    for length in (0, 1, 4, 8, 64, 512):
        blob = bytes(rng.randrange(256) for _ in range(length))
        meta.read_metadata(blob)  # must not raise
        meta.header_version(blob)
