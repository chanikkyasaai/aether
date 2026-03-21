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


def _compute_los_windows_batch():
    """
    Compute ground-station blackout windows for all satellites over the next 3 hours.
    Vectorized: LOS check is computed for ALL (M, N_GS) pairs in one NumPy call per step.
    Uses rk4_serial for the propagation loop (no thread overhead for N≤50).
    Stores result in sim_state.sat_los_cache = {sat_id: [{start_s, end_s}, ...]}
    """
    from acm.core.ground_station import GROUND_STATIONS
    from acm.core.physics import rk4_serial as _rk4_serial

    M = sim_state.sat_states.shape[0]
    if M == 0 or not GROUND_STATIONS:
        sim_state.sat_los_cache = {}
        return

    HORIZON_S = 10800.0   # 3 hours
    CHECK_STEP_S = 300.0  # 5-minute intervals
    n_steps = int(HORIZON_S / CHECK_STEP_S)

    # Pre-build (N_GS, 3) ground station ECEF matrix and min-elevation thresholds
    gs_ecef = np.array([gs.ecef for gs in GROUND_STATIONS])          # (G, 3)
    gs_min_elev = np.array([gs.min_elevation_deg for gs in GROUND_STATIONS])  # (G,)

    def _has_los_vectorized(sat_positions: np.ndarray) -> np.ndarray:
        """Return (M,) bool array: True if satellite has LOS to ANY ground station."""
        # sat_positions: (M, 3), gs_ecef: (G, 3)
        # range_vec: (M, G, 3)
        range_vec = sat_positions[:, np.newaxis, :] - gs_ecef[np.newaxis, :, :]
        gs_unit = gs_ecef / np.linalg.norm(gs_ecef, axis=1, keepdims=True)  # (G, 3)
        range_norm = np.linalg.norm(range_vec, axis=2)  # (M, G)
        range_norm = np.maximum(range_norm, 1e-9)
        # dot product of range_vec with gs_unit: (M, G)
        dot = np.einsum('mgk,gk->mg', range_vec, gs_unit)
        sin_elev = dot / range_norm  # (M, G)
        elev_deg = np.degrees(np.arcsin(np.clip(sin_elev, -1.0, 1.0)))  # (M, G)
        # satellite has LOS if elevation >= min_elevation for ANY station
        return np.any(elev_deg >= gs_min_elev[np.newaxis, :], axis=1)  # (M,)

    states = sim_state.sat_states.copy()
    current_t = sim_state.current_time_s

    # Initial LOS state (vectorized)
    has_los_init = _has_los_vectorized(states[:, :3])
    in_blackout = ~has_los_init                # (M,) bool
    blackout_start = np.full(M, current_t)     # (M,) float
    windows = [[] for _ in range(M)]

    t = current_t
    for _ in range(n_steps):
        states = _rk4_serial(states, CHECK_STEP_S)
        t += CHECK_STEP_S
        has_los = _has_los_vectorized(states[:, :3])  # (M,) vectorized

        # Blackout ends: was in blackout, now has LOS
        ended = in_blackout & has_los
        for i in np.where(ended)[0]:
            windows[i].append({'start_s': float(blackout_start[i]), 'end_s': t})
        in_blackout[ended] = False

        # Blackout starts: was not in blackout, now no LOS
        started = ~in_blackout & ~has_los
        blackout_start[started] = t
        in_blackout[started] = True

    # Close any still-open blackout windows
    for i in range(M):
        if in_blackout[i]:
            windows[i].append({'start_s': float(blackout_start[i]), 'end_s': current_t + HORIZON_S})

    sim_state.sat_los_cache = {sim_state.sat_ids[i]: windows[i] for i in range(M)}


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

        # 11. Compute ground station LOS/blackout windows for Gantt visualization
        _compute_los_windows_batch()

        # 12. Compute new timestamp
        from datetime import datetime, timezone
        new_ts = sim_state.initial_epoch + timedelta(seconds=sim_state.current_time_s)
        new_ts_iso = new_ts.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    return SimulateStepResponse(
        status="STEP_COMPLETE",
        new_timestamp=new_ts_iso,
        collisions_detected=sim_state.collision_count,
        maneuvers_executed=sim_state.maneuvers_executed
    )
