"""
DEMO STEP 2 — Run 10 simulation steps and show performance
Run: python3 demo_scripts/02_run_steps.py
"""
import requests, time

BASE = "http://localhost:8000"
session = requests.Session()

print("Running 10 simulation steps (60s each)...")
print("-" * 65)

for i in range(10):
    t0 = time.time()
    r = session.post(f"{BASE}/api/simulate/step",
                     json={"step_seconds": 60},
                     timeout=60)
    ms = (time.time() - t0) * 1000
    resp = r.json()
    status = resp.get("status", "?")
    maneuvers = resp.get("maneuvers_executed", 0)
    collisions = resp.get("collisions_detected", 0)
    print(f"  Step {i+1:2d}: {ms:6.0f} ms | {status} | maneuvers={maneuvers} | collisions={collisions}")
    time.sleep(0.2)

print("-" * 65)

status = session.get(f"{BASE}/api/status", timeout=10).json()
print(f"\nFleet summary:")
print(f"  Satellites tracked  : {status['satellites_tracked']}")
print(f"  Debris tracked      : {status['debris_tracked']}")
print(f"  Fleet fuel remaining: {status['fleet_fuel_remaining_kg']:.1f} kg")
print(f"  Active CDM warnings : {status['active_cdm_warnings']}")
print(f"  Critical conjunctions: {status['critical_conjunctions']}")
print(f"  Maneuvers queued    : {status['maneuvers_queued']}")
print(f"  Total collisions    : {status['total_collisions']}")
