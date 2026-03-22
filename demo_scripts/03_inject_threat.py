"""
DEMO STEP 3 — Inject a close-approach debris object to trigger evasion burn
Run: python3 demo_scripts/03_inject_threat.py
Then watch the Gantt timeline for the EVASION + RECOVERY burn sequence.
"""
import requests, math, time
from datetime import datetime, timezone

BASE = "http://localhost:8000"
session = requests.Session()

# Get current SAT-000 position from snapshot
snap = session.get(f"{BASE}/api/visualization/snapshot", timeout=10).json()
sats = {s["id"]: s for s in snap.get("satellites", [])}

if "SAT-000" not in sats:
    print("SAT-000 not found in snapshot. Run 01_load_telemetry.py first.")
    exit(1)

sat = sats["SAT-000"]
print(f"SAT-000 current position: lat={sat['lat']:.1f} lon={sat['lon']:.1f} fuel={sat['fuel_kg']:.1f}kg")

# Re-send full telemetry with a threat debris placed 150m from SAT-000
# We use the known Walker Delta initial position for SAT-000
r_sat = 6921.0
v_sat = math.sqrt(398600.4418 / r_sat)
angle = 0.0  # SAT-000 starts at angle=0
inc = math.radians(53.0)

# SAT-000 position & velocity
sat_r = [r_sat, 0.0, 0.0]
sat_v = [0.0, v_sat * math.cos(inc), v_sat * math.sin(inc)]

# Threat debris: same position + 0.150 km offset in radial direction, slightly higher speed
threat = {
    "id": "DEB-THREAT",
    "type": "DEBRIS",
    "r": {"x": sat_r[0] + 0.150, "y": sat_r[1], "z": sat_r[2]},
    "v": {"x": sat_v[0], "y": -sat_v[1] * 1.001, "z": -sat_v[2] * 1.001}
}

objects = []
for i in range(50):
    r = r_sat
    v = v_sat
    a = 2 * math.pi * i / 50
    objects.append({
        "id": f"SAT-{i:03d}", "type": "SATELLITE",
        "r": {"x": r*math.cos(a), "y": r*math.sin(a)*math.cos(inc), "z": r*math.sin(a)*math.sin(inc)},
        "v": {"x": -v*math.sin(a), "y": v*math.cos(a)*math.cos(inc), "z": v*math.cos(a)*math.sin(inc)}
    })

import random
random.seed(42)
for i in range(999):
    alt = 500 + random.uniform(-80, 80)
    rr = 6371 + alt
    vv = math.sqrt(398600.4418 / rr)
    aa = random.uniform(0, 2*math.pi)
    ii = math.radians(random.uniform(40, 70))
    objects.append({
        "id": f"DEB-{i:05d}", "type": "DEBRIS",
        "r": {"x": rr*math.cos(aa), "y": rr*math.sin(aa)*math.cos(ii), "z": rr*math.sin(aa)*math.sin(ii)},
        "v": {"x": -vv*math.sin(aa), "y": vv*math.cos(aa)*math.cos(ii), "z": vv*math.cos(aa)*math.sin(ii)}
    })

objects.append(threat)

print("Injecting 50 sats + 999 debris + 1 THREAT debris at 150m from SAT-000...")
r = session.post(f"{BASE}/api/telemetry",
                 json={"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "objects": objects},
                 timeout=30)
print(f"Telemetry: {r.status_code} | {r.json()}")

print("\nRunning 3 steps to trigger conjunction detection + evasion planning...")
for i in range(3):
    t0 = time.time()
    rr = session.post(f"{BASE}/api/simulate/step",
                      json={"step_seconds": 60}, timeout=60)
    ms = (time.time() - t0) * 1000
    resp = rr.json()
    print(f"  Step {i+1}: {ms:.0f}ms | maneuvers_executed={resp.get('maneuvers_executed',0)}")

status = session.get(f"{BASE}/api/status", timeout=10).json()
print(f"\nAfter injection:")
print(f"  CDM warnings: {status['active_cdm_warnings']}")
print(f"  Critical: {status['critical_conjunctions']}")
print(f"  Maneuvers queued: {status['maneuvers_queued']}")
print()

burns = status.get("scheduled_burns", [])
if burns:
    print("Scheduled burns:")
    for b in burns:
        btype = b.get("burn_type", "?")
        bt = b.get("burn_time_s", 0)
        dv = b.get("dv_magnitude_m_s", b.get("dv_magnitude_km_s", 0))
        print(f"  {b.get('burn_id','?')}: {btype} at sim_t={bt:.0f}s, dv={dv:.3f}")
else:
    print("No burns queued yet. Check recent_events:")
    for ev in status.get("recent_events", [])[:5]:
        print(f"  {ev}")

print("\nWatch the Gantt Timeline — EVASION and RECOVERY bars should appear for SAT-000")
