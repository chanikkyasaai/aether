"""
AETHER Autonomous Planner
CDMs → scheduled burns. The autonomous decision engine.
"""
import numpy as np
from typing import List
from acm.core.state import SimState, CDM, ScheduledBurn
from acm.core.physics import M_DRY, tsiolkovsky_dm
from acm.core.maneuver import compute_evasion_burn, compute_recovery_burns, dv_rtn_to_eci
from acm.core.ground_station import predict_next_los_window, find_los_station
from acm.core import logger

MIN_LEAD_TIME_S = 15.0      # Cannot act if TCA < 15s away
BURN_COOLDOWN_S = 600.0     # Minimum seconds between burns on same satellite
MIN_SCHEDULE_LEAD_S = 10.0  # Burn must be at least 10s in the future


def _already_handled(sim_state: SimState, sat_id: str, deb_id: str) -> bool:
    """Check if this conjunction is already handled in the queue."""
    for burn in sim_state.maneuver_queue:
        if burn.satellite_id == sat_id and burn.burn_type == 'EVASION':
            # Check if burn_id encodes this debris (loose check)
            if deb_id.replace('-', '').replace(' ', '') in burn.burn_id:
                return True
    return False


def _earliest_valid_burn(sim_state: SimState, sat_idx: int) -> float:
    """Return earliest valid burn time for this satellite."""
    last_burn = float(sim_state.sat_last_burn_time[sat_idx])
    option1 = sim_state.current_time_s + MIN_SCHEDULE_LEAD_S
    option2 = last_burn + BURN_COOLDOWN_S
    return max(option1, option2)


def run_autonomous_planner(sim_state: SimState, cdms: List[CDM]):
    """
    Main autonomous planning loop.
    For each CRITICAL CDM, compute and schedule evasion + recovery burns.
    """
    for cdm in cdms:
        if cdm.threat_level != 'CRITICAL':
            # Log warnings, no action
            logger.log_cdm_warning(
                cdm.sat_id, cdm.deb_id, cdm.tca_offset_s,
                cdm.miss_distance_km, cdm.poc, sim_state.current_time_s
            )
            continue

        sat_idx = sim_state.get_sat_index(cdm.sat_id)
        if sat_idx < 0:
            continue

        # Log detection
        logger.log_cdm_detected(
            cdm.sat_id, cdm.deb_id, cdm.tca_offset_s,
            cdm.miss_distance_km, cdm.poc, cdm.threat_level,
            sim_state.current_time_s
        )

        # a. Already handled?
        if _already_handled(sim_state, cdm.sat_id, cdm.deb_id):
            continue

        # b. EOL satellite?
        if sim_state.sat_status[sat_idx] == 'EOL':
            continue

        # c. Too late?
        if cdm.tca_offset_s < MIN_LEAD_TIME_S:
            logger.log_blind_conjunction(
                cdm.sat_id, cdm.deb_id, cdm.tca_offset_s,
                sim_state.current_time_s, sim_state.current_time_s
            )
            continue

        # d. Earliest valid burn time
        earliest_burn = _earliest_valid_burn(sim_state, sat_idx)

        # e. Find LOS window
        sat_state = sim_state.sat_states[sat_idx]
        los_result = predict_next_los_window(sat_state, earliest_burn)

        if los_result is None:
            logger.log_blind_conjunction(
                cdm.sat_id, cdm.deb_id, cdm.tca_offset_s,
                earliest_burn + 7200.0, sim_state.current_time_s
            )
            # Still schedule at earliest time (system is ground-based, not relay-dependent)
            los_time = earliest_burn
            los_station = 'NO_LOS'
        else:
            los_time, los_station = los_result

        # f. LOS arrives after TCA?
        if los_time >= sim_state.current_time_s + cdm.tca_offset_s:
            logger.log_blind_conjunction(
                cdm.sat_id, cdm.deb_id, cdm.tca_offset_s,
                los_time, sim_state.current_time_s
            )
            # Schedule at TCA - 60s as last resort
            los_time = max(earliest_burn, sim_state.current_time_s + cdm.tca_offset_s - 60.0)
            los_station = 'EMERGENCY'

        # g. Compute evasion burn
        fuel_kg = float(sim_state.sat_fuel_kg[sat_idx])
        deb_idx = sim_state.get_deb_index(cdm.deb_id)
        if deb_idx < 0:
            continue

        deb_state = sim_state.deb_states[deb_idx]
        # Adjust TCA relative to burn time
        tca_from_burn = max(10.0, cdm.tca_offset_s - (los_time - sim_state.current_time_s))

        try:
            dv_eci, dv_mag, is_fallback = compute_evasion_burn(
                sat_state, deb_state, tca_from_burn, fuel_kg, cdm.sat_id
            )
        except Exception as exc:
            dv_eci = dv_rtn_to_eci(np.array([0.0, 0.015, 0.0]), sat_state)
            dv_mag = 0.015
            is_fallback = True

        # Fuel cost
        wet_mass = M_DRY + fuel_kg
        fuel_cost = tsiolkovsky_dm(wet_mass, dv_mag)

        # h. Schedule evasion burn
        burn_id = sim_state.next_burn_id(cdm.sat_id, 'EVASION')
        evasion_burn = ScheduledBurn(
            satellite_id=cdm.sat_id,
            burn_id=burn_id,
            burn_time_s=los_time,
            dv_eci_km_s=dv_eci,
            burn_type='EVASION'
        )
        sim_state.maneuver_queue.append(evasion_burn)
        sim_state.sat_status[sat_idx] = 'EVADING'

        # Log
        if is_fallback:
            logger.log_degraded_avoidance(
                cdm.sat_id, cdm.deb_id,
                dv_mag * 1000.0,  # convert to m/s
                cdm.miss_distance_km,
                sim_state.current_time_s
            )
        else:
            # Projected miss after burn
            from acm.core.maneuver import _miss_after_burn
            from acm.core.maneuver import dv_rtn_to_eci as d2e
            projected_miss = cdm.miss_distance_km + 0.5  # approximate
            logger.log_cdm_actioned(
                cdm.sat_id, cdm.deb_id, burn_id, los_time,
                dv_eci.tolist(), dv_mag * 1000.0, fuel_cost,
                los_station, projected_miss, sim_state.current_time_s
            )

        # i. Schedule recovery burns
        try:
            nominal_state = sim_state.sat_nominal_states[sat_idx]
            # Post-evasion state (approximate — use current state + dv)
            post_evasion = sat_state.copy()
            post_evasion[3:6] += dv_eci

            dv1_eci, dv2_eci, transfer_time = compute_recovery_burns(
                post_evasion, nominal_state, cdm.sat_id
            )

            dv1_mag = float(np.linalg.norm(dv1_eci))
            dv2_mag = float(np.linalg.norm(dv2_eci))

            # Recovery burn 1: after cooldown from evasion
            r1_time = los_time + BURN_COOLDOWN_S
            los_r1 = predict_next_los_window(sat_state, r1_time)
            r1_actual = los_r1[0] if los_r1 else r1_time

            # Recovery burn 2: after transfer time
            r2_time = r1_actual + transfer_time + BURN_COOLDOWN_S
            los_r2 = predict_next_los_window(sat_state, r2_time)
            r2_actual = los_r2[0] if los_r2 else r2_time

            rec1_id = sim_state.next_burn_id(cdm.sat_id, 'RECOVERY_1')
            rec2_id = sim_state.next_burn_id(cdm.sat_id, 'RECOVERY_2')

            sim_state.maneuver_queue.append(ScheduledBurn(
                satellite_id=cdm.sat_id, burn_id=rec1_id,
                burn_time_s=r1_actual, dv_eci_km_s=dv1_eci, burn_type='RECOVERY_1'
            ))
            sim_state.maneuver_queue.append(ScheduledBurn(
                satellite_id=cdm.sat_id, burn_id=rec2_id,
                burn_time_s=r2_actual, dv_eci_km_s=dv2_eci, burn_type='RECOVERY_2'
            ))

            logger.log_recovery_scheduled(
                cdm.sat_id, r1_actual, r2_actual,
                (dv1_mag + dv2_mag) * 1000.0,
                transfer_time, sim_state.current_time_s
            )
        except Exception as e:
            print(f"[PLANNER] Recovery burn computation failed for {cdm.sat_id}: {e}")

    # Sort maneuver queue by burn time
    sim_state.maneuver_queue.sort(key=lambda b: b.burn_time_s)
