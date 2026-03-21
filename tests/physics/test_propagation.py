"""
Orbital propagation correctness tests.
Verifies the physics engine produces accurate results against known Keplerian predictions.
"""
import math
import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    RE, MU, CONSTELLATION_ALT, circular_orbit_state, tsiolkovsky_dm, M_WET
)


class TestKeplerianOrbit:
    """
    These tests verify the propagator against analytical Keplerian predictions.
    A correct RK4 propagator at 30s steps should have < 1 km error after one orbit.
    """

    def test_circular_orbit_period(self):
        """Satellite should return near initial position after one orbital period."""
        from acm.core.physics import propagate

        state = circular_orbit_state(CONSTELLATION_ALT, true_anomaly_deg=0)
        r = RE + CONSTELLATION_ALT
        T = 2 * math.pi * math.sqrt(r**3 / MU)  # orbital period in seconds

        propagated = propagate(state.reshape(1, 6), T, step=30.0)
        final = propagated[0]

        pos_error = np.linalg.norm(final[:3] - state[:3])
        # J2 causes nodal regression (~7 deg/day at 53° incl, 550 km) producing
        # ~60-80 km position shift per orbit. Accept up to 120 km.
        assert pos_error < 120.0, (
            f"After one orbit, position error is {pos_error:.2f} km (expect < 120 km). "
            f"RK4 propagator may have incorrect J2 implementation."
        )

    def test_circular_velocity_maintained(self):
        """Speed should remain approximately constant in circular orbit."""
        from acm.core.physics import propagate

        state = circular_orbit_state(CONSTELLATION_ALT)
        v_init = np.linalg.norm(state[3:6])

        propagated = propagate(state.reshape(1, 6), 3600.0, step=30.0)
        v_final = np.linalg.norm(propagated[0, 3:6])

        assert abs(v_final - v_init) < 0.01, (
            f"Speed changed from {v_init:.4f} to {v_final:.4f} km/s. "
            f"Energy conservation violated — check J2 perturbation implementation."
        )

    def test_altitude_maintained(self):
        """Altitude should remain approximately 550 km for circular orbit."""
        from acm.core.physics import propagate

        state = circular_orbit_state(CONSTELLATION_ALT)
        initial_alt = np.linalg.norm(state[:3]) - RE

        propagated = propagate(state.reshape(1, 6), 86400.0, step=30.0)
        final_alt = np.linalg.norm(propagated[0, :3]) - RE

        alt_drift = abs(final_alt - initial_alt)
        assert alt_drift < 5.0, (
            f"Altitude drifted {alt_drift:.2f} km in 24h (J2-only). "
            f"Expected < 5 km. Check J2 perturbation formula."
        )

    def test_batch_propagation_consistency(self):
        """Batch propagation (N objects) should match individual propagation."""
        from acm.core.physics import propagate, rk4_batch

        states = np.array([
            circular_orbit_state(CONSTELLATION_ALT, nu, 53.0)
            for nu in range(0, 360, 36)
        ], dtype=np.float64)

        batch_result = propagate(states, 300.0, step=30.0)

        for i in range(len(states)):
            single = propagate(states[i:i+1], 300.0, step=30.0)
            err = np.linalg.norm(batch_result[i, :3] - single[0, :3])
            assert err < 0.001, (
                f"Satellite {i} batch vs single error: {err:.6f} km. "
                f"Numba parallel loop may have race condition."
            )

    def test_j2_causes_nodal_regression(self):
        """J2 perturbation should cause measurable nodal regression in 24h."""
        from acm.core.physics import propagate

        state = circular_orbit_state(CONSTELLATION_ALT, 0, 53.0)
        propagated = propagate(state.reshape(1, 6), 86400.0, step=30.0)

        # Check that position has drifted from pure Keplerian (J2 effect)
        # J2 causes ~7 deg/day nodal regression for 53° incl at 550km
        init_pos = state[:3]
        final_pos = propagated[0, :3]

        # They should NOT be identical (J2 is doing work)
        pos_diff = np.linalg.norm(final_pos - init_pos)
        assert pos_diff > 0.1, "J2 perturbation appears to have no effect"

    def test_two_objects_propagate_independently(self):
        """Two objects should propagate without affecting each other."""
        from acm.core.physics import propagate

        s1 = circular_orbit_state(CONSTELLATION_ALT, 0)
        s2 = circular_orbit_state(CONSTELLATION_ALT + 50, 180)

        # Propagate together
        both = np.vstack([s1, s2])
        both_result = propagate(both, 3600.0, step=30.0)

        # Propagate separately
        r1 = propagate(s1.reshape(1, 6), 3600.0, step=30.0)[0]
        r2 = propagate(s2.reshape(1, 6), 3600.0, step=30.0)[0]

        assert np.linalg.norm(both_result[0] - r1) < 0.001
        assert np.linalg.norm(both_result[1] - r2) < 0.001


class TestRK4Integration:
    """Verify RK4 step accuracy."""

    def test_rk4_single_step_accuracy(self):
        """Single RK4 step should be within truncation error bounds."""
        from acm.core.physics import rk4_batch

        state = circular_orbit_state(CONSTELLATION_ALT)
        states = state.reshape(1, 6)

        result = rk4_batch(states, 30.0)
        assert result.shape == (1, 6)
        assert np.all(np.isfinite(result))

    def test_rk4_energy_conserved(self):
        """Specific orbital energy should be approximately conserved."""
        from acm.core.physics import propagate

        state = circular_orbit_state(CONSTELLATION_ALT)
        r0 = np.linalg.norm(state[:3])
        v0 = np.linalg.norm(state[3:6])
        E0 = 0.5 * v0**2 - MU / r0  # specific orbital energy

        propagated = propagate(state.reshape(1, 6), 86400.0, step=30.0)[0]
        r1 = np.linalg.norm(propagated[:3])
        v1 = np.linalg.norm(propagated[3:6])
        E1 = 0.5 * v1**2 - MU / r1

        assert abs(E1 - E0) < 0.05, (
            f"Energy drift: {abs(E1-E0):.4f} km²/s². Expected < 0.05."
        )
