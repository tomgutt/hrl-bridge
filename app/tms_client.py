"""Async TCP client for the legacy TMS.

One connection per request (server closes after writing the response).
Handles all four fault categories from spec-faults.md: timeout, partial response,
malformed response, delayed termination. Faults are not signaled — we detect
them from the wire alone.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .config import settings
from .parser import ParseError, encode_request, parse_error_line, parse_record_line

log = logging.getLogger(__name__)

# These error codes are deterministic — retrying never helps.
NON_RETRYABLE = {
    "AUTH_FAILED",
    "UNKNOWN_CMD",
    "MISSING_FIELD",
    "UNKNOWN_LOAD",
    "ALREADY_BOOKED",
    "INVALID_RATE",
    "MALFORMED",
}

# Faults we detect from the wire. The TMS spec lists these explicitly as
# observable categories; we surface each with a distinct fault tag.
FAULT_TIMEOUT = "timeout"
FAULT_PARTIAL = "partial"
FAULT_MALFORMED = "malformed"


class TMSError(Exception):
    """A `ERR|CODE:..|MSG:..` response from the TMS. `code` is the protocol code."""

    def __init__(self, code: str, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


class TMSFault(Exception):
    """A wire-level fault: the response shape itself is broken or absent."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


@dataclass(slots=True)
class TMSResponse:
    """Successful response: zero or more parsed records."""

    records: list[dict[str, Any]]


async def call(cmd: str, fields: dict[str, str | int]) -> TMSResponse:
    """Issue one TMS request with retry/backoff for transient faults."""
    payload = encode_request(cmd, settings.tms_token, fields)
    last_fault: TMSFault | None = None
    backoff_ms = settings.tms_backoff_initial_ms

    for attempt in range(1, settings.tms_retry_attempts + 1):
        try:
            return await _one_shot(payload)
        except TMSError:
            # Protocol-level errors (AUTH_FAILED etc.) — never retry, bubble up.
            raise
        except TMSFault as fault:
            last_fault = fault
            log.warning(
                "tms_fault attempt=%d kind=%s detail=%s", attempt, fault.kind, fault.detail
            )
            if attempt < settings.tms_retry_attempts:
                await asyncio.sleep(backoff_ms / 1000)
                backoff_ms *= 3  # 250 → 750 → 2250

    assert last_fault is not None
    raise last_fault


async def _one_shot(payload: bytes) -> TMSResponse:
    """Open one TCP connection, write the request, read until END or close."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.tms_host, settings.tms_port),
            timeout=settings.tms_read_timeout_s,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise TMSFault(FAULT_TIMEOUT, f"connect: {exc}") from exc

    try:
        writer.write(payload)
        await writer.drain()

        try:
            data = await asyncio.wait_for(
                _read_until_end(reader), timeout=settings.tms_read_timeout_s
            )
        except asyncio.TimeoutError as exc:
            raise TMSFault(FAULT_TIMEOUT, "no response within idle timeout") from exc

        return _interpret(data)
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except (OSError, asyncio.TimeoutError):
            # Delayed-termination fault — server holds the connection open past END.
            # We've already read the response, so just abandon the socket.
            pass


async def _read_until_end(reader: asyncio.StreamReader) -> bytes:
    """Read CRLF-terminated lines until we see `END\\r\\n`, an `ERR` line, or EOF."""
    chunks: list[bytes] = []
    while True:
        line = await reader.readline()
        if not line:
            return b"".join(chunks)  # EOF — caller decides if this is partial
        chunks.append(line)
        stripped = line.rstrip(b"\r\n")
        if stripped == b"END":
            return b"".join(chunks)
        if stripped.startswith(b"ERR"):
            return b"".join(chunks)


def _interpret(data: bytes) -> TMSResponse:
    """Turn the raw byte buffer into either records, an error, or a fault."""
    if not data:
        raise TMSFault(FAULT_PARTIAL, "no bytes read")

    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise TMSFault(FAULT_MALFORMED, f"non-ascii: {exc}") from exc

    lines = [ln for ln in text.split("\r\n") if ln != ""]
    if not lines:
        raise TMSFault(FAULT_PARTIAL, "no terminated lines")

    last = lines[-1]

    if last.startswith("ERR"):
        try:
            code, msg = parse_error_line(last)
        except ParseError as exc:
            raise TMSFault(FAULT_MALFORMED, f"unparseable error line: {exc}") from exc
        raise TMSError(code, msg)

    if last != "END":
        # Success path requires a terminating END line.
        raise TMSFault(FAULT_PARTIAL, f"missing END terminator (last line: {last!r})")

    records: list[dict[str, Any]] = []
    for ln in lines[:-1]:
        try:
            records.append(parse_record_line(ln))
        except ParseError as exc:
            raise TMSFault(FAULT_MALFORMED, f"bad record: {exc}") from exc

    return TMSResponse(records=records)
