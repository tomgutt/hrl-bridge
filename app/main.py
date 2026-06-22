"""FastAPI entry point. Wires routes, sets up logging, exposes /healthz + /loads/*."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import settings
from .routes import health, loads

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="HRL TMS Bridge",
    version="0.1.0",
    description="HTTPS shim over the legacy TCP TMS. Redacts MAX_BUY on the public path.",
)

app.include_router(health.router)
app.include_router(loads.router)
app.include_router(loads.internal)
