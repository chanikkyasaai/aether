"""
AETHER GET /api/status — System health check
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
from acm.api.schemas import StatusResponse, ActiveCDM, ScheduledBurnInfo
import numpy as np
from acm.core.state import sim_state
from acm.core import logger

router = APIRouter()


@router.get("/api/status", response_model=StatusResponse)
def get_status():
    with sim_state.sim_lock:
        n_sats = len(sim_state.sat_ids)
        n_deb = len(sim_state.deb_ids)
        n_warnings = sum(1 for c in sim_state.active_cdms if c.threat_level == 'WARNING')
        n_critical = sum(1 for c in sim_state.active_cdms if c.threat_level == 'CRITICAL')
        n_queued = len(sim_state.maneuver_queue)
        total_collisions = sim_state.collision_count
        fleet_fuel = float(sim_state.sat_fuel_kg.sum()) if n_sats > 0 else 0.0

        active_cdms_list = [
            ActiveCDM(
                sat_id=c.sat_id,
                deb_id=c.deb_id,
                tca_offset_s=c.tca_offset_s,
                miss_distance_km=c.miss_distance_km,
                poc=c.poc,
                threat_level=c.threat_level,
                approach_azimuth_deg=c.approach_azimuth_deg,
            )
            for c in sim_state.active_cdms
        ]

        scheduled_burns_list = [
            ScheduledBurnInfo(
                satellite_id=b.satellite_id,
                burn_id=b.burn_id,
                burn_time_s=b.burn_time_s,
                burn_type=b.burn_type,
                dv_magnitude_m_s=round(float(np.linalg.norm(b.dv_eci_km_s)) * 1000, 2),
            )
            for b in sim_state.maneuver_queue
        ]

        los_windows = dict(sim_state.sat_los_cache)

        if sim_state.initial_epoch is not None:
            ts = sim_state.initial_epoch + timedelta(seconds=sim_state.current_time_s)
            ts_str = ts.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        else:
            ts_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    recent = logger.get_recent_events(10)

    return StatusResponse(
        system="AETHER",
        sim_time_iso=ts_str,
        satellites_tracked=n_sats,
        debris_tracked=n_deb,
        active_cdm_warnings=n_warnings,
        critical_conjunctions=n_critical,
        maneuvers_queued=n_queued,
        total_collisions=total_collisions,
        fleet_fuel_remaining_kg=round(fleet_fuel, 2),
        recent_events=recent,
        active_cdms=active_cdms_list,
        scheduled_burns=scheduled_burns_list,
        sat_los_windows=los_windows,
    )
