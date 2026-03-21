"""
AETHER GET /api/visualization/snapshot — Position snapshot for frontend
Must respond in < 50ms. No heavy computation.

Performance strategy:
1. Debris ECI→geodetic is pre-computed after each step/telemetry into _debris_cloud_cache.
2. The full snapshot is pre-serialized into _snapshot_json_cache (bytes) after each rebuild.
3. This endpoint returns the pre-built bytes directly via JSONResponse — zero Pydantic overhead.
"""
import json
import numpy as np
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
from fastapi.responses import Response
from acm.core.state import sim_state
from acm.core.physics import RE

router = APIRouter()


def _eci_batch_to_geodetic(positions: np.ndarray):
    r = np.linalg.norm(positions, axis=1)
    lat = np.degrees(np.arcsin(positions[:, 2] / r))
    lon = np.degrees(np.arctan2(positions[:, 1], positions[:, 0]))
    return lat, lon


def build_snapshot_cache(state) -> bytes:
    """
    Build the full snapshot JSON once and cache as bytes.
    Called from simulate_step and telemetry ingest — not on every GET.
    """
    if state.initial_epoch is not None:
        ts = state.initial_epoch + timedelta(seconds=state.current_time_s)
        ts_str = ts.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    else:
        ts_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    # Satellites — vectorized over M=50
    satellites = []
    if state.sat_states.shape[0] > 0:
        sat_lat, sat_lon = _eci_batch_to_geodetic(state.sat_states[:, :3])
        for i, sat_id in enumerate(state.sat_ids):
            satellites.append({
                "id": sat_id,
                "lat": round(float(sat_lat[i]), 6),
                "lon": round(float(sat_lon[i]), 6),
                "fuel_kg": round(float(state.sat_fuel_kg[i]), 3),
                "status": state.sat_status[i]
            })

    payload = {
        "timestamp": ts_str,
        "satellites": satellites,
        "debris_cloud": state._debris_cloud_cache
    }
    return json.dumps(payload, separators=(',', ':')).encode()


@router.get("/api/visualization/snapshot")
async def get_snapshot():
    """Return pre-serialized snapshot bytes. Zero Pydantic overhead."""
    with sim_state.sim_lock:
        cached = sim_state._snapshot_json_cache
    if cached:
        return Response(content=cached, media_type="application/json")
    # Fallback: build on demand if cache is empty (e.g. before first telemetry)
    with sim_state.sim_lock:
        data = build_snapshot_cache(sim_state)
    return Response(content=data, media_type="application/json")
