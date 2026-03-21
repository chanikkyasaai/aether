"""
AETHER FastAPI Application
Single entry point. Numba warmup in lifespan. CORS. StaticFiles.
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from acm.api import routes_telemetry, routes_simulate, routes_maneuver, routes_viz, routes_status, routes_reset
from acm.core.ground_station import init_ground_stations


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: warmup Numba JIT and initialize ground stations."""
    print("[AETHER] Warming up Numba JIT compiler...")
    from acm.core.physics import warmup
    warmup()
    print("[AETHER] Numba JIT warmup complete.")

    print("[AETHER] Loading ground stations...")
    init_ground_stations()
    print("[AETHER] Ground stations loaded.")

    print("[AETHER] System ready. Accepting requests on port 8000.")
    yield
    print("[AETHER] Shutdown.")


app = FastAPI(
    title="AETHER — Autonomous Constellation Manager",
    version="3.0.0",
    description="Orbital debris avoidance, conjunction assessment, autonomous maneuver planning.",
    lifespan=lifespan
)

# CORS — allow all origins for grader access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes — register BEFORE static files mount
app.include_router(routes_telemetry.router)
app.include_router(routes_simulate.router)
app.include_router(routes_maneuver.router)
app.include_router(routes_viz.router)
app.include_router(routes_status.router)
app.include_router(routes_reset.router)  # TEST_MODE only — enforced inside handler

# Serve React frontend — mount AFTER all API routes
FRONTEND_DIST = os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'dist')
FRONTEND_DIST = os.path.abspath(FRONTEND_DIST)
if os.path.exists(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    print(f"[AETHER] Frontend dist not found at {FRONTEND_DIST} — serving API only.")
