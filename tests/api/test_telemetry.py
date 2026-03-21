"""
POST /api/telemetry â€” Contract tests matching grader specification.
Tests telemetry ingestion, schema validation, and state replacement behaviour.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    RE, CONSTELLATION_ALT, BASE_URL, TEST_EPOCH,
    circular_orbit_state, state_to_obj, make_telemetry_payload,
    post_telemetry, get_status, get_snapshot,
)


class TestTelemetryIngestion:

    def test_ingest_single_satellite(self, session, reset_state):
        """1 satellite â†’ HTTP 200, processed_count == 1."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        r = post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])
        assert r.status_code == 200, (
            f"Expected 200, got {r.status_code}: {r.text}. "
            "Requirement: POST /api/telemetry must return 200 for valid input."
        )
        body = r.json()
        assert body.get("processed_count") == 1, (
            f"Expected processed_count=1, got {body.get('processed_count')}. "
            "Requirement: routes_telemetry.py must count all accepted objects."
        )

    def test_ingest_single_debris(self, session, reset_state):
        """1 debris object â†’ HTTP 200, processed_count == 1."""
        state = circular_orbit_state(500.0)
        r = post_telemetry(session, [state_to_obj("DEB-00001", "DEBRIS", state)])
        assert r.status_code == 200, (
            f"Expected 200, got {r.status_code}. "
            "Requirement: DEBRIS objects must be accepted by /api/telemetry."
        )
        assert r.json().get("processed_count") == 1, (
            f"processed_count should be 1, got {r.json().get('processed_count')}."
        )

    def test_ingest_mixed_objects(self, session, reset_state):
        """3 satellites + 5 debris in one payload â†’ processed_count == 8."""
        objects = []
        for i in range(3):
            objects.append(state_to_obj(
                f"SAT-{i:02d}", "SATELLITE",
                circular_orbit_state(CONSTELLATION_ALT, i * 120)
            ))
        for i in range(5):
            objects.append(state_to_obj(
                f"DEB-{i:05d}", "DEBRIS",
                circular_orbit_state(480.0, i * 72)
            ))
        r = post_telemetry(session, objects)
        assert r.status_code == 200
        assert r.json().get("processed_count") == 8, (
            f"Expected 8 (3 sats + 5 debris), got {r.json().get('processed_count')}. "
            "Requirement: Count both SATELLITE and DEBRIS objects in processed_count."
        )

    def test_response_schema(self, session, reset_state):
        """Response must contain status='ACK', processed_count (int), active_cdm_warnings (int)."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        r = post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") == "ACK", (
            f"status must be 'ACK', got '{body.get('status')}'. "
            "Requirement: Return {{'status': 'ACK', ...}} from POST /api/telemetry."
        )
        assert isinstance(body.get("processed_count"), int), (
            f"processed_count must be an int, got {type(body.get('processed_count'))}."
        )
        assert isinstance(body.get("active_cdm_warnings"), int), (
            f"active_cdm_warnings must be an int, got {type(body.get('active_cdm_warnings'))}. "
            "Requirement: Include active_cdm_warnings in telemetry response."
        )

    def test_empty_objects_list(self, session, reset_state):
        """Empty objects list â†’ 200 OK (or 422 if server requires non-empty)."""
        payload = make_telemetry_payload([])
        r = session.post(f"{BASE_URL}/api/telemetry", json=payload, timeout=10)
        assert r.status_code in (200, 422), (
            f"Empty object list should return 200 or 422, got {r.status_code}."
        )

    def test_invalid_type_rejected(self, session, reset_state):
        """Object with type='ASTEROID' must be rejected with 422 Unprocessable Entity."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        obj = state_to_obj("AST-001", "ASTEROID", state)
        payload = make_telemetry_payload([obj])
        r = session.post(f"{BASE_URL}/api/telemetry", json=payload, timeout=10)
        assert r.status_code == 422, (
            f"Expected 422 for unknown object type 'ASTEROID', got {r.status_code}. "
            "Requirement: Validate object type is one of SATELLITE, DEBRIS in telemetry schema."
        )

    def test_50_satellites_ingested(self, session, reset_state):
        """50 satellites in one payload â†’ processed_count=50 and satellites_tracked=50."""
        objects = []
        for i in range(50):
            objects.append(state_to_obj(
                f"SAT-{i:02d}", "SATELLITE",
                circular_orbit_state(CONSTELLATION_ALT, i * 7.2)
            ))
        r = post_telemetry(session, objects)
        assert r.status_code == 200
        assert r.json().get("processed_count") == 50, (
            f"Expected processed_count=50, got {r.json().get('processed_count')}."
        )
        status = get_status(session).json()
        assert status.get("satellites_tracked") == 50, (
            f"Expected satellites_tracked=50, got {status.get('satellites_tracked')}. "
            "Requirement: Ensure all 50 objects stored in state after telemetry call."
        )

    def test_telemetry_upsert_semantics(self, session, reset_state):
        """
        Spec: /api/telemetry uses upsert semantics.
        New IDs are appended. Existing IDs overwrite state, preserve fuel/status.
        Sending 2 sats then 3 different sats â†’ 5 total (not 3).
        """
        objects_2 = [
            state_to_obj(f"SAT-A{i}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 180))
            for i in range(2)
        ]
        post_telemetry(session, objects_2)
        assert get_status(session).json()["satellites_tracked"] == 2

        objects_3 = [
            state_to_obj(f"SAT-B{i}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 120))
            for i in range(3)
        ]
        post_telemetry(session, objects_3)
        tracked = get_status(session).json()["satellites_tracked"]
        assert tracked == 5, (
            f"Expected 5 satellites after upserting 3 new IDs into existing 2, got {tracked}. "
            "Requirement: /api/telemetry must upsert (append new IDs, overwrite existing). "
            "Old IDs not in the new payload must be preserved."
        )

    def test_iso_timestamp_format(self, session, reset_state):
        """sim_time_iso in /api/status response must be a valid ISO 8601 string."""
        from datetime import datetime
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])
        status = get_status(session).json()
        ts = status.get("sim_time_iso", "")
        assert isinstance(ts, str) and len(ts) > 0, (
            f"sim_time_iso should be a non-empty string, got {repr(ts)}."
        )
        # Should contain a date portion parseable as ISO
        try:
            # Strip trailing Z and parse
            clean = ts.replace("Z", "+00:00")
            datetime.fromisoformat(clean)
        except ValueError:
            pytest.fail(
                f"sim_time_iso '{ts}' is not a valid ISO 8601 timestamp. "
                "Requirement: Format sim time as datetime.isoformat() + 'Z'."
            )

