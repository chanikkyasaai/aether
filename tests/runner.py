#!/usr/bin/env python3
"""
AETHER Test Suite Runner
========================
Runs all tests programmatically, prints a score summary by grader category,
and reports pass rates and estimated scores.

Usage:
    python tests/runner.py [--url http://localhost:8000] [--verbose] [--no-slow]
"""

import sys
import os
import argparse
import time
import requests

# Ensure project root is on the path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ── Grader category definitions ───────────────────────────────────────────────

CATEGORIES = [
    {
        "name": "Safety",
        "weight": 0.25,
        "paths": ["tests/grader/test_safety_score.py"],
        "description": "Autonomous collision avoidance: CDM detection + evasion burns",
    },
    {
        "name": "Fuel",
        "weight": 0.20,
        "paths": ["tests/grader/test_fuel_score.py"],
        "description": "Fuel efficiency: Tsiolkovsky accuracy + non-negativity",
    },
    {
        "name": "Uptime",
        "weight": 0.15,
        "paths": ["tests/grader/test_uptime_score.py"],
        "description": "Server reliability: sequential + concurrent + large payload",
    },
    {
        "name": "Speed",
        "weight": 0.15,
        "paths": ["tests/grader/test_speed_score.py"],
        "description": "Latency: step / status / snapshot / telemetry response times",
    },
    {
        "name": "Physics",
        "weight": 0.15,
        "paths": ["tests/physics/"],
        "description": "Orbital propagation accuracy + Tsiolkovsky equation",
    },
    {
        "name": "API",
        "weight": 0.10,
        "paths": ["tests/api/"],
        "description": "REST contract tests: telemetry / simulate / maneuver / snapshot / status",
    },
    {
        "name": "Scenarios",
        "weight": 0.00,  # informational — not in explicit grader breakdown
        "paths": ["tests/scenarios/"],
        "description": "End-to-end: basic / fleet / edge cases / stress",
    },
]


# ── Server health check ───────────────────────────────────────────────────────

def check_server(base_url: str) -> bool:
    """Return True if the server is reachable and healthy."""
    try:
        r = requests.get(f"{base_url}/api/status", timeout=5)
        if r.status_code == 200:
            body = r.json()
            print(f"  Server OK — system={body.get('system', '?')}, "
                  f"sats={body.get('satellites_tracked', '?')}, "
                  f"debris={body.get('debris_tracked', '?')}")
            return True
        else:
            print(f"  Server returned HTTP {r.status_code}")
            return False
    except Exception as exc:
        print(f"  Server unreachable: {exc}")
        return False


# ── Pytest runner ─────────────────────────────────────────────────────────────

def run_category(category: dict, base_url: str, verbose: bool,
                 include_slow: bool) -> dict:
    """
    Run pytest for a single category. Returns a result dict with pass/fail counts.
    """
    import pytest

    args = []
    for path in category["paths"]:
        full_path = os.path.join(ROOT, path)
        if os.path.exists(full_path):
            args.append(full_path)
        else:
            print(f"    WARNING: path not found: {full_path}")

    if not args:
        return {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "total": 0}

    if not include_slow:
        args += ["-m", "not slow"]

    if verbose:
        args += ["-v"]
    else:
        args += ["-q", "--tb=line"]

    args += [
        f"--rootdir={ROOT}",
        "--no-header",
        "-p", "no:cacheprovider",
    ]

    # Inject base URL via env var
    env_backup = os.environ.get("AETHER_URL")
    os.environ["AETHER_URL"] = base_url

    class ResultCollector:
        def __init__(self):
            self.passed = 0
            self.failed = 0
            self.error = 0
            self.skipped = 0

    collector = ResultCollector()

    class Plugin:
        def pytest_runtest_logreport(self, report):
            if report.when == "call":
                if report.passed:
                    collector.passed += 1
                elif report.failed:
                    collector.failed += 1
                elif report.skipped:
                    collector.skipped += 1
            elif report.when == "setup" and report.failed:
                collector.error += 1

    plugin = Plugin()
    t0 = time.perf_counter()
    exit_code = pytest.main(args, plugins=[plugin])
    elapsed = time.perf_counter() - t0

    if env_backup is None:
        os.environ.pop("AETHER_URL", None)
    else:
        os.environ["AETHER_URL"] = env_backup

    total = collector.passed + collector.failed + collector.error
    return {
        "passed": collector.passed,
        "failed": collector.failed,
        "error": collector.error,
        "skipped": collector.skipped,
        "total": total,
        "elapsed_s": elapsed,
        "exit_code": exit_code,
    }


# ── Score display ─────────────────────────────────────────────────────────────

def print_separator(char="─", width=72):
    print(char * width)


def print_score_summary(results: list):
    print()
    print_separator("═")
    print("  AETHER GRADER SCORE SUMMARY")
    print_separator("═")

    total_weighted = 0.0
    total_weight = 0.0

    fmt = "  {:<12} {:>6}  {:>5}/{:<5}  {:>7}  {:>8}"
    print(fmt.format("Category", "Weight", "Pass", "Total", "Rate%", "Est.Score"))
    print_separator()

    for cat, res in results:
        weight = cat["weight"]
        total = res["total"]
        passed = res["passed"]
        rate = (passed / total * 100) if total > 0 else 0.0
        est = weight * rate / 100
        total_weighted += est
        if weight > 0:
            total_weight += weight

        bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
        skipped = res.get("skipped", 0)
        skip_note = f" (+{skipped} skipped)" if skipped else ""

        print(fmt.format(
            cat["name"],
            f"{weight*100:.0f}%",
            passed,
            f"{total}{skip_note}",
            f"{rate:.1f}%",
            f"{est*100:.1f}%"
        ))

    print_separator()

    # Normalise for categories with weight > 0
    if total_weight > 0:
        normalised = total_weighted / total_weight
    else:
        normalised = 0.0

    print(f"  ESTIMATED TOTAL SCORE:  {total_weighted*100:.1f}% "
          f"(normalised to weighted categories: {normalised*100:.1f}%)")
    print_separator("═")

    # Per-category details
    print()
    print("  CATEGORY DETAILS")
    print_separator()
    for cat, res in results:
        elapsed = res.get("elapsed_s", 0)
        status = "PASS" if res["failed"] == 0 and res["error"] == 0 else "FAIL"
        skipped = res.get("skipped", 0)
        print(f"  [{status}] {cat['name']:<12}  "
              f"{res['passed']}/{res['total']} passed  "
              f"({res['failed']} failed, {res['error']} errors, {skipped} skipped)  "
              f"[{elapsed:.1f}s]")
        print(f"         {cat['description']}")
    print_separator()
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AETHER test suite runner")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Backend URL (default: http://localhost:8000)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose pytest output")
    parser.add_argument("--no-slow", action="store_true",
                        help="Skip tests marked with @pytest.mark.slow")
    parser.add_argument("--category", "-c", default=None,
                        help="Run only this category (e.g. Safety, Speed, API)")
    args = parser.parse_args()

    print()
    print("=" * 72)
    print("  AETHER NSH 2026 — Test Suite Runner")
    print("=" * 72)
    print()

    # ── Step 1: Server health check ──────────────────────────────────────────
    print("Step 1: Checking server health...")
    server_ok = check_server(args.url)
    if not server_ok:
        print()
        print("  ERROR: Server is not reachable. Start the backend first:")
        print("    uvicorn acm.main:app --host 0.0.0.0 --port 8000")
        print("  Also ensure TEST_MODE=1 is set in the server environment.")
        sys.exit(1)
    print()

    # ── Step 2: Run tests by category ───────────────────────────────────────
    print("Step 2: Running test categories...")
    print()

    categories_to_run = CATEGORIES
    if args.category:
        categories_to_run = [
            c for c in CATEGORIES
            if c["name"].lower() == args.category.lower()
        ]
        if not categories_to_run:
            print(f"  ERROR: Unknown category '{args.category}'. "
                  f"Valid: {[c['name'] for c in CATEGORIES]}")
            sys.exit(1)

    results = []
    for cat in categories_to_run:
        print(f"  Running: {cat['name']} ({cat['weight']*100:.0f}% weight) ...")
        res = run_category(cat, args.url, args.verbose,
                           include_slow=not args.no_slow)
        results.append((cat, res))
        status = "OK" if res["failed"] == 0 and res["error"] == 0 else "FAILURES"
        print(f"    → {res['passed']}/{res['total']} passed [{status}] "
              f"in {res.get('elapsed_s', 0):.1f}s")
        print()

    # ── Step 3: Print score summary ──────────────────────────────────────────
    print("Step 3: Score summary")
    print_score_summary(results)

    # Exit with non-zero if any test failed
    any_failure = any(
        r["failed"] > 0 or r["error"] > 0 for _, r in results
    )
    sys.exit(1 if any_failure else 0)


if __name__ == "__main__":
    main()
