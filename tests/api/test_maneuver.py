"""
POST /api/maneuver/schedule — Contract tests matching grader specification.
"""
import math
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    BASE_URL, TEST_EPOCH, CONSTELLATION_ALT, M_WET, M_FUEL,
    tsiolkovsky_dm, circular_orbit_state, state_to_obj,
    post_telemetry, post_step, post_maneuver, get_snapshot, get_status
)


def _single_burn(burn_id, burn_time, dv_x=0.0, dv_y=0.001, dv_z=0.0):
    return {
        "burn_id": burn_id,
        "burnTime": burn_time,
        "deltaV_vector": {"x": dv_x, "y": dv_y, "z": dv_z}
    }


class TestManeuverSchedule:

    def test_schedule_valid_burn(self, session, reset_state):
        """Ingesting 1 sat and scheduling 1 burn → HTTP 200/202, status='SCHEDULED'."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])

        r = post_maneuver(session, "SAT-01", [
            _single_burn("BURN-001", "2026-03-12T08:01:00.000Z")
        ])
        assert r.status_code in (200, 202), (
            f"Expected 200 or 202 for valid burn schedule, got {r.status_code}: {r.text}. "
            "FIX: Return 200 or 202 from POST /api/maneuver/schedule."
        )
        body = r.json()
        status_val = body.get("status", "")
        assert "SCHEDULED" in status_val.upper(), (
            f"Expected status containing 'SCHEDULED', got '{status_val}'. "
            "FIX: ManeuverResponse must include status='SCHEDULED' on success."
        )

    def test_response_validation_fields(self, session, reset_state):
        """Response must include ground_station_los (bool), sufficient_fuel (bool),
        projected_mass_remaining_kg (float)."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])

        r = post_maneuver(session, "SAT-01", [
            _single_burn("BURN-001", "2026-03-12T08:01:00.000Z", dv_y=0.001)
        ])
        assert r.status_code in (200, 202)
        body = r.json()

        # Fields may be at top level or inside a nested "validation" key
        flat = body.copy()
        if "validation" in body:
            flat.update(body["validation"])

        assert "ground_station_los" in flat, (
            f"Missing 'ground_station_los' in response: {body}. "
            "FIX: Add ground_station_los bool to ManeuverResponse."
        )
        assert isinstance(flat["ground_station_los"], bool), (
            f"ground_station_los must be bool, got {type(flat['ground_station_los'])}."
        )
        assert "sufficient_fuel" in flat, (
            f"Missing 'sufficient_fuel' in response: {body}. "
            "FIX: Add sufficient_fuel bool to ManeuverResponse."
        )
        assert isinstance(flat["sufficient_fuel"], bool), (
            f"sufficient_fuel must be bool, got {type(flat['sufficient_fuel'])}."
        )
        assert "projected_mass_remaining_kg" in flat, (
            f"Missing 'projected_mass_remaining_kg' in response: {body}. "
            "FIX: Compute and return projected post-burn mass."
        )
        assert isinstance(flat["projected_mass_remaining_kg"], (int, float)), (
            f"projected_mass_remaining_kg must be numeric."
        )

    def test_insufficient_fuel_flagged(self, session, reset_state):
        """Scheduling a burn that would consume more fuel than available → sufficient_fuel=False."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])

        # 25 burns of 15 m/s each exceed the 50 kg fuel budget
        # (10 burns only use ~28 kg < 50 kg, need ~18+ burns to exhaust 50 kg)
        burns = [
            _single_burn(f"B{i:03d}", f"2026-03-12T{8 + i//60:02d}:{i%60:02d}:00.000Z",
                         dv_y=0.015)
            for i in range(25)
        ]
        r = post_maneuver(session, "SAT-01", burns)
        # Accept 422 (rejected outright) or 200/202 with sufficient_fuel=False
        if r.status_code in (200, 202):
            body = r.json()
            flat = body.copy()
            if "validation" in body:
                flat.update(body["validation"])
            assert flat.get("sufficient_fuel") is False, (
                f"Overfuel burn should flag sufficient_fuel=False, got: {body}. "
                "FIX: Compute total dv cost and compare against fuel_kg."
            )
        else:
            assert r.status_code == 422, (
                f"Expected 422 or sufficient_fuel=False for overfuel, got {r.status_code}."
            )

    def test_unknown_satellite_rejected(self, session, reset_state):
        """Scheduling a burn for a satellite ID not in the system → 404 or 400."""
        r = post_maneuver(session, "SAT-NONEXISTENT-XYZ", [
            _single_burn("BURN-001", "2026-03-12T08:01:00.000Z")
        ])
        assert r.status_code in (400, 404), (
            f"Expected 400 or 404 for unknown satellite, got {r.status_code}: {r.text}. "
            "FIX: Lookup satellite by ID and return 404 if not found."
        )

    def test_burn_dv_magnitude_small(self, session, reset_state):
        """0.001 km/s (1 m/s) burn → projected_mass_remaining_kg close to initial wet mass."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])

        dv = 0.001  # km/s
        r = post_maneuver(session, "SAT-01", [
            _single_burn("BURN-001", "2026-03-12T08:01:00.000Z", dv_y=dv)
        ])
        assert r.status_code in (200, 202)
        body = r.json()
        flat = body.copy()
        if "validation" in body:
            flat.update(body["validation"])

        projected = flat.get("projected_mass_remaining_kg", 0)
        dm_expected = tsiolkovsky_dm(M_WET, dv)
        expected_remaining = M_WET - dm_expected
        assert abs(projected - expected_remaining) < 1.0, (
            f"projected_mass_remaining_kg={projected:.3f} kg, "
            f"expected ≈{expected_remaining:.3f} kg (consumed {dm_expected:.4f} kg). "
            "FIX: Apply Tsiolkovsky to compute projected remaining mass."
        )

    def test_burn_sequence_multiple(self, session, reset_state):
        """Schedule a 2-burn evasion+recovery sequence → HTTP 200/202 with no error."""
        state = circular_orbit_state(CONSTELLATION_ALT)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])

        burns = [
            _single_burn("EVA-001", "2026-03-12T08:02:00.000Z",
                         dv_x=0.0, dv_y=0.003, dv_z=0.0),
            _single_burn("REC-001", "2026-03-12T08:15:00.000Z",
                         dv_x=0.0, dv_y=-0.003, dv_z=0.0),
        ]
        r = post_maneuver(session, "SAT-01", burns)
        assert r.status_code in (200, 202), (
            f"2-burn sequence failed with {r.status_code}: {r.text}. "
            "FIX: POST /api/maneuver/schedule must accept multi-burn sequences."
        )
