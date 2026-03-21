"""
Stress tests.
Tests server stability under heavy load, concurrent requests, and repeated cycles.
"""
import threading
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT,
    circular_orbit_state, state_to_obj, post_telemetry, post_step,
    get_status, load_constellation_50
)


class TestScenarioStress:

    @pytest.mark.slow
    def test_1000_debris_10_steps(self, session, reset_state):
        """50 sats + 1000 debris: 10 propagation steps must all complete without server crash."""
        load_constellation_50(session, n_debris=1000)

        for i in range(10):
            r = post_step(session, 60)
            assert r.status_code == 200, (
                f"Step {i+1} failed: {r.status_code}: {r.text}. "
                "FIX: Server must handle 1000-debris propagation without crash."
            )
            assert r.json().get("status") == "STEP_COMPLETE", (
                f"Step {i+1} returned unexpected status: {r.json()}."
            )

    def test_concurrent_step_requests(self, session, reset_state):
        """
        5 concurrent /simulate/step requests must all return HTTP 200.
        The server is expected to serialize them (lock) rather than run in parallel.
        """
        import requests
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-CONC-01", "SATELLITE", state)])

        results = []
        errors = []

        def do_step():
            try:
                s = requests.Session()
                s.headers.update({"Content-Type": "application/json"})
                r = s.post(f"{BASE_URL}/api/simulate/step",
                           json={"step_seconds": 1}, timeout=60)
                results.append(r.status_code)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=do_step) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=65)

        assert len(errors) == 0, (
            f"Concurrent step errors: {errors}. "
            "FIX: Protect /simulate/step with a threading.Lock()."
        )
        assert all(s == 200 for s in results), (
            f"Not all concurrent step requests returned 200: {results}. "
            "FIX: Serialise concurrent step requests without returning errors."
        )
        assert len(results) == 5, f"Only {len(results)}/5 step requests completed."

    @pytest.mark.slow
    def test_rapid_reset_reload_cycle(self, session, reset_state):
        """
        10 cycles of (reset → load 50 sats → step): each cycle must produce
        consistent, valid results — no accumulating state or memory corruption.
        """
        for cycle in range(10):
            # Reset
            r_reset = session.post(f"{BASE_URL}/api/reset", timeout=10)
            assert r_reset.status_code == 200, (
                f"Cycle {cycle+1}: Reset failed: {r_reset.status_code}."
            )

            # Load
            load_constellation_50(session, n_debris=0)

            # Step
            r_step = post_step(session, 60)
            assert r_step.status_code == 200, (
                f"Cycle {cycle+1}: Step failed: {r_step.status_code}: {r_step.text}."
            )

            # Verify consistent state
            status = get_status(session).json()
            assert status["satellites_tracked"] == 50, (
                f"Cycle {cycle+1}: Expected 50 satellites, "
                f"got {status['satellites_tracked']}."
            )
            assert status["total_collisions"] == 0, (
                f"Cycle {cycle+1}: Unexpected collisions: {status['total_collisions']}."
            )

    @pytest.mark.slow
    def test_memory_stable_across_steps(self, session, reset_state):
        """
        100 steps of 60 s each: all status fields must remain valid types throughout.
        Checks for memory leaks or state corruption over extended runs.
        """
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-MEM-01", "SATELLITE", state)])

        for i in range(100):
            r = post_step(session, 60)
            assert r.status_code == 200, f"Step {i+1} failed: {r.status_code}."

            status = get_status(session).json()

            # Type integrity checks every step
            assert isinstance(status["satellites_tracked"], int), (
                f"Step {i+1}: satellites_tracked is not int: "
                f"{type(status['satellites_tracked'])}."
            )
            assert isinstance(status["debris_tracked"], int), (
                f"Step {i+1}: debris_tracked is not int: "
                f"{type(status['debris_tracked'])}."
            )
            assert isinstance(status["total_collisions"], int), (
                f"Step {i+1}: total_collisions is not int: "
                f"{type(status['total_collisions'])}."
            )
            assert isinstance(status["fleet_fuel_remaining_kg"], (int, float)), (
                f"Step {i+1}: fleet_fuel_remaining_kg is not numeric."
            )
            assert isinstance(status["recent_events"], list), (
                f"Step {i+1}: recent_events is not a list."
            )
            assert status["fleet_fuel_remaining_kg"] >= 0, (
                f"Step {i+1}: fleet_fuel_remaining_kg is negative: "
                f"{status['fleet_fuel_remaining_kg']}."
            )
