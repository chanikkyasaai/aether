"""
AETHER Audit Logger
10 event types, JSONL format, in-memory buffer for /api/status
"""
import json
import os
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any
from collections import deque

LOG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'logs', 'acm_audit.jsonl')
LOG_PATH = os.path.abspath(LOG_PATH)

_log_lock = threading.Lock()
_recent_events: deque = deque(maxlen=100)  # in-memory buffer


def _write_event(event: Dict[str, Any]):
    """Write event to JSONL file and in-memory buffer."""
    with _log_lock:
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(event) + '\n')
        except Exception as e:
            print(f"[LOGGER ERROR] {e}")
        _recent_events.append(event)


def _base(event_type: str, sim_time_s: float) -> Dict[str, Any]:
    return {
        'timestamp_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        'sim_time_s': round(sim_time_s, 2),
        'event_type': event_type,
    }


def log_cdm_detected(sat_id: str, deb_id: str, tca_offset_s: float,
                      miss_km: float, poc: float, threat_level: str, sim_time_s: float):
    e = _base('CDM_DETECTED', sim_time_s)
    e.update({'sat_id': sat_id, 'deb_id': deb_id, 'tca_offset_s': round(tca_offset_s, 1),
               'miss_km': round(miss_km, 4), 'poc': round(poc, 8), 'threat_level': threat_level})
    _write_event(e)


def log_cdm_actioned(sat_id: str, deb_id: str, burn_id: str, burn_time: float,
                      dv_rtn: list, dv_magnitude_m_s: float, fuel_cost_kg: float,
                      los_station: str, projected_miss_km: float, sim_time_s: float):
    e = _base('CDM_ACTIONED', sim_time_s)
    e.update({'sat_id': sat_id, 'deb_id': deb_id, 'burn_id': burn_id,
               'burn_time': round(burn_time, 1), 'dv_rtn': [round(v, 6) for v in dv_rtn],
               'dv_magnitude_m_s': round(dv_magnitude_m_s, 4), 'fuel_cost_kg': round(fuel_cost_kg, 4),
               'los_station': los_station, 'projected_miss_km': round(projected_miss_km, 4)})
    _write_event(e)


def log_cdm_warning(sat_id: str, deb_id: str, tca_offset_s: float,
                     miss_km: float, poc: float, sim_time_s: float):
    e = _base('CDM_WARNING', sim_time_s)
    e.update({'sat_id': sat_id, 'deb_id': deb_id, 'tca_offset_s': round(tca_offset_s, 1),
               'miss_km': round(miss_km, 4), 'poc': round(poc, 8)})
    _write_event(e)


def log_burn_executed(burn_id: str, sat_id: str, actual_dv_eci: list,
                       mass_before_kg: float, mass_after_kg: float, sim_time_s: float):
    e = _base('BURN_EXECUTED', sim_time_s)
    e.update({'burn_id': burn_id, 'sat_id': sat_id,
               'actual_dv_eci': [round(v, 8) for v in actual_dv_eci],
               'mass_before_kg': round(mass_before_kg, 3), 'mass_after_kg': round(mass_after_kg, 3)})
    _write_event(e)


def log_recovery_scheduled(sat_id: str, burn1_time: float, burn2_time: float,
                             total_dv_m_s: float, transfer_time_s: float, sim_time_s: float):
    e = _base('RECOVERY_SCHEDULED', sim_time_s)
    e.update({'sat_id': sat_id, 'burn1_time': round(burn1_time, 1),
               'burn2_time': round(burn2_time, 1), 'total_dv_m_s': round(total_dv_m_s, 4),
               'transfer_time_s': round(transfer_time_s, 1)})
    _write_event(e)


def log_recovery_complete(sat_id: str, slot_error_km: float,
                           time_outside_slot_s: float, sim_time_s: float):
    e = _base('RECOVERY_COMPLETE', sim_time_s)
    e.update({'sat_id': sat_id, 'slot_error_km': round(slot_error_km, 4),
               'time_outside_slot_s': round(time_outside_slot_s, 1)})
    _write_event(e)


def log_collision_detected(sat_id: str, deb_id: str, miss_km: float, sim_time_s: float):
    e = _base('COLLISION_DETECTED', sim_time_s)
    e.update({'sat_id': sat_id, 'deb_id': deb_id, 'miss_km': round(miss_km, 6)})
    _write_event(e)


def log_eol_triggered(sat_id: str, fuel_remaining_kg: float,
                       graveyard_burn_dv: float, sim_time_s: float):
    e = _base('EOL_TRIGGERED', sim_time_s)
    e.update({'sat_id': sat_id, 'fuel_remaining_kg': round(fuel_remaining_kg, 4),
               'graveyard_burn_dv': round(graveyard_burn_dv, 6)})
    _write_event(e)


def log_blind_conjunction(sat_id: str, deb_id: str, tca_offset_s: float,
                           next_los_time_s: float, sim_time_s: float):
    e = _base('BLIND_CONJUNCTION', sim_time_s)
    e.update({'sat_id': sat_id, 'deb_id': deb_id,
               'tca_offset_s': round(tca_offset_s, 1), 'next_los_time_s': round(next_los_time_s, 1)})
    _write_event(e)


def log_degraded_avoidance(sat_id: str, deb_id: str, fallback_dv_m_s: float,
                            projected_miss_km: float, sim_time_s: float):
    e = _base('DEGRADED_AVOIDANCE', sim_time_s)
    e.update({'sat_id': sat_id, 'deb_id': deb_id,
               'fallback_dv_m_s': round(fallback_dv_m_s, 4),
               'projected_miss_km': round(projected_miss_km, 4)})
    _write_event(e)


def get_recent_events(n: int = 10) -> List[Dict[str, Any]]:
    """Return the last n events for /api/status."""
    events = list(_recent_events)
    return events[-n:]
