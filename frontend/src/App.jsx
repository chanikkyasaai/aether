import React, { useState, Component } from 'react'
import { useSnapshot, useStatus } from './api.js'
import GroundTrack from './GroundTrack.jsx'
import BullseyePlot from './BullseyePlot.jsx'
import FuelHeatmap from './FuelHeatmap.jsx'
import GanttTimeline from './GanttTimeline.jsx'
import OrbitView3D from './OrbitView3D.jsx'

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null } }
  static getDerivedStateFromError(e) { return { error: e } }
  render() {
    if (this.state.error) return (
      <div style={{ color: '#ef4444', padding: '20px', fontFamily: 'monospace', background: '#0A1628', height: '100vh' }}>
        <div style={{ color: '#06b6d4', marginBottom: '8px' }}>AETHER — RENDER ERROR</div>
        <div style={{ color: '#f59e0b' }}>{this.state.error?.message}</div>
        <pre style={{ marginTop: '12px', fontSize: '11px', color: '#94a3b8', whiteSpace: 'pre-wrap' }}>{this.state.error?.stack}</pre>
      </div>
    )
    return this.props.children
  }
}

export default function App() {
  const { snapshot } = useSnapshot()
  const status = useStatus()
  const [selectedSat, setSelectedSat] = useState(null)
  const [show3D, setShow3D] = useState(false)

  const headerStyle = {
    height: '40px',
    background: '#060e1c',
    borderBottom: '1px solid #1e3a5f',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 16px',
    flexShrink: 0
  }

  const gridStyle = {
    display: 'grid',
    gridTemplateColumns: '65% 35%',
    gridTemplateRows: '55vh 45vh',
    gap: '1px',
    background: '#1e3a5f',
    flex: 1,
    overflow: 'hidden'
  }

  return (
    <ErrorBoundary>
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#0A1628' }}>
      {/* Top status bar */}
      <div style={headerStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <span style={{ color: '#06b6d4', fontWeight: 'bold', fontSize: '13px', letterSpacing: '0.2em' }}>
            ◈ AETHER ACM v3.0
          </span>
          <span style={{ color: '#64748b', fontSize: '11px' }}>
            {status.sim_time_iso ? `SIM: ${status.sim_time_iso.replace('T', ' ').replace('Z', ' UTC')}` : 'INITIALIZING...'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '20px', fontSize: '11px' }}>
          <Stat label="SATs" value={status.satellites_tracked} color="#22c55e" />
          <Stat label="DEBRIS" value={status.debris_tracked} color="#94a3b8" />
          <Stat label="CRITICAL" value={status.critical_conjunctions} color={status.critical_conjunctions > 0 ? '#ef4444' : '#22c55e'} />
          <Stat label="BURNS Q" value={status.maneuvers_queued} color="#f59e0b" />
          <Stat label="COLLISIONS" value={status.total_collisions} color={status.total_collisions > 0 ? '#ef4444' : '#22c55e'} />
          <Stat label="FLEET FUEL" value={`${status.fleet_fuel_remaining_kg.toFixed(0)}kg`} color="#06b6d4" />
        </div>
      </div>

      {/* Main grid */}
      <div style={gridStyle}>
        {/* Top-left: Ground Track */}
        <div className="panel">
          <div className="panel-header">
            <span>GROUND TRACK — ORBITAL INSIGHT VISUALIZER</span>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              {selectedSat && <span style={{ color: '#f59e0b', fontSize: '10px' }}>SEL: {selectedSat}</span>}
              <button
                onClick={() => setShow3D(true)}
                style={{
                  background: 'rgba(6,182,212,0.15)',
                  border: '1px solid #06b6d4',
                  color: '#06b6d4',
                  padding: '2px 8px',
                  fontSize: '10px',
                  cursor: 'pointer',
                  borderRadius: '3px',
                  letterSpacing: '0.05em'
                }}
              >
                3D VIEW
              </button>
            </div>
          </div>
          <div className="panel-content">
            <GroundTrack
              snapshot={snapshot}
              status={status}
              selectedSat={selectedSat}
              onSelectSat={setSelectedSat}
            />
          </div>
        </div>

        {/* Top-right: Bullseye Plot */}
        <div className="panel">
          <div className="panel-header">
            <span>CONJUNCTION BULLSEYE — TCA vs AZIMUTH</span>
          </div>
          <div className="panel-content">
            <BullseyePlot
              snapshot={snapshot}
              status={status}
              selectedSat={selectedSat}
            />
          </div>
        </div>

        {/* Bottom-left: Gantt Timeline */}
        <div className="panel">
          <div className="panel-header">
            <span>BURN SCHEDULE — MANEUVER GANTT TIMELINE</span>
            <span style={{ color: '#64748b', fontSize: '10px' }}>
              EVASION ■ RECOVERY ■ COOLDOWN ■
            </span>
          </div>
          <div className="panel-content">
            <GanttTimeline snapshot={snapshot} status={status} />
          </div>
        </div>

        {/* Bottom-right: Fuel Heatmap */}
        <div className="panel">
          <div className="panel-header">
            <span>FLEET FUEL STATUS — 50 SATELLITE HEATMAP</span>
          </div>
          <div className="panel-content">
            <FuelHeatmap snapshot={snapshot} />
          </div>
        </div>
      </div>

      {/* 3D Modal */}
      {show3D && (
        <OrbitView3D
          snapshot={snapshot}
          status={status}
          selectedSat={selectedSat}
          onClose={() => setShow3D(false)}
        />
      )}
    </div>
    </ErrorBoundary>
  )
}

function Stat({ label, value, color }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ color: '#64748b', fontSize: '9px', letterSpacing: '0.1em' }}>{label}</div>
      <div style={{ color, fontWeight: 'bold', fontSize: '12px' }}>{value}</div>
    </div>
  )
}
