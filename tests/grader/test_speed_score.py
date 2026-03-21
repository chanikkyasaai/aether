"""
Speed Score tests — 15% of total grader score.
Latency tests for all major endpoints under realistic load.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT,
    circular_orbit_state, state_to_obj, post_telemetry,
    load_constellation_50, assert_response_time, timed_post, timed_get
)


def _load_sats_and_debris(session, n_sats, n_debris):
    """Helper: ingest n_sats satellites + n_debris debris objects."""
    import numpy as np
    objects = []
    for i in range(n_sats):
        objects.append(state_to_obj(
            f"SAT-{i:02d}", "SATELLITE",
            circular_orbit_state(CONSTELLATION_ALT, i * (360 / max(n_sats, 1)), 53.0)
        ))
    rng = np.random.default_rng(99)
    for i in range(n_debris):
        alt = rng.uniform(450, 650)
        nu = rng.uniform(0, 360)
        inc = rng.uniform(0, 98)
        objects.append(state_to_obj(
            f"DEB-{i:05d}", "DEBRIS",
            circular_orbit_state(alt, nu, inc, rng.uniform(0, 360))
        ))
    payload = {"timestamp": TEST_EPOCH, "objects": objects}
    r = session.post(f"{BASE_URL}/api/telemetry", json=payload, timeout=60)
    assert r.status_code == 200, f"Load failed: {r.status_code}"


class TestSpeedScore:

    @pytest.mark.slow
    def test_step_50sats_1000deb_under_5s(self, session, reset_state):
        """50 sats + 1000 debris: /simulate/step must respond in < 5000 ms."""
        _load_sats_and_debris(session, 50, 1000)
        r, ms = timed_post(session, f"{BASE_URL}/api/simulate/step",
                           {"step_seconds": 60})
        assert r.status_code == 200, f"Step failed: {r.status_code}"
        assert_response_time(ms, 5000, "50sats+1000deb /simulate/step")

    def test_status_latency_under_100ms(self, session, reset_state):
        """GET /api/status must respond in < 100 ms even with data loaded."""
        load_constellation_50(session, n_debris=1000)
        # Warm-up call
        timed_get(session, f"{BASE_URL}/api/status")
        # Measured call
        r, ms = timed_get(session, f"{BASE_URL}/api/status")
        assert r.status_code == 200
        assert_response_time(ms, 100, "GET /api/status")

    def test_snapshot_latency_under_200ms(self, session, reset_state):
        """GET /api/visualization/snapshot must respond in < 200 ms."""
        load_constellation_50(session, n_debris=1000)
        # Warm-up
        timed_get(session, f"{BASE_URL}/api/visualization/snapshot")
        # Measured
        r, ms = timed_get(session, f"{BASE_URL}/api/visualization/snapshot")
        assert r.status_code == 200
        assert_response_time(ms, 200, "GET /api/visualization/snapshot")

    def test_telemetry_50sat_under_500ms(self, session, reset_state):
        """POST /api/telemetry with 50 satellites must complete in < 500 ms."""
        objects = [
            state_to_obj(f"SAT-{i:02d}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 7.2))
            for i in range(50)
        ]
        payload = {"timestamp": TEST_EPOCH, "objects": objects}
        r, ms = timed_post(session, f"{BASE_URL}/api/telemetry", payload)
        assert r.status_code == 200, f"Telemetry failed: {r.status_code}"
        assert_response_time(ms, 500, "POST /api/telemetry (50 sats)")

    @pytest.mark.slow
    def test_step_10k_debris_under_30s(self, session, reset_state):
        """50 sats + 10000 debris: /simulate/step must respond in < 30000 ms."""
        _load_sats_and_debris(session, 50, 10000)
        # One warmup step (Numba JIT should already be warmed up at server start)
        r_warm, _ = timed_post(session, f"{BASE_URL}/api/simulate/step",
                               {"step_seconds": 60})
        assert r_warm.status_code == 200, f"Warmup step failed: {r_warm.status_code}"
        # Measured step
        r, ms = timed_post(session, f"{BASE_URL}/api/simulate/step",
                           {"step_seconds": 60})
        assert r.status_code == 200, f"Measured step failed: {r.status_code}"
        assert_response_time(ms, 30_000, "50sats+10000deb /simulate/step [GRADER SCALE]")
