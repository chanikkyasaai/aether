"""
Fleet-level scenarios.
Tests Walker Delta constellation behaviour: tracking, fuel accounting, maneuver lifecycle.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT, M_FUEL,
    circular_orbit_state, state_to_obj,
    post_telemetry, post_step, post_maneuver, get_snapshot, get_status,
    load_constellation_50
)


class TestScenarioFleet:

    def test_50_satellite_walker_delta(self, session, reset_state):
        """Load 50 Walker Delta satellites â†’ status.satellites_tracked must equal 50."""
        load_constellation_50(session, n_debris=0)
        status = get_status(session).json()
        assert status["satellites_tracked"] == 50, (
            f"Expected satellites_tracked=50 after Walker Delta load, "
            f"got {status['satellites_tracked']}. "
            "Requirement: All 50 satellites in the Walker Delta must be ingested and tracked."
        )

    @pytest.mark.slow
    def test_10_steps_fleet_healthy(self, session, reset_state):
        """
        50 sats in Walker Delta with 100 random debris: after 10 steps of 60 s,
        total_collisions must remain 0 (satellites start well-separated).
        """
        load_constellation_50(session, n_debris=100)

        for i in range(10):
            r = post_step(session, 60)
            assert r.status_code == 200, (
                f"Step {i+1} failed: {r.status_code}: {r.text}."
            )

        status = get_status(session).json()
        assert status["total_collisions"] == 0, (
            f"Expected 0 collisions after 10 steps with well-separated constellation, "
            f"got {status['total_collisions']}. "
            "Check: Walker Delta satellites may be initialised too close together. "
            "Requirement: Ensure RAAN spacing is 72Â° and in-plane spacing is 36Â°."
        )

    @pytest.mark.slow
    def test_fleet_fuel_accounting(self, session, reset_state):
        """
        After 10 propagation steps, fleet_fuel_remaining_kg must be <= initial total.
        Some fuel may be consumed by autonomous avoidance burns.
        """
        load_constellation_50(session, n_debris=100)
        initial_status = get_status(session).json()
        initial_fuel = initial_status["fleet_fuel_remaining_kg"]

        for _ in range(10):
            post_step(session, 60)

        final_status = get_status(session).json()
        final_fuel = final_status["fleet_fuel_remaining_kg"]

        assert final_fuel <= initial_fuel, (
            f"Fleet fuel increased after steps: {initial_fuel:.2f} â†’ {final_fuel:.2f} kg. "
            "Requirement: Fuel must only ever decrease â€” check fuel update logic in step handler."
        )
        assert final_fuel >= 0.0, (
            f"Fleet fuel went negative: {final_fuel:.2f} kg. "
            "Requirement: Clamp individual satellite fuel to 0."
        )

    @pytest.mark.slow
    def test_maneuver_queue_clears(self, session, reset_state):
        """
        Schedule burns for one satellite, run enough steps to execute them all,
        then maneuvers_queued should eventually reach 0.
        """
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-MQ-01", "SATELLITE", state)])

        # Schedule 3 burns spaced 2 minutes apart
        burns = [
            {
                "burn_id": f"B{i:03d}",
                "burnTime": f"2026-03-12T08:0{i+1}:00.000Z",
                "deltaV_vector": {"x": 0.0, "y": 0.001, "z": 0.0}
            }
            for i in range(3)
        ]
        r = post_maneuver(session, "SAT-MQ-01", burns)
        assert r.status_code in (200, 202), (
            f"Maneuver schedule failed: {r.status_code}: {r.text}."
        )

        initial_status = get_status(session).json()
        assert initial_status["maneuvers_queued"] > 0, (
            "Expected maneuvers_queued > 0 after scheduling burns."
        )

        # Run 5 steps of 60 s each â†’ all 3 burns should have fired by t=300s
        for _ in range(5):
            post_step(session, 60)

        final_status = get_status(session).json()
        assert final_status["maneuvers_queued"] == 0, (
            f"Expected maneuvers_queued=0 after all burns executed, "
            f"got {final_status['maneuvers_queued']}. "
            "Requirement: Remove executed burns from the queue in routes_simulate.py."
        )

