"""
AETHER TEST-ONLY Reset endpoint.
Only active when TEST_MODE=1 environment variable is set.
Clears all sim_state so each test starts clean.
"""
import os
import numpy as np
from fastapi import APIRouter, HTTPException
from acm.core.state import sim_state

router = APIRouter()


@router.post("/api/reset")
async def reset_simulation():
    """Reset all simulation state. Only available in TEST_MODE."""
    if os.environ.get("TEST_MODE", "0") != "1":
        raise HTTPException(status_code=403, detail="Reset only available in TEST_MODE")

    with sim_state.sim_lock:
        sim_state.sat_states = np.zeros((0, 6), dtype=np.float64)
        sim_state.sat_ids = []
        sim_state.sat_fuel_kg = np.zeros(0, dtype=np.float64)
        sim_state.sat_nominal_states = np.zeros((0, 6), dtype=np.float64)
        sim_state.sat_last_burn_time = np.zeros(0, dtype=np.float64)
        sim_state.sat_status = []

        sim_state.deb_states = np.zeros((0, 6), dtype=np.float64)
        sim_state.deb_ids = []

        sim_state.current_time_s = 0.0
        sim_state.initial_epoch = None

        sim_state.active_cdms = []
        sim_state.maneuver_queue = []

        sim_state.collision_count = 0
        sim_state.maneuvers_executed = 0
        sim_state._burn_counters = {}

        sim_state._debris_cloud_cache = []
        sim_state._snapshot_json_cache = b''

    # Clear audit log buffer
    from acm.core import logger
    logger._recent_events.clear()

    return {"status": "RESET", "message": "All simulation state cleared"}
