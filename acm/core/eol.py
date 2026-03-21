"""
AETHER End-of-Life Module
Fuel monitor, graveyard maneuver at ≤2.5 kg fuel.
"""
import numpy as np
from typing import List
from acm.core.physics import MU, RE, GRAVEYARD_ALT_KM, M_DRY, tsiolkovsky_dm
from acm.core.state import SimState, ScheduledBurn
from acm.core import logger

EOL_FUEL_KG = 2.5          # trigger graveyard at this fuel level
GRAVEYARD_TRIGGER_KG = 2.5


def _graveyard_dv(sat_state: np.ndarray, fuel_kg: float) -> float:
    """
    Compute prograde delta-V to raise apogee to GRAVEYARD_ALT_KM.
    Returns dv in km/s.
    """
    r_c = float(np.linalg.norm(sat_state[:3]))
    r_g = RE + GRAVEYARD_ALT_KM
    a_t = (r_c + r_g) / 2.0
    v_circ = np.sqrt(MU / r_c)
    v_peri = np.sqrt(MU * (2.0/r_c - 1.0/a_t))
    return abs(v_peri - v_circ)


def check(sim_state: SimState):
    """
    Check all satellites for EOL condition.
    For any satellite at ≤ GRAVEYARD_TRIGGER_KG fuel:
    - Schedule graveyard burn if not already scheduled
    - Mark status as EOL
    """
    for idx, sat_id in enumerate(sim_state.sat_ids):
        if sim_state.sat_status[idx] == 'EOL':
            continue

        fuel = float(sim_state.sat_fuel_kg[idx])
        if fuel > GRAVEYARD_TRIGGER_KG:
            continue

        # Check if graveyard burn already scheduled
        already_scheduled = any(
            b.satellite_id == sat_id and b.burn_type == 'GRAVEYARD'
            for b in sim_state.maneuver_queue
        )
        if already_scheduled:
            continue

        # Compute graveyard burn
        sat_state = sim_state.sat_states[idx]
        dv_mag = _graveyard_dv(sat_state, fuel)

        # Apply prograde (T-direction)
        from acm.core.maneuver import dv_rtn_to_eci
        dv_rtn = np.array([0.0, dv_mag, 0.0])
        dv_eci = dv_rtn_to_eci(dv_rtn, sat_state)

        # Schedule at current time + 30s (next available)
        burn_time = sim_state.current_time_s + 30.0
        burn_id = sim_state.next_burn_id(sat_id, 'GRAVEYARD')

        burn = ScheduledBurn(
            satellite_id=sat_id,
            burn_id=burn_id,
            burn_time_s=burn_time,
            dv_eci_km_s=dv_eci,
            burn_type='GRAVEYARD'
        )
        sim_state.maneuver_queue.append(burn)
        sim_state.sat_status[idx] = 'EOL'

        logger.log_eol_triggered(sat_id, fuel, dv_mag, sim_state.current_time_s)
