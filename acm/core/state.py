"""
AETHER SimState — single source of truth for all runtime state.
All modules read/write through a threading.Lock().
"""
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import numpy as np


@dataclass
class ScheduledBurn:
    satellite_id: str
    burn_id: str           # e.g. "EVASION_SAT04_001"
    burn_time_s: float     # simulation seconds from epoch
    dv_eci_km_s: np.ndarray  # shape (3,) delta-V in ECI frame (km/s)
    burn_type: str         # "EVASION" | "RECOVERY_1" | "RECOVERY_2"


@dataclass
class CDM:
    sat_id: str
    deb_id: str
    tca_offset_s: float    # seconds from now to TCA
    miss_distance_km: float
    rel_velocity_km_s: float
    poc: float             # Probability of Collision (Akella-Alfriend)
    threat_level: str      # "CRITICAL" | "WARNING"
    approach_azimuth_deg: float = 0.0  # for bullseye plot


class SimState:
    """
    Central in-memory simulation state.
    All access must acquire sim_lock first.
    """
    def __init__(self):
        self.sim_lock = threading.Lock()

        # Satellite arrays
        self.sat_states: np.ndarray = np.zeros((0, 6), dtype=np.float64)
        self.sat_ids: List[str] = []
        self.sat_fuel_kg: np.ndarray = np.zeros(0, dtype=np.float64)
        self.sat_nominal_states: np.ndarray = np.zeros((0, 6), dtype=np.float64)
        self.sat_last_burn_time: np.ndarray = np.zeros(0, dtype=np.float64)
        self.sat_status: List[str] = []  # NOMINAL | EVADING | RECOVERING | EOL

        # Debris arrays
        self.deb_states: np.ndarray = np.zeros((0, 6), dtype=np.float64)
        self.deb_ids: List[str] = []

        # Time
        self.current_time_s: float = 0.0
        self.initial_epoch: Optional[datetime] = None

        # Planning state
        self.active_cdms: List[CDM] = []
        self.maneuver_queue: List[ScheduledBurn] = []

        # Counters
        self.collision_count: int = 0
        self.maneuvers_executed: int = 0

        # Pre-computed caches — rebuilt after each step/telemetry ingest
        self._debris_cloud_cache: list = []   # [[id, lat, lon, alt_km], ...]
        self._snapshot_json_cache: bytes = b''  # full snapshot pre-serialized to JSON bytes

        # Burn ID counter per satellite
        self._burn_counters: dict = {}

    def next_burn_id(self, satellite_id: str, burn_type: str) -> str:
        key = f"{burn_type}_{satellite_id}"
        self._burn_counters[key] = self._burn_counters.get(key, 0) + 1
        sat_short = satellite_id.replace("-", "").replace(" ", "")
        return f"{burn_type}_{sat_short}_{self._burn_counters[key]:03d}"

    def get_sat_index(self, sat_id: str) -> int:
        try:
            return self.sat_ids.index(sat_id)
        except ValueError:
            return -1

    def get_deb_index(self, deb_id: str) -> int:
        try:
            return self.deb_ids.index(deb_id)
        except ValueError:
            return -1

    def rebuild_debris_cache(self):
        """
        Vectorized ECI → geodetic for all debris. Call after each propagation step.
        Snapshot reads this pre-computed list — O(1) per snapshot call.
        """
        from acm.core.physics import RE as _RE
        if self.deb_states.shape[0] == 0:
            self._debris_cloud_cache = []
        else:
            pos = self.deb_states[:, :3]
            r = np.linalg.norm(pos, axis=1)
            lat = np.degrees(np.arcsin(pos[:, 2] / r))
            lon = np.degrees(np.arctan2(pos[:, 1], pos[:, 0]))
            alt = r - _RE
            lat_r = np.round(lat, 4)
            lon_r = np.round(lon, 4)
            alt_r = np.round(alt, 2)
            floats = np.column_stack([lat_r, lon_r, alt_r]).tolist()
            self._debris_cloud_cache = [[self.deb_ids[i]] + floats[i] for i in range(len(self.deb_ids))]
        # Always rebuild the full JSON cache
        from acm.api.routes_viz import build_snapshot_cache
        self._snapshot_json_cache = build_snapshot_cache(self)

    def wet_mass_kg(self, sat_idx: int) -> float:
        from acm.core.physics import M_DRY
        return M_DRY + float(self.sat_fuel_kg[sat_idx])


# Global singleton — imported by all modules
sim_state = SimState()
sim_lock = sim_state.sim_lock
