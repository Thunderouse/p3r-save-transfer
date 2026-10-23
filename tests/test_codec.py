"""Codec tests: known vectors, reference agreement, round-trip, magic detection."""

from __future__ import annotations

import random

import pytest

from p3r_transfer import codec


def c_decrypt_byte(data: int, key: int) -> int:
    """Literal transcription of illusionyy/P3R-Save-EnDecryptor decrypt_byte."""
    b_var1 = (data ^ key) & 0xFF
    return ((b_var1 >> 4) & 3) | ((b_var1 & 3) << 4) | (b_var1 & 0xCC)


def c_encrypt_byte(data: int, key: int) -> int:
    """Literal transcription of illusionyy/P3R-Save-EnDecryptor encrypt_byte."""
    return ((((data & 0xFF) >> 4) & 3) | ((data & 3) << 4) | (data & 0xCC)) ^ key


def reference(data: bytes, *, sealing: bool) -> bytes:
    transform = c_encrypt_byte if sealing else c_decrypt_byte
    return bytes(transform(byte, codec.KEY[i % len(codec.KEY)]) for i, byte in enumerate(data))


def test_key_is_31_bytes():
    assert codec.KEY == b"ae5zeitaix1joowooNgie3fahP5Ohph"
    assert codec.KEY_LEN == 31


def test_gvas_vector_round_trip():
    sealed = codec.encrypt(b"GVAS")
    assert sealed == bytes.fromhex("1500650b")
    assert int.from_bytes(sealed, "little") == codec.OBFUSCATED_MAGIC_LE == 0x0B650015
    assert codec.decrypt(sealed) == b"GVAS"
    assert int.from_bytes(b"GVAS", "little") == codec.GVAS_MAGIC_LE == 0x53415647


def test_matches_reference_c_expressions():
    """Compare against the C reference formulas for every byte and key phase."""
    blob = bytes(range(256)) * 31
    assert codec.encrypt(blob) == reference(blob, sealing=True)
    assert codec.decrypt(blob) == reference(blob, sealing=False)

    for phase, key_byte in enumerate(codec.KEY):
        assert codec._SEAL_TABLES[phase] == bytes(
            c_encrypt_byte(value, key_byte) for value in range(256)
        )
        assert codec._OPEN_TABLES[phase] == bytes(
            c_decrypt_byte(value, key_byte) for value in range(256)
        )


@pytest.mark.parametrize(
    "length", [0, 1, 2, 3, 4, 5, 29, 30, 31, 32, 33, 61, 62, 63, 64, 1000, 65536]
)
def test_round_trip_any_length(length: int):
    rng = random.Random(9000 + length)
    blob = bytes(rng.randrange(256) for _ in range(length))
    sealed = codec.encrypt(blob)
    assert len(sealed) == len(blob)
    assert codec.decrypt(sealed) == blob
    assert codec.encrypt(codec.decrypt(blob)) == blob
    assert codec.decrypt(codec.encrypt(blob)) == blob
    if length >= 4:
        assert codec.detect_kind(blob) in {codec.SaveKind.GVAS, codec.SaveKind.UNKNOWN}


def test_matches_reference_on_random_blobs():
    rng = random.Random(4242)
    for length in (7, 31, 32, 155, 1024):
        blob = bytes(rng.randrange(256) for _ in range(length))
        assert codec.encrypt(blob) == reference(blob, sealing=True)
        assert codec.decrypt(blob) == reference(blob, sealing=False)


def test_swap_bits_is_an_involution_and_a_permutation():
    outputs = [codec.swap_bits(value) for value in range(256)]
    assert sorted(outputs) == list(range(256))
    for value in range(256):
        assert codec.swap_bits(codec.swap_bits(value)) == value
        # bits 2, 3, 6, 7 are untouched
        assert codec.swap_bits(value) & 0xCC == value & 0xCC


def test_empty_input_stays_empty():
    assert codec.encrypt(b"") == b""
    assert codec.decrypt(b"") == b""
    assert codec.detect_kind(b"") is codec.SaveKind.UNKNOWN


def test_short_magic_helpers():
    assert codec.magic_of(b"GVA") is None
    assert codec.magic_of(b"GVAS") == 0x53415647
    assert codec.format_magic(b"G") == "<файл короче 4 байт>"
    assert codec.format_magic(b"\x15\x00\x65\x0b") == "0x0B650015"
    assert codec.format_magic(b"XXXX") == "0x58585858"


def test_detect_kind_matrix():
    assert codec.detect_kind(b"GVAS" + b"\x00" * 40) is codec.SaveKind.GVAS
    assert codec.detect_kind(bytes.fromhex("1500650b") + b"\x00" * 40) is codec.SaveKind.OBFUSCATED
    assert codec.detect_kind(b"GVA") is codec.SaveKind.UNKNOWN
    assert codec.detect_kind(b"XXXX" + b"\x00" * 40) is codec.SaveKind.UNKNOWN
    assert codec.detect_kind(codec.encrypt(b"GVAS")) is codec.SaveKind.OBFUSCATED


def test_input_is_not_mutated():
    data = bytearray(b"GVAS" + bytes(range(60)))
    snapshot = bytes(data)
    codec.encrypt(bytes(data))
    codec.decrypt(bytes(data))
    assert bytes(data) == snapshot


def test_sealed_output_is_never_plain_gvas():
    rng = random.Random(7)
    blob = b"GVAS" + bytes(rng.randrange(256) for _ in range(500))
    assert codec.detect_kind(codec.encrypt(blob)) is codec.SaveKind.OBFUSCATED
    assert codec.detect_kind(codec.decrypt(blob)) is not codec.SaveKind.OBFUSCATED
