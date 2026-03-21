"""
AETHER POST /api/simulate/step — Advance simulation
MUST be plain def (not async) — CPU-bound, FastAPI threads it.
"""
from datetime import timedelta
from fastapi import APIRouter
import numpy as np

from acm.api.schemas import SimulateStepRequest, SimulateStepResponse
from acm.core.state import sim_state
from acm.core.physics import propagate, tsiolkovsky_dm, M_DRY
from acm.core.conjunction import screen_conjunctions
from acm.core.planner import run_autonomous_planner
from acm.core.station_keeping import propagate_nominal_slots, check_slot_recovery
from acm.core import eol
from acm.core import logger

router = APIRouter()

COLLISION_THRESHOLD_KM = 0.100  # 100 m


def _process_due_maneuvers(dt: float):
    """Apply all burns scheduled in [current_time, current_time + dt]."""
    t_start = sim_state.current_time_s
    t_end = t_start + dt
    executed = []
    remaining = []

    for burn in sim_state.maneuver_queue:
        if burn.burn_time_s <= t_end:
            sat_idx = sim_state.get_sat_index(burn.satellite_id)
            if sat_idx >= 0 and sim_state.sat_status[sat_idx] != 'EOL' or burn.burn_type == 'GRAVEYARD':
                # Apply ΔV
                dv = burn.dv_eci_km_s
                dv_mag = float(np.linalg.norm(dv))
                wet_mass = sim_state.wet_mass_kg(sat_idx) if sat_idx >= 0 else M_DRY
                fuel_before = float(sim_state.sat_fuel_kg[sat_idx]) if sat_idx >= 0 else 0.0

                dm = tsiolkovsky_dm(wet_mass, dv_mag)
                new_fuel = max(0.0, fuel_before - dm)

                if sat_idx >= 0:
                    sim_state.sat_states[sat_idx, 3:6] += dv
                    sim_state.sat_fuel_kg[sat_idx] = new_fuel
                    sim_state.sat_last_burn_time[sat_idx] = burn.burn_time_s

                    # Update status on recovery completion
                    if burn.burn_type == 'RECOVERY_2':
                        sim_state.sat_status[sat_idx] = 'RECOVERING'
                    elif burn.burn_type == 'RECOVERY_1':
                        pass  # stay EVADING until recovery_2
                    elif burn.burn_type == 'GRAVEYARD':
                        sim_state.sat_status[sat_idx] = 'EOL'

                logger.log_burn_executed(
                    burn.burn_id, burn.satellite_id,
                    dv.tolist(),
                    wet_mass, M_DRY + new_fuel,
                    sim_state.current_time_s
                )
                sim_state.maneuvers_executed += 1
                executed.append(burn)
        else:
            remaining.append(burn)

    sim_state.maneuver_queue = remaining
    return len(executed)


def _check_collisions_during_propagation(states_before: np.ndarray, states_after: np.ndarray):
    """
    Check for any satellite-debris distance < COLLISION_THRESHOLD_KM during propagation.
    """
    if sim_state.sat_states.shape[0] == 0 or sim_state.deb_states.shape[0] == 0:
        return
    M = sim_state.sat_states.shape[0]
    for sat_idx in range(M):
        sat_pos = sim_state.sat_states[sat_idx, :3]
        dists = np.linalg.norm(sim_state.deb_states[:, :3] - sat_pos, axis=1)
        close = np.where(dists < COLLISION_THRESHOLD_KM)[0]
        for deb_idx in close:
            miss = float(dists[deb_idx])
            sim_state.collision_count += 1
            logger.log_collision_detected(
                sim_state.sat_ids[sat_idx],
                sim_state.deb_ids[deb_idx],
                miss,
                sim_state.current_time_s
            )


@router.post("/api/simulate/step", response_model=SimulateStepResponse)
def simulate_step(req: SimulateStepRequest):
    """
    Advance simulation by step_seconds.
    PLAIN DEF — not async. FastAPI runs in thread pool.
    """
    dt = float(req.step_seconds)

    with sim_state.sim_lock:
        if sim_state.initial_epoch is None:
            from datetime import datetime, timezone
            sim_state.initial_epoch = datetime.now(timezone.utc)

        # 1. Process due maneuvers
        _process_due_maneuvers(dt)

        # 2. Propagate all objects (satellites + debris) in one batch
        if sim_state.sat_states.shape[0] > 0 or sim_state.deb_states.shape[0] > 0:
            M = sim_state.sat_states.shape[0]
            N = sim_state.deb_states.shape[0]

            if M > 0 and N > 0:
                all_states = np.vstack([sim_state.sat_states, sim_state.deb_states])
            elif M > 0:
                all_states = sim_state.sat_states
            else:
                all_states = sim_state.deb_states

            from acm.core.physics import propagate as phys_propagate
            all_new = phys_propagate(all_states, dt, step=30.0)

            if M > 0 and N > 0:
                sim_state.sat_states = all_new[:M]
                sim_state.deb_states = all_new[M:]
            elif M > 0:
                sim_state.sat_states = all_new
            else:
                sim_state.deb_states = all_new

        # 3. Propagate nominal slots
        propagate_nominal_slots(sim_state, dt)

        # 4. Advance time
        sim_state.current_time_s += dt

        # 5. Check for collisions
        _check_collisions_during_propagation(None, None)

        # 6. Screen conjunctions
        cdms = screen_conjunctions(sim_state)
        sim_state.active_cdms = cdms

        # 7. Run autonomous planner
        run_autonomous_planner(sim_state, cdms)

        # 8. EOL check
        eol.check(sim_state)

        # 9. Station keeping check
        check_slot_recovery(sim_state)

        # 10. Rebuild debris snapshot cache (amortizes ECI→geodetic cost across snapshot calls)
        sim_state.rebuild_debris_cache()

        # 11. Compute new timestamp
        from datetime import datetime, timezone
        new_ts = sim_state.initial_epoch + timedelta(seconds=sim_state.current_time_s)
        new_ts_iso = new_ts.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    return SimulateStepResponse(
        status="STEP_COMPLETE",
        new_timestamp=new_ts_iso,
        collisions_detected=sim_state.collision_count,
        maneuvers_executed=sim_state.maneuvers_executed
    )
