"""
Edge case scenarios.
Tests boundary conditions, malformed inputs, and unusual but valid request patterns.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT,
    circular_orbit_state, state_to_obj, make_telemetry_payload,
    post_telemetry, post_step, post_maneuver, get_status, get_snapshot
)


class TestScenarioEdgeCases:

    def test_step_with_zero_satellites(self, session, reset_state):
        """POST /simulate/step with nothing loaded must return HTTP 200."""
        r = post_step(session, 60)
        assert r.status_code == 200, (
            f"Expected 200 for step with no objects, got {r.status_code}: {r.text}. "
            "Requirement: Guard empty-state step with early return, not an error."
        )

    def test_duplicate_satellite_id_handling(self, session, reset_state):
        """
        Ingesting the same sat_id twice in a single payload must not crash
        and must not create duplicate entries.
        """
        state = circular_orbit_state(CONSTELLATION_ALT)
        obj = state_to_obj("SAT-DUP", "SATELLITE", state)
        # Send same object twice in same payload
        payload = make_telemetry_payload([obj, obj])
        r = session.post(f"{BASE_URL}/api/telemetry", json=payload, timeout=10)
        assert r.status_code in (200, 422), (
            f"Duplicate ID in payload returned unexpected status {r.status_code}: {r.text}. "
            "Requirement: Handle duplicate IDs by deduplicating or returning 422."
        )
        if r.status_code == 200:
            status = get_status(session).json()
            # Should have at most 1 satellite (deduplicated)
            assert status["satellites_tracked"] <= 1, (
                f"Duplicate satellite ID created {status['satellites_tracked']} entries. "
                "Requirement: Use upsert (overwrite) on duplicate IDs."
            )

    def test_very_large_step_seconds(self, session, reset_state):
        """step_seconds=86400 (one full day) must return HTTP 200 without error."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-LARGE-01", "SATELLITE", state)])
        r = post_step(session, 86400)
        assert r.status_code == 200, (
            f"step=86400 returned {r.status_code}: {r.text}. "
            "Requirement: Accept any step_seconds up to 86400 without error."
        )

    def test_single_debris_no_satellite(self, session, reset_state):
        """Only debris with no satellites: step must return 200 and debris must be tracked."""
        state = circular_orbit_state(500.0)
        post_telemetry(session, [state_to_obj("DEB-ONLY", "DEBRIS", state)])

        r = post_step(session, 60)
        assert r.status_code == 200, (
            f"Step with only debris failed: {r.status_code}: {r.text}. "
            "Requirement: Step must handle debris-only state without crash."
        )
        status = get_status(session).json()
        assert status["debris_tracked"] > 0, (
            f"debris_tracked={status['debris_tracked']}, expected > 0 after debris ingestion."
        )

    def test_empty_maneuver_sequence(self, session, reset_state):
        """Scheduling an empty maneuver_sequence must return 422 or 400."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-EM-01", "SATELLITE", state)])

        r = post_maneuver(session, "SAT-EM-01", [])  # empty sequence
        assert r.status_code in (400, 422), (
            f"Expected 400 or 422 for empty maneuver_sequence, got {r.status_code}: {r.text}. "
            "Requirement: Validate that maneuver_sequence is non-empty."
        )

    def test_burn_time_in_past(self, session, reset_state):
        """
        Scheduling a burn with burnTime before current sim time must be handled
        gracefully â€” either accepted (and executed immediately) or rejected with 4xx.
        Must not crash the server.
        """
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-PAST-01", "SATELLITE", state)])

        # Advance sim time by 120 seconds first
        post_step(session, 120)

        # Now schedule a burn in the "past" (before current sim time)
        r = post_maneuver(session, "SAT-PAST-01", [{
            "burn_id": "BURN-PAST",
            "burnTime": "2026-03-12T08:00:30.000Z",  # 30 s into epoch â€” before t+120
            "deltaV_vector": {"x": 0.0, "y": 0.001, "z": 0.0}
        }])
        # Server may accept (200/202) or reject (400/422) â€” must not 500
        assert r.status_code != 500, (
            f"Server returned 500 for past burnTime: {r.text[:200]}. "
            "Requirement: Handle past burnTime gracefully â€” reject with 422 or execute immediately."
        )
        assert r.status_code in (200, 202, 400, 422), (
            f"Unexpected status {r.status_code} for past burnTime."
        )

