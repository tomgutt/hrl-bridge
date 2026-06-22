"""Healthcheck — proxies DEBUG_ECHO so we cover transport + auth + framing.

DEBUG_ECHO bypasses fault injection per spec-debug-echo.md, so a green /healthz
says nothing about the operational path's reliability — operators should still
expect transient TMS faults on /loads/*.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from .. import tms_client
from ..auth import require_bridge_token

router = APIRouter()


@router.get("/healthz", dependencies=[Depends(require_bridge_token)])
async def healthz(msg: str = "probe") -> dict[str, object]:
    try:
        resp = await tms_client.call("DEBUG_ECHO", {"MSG": msg})
    except tms_client.TMSError as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail={"tms_error": e.code, "message": e.msg},
        ) from e
    except tms_client.TMSFault as f:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"tms_fault": f.kind, "detail": f.detail},
        ) from f

    if not resp.records:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "empty echo")
    rec = resp.records[0]
    return {
        "ok": True,
        "auth": rec.get("AUTH"),
        "fields_parsed": rec.get("FIELDS_PARSED"),
        "echoed_msg": rec.get("MSG"),
    }


@router.get("/livez")
async def livez() -> dict[str, bool]:
    """Liveness only. Does not touch the TMS — useful for k8s/Railway probes
    that should not page when the upstream is down."""
    return {"ok": True}
