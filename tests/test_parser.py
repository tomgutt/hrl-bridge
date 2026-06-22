"""Parser unit tests — exercises every transcript shape from the TMS handbook."""

from __future__ import annotations

import pytest

from app.parser import (
    ParseError,
    encode_request,
    parse_error_line,
    parse_record_line,
)


def test_load_query_record_atlanta_dallas():
    line = (
        "LOAD_ID:LD0000045821|ORIG_CITY:Atlanta                       |ORIG_STATE:GA|"
        "ORIG_ZIP:30303|DEST_CITY:Dallas                        |DEST_STATE:TX|"
        "DEST_ZIP:75201|PICKUP_DT:20260512080000|EQTYPE:DRY_VAN   |RATE:0002150|"
        "MILES:000785|STATUS:OPEN"
    )
    rec = parse_record_line(line)
    assert rec["LOAD_ID"] == "LD0000045821"
    assert rec["ORIG_CITY"] == "Atlanta"  # trailing pad trimmed
    assert rec["ORIG_STATE"] == "GA"
    assert rec["RATE"] == 2150  # leading zeros stripped, int-coerced
    assert rec["MILES"] == 785
    assert rec["EQTYPE"] == "DRY_VAN"
    assert rec["PICKUP_DT"] == "2026-05-12T08:00:00Z"


def test_load_get_with_blank_notes_preserves_empty_string():
    line = (
        "LOAD_ID:LD0000045903|ORIG_CITY:Atlanta|DEST_CITY:Houston|RATE:0002280|"
        "WEIGHT:0040800|COMMODITY:RETAIL DRY GOODS                |PIECES:000031|"
        "MILES:000789|DIMS:48X40 STD GMA PALLETS              |"
        "NOTES:                                                                   |"
        "STATUS:OPEN    |MAX_BUY:0002065"
    )
    rec = parse_record_line(line)
    assert rec["NOTES"] == ""  # blank-but-padded notes collapse to ""
    assert rec["COMMODITY"] == "RETAIL DRY GOODS"
    assert rec["MAX_BUY"] == 2065
    assert rec["WEIGHT"] == 40800


def test_load_get_with_real_notes():
    line = (
        "LOAD_ID:LD0000046112|EQTYPE:REEFER|RATE:0003420|"
        "NOTES:Reefer set 34F continuous. Pre-cool trailer. Live unload. "
        "2H detention free, then $75/h.                          |MAX_BUY:0003080"
    )
    rec = parse_record_line(line)
    assert rec["NOTES"].startswith("Reefer set 34F")
    assert rec["NOTES"].endswith("$75/h.")  # internal whitespace preserved, trailing trimmed
    assert rec["MAX_BUY"] == 3080


def test_book_response_record():
    line = (
        "LOAD_ID:LD0000045821|BOOKING_REF:BR00000000091277|STATUS:BOOKED  |"
        "TIMESTAMP:20260504193122"
    )
    rec = parse_record_line(line)
    assert rec["BOOKING_REF"] == "BR00000000091277"
    assert rec["STATUS"] == "BOOKED"
    assert rec["TIMESTAMP"] == "2026-05-04T19:31:22Z"


def test_error_line_auth_failed():
    code, msg = parse_error_line("ERR|CODE:AUTH_FAILED|MSG:invalid or missing auth token")
    assert code == "AUTH_FAILED"
    assert msg == "invalid or missing auth token"


def test_error_line_already_booked():
    code, msg = parse_error_line("ERR|CODE:ALREADY_BOOKED|MSG:load not available")
    assert code == "ALREADY_BOOKED"


def test_parse_record_tolerates_bareword_prefix():
    # `ECHO` and `ERR` are documented response markers without a colon.
    # The parser must skip them, not reject the line.
    rec = parse_record_line("ECHO|AUTH:OK|FIELDS_PARSED:3|MSG:HELLO")
    assert rec["AUTH"] == "OK"
    assert rec["MSG"] == "HELLO"
    # Trailing bareword on an otherwise valid record is also tolerated.
    rec2 = parse_record_line("LOAD_ID:LD0000045821|BAD_PAIR")
    assert rec2["LOAD_ID"] == "LD0000045821"


def test_parse_record_rejects_empty_key():
    with pytest.raises(ParseError):
        parse_record_line(":value")


def test_encode_request_orders_cmd_then_auth():
    out = encode_request(
        "LOAD_QUERY", "t-9c3a", {"ORIG_STATE": "GA", "EQTYPE": "DRY_VAN", "MAX_RESULTS": 5}
    )
    text = out.decode("ascii")
    assert text.startswith("CMD:LOAD_QUERY|AUTH:t-9c3a|")
    assert text.endswith("\r\n")
    assert "ORIG_STATE:GA" in text
    assert "MAX_RESULTS:5" in text


def test_encode_request_rejects_pipe_in_value():
    with pytest.raises(ValueError):
        encode_request("LOAD_QUERY", "tok", {"ORIG_CITY": "A|B"})


def test_encode_request_rejects_crlf_in_value():
    with pytest.raises(ValueError):
        encode_request("LOAD_QUERY", "tok", {"NOTES": "a\r\nb"})


def test_encode_request_rejects_oversize_frame():
    huge = "x" * 5000
    with pytest.raises(ValueError):
        encode_request("LOAD_QUERY", "tok", {"NOTES": huge})
