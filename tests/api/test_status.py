"""
GET /api/status — System health endpoint tests matching grader specification.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, CONSTELLATION_ALT,
    circular_orbit_state, state_to_obj, post_telemetry,
    get_status, load_constellation_50
)


class TestStatus:

    def test_status_schema(self, session, reset_state):
        """All required fields must be present with correct types."""
        body = get_status(session).json()
        required = {
            "system": str,
            "sim_time_iso": str,
            "satellites_tracked": int,
            "debris_tracked": int,
            "active_cdm_warnings": int,
            "critical_conjunctions": int,
            "maneuvers_queued": int,
            "total_collisions": int,
            "fleet_fuel_remaining_kg": (int, float),
            "recent_events": list,
        }
        for field, expected_type in required.items():
            assert field in body, (
                f"Required field '{field}' missing from /api/status response. "
                f"Response keys: {list(body.keys())}. "
                f"FIX: Add '{field}' to StatusResponse schema."
            )
            assert isinstance(body[field], expected_type), (
                f"Field '{field}' has type {type(body[field])}, expected {expected_type}. "
                f"FIX: Ensure correct type for '{field}'."
            )

    def test_satellites_tracked_matches_ingested(self, session, reset_state):
        """After ingesting 10 satellites, satellites_tracked must equal 10."""
        objects = [
            state_to_obj(f"SAT-{i:02d}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 36))
            for i in range(10)
        ]
        post_telemetry(session, objects)
        status = get_status(session).json()
        assert status["satellites_tracked"] == 10, (
            f"Expected satellites_tracked=10, got {status['satellites_tracked']}. "
            "FIX: Count satellites correctly in state and expose via /api/status."
        )

    def test_debris_tracked_matches_ingested(self, session, reset_state):
        """After ingesting 200 debris, debris_tracked must equal 200."""
        objects = [
            state_to_obj(f"DEB-{i:05d}", "DEBRIS",
                         circular_orbit_state(480.0 + (i % 100), i % 360))
            for i in range(200)
        ]
        post_telemetry(session, objects)
        status = get_status(session).json()
        assert status["debris_tracked"] == 200, (
            f"Expected debris_tracked=200, got {status['debris_tracked']}. "
            "FIX: Count debris correctly in state and expose via /api/status."
        )

    def test_initial_collision_count_zero(self, session, reset_state):
        """Immediately after reset, total_collisions must be 0."""
        status = get_status(session).json()
        assert status["total_collisions"] == 0, (
            f"Expected total_collisions=0 on fresh state, got {status['total_collisions']}. "
            "FIX: Reset must zero out the collision counter."
        )

    def test_fleet_fuel_calculation(self, session, reset_state):
        """50 freshly-ingested satellites → fleet_fuel_remaining_kg ≈ 50 × 50 = 2500 kg."""
        load_constellation_50(session, n_debris=0)
        status = get_status(session).json()
        expected = 50 * 50.0  # 2500 kg
        actual = status["fleet_fuel_remaining_kg"]
        assert abs(actual - expected) < 1.0, (
            f"Expected fleet fuel ≈ {expected:.1f} kg, got {actual:.1f} kg. "
            "FIX: Sum all satellite fuel_kg values for fleet_fuel_remaining_kg."
        )

    def test_recent_events_is_list(self, session, reset_state):
        """recent_events field must always be a list (can be empty)."""
        body = get_status(session).json()
        assert isinstance(body.get("recent_events"), list), (
            f"recent_events must be a list, got {type(body.get('recent_events'))}. "
            "FIX: Initialize recent_events as [] in state and return it in status."
        )

    def test_system_name(self, session, reset_state):
        """system field must equal exactly 'AETHER'."""
        body = get_status(session).json()
        assert body.get("system") == "AETHER", (
            f"system field must be 'AETHER', got '{body.get('system')}'. "
            "FIX: Hardcode system='AETHER' in StatusResponse."
        )
