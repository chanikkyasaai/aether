"""
POST /api/simulate/step â€” Contract and performance tests.
The grader evaluates /step speed as 15% of total score.
"""
import time
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT,
    circular_orbit_state, state_to_obj, post_telemetry, post_step,
    get_status, load_constellation_50, assert_response_time, timed_post
)


class TestSimulateStep:

    def test_step_returns_correct_schema(self, session, reset_state):
        """Response must have status, new_timestamp, collisions_detected, maneuvers_executed."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])
        r = post_step(session, 60)
        assert r.status_code == 200, (
            f"Expected 200, got {r.status_code}: {r.text}."
        )
        body = r.json()
        for field in ("status", "new_timestamp", "collisions_detected", "maneuvers_executed"):
            assert field in body, (
                f"Required field '{field}' missing from /simulate/step response. "
                f"Response was: {body}. "
                f"Requirement: Include all four fields in StepResponse schema."
            )

    def test_step_advances_time(self, session, reset_state):
        """After step=60, new_timestamp must be exactly 60 s later than ingestion epoch."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)],
                       "2026-03-12T08:00:00.000Z")
        r = post_step(session, 60)
        assert r.status_code == 200
        ts = r.json().get("new_timestamp", "")
        # Expect epoch + 60s = 08:00:01 ... 08:01:00
        assert "2026-03-12T08:01:00" in ts or "08:01:00" in ts, (
            f"Expected timestamp to reflect +60s from 08:00:00, got '{ts}'. "
            "Requirement: Advance sim_state.current_time_s by step_seconds each step."
        )

    def test_step_without_satellites(self, session, reset_state):
        """Empty simulation (no objects loaded) must still return HTTP 200."""
        r = post_step(session, 60)
        assert r.status_code == 200, (
            f"Expected 200 for empty step, got {r.status_code}: {r.text}. "
            "Requirement: /simulate/step should handle empty state gracefully."
        )

    def test_multiple_steps_accumulate_time(self, session, reset_state):
        """5 consecutive steps of 60 s must advance sim time by exactly 300 s total."""
        from datetime import datetime, timezone, timedelta
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)],
                       "2026-03-12T08:00:00.000Z")
        last_ts = None
        for _ in range(5):
            r = post_step(session, 60)
            assert r.status_code == 200
            last_ts = r.json().get("new_timestamp", "")
        # After 5Ã—60=300s, time should be 08:05:00
        assert "08:05:00" in last_ts, (
            f"After 5Ã—60s steps, expected 08:05:00 in timestamp, got '{last_ts}'. "
            "Requirement: Sim clock must accumulate steps correctly."
        )

    @pytest.mark.slow
    def test_step_with_full_constellation(self, session, reset_state):
        """50 sats + 100 debris: step must return within 30 s."""
        load_constellation_50(session, n_debris=100)
        r, ms = timed_post(session, f"{BASE_URL}/api/simulate/step",
                           {"step_seconds": 60})
        assert r.status_code == 200, f"Step failed: {r.status_code}: {r.text}"
        assert ms < 30_000, (
            f"/simulate/step with 50 sats + 100 debris took {ms:.0f} ms (limit 30000 ms). "
            "Requirement: Parallelize physics propagation with Numba @njit(parallel=True)."
        )

    def test_step_seconds_range(self, session, reset_state):
        """Both step=1 and step=86400 must be accepted without error."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])

        r1 = post_step(session, 1)
        assert r1.status_code == 200, (
            f"step=1 failed with {r1.status_code}: {r1.text}."
        )

        r2 = post_step(session, 86400)
        assert r2.status_code == 200, (
            f"step=86400 failed with {r2.status_code}: {r2.text}. "
            "Requirement: Accept any step_seconds value in [1, 86400]."
        )

