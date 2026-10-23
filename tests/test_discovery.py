"""Discovery of wgs (Microsoft Store) and Steam save trees."""

from __future__ import annotations

import pytest

import synth
from p3r_transfer import discovery
from p3r_transfer.codec import SaveKind
from p3r_transfer.errors import SteamDirNotFoundError, TransferError

USER_ID = "25354ABCDEF01234"
CONTAINER = "8F3A1C2E-0B4D-4E6F-9A11-223344556677"


def make_wgs_tree(root, *, files=1):
    wgs = root / discovery.WGS_RELATIVE
    user = wgs / USER_ID
    user.mkdir(parents=True)
    for index in range(files):
        parent = f"0000000{index}-1111-2222-3333-4444444444{index}"
        leaf = user / parent / CONTAINER
        leaf.mkdir(parents=True)
        (leaf / "C8F1A2B3D4E5F60718293A4B5C6D7E8F").write_bytes(synth.full_save())
        (leaf / "container.0000").write_bytes(b"\x00" * 64)
    return wgs


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "Roaming"
    local.mkdir()
    roaming.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    return local, roaming


def test_default_roots_come_from_environment(fake_env):
    local, roaming = fake_env
    assert discovery.default_wgs_root() == local / discovery.WGS_RELATIVE
    assert discovery.default_steam_root() == roaming / discovery.STEAM_RELATIVE


def test_default_roots_without_env(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(TransferError):
        discovery.default_wgs_root()
    with pytest.raises(TransferError):
        discovery.default_steam_root()


def test_resolve_wgs_root_missing(tmp_path):
    with pytest.raises(TransferError):
        discovery.resolve_wgs_root(tmp_path / "nope")


def test_resolve_wgs_root_points_at_file(tmp_path):
    target = tmp_path / "file"
    target.write_text("x")
    with pytest.raises(TransferError):
        discovery.resolve_wgs_root(target)


def test_iter_wgs_saves_reads_metadata_and_guid(fake_env):
    local, _ = fake_env
    wgs = make_wgs_tree(local)
    records = list(discovery.iter_wgs_saves(wgs))
    assert len(records) == 1
    record = records[0]
    assert record.kind is SaveKind.GVAS
    assert record.container_guid == CONTAINER
    assert record.user_id == USER_ID
    assert record.meta is not None and record.meta.level == 12
    assert record.size == len(synth.full_save())
    assert record.magic == 0x53415647
    assert record.slot is None


def test_iter_wgs_saves_skips_service_and_empty_files(fake_env):
    local, _ = fake_env
    wgs = make_wgs_tree(local, files=1)
    leaf = next(wgs.rglob("C8F1A2B3D4E5F60718293A4B5C6D7E8F")).parent
    (leaf / "empty.bin").write_bytes(b"")
    (leaf / "note.tmp").write_bytes(b"junk")
    (leaf / ".hidden").write_bytes(b"junk")
    assert len(list(discovery.iter_wgs_saves(wgs))) == 1


def test_iter_wgs_saves_reports_unknown_magic(fake_env):
    local, _ = fake_env
    wgs = make_wgs_tree(local)
    leaf = next(wgs.rglob("C8F1A2B3D4E5F60718293A4B5C6D7E8F")).parent
    (leaf / "weird.bin").write_bytes(b"XXXX" + b"\x00" * 64)
    records = list(discovery.iter_wgs_saves(wgs))
    assert len(records) == 2
    unknown = [r for r in records if r.kind is SaveKind.UNKNOWN]
    assert unknown and unknown[0].magic == 0x58585858


def test_steam_tree_and_slots(fake_env):
    _, roaming = fake_env
    steam = roaming / discovery.STEAM_RELATIVE / "11111111111111111"
    steam.mkdir(parents=True)
    (steam / "SaveData0007.sav").write_bytes(synth.sealed_save())
    (steam / "SaveData0008.sav").write_bytes(synth.sealed_save())
    (roaming / discovery.STEAM_RELATIVE / "not-a-steamid").mkdir()

    ids = discovery.find_steam_ids(roaming / discovery.STEAM_RELATIVE)
    assert [p.name for p in ids] == ["11111111111111111"]

    records = list(discovery.iter_steam_saves(roaming / discovery.STEAM_RELATIVE))
    assert {r.slot for r in records} == {7, 8}
    assert all(r.kind is SaveKind.OBFUSCATED for r in records)


def test_resolve_steam_root_missing(tmp_path):
    with pytest.raises(SteamDirNotFoundError):
        discovery.resolve_steam_root(tmp_path / "nope")


def test_slot_from_filename():
    assert discovery.slot_from_filename("SaveData001.sav") == 1
    assert discovery.slot_from_filename("savedata021.sav") == 21
    assert discovery.slot_from_filename("SaveData0000.sav") is None
    assert discovery.slot_from_filename("SaveData1000.sav") is None
    assert discovery.slot_from_filename("save.sav") is None


def test_existing_slots(tmp_path):
    (tmp_path / "SaveData003.sav").write_bytes(b"x")
    (tmp_path / "SaveData001.sav").write_bytes(b"x")
    (tmp_path / "other.bin").write_bytes(b"x")
    slots = discovery.existing_slots(tmp_path)
    assert sorted(slots) == [1, 3]
    assert discovery.existing_slots(tmp_path / "missing") == {}


def test_sort_for_slots_uses_play_time_then_mtime(fake_env):
    local, _ = fake_env
    wgs = make_wgs_tree(local, files=3)
    records = list(discovery.iter_wgs_saves(wgs))
    for offset, record in enumerate(records):
        object.__setattr__(record, "mtime", 1000.0 + offset)
    ordered = discovery.sort_for_slots(records)
    # all three have identical synthetic play time, so mtime breaks the tie
    assert [r.mtime for r in ordered] == [1000.0, 1001.0, 1002.0]

    records[0] = discovery.SaveRecord(
        path=records[0].path,
        size=records[0].size,
        kind=records[0].kind,
        mtime=9999.0,
        meta=synth_meta(60),
    )
    assert discovery.sort_for_slots(records)[0].path == records[0].path


def synth_meta(seconds):
    from p3r_transfer.meta import SaveMetadata

    return SaveMetadata(play_time_seconds=seconds, parsed=True)
