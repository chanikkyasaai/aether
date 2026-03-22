"""
DEMO STEP 1 — Load 50 satellites + 1000 debris
Run: python3 demo_scripts/01_load_telemetry.py
"""
import requests, math, random
from datetime import datetime, timezone

BASE = "http://localhost:8000"
random.seed(42)

def make_sat(i):
    r = 6921.0  # 550 km altitude
    v = math.sqrt(398600.4418 / r)
    angle = 2 * math.pi * i / 50
    inc = math.radians(53.0)
    return {
        "id": f"SAT-{i:03d}", "type": "SATELLITE",
        "r": {"x": r*math.cos(angle),
              "y": r*math.sin(angle)*math.cos(inc),
              "z": r*math.sin(angle)*math.sin(inc)},
        "v": {"x": -v*math.sin(angle),
              "y": v*math.cos(angle)*math.cos(inc),
              "z": v*math.cos(angle)*math.sin(inc)}
    }

def make_deb(i):
    alt = 500 + random.uniform(-80, 80)
    r = 6371 + alt
    v = math.sqrt(398600.4418 / r)
    angle = random.uniform(0, 2*math.pi)
    inc = math.radians(random.uniform(40, 70))
    return {
        "id": f"DEB-{i:05d}", "type": "DEBRIS",
        "r": {"x": r*math.cos(angle),
              "y": r*math.sin(angle)*math.cos(inc),
              "z": r*math.sin(angle)*math.sin(inc)},
        "v": {"x": -v*math.sin(angle),
              "y": v*math.cos(angle)*math.cos(inc),
              "z": v*math.cos(angle)*math.sin(inc)}
    }

objects = [make_sat(i) for i in range(50)] + [make_deb(i) for i in range(1000)]
payload = {
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "objects": objects
}

print("Sending telemetry: 50 satellites + 1000 debris...")
r = requests.post(f"{BASE}/api/telemetry", json=payload, timeout=30)
resp = r.json()
print(f"Status: {r.status_code}")
print(f"Processed: {resp['processed_count']} objects")
print(f"Active CDM warnings: {resp['active_cdm_warnings']}")
print()
print("Dashboard updated. Check http://localhost:8000")
