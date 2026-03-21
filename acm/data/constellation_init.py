"""
AETHER Constellation Initialization
50 satellites at 550 km circular orbit, equally spaced in 5 orbital planes.
DEMO_MODE=True uses fixed seed for reproducible conjunction events.
"""
import numpy as np
from acm.core.physics import RE, MU, CONSTELLATION_ALT_KM

DEMO_MODE = True
DEMO_SEED = 42

N_PLANES = 5
SATS_PER_PLANE = 10
ALT_KM = CONSTELLATION_ALT_KM
INCLINATION_DEG = 53.0  # ISS-like inclination

# Debris field parameters
N_DEBRIS = 10000
DEB_ALT_MIN = 450.0
DEB_ALT_MAX = 650.0


def _circular_state(alt_km: float, inc_deg: float, raan_deg: float, nu_deg: float) -> np.ndarray:
    """
    Generate ECI state vector for circular orbit.
    alt_km: altitude above Earth surface
    inc_deg: inclination (degrees)
    raan_deg: right ascension of ascending node (degrees)
    nu_deg: true anomaly (degrees)
    """
    r = RE + alt_km
    v_circ = np.sqrt(MU / r)

    inc = np.radians(inc_deg)
    raan = np.radians(raan_deg)
    nu = np.radians(nu_deg)

    # Position in orbital plane
    x_orb = r * np.cos(nu)
    y_orb = r * np.sin(nu)

    # Velocity in orbital plane (circular, prograde)
    vx_orb = -v_circ * np.sin(nu)
    vy_orb = v_circ * np.cos(nu)

    # Rotation matrices: R3(-RAAN) * R1(-inc) * R3(-argp=0)
    cos_r, sin_r = np.cos(raan), np.sin(raan)
    cos_i, sin_i = np.cos(inc), np.sin(inc)

    # ECI position
    x = cos_r * x_orb - sin_r * cos_i * y_orb
    y = sin_r * x_orb + cos_r * cos_i * y_orb
    z = sin_i * y_orb

    # ECI velocity
    vx = cos_r * vx_orb - sin_r * cos_i * vy_orb
    vy = sin_r * vx_orb + cos_r * cos_i * vy_orb
    vz = sin_i * vy_orb

    return np.array([x, y, z, vx, vy, vz], dtype=np.float64)


def generate_constellation():
    """
    Generate 50 satellite ECI states and IDs.
    Returns (sat_ids, sat_states)
    """
    rng = np.random.default_rng(DEMO_SEED if DEMO_MODE else None)
    sat_ids = []
    sat_states = []

    plane_names = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon']

    for plane_idx in range(N_PLANES):
        raan = plane_idx * (360.0 / N_PLANES)
        for sat_idx in range(SATS_PER_PLANE):
            nu = sat_idx * (360.0 / SATS_PER_PLANE)
            # Small altitude variation ±5 km within plane
            alt_variation = rng.uniform(-5.0, 5.0) if not DEMO_MODE else 0.0
            state = _circular_state(ALT_KM + alt_variation, INCLINATION_DEG, raan, nu)
            sat_id = f"SAT-{plane_names[plane_idx]}-{sat_idx+1:02d}"
            sat_ids.append(sat_id)
            sat_states.append(state)

    return sat_ids, np.array(sat_states, dtype=np.float64)


def generate_debris_field(n_debris: int = N_DEBRIS):
    """
    Generate a realistic debris field around LEO.
    Returns (deb_ids, deb_states)
    """
    rng = np.random.default_rng(DEMO_SEED if DEMO_MODE else None)
    deb_ids = []
    deb_states = []

    for i in range(n_debris):
        alt = rng.uniform(DEB_ALT_MIN, DEB_ALT_MAX)
        inc = rng.uniform(0.0, 98.0)  # various inclinations
        raan = rng.uniform(0.0, 360.0)
        nu = rng.uniform(0.0, 360.0)

        state = _circular_state(alt, inc, raan, nu)
        # Add small eccentricity-like perturbations
        state[3:6] += rng.normal(0, 0.01, 3)  # small velocity noise

        deb_id = f"DEB-{i+1:05d}"
        deb_ids.append(deb_id)
        deb_states.append(state)

    return deb_ids, np.array(deb_states, dtype=np.float64)
