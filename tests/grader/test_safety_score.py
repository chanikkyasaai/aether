"""
Safety Score tests — 25% of total grader score.
Tests autonomous collision avoidance: CDM detection, evasion burn scheduling,
and correct handling of non-threatening debris.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT, COLLISION_KM,
    circular_orbit_state, state_to_obj, converging_debris, diverging_debris,
    post_telemetry, post_step, get_status, load_constellation_50
)


class TestSafetyScore:

    def test_cdm_detected_on_converging_debris(self, session, reset_state):
        """
        Ingesting a satellite + converging debris at 0.5 km miss / 120 s TCA
        must trigger a CDM warning after a propagation step.
        Safety score: CDM detection is the first gate.
        rel_speed=1.0 km/s keeps debris at 120 km (within 200 km coarse filter).
        """
        sat_state = circular_orbit_state(CONSTELLATION_ALT)
        deb_state = converging_debris(sat_state, miss_km=0.5, tca_seconds=120,
                                      rel_speed_km_s=1.0)

        objects = [
            state_to_obj("SAT-SAFE-01", "SATELLITE", sat_state),
            state_to_obj("DEB-CONV-01", "DEBRIS", deb_state),
        ]
        post_telemetry(session, objects)

        r = post_step(session, 60)
        assert r.status_code == 200

        status = get_status(session).json()
        total_cdms = status["active_cdm_warnings"] + status["critical_conjunctions"]
        assert total_cdms > 0, (
            f"Expected CDM (warning or critical) for converging debris at 0.5 km miss, "
            f"got active_cdm_warnings={status['active_cdm_warnings']}, "
            f"critical_conjunctions={status['critical_conjunctions']}. "
            "DIAGNOSIS: conjunction.py KDTree screening may have wrong threshold. "
            "FIX: CDM must be generated when predicted miss < 5 km within TCA window."
        )

    def test_evasion_burn_scheduled(self, session, reset_state):
        """
        After CDM detection, the autonomous avoidance system must either queue
        a burn (maneuvers_queued > 0) or have already executed one (maneuvers_executed > 0).
        Safety score: autonomous avoidance is 50% of the safety component.
        """
        sat_state = circular_orbit_state(CONSTELLATION_ALT)
        deb_state = converging_debris(sat_state, miss_km=0.5, tca_seconds=120,
                                      rel_speed_km_s=1.0)

        objects = [
            state_to_obj("SAT-SAFE-01", "SATELLITE", sat_state),
            state_to_obj("DEB-CONV-01", "DEBRIS", deb_state),
        ]
        post_telemetry(session, objects)

        # First step: CDM detection + maneuver scheduling
        step_r = post_step(session, 60)
        assert step_r.status_code == 200
        step_body = step_r.json()

        status = get_status(session).json()
        maneuver_activity = (
            status["maneuvers_queued"] > 0
            or step_body.get("maneuvers_executed", 0) > 0
        )
        assert maneuver_activity, (
            f"Expected maneuver to be queued or executed after CDM detection. "
            f"maneuvers_queued={status['maneuvers_queued']}, "
            f"maneuvers_executed={step_body.get('maneuvers_executed', 0)}. "
            "DIAGNOSIS: avoidance.py _schedule_evasion() may not be wired into step loop. "
            "FIX: After CDM is raised, call avoidance planner to schedule an evasion burn."
        )

    def test_no_cdm_on_diverging_debris(self, session, reset_state):
        """
        Debris already 200 km away and diverging must NOT trigger a CDM warning.
        False positives waste fuel and reduce the safety score.
        """
        sat_state = circular_orbit_state(CONSTELLATION_ALT)
        deb_state = diverging_debris(sat_state, separation_km=200.0)

        objects = [
            state_to_obj("SAT-SAFE-01", "SATELLITE", sat_state),
            state_to_obj("DEB-DIV-01", "DEBRIS", deb_state),
        ]
        post_telemetry(session, objects)
        post_step(session, 60)

        status = get_status(session).json()
        assert status["active_cdm_warnings"] == 0, (
            f"Expected 0 CDM warnings for diverging debris at 200 km, "
            f"got {status['active_cdm_warnings']}. "
            "DIAGNOSIS: Conjunction screening is triggering false positives. "
            "FIX: Filter diverging objects using relative velocity dot product."
        )

    def test_critical_conjunction_threshold(self, session, reset_state):
        """
        Debris at <200 m miss distance with very high PoC must appear as
        critical_conjunctions > 0 in status.
        """
        sat_state = circular_orbit_state(CONSTELLATION_ALT)
        # Miss distance just inside COLLISION_KM (100 m = 0.100 km)
        # rel_speed=1.0 km/s places debris 60 km ahead (within 200 km coarse filter)
        deb_state = converging_debris(sat_state, miss_km=0.05, tca_seconds=60,
                                      rel_speed_km_s=1.0)

        objects = [
            state_to_obj("SAT-SAFE-01", "SATELLITE", sat_state),
            state_to_obj("DEB-CRIT-01", "DEBRIS", deb_state),
        ]
        post_telemetry(session, objects)
        post_step(session, 30)

        status = get_status(session).json()
        assert status["critical_conjunctions"] > 0, (
            f"Expected critical_conjunctions > 0 for near-collision geometry, "
            f"got {status['critical_conjunctions']}. "
            "DIAGNOSIS: CDM raised but not escalated to critical_conjunctions. "
            "FIX: If PoC > threshold or miss_km < COLLISION_KM, mark as critical."
        )

    def test_zero_collisions_safe_scenario(self, session, reset_state):
        """
        Well-separated constellation (Walker Delta) with random debris should
        accumulate zero collisions over 10 propagation steps.
        """
        load_constellation_50(session, n_debris=100)

        for step_num in range(10):
            r = post_step(session, 60)
            assert r.status_code == 200, (
                f"Step {step_num+1} failed: {r.status_code}: {r.text}."
            )

        status = get_status(session).json()
        assert status["total_collisions"] == 0, (
            f"Expected 0 total_collisions for well-separated constellation, "
            f"got {status['total_collisions']}. "
            "DIAGNOSIS: Walker Delta spacing may be too tight, or collision "
            "detection threshold is too large. "
            "FIX: Ensure 50-sat Walker constellation has adequate separation (>10 km)."
        )
