"""
AETHER Maneuver Module
RTN<->ECI transforms, SLSQP evasion optimization, Hohmann recovery burns.
"""
import numpy as np
from scipy.optimize import minimize
from typing import Tuple, Optional
from acm.core.physics import MU, tsiolkovsky_dm, M_DRY, propagate, propagate_serial
from acm.core.state import ScheduledBurn


MAX_DV_KM_S = 0.015     # 15 m/s thrust limit
STANDOFF_HIGH = 0.500   # km — nominal standoff (fuel > 50%)
STANDOFF_MED = 0.200    # km — reduced standoff (10-50% fuel)
EOL_FUEL_FRACTION = 0.10

# Initial guesses for multi-start SLSQP (RTN frame, km/s)
# Cover prograde, retrograde, radial, normal, and diagonal directions
_SLSQP_SEEDS = [
    np.array([0.0,  0.003,  0.0]),   # prograde (main)
    np.array([0.0, -0.003,  0.0]),   # retrograde
    np.array([0.003, 0.0,   0.0]),   # radial outward
    np.array([0.0,  0.003,  0.003]), # prograde + normal
    np.array([0.003, 0.003, 0.0]),   # radial + prograde
]


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
                     tca_s: float, step: float = 0.0) -> float:
    """Compute miss distance at TCA after applying dv_rtn to satellite.
    step=0 (auto): uses tca_s/40 capped at [30, 300]s — fast for large TCA times.
    step>0: exact value used (e.g. 30.0 for final accuracy check).
    """
    if step <= 0.0:
        # Auto step: exactly 40 RK4 sub-steps regardless of TCA duration.
        # No upper cap — for 24h TCA, step=2160s gives exactly 40 sub-steps.
        # Capping at 300s would give 288 sub-steps for 24h TCA.
        step = float(max(tca_s / 40.0, 30.0))
    dv_eci = dv_rtn_to_eci(dv_rtn, sat_state)
    new_sat = sat_state.copy()
    new_sat[3:6] += dv_eci
    combined = np.vstack([new_sat.reshape(1, 6), deb_state.reshape(1, 6)])
    # Use propagate_serial (no Numba thread-pool overhead) — critical for performance.
    # propagate() uses parallel prange which has ~400µs overhead per call for N=2.
    # propagate_serial uses serial range: ~5µs per call. 80× faster for small N.
    propagated = propagate_serial(combined, tca_s, step=step)
    return float(np.linalg.norm(propagated[0, :3] - propagated[1, :3]))


def compute_evasion_burn(sat_state: np.ndarray, deb_state: np.ndarray,
                          tca_s: float, fuel_kg: float,
                          sat_id: str) -> Tuple[np.ndarray, float, bool]:
    """
    Multi-start SLSQP constrained minimization for minimum-fuel evasion.
    Tries 5 RTN seed directions; picks the best feasible result.
    Falls back to maximum prograde only if all seeds fail.
    Returns (dv_eci_km_s, dv_magnitude_km_s, is_fallback)
    """
    wet_mass = M_DRY + fuel_kg
    fuel_fraction = fuel_kg / 50.0

    if fuel_fraction < EOL_FUEL_FRACTION:
        standoff = 0.100
    elif fuel_fraction < 0.5:
        standoff = STANDOFF_MED
    else:
        standoff = STANDOFF_HIGH

    def objective(dv): return float(np.linalg.norm(dv))

    # Auto-step constraint functions (≤40 RK4 steps per eval, capped 30–300s)
    def miss_constraint(dv):
        return _miss_after_burn(dv, sat_state, deb_state, tca_s, step=0.0) - standoff

    def fuel_constraint(dv):
        dm = tsiolkovsky_dm(wet_mass, float(np.linalg.norm(dv)))
        return fuel_kg - dm - 0.5

    constraints = [
        {'type': 'ineq', 'fun': miss_constraint},
        {'type': 'ineq', 'fun': fuel_constraint},
    ]
    bounds = [(-MAX_DV_KM_S, MAX_DV_KM_S)] * 3

    best_dv_rtn: Optional[np.ndarray] = None
    best_mag = np.inf

    for x0 in _SLSQP_SEEDS:
        try:
            result = minimize(
                fun=objective,
                x0=x0.copy(),
                method='SLSQP',
                bounds=bounds,
                constraints=constraints,
                options={'ftol': 1e-7, 'maxiter': 50}
            )
            if not result.success:
                continue
            dv_mag = float(np.linalg.norm(result.x))
            if dv_mag > MAX_DV_KM_S:
                continue
            # Verify constraint satisfied with finer step (10× more sub-steps than opt)
            verify_step = float(max(tca_s / 400.0, 30.0))
            actual_miss = _miss_after_burn(result.x, sat_state, deb_state, tca_s, step=verify_step)
            if actual_miss < standoff * 0.9:
                continue  # fast-step said OK but accurate check failed — skip
            if dv_mag < best_mag:
                best_mag = dv_mag
                best_dv_rtn = result.x.copy()
        except Exception:
            continue

    if best_dv_rtn is not None:
        dv_eci = dv_rtn_to_eci(best_dv_rtn, sat_state)
        return dv_eci, float(np.linalg.norm(dv_eci)), False

    # All seeds failed — fallback: prograde burn scaled to clear standoff
    # Try progressively larger prograde burns until standoff is met
    for dv_mag_try in [0.005, 0.008, 0.010, MAX_DV_KM_S]:
        dv_rtn_try = np.array([0.0, dv_mag_try, 0.0])
        miss = _miss_after_burn(dv_rtn_try, sat_state, deb_state, tca_s, step=0.0)
        if miss >= standoff * 0.5:  # accept partial standoff in fallback
            dv_eci = dv_rtn_to_eci(dv_rtn_try, sat_state)
            return dv_eci, float(np.linalg.norm(dv_eci)), True

    # Absolute fallback: max prograde
    dv_rtn = np.array([0.0, MAX_DV_KM_S, 0.0])
    dv_eci = dv_rtn_to_eci(dv_rtn, sat_state)
    return dv_eci, float(np.linalg.norm(dv_eci)), True


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
