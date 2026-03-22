"""
DEMO STEP 4 — Show the structured audit log
Run: python3 demo_scripts/04_show_logs.py
"""
import json, os

log_path = os.path.join(os.path.dirname(__file__), "..", "logs", "acm_audit.jsonl")

if not os.path.exists(log_path):
    print("No log file found. Run steps first.")
    exit(1)

with open(log_path) as f:
    lines = [l.strip() for l in f if l.strip()]

print(f"Total events logged: {len(lines)}")
print()

if not lines:
    print("Log is empty — no events yet. Run 02_run_steps.py and 03_inject_threat.py first.")
    exit(0)

# Show last 8 events formatted
print("Last events:")
print("=" * 70)
for line in lines[-8:]:
    try:
        ev = json.loads(line)
        print(json.dumps(ev, indent=2))
        print("-" * 70)
    except json.JSONDecodeError:
        print(line)

# Count by event type
from collections import Counter
types = Counter()
for line in lines:
    try:
        types[json.loads(line).get("event_type", "UNKNOWN")] += 1
    except Exception:
        pass

print("\nEvent type summary:")
for et, count in sorted(types.items()):
    print(f"  {et:30s} {count:4d}")
