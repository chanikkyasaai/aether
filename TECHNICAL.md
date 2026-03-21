# AETHER — Technical Reference
### Every module. Why it exists. How it works. Measured performance.
*Written for: understanding the system deeply enough to defend every decision under questioning.*

---

## 1. System Overview

AETHER is a ground-based autonomous collision management system for a 50-satellite LEO constellation. It runs as a single process serving a REST API and a React dashboard. Every simulation step runs the full pipeline autonomously — no human in the loop.

**One-line per module:**

| Module | One line |
|--------|----------|
| `physics.py` | J2-perturbed RK4 propagator, Numba JIT, two paths (serial/parallel) |
| `state.py` | Shared simulation state, thread-safe, NumPy arrays, snapshot cache |
| `conjunction.py` | KD-tree filter → vectorized batch TCA sweep → Akella-Alfriend PoC |
| `maneuver.py` | RTN↔ECI transforms, multi-start SLSQP evasion, Hohmann recovery |
| `planner.py` | Autonomous CDM → burn decision engine, LOS-aware scheduling |
| `station_keeping.py` | Nominal slot propagation, slot-return detection |
| `ground_station.py` | 6-station network, elevation check, LOS window prediction |
| `eol.py` | End-of-life detection, graveyard burn to 600 km |
| `logger.py` | Structured JSONL audit log, 10 event types |

---

## 2. Physics Engine — `physics.py`

### What it does
Propagates the position and velocity of N orbital objects forward in time using 4th-order Runge-Kutta integration with J2 perturbation.

### The physics

**J2 perturbation** is the dominant non-spherical gravity term for LEO. It causes:
- Nodal precession: ~7°/day at 53° inclination, 550 km
- Perigee rotation: ~3°/day at 53°

Without J2, orbit predictions drift by kilometres per hour. It's the minimum viable model for 24-hour conjunction prediction.

**RK4** is a 4th-order method — local truncation error is O(h⁵). At h=30s and orbital angular velocity ~1.09 mrad/s, one step subtends ~1.9° of arc, giving sub-metre accuracy per step.

### The two propagator paths

```python
_batch_derivatives:   @njit(parallel=True)  — uses prange (multi-threaded)
_serial_derivatives:  @njit()               — uses range  (single-threaded)

rk4_batch  → parallel  — use for N > ~1000 (fleet propagation)
rk4_serial → serial    — use for N ≤ ~1000 (TCA sweep, small batches)
```

**Why two paths?** Numba's `prange` dispatches work across a thread pool. For small N, thread synchronization overhead (~400 µs per call) dominates the actual computation (~2 µs). For the conjunction TCA sweep, which calls `rk4_serial` 288 times on a batch of K pairs, this difference is 400µs × 288 = 115 ms wasted vs 2µs × 288 = 0.6 ms.

The cross-over point is experimentally around N=1000. At competition scale (10,050 objects for fleet propagation), parallel wins clearly.

### Numba warmup

Numba compiles on first call. Without warmup, the grader's first `simulate/step` would stall for 10–15 seconds. `main.py` runs both `rk4_batch` and `rk4_serial` on dummy data in the FastAPI lifespan startup hook, ensuring compiled bytecode is cached before any request arrives.

### Constants used (WGS84 reference)

```python
MU  = 398600.4418   # km³/s² — Earth gravitational parameter
RE  = 6378.137      # km     — equatorial radius (WGS84)
J2  = 1.08263e-3    #        — second zonal harmonic (WGS84)
G0  = 9.80665e-3    # km/s²  — standard gravity
ISP = 300.0         # s      — monopropellant specific impulse
M_DRY = 500.0       # kg     — satellite dry mass
```

### Tsiolkovsky equation

```python
def tsiolkovsky_dm(m_current_kg, dv_km_s):
    dm = m_current_kg * (1 - exp(-dv_km_s / (ISP * G0)))
    return max(0.0, dm)
```

Used everywhere fuel is consumed. The exponential ensures mass ratio is correct regardless of ΔV magnitude.

### Measured performance

| Operation | N | Time |
|-----------|---|------|
| `rk4_serial` single step | 100 pairs (200 objects) | ~0.05 ms |
| `rk4_batch` single step | 10,050 objects | ~3 ms |
| `propagate` 60s, step=30s | 10,050 objects | ~6 ms |

---

## 3. State Management — `state.py`

### What it does
Holds all simulation state in a single `SimState` singleton. Everything that changes during a step lives here. Thread-safe via `threading.Lock()`.

### Key data structures

```python
sat_states:    np.ndarray shape (M, 6)   # [x,y,z,vx,vy,vz] per satellite, km/km/s
deb_states:    np.ndarray shape (N, 6)   # same for debris
sat_fuel_kg:   np.ndarray shape (M,)     # remaining propellant per satellite
sat_status:    list[str]                  # 'NOMINAL' | 'EVADING' | 'RECOVERING' | 'EOL'
sat_ids:       list[str]                  # 'SAT-000' ... 'SAT-049'
maneuver_queue: list[ScheduledBurn]       # pending burns sorted by time
active_cdms:   list[CDM]                  # current conjunction data messages
sat_los_cache: dict[str, list[dict]]      # per-satellite blackout windows (LOS)
```

### Why NumPy arrays, not Python lists

Every simulation step passes `sat_states` and `deb_states` directly into Numba-compiled functions. Numba requires NumPy arrays — Python lists would require conversion on every call. With M+N = 10,050 objects per step, the conversion overhead would be significant.

### The threading model

`simulate_step` is a plain `def` (not async) — FastAPI runs it in a thread pool. It holds `sim_state.sim_lock` for the full duration of each step (~200 ms). All other endpoints that access `sim_state` are also plain `def` routes, so they run in the thread pool and block on the lock, rather than blocking the asyncio event loop.

**Critical design decision:** If status/snapshot routes are `async def` and use `with threading.Lock():`, they block the asyncio event loop while waiting for the lock during the 200ms step window. Using plain `def` routes keeps lock waits in the thread pool and avoids event-loop stalls.

### Snapshot cache

After each step, the full snapshot JSON is serialized to bytes once:
```python
_snapshot_json_cache: bytes  # pre-serialized JSON
_debris_cloud_cache: list    # pre-computed geodetic debris positions
```

GET `/api/visualization/snapshot` just returns the cached bytes — zero computation, zero Pydantic serialization. Measured: **2–3 ms** per GET vs ~270 ms without caching.

---

## 4. Conjunction Screening — `conjunction.py`

### The problem

50 satellites × 10,000 debris = 500,000 pairs. Checking TCA for all pairs at 24h horizon is intractable naively. The solution is a three-phase pipeline.

### Phase 1: KD-tree coarse filter

```python
tree = KDTree(deb_positions)         # O(N log N) build, N=10,000
candidates = tree.query_ball_point(  # O(M log N) query, M=50
    sat_positions, r=200.0           # 200 km radius filter
)
```

At random debris in 450–650 km shell, this typically returns ~3–15 debris per satellite = ~150–750 candidate pairs. From 500,000 to 150 — before any propagation.

**Why 200 km?** The maximum miss-distance threshold for CDM generation is 5 km. Over 24 hours, J2 perturbation can shift relative positions by up to ~50 km. 200 km gives a 40× margin, ensuring no real conjunction is filtered out.

**vs. CARA's altitude-band filter:** CARA uses perigee-apogee altitude ranges — objects whose altitude ranges don't overlap can't collide. Simple and fast. AETHER's KD-tree is more accurate for non-uniform debris distributions (e.g., debris clusters after fragmentation events) because it adapts to Cartesian density, not just altitude.

### Phase 2: Vectorized batch TCA sweep

For K candidate pairs:

```python
packed = np.zeros((2*K, 6))   # even rows = satellites, odd rows = debris
for i, (s_idx, d_idx) in enumerate(pairs):
    packed[2*i]     = sat_states[s_idx]
    packed[2*i + 1] = deb_states[d_idx]

# 24h horizon, 5-minute steps = 288 steps
for t in range(0, 86400, 300):
    current = rk4_serial(current, 300)          # ONE Numba call on (2K, 6)
    miss = norm(current[0::2,:3] - current[1::2,:3], axis=1)  # (K,) vectorized
    improved = miss < min_miss
    t_lo[improved] = t_prev
    t_hi[improved] = t
    states_at_tlo[improved] = prev[improved]    # save state AT t_lo
```

**Key insight: K×T vs T dispatches.** Naive approach: for each of K pairs, for each of 288 time steps, call `rk4_serial(pair_state, dt)`. That's K×288 = 43,200 Numba dispatch calls for K=150. Vectorized approach: pack all pairs into one array, call `rk4_serial` once per step on the full (2K, 6) array. That's 288 calls total.

**Key insight: Saving states_at_tlo.** During the coarse sweep, the state of each pair at its best t_lo is saved. Refinement then propagates from that saved state, not from t=0. A pair with TCA at t=20h would otherwise require propagating 72,000s (2,400 sub-steps at 30s) per refinement evaluation. With saved state, refinement propagates only 300s (10 sub-steps) regardless.

### Phase 3: Golden-section TCA refinement

For each pair within the coarse threshold:

```python
result = minimize_scalar(
    miss_func,             # miss distance as function of dt ∈ [0, 300s]
    bounds=(0.0, 300.0),
    method='bounded',
    options={'xatol': 1.0}  # 1-second TCA accuracy
)
```

Golden-section search converges in O(log_{1/φ}(1/ε)) ≈ log_{0.618}(1/300) ≈ 15 evaluations. Each evaluation propagates 10 sub-steps. Total: ~150 Numba calls per pair.

### Phase 4: Akella-Alfriend PoC

```python
r_hb = (SAT_SIZE_M + DEB_SIZE_M) / 1000.0   # combined hard-body radius in km
sigma = max(miss_km * 0.1, 0.010)             # 10% position uncertainty floor
poc = (r_hb**2 / (2*sigma**2)) * exp(-miss_km**2 / (2*sigma**2))
```

This is the **Akella-Alfriend (2000) short-encounter model** — the same formula used by NASA CARA and ESA CREAM as their primary PoC metric. It assumes a 2D Gaussian relative position distribution in the encounter plane.

The `sigma = miss × 0.1` approximation: in a real system, sigma comes from the combined position covariance of both objects projected into the encounter plane. Without covariance data in the input schema, 10% is a conservative proxy. The absolute PoC values differ from CARA, but the relative threat ordering (which conjunctions are more dangerous) is preserved.

### Measured performance

| Scenario | KD-tree pairs | Phase 2 (288 steps) | Phase 3+4 | Total |
|----------|--------------|---------------------|-----------|-------|
| 50 sats + 1,000 debris | ~150 | ~40 ms | ~8 ms | ~50 ms |
| 50 sats + 10,000 debris | ~500 | ~130 ms | ~25 ms | ~155 ms |

---

## 5. Maneuver Planning — `maneuver.py`

### RTN frame

All burn optimization is done in the Radial-Transverse-Normal (RTN) frame:
- **R:** radial, Earth center → satellite
- **T:** transverse, in velocity direction (prograde)
- **N:** normal, perpendicular to orbital plane

RTN is the natural frame for orbital maneuvers — prograde burns raise the orbit, radial burns change eccentricity, normal burns change inclination.

```python
def rtn_to_eci_matrix(sat_state):
    r_hat = r / |r|
    n_hat = (r × v) / |r × v|
    t_hat = n_hat × r_hat
    return column_stack([r_hat, t_hat, n_hat])   # 3×3 rotation matrix
```

### SLSQP evasion optimization

**Objective:** minimize `|ΔV|` (minimize fuel consumed)

**Constraints:**
1. Miss distance at TCA ≥ standoff distance (500m, 200m, or 100m by fuel level)
2. Remaining fuel after burn ≥ 0.5 kg (margin)

**Bounds:** `|ΔV_component| ≤ 15 m/s` in each of R, T, N

```python
result = minimize(
    fun=lambda dv: norm(dv),
    x0=seed,
    method='SLSQP',
    bounds=[(-0.015, 0.015)] * 3,
    constraints=[
        {'type': 'ineq', 'fun': miss_constraint},   # miss - standoff ≥ 0
        {'type': 'ineq', 'fun': fuel_constraint},   # fuel - dm - 0.5 ≥ 0
    ],
    options={'ftol': 1e-9, 'maxiter': 150}
)
```

**Multi-start:** 5 initial seed directions are tried:
```python
seeds = [
    [0, +0.003, 0],   # prograde
    [0, -0.003, 0],   # retrograde
    [+0.003, 0, 0],   # radial out
    [0, +0.003, +0.003],  # prograde + normal
    [+0.003, +0.003, 0],  # radial + prograde
]
```

The best feasible result (lowest `|ΔV|`) is used. This eliminates local-minimum traps that a single-start SLSQP would fall into, and covers all physically meaningful evasion directions.

**Verification:** After SLSQP returns a result, the miss distance is recomputed at higher accuracy (30s sub-steps) to confirm the constraint is genuinely satisfied before accepting the solution.

**Fallback:** If all 5 seeds fail, progressively larger prograde burns (5, 8, 10, 15 m/s) are tried until standoff is achieved in the evaluated model. This is a conservative fallback that prioritizes separation, with higher fuel cost.

**Why SLSQP vs alternatives:**
- Grid search (STK CAMP): O(grid_size × eval_cost), suboptimal
- Convex SDP (arXiv 2024): can provide stronger global-optimality guarantees, but requires CVXPY+ECOS and a heavier dependency/runtime footprint
- SLSQP: fast (seconds for 150 iterations), handles inequality constraints natively, and is operationally suitable for this 3-variable constrained problem; multi-start seeding reduces local-minimum risk

### Hohmann recovery

After evasion, the satellite is off its nominal slot. Two prograde burns return it:

```python
# Transfer ellipse semi-major axis
a_t = (r_current + r_nominal) / 2.0

# Burn 1: at current orbit, raise to transfer ellipse
dv1 = sqrt(MU * (2/r_current - 1/a_t)) - sqrt(MU / r_current)

# Burn 2: at apogee, circularize at nominal orbit
dv2 = sqrt(MU / r_nominal) - sqrt(MU * (2/r_nominal - 1/a_t))

# Transfer time (half-period of transfer ellipse)
T_transfer = pi * sqrt(a_t**3 / MU)
```

Both burns are in the T (prograde) direction. Burn 1 fires at the evasion position, burn 2 fires at apogee (propagated forward `T_transfer` seconds). This is the analytically exact Hohmann transfer for circular coplanar orbits — no iteration needed.

---

## 6. Autonomous Planner — `planner.py`

### Decision logic per CRITICAL CDM

The planner runs after every simulate step. For each CRITICAL conjunction:

```
1. _already_handled(sat_id, tca_abs_s)?
   → Yes: skip (an EVASION burn already fires before this TCA)

2. sat_status == 'EOL'?
   → Yes: skip (deorbiting, cannot maneuver)

3. tca_offset_s < 15s?
   → Log BLIND_CONJUNCTION, skip (can't schedule in time)

4. earliest_burn = max(current_time, last_burn_time + 600s)
   (600s cooldown between burns)

5. earliest_burn ≥ tca_abs_s - MIN_LEAD_S?
   → Emergency override: earliest_burn = current_time + 10s
   (cooldown deadlock prevention — safety takes priority over cooldown)

6. Compute evasion burn: SLSQP (5 seeds) → fallback prograde
7. Schedule EVASION burn at earliest_burn
8. Compute Hohmann recovery burns
9. Schedule RECOVERY_1 at evasion + 3600s, RECOVERY_2 at + transfer_time
10. Update sat_status = 'EVADING'
```

### Cooldown rationale

600 seconds between burns prevents thruster overheating and allows telemetry confirmation of the previous burn's effect. If a second CRITICAL CDM arrives during cooldown, the emergency override ensures safety takes priority.

### _already_handled check

```python
for burn in maneuver_queue:
    if burn.satellite_id == sat_id and burn.burn_type == 'EVASION':
        if burn.burn_time_s < tca_abs_s:
            return True  # evasion fires before this TCA
```

This checks whether an existing EVASION burn fires before the TCA — not just whether a burn exists. A satellite evading one debris might face a second conjunction after its evasion. This check correctly identifies that the first evasion already handles it only if it fires in time.

---

## 7. Station Keeping — `station_keeping.py`

### What it does

Tracks each satellite's nominal slot (the position it should be at, propagated alongside the satellite). When a recovering satellite gets within 10 km of its nominal position, the RECOVERING status clears to NOMINAL.

### Why 10 km

The spec defines the constellation slot box as 10 km. Recovery burns return the satellite to within this box. The check runs every simulation step so the transition from RECOVERING → NOMINAL is detected promptly.

---

## 8. Ground Station Network — `ground_station.py`

### The 6 stations

| ID | Location | Lat | Lon |
|----|----------|-----|-----|
| GS-001 | IIT Delhi | 28.5° N | 77.2° E |
| GS-002 | Bangalore ISRO | 12.9° N | 77.6° E |
| GS-003 | Svalbard | 78.2° N | 15.4° E |
| GS-004 | Fairbanks, Alaska | 64.8° N | 147.7° W |
| GS-005 | Santiago, Chile | 33.4° S | 70.7° W |
| GS-006 | Mauritius | 20.2° S | 57.5° E |

This global distribution ensures ~80% uptime coverage for a 53° inclination constellation.

### LOS check

```python
def elevation_deg(self, sat_pos_eci):
    range_vec = sat_pos_eci - self.ecef          # satellite − station
    gs_unit = self.ecef / norm(self.ecef)        # up-direction at station
    sin_elev = dot(range_vec, gs_unit) / norm(range_vec)
    return degrees(arcsin(clip(sin_elev, -1, 1)))

def has_los(self, sat_pos_eci):
    return self.elevation_deg(sat_pos_eci) >= 5.0  # 5° min elevation
```

ECI ≈ ECEF approximation: Earth rotation is ignored. Over a 2-hour LOS prediction window, Earth rotates ~30°, introducing ~0.5–2 min error in window timing. Acceptable for burn scheduling.

### Vectorized LOS for all satellites

When computing 3-hour blackout windows (36 steps × 6 stations × 50 satellites), the LOS check is vectorized over all (M, G) pairs in one NumPy operation:

```python
range_vec = sat_positions[:, np.newaxis, :] - gs_ecef[np.newaxis, :, :]  # (M, G, 3)
dot_products = einsum('mgk,gk->mg', range_vec, gs_unit)                  # (M, G)
elev_deg = degrees(arcsin(clip(dot_products / range_norm, -1, 1)))        # (M, G)
has_los = any(elev_deg >= min_elevation, axis=1)                          # (M,)
```

This replaces 50 × 6 = 300 Python function calls per step with one NumPy operation. Performance: **340ms → 7ms** for 36-step LOS window computation.

---

## 9. End-of-Life Management — `eol.py`

### Trigger condition

When `sat_fuel_kg[i] ≤ 2.5 kg` (5% of initial 50 kg), EOL is triggered.

### What happens

1. Satellite status set to `'EOL'`
2. Hohmann burn computed from current altitude (~550 km) to graveyard orbit (≥600 km)
3. GRAVEYARD burn scheduled immediately
4. Upon execution: satellite removed from constellation, added to debris catalog (becomes a tracked debris object)

### IADC compliance

The Inter-Agency Space Debris Coordination Committee (IADC) Space Debris Mitigation Guidelines require LEO objects to deorbit within 25 years, or be raised to a graveyard orbit if deorbit is impractical. At 600 km, atmospheric drag will deorbit the satellite within 25 years. AETHER implements this automatically.

---

## 10. Structured Logger — `logger.py`

### The 10 event types

| Event | Trigger | Key fields |
|-------|---------|------------|
| `CDM_DETECTED` | New CRITICAL conjunction detected | sat_id, deb_id, miss_km, poc, threat_level |
| `CDM_WARNING` | Non-critical conjunction warning | sat_id, deb_id, miss_km, poc |
| `CDM_ACTIONED` | Evasion burn scheduled for a conjunction | sat_id, deb_id, burn_id |
| `BURN_EXECUTED` | Burn fires during step | burn_id, mass_before_kg, mass_after_kg |
| `RECOVERY_SCHEDULED` | Recovery sequence queued | sat_id, burn1_time, burn2_time |
| `RECOVERY_COMPLETE` | Recovery status returned to NOMINAL | sat_id, slot_error_km |
| `COLLISION_DETECTED` | Miss < 100m during step | sat_id, deb_id, miss_km |
| `EOL_TRIGGERED` | Fuel ≤ 2.5 kg | sat_id, fuel_kg |
| `BLIND_CONJUNCTION` | TCA < 15s, too late to act | sat_id, deb_id, tca_s |
| `DEGRADED_AVOIDANCE` | Fallback maneuver used after optimizer failure | sat_id, deb_id, fallback_dv_m_s |

### Format

```jsonl
{"timestamp_utc":"2026-03-21T10:15:32.123Z","sim_time_s":3600.0,"event_type":"CDM_WARNING","sat_id":"SAT-000","deb_id":"DEB-00042","tca_offset_s":420.0,"miss_km":0.23,"poc":0.0021}
{"timestamp_utc":"2026-03-21T10:15:33.044Z","sim_time_s":3600.0,"event_type":"BURN_EXECUTED","burn_id":"EVASION_SAT-000_001","sat_id":"SAT-000","actual_dv_eci":[0.0,0.0031,0.0],"mass_before_kg":550.0,"mass_after_kg":549.4}
```

Each line is valid JSON. The log file (`logs/acm_audit.jsonl`) is the permanent audit trail for all autonomous decisions.

---

## 11. API Layer — `acm/api/`

### The 6 endpoints

| Method | Path | Handler | Notes |
|--------|------|---------|-------|
| POST | `/api/telemetry` | `routes_telemetry.py` | Batch ingest, replaces all state |
| POST | `/api/simulate/step` | `routes_simulate.py` | Full pipeline, plain def |
| POST | `/api/maneuver/schedule` | `routes_maneuver.py` | Manual burn, returns 202 |
| GET | `/api/visualization/snapshot` | `routes_viz.py` | Pre-cached bytes, plain def |
| GET | `/api/status` | `routes_status.py` | Fleet status, CDMs, burns, plain def |
| POST | `/api/reset` | `routes_reset.py` | TEST_MODE only, 403 in production |

### Why `plain def` matters

FastAPI runs `async def` routes directly on the asyncio event loop. If an `async def` route does `with threading.Lock()` and the lock is held (by `simulate_step` for ~200ms), the event loop **blocks** — no other async I/O can proceed. This caused 2,000ms of added latency per request during simulation.

`def` (sync) routes run in a thread pool. They block their own thread while waiting for the lock, leaving the event loop free. **All lock-holding routes are plain `def`.**

### Snapshot caching strategy

After each `simulate/step`:
```
1. ECI→geodetic conversion for all 10,000 debris positions → _debris_cloud_cache
2. ECI→geodetic for 50 satellites
3. Full payload serialized to bytes → _snapshot_json_cache
4. GET /api/visualization/snapshot returns _snapshot_json_cache directly
```

Without caching: each GET triggers ECI→geodetic for 10,050 objects + JSON serialization = ~270ms.
With caching: GET acquires lock briefly, copies bytes reference, returns = **2ms**.

---

## 12. Frontend — `frontend/src/`

### Panel layout

```
┌─────────────────────────┬─────────────────────┐
│  GroundTrack (deck.gl)  │  BullseyePlot (D3)  │
│  MapLibre dark matter   │  azimuth × TCA time │
│  satellite dots + trails│  CDM threats        │
│  CDM red/amber lines    │  PoC-sized bubbles  │
│  6 GS markers           │                     │
├─────────────────────────┼─────────────────────┤
│  FuelHeatmap (SVG)      │  GanttTimeline (D3) │
│  50 cells green→red     │  burns per satellite│
│  updates each poll      │  + cooldown zones   │
│                         │  + blackout windows │
├─────────────────────────┴─────────────────────┤
│  OrbitView3D (Three.js) — full width          │
│  3D globe, 10k debris GPU points, orbit arcs  │
│  fly-to on click, OrbitControls               │
└───────────────────────────────────────────────┘
```

### Polling architecture

```javascript
useSnapshot: polling /api/visualization/snapshot at 10 Hz
useStatus:   polling /api/status at 2 Hz
```

The snapshot contains positions and fuel; status contains CDMs, burns, events. Separate polling rates balance responsiveness against server load.

### GanttTimeline blackout zones

After each step, `sim_state.sat_los_cache` contains per-satellite lists of `{start_s, end_s}` blackout windows (3-hour lookahead, 5-minute resolution). The Gantt renders these as semi-transparent red overlays — showing when each satellite has no ground station contact and therefore cannot receive burn commands.

---

## 13. Performance Measurement Notes

Measurement environment: Intel i5 laptop, Windows 11, Python 3.11, local server process, keep-alive HTTP sessions unless stated otherwise.

Methodology notes:
- Endpoint latency values come from the benchmark tests used in this repository and local verification runs.
- Reported statistics are mean/max where those are the values captured by the current benchmark scripts.
- Results are environment-dependent and should be interpreted as implementation evidence, not hardware-independent guarantees.

### Step latency (keep-alive HTTP session)

| Workload | Mean | Max | Grader limit |
|---------|------|-----|-------------|
| 50 sats + 1,000 debris | 194 ms | 228 ms | 5,000 ms |
| 50 sats + 10,000 debris | ~350 ms | ~420 ms | 500 ms¹ |

¹ The 500 ms limit in the spec is evaluated by the grader with keep-alive connections. New-connection testing includes additional transport overhead and is not directly comparable.

### Step time breakdown (50 sats + 1,000 debris)

| Sub-step | Time |
|---------|------|
| Fleet propagation (1,050 objects) | ~4 ms |
| Slot propagation (50 nominal slots) | ~2 ms |
| Collision check | ~8 ms |
| Conjunction screening (KD-tree + TCA) | ~180 ms |
| Autonomous planner | ~4 ms |
| EOL check | <1 ms |
| Station keeping | <1 ms |
| Debris snapshot cache rebuild | ~4 ms |
| LOS window computation (36 steps × 50 sats × 6 GS) | ~7 ms |
| **Total** | **~210 ms** |

### Endpoint latencies (keep-alive, data loaded)

| Endpoint | Measured | Grader limit |
|---------|---------|-------------|
| GET /api/status | 3–5 ms | 100 ms |
| GET /api/visualization/snapshot | 2–3 ms | 200 ms |
| POST /api/telemetry (50 sats + 10k debris) | ~2,100 ms | — |

### Test coverage

| Suite | Tests | Status |
|-------|-------|--------|
| Physics (propagation, Tsiolkovsky) | 16 | ✅ 16/16 |
| API contracts | 35 | ✅ 35/35 |
| Grader (safety, fuel, uptime, speed) | 20 | ✅ 20/20 |
| Scenarios (fleet, stress, edge cases) | 19 | ✅ 19/19 |
| **Total** | **90** | **✅ 90/90** |

---

## 14. Review Q&A

The following responses summarize model choices and limitations in engineering terms.

**Q: What probability-of-collision model do you use?**
A: Akella-Alfriend 2000 short-encounter approximation. Formula: `Pc = (r_hb² / 2σ²) × exp(−miss² / 2σ²)`, where `r_hb` is combined hard-body radius and `σ` is relative position uncertainty.

**Q: Why not use a full covariance matrix for PoC?**
A: The input schema does not include covariance matrices. The implementation uses `σ = max(0.1 × miss_distance, 10 m)` as an uncertainty proxy. With covariance-enabled CDM input, this term can be replaced by encounter-plane covariance projection.

**Q: Why KD-tree instead of altitude-band pre-filter?**
A: KD-tree filtering operates directly in Cartesian position space and adapts to local object density. This improves candidate reduction quality for non-uniform debris distributions, including clustered fields.

**Q: Why SLSQP for maneuver optimization?**
A: The optimizer has three decision variables (ΔV in RTN), a smooth objective, and two inequality constraints (standoff and fuel margin). SLSQP is a practical constrained NLP choice for this structure, and multi-start seeding improves robustness against local minima.

**Q: Your propagator doesn't include atmospheric drag. How do you handle this?**
A: For a 24-hour window at 550 km, drag is not explicitly modeled in this propagator. The coarse filter radius (200 km) is intentionally wider than expected short-horizon drift, so candidate conjunctions remain in scope. The trade-off is increased false-positive risk at longer horizons.

**Q: Why is the step latency 200ms when you have Numba JIT?**
A: The dominant cost is conjunction screening over the 24-hour horizon. Vectorized batching reduces dispatch overhead by evaluating all candidate pairs per coarse step in one propagator call. The main runtime driver is the number of propagated coarse/refinement evaluations, not HTTP serialization.
