import React, { useMemo } from 'react'

const FUEL_INIT = 50.0

function fuelColor(fuel_kg, status) {
  if (status === 'EOL') return '#374151'
  const pct = fuel_kg / FUEL_INIT
  if (pct > 0.5) return '#22c55e'
  if (pct > 0.1) return '#f59e0b'
  return '#ef4444'
}

function fuelBarColor(fuel_kg) {
  const pct = fuel_kg / FUEL_INIT
  if (pct > 0.5) return 'linear-gradient(90deg, #15803d, #22c55e)'
  if (pct > 0.1) return 'linear-gradient(90deg, #92400e, #f59e0b)'
  return 'linear-gradient(90deg, #7f1d1d, #ef4444)'
}

export default function FuelHeatmap({ snapshot }) {
  const satellites = useMemo(() => {
    const sats = [...(snapshot.satellites || [])]
    // Sort ascending by fuel (most critical first)
    sats.sort((a, b) => a.fuel_kg - b.fuel_kg)
    return sats
  }, [snapshot.satellites])

  const fleetFuel = useMemo(() =>
    satellites.reduce((sum, s) => sum + s.fuel_kg, 0)
  , [satellites])

  const atRisk = satellites.filter(s => s.fuel_kg / FUEL_INIT < 0.1 && s.status !== 'EOL').length

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Fleet summary */}
      <div style={{
        padding: '6px 10px',
        background: '#060e1c',
        borderBottom: '1px solid #1e3a5f',
        display: 'flex',
        gap: '20px',
        fontSize: '10px',
        flexShrink: 0
      }}>
        <div>
          <span style={{ color: '#64748b' }}>FLEET FUEL: </span>
          <span style={{ color: '#06b6d4', fontWeight: 'bold' }}>{fleetFuel.toFixed(1)} kg</span>
        </div>
        <div>
          <span style={{ color: '#64748b' }}>AT RISK: </span>
          <span style={{ color: atRisk > 0 ? '#ef4444' : '#22c55e', fontWeight: 'bold' }}>{atRisk}</span>
        </div>
        <div>
          <span style={{ color: '#64748b' }}>FLEET: </span>
          <span style={{ color: '#94a3b8' }}>{satellites.length} SATs</span>
        </div>
      </div>

      {/* Satellite grid */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '6px',
        display: 'grid',
        gridTemplateColumns: 'repeat(5, 1fr)',
        gridAutoRows: 'max-content',
        gap: '4px',
        alignContent: 'start'
      }}>
        {satellites.map(sat => {
          const pct = sat.fuel_kg / FUEL_INIT
          const isEOL = sat.status === 'EOL'
          const isCritical = pct < 0.1 && !isEOL

          return (
            <div
              key={sat.id}
              className={isCritical ? 'pulse-red' : ''}
              style={{
                background: isEOL ? '#111827' : '#0d1f3c',
                border: `1px solid ${isEOL ? '#374151' : isCritical ? '#ef4444' : '#1e3a5f'}`,
                borderRadius: '3px',
                padding: '4px 5px',
                fontSize: '9px',
                fontFamily: 'monospace',
              }}
            >
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                marginBottom: '2px',
                alignItems: 'center'
              }}>
                <span style={{ color: isEOL ? '#6b7280' : '#94a3b8', fontSize: '8px' }}>
                  {sat.id.replace('SAT-', '')}
                </span>
                {isEOL
                  ? <span style={{ color: '#6b7280', fontSize: '8px', fontWeight: 'bold' }}>EOL</span>
                  : <span style={{ color: fuelColor(sat.fuel_kg, sat.status), fontSize: '8px' }}>
                      {(pct * 100).toFixed(0)}%
                    </span>
                }
              </div>

              {!isEOL && (
                <div style={{ height: '4px', background: '#1f2937', borderRadius: '2px', overflow: 'hidden' }}>
                  <div style={{
                    height: '100%',
                    width: `${Math.max(2, pct * 100)}%`,
                    background: fuelBarColor(sat.fuel_kg),
                    borderRadius: '2px',
                    transition: 'width 0.5s ease'
                  }} />
                </div>
              )}

              <div style={{ color: '#475569', fontSize: '8px', marginTop: '2px' }}>
                {isEOL ? '——' : `${sat.fuel_kg.toFixed(1)}kg`}
              </div>
            </div>
          )
        })}

        {/* Empty slots if < 50 sats */}
        {Array.from({ length: Math.max(0, 50 - satellites.length) }).map((_, i) => (
          <div key={`empty-${i}`} style={{
            background: '#060e1c',
            border: '1px solid #0d1f3c',
            borderRadius: '3px',
            padding: '4px 5px',
            opacity: 0.3,
            height: '48px'
          }} />
        ))}
      </div>
    </div>
  )
}
