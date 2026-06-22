"""HTTPS-facing load endpoints. Translate JSON ↔ TMS wire format and redact MAX_BUY."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .. import tms_client
from ..auth import require_bridge_token

router = APIRouter(prefix="/loads", dependencies=[Depends(require_bridge_token)])
internal = APIRouter(prefix="/internal/loads", dependencies=[Depends(require_bridge_token)])


# ---------- request models ---------- #


class QueryRequest(BaseModel):
    """At least one filter required (the TMS itself enforces this and returns
    `MISSING_FIELD` otherwise — we mirror that 422 here for fail-fast behavior)."""

    orig_state: str | None = None
    orig_city: str | None = None
    orig_zip: str | None = None
    dest_state: str | None = None
    dest_city: str | None = None
    dest_zip: str | None = None
    eqtype: str | None = None
    max_results: int | None = Field(default=None, ge=1, le=200)


class BookRequest(BaseModel):
    load_id: str
    mc_num: str
    agreed_rate: int = Field(gt=0)


# ---------- helpers ---------- #


def _redact_max_buy(rec: dict[str, Any]) -> dict[str, Any]:
    """Strip MAX_BUY from any record before it leaves the bridge on the public path.

    The agent's prompt context must never contain the ceiling; making redaction
    structural (here, in the only place that talks to the wire) is what makes
    that property robust to prompt-injection attacks.
    """
    return {k: v for k, v in rec.items() if k != "MAX_BUY"}


def _to_http_status(code: str) -> int:
    """Map TMS error codes to HTTP statuses."""
    return {
        "AUTH_FAILED": status.HTTP_502_BAD_GATEWAY,
        "UNKNOWN_CMD": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "MISSING_FIELD": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "UNKNOWN_LOAD": status.HTTP_404_NOT_FOUND,
        "ALREADY_BOOKED": status.HTTP_409_CONFLICT,
        "INVALID_RATE": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "MALFORMED": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "SERVER_ERROR": status.HTTP_502_BAD_GATEWAY,
    }.get(code, status.HTTP_502_BAD_GATEWAY)


def _to_http_fault(kind: str) -> int:
    return {
        tms_client.FAULT_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
        tms_client.FAULT_PARTIAL: status.HTTP_502_BAD_GATEWAY,
        tms_client.FAULT_MALFORMED: status.HTTP_502_BAD_GATEWAY,
    }.get(kind, status.HTTP_502_BAD_GATEWAY)


async def _safe_call(cmd: str, fields: dict[str, str | int]) -> list[dict[str, Any]]:
    try:
        resp = await tms_client.call(cmd, fields)
    except tms_client.TMSError as e:
        raise HTTPException(
            _to_http_status(e.code), detail={"tms_error": e.code, "message": e.msg}
        ) from e
    except tms_client.TMSFault as f:
        raise HTTPException(
            _to_http_fault(f.kind), detail={"tms_fault": f.kind, "detail": f.detail}
        ) from f
    return resp.records


# ---------- routes ---------- #


@router.post("/query")
async def query_loads(req: QueryRequest) -> dict[str, Any]:
    fields: dict[str, str | int] = {}
    if req.orig_state: fields["ORIG_STATE"] = req.orig_state
    if req.orig_city:  fields["ORIG_CITY"]  = req.orig_city
    if req.orig_zip:   fields["ORIG_ZIP"]   = req.orig_zip
    if req.dest_state: fields["DEST_STATE"] = req.dest_state
    if req.dest_city:  fields["DEST_CITY"]  = req.dest_city
    if req.dest_zip:   fields["DEST_ZIP"]   = req.dest_zip
    if req.eqtype:     fields["EQTYPE"]     = req.eqtype
    if req.max_results is not None:
        fields["MAX_RESULTS"] = req.max_results

    if not fields:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"tms_error": "MISSING_FIELD", "message": "at least one filter required"},
        )

    records = await _safe_call("LOAD_QUERY", fields)
    return {"loads": [_redact_max_buy(r) for r in records]}


@router.get("/{load_id}")
async def get_load(load_id: str) -> dict[str, Any]:
    records = await _safe_call("LOAD_GET", {"LOAD_ID": load_id})
    if not records:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no record returned")
    return {"load": _redact_max_buy(records[0])}


@router.post("/book")
async def book_load(req: BookRequest) -> dict[str, Any]:
    records = await _safe_call(
        "LOAD_BOOK",
        {"LOAD_ID": req.load_id, "MC_NUM": req.mc_num, "AGREED_RATE": req.agreed_rate},
    )
    if not records:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "empty book response")
    rec = records[0]
    return {
        "load_id": rec.get("LOAD_ID"),
        "booking_ref": rec.get("BOOKING_REF"),
        "status": rec.get("STATUS"),
        "timestamp": rec.get("TIMESTAMP"),
    }


# ---- INTERNAL: only the workflow's negotiation tool calls this. ----
# Returns MAX_BUY. The agent's prompt context never sees the response of this route.

@internal.get("/{load_id}/ceiling")
async def get_ceiling(load_id: str) -> dict[str, Any]:
    records = await _safe_call("LOAD_GET", {"LOAD_ID": load_id})
    if not records:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no record returned")
    rec = records[0]
    return {
        "load_id": rec.get("LOAD_ID"),
        "loadboard_rate": rec.get("RATE"),
        "max_buy": rec.get("MAX_BUY"),
    }
