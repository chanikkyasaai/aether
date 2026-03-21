"""
AETHER Station Keeping Module
Nominal slot propagation and recovery trigger.
"""
import numpy as np
from acm.core.state import SimState
from acm.core.physics import propagate, RE

SLOT_RECOVERY_THRESHOLD_KM = 10.0   # km — trigger recovery if outside this
NOMINAL_STEP_S = 30.0


def propagate_nominal_slots(sim_state: SimState, dt: float):
    """
    Propagate nominal slot states alongside satellite states.
    Called every simulation step.
    """
    if sim_state.sat_nominal_states.shape[0] == 0:
        return
    sim_state.sat_nominal_states = propagate(sim_state.sat_nominal_states, dt, step=NOMINAL_STEP_S)


def check_slot_recovery(sim_state: SimState):
    """
    For satellites in RECOVERING state: check if they've returned to nominal slot.
    If slot error < SLOT_RECOVERY_THRESHOLD_KM, mark as NOMINAL and log recovery.
    """
    from acm.core import logger
    for idx, sat_id in enumerate(sim_state.sat_ids):
        if sim_state.sat_status[idx] != 'RECOVERING':
            continue
        sat_pos = sim_state.sat_states[idx, :3]
        nom_pos = sim_state.sat_nominal_states[idx, :3]
        slot_error = float(np.linalg.norm(sat_pos - nom_pos))
        if slot_error < SLOT_RECOVERY_THRESHOLD_KM:
            sim_state.sat_status[idx] = 'NOMINAL'
            logger.log_recovery_complete(sat_id, slot_error, 0.0, sim_state.current_time_s)
