"""
AETHER Test Infrastructure
Shared fixtures, helpers, and orbital mechanics utilities.
All tests import from here.
"""
import os
import time
import math
import numpy as np
import pytest
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# â”€â”€ Physical constants (exact spec values) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
RE   = 6378.137        # km â€” Earth equatorial radius (WGS84)
MU   = 398600.4418     # kmÂ³/sÂ² â€” Earth gravitational parameter
J2   = 1.08263e-3      # dimensionless â€” second zonal harmonic
G0   = 0.00980665      # km/sÂ² â€” standard gravity
ISP  = 300.0           # s â€” monopropellant specific impulse
M_DRY      = 500.0     # kg
M_FUEL     = 50.0      # kg
M_WET      = M_DRY + M_FUEL  # 550.0 kg

CONSTELLATION_ALT = 550.0   # km
GRAVEYARD_ALT     = 600.0   # km
MAX_DV            = 0.015   # km/s (15 m/s)
COOLDOWN_S        = 600.0   # s
LATENCY_S         = 10.0    # s
COLLISION_KM      = 0.100   # km (100 m)
SLOT_BOX_KM       = 10.0    # km

BASE_URL   = os.environ.get("AETHER_URL", "http://localhost:8000")
TEST_EPOCH = "2026-03-12T08:00:00.000Z"


# â”€â”€ Server session â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.fixture(scope="session")
def session():
    """Requests session â€” kept alive for whole test session."""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    # Verify server is up
    try:
        r = s.get(f"{BASE_URL}/api/status", timeout=10)
        assert r.status_code == 200, f"Server not ready: {r.status_code}"
    except Exception as e:
        pytest.skip(f"Server not reachable at {BASE_URL}: {e}")
    return s


@pytest.fixture(autouse=True)
def reset_state(session):
    """Reset sim state before every test. Requires TEST_MODE=1 on server."""
    r = session.post(f"{BASE_URL}/api/reset", timeout=10)
    if r.status_code == 403:
        pytest.skip("Server not in TEST_MODE â€” set TEST_MODE=1 env var")
    assert r.status_code == 200, f"Reset failed: {r.status_code} {r.text}"
    yield


# â”€â”€ Orbital mechanics helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def circular_orbit_state(alt_km: float, true_anomaly_deg: float = 0.0,
                          inclination_deg: float = 53.0,
                          raan_deg: float = 0.0) -> np.ndarray:
    """
    Returns [x,y,z,vx,vy,vz] ECI state vector for circular orbit.
    Matches the physics.py constants exactly.
    """
    r = RE + alt_km
    v = math.sqrt(MU / r)

    inc  = math.radians(inclination_deg)
    raan = math.radians(raan_deg)
    nu   = math.radians(true_anomaly_deg)

    # Position in perifocal frame
    x_pf  =  r * math.cos(nu)
    y_pf  =  r * math.sin(nu)
    vx_pf = -v * math.sin(nu)
    vy_pf =  v * math.cos(nu)

    # Rotate: R3(-raan) * R1(-inc)
    cos_r, sin_r = math.cos(raan), math.sin(raan)
    cos_i, sin_i = math.cos(inc),  math.sin(inc)

    x  =  cos_r * x_pf  - sin_r * cos_i * y_pf
    y  =  sin_r * x_pf  + cos_r * cos_i * y_pf
    z  =  sin_i * y_pf

    vx =  cos_r * vx_pf - sin_r * cos_i * vy_pf
    vy =  sin_r * vx_pf + cos_r * cos_i * vy_pf
    vz =  sin_i * vy_pf

    return np.array([x, y, z, vx, vy, vz], dtype=np.float64)


def tsiolkovsky_dm(m_current_kg: float, dv_km_s: float) -> float:
    """Fuel consumed (kg) for given delta-V."""
    if dv_km_s <= 0:
        return 0.0
    return m_current_kg * (1.0 - math.exp(-dv_km_s / (ISP * G0)))


def converging_debris(sat_state: np.ndarray, miss_km: float,
                       tca_seconds: float,
                       rel_speed_km_s: float = 7.5) -> np.ndarray:
    """
    Creates a debris state that passes the satellite at miss_km in tca_seconds.
    The debris approaches head-on (retrograde direction) with a lateral offset
    to produce the specified miss distance at TCA.

    Strategy:
      - Place debris ahead of satellite by rel_speed * tca_s along the track
      - Add a lateral offset (radial direction) equal to miss_km
      - Set debris velocity as exact retrograde relative to satellite
    """
    sat_pos = sat_state[:3].copy()
    sat_vel = sat_state[3:6].copy()

    # Unit vectors in RTN frame
    r_hat = sat_pos / np.linalg.norm(sat_pos)
    n_cross = np.cross(sat_pos, sat_vel)
    n_hat = n_cross / np.linalg.norm(n_cross)
    t_hat = np.cross(n_hat, r_hat)

    # Place debris: along-track distance for TCA
    along_track = rel_speed_km_s * tca_seconds  # km ahead of satellite

    # Debris position: ahead in track + lateral offset for miss
    deb_pos = sat_pos + along_track * t_hat + miss_km * r_hat

    # Debris velocity: approaching (retrograde relative to satellite)
    # Relative velocity: debris moves toward satellite at rel_speed
    deb_vel = sat_vel - rel_speed_km_s * t_hat  # head-on approach

    return np.array([*deb_pos, *deb_vel], dtype=np.float64)


def diverging_debris(sat_state: np.ndarray, separation_km: float = 200.0) -> np.ndarray:
    """Creates debris that is moving away â€” should NOT trigger CDM."""
    sat_pos = sat_state[:3].copy()
    sat_vel = sat_state[3:6].copy()

    r_hat = sat_pos / np.linalg.norm(sat_pos)
    # Place debris in radial direction, moving away
    deb_pos = sat_pos + separation_km * r_hat
    deb_vel = sat_vel + 0.5 * r_hat  # moving radially outward

    return np.array([*deb_pos, *deb_vel], dtype=np.float64)


def state_to_obj(obj_id: str, obj_type: str, state: np.ndarray) -> dict:
    """Convert ECI state vector to telemetry object dict."""
    return {
        "id": obj_id,
        "type": obj_type,
        "r": {"x": float(state[0]), "y": float(state[1]), "z": float(state[2])},
        "v": {"x": float(state[3]), "y": float(state[4]), "z": float(state[5])}
    }


def make_telemetry_payload(objects: list, timestamp: str = TEST_EPOCH) -> dict:
    """Format exact JSON the grader sends."""
    return {"timestamp": timestamp, "objects": objects}


# â”€â”€ API helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def post_telemetry(session, objects: list, timestamp: str = TEST_EPOCH) -> requests.Response:
    payload = make_telemetry_payload(objects, timestamp)
    return session.post(f"{BASE_URL}/api/telemetry", json=payload, timeout=30)


def post_step(session, step_seconds: int = 60) -> requests.Response:
    return session.post(f"{BASE_URL}/api/simulate/step",
                        json={"step_seconds": step_seconds}, timeout=60)


def post_maneuver(session, sat_id: str, burns: list) -> requests.Response:
    """burns: list of {burn_id, burnTime (ISO), deltaV_vector: {x,y,z}}"""
    return session.post(f"{BASE_URL}/api/maneuver/schedule", json={
        "satelliteId": sat_id,
        "maneuver_sequence": burns
    }, timeout=30)


def get_snapshot(session) -> requests.Response:
    return session.get(f"{BASE_URL}/api/visualization/snapshot", timeout=10)


def get_status(session) -> requests.Response:
    return session.get(f"{BASE_URL}/api/status", timeout=10)


def reset(session) -> requests.Response:
    return session.post(f"{BASE_URL}/api/reset", timeout=10)


def load_constellation_50(session, n_debris: int = 100,
                            timestamp: str = TEST_EPOCH) -> dict:
    """
    Load 50 satellites in Walker Delta + N debris. Returns telemetry response.
    Used by performance and safety tests.
    """
    objects = []
    plane_names = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon']
    for plane_idx in range(5):
        raan = plane_idx * 72.0
        for sat_idx in range(10):
            nu = sat_idx * 36.0
            state = circular_orbit_state(CONSTELLATION_ALT, nu, 53.0, raan)
            sid = f"SAT-{plane_names[plane_idx]}-{sat_idx+1:02d}"
            objects.append(state_to_obj(sid, "SATELLITE", state))

    rng = np.random.default_rng(42)
    for i in range(n_debris):
        alt = rng.uniform(450, 650)
        nu  = rng.uniform(0, 360)
        inc = rng.uniform(0, 98)
        state = circular_orbit_state(alt, nu, inc, rng.uniform(0, 360))
        objects.append(state_to_obj(f"DEB-{i+1:05d}", "DEBRIS", state))

    r = post_telemetry(session, objects, timestamp)
    assert r.status_code == 200
    return r.json()


# â”€â”€ Assertion helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def assert_response_time(response_time_ms: float, limit_ms: float, label: str = ""):
    assert response_time_ms < limit_ms, (
        f"{label} latency {response_time_ms:.1f}ms exceeds {limit_ms}ms limit. "
        f"Check: Numba JIT not parallelized or KD-tree not used. "
        f"Requirement: Check physics.py @njit(parallel=True) and conjunction.py KDTree."
    )


def timed_post(session, url: str, json_body: dict) -> tuple:
    """Returns (response, elapsed_ms)."""
    t0 = time.perf_counter()
    r = session.post(url, json=json_body, timeout=60)
    ms = (time.perf_counter() - t0) * 1000
    return r, ms


def timed_get(session, url: str) -> tuple:
    t0 = time.perf_counter()
    r = session.get(url, timeout=10)
    ms = (time.perf_counter() - t0) * 1000
    return r, ms


