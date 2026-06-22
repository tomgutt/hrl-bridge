from fastapi import Header, HTTPException, status

from .config import settings


async def require_bridge_token(authorization: str | None = Header(default=None)) -> None:
    """Bearer auth on every inbound HTTP route."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    # Constant-time-ish compare; tokens are short so the cost is negligible.
    if token != settings.bridge_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid bearer token")
