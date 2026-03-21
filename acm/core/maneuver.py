"""
AETHER Maneuver Module
RTN<->ECI transforms, SLSQP evasion optimization, Hohmann recovery burns.
"""
import numpy as np
from scipy.optimize import minimize
from typing import Tuple, Optional
from acm.core.physics import MU, tsiolkovsky_dm, M_DRY, propagate
from acm.core.state import ScheduledBurn


MAX_DV_KM_S = 0.015     # 15 m/s thrust limit
STANDOFF_HIGH = 0.500   # km — nominal standoff (fuel > 50%)
STANDOFF_MED = 0.200    # km — reduced standoff (10-50% fuel)
EOL_FUEL_FRACTION = 0.10


def rtn_to_eci_matrix(sat_state: np.ndarray) -> np.ndarray:
    """
    Build 3x3 rotation matrix from RTN frame to ECI frame.
    R_hat: radial (Earth center → satellite)
    N_hat: normal (perpendicular to orbital plane)
    T_hat: transverse (in direction of velocity)
    """
    r = sat_state[:3]
    v = sat_state[3:6]
    r_hat = r / np.linalg.norm(r)
    n_cross = np.cross(r, v)
    n_norm = np.linalg.norm(n_cross)
    if n_norm < 1e-12:
        # Degenerate case — use arbitrary normal
        n_hat = np.array([0.0, 0.0, 1.0])
    else:
        n_hat = n_cross / n_norm
    t_hat = np.cross(n_hat, r_hat)
    # Columns: R, T, N
    return np.column_stack([r_hat, t_hat, n_hat])


def dv_rtn_to_eci(dv_rtn: np.ndarray, sat_state: np.ndarray) -> np.ndarray:
    """Convert delta-V from RTN frame to ECI frame."""
    M = rtn_to_eci_matrix(sat_state)
    return M @ dv_rtn


def _miss_after_burn(dv_rtn: np.ndarray, sat_state: np.ndarray, deb_state: np.ndarray,
                     tca_s: float) -> float:
    """Compute miss distance at TCA after applying dv_rtn to satellite."""
    dv_eci = dv_rtn_to_eci(dv_rtn, sat_state)
    new_sat = sat_state.copy()
    new_sat[3:6] += dv_eci
    combined = np.vstack([new_sat.reshape(1, 6), deb_state.reshape(1, 6)])
    propagated = propagate(combined, tca_s, step=30.0)
    return float(np.linalg.norm(propagated[0, :3] - propagated[1, :3]))


def compute_evasion_burn(sat_state: np.ndarray, deb_state: np.ndarray,
                          tca_s: float, fuel_kg: float,
                          sat_id: str) -> Tuple[np.ndarray, float, bool]:
    """
    SLSQP constrained minimization for minimum-fuel evasion.
    Returns (dv_eci_km_s, dv_magnitude_km_s, is_fallback)
    """
    wet_mass = M_DRY + fuel_kg
    fuel_fraction = fuel_kg / 50.0  # fraction of initial fuel

    # Choose standoff based on fuel level
    if fuel_fraction < EOL_FUEL_FRACTION:
        # Very low fuel — use minimum standoff
        standoff = 0.100
    elif fuel_fraction < 0.5:
        standoff = STANDOFF_MED
    else:
        standoff = STANDOFF_HIGH

    # Objective: minimize |dv_rtn|
    def objective(dv): return np.linalg.norm(dv)

    # Constraint: miss at TCA >= standoff
    def miss_constraint(dv):
        return _miss_after_burn(dv, sat_state, deb_state, tca_s) - standoff

    # Constraint: fuel must remain positive after burn
    def fuel_constraint(dv):
        dm = tsiolkovsky_dm(wet_mass, np.linalg.norm(dv))
        return fuel_kg - dm - 0.5  # keep 0.5 kg margin

    constraints = [
        {'type': 'ineq', 'fun': miss_constraint},
        {'type': 'ineq', 'fun': fuel_constraint},
    ]
    bounds = [(-MAX_DV_KM_S, MAX_DV_KM_S)] * 3

    # Initial guess: prograde bias
    x0 = np.array([0.0, 0.003, 0.0])

    try:
        result = minimize(
            fun=objective,
            x0=x0,
            method='SLSQP',
            bounds=bounds,
            constraints=constraints,
            options={'ftol': 1e-8, 'maxiter': 200}
        )
        if result.success and np.linalg.norm(result.x) <= MAX_DV_KM_S:
            dv_rtn = result.x
            is_fallback = False
        else:
            raise ValueError("SLSQP failed")
    except Exception:
        # Fallback: maximum prograde burn
        dv_rtn = np.array([0.0, MAX_DV_KM_S, 0.0])
        is_fallback = True

    dv_eci = dv_rtn_to_eci(dv_rtn, sat_state)
    dv_mag = float(np.linalg.norm(dv_eci))
    return dv_eci, dv_mag, is_fallback


def compute_recovery_burns(sat_state_post_evasion: np.ndarray,
                            nominal_state: np.ndarray,
                            sat_id: str) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Two-burn Hohmann phasing transfer from current orbit to nominal slot.
    Returns (dv1_eci, dv2_eci, transfer_time_s)
    Both burns are prograde (T-direction in RTN).
    """
    r_c = float(np.linalg.norm(sat_state_post_evasion[:3]))  # current radius
    r_n = float(np.linalg.norm(nominal_state[:3]))            # nominal radius

    # Clamp to reasonable range
    r_c = max(r_c, 6500.0)
    r_n = max(r_n, 6500.0)

    a_t = (r_c + r_n) / 2.0  # transfer ellipse semi-major axis

    v_circ_c = np.sqrt(MU / r_c)
    v_circ_n = np.sqrt(MU / r_n)
    v_peri = np.sqrt(MU * (2.0/r_c - 1.0/a_t))
    v_apo = np.sqrt(MU * (2.0/r_n - 1.0/a_t))

    dv1_mag = abs(v_peri - v_circ_c)  # prograde at current orbit
    dv2_mag = abs(v_circ_n - v_apo)   # circularize at nominal orbit

    transfer_time_s = np.pi * np.sqrt(a_t**3 / MU)

    # Convert to ECI via RTN (both prograde → T direction)
    dv1_rtn = np.array([0.0, dv1_mag, 0.0])
    dv2_rtn = np.array([0.0, dv2_mag, 0.0])

    # Post-burn state for burn 2 (approximate — propagate to apogee)
    sat_after_burn1 = sat_state_post_evasion.copy()
    dv1_eci = dv_rtn_to_eci(dv1_rtn, sat_state_post_evasion)
    sat_after_burn1[3:6] += dv1_eci

    # Propagate to apogee for burn 2 RTN frame
    sat_at_apogee = propagate(sat_after_burn1.reshape(1, 6), transfer_time_s, step=30.0)[0]
    dv2_eci = dv_rtn_to_eci(dv2_rtn, sat_at_apogee)

    return dv1_eci, dv2_eci, transfer_time_s
