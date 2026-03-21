"""
AETHER Ground Station module
Line-of-sight elevation check and blackout window prediction.
"""
import numpy as np
import os
import csv
from typing import List, Optional, Tuple
from acm.core.physics import RE

# Default min elevation angles (degrees)
DEFAULT_MIN_ELEVATION = 5.0


class GroundStation:
    def __init__(self, gs_id: str, name: str, lat_deg: float, lon_deg: float,
                 alt_km: float = 0.0, min_elevation_deg: float = DEFAULT_MIN_ELEVATION):
        self.gs_id = gs_id
        self.name = name
        self.lat_rad = np.radians(lat_deg)
        self.lon_rad = np.radians(lon_deg)
        self.alt_km = alt_km
        self.min_elevation_deg = min_elevation_deg
        # ECEF position of ground station
        self.ecef = self._compute_ecef()

    def _compute_ecef(self) -> np.ndarray:
        r = RE + self.alt_km
        return np.array([
            r * np.cos(self.lat_rad) * np.cos(self.lon_rad),
            r * np.cos(self.lat_rad) * np.sin(self.lon_rad),
            r * np.sin(self.lat_rad)
        ])

    def elevation_deg(self, sat_pos_eci: np.ndarray) -> float:
        """
        Elevation angle (degrees) from this station to satellite.
        ECI ≈ ECEF approximation valid for LOS check.
        """
        range_vec = sat_pos_eci - self.ecef
        gs_unit = self.ecef / np.linalg.norm(self.ecef)
        range_norm = np.linalg.norm(range_vec)
        if range_norm < 1e-9:
            return 90.0
        sin_elev = np.dot(range_vec, gs_unit) / range_norm
        return np.degrees(np.arcsin(np.clip(sin_elev, -1.0, 1.0)))

    def has_los(self, sat_pos_eci: np.ndarray) -> bool:
        return self.elevation_deg(sat_pos_eci) >= self.min_elevation_deg


def load_ground_stations() -> List[GroundStation]:
    """Load ground stations from CSV file."""
    csv_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'ground_stations.csv')
    csv_path = os.path.abspath(csv_path)
    stations = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            stations.append(GroundStation(
                gs_id=row['id'],
                name=row['name'],
                lat_deg=float(row['lat_deg']),
                lon_deg=float(row['lon_deg']),
                alt_km=float(row.get('alt_km', 0.0)),
                min_elevation_deg=float(row.get('min_elevation_deg', DEFAULT_MIN_ELEVATION))
            ))
    return stations


# Global ground station list
GROUND_STATIONS: List[GroundStation] = []


def init_ground_stations():
    global GROUND_STATIONS
    GROUND_STATIONS = load_ground_stations()


def find_los_station(sat_pos_eci: np.ndarray) -> Optional[GroundStation]:
    """Return first ground station with LOS to satellite, or None."""
    for gs in GROUND_STATIONS:
        if gs.has_los(sat_pos_eci):
            return gs
    return None


def predict_next_los_window(sat_state: np.ndarray, earliest_time_s: float,
                             search_horizon_s: float = 7200.0) -> Optional[Tuple[float, str]]:
    """
    Predict next LOS window starting from earliest_time_s.
    Returns (window_start_s, station_id) or None if no window found in horizon.
    Propagates satellite forward in 60s steps.
    """
    from acm.core.physics import rk4_batch
    from acm.core.state import sim_state

    current_state = sat_state.copy().reshape(1, 6)
    elapsed = 0.0
    step = 60.0  # check every 60 seconds

    # Fast-forward to earliest_time_s from current sim time
    lead_time = max(0.0, earliest_time_s - sim_state.current_time_s)
    if lead_time > 0:
        from acm.core.physics import propagate
        current_state = propagate(current_state, lead_time, step=30.0)

    while elapsed < search_horizon_s:
        sat_pos = current_state[0, :3]
        for gs in GROUND_STATIONS:
            if gs.has_los(sat_pos):
                return (earliest_time_s + elapsed, gs.gs_id)
        current_state = rk4_batch(current_state, step)
        elapsed += step

    return None
