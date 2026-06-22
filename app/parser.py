"""TMS wire-format parser.

The legacy TMS speaks a `KEY:VALUE|KEY:VALUE|...\\r\\n`-delimited line protocol over TCP.
This module turns one or more record lines into typed Python dicts and reverse.
Field semantics are derived from sample transcripts in the TMS handbook —
the spec is explicit that the wire is authoritative.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Fields we always coerce to int (after stripping the leading-zero padding the
# server applies). Source: spec-load-query.md / spec-load-get.md transcripts.
INT_FIELDS = {"RATE", "MAX_BUY", "MILES", "WEIGHT", "PIECES"}

# Datetime fields use YYYYMMDDHHMMSS, UTC.
DATETIME_FIELDS = {"PICKUP_DT", "DELIVERY_DT", "TIMESTAMP"}

# Free-text fields. Trailing space-padding is part of the wire, but we trim it.
# NOTES specifically may legitimately be blank — preserve "" rather than collapsing to None.
TEXT_FIELDS = {"COMMODITY", "DIMS", "NOTES"}


class ParseError(Exception):
    """Raised when a record cannot be parsed against the protocol grammar."""


def parse_record_line(line: str) -> dict[str, Any]:
    """Parse a single `KEY:VALUE|KEY:VALUE|...` line into a typed dict.

    The server space-pads many fields to fixed widths; we trim outer whitespace
    on values. We do NOT trim NOTES below — that's handled by the caller via
    the TEXT_FIELDS list, which preserves the empty-string semantic.
    """
    if not line:
        raise ParseError("empty line")

    out: dict[str, Any] = {}
    for pair in line.split("|"):
        if not pair:
            continue
        if ":" not in pair:
            # Skip leading prefix tokens like `ECHO`, `ERR` — they have no colon
            # and are documented in the spec as record markers, not KEY:VALUE pairs.
            continue
        key, _, raw = pair.partition(":")
        key = key.strip()
        if not key:
            raise ParseError(f"empty key in pair: {pair!r}")

        if key in TEXT_FIELDS:
            # Trim trailing padding but keep "" if originally blank.
            value: Any = raw.rstrip()
        else:
            value = raw.strip()

        if key in INT_FIELDS and value != "":
            try:
                value = int(value)
            except ValueError as exc:
                raise ParseError(f"non-integer in {key}: {value!r}") from exc
        elif key in DATETIME_FIELDS and value != "":
            value = _parse_dt(value, key)

        out[key] = value
    return out


def _parse_dt(raw: str, field: str) -> str:
    """`YYYYMMDDHHMMSS` UTC → ISO 8601 `Z` string."""
    if len(raw) != 14 or not raw.isdigit():
        raise ParseError(f"unexpected datetime in {field}: {raw!r}")
    try:
        dt = datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ParseError(f"bad datetime in {field}: {raw!r}") from exc
    return dt.isoformat().replace("+00:00", "Z")


def parse_error_line(line: str) -> tuple[str, str]:
    """Parse `ERR|CODE:<code>|MSG:<msg>` → (code, msg). Raises if not an error line."""
    if not line.startswith("ERR"):
        raise ParseError("not an error line")
    rec: dict[str, Any] = {}
    for pair in line.split("|"):
        if not pair or pair == "ERR" or ":" not in pair:
            continue
        k, _, v = pair.partition(":")
        rec[k.strip()] = v.strip()
    code = rec.get("CODE", "")
    msg = rec.get("MSG", "")
    if not code:
        raise ParseError("error line missing CODE")
    return str(code), str(msg)


def encode_request(cmd: str, token: str, fields: dict[str, str | int]) -> bytes:
    """Build a single `\\r\\n`-terminated request line.

    Per spec: CMD first, AUTH second, then arbitrary KEY:VALUE pairs.
    Values must NOT contain `|` or `\\r\\n`; we reject rather than mangle.
    """
    parts = [f"CMD:{cmd}", f"AUTH:{token}"]
    for k, v in fields.items():
        sv = str(v)
        if "|" in sv or "\r" in sv or "\n" in sv:
            raise ValueError(f"forbidden char in field {k}: {sv!r}")
        parts.append(f"{k}:{sv}")
    line = "|".join(parts) + "\r\n"
    if len(line.encode("ascii")) > 4096:
        raise ValueError("request exceeds 4096-byte frame limit")
    return line.encode("ascii")
