"""
AETHER POST /api/maneuver/schedule — Schedule external maneuver
"""
import numpy as np
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from acm.api.schemas import ManeuverScheduleRequest, ManeuverScheduleResponse, ManeuverValidation
from acm.core.state import sim_state, ScheduledBurn
from acm.core.physics import M_DRY, tsiolkovsky_dm
from acm.core.ground_station import find_los_station

router = APIRouter()


def _parse_iso(ts: str) -> float:
    """Parse ISO timestamp to simulation seconds from epoch."""
    ts = ts.replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return sim_state.current_time_s + 60.0
    if sim_state.initial_epoch is None:
        return 60.0
    delta = (dt - sim_state.initial_epoch).total_seconds()
    return delta


@router.post("/api/maneuver/schedule", response_model=ManeuverScheduleResponse, status_code=202)
async def schedule_maneuver(req: ManeuverScheduleRequest):
    if not req.maneuver_sequence:
        raise HTTPException(status_code=422, detail="maneuver_sequence must not be empty")
    with sim_state.sim_lock:
        sat_idx = sim_state.get_sat_index(req.satelliteId)
        if sat_idx < 0:
            raise HTTPException(status_code=404, detail=f"Satellite {req.satelliteId} not found")

        fuel_kg = float(sim_state.sat_fuel_kg[sat_idx])
        wet_mass = M_DRY + fuel_kg
        total_fuel_cost = 0.0
        has_los = False

        for item in req.maneuver_sequence:
            dv = np.array([item.deltaV_vector.x, item.deltaV_vector.y, item.deltaV_vector.z])
            dv_mag = float(np.linalg.norm(dv))
            total_fuel_cost += tsiolkovsky_dm(wet_mass - total_fuel_cost, dv_mag)

        sufficient = (fuel_kg - total_fuel_cost) >= 0.0
        if not sufficient:
            raise HTTPException(status_code=422, detail="Insufficient fuel for maneuver sequence")

        # LOS check for first burn
        burn_time_s = _parse_iso(req.maneuver_sequence[0].burnTime) if req.maneuver_sequence else sim_state.current_time_s
        sat_state = sim_state.sat_states[sat_idx]
        gs = find_los_station(sat_state[:3])
        has_los = gs is not None

        # Schedule all burns
        for item in req.maneuver_sequence:
            dv = np.array([item.deltaV_vector.x, item.deltaV_vector.y, item.deltaV_vector.z])
            bt = _parse_iso(item.burnTime)
            burn = ScheduledBurn(
                satellite_id=req.satelliteId,
                burn_id=item.burn_id,
                burn_time_s=bt,
                dv_eci_km_s=dv,
                burn_type='MANUAL'
            )
            sim_state.maneuver_queue.append(burn)

        sim_state.maneuver_queue.sort(key=lambda b: b.burn_time_s)
        remaining_fuel = fuel_kg - total_fuel_cost

    return ManeuverScheduleResponse(
        status="SCHEDULED",
        validation=ManeuverValidation(
            ground_station_los=has_los,
            sufficient_fuel=sufficient,
            projected_mass_remaining_kg=round(M_DRY + remaining_fuel, 3)
        )
    )
