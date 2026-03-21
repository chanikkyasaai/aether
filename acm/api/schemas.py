"""
AETHER Pydantic v2 schemas for all API request/response models.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# --- Telemetry ---

class Vector3(BaseModel):
    x: float
    y: float
    z: float


class OrbitalObject(BaseModel):
    id: str
    type: str  # "SATELLITE" | "DEBRIS"
    r: Vector3
    v: Vector3


class TelemetryRequest(BaseModel):
    timestamp: str
    objects: List[OrbitalObject]


class TelemetryResponse(BaseModel):
    status: str = "ACK"
    processed_count: int
    active_cdm_warnings: int


# --- Simulate Step ---

class SimulateStepRequest(BaseModel):
    step_seconds: int = Field(default=60, ge=1, le=86400)


class SimulateStepResponse(BaseModel):
    status: str = "STEP_COMPLETE"
    new_timestamp: str
    collisions_detected: int
    maneuvers_executed: int


# --- Maneuver Schedule ---

class BurnVector(BaseModel):
    x: float
    y: float
    z: float


class BurnSequenceItem(BaseModel):
    burn_id: str
    burnTime: str  # ISO 8601
    deltaV_vector: BurnVector


class ManeuverScheduleRequest(BaseModel):
    satelliteId: str
    maneuver_sequence: List[BurnSequenceItem]


class ManeuverValidation(BaseModel):
    ground_station_los: bool
    sufficient_fuel: bool
    projected_mass_remaining_kg: float


class ManeuverScheduleResponse(BaseModel):
    status: str = "SCHEDULED"
    validation: ManeuverValidation


# --- Visualization Snapshot ---

class SatelliteSnapshot(BaseModel):
    id: str
    lat: float
    lon: float
    fuel_kg: float
    status: str


class SnapshotResponse(BaseModel):
    timestamp: str
    satellites: List[SatelliteSnapshot]
    debris_cloud: List[List[Any]]  # [[id, lat, lon, alt_km], ...]


# --- Status ---

class ActiveCDM(BaseModel):
    sat_id: str
    deb_id: str
    tca_offset_s: float
    miss_distance_km: float
    poc: float
    threat_level: str
    approach_azimuth_deg: float


class ScheduledBurnInfo(BaseModel):
    satellite_id: str
    burn_id: str
    burn_time_s: float
    burn_type: str
    dv_magnitude_m_s: float


class StatusResponse(BaseModel):
    system: str = "AETHER"
    sim_time_iso: str
    satellites_tracked: int
    debris_tracked: int
    active_cdm_warnings: int
    critical_conjunctions: int
    maneuvers_queued: int
    total_collisions: int
    fleet_fuel_remaining_kg: float
    recent_events: List[Dict[str, Any]]
    active_cdms: List[ActiveCDM] = []
    scheduled_burns: List[ScheduledBurnInfo] = []
