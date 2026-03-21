import React, { useMemo } from 'react'

const WIDTH = 340
const HEIGHT = 340
const CX = WIDTH / 2
const CY = HEIGHT / 2 + 10
const MAX_R = 140
const TCA_RINGS = [60, 360, 720, 1440] // minutes
const TCA_LABELS = ['1h', '6h', '12h', '24h']

function tcaToRadius(tca_min) {
  // Map 0..1440 min to 0..MAX_R
  return Math.min(MAX_R, (tca_min / 1440) * MAX_R)
}

function missColor(miss_km) {
  if (miss_km < 1.0) return '#ef4444'
  if (miss_km < 5.0) return '#f59e0b'
  return '#22c55e'
}

export default function BullseyePlot({ snapshot, status, selectedSat }) {
  const sat = useMemo(() => {
    if (!selectedSat || !snapshot.satellites) return null
    return snapshot.satellites.find(s => s.id === selectedSat)
  }, [selectedSat, snapshot.satellites])

  // Get CDMs for selected satellite from recent events
  const cdms = useMemo(() => {
    if (!selectedSat || !status?.recent_events) return []
    const seen = new Set()
    const result = []
    for (const ev of status.recent_events) {
      if (ev.sat_id === selectedSat && ev.miss_km !== undefined) {
        const key = ev.deb_id
        if (!seen.has(key)) {
          seen.add(key)
          result.push({
            deb_id: ev.deb_id,
            tca_min: (ev.tca_offset_s || 0) / 60,
            miss_km: ev.miss_km || 0,
            poc: ev.poc || 0,
            azimuth: ev.approach_azimuth_deg || Math.random() * 360,
            threat_level: ev.threat_level || 'WARNING'
          })
        }
      }
    }
    return result
  }, [selectedSat, status])

  if (!selectedSat) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#64748b', fontSize: '12px', textAlign: 'center', padding: '20px' }}>
        <div>
          <div style={{ fontSize: '24px', marginBottom: '12px' }}>◎</div>
          <div>No satellite selected</div>
          <div style={{ marginTop: '4px', fontSize: '10px' }}>Click a satellite on the Ground Track</div>
        </div>
      </div>
    )
  }

  const fuelPct = sat ? (sat.fuel_kg / 50.0 * 100) : 0
  const fuelColor = fuelPct > 50 ? '#22c55e' : fuelPct > 10 ? '#f59e0b' : '#ef4444'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '8px' }}>
      {/* Satellite info header */}
      <div style={{ marginBottom: '8px', padding: '6px 10px', background: '#091428', border: '1px solid #1e3a5f', borderRadius: '4px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ color: '#06b6d4', fontWeight: 'bold', fontSize: '12px' }}>{selectedSat}</span>
          <span className={`status-badge badge-${(sat?.status || 'nominal').toLowerCase()}`}>
            {sat?.status || 'UNKNOWN'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '16px', marginTop: '4px', fontSize: '10px', color: '#94a3b8' }}>
          <span>FUEL: <span style={{ color: fuelColor }}>{sat?.fuel_kg?.toFixed(1) || '—'} kg ({fuelPct.toFixed(0)}%)</span></span>
          <span>CDMs: <span style={{ color: cdms.length > 0 ? '#ef4444' : '#22c55e' }}>{cdms.length}</span></span>
        </div>
        {cdms.length > 0 && (
          <div style={{ marginTop: '4px', fontSize: '10px', color: '#ef4444' }}>
            ⚠ MOST DANGEROUS: {cdms[0]?.deb_id} — {cdms[0]?.miss_km?.toFixed(2)} km miss @ TCA {cdms[0]?.tca_min?.toFixed(0)} min
          </div>
        )}
      </div>

      {/* SVG Bullseye */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <svg width={WIDTH} height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
          {/* Background */}
          <rect width={WIDTH} height={HEIGHT} fill="#060e1c" rx="4" />

          {/* Rings */}
          {TCA_RINGS.map((tca_min, i) => {
            const r = tcaToRadius(tca_min)
            return (
              <g key={i}>
                <circle cx={CX} cy={CY} r={r} fill="none" stroke="#1e3a5f" strokeWidth="1" strokeDasharray="4 4" />
                <text x={CX + r + 3} y={CY + 4} fill="#334155" fontSize="9" fontFamily="monospace">{TCA_LABELS[i]}</text>
              </g>
            )
          })}

          {/* Crosshairs */}
          <line x1={CX} y1={CY - MAX_R - 10} x2={CX} y2={CY + MAX_R + 10} stroke="#1e3a5f" strokeWidth="0.5" />
          <line x1={CX - MAX_R - 10} y1={CY} x2={CX + MAX_R + 10} y2={CY} stroke="#1e3a5f" strokeWidth="0.5" />

          {/* Axis labels */}
          <text x={CX} y={CY - MAX_R - 14} textAnchor="middle" fill="#475569" fontSize="9" fontFamily="monospace">PROGRADE</text>
          <text x={CX} y={CY + MAX_R + 20} textAnchor="middle" fill="#475569" fontSize="9" fontFamily="monospace">RETROGRADE</text>
          <text x={CX + MAX_R + 14} y={CY + 4} textAnchor="start" fill="#475569" fontSize="9" fontFamily="monospace">OUT</text>

          {/* Satellite center */}
          <circle cx={CX} cy={CY} r={6} fill="#06b6d4" />
          <circle cx={CX} cy={CY} r={10} fill="none" stroke="#06b6d4" strokeWidth="1" opacity="0.5" />

          {/* Debris dots */}
          {cdms.map((cdm, i) => {
            const r = tcaToRadius(cdm.tca_min)
            const az = (cdm.azimuth || 0) * Math.PI / 180
            const x = CX + r * Math.sin(az)
            const y = CY - r * Math.cos(az)
            const dotR = Math.max(4, Math.min(12, 6 / Math.max(0.1, cdm.miss_km)))
            const color = missColor(cdm.miss_km)
            return (
              <g key={i}>
                <circle cx={x} cy={y} r={dotR} fill={color} opacity={0.85} />
                {cdm.threat_level === 'CRITICAL' && (
                  <circle cx={x} cy={y} r={dotR + 4} fill="none" stroke={color} strokeWidth="1" opacity="0.4" />
                )}
                <text x={x + dotR + 2} y={y + 3} fill={color} fontSize="8" fontFamily="monospace">
                  {cdm.deb_id?.slice(-5)}
                </text>
              </g>
            )
          })}

          {cdms.length === 0 && (
            <text x={CX} y={CY + MAX_R + 35} textAnchor="middle" fill="#22c55e" fontSize="10" fontFamily="monospace">
              NO CONJUNCTION THREATS
            </text>
          )}

          {/* Center label */}
          <text x={CX} y={CY + 28} textAnchor="middle" fill="#64748b" fontSize="8" fontFamily="monospace">SATELLITE</text>
        </svg>
      </div>
    </div>
  )
}
