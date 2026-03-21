"""
AETHER Conjunction Assessment Module
KD-tree coarse filter → vectorized batch TCA sweep → golden-section refine → Akella-Alfriend PoC

Performance architecture:
  All candidate pairs are packed as (2K, 6) and propagated in a SINGLE rk4_serial call
  per coarse time step.  This collapses K×T Python dispatch calls down to T calls,
  where T = TCA_HORIZON_S / TCA_COARSE_STEP_S (288 for 24h at 300s steps).
"""
import numpy as np
from scipy.spatial import KDTree
from scipy.optimize import minimize_scalar
from typing import List, Tuple
from acm.core.state import SimState, CDM
from acm.core.physics import propagate, propagate_serial, rk4_serial, RE, MU, J2

# Thresholds
COARSE_RADIUS_KM = 200.0       # KD-tree initial filter radius
MAX_CANDIDATES_PER_SAT = 50    # Cap candidates to bound worst-case cost
DISCARD_MISS_KM = 5.0          # Discard if TCA miss > 5 km
CRITICAL_MISS_KM = 1.0         # CRITICAL threshold
WARNING_MISS_KM = 5.0          # WARNING threshold
TCA_COARSE_STEP_S = 300.0      # 5-minute coarse sweep interval
TCA_HORIZON_S = 86400.0        # 24-hour look-ahead (spec requirement)
SAT_SIZE_M = 1.5               # satellite hard-body radius (m)
DEB_SIZE_M = 0.5               # debris hard-body radius (m)



def _akella_alfriend_poc(miss_km: float, rel_vel_km_s: float) -> float:
    """
    Akella-Alfriend (2000) short-encounter probability of collision.
    """
    r_hb = (SAT_SIZE_M + DEB_SIZE_M) / 1000.0  # combined hard-body radius in km
    sigma = max(miss_km * 0.1, 0.010)           # 10% uncertainty floor, min 10m
    poc = (r_hb**2 / (2.0 * sigma**2)) * np.exp(-miss_km**2 / (2.0 * sigma**2))
    return float(poc)


def _compute_approach_azimuth(sat_state: np.ndarray, deb_state: np.ndarray) -> float:
    """
    Compute approach azimuth of debris relative to satellite in RTN frame.
    Returns angle in degrees [0, 360).
    """
    r = sat_state[:3]
    v = sat_state[3:6]
    r_hat = r / np.linalg.norm(r)
    n_hat = np.cross(r, v)
    n_norm = np.linalg.norm(n_hat)
    if n_norm < 1e-9:
        return 0.0
    n_hat = n_hat / n_norm
    t_hat = np.cross(n_hat, r_hat)
    rel_pos = deb_state[:3] - sat_state[:3]
    t_comp = np.dot(rel_pos, t_hat)
    r_comp = np.dot(rel_pos, r_hat)
    azimuth = np.degrees(np.arctan2(t_comp, r_comp)) % 360.0
    return float(azimuth)


def _refine_tca(combined_at_tlo: np.ndarray, t_lo: float, t_hi: float,
                orig_sat_state: np.ndarray) -> Tuple[float, float, float]:
    """
    Golden-section refinement of TCA within bracket [t_lo, t_hi].

    combined_at_tlo: (2, 6) state of the pair AT time t_lo (from coarse sweep snapshot).
    Refinement propagates within [0, TCA_COARSE_STEP_S] from this saved state —
    never more than 300s per evaluation regardless of when t_lo occurs in 24h horizon.

    Returns (tca_s, miss_km, rel_velocity_km_s).  tca_s is absolute sim time.
    """
    bracket = float(t_hi - t_lo)  # always == TCA_COARSE_STEP_S or 0

    def miss_func(dt: float) -> float:
        propagated = propagate_serial(combined_at_tlo, dt, step=30.0)
        return float(np.linalg.norm(propagated[0, :3] - propagated[1, :3]))

    if bracket > 0:
        result = minimize_scalar(
            miss_func,
            bounds=(0.0, bracket),
            method='bounded',
            options={'xatol': 1.0}
        )
        tca_s = t_lo + float(result.x)
        miss_km = float(result.fun)
    else:
        tca_s = t_lo
        miss_km = float(np.linalg.norm(combined_at_tlo[0, :3] - combined_at_tlo[1, :3]))

    rel_vel = float(np.linalg.norm(orig_sat_state[3:6] - combined_at_tlo[1, 3:6]))
    return tca_s, miss_km, rel_vel


def _vectorized_batch_tca(
    pairs: List[Tuple[int, int]],
    sat_states: np.ndarray,
    deb_states: np.ndarray
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Vectorized coarse TCA sweep over ALL candidate pairs simultaneously.

    Packs K pairs as (2K, 6): even rows = satellites, odd rows = debris.
    Each coarse time step calls rk4_serial() ONCE on the full (2K, 6) batch.

    Crucially: saves the pair states AT each pair's t_lo moment so golden-section
    refinement propagates within [0, TCA_COARSE_STEP_S] from that saved state —
    bounding refinement cost regardless of where in the 24h horizon TCA occurs.

    Returns:
        min_miss_km:    (K,) minimum coarse miss distance per pair
        t_lo:           (K,) lower bracket bound (absolute sim time)
        t_hi:           (K,) upper bracket bound (absolute sim time)
        states_at_tlo:  (K, 2, 6) pair states at t_lo for each pair
    """
    K = len(pairs)
    if K == 0:
        empty = np.array([])
        return empty, empty, empty, np.zeros((0, 2, 6))

    # Build (2K, 6) packed state array
    packed = np.zeros((2 * K, 6), dtype=np.float64)
    for i, (s_idx, d_idx) in enumerate(pairs):
        packed[2 * i]     = sat_states[s_idx]
        packed[2 * i + 1] = deb_states[d_idx]

    times = np.arange(0.0, TCA_HORIZON_S + TCA_COARSE_STEP_S, TCA_COARSE_STEP_S)

    min_miss = np.full(K, np.inf, dtype=np.float64)
    t_lo     = np.zeros(K, dtype=np.float64)
    t_hi     = np.full(K, TCA_COARSE_STEP_S, dtype=np.float64)

    # states_at_tlo[i] holds the (2, 6) state of pair i at the current best t_lo
    states_at_tlo = packed.reshape(K, 2, 6).copy()  # initial state (t=0)

    current = packed.copy()
    prev = packed.copy()  # state one step before current

    for i in range(1, len(times)):
        dt_step = times[i] - times[i - 1]
        prev = current.copy()
        # Single RK4 step per coarse interval — coarse accuracy; refinement uses step=30s
        current = rk4_serial(current, dt_step)

        sat_pos = current[0::2, :3]   # (K, 3)
        deb_pos = current[1::2, :3]   # (K, 3)
        miss = np.linalg.norm(sat_pos - deb_pos, axis=1)  # (K,)

        improved = miss < min_miss
        if improved.any():
            idx = np.where(improved)[0]
            t_lo[idx]     = times[i - 1]
            t_hi[idx]     = times[i]
            min_miss[idx] = miss[idx]
            # Save the state at the START of this bracket (prev, not current)
            for j in idx:
                states_at_tlo[j, 0] = prev[2 * j]
                states_at_tlo[j, 1] = prev[2 * j + 1]

    return min_miss, t_lo, t_hi, states_at_tlo


def screen_conjunctions(sim_state: SimState) -> List[CDM]:
    """
    Full conjunction screening pipeline:
    1. KD-tree coarse filter on debris positions
    2. Vectorized batch TCA sweep across ALL pairs in one propagation call per step
    3. Golden-section refinement only for pairs within coarse threshold
    4. Akella-Alfriend PoC computation
    Returns CDM list sorted by PoC descending.
    """
    if sim_state.sat_states.shape[0] == 0 or sim_state.deb_states.shape[0] == 0:
        return []

    # Phase 1: KD-tree coarse filter
    deb_positions = sim_state.deb_states[:, :3]
    tree = KDTree(deb_positions)
    sat_positions = sim_state.sat_states[:, :3]
    candidate_lists = tree.query_ball_point(sat_positions, r=COARSE_RADIUS_KM)

    # Collect all candidate pairs as (sat_idx, deb_idx)
    pairs: List[Tuple[int, int]] = []
    for sat_idx, deb_indices in enumerate(candidate_lists):
        if sim_state.sat_status[sat_idx] == 'EOL':
            continue
        for deb_idx in deb_indices[:MAX_CANDIDATES_PER_SAT]:
            pairs.append((sat_idx, deb_idx))

    if not pairs:
        return []

    # Phase 2: Vectorized batch coarse TCA sweep (single rk4_serial call per time step)
    min_miss, t_lo, t_hi, states_at_tlo = _vectorized_batch_tca(
        pairs, sim_state.sat_states, sim_state.deb_states
    )

    # Phase 3: Golden-section refinement — propagates within [0, 300s] from saved t_lo state
    cdms = []
    for i, (sat_idx, deb_idx) in enumerate(pairs):
        sat_state = sim_state.sat_states[sat_idx]
        sat_id    = sim_state.sat_ids[sat_idx]
        deb_id    = sim_state.deb_ids[deb_idx]

        try:
            tca_s, miss_km, rel_vel = _refine_tca(
                states_at_tlo[i], float(t_lo[i]), float(t_hi[i]), sat_state
            )
        except Exception:
            continue

        if miss_km > DISCARD_MISS_KM:
            continue

        poc = _akella_alfriend_poc(miss_km, rel_vel)
        threat_level = 'CRITICAL' if miss_km < CRITICAL_MISS_KM else 'WARNING'
        azimuth = _compute_approach_azimuth(sat_state, sim_state.deb_states[deb_idx])

        cdms.append(CDM(
            sat_id=sat_id,
            deb_id=deb_id,
            tca_offset_s=tca_s,
            miss_distance_km=miss_km,
            rel_velocity_km_s=rel_vel,
            poc=poc,
            threat_level=threat_level,
            approach_azimuth_deg=azimuth
        ))

    # Sort by PoC descending
    cdms.sort(key=lambda c: c.poc, reverse=True)
    return cdms
