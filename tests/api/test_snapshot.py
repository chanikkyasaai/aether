"""
GET /api/visualization/snapshot — Contract tests matching grader specification.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    RE, BASE_URL, CONSTELLATION_ALT, TEST_EPOCH,
    circular_orbit_state, state_to_obj, post_telemetry,
    post_step, get_snapshot, load_constellation_50,
)


class TestSnapshot:

    def test_empty_state_snapshot(self, session, reset_state):
        """With no objects loaded, snapshot must return satellites=[] and debris_cloud=[]."""
        r = get_snapshot(session)
        assert r.status_code == 200, (
            f"Expected 200 for empty snapshot, got {r.status_code}: {r.text}."
        )
        body = r.json()
        assert body.get("satellites") == [], (
            f"Expected empty satellites list, got {body.get('satellites')}."
        )
        assert body.get("debris_cloud") == [], (
            f"Expected empty debris_cloud list, got {body.get('debris_cloud')}."
        )

    def test_snapshot_with_satellites(self, session, reset_state):
        """After ingesting 3 satellites, snapshot.satellites must have exactly 3 entries."""
        objects = [
            state_to_obj(f"SAT-{i:02d}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 120))
            for i in range(3)
        ]
        post_telemetry(session, objects)
        body = get_snapshot(session).json()
        assert len(body.get("satellites", [])) == 3, (
            f"Expected 3 satellite entries in snapshot, got "
            f"{len(body.get('satellites', []))}. "
            "FIX: Snapshot must reflect all ingested satellites."
        )

    def test_snapshot_lat_lon_range(self, session, reset_state):
        """All satellite lat must be in [-90,90] and lon in [-180,180]."""
        objects = [
            state_to_obj(f"SAT-{i:02d}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 36, 53.0))
            for i in range(10)
        ]
        post_telemetry(session, objects)
        body = get_snapshot(session).json()
        for sat in body["satellites"]:
            assert -90.0 <= sat["lat"] <= 90.0, (
                f"SAT {sat['id']} lat={sat['lat']} is out of [-90, 90]. "
                "FIX: ECI-to-geodetic conversion is incorrect."
            )
            assert -180.0 <= sat["lon"] <= 180.0, (
                f"SAT {sat['id']} lon={sat['lon']} is out of [-180, 180]. "
                "FIX: ECI-to-geodetic conversion produces wrong longitude."
            )

    def test_snapshot_fuel_positive(self, session, reset_state):
        """All satellite fuel_kg values must be >= 0."""
        objects = [
            state_to_obj(f"SAT-{i:02d}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 72))
            for i in range(5)
        ]
        post_telemetry(session, objects)
        body = get_snapshot(session).json()
        for sat in body["satellites"]:
            assert sat["fuel_kg"] >= 0.0, (
                f"SAT {sat['id']} fuel_kg={sat['fuel_kg']} is negative. "
                "FIX: Fuel must never go below 0."
            )

    def test_snapshot_status_valid(self, session, reset_state):
        """All satellite status values must be in {NOMINAL, EVADING, RECOVERING, EOL}."""
        valid_statuses = {"NOMINAL", "EVADING", "RECOVERING", "EOL"}
        objects = [
            state_to_obj(f"SAT-{i:02d}", "SATELLITE",
                         circular_orbit_state(CONSTELLATION_ALT, i * 72))
            for i in range(5)
        ]
        post_telemetry(session, objects)
        body = get_snapshot(session).json()
        for sat in body["satellites"]:
            assert sat["status"] in valid_statuses, (
                f"SAT {sat['id']} has invalid status '{sat['status']}'. "
                f"Valid values: {valid_statuses}. "
                "FIX: SatelliteStatus enum must match the grader's expected values."
            )

    def test_debris_cloud_format(self, session, reset_state):
        """Each debris_cloud entry must be a 4-element list: [id, lat, lon, alt_km]."""
        state = circular_orbit_state(500.0)
        post_telemetry(session, [state_to_obj("DEB-00001", "DEBRIS", state)])
        body = get_snapshot(session).json()
        cloud = body.get("debris_cloud", [])
        assert len(cloud) == 1, f"Expected 1 debris entry, got {len(cloud)}."
        entry = cloud[0]
        assert len(entry) == 4, (
            f"debris_cloud entry must have 4 elements [id, lat, lon, alt_km], "
            f"got {len(entry)}: {entry}. "
            "FIX: Format debris as [id_str, lat_float, lon_float, alt_km_float]."
        )
        assert isinstance(entry[0], str), f"entry[0] (id) must be str, got {type(entry[0])}."
        assert -90.0 <= entry[1] <= 90.0, f"lat {entry[1]} out of range."
        assert -180.0 <= entry[2] <= 180.0, f"lon {entry[2]} out of range."
        assert entry[3] > 0.0, f"alt_km {entry[3]} must be positive."

    def test_snapshot_after_step(self, session, reset_state):
        """Satellite position (lat/lon) must change after a simulate step."""
        state = circular_orbit_state(CONSTELLATION_ALT, 0.0)
        post_telemetry(session, [state_to_obj("SAT-01", "SATELLITE", state)])

        snap0 = get_snapshot(session).json()
        sat0 = next(s for s in snap0["satellites"] if s["id"] == "SAT-01")
        lat0, lon0 = sat0["lat"], sat0["lon"]

        post_step(session, 300)  # 5-minute step — large enough to see movement

        snap1 = get_snapshot(session).json()
        sat1 = next(s for s in snap1["satellites"] if s["id"] == "SAT-01")
        lat1, lon1 = sat1["lat"], sat1["lon"]

        position_changed = (abs(lat1 - lat0) > 0.001) or (abs(lon1 - lon0) > 0.001)
        assert position_changed, (
            f"Satellite position unchanged after 300 s step: "
            f"before=({lat0:.4f}, {lon0:.4f}), after=({lat1:.4f}, {lon1:.4f}). "
            "FIX: /simulate/step must propagate orbital state and update ECI positions."
        )

    def test_50_sat_snapshot_count(self, session, reset_state):
        """After ingesting 50 satellites, snapshot must list exactly 50 entries."""
        load_constellation_50(session, n_debris=0)
        body = get_snapshot(session).json()
        count = len(body.get("satellites", []))
        assert count == 50, (
            f"Expected 50 satellite entries, got {count}. "
            "FIX: All 50 ingested satellites must appear in the snapshot."
        )
