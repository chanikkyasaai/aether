"""
DEMO STEP 5 — Speed test with 10,000 debris (grader scenario)
Run: python3 demo_scripts/05_speed_test.py
"""
import requests, time, math, random
from datetime import datetime, timezone

BASE = "http://localhost:8000"
session = requests.Session()

random.seed(99)

print("Loading 50 satellites + 10,000 debris...")
objects = []

for i in range(50):
    r = 6921.0
    v = math.sqrt(398600.4418 / r)
    a = 2 * math.pi * i / 50
    inc = math.radians(53.0)
    objects.append({
        "id": f"SAT-{i:03d}", "type": "SATELLITE",
        "r": {"x": r*math.cos(a), "y": r*math.sin(a)*math.cos(inc), "z": r*math.sin(a)*math.sin(inc)},
        "v": {"x": -v*math.sin(a), "y": v*math.cos(a)*math.cos(inc), "z": v*math.cos(a)*math.sin(inc)}
    })

for i in range(10000):
    alt = 500 + random.uniform(-100, 100)
    r = 6371 + alt
    v = math.sqrt(398600.4418 / r)
    a = random.uniform(0, 6.283)
    inc = math.radians(random.uniform(35, 75))
    objects.append({
        "id": f"DEB-{i:05d}", "type": "DEBRIS",
        "r": {"x": r*math.cos(a), "y": r*math.sin(a)*math.cos(inc), "z": r*math.sin(a)*math.sin(inc)},
        "v": {"x": -v*math.sin(a), "y": v*math.cos(a)*math.cos(inc), "z": v*math.cos(a)*math.sin(inc)}
    })

t0 = time.time()
session.post(f"{BASE}/api/telemetry",
             json={"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "objects": objects},
             timeout=60)
print(f"Telemetry ingest: {(time.time()-t0)*1000:.0f} ms")

print("\nWarm-up step (first step after load)...")
t0 = time.time()
r = session.post(f"{BASE}/api/simulate/step", json={"step_seconds": 60}, timeout=120)
warmup_ms = (time.time()-t0)*1000
print(f"  Warm-up: {warmup_ms:.0f} ms")

print("\nBenchmark: 5 steps × 60s with 10,000 debris")
print("-" * 50)
times = []
for k in range(5):
    t0 = time.time()
    r = session.post(f"{BASE}/api/simulate/step", json={"step_seconds": 60}, timeout=120)
    ms = (time.time()-t0)*1000
    times.append(ms)
    resp = r.json()
    print(f"  Step {k+1}: {ms:.0f} ms | maneuvers={resp.get('maneuvers_executed',0)}")

print("-" * 50)
mean_ms = sum(times) / len(times)
print(f"Mean:   {mean_ms:.0f} ms")
print(f"Max:    {max(times):.0f} ms")
print(f"Target: 500 ms")
print(f"Result: {'PASS ✓' if max(times) < 500 else 'SLOW - ' + str(round(max(times))) + 'ms'}")
