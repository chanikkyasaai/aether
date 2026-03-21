"""
Uptime Score tests â€” 15% of total grader score.
Tests server reliability under sequential load, concurrent requests, and large payloads.
"""
import time
import threading
import pytest
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT,
    circular_orbit_state, state_to_obj, post_telemetry, post_step,
    get_status, load_constellation_50
)


class TestUptimeScore:

    def test_server_responds_to_100_status_requests(self, session, reset_state):
        """100 sequential GET /api/status requests must all return HTTP 200."""
        failures = []
        for i in range(100):
            r = get_status(session)
            if r.status_code != 200:
                failures.append(f"Request {i+1}: status {r.status_code}")
        assert len(failures) == 0, (
            f"{len(failures)} out of 100 status requests failed:\n"
            + "\n".join(failures[:10]) +
            "\nRequirement: Server must remain stable under sequential read load."
        )

    def test_concurrent_status_requests(self, session, reset_state):
        """20 concurrent GET /api/status requests must all return HTTP 200."""
        import requests
        results = []
        errors = []

        def fetch_status():
            try:
                s = requests.Session()
                s.headers.update({"Content-Type": "application/json"})
                r = s.get(f"{BASE_URL}/api/status", timeout=10)
                results.append(r.status_code)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=fetch_status) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(errors) == 0, (
            f"Concurrent status errors: {errors}. "
            "Requirement: Server must handle concurrent reads without threading issues."
        )
        assert all(s == 200 for s in results), (
            f"Not all 20 concurrent status requests returned 200: {results}. "
            "Requirement: FastAPI should handle concurrent GET requests without blocking."
        )
        assert len(results) == 20, f"Only {len(results)}/20 requests completed."

    @pytest.mark.slow
    def test_server_handles_rapid_steps(self, session, reset_state):
        """50 rapid sequential /simulate/step requests must all succeed."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-UP-01", "SATELLITE", state)])

        failures = []
        for i in range(50):
            r = post_step(session, 1)
            if r.status_code != 200:
                failures.append(f"Step {i+1}: status {r.status_code}")
        assert len(failures) == 0, (
            f"{len(failures)}/50 step requests failed:\n"
            + "\n".join(failures[:10]) +
            "\nRequirement: Server must handle rapid sequential step requests without crash."
        )

    @pytest.mark.slow
    def test_large_debris_field_no_crash(self, session, reset_state):
        """Ingesting 10000 debris objects must not crash the server (no HTTP 500)."""
        objects = [
            state_to_obj(f"DEB-{i:05d}", "DEBRIS",
                         circular_orbit_state(450.0 + (i % 200), i % 360))
            for i in range(10000)
        ]
        payload = {"timestamp": TEST_EPOCH, "objects": objects}
        r = session.post(f"{BASE_URL}/api/telemetry", json=payload, timeout=120)
        assert r.status_code != 500, (
            f"Server returned 500 for 10000 debris ingestion: {r.text[:200]}. "
            "Requirement: Handle large batch ingestion without memory or processing errors."
        )
        assert r.status_code == 200, (
            f"Expected 200 for 10000 debris, got {r.status_code}: {r.text[:200]}."
        )

    def test_concurrent_telemetry_and_status(self, session, reset_state):
        """Simultaneous telemetry POST and status GET must both succeed without errors."""
        import requests

        tel_result = []
        sta_result = []
        errors = []

        def do_telemetry():
            try:
                s = requests.Session()
                s.headers.update({"Content-Type": "application/json"})
                objects = [
                    state_to_obj(f"SAT-{i:02d}", "SATELLITE",
                                 circular_orbit_state(CONSTELLATION_ALT, i * 36))
                    for i in range(10)
                ]
                payload = {"timestamp": TEST_EPOCH, "objects": objects}
                r = s.post(f"{BASE_URL}/api/telemetry", json=payload, timeout=30)
                tel_result.append(r.status_code)
            except Exception as exc:
                errors.append(f"telemetry error: {exc}")

        def do_status():
            try:
                s = requests.Session()
                s.headers.update({"Content-Type": "application/json"})
                r = s.get(f"{BASE_URL}/api/status", timeout=10)
                sta_result.append(r.status_code)
            except Exception as exc:
                errors.append(f"status error: {exc}")

        t1 = threading.Thread(target=do_telemetry)
        t2 = threading.Thread(target=do_status)
        t1.start()
        t2.start()
        t1.join(timeout=35)
        t2.join(timeout=15)

        assert len(errors) == 0, (
            f"Concurrent request errors: {errors}. "
            "Requirement: Protect shared state with a lock in routes_telemetry.py."
        )
        assert tel_result and tel_result[0] == 200, (
            f"Telemetry POST failed: {tel_result}."
        )
        assert sta_result and sta_result[0] == 200, (
            f"Status GET failed: {sta_result}."
        )

