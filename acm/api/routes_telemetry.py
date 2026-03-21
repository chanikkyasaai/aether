"""
AETHER POST /api/telemetry — Ingest orbital telemetry
"""
from datetime import datetime, timezone
from fastapi import APIRouter
import numpy as np

from acm.api.schemas import TelemetryRequest, TelemetryResponse
from acm.core.state import sim_state
from acm.core.physics import M_DRY, M_FUEL_INIT, CONSTELLATION_ALT_KM, RE

router = APIRouter()


def _parse_iso(ts: str) -> datetime:
    ts = ts.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.now(timezone.utc)


VALID_TYPES = {"SATELLITE", "DEBRIS"}


@router.post("/api/telemetry", response_model=TelemetryResponse)
async def ingest_telemetry(req: TelemetryRequest):
    from fastapi import HTTPException

    # Validate object types — reject unknown types with 422
    for obj in req.objects:
        if obj.type.upper() not in VALID_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown object type '{obj.type}'. Must be SATELLITE or DEBRIS."
            )

    epoch = _parse_iso(req.timestamp)

    with sim_state.sim_lock:
        # Set initial epoch on first call
        if sim_state.initial_epoch is None:
            sim_state.initial_epoch = epoch

        # Deduplicate within batch — keep last occurrence of each ID
        seen_sat = {}
        seen_deb = {}
        for o in req.objects:
            if o.type.upper() == "SATELLITE":
                seen_sat[o.id] = o
            else:
                seen_deb[o.id] = o
        satellites = list(seen_sat.values())
        debris_list = list(seen_deb.values())

        # Upsert satellites: new → append with defaults, existing → overwrite state, preserve fuel/status
        if satellites:
            existing_sat_ids = list(sim_state.sat_ids)
            existing_sat_states = sim_state.sat_states.copy() if sim_state.sat_states.shape[0] > 0 else np.zeros((0, 6))
            existing_sat_nominal = sim_state.sat_nominal_states.copy() if sim_state.sat_nominal_states.shape[0] > 0 else np.zeros((0, 6))
            existing_sat_fuel = sim_state.sat_fuel_kg.copy() if sim_state.sat_fuel_kg.shape[0] > 0 else np.zeros(0)
            existing_sat_burn_time = sim_state.sat_last_burn_time.copy() if sim_state.sat_last_burn_time.shape[0] > 0 else np.zeros(0)
            existing_sat_status = list(sim_state.sat_status)

            for obj in satellites:
                sv = np.array([obj.r.x, obj.r.y, obj.r.z,
                               obj.v.x, obj.v.y, obj.v.z], dtype=np.float64)
                if obj.id in existing_sat_ids:
                    idx = existing_sat_ids.index(obj.id)
                    existing_sat_states[idx] = sv
                    # Preserve fuel, status, burn_time; update nominal only on first ingest
                else:
                    existing_sat_ids.append(obj.id)
                    existing_sat_states = np.vstack([existing_sat_states, sv]) if existing_sat_states.shape[0] > 0 else sv.reshape(1, 6)
                    existing_sat_nominal = np.vstack([existing_sat_nominal, sv]) if existing_sat_nominal.shape[0] > 0 else sv.reshape(1, 6)
                    existing_sat_fuel = np.append(existing_sat_fuel, M_FUEL_INIT)
                    existing_sat_burn_time = np.append(existing_sat_burn_time, -1e9)
                    existing_sat_status.append('NOMINAL')

            sim_state.sat_ids = existing_sat_ids
            sim_state.sat_states = existing_sat_states.astype(np.float64)
            sim_state.sat_nominal_states = existing_sat_nominal.astype(np.float64)
            sim_state.sat_fuel_kg = existing_sat_fuel.astype(np.float64)
            sim_state.sat_last_burn_time = existing_sat_burn_time.astype(np.float64)
            sim_state.sat_status = existing_sat_status

        # Upsert debris: new → append, existing → overwrite state
        if debris_list:
            existing_deb_ids = list(sim_state.deb_ids)
            existing_deb_states = sim_state.deb_states.copy() if sim_state.deb_states.shape[0] > 0 else np.zeros((0, 6))

            for obj in debris_list:
                sv = np.array([obj.r.x, obj.r.y, obj.r.z,
                               obj.v.x, obj.v.y, obj.v.z], dtype=np.float64)
                if obj.id in existing_deb_ids:
                    idx = existing_deb_ids.index(obj.id)
                    existing_deb_states[idx] = sv
                else:
                    existing_deb_ids.append(obj.id)
                    existing_deb_states = np.vstack([existing_deb_states, sv]) if existing_deb_states.shape[0] > 0 else sv.reshape(1, 6)

            sim_state.deb_ids = existing_deb_ids
            sim_state.deb_states = existing_deb_states.astype(np.float64)

        # Rebuild snapshot cache so first snapshot is fast
        if satellites or debris_list:
            sim_state.rebuild_debris_cache()

        active_warnings = sum(1 for c in sim_state.active_cdms if c.threat_level == 'WARNING')
        processed = len(satellites) + len(debris_list)

    return TelemetryResponse(
        status="ACK",
        processed_count=processed,
        active_cdm_warnings=active_warnings
    )
