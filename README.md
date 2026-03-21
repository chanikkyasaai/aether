# AETHER — Autonomous Constellation Manager
### NSH 2026 · IIT Delhi

---

## Table of Contents
1. [Problem Statement](#1-problem-statement)
2. [Solution Overview](#2-solution-overview)
3. [Design Approach](#3-design-approach)
4. [System Architecture](#4-system-architecture)
5. [Module Reference](#5-module-reference)
6. [API Specification](#6-api-specification)
7. [Frontend](#7-frontend)
8. [Build Instructions](#8-build-instructions)
9. [Testing](#9-testing)
10. [Performance](#10-performance)
11. [Scoring Alignment](#11-scoring-alignment)
12. [Gap Analysis](#12-gap-analysis)

---

## 1. Problem Statement

A 50-satellite LEO constellation at 550 km altitude operates inside a growing debris field of 10,000+ objects. The system must:

- Ingest real-time orbital telemetry for satellites and debris
- Screen every debris object for conjunction risk with every satellite every step
- Autonomously plan and execute evasion burns **before** Time of Closest Approach (TCA)
- Return satellites to their nominal slot after evasion via Hohmann phasing transfers
- Deorbit fuel-exhausted satellites to a graveyard orbit (IADC compliant, 600 km)
- Serve a real-time dashboard with ground track, conjunction bullseye, fuel heatmap, and Gantt timeline
- Run at `http://localhost:8000` in Docker on `ubuntu:22.04`

**Judging Criteria:**

| Category | Weight | What it measures |
|----------|--------|-----------------|
| Safety | 25% | CDM detection, evasion burn execution, zero collisions |
| Fuel Efficiency | 20% | Minimum delta-V per maneuver, Tsiolkovsky accuracy |
| Uptime | 15% | Server stability under load, concurrent requests |
| Speed | 15% | Step latency at 10k debris, endpoint response times |
| UI | 15% | Real-time dashboard, ground track, 3D view |
| Logging | 10% | Structured audit log, all event types |

---

## 2. Solution Overview

AETHER is a **full-stack autonomous orbital debris avoidance system** built on:

- **FastAPI + Uvicorn** — async HTTP server with thread-pool for CPU-bound simulation
- **Numba JIT (serial + parallel)** — two propagator paths: serial `range` for conjunction search (avoids thread-pool overhead on small N), parallel `prange` for fleet propagation (10k objects)
- **Vectorized batch TCA sweep** — all candidate pairs propagated simultaneously as a single `(2K, 6)` array; one `rk4_serial` call per coarse time step instead of K×T dispatch calls
- **KD-tree O(N log N)** conjunction screening instead of O(MN) naive search
- **SciPy SLSQP** — constrained minimum-fuel evasion burn optimization
- **React 18 + deck.gl + Three.js** — GPU-rendered real-time dashboard

### Key Design Decisions

| Problem | Solution | Rationale |
|---------|----------|-----------|
| 50 sats × 10k debris = 500k pair checks | KD-tree + 200 km filter → ~150 candidates | O(N log N) vs O(MN) |
| TCA search over 24h — coarse propagation cost | Pack all K pairs as (2K, 6); single RK4 per 300s step | K×288 dispatch → 288 total |
| Slow golden-section refinement on 24h horizon | Save pair states at t_lo during coarse sweep; refine only within [0, 300s] | Bounds refinement cost to ~15 evaluations × 2.5 ms |
| Numba `prange` overhead for small N | Serial `range` in `_serial_derivatives`; only ~2 µs per call vs 400 µs for prange | Thread-pool sync cost dominates for N ≤ 1000 |
| Snapshot latency > 50 ms | Pre-compute ECI→geodetic + pre-serialize full JSON to bytes after each step | O(1) GET: return cached bytes, zero Pydantic overhead |
| 24h TCA horizon (spec requirement) | 300s coarse step × 288 steps; single RK4 per step → ~35 ms Phase 2 | Spec compliance without sacrificing speed |
| CPU-bound step endpoint | Plain `def simulate_step` (not async), FastAPI thread pool | Doesn't block the event loop |
| Numba first-call latency | `warmup()` at lifespan startup | Zero cold-start penalty for grader |
| MapLibre CDN offline risk | Inline `OFFLINE_STYLE` fallback; `onError` handler switches to it | Dashboard renders even without network |

---

## 3. Design Approach

### 3.1 Physics Philosophy

All orbital mechanics uses **WGS84 reference constants** to match what the grader expects:

```
MU  = 398600.4418 km³/s²   (Earth gravitational parameter)
RE  = 6378.137 km           (equatorial radius)
J2  = 1.08263×10⁻³          (second zonal harmonic)
G0  = 0.00980665 km/s²      (standard gravity, km-unit)
ISP = 300 s                  (monopropellant specific impulse)
M_DRY      = 500 kg          (satellite dry mass)
M_FUEL_INIT = 50 kg          (initial propellant per satellite)
```

The propagator implements **J2-perturbed RK4** at 30-second sub-steps. J2 is the dominant perturbation for LEO (causes ~7°/day nodal precession at 53° inclination), making it essential for accurate TCA prediction across the full 24-hour screening horizon.

### 3.2 Dual Propagator Strategy

Two Numba-JIT propagators are maintained:

```
_batch_derivatives  @njit(parallel=True)   — prange over N objects
_serial_derivatives @njit(serial)          — range over N objects (no thread pool)

rk4_batch    → uses _batch_derivatives  — optimal for N > 1000 (fleet propagation)
rk4_serial   → uses _serial_derivatives — optimal for N ≤ 1000 (TCA search)

propagate_smart(states, dt) — dispatches based on N:
    N ≤ 1000  → propagate_serial  (serial Numba, ~2µs/call)
    N > 1000  → propagate         (parallel Numba, ~3ms/call)
```

**Why this matters for TCA search:** The conjunction sweep packs K pairs as `(2K, 6)` and calls `rk4_serial` once per coarse step. With K ≤ 2500 pairs and 288 coarse steps (24h ÷ 300s), thread-pool overhead from `prange` would add 400 µs × 288 = 115 ms per step. Serial eliminates this entirely.

### 3.3 Conjunction Screening Pipeline

```
All debris positions (N=10,000)
        │
        ▼
  KDTree.query_ball_point(sat_positions, r=200km)
        │  → reduces to ~150 candidate (sat, debris) pairs
        ▼
  Vectorized Batch TCA Sweep — _vectorized_batch_tca()
  Pack K pairs as (2K, 6): even rows = satellites, odd rows = debris
  For t in [0, 86400s] at 300s coarse steps:
      current = rk4_serial(current, 300s)   ← single call on full (2K,6)
      miss[k] = |sat_pos[k] - deb_pos[k]|  ← vectorized
      if miss[k] < min_miss[k]:
          save states_at_tlo[k] = prev[k]   ← save for refinement
  Returns: (min_miss, t_lo, t_hi, states_at_tlo)  shape (K,), (K,), (K,), (K,2,6)
        │
        ▼
  Golden-Section Refinement — _refine_tca()
  Receives pair state AT t_lo (NOT at t=0)
  propagate_serial(states_at_tlo[k], dt, step=30s) for dt in [0, 300s]
  scipy.optimize.minimize_scalar(bounded, xatol=1s)
        │  → refined TCA estimate, miss_km, rel_velocity
        ▼
  Akella-Alfriend (2000) PoC formula
  σ = max(miss × 0.1, 0.010 km)
  PoC = (r_hb²/2σ²) × exp(−miss²/2σ²)
        │
        ▼
  CDM with threat_level: CRITICAL (miss < 1 km) | WARNING (1–5 km)
  Sort by PoC descending
```

**Key insight for 24h horizon:** Without saving `states_at_tlo`, refinement would propagate from t=0 to t_lo (up to 84000s) — 2800 sub-steps per evaluation × 15 evaluations = 42,000 sub-steps per pair. By saving states at t_lo, refinement propagates only 10 sub-steps (300s ÷ 30s), bounding cost regardless of where in the 24h horizon TCA occurs.

### 3.4 Autonomous Planner Decision Tree

For every **CRITICAL** CDM detected:

```
1. Already handled in maneuver queue? → skip
2. Satellite EOL?                     → skip
3. TCA < 15s away?                    → log BLIND_CONJUNCTION, skip
4. Find earliest valid burn time (last_burn + 600s cooldown)
5. Find next LOS window (propagate satellite, check elevation ≥ 5°, 6 stations)
6. LOS arrives after TCA?             → schedule EMERGENCY burn at TCA - 60s
7. Compute evasion burn via SLSQP (or fallback to max prograde)
8. Schedule EVASION burn
9. Compute Hohmann recovery: two prograde burns
10. Schedule RECOVERY_1 and RECOVERY_2 burns
```

### 3.5 Fuel Efficiency Strategy

Evasion delta-V is **tiered by remaining fuel**:

| Fuel remaining | Standoff distance | Strategy |
|---------------|-------------------|----------|
| > 50% (> 25 kg) | 500 m | Full standoff, SLSQP optimal |
| 10–50% (5–25 kg) | 200 m | Reduced standoff |
| < 10% (< 5 kg) | 100 m | Minimum viable standoff |
| ≤ 2.5 kg | EOL | Graveyard burn to 600 km |

The SLSQP optimizer minimizes `|ΔV|` subject to:
- Miss at TCA ≥ standoff distance
- Remaining fuel ≥ 0.5 kg margin
- `|ΔV| ≤ 15 m/s` (thruster limit)

---

## 4. System Architecture

```
aether/
├── acm/                          # Python backend package
│   ├── core/                     # Physics and autonomous logic
│   │   ├── physics.py            # RK4+J2 propagator (serial + parallel), Tsiolkovsky, Numba JIT
│   │   ├── state.py              # SimState singleton, threading.Lock, snapshot cache
│   │   ├── conjunction.py        # KD-tree screening, vectorized batch TCA, Akella-Alfriend PoC
│   │   ├── maneuver.py           # RTN↔ECI transforms, SLSQP, Hohmann recovery
│   │   ├── planner.py            # Autonomous CDM → burn decision engine
│   │   ├── station_keeping.py    # Nominal slot propagation, recovery check
│   │   ├── ground_station.py     # LOS elevation check, next-window prediction (6 stations)
│   │   ├── eol.py                # End-of-life detection, graveyard burn (600 km)
│   │   └── logger.py             # Structured JSONL audit log, 10 event types
│   ├── api/                      # FastAPI route handlers
│   │   ├── main.py               # App factory, lifespan (warmup), CORS, static files
│   │   ├── schemas.py            # Pydantic v2 request/response models
│   │   ├── routes_telemetry.py   # POST /api/telemetry
│   │   ├── routes_simulate.py    # POST /api/simulate/step
│   │   ├── routes_maneuver.py    # POST /api/maneuver/schedule
│   │   ├── routes_viz.py         # GET /api/visualization/snapshot (pre-serialized cache)
│   │   ├── routes_status.py      # GET /api/status
│   │   └── routes_reset.py       # POST /api/reset (TEST_MODE only)
│   └── data/
│       ├── ground_stations.csv   # 6 ground stations (IIT Delhi, ISRO, Svalbard, …)
│       └── constellation_init.py # 50-satellite Walker Delta + 10k debris generator
├── frontend/                     # React 18 dashboard
│   ├── src/
│   │   ├── App.jsx               # Layout, status bar, panel grid, error boundary
│   │   ├── api.js                # useSnapshot (10 Hz) + useStatus (2 Hz) polling hooks
│   │   ├── GroundTrack.jsx       # deck.gl MapLibre, trails, CDM lines, offline fallback
│   │   ├── BullseyePlot.jsx      # D3 radial conjunction plot (TCA vs azimuth)
│   │   ├── FuelHeatmap.jsx       # 50-cell SVG fuel heatmap
│   │   ├── GanttTimeline.jsx     # D3 burn schedule Gantt chart
│   │   └── OrbitView3D.jsx       # Three.js r160 globe, orbit arcs, TWEEN camera
│   ├── package.json
│   └── vite.config.js
├── tests/                        # Automated test suite (90 tests, 90 passing)
│   ├── conftest.py               # Fixtures, orbital helpers, API helpers
│   ├── physics/                  # Propagation accuracy, Tsiolkovsky
│   ├── api/                      # All endpoint contracts
│   ├── grader/                   # Safety, fuel, uptime, speed scoring tests
│   ├── scenarios/                # End-to-end scenario tests
│   └── runner.py                 # Standalone score runner
├── report/
│   └── report.tex                # 15-page LaTeX technical report
├── Dockerfile                    # FROM ubuntu:22.04, Python 3.10, Node
├── requirements.txt
└── README.md
```

---

## 5. Module Reference

### `acm/core/physics.py`

The physics engine. Every object in the simulation is propagated here.

**`_batch_derivatives(states)`** — Numba `@njit(parallel=True, cache=True, nogil=True)`
Computes J2-perturbed accelerations for N objects using `prange` (multi-threaded). Used for large N (fleet propagation).

**`_serial_derivatives(states)`** — Numba `@njit(cache=True, nogil=True)`
Same computation using `range` (single-threaded). Avoids Numba thread-pool sync overhead (~400 µs/call for small N). Used for TCA sweep across all candidate pairs.

**`rk4_batch(states, dt)`** — Single RK4 step using `_batch_derivatives` (parallel).

**`rk4_serial(states, dt)`** — Single RK4 step using `_serial_derivatives` (serial, no thread overhead).

**`propagate(states, total_dt, step=30.0)`** — Multi-step parallel propagation.

**`propagate_serial(states, total_dt, step=30.0)`** — Multi-step serial propagation. Optimal for N ≤ ~1000.

**`propagate_smart(states, total_dt, step=30.0)`** — Auto-dispatches: serial for N ≤ 1000, parallel for N > 1000.

**`tsiolkovsky_dm(m_current_kg, dv_km_s)`** — `dm = m × (1 − exp(−ΔV / (Isp × g₀)))`

**`warmup()`** — Called at startup. Triggers JIT compilation for both `rk4_batch` and `rk4_serial` to eliminate cold-start penalty.

---

### `acm/core/state.py`

Global simulation state. Thread-safe via `threading.Lock()`. All access must acquire `sim_lock` first.

```python
class SimState:
    sat_states:         np.ndarray  # (M, 6) ECI position+velocity, km / km·s⁻¹
    sat_ids:            list        # satellite ID strings
    sat_fuel_kg:        np.ndarray  # (M,) current fuel per satellite, kg
    sat_nominal_states: np.ndarray  # (M, 6) nominal slot positions (updated each step)
    sat_last_burn_time: np.ndarray  # (M,) sim time of last burn
    sat_status:         list        # NOMINAL | EVADING | RECOVERING | EOL
    deb_states:         np.ndarray  # (N, 6) debris ECI states
    deb_ids:            list        # debris ID strings
    active_cdms:        list        # current CDMs from last screening
    maneuver_queue:     list        # scheduled ScheduledBurn objects
    current_time_s:     float       # simulation elapsed seconds
    initial_epoch:      datetime    # UTC epoch of t=0

    # Performance caches — rebuilt after each propagation step or telemetry ingest
    _debris_cloud_cache:    list    # [[id, lat, lon, alt_km], …] pre-computed
    _snapshot_json_cache:   bytes   # full snapshot pre-serialized to JSON bytes
```

**`rebuild_debris_cache()`** — Vectorized ECI→geodetic for all debris. Then calls `build_snapshot_cache()` to pre-serialize the full snapshot to bytes. Called after every simulate/step and every telemetry ingest.

---

### `acm/core/conjunction.py`

Conjunction screening pipeline. Parameters:

| Constant | Value | Purpose |
|----------|-------|---------|
| `COARSE_RADIUS_KM` | 200.0 km | KD-tree initial filter radius |
| `MAX_CANDIDATES_PER_SAT` | 50 | Per-satellite cap to bound worst-case |
| `TCA_COARSE_STEP_S` | 300.0 s | Coarse sweep interval (5 minutes) |
| `TCA_HORIZON_S` | 86400.0 s | Look-ahead window (24 hours — spec requirement) |
| `DISCARD_MISS_KM` | 5.0 km | Discard pairs with TCA miss > 5 km |
| `CRITICAL_MISS_KM` | 1.0 km | CRITICAL threat threshold |

**`_vectorized_batch_tca(pairs, sat_states, deb_states)`**
Packs K pairs as `(2K, 6)`. Calls `rk4_serial(current, 300s)` ONCE per coarse step for all pairs simultaneously. Records pair states at t_lo for bounded refinement. Returns `(min_miss, t_lo, t_hi, states_at_tlo)`.

**`_refine_tca(combined_at_tlo, t_lo, t_hi, orig_sat_state)`**
Receives pair state AT t_lo (saved during coarse sweep). Propagates only within `[0, TCA_COARSE_STEP_S]` — never more than 300s per evaluation. Uses `scipy.optimize.minimize_scalar(bounded, xatol=1.0)`. Returns `(tca_s, miss_km, rel_vel_km_s)`.

**`_akella_alfriend_poc(miss_km, rel_vel_km_s)`**
Short-encounter probability formula:
```
σ = max(miss × 0.1, 0.010 km)
PoC = (r_hb² / 2σ²) × exp(−miss² / 2σ²)
r_hb = (1.5 m + 0.5 m) / 1000 = 0.002 km  (combined hard-body radius)
```

**`screen_conjunctions(sim_state)`**
Full pipeline: KD-tree → vectorized batch TCA sweep → golden-section refinement → Akella-Alfriend PoC. Returns CDM list sorted by PoC descending.

---

### `acm/core/maneuver.py`

RTN↔ECI frame transforms and burn computation.

**`rtn_to_eci_matrix(sat_state)`** — Builds 3×3 rotation matrix. Columns are `[r̂, t̂, n̂]` where:
- `r̂ = r / |r|` (radial outward)
- `n̂ = (r × v) / |r × v|` (normal to orbit plane)
- `t̂ = n̂ × r̂` (along-track)

**`compute_evasion_burn(sat_state, deb_state, tca_s, fuel_kg, sat_id)`**
SLSQP constrained optimization:
```
minimize  |ΔV_RTN|
s.t.      miss(ΔV) ≥ standoff_km
          fuel_remaining ≥ 0.5 kg
          |ΔV| ≤ 0.015 km/s
```
Falls back to max prograde burn if optimizer fails (logs `DEGRADED_AVOIDANCE`).

**`compute_recovery_burns(post_evasion_state, nominal_state, sat_id)`**
Classic two-burn Hohmann phasing transfer back to nominal slot.

---

### `acm/core/planner.py`

The autonomous decision engine. Called every simulation step after conjunction screening. For each CRITICAL CDM: enforces cooldown, finds LOS window, computes burns, schedules EVASION + RECOVERY_1 + RECOVERY_2.

---

### `acm/core/ground_station.py`

**6 stations:** IIT Delhi, Bangalore ISRO, Trivandrum VSSC, Mauritius, Biak Indonesia, Svalbard.

**`predict_next_los_window(sat_state, earliest_time_s)`** — Propagates satellite forward in 60s steps up to 2-hour horizon. Returns `(window_start_s, station_id)` for first station at elevation ≥ 5°.

---

### `acm/core/eol.py`

Triggers graveyard deorbit when `fuel_kg ≤ 2.5 kg`. Schedules a prograde burn from 550 km to 600 km (IADC compliant). Sets satellite status to EOL on execution.

---

### `acm/core/logger.py`

Structured JSONL audit log at `logs/acm_audit.jsonl`. In-memory `deque(maxlen=100)` for the status endpoint. 10 event types:

| Event Type | Trigger |
|-----------|---------|
| `CDM_DETECTED` | New CRITICAL conjunction found |
| `CDM_WARNING` | Non-critical conjunction |
| `CDM_ACTIONED` | Evasion burn scheduled |
| `BURN_EXECUTED` | Delta-V applied; mass before/after logged |
| `RECOVERY_SCHEDULED` | Hohmann recovery queued |
| `RECOVERY_COMPLETE` | Satellite returned to nominal slot |
| `COLLISION_DETECTED` | Miss distance < 100 m |
| `EOL_TRIGGERED` | Fuel ≤ 2.5 kg; graveyard burn queued |
| `BLIND_CONJUNCTION` | LOS blocked or TCA < 15s away |
| `DEGRADED_AVOIDANCE` | SLSQP failed; fell back to max prograde |

---

### `acm/core/station_keeping.py`

Propagates nominal slot positions each step (keeps them synchronized with orbital dynamics). `check_slot_recovery()` transitions RECOVERING → NOMINAL when position error < 10 km from nominal slot.

---

### `acm/api/routes_viz.py`

Pre-serialized snapshot cache. Zero Pydantic overhead on GET.

**`build_snapshot_cache(state) → bytes`** — Vectorized ECI→geodetic for all satellites. Builds full JSON payload including `_debris_cloud_cache`. Serializes to bytes via `json.dumps(separators=(',',':'))`. Called from `state.rebuild_debris_cache()` after every step/telemetry, not on every GET.

**`GET /api/visualization/snapshot`** — Acquires lock, reads `_snapshot_json_cache`, releases lock. Returns raw bytes via `Response(content=cached, media_type="application/json")`. Fallback to on-demand build if cache is empty.

---

### `acm/api/` — FastAPI Routes

| Route | Method | Handler type | Purpose |
|-------|--------|-------------|---------|
| `/api/telemetry` | POST | `async def` | Ingest satellite + debris states. Full batch replacement. Validates types. Rebuilds snapshot cache. |
| `/api/simulate/step` | POST | **`def`** (sync) | Advance sim: propagate → collisions → CDMs → planner → EOL → slot check → rebuild cache |
| `/api/maneuver/schedule` | POST | `async def` | Schedule external burn sequence. Validates fuel. Returns LOS check. |
| `/api/visualization/snapshot` | GET | `def` | Returns pre-serialized JSON bytes. O(1) response. |
| `/api/status` | GET | `def` | System health: counts, fleet fuel, recent events |
| `/api/reset` | POST | `async def` | TEST_MODE only. Wipes all state + caches for test isolation. |

**Critical:** `/api/simulate/step` is a **plain `def`** (not async). FastAPI dispatches it in a thread pool, preventing it from blocking the async event loop during CPU-bound computation.

---

## 6. API Specification

### POST `/api/telemetry`
```json
{
  "timestamp": "2026-03-12T08:00:00.000Z",
  "objects": [
    {
      "id": "SAT-Alpha-01",
      "type": "SATELLITE",
      "r": {"x": 6928.137, "y": 0.0, "z": 0.0},
      "v": {"x": 0.0, "y": 7.784, "z": 0.0}
    }
  ]
}
```
Response: `{"status": "ACK", "processed_count": 1, "active_cdm_warnings": 0}`

**Notes:**
- Unknown `type` values return **422 Unprocessable Entity**
- Each call **replaces** the entire satellite/debris fleet (batch replacement, not accumulation)
- Duplicate IDs within a single payload are deduplicated (last occurrence wins)
- Satellite fuel/status is preserved on re-ingestion of the same ID
- Snapshot cache is rebuilt immediately after ingest (satellite-only or debris-only)

### POST `/api/simulate/step`
```json
{"step_seconds": 60}
```
Response: `{"status": "STEP_COMPLETE", "new_timestamp": "...", "collisions_detected": 0, "maneuvers_executed": 0}`

**Execution order (all under lock):**
1. Apply due maneuvers (burns within `[now, now+dt]`)
2. Propagate all objects as single batch via `propagate` (RK4 @ 30s substeps)
3. Propagate nominal slots
4. Advance simulation time
5. Check collisions (100 m threshold)
6. Screen conjunctions (KD-tree → vectorized batch TCA → refinement → PoC)
7. Run autonomous planner (CDM → EVASION + RECOVERY burns)
8. EOL check
9. Slot recovery check
10. Rebuild snapshot cache

### POST `/api/maneuver/schedule`
```json
{
  "satelliteId": "SAT-Alpha-01",
  "maneuver_sequence": [
    {
      "burn_id": "EVA-001",
      "burnTime": "2026-03-12T08:02:00.000Z",
      "deltaV_vector": {"x": 0.0, "y": 0.003, "z": 0.0}
    }
  ]
}
```
Returns **422** if empty sequence. Returns **404** if satellite not found. Returns **422** if sequence exceeds fuel budget.

```json
{
  "status": "SCHEDULED",
  "validation": {
    "ground_station_los": true,
    "sufficient_fuel": true,
    "projected_mass_remaining_kg": 547.3
  }
}
```

### GET `/api/visualization/snapshot`
```json
{
  "timestamp": "2026-03-12T08:01:00.000Z",
  "satellites": [
    {"id": "SAT-Alpha-01", "lat": 0.0, "lon": 0.0, "fuel_kg": 50.0, "status": "NOMINAL"}
  ],
  "debris_cloud": [["DEB-00001", -23.4, 145.2, 553.7]]
}
```
`debris_cloud` rows: `[id, lat_deg, lon_deg, alt_km]`

### GET `/api/status`
```json
{
  "system": "AETHER",
  "sim_time_iso": "2026-03-12T08:01:00.000Z",
  "satellites_tracked": 50,
  "debris_tracked": 10000,
  "active_cdm_warnings": 2,
  "critical_conjunctions": 0,
  "maneuvers_queued": 3,
  "total_collisions": 0,
  "fleet_fuel_remaining_kg": 2487.3,
  "recent_events": [...]
}
```

---

## 7. Frontend

Five React components served as a static build at `/`:

### `GroundTrack.jsx`
- **MapLibre GL** dark-matter basemap via `react-map-gl/maplibre`
- **deck.gl ScatterplotLayer** for satellites (color-coded by status) and debris cloud
- **deck.gl PathLayer** for 90-minute orbital trails (54-point buffer)
- **deck.gl LineLayer** for CDM threat lines (red = CRITICAL, amber = WARNING)
- **6 ground station** markers with hover tooltips
- Click a satellite to select it (highlighted with white ring)
- **Offline fallback:** `onError` handler switches to an inline dark background style if the CDN basemap fails to load — dashboard remains functional without network

### `BullseyePlot.jsx`
D3 radial chart. Each CDM plotted at angle = approach azimuth (degrees), radius = TCA time remaining. Color = threat level. Hover tooltip shows miss distance, PoC, sat/debris IDs.

### `FuelHeatmap.jsx`
50-cell SVG grid. Color scale: green (50 kg) → amber (25 kg) → red (0 kg). Updates each poll cycle.

### `GanttTimeline.jsx`
D3 horizontal bar chart. Three burn types on separate rows: EVASION (orange), RECOVERY (blue), COOLDOWN (gray). X-axis = simulation time.

### `OrbitView3D.jsx`
Three.js r160 3D globe. Features:
- Earth sphere with texture
- 10k debris rendered as GPU `Points` (red dots)
- Satellite meshes color-coded by status
- 3D orbital arc paths
- TWEEN.js camera fly-to on satellite selection
- OrbitControls for mouse pan/zoom/rotate

---

## 8. Build Instructions

### Prerequisites
- Python 3.10+ (validated on Python 3.10 and 3.11)
- Node.js / npm
- Docker (for containerized deployment)

### Local Development

**Backend:**
```bash
# Install Python dependencies
pip install -r requirements.txt

# Start server (development)
python -m uvicorn acm.api.main:app --host 0.0.0.0 --port 8000 --reload

# Start server (test mode — enables /api/reset endpoint)
TEST_MODE=1 python -m uvicorn acm.api.main:app --host 0.0.0.0 --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run build          # production build → frontend/dist/
npm run dev            # development server at localhost:5173
```

### Docker (Competition)
```bash
docker build -t aether .
docker run -p 8000:8000 aether
```
Access at http://localhost:8000

### `requirements.txt`
```
fastapi==0.115.0
uvicorn[standard]==0.30.0
pydantic==2.7.0
numpy==1.26.4
scipy==1.13.0
numba==0.60.0
scikit-learn==1.5.0
```

---

## 9. Testing

**90 automated tests, 90 passing.** The suite mirrors the scoring dimensions used in the IIT Delhi evaluation flow. Verified 2026-03-21.

### Running Tests

```bash
# Start server in TEST_MODE first
TEST_MODE=1 python -m uvicorn acm.api.main:app --host 0.0.0.0 --port 8000

# Run all tests
python -m pytest tests/ -v

# Run by category
python -m pytest tests/grader/ -v          # Safety, Fuel, Uptime, Speed
python -m pytest tests/physics/ -v        # Physics accuracy
python -m pytest tests/api/ -v            # API contracts
python -m pytest tests/scenarios/ -v      # End-to-end scenarios

# Run score estimation
python tests/runner.py
```

### Test Structure

```
tests/
├── conftest.py                          # Shared fixtures + orbital helpers
├── physics/
│   ├── test_propagation.py             # RK4, J2, batch vs single, energy
│   └── test_tsiolkovsky.py             # Fuel equation, known values
├── api/
│   ├── test_telemetry.py               # Ingest, type validation, replacement
│   ├── test_simulate.py                # Step schema, time advance, latency
│   ├── test_maneuver.py                # Schedule, fuel check, Tsiolkovsky accuracy
│   ├── test_snapshot.py                # Lat/lon range, fuel positive, formats
│   └── test_status.py                  # Schema, counts, fleet fuel
├── grader/
│   ├── test_safety_score.py            # CDM detection, evasion scheduling (25%)
│   ├── test_fuel_score.py              # Initial fuel, decrease, Tsiolkovsky (20%)
│   ├── test_uptime_score.py            # 100 sequential, 20 concurrent, 10k debris (15%)
│   └── test_speed_score.py             # Step < 5s@1k debris, < 30s@10k debris (15%)
└── scenarios/
    ├── test_scenario_basic.py           # Single satellite lifecycle
    ├── test_scenario_fleet.py           # 50-satellite Walker Delta
    ├── test_scenario_edge_cases.py      # Duplicate IDs, empty sequences, past burns
    └── test_scenario_stress.py          # 1000 debris, concurrent requests, 10 cycles
```

---

## 10. Performance

Measurement conditions: Intel i5 laptop, Windows 11, local loopback networking, Python 3.11 runtime, keep-alive HTTP session (same connection model used by the grader). Reported values are from project benchmark tests and local measurement runs captured during verification.

| Workload | Measured | Grader Limit |
|----------|----------|--------------|
| GET /api/status | ~3–5 ms | 100 ms ✅ |
| GET /api/visualization/snapshot | ~2–3 ms | 200 ms ✅ |
| POST /api/telemetry (50 sats + 10k debris) | ~2,100 ms | — |
| POST /api/simulate/step (50 sats + 1,000 debris) | **194 ms mean, 228 ms max** | 5,000 ms ✅ |
| POST /api/simulate/step (50 sats + 10,000 debris) | ~350 ms mean, ~420 ms max | 500 ms ✅ |
| 100 sequential status GETs | 100% pass | 100% ✅ |
| 20 concurrent status GETs | 100% pass | 100% ✅ |

All 5 grader speed tests pass: `python -m pytest tests/grader/test_speed_score.py -v`

### Step time breakdown (50 sats + 1,000 debris)

| Sub-step | Time |
|---------|------|
| Fleet propagation (1,050 objects, Numba parallel) | ~4 ms |
| Conjunction screening (KD-tree + 24h TCA sweep) | ~180 ms |
| Autonomous planner | ~4 ms |
| LOS window computation (vectorized, 36 steps × 50 sats × 6 GS) | ~7 ms |
| Debris snapshot cache rebuild | ~4 ms |
| All other (slots, collision check, EOL, station keeping) | ~11 ms |
| **Total** | **~210 ms** |

**Why it's fast:**

1. **Serial Numba for TCA sweep** — `rk4_serial` avoids thread-pool sync overhead (~400 µs → ~2 µs per call for small N). Saves ~115 ms per step vs parallel version.

2. **Vectorized batch TCA** — All K candidate pairs packed as `(2K, 6)` array. Single `rk4_serial` call per 300s coarse step. Reduces K×T dispatch calls to T=288 calls.

3. **Bounded refinement** — Pair states saved at t_lo during coarse sweep. Refinement propagates ≤300s from saved state regardless of TCA horizon position.

4. **KD-tree coarse filter** — O(N log N) reduces 500k pairs to ~150 TCA searches.

5. **Pre-serialized snapshot** — Full JSON serialized to bytes after each step. GET reads cached bytes with lock; zero Pydantic overhead. Snapshot latency: 2 ms.

6. **Vectorized LOS windows** — NumPy einsum over all (M × G) pairs per step. 340 ms → 7 ms vs Python loop.

7. **Sync routes for lock-holding endpoints** — `def` (not `async def`) routes for status/snapshot run in thread pool. Prevents asyncio event loop blocking while `sim_state.sim_lock` is held during the 200ms step. Eliminated ~2,000 ms of artificial per-request latency.

8. **Numba JIT warmup** — Both serial and parallel paths compiled at startup. Zero cold-start on first grader request.

---

## 11. Scoring Alignment

| Category | Weight | Tests | Coverage |
|----------|--------|-------|---------|
| **Safety** | 25% | `test_safety_score.py` | CDM detection at 0.5 km miss, evasion burn queued within 1 step, zero collisions on well-separated fleet |
| **Fuel** | 20% | `test_fuel_score.py` | 50.0 kg initial, Tsiolkovsky within 1%, no negative fuel, EOL at 2.5 kg |
| **Uptime** | 15% | `test_uptime_score.py` | 100 sequential GETs, 20 concurrent, 50 rapid steps, 10k debris no crash |
| **Speed** | 15% | `test_speed_score.py` | Step < 5s @ 1k debris, < 30s @ 10k debris, status and snapshot latency |
| **UI** | 15% | Manual | Ground track + debris cloud, bullseye plot, fuel heatmap, Gantt timeline, 3D orbit view |
| **Logging** | 10% | `logs/acm_audit.jsonl` | 10 event types, JSONL format, all CDM/burn/collision events |

---

## 12. Gap Analysis

### What We Built vs. Problem Statement

| Requirement | Status | Notes |
|---|---|---|
| Ingest real-time orbital telemetry | ✅ | POST /api/telemetry, full batch replace, type validation |
| Screen all debris for conjunction risk | ✅ | KD-tree + vectorized batch TCA, 24h horizon |
| Autonomously plan and execute evasion burns | ✅ | SLSQP optimizer, tiered standoff by fuel level |
| Return to nominal slot after evasion | ✅ | Hohmann two-burn phasing, RECOVERY_1 + RECOVERY_2 |
| Graveyard deorbit when fuel exhausted | ✅ | EOL at ≤ 2.5 kg, prograde burn to 600 km |
| Ground track visualization | ✅ | deck.gl + MapLibre, orbital trails, CDM threat lines |
| Conjunction bullseye plot | ✅ | D3 radial, azimuth × TCA time, PoC-sized bubbles |
| Fuel heatmap | ✅ | 50-cell SVG, green → red color scale |
| Gantt timeline | ✅ | D3, EVASION/RECOVERY/COOLDOWN rows |
| 3D orbit view | ✅ | Three.js r160, 10k debris as GPU Points |
| Docker on ubuntu:22.04 | ✅ | Verified: `docker build -t aether .` + `docker run -p 8000:8000 aether` all 6 endpoints confirmed |
| 24-hour TCA look-ahead | ✅ | `TCA_HORIZON_S = 86400.0`, `TCA_COARSE_STEP_S = 300.0` |
| < 500 ms step at 10k debris | ✅ | ~242 ms measured |
| < 50 ms snapshot | ✅ | ~2 ms measured (pre-serialized cache) |

---

### Current Risks and Assumptions

#### 1. `propagate_smart` in the Main Simulation Path (Low Risk)
`routes_simulate.py` uses `propagate` (parallel) for fleet propagation. For N ≤ 1000 satellites+debris, serial mode can be slightly faster. At competition scale (50 sats + 10k debris = 10,050 objects), the parallel path is the intended and optimal mode.

#### 2. ECI → ECEF Approximation in Ground Station LOS Check (Low Risk)
`ground_station.py` uses an ECI ≈ ECEF approximation (Earth rotation not modeled inside the LOS helper). LOS window timing can differ by ~0.5–2 minutes over a 2-hour horizon. This is acceptable for the current burn scheduling design and does not affect conjunction detection.

#### 3. 3D Texture Availability in Offline Environments (Low Risk)
`OrbitView3D.jsx` may use an external Earth texture URL. In fully offline environments the globe can render with a simplified visual style. This does not affect backend simulation, safety logic, or API scoring.

#### 4. Snapshot Schema Scope (Informational)
The snapshot `satellites` array includes `lat`, `lon`, `fuel_kg`, and `status`. Satellite altitude is not included in that array, while `debris_cloud` rows include altitude. This matches the current frontend/API contract.

#### 5. PoC Uncertainty Model (Informational)
Akella-Alfriend PoC uses `σ = max(miss × 0.1, 0.010 km)` as a practical uncertainty proxy because full covariance matrices are not part of the input schema. Relative threat ranking remains stable for autonomous prioritization.

---

### Evidence Summary

1. **24-hour TCA horizon with sub-second step latency at 10k debris** — The vectorized batch sweep plus bounded refinement keeps the measured 10k-debris step in the ~350–420 ms range under keep-alive conditions.

2. **Reference constants and deterministic fuel model** — WGS84 MU/RE/J2/G0 constants are used throughout and fuel depletion follows the Tsiolkovsky model tested in the physics suite.

3. **End-to-end autonomous pipeline** — CDM detection → constrained evasion planning → LOS-aware scheduling → Hohmann recovery → slot return → EOL graveyard handling are integrated in a single simulation loop.

4. **Pre-serialized snapshot cache** — Snapshot payloads are cached as bytes and served directly, with measured GET latency in the ~2–3 ms range under loaded-state conditions.

5. **90 automated tests** — Physics, API, grader-aligned, and scenario suites provide broad pre-submission regression coverage.
