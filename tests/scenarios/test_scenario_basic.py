"""
Basic end-to-end scenarios.
Tests full lifecycle flows: ingest → step → snapshot → status → reset.
"""
import pytest
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT,
    circular_orbit_state, state_to_obj,
    post_telemetry, post_step, get_snapshot, get_status
)


class TestScenarioBasic:

    def test_full_lifecycle_single_satellite(self, session, reset_state):
        """
        Ingest 1 satellite, step 10 times, verify satellite is still nominal
        and tracked in snapshot.
        """
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-LIFE-01", "SATELLITE", state)])

        for i in range(10):
            r = post_step(session, 60)
            assert r.status_code == 200, (
                f"Step {i+1} failed: {r.status_code}: {r.text}."
            )

        snap = get_snapshot(session).json()
        sat = next(
            (s for s in snap["satellites"] if s["id"] == "SAT-LIFE-01"), None
        )
        assert sat is not None, (
            "SAT-LIFE-01 disappeared from snapshot after 10 steps. "
            "FIX: Satellites must persist across propagation steps."
        )
        assert sat["status"] == "NOMINAL", (
            f"SAT-LIFE-01 status is '{sat['status']}' after 10 quiet steps, expected NOMINAL. "
            "FIX: Status should stay NOMINAL when no conjunctions exist."
        )

    def test_telemetry_then_snapshot(self, session, reset_state):
        """Ingest 5 satellites, then immediately verify snapshot shows all 5 with valid coords."""
        objects = [
            state_to_obj(f"SAT-SNAP-{i:02d}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 72, 53.0))
            for i in range(5)
        ]
        post_telemetry(session, objects)

        snap = get_snapshot(session).json()
        sats = snap.get("satellites", [])
        assert len(sats) == 5, (
            f"Expected 5 satellites in snapshot, got {len(sats)}. "
            "FIX: All ingested satellites must appear in /visualization/snapshot."
        )
        for sat in sats:
            assert -90 <= sat["lat"] <= 90, (
                f"SAT {sat['id']} lat={sat['lat']} out of range."
            )
            assert -180 <= sat["lon"] <= 180, (
                f"SAT {sat['id']} lon={sat['lon']} out of range."
            )
            assert sat["fuel_kg"] >= 0, (
                f"SAT {sat['id']} fuel_kg={sat['fuel_kg']} is negative."
            )

    def test_step_then_status_updates(self, session, reset_state):
        """After executing a step, sim_time_iso in /api/status must change."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-TIME-01", "SATELLITE", state)])

        status_before = get_status(session).json()
        time_before = status_before.get("sim_time_iso", "")

        post_step(session, 60)

        status_after = get_status(session).json()
        time_after = status_after.get("sim_time_iso", "")

        assert time_after != time_before, (
            f"sim_time_iso did not change after step: before='{time_before}', "
            f"after='{time_after}'. "
            "FIX: /simulate/step must advance sim_state.current_time_s and update status."
        )

    def test_reset_clears_state(self, session, reset_state):
        """
        Load 50 satellites, then POST /api/reset → status must show 0 satellites tracked.
        Note: reset_state fixture already calls reset before each test.
        This test verifies the reset endpoint explicitly.
        """
        from tests.conftest import load_constellation_50
        load_constellation_50(session, n_debris=10)

        status_loaded = get_status(session).json()
        assert status_loaded["satellites_tracked"] == 50, (
            f"Setup failed: expected 50 satellites, got "
            f"{status_loaded['satellites_tracked']}."
        )

        # Explicit reset
        r_reset = session.post(f"{BASE_URL}/api/reset", timeout=10)
        assert r_reset.status_code == 200, (
            f"POST /api/reset failed: {r_reset.status_code}: {r_reset.text}."
        )

        status_clear = get_status(session).json()
        assert status_clear["satellites_tracked"] == 0, (
            f"After reset, satellites_tracked={status_clear['satellites_tracked']}, expected 0. "
            "FIX: /api/reset must clear all satellites, debris, maneuvers and reset counters."
        )
        assert status_clear["debris_tracked"] == 0, (
            f"After reset, debris_tracked={status_clear['debris_tracked']}, expected 0."
        )
        assert status_clear["total_collisions"] == 0, (
            f"After reset, total_collisions={status_clear['total_collisions']}, expected 0."
        )
