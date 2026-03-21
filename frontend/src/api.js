/**
 * AETHER API polling hooks
 * Snapshot at 10 Hz for positions, Status at 2 Hz for CDMs/burns
 */
import { useState, useEffect, useRef } from 'react'

export function useSnapshot() {
  const [snapshot, setSnapshot] = useState({ timestamp: '', satellites: [], debris_cloud: [] })
  const [error, setError] = useState(null)

  useEffect(() => {
    const poll = () => {
      fetch('/api/visualization/snapshot')
        .then(r => r.json())
        .then(data => { setSnapshot(data); setError(null) })
        .catch(e => setError(e.message))
    }
    poll()
    const id = setInterval(poll, 100) // 10 Hz
    return () => clearInterval(id)
  }, [])

  return { snapshot, error }
}

export function useStatus() {
  const [status, setStatus] = useState({
    system: 'AETHER',
    sim_time_iso: '',
    satellites_tracked: 0,
    debris_tracked: 0,
    active_cdm_warnings: 0,
    critical_conjunctions: 0,
    maneuvers_queued: 0,
    total_collisions: 0,
    fleet_fuel_remaining_kg: 0,
    recent_events: []
  })

  useEffect(() => {
    const poll = () => {
      fetch('/api/status')
        .then(r => r.json())
        .then(data => setStatus(data))
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 500) // 2 Hz
    return () => clearInterval(id)
  }, [])

  return status
}

export function useActiveCDMs(status) {
  // Extract CDMs from recent events
  const cdms = []
  if (status && status.recent_events) {
    // Build CDM list from status — in real system this would be a dedicated endpoint
    // For now parse from recent events
    for (const ev of status.recent_events) {
      if (ev.event_type === 'CDM_DETECTED' || ev.event_type === 'CDM_ACTIONED') {
        cdms.push({
          sat_id: ev.sat_id,
          deb_id: ev.deb_id,
          threat_level: ev.threat_level || 'WARNING',
          miss_km: ev.miss_km || 0,
          tca_offset_s: ev.tca_offset_s || 0,
          poc: ev.poc || 0
        })
      }
    }
  }
  return cdms
}
