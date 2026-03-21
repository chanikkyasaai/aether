"""
Tsiolkovsky rocket equation unit tests.
Verifies fuel consumption calculations used throughout the AETHER propulsion model.
"""
import math
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.conftest import (
    tsiolkovsky_dm, ISP, G0, M_WET, M_DRY, M_FUEL
)


class TestTsiolkovskyEquation:
    """Unit tests for the Tsiolkovsky rocket equation implementation."""

    def test_zero_dv_zero_consumption(self):
        """Zero delta-V must produce exactly zero fuel consumption."""
        dm = tsiolkovsky_dm(M_WET, 0.0)
        assert dm == 0.0, (
            f"Expected dm=0 for dv=0, got {dm}. "
            "FIX: tsiolkovsky_dm must return 0.0 when dv <= 0."
        )

    def test_known_value_1ms(self):
        """dv=0.001 km/s (1 m/s) on 550 kg must match closed-form: m*(1-exp(-dv/Ve))."""
        dv = 0.001  # km/s
        expected = M_WET * (1.0 - math.exp(-dv / (ISP * G0)))
        dm = tsiolkovsky_dm(M_WET, dv)
        assert abs(dm - expected) < 1e-9, (
            f"dm={dm:.9f} kg, expected {expected:.9f} kg for dv=1 m/s. "
            "FIX: formula should be dm = m_current * (1 - exp(-dv / (ISP * G0)))."
        )
        # Sanity range: ~0.187 kg
        assert 0.18 < dm < 0.20, (
            f"1 m/s burn on 550 kg should consume ~0.187 kg, got {dm:.4f} kg."
        )

    def test_full_fuel_budget(self):
        """After 15 m/s max burn on wet mass, remaining fuel must be >= 0."""
        dv_max = 0.015  # km/s (MAX_DV)
        dm = tsiolkovsky_dm(M_WET, dv_max)
        remaining = M_FUEL - dm
        assert remaining >= 0.0, (
            f"After {dv_max*1000:.0f} m/s burn: consumed {dm:.4f} kg from "
            f"{M_FUEL:.1f} kg available — fuel went negative ({remaining:.4f} kg). "
            "FIX: MAX_DV must be set so total fuel budget is not exceeded."
        )

    def test_fuel_monotonic(self):
        """Larger delta-V must always consume strictly more fuel (monotonically increasing)."""
        dvs = [0.001, 0.002, 0.005, 0.010, 0.015]
        dms = [tsiolkovsky_dm(M_WET, dv) for dv in dvs]
        for i in range(len(dms) - 1):
            assert dms[i] < dms[i + 1], (
                f"Fuel consumption not monotonic: "
                f"dv={dvs[i]:.3f} km/s → {dms[i]:.6f} kg  vs  "
                f"dv={dvs[i+1]:.3f} km/s → {dms[i+1]:.6f} kg. "
                "FIX: tsiolkovsky_dm must be strictly increasing in dv."
            )

    def test_mass_dependence(self):
        """Heavier satellite must consume more fuel for the same delta-V."""
        dv = 0.005  # km/s
        m_heavy = M_WET          # 550 kg
        m_light = M_WET * 0.5    # 275 kg
        dm_heavy = tsiolkovsky_dm(m_heavy, dv)
        dm_light = tsiolkovsky_dm(m_light, dv)
        assert dm_heavy > dm_light, (
            f"Heavy sat ({m_heavy} kg) consumed {dm_heavy:.4f} kg, "
            f"light sat ({m_light} kg) consumed {dm_light:.4f} kg. "
            "Expected heavier mass to consume more. "
            "FIX: tsiolkovsky_dm must scale proportionally with m_current_kg."
        )

    def test_isp_calculation(self):
        """ISP*G0 must equal exhaust velocity: 300 * 0.00980665 ≈ 2.94199 km/s."""
        exhaust_vel = ISP * G0
        expected = 300.0 * 0.00980665
        assert abs(exhaust_vel - expected) < 1e-6, (
            f"ISP*G0 = {exhaust_vel:.8f} km/s, expected {expected:.8f} km/s. "
            "FIX: Use ISP=300.0 s and G0=0.00980665 km/s² exactly."
        )
        assert abs(exhaust_vel - 2.94199) < 1e-3, (
            f"Exhaust velocity {exhaust_vel:.5f} km/s should be ~2.94199 km/s."
        )

    def test_negative_dv_returns_zero(self):
        """Negative delta-V (physically impossible) must return zero consumption."""
        dm = tsiolkovsky_dm(M_WET, -0.005)
        assert dm == 0.0, (
            f"Negative dv should return 0.0, got {dm:.6f}. "
            "FIX: Guard against dv < 0 in tsiolkovsky_dm."
        )

    def test_wet_mass_decreases_with_burns(self):
        """Each successive burn consumes less fuel because wet mass decreases."""
        wet = M_WET
        burns_dv = [0.003] * 5  # five identical 3 m/s burns
        consumptions = []
        for dv in burns_dv:
            dm = tsiolkovsky_dm(wet, dv)
            consumptions.append(dm)
            wet -= dm
        for i in range(1, len(consumptions)):
            assert consumptions[i] < consumptions[i - 1], (
                f"Burn {i} ({consumptions[i]:.6f} kg) not less than "
                f"burn {i-1} ({consumptions[i-1]:.6f} kg). "
                "FIX: wet mass must be updated between burns."
            )
