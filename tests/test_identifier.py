import pandas as pd
import pytest

from fleetpulse.j1939.identifier import parse_can_id, parse_can_ids
from fleetpulse.j1939.pgn_catalog import PgnCategory, classify_pgn, pgn_name


def test_eec1_identifier_manually_verified():
    j = parse_can_id(0x0CF004E6)
    assert j.priority == 3
    assert j.extended_data_page == 0
    assert j.data_page == 0
    assert j.pdu_format == 0xF0
    assert j.pdu_specific == 0x04
    assert j.source_address == 0xE6
    assert j.is_pdu2
    assert j.destination_address is None
    assert j.pgn == 61444


def test_pdu1_destination_not_part_of_pgn():
    # Request (PF=0xEA) to global address 0xFF from SA 0x00
    j = parse_can_id(0x18EAFF00)
    assert j.priority == 6
    assert j.pdu_format == 0xEA
    assert not j.is_pdu2
    assert j.destination_address == 0xFF
    assert j.pgn == 59904


def test_pdu1_specific_destination():
    j = parse_can_id(0x18EA17F9)
    assert j.destination_address == 0x17
    assert j.source_address == 0xF9
    assert j.pgn == 59904


def test_data_page_one():
    j = parse_can_id(0x15FF55E6)  # seen in the Turku sample
    assert j.priority == 5
    assert j.data_page == 1
    assert j.pgn == 0x1FF55 == 130901
    assert classify_pgn(j.pgn) is PgnCategory.PROPRIETARY_B


def test_short_hex_id_without_leading_zero():
    # Logger writes 0x8fe6ee6, i.e. priority 2
    j = parse_can_id(int("0x8fe6ee6", 16))
    assert j.priority == 2
    assert j.pgn == 65134


@pytest.mark.parametrize("bad", [-1, 0x20000000, 0xFFFFFFFF])
def test_rejects_non_29_bit(bad):
    with pytest.raises(ValueError):
        parse_can_id(bad)


def test_vectorised_matches_scalar():
    ids = [0x0CF004E6, 0x18EAFF00, 0x15FF55E6, 0x08FE6EE6, 0x18E5FFB8, 0x1CFF9053]
    df = parse_can_ids(pd.Series(ids))
    for i, can_id in enumerate(ids):
        j = parse_can_id(can_id)
        row = df.iloc[i]
        assert row["pgn"] == j.pgn
        assert row["priority"] == j.priority
        assert row["source_address"] == j.source_address
        assert row["data_page"] == j.data_page
        assert row["is_pdu2"] == j.is_pdu2
        if j.destination_address is None:
            assert pd.isna(row["destination_address"])
        else:
            assert row["destination_address"] == j.destination_address


def test_vectorised_rejects_invalid():
    with pytest.raises(ValueError):
        parse_can_ids(pd.Series([0x0CF004E6, 0x20000000]))


@pytest.mark.parametrize(
    "pgn,category",
    [
        (61444, PgnCategory.STANDARD_DOCUMENTED),
        (61184, PgnCategory.PROPRIETARY_A),
        (126720, PgnCategory.PROPRIETARY_A2),
        (65280, PgnCategory.PROPRIETARY_B),
        (65535, PgnCategory.PROPRIETARY_B),
        (130816, PgnCategory.PROPRIETARY_B),
        (58624, PgnCategory.STANDARD_UNMAPPED),
    ],
)
def test_classify_pgn(pgn, category):
    assert classify_pgn(pgn) is category


def test_unknown_pgns_are_never_named():
    assert pgn_name(61444) == "EEC1"
    assert pgn_name(65408).startswith("UNKNOWN")
    assert pgn_name(58624).startswith("UNKNOWN")
