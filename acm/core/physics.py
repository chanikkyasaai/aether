"""
AETHER Physics Engine
RK4+J2 propagator, Tsiolkovsky rocket equation, Numba JIT
"""
import numpy as np
from numba import njit, prange

# Physical constants - exact values from WGS84
MU = 398600.4418          # km³/s² — Earth gravitational parameter
RE = 6378.137             # km — Earth equatorial radius (WGS84)
J2 = 1.08263e-3           # dimensionless — second zonal harmonic (WGS84)
G0 = 9.80665e-3           # km/s² — standard gravity in km/s units
ISP = 300.0               # s — monopropellant specific impulse
M_DRY = 500.0             # kg — satellite dry mass
M_FUEL_INIT = 50.0        # kg — initial propellant per satellite
CONSTELLATION_ALT_KM = 550.0   # km — nominal operating altitude
GRAVEYARD_ALT_KM = 600.0       # km — EOL target (IADC compliant)


@njit(nopython=True, parallel=True, cache=True, nogil=True)
def _batch_derivatives(states: np.ndarray) -> np.ndarray:
    """
    Compute J2-perturbed gravitational accelerations for N objects.
    states: (N, 6) array [x, y, z, vx, vy, vz] in km and km/s
    returns: (N, 6) derivatives [vx, vy, vz, ax, ay, az]
    """
    N = states.shape[0]
    derivs = np.zeros_like(states)
    for i in prange(N):
        x = states[i, 0]
        y = states[i, 1]
        z = states[i, 2]
        vx = states[i, 3]
        vy = states[i, 4]
        vz = states[i, 5]

        r2 = x*x + y*y + z*z
        r = np.sqrt(r2)
        r3 = r2 * r
        r5 = r3 * r2

        # Two-body acceleration
        mu_r3 = MU / r3
        ax = -mu_r3 * x
        ay = -mu_r3 * y
        az = -mu_r3 * z

        # J2 perturbation
        j2_factor = 1.5 * J2 * MU * RE * RE / r5
        z2_r2 = 5.0 * z * z / r2
        ax += j2_factor * x * (z2_r2 - 1.0)
        ay += j2_factor * y * (z2_r2 - 1.0)
        az += j2_factor * z * (z2_r2 - 3.0)

        derivs[i, 0] = vx
        derivs[i, 1] = vy
        derivs[i, 2] = vz
        derivs[i, 3] = ax
        derivs[i, 4] = ay
        derivs[i, 5] = az

    return derivs


def rk4_batch(states: np.ndarray, dt: float) -> np.ndarray:
    """
    Single RK4 step for N objects.
    states: (N, 6) float64 array
    dt: time step in seconds
    returns: (N, 6) propagated states
    """
    k1 = _batch_derivatives(states)
    k2 = _batch_derivatives(states + 0.5 * dt * k1)
    k3 = _batch_derivatives(states + 0.5 * dt * k2)
    k4 = _batch_derivatives(states + dt * k3)
    return states + (dt / 6.0) * (k1 + 2.0*k2 + 2.0*k3 + k4)


def propagate(states: np.ndarray, total_dt: float, step: float = 30.0) -> np.ndarray:
    """
    Multi-step propagation of N objects.
    states: (N, 6) float64
    total_dt: total time to propagate (seconds)
    step: sub-step size (default 30s for 550km LEO)
    returns: (N, 6) final states
    """
    current = states.copy()
    remaining = total_dt
    while remaining > 1e-9:
        dt = min(step, remaining)
        current = rk4_batch(current, dt)
        remaining -= dt
    return current


def tsiolkovsky_dm(m_current_kg: float, dv_km_s: float, isp: float = ISP) -> float:
    """
    Compute fuel consumed (kg) for a given delta-V maneuver.
    m_current_kg: current wet mass (dry + fuel) in kg
    dv_km_s: delta-V magnitude in km/s
    Returns: mass consumed in kg (positive number)
    """
    if dv_km_s <= 0.0:
        return 0.0
    dm = m_current_kg * (1.0 - np.exp(-dv_km_s / (isp * G0)))
    return max(0.0, dm)


@njit(cache=True, nogil=True)
def _serial_derivatives(states: np.ndarray) -> np.ndarray:
    """
    J2-perturbed derivatives for N objects — serial loop, NO prange.
    Faster than _batch_derivatives for small N (N ≤ ~1000) because it avoids
    Numba thread-pool synchronization overhead (~2ms per call for small N).
    """
    N = states.shape[0]
    derivs = np.zeros_like(states)
    for i in range(N):
        x = states[i, 0]
        y = states[i, 1]
        z = states[i, 2]
        vx = states[i, 3]
        vy = states[i, 4]
        vz = states[i, 5]

        r2 = x*x + y*y + z*z
        r = r2 ** 0.5
        r3 = r2 * r
        r5 = r3 * r2

        mu_r3 = MU / r3
        ax = -mu_r3 * x
        ay = -mu_r3 * y
        az = -mu_r3 * z

        j2_factor = 1.5 * J2 * MU * RE * RE / r5
        z2_r2 = 5.0 * z * z / r2
        ax += j2_factor * x * (z2_r2 - 1.0)
        ay += j2_factor * y * (z2_r2 - 1.0)
        az += j2_factor * z * (z2_r2 - 3.0)

        derivs[i, 0] = vx
        derivs[i, 1] = vy
        derivs[i, 2] = vz
        derivs[i, 3] = ax
        derivs[i, 4] = ay
        derivs[i, 5] = az

    return derivs


@njit(cache=True, nogil=True)
def rk4_serial(states: np.ndarray, dt: float) -> np.ndarray:
    """Single RK4 step — serial, no thread overhead. Use for small N."""
    k1 = _serial_derivatives(states)
    k2 = _serial_derivatives(states + 0.5 * dt * k1)
    k3 = _serial_derivatives(states + 0.5 * dt * k2)
    k4 = _serial_derivatives(states + dt * k3)
    return states + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


def propagate_serial(states: np.ndarray, total_dt: float, step: float = 30.0) -> np.ndarray:
    """
    Multi-step serial propagation.  No Numba prange — optimal for N ≤ ~1000.
    """
    current = states.copy()
    remaining = total_dt
    while remaining > 1e-9:
        dt = min(step, remaining)
        current = rk4_serial(current, dt)
        remaining -= dt
    return current


# Threshold below which serial propagation is faster than parallel
_PARALLEL_THRESHOLD = 1000


def propagate_smart(states: np.ndarray, total_dt: float, step: float = 30.0) -> np.ndarray:
    """Dispatch to serial or parallel propagator based on N."""
    if states.shape[0] <= _PARALLEL_THRESHOLD:
        return propagate_serial(states, total_dt, step)
    return propagate(states, total_dt, step)


def warmup():
    """Trigger Numba JIT compilation. Call at startup before accepting requests."""
    dummy = np.zeros((2, 6), dtype=np.float64)
    dummy[0] = [RE + CONSTELLATION_ALT_KM, 0, 0, 0, 7.784, 0]
    dummy[1] = [RE + CONSTELLATION_ALT_KM + 10, 0, 0, 0, 7.78, 0]
    rk4_batch(dummy, 1.0)
    rk4_batch(dummy, 30.0)
    # Also warm up the serial path
    rk4_serial(dummy, 1.0)
    rk4_serial(dummy, 30.0)
