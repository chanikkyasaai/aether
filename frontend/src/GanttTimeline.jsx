import React, { useEffect, useRef, useMemo } from 'react'
import * as d3 from 'd3'

const BURN_COLORS = {
  EVASION:    '#2563eb',
  RECOVERY_1: '#0d9488',
  RECOVERY_2: '#0d9488',
  GRAVEYARD:  '#7c3aed',
  MANUAL:     '#6b7280',
}
const COOLDOWN_COLOR  = '#374151'
const BLACKOUT_COLOR  = '#7f1d1d'   // dark red — no ground-station LOS
const CURRENT_TIME_COLOR = '#ef4444'
const COOLDOWN_S = 600

export default function GanttTimeline({ snapshot, status }) {
  const svgRef = useRef(null)
  const containerRef = useRef(null)

  // All satellite IDs — show rows for every tracked satellite, not just those with burns
  const allSatIds = useMemo(() => {
    const fromSnapshot = (snapshot?.satellites || []).map(s => s.id)
    const fromBurns    = [
      ...(status?.scheduled_burns || []).map(b => b.satellite_id),
      ...(status?.recent_events   || [])
          .filter(e => e.event_type === 'BURN_EXECUTED')
          .map(e => e.sat_id),
    ]
    const merged = [...new Set([...fromSnapshot, ...fromBurns])].filter(Boolean)
    merged.sort()
    return merged
  }, [snapshot, status])

  // Build burn event list (scheduled + executed)
  const burnEvents = useMemo(() => {
    const burns = []
    for (const b of (status?.scheduled_burns || [])) {
      burns.push({
        sat_id: b.satellite_id, burn_id: b.burn_id,
        burn_time: b.burn_time_s, burn_type: b.burn_type,
        dv_m_s: b.dv_magnitude_m_s || 0, fuel_cost: 0, queued: true,
      })
    }
    for (const ev of (status?.recent_events || [])) {
      if (ev.event_type === 'BURN_EXECUTED') {
        burns.push({
          sat_id: ev.sat_id, burn_id: ev.burn_id || '',
          burn_time: ev.sim_time_s || 0,
          burn_type: (ev.burn_id || '').split('_')[0] || 'MANUAL',
          dv_m_s: ev.dv_magnitude_m_s || 0,
          fuel_cost: ev.fuel_cost_kg || 0, queued: false,
        })
      }
    }
    return burns
  }, [status])

  // LOS blackout windows keyed by sat_id
  const losWindows = useMemo(() => status?.sat_los_windows || {}, [status])

  const currentTime = useMemo(() => {
    const events = status?.recent_events || []
    if (events.length > 0) return events[events.length - 1].sim_time_s || 0
    return 0
  }, [status])

  useEffect(() => {
    if (!svgRef.current || !containerRef.current) return
    const container = containerRef.current
    const W = container.clientWidth  || 600
    const H = container.clientHeight || 200

    const margin = { top: 8, right: 16, bottom: 24, left: 90 }
    const innerW = W - margin.left - margin.right
    const innerH = H - margin.top - margin.bottom

    const satIds = allSatIds.length > 0 ? allSatIds : [...new Set(burnEvents.map(b => b.sat_id))].filter(Boolean)

    const svg = d3.select(svgRef.current)
    svg.selectAll('*').remove()
    svg.attr('width', W).attr('height', H)

    if (satIds.length === 0) {
      svg.append('text')
        .attr('x', W / 2).attr('y', H / 2)
        .attr('text-anchor', 'middle').attr('fill', '#475569')
        .attr('font-size', 12).attr('font-family', 'monospace')
        .text('No satellites tracked — awaiting telemetry')
      return
    }

    const rowH = Math.min(28, innerH / satIds.length)
    const t0 = currentTime - 3600
    const t1 = currentTime + 21600
    const xScale = d3.scaleLinear().domain([t0, t1]).range([0, innerW])

    const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

    // Background
    g.append('rect').attr('width', innerW).attr('height', innerH).attr('fill', '#060e1c')

    // Row backgrounds
    satIds.forEach((satId, i) => {
      g.append('rect')
        .attr('x', 0).attr('y', i * rowH)
        .attr('width', innerW).attr('height', rowH)
        .attr('fill', i % 2 === 0 ? '#0a1420' : '#0d1f3c')
    })

    // Blackout zones — red overlay where no ground station LOS
    satIds.forEach((satId, i) => {
      const windows = losWindows[satId] || []
      windows.forEach(w => {
        const x1 = xScale(w.start_s)
        const x2 = xScale(w.end_s)
        if (x2 < 0 || x1 > innerW) return
        g.append('rect')
          .attr('x', Math.max(0, x1)).attr('y', i * rowH)
          .attr('width', Math.min(x2, innerW) - Math.max(0, x1)).attr('height', rowH)
          .attr('fill', BLACKOUT_COLOR).attr('opacity', 0.35)
          .append('title').text(`${satId}: No LOS`)
      })
    })

    // Grid lines (1-hour intervals)
    g.selectAll('.grid-line')
      .data(xScale.ticks(12))
      .join('line').attr('class', 'grid-line')
      .attr('x1', d => xScale(d)).attr('x2', d => xScale(d))
      .attr('y1', 0).attr('y2', innerH)
      .attr('stroke', '#1e3a5f').attr('stroke-width', 0.5)

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${innerH})`)
      .call(d3.axisBottom(xScale).ticks(8).tickFormat(d => {
        const dt = d - currentTime
        const h  = Math.floor(Math.abs(dt) / 3600)
        const m  = Math.floor((Math.abs(dt) % 3600) / 60)
        return `${dt < 0 ? '-' : '+'}${h}h${m.toString().padStart(2, '0')}m`
      }))
      .selectAll('text').attr('fill', '#475569').attr('font-size', 9).attr('font-family', 'monospace')
    g.selectAll('.domain,.tick line').attr('stroke', '#1e3a5f')

    // Burn blocks + cooldown
    burnEvents.forEach(burn => {
      const satIdx = satIds.indexOf(burn.sat_id)
      if (satIdx < 0) return
      const y = satIdx * rowH + 2
      const h = rowH - 4
      const DURATION = 60
      const color = BURN_COLORS[burn.burn_type] || BURN_COLORS.MANUAL
      const x1 = xScale(burn.burn_time)
      const x2 = xScale(burn.burn_time + DURATION)
      if (x2 < 0 || x1 > innerW) return

      g.append('rect')
        .attr('x', Math.max(0, x1)).attr('y', y)
        .attr('width', Math.min(x2, innerW) - Math.max(0, x1)).attr('height', h)
        .attr('fill', color).attr('rx', 2).attr('opacity', burn.queued ? 0.95 : 0.6)
        .append('title')
        .text(`${burn.burn_id}\n${burn.sat_id}\nΔV: ${burn.dv_m_s.toFixed(1)} m/s\nFuel: ${burn.fuel_cost.toFixed(2)} kg`)

      // Cooldown
      const cx1 = xScale(burn.burn_time + DURATION)
      const cx2 = xScale(burn.burn_time + DURATION + COOLDOWN_S)
      if (cx1 < innerW && cx2 > 0) {
        g.append('rect')
          .attr('x', Math.max(0, cx1)).attr('y', y)
          .attr('width', Math.min(cx2, innerW) - Math.max(0, cx1)).attr('height', h)
          .attr('fill', COOLDOWN_COLOR).attr('rx', 2).attr('opacity', 0.6)
      }
    })

    // Satellite labels
    satIds.forEach((satId, i) => {
      g.append('text')
        .attr('x', -4).attr('y', i * rowH + rowH / 2 + 4)
        .attr('text-anchor', 'end').attr('fill', '#94a3b8')
        .attr('font-size', 9).attr('font-family', 'monospace')
        .text(satId.replace('SAT-', ''))
    })

    // Current time line
    const ctX = xScale(currentTime)
    if (ctX >= 0 && ctX <= innerW) {
      g.append('line')
        .attr('x1', ctX).attr('x2', ctX).attr('y1', 0).attr('y2', innerH)
        .attr('stroke', CURRENT_TIME_COLOR).attr('stroke-width', 2).attr('opacity', 0.8)
      g.append('text')
        .attr('x', ctX + 2).attr('y', 10).attr('fill', CURRENT_TIME_COLOR)
        .attr('font-size', 9).attr('font-family', 'monospace').text('NOW')
    }

    // Legend
    const legendData = [
      { label: 'EVASION',  color: BURN_COLORS.EVASION },
      { label: 'RECOVERY', color: BURN_COLORS.RECOVERY_1 },
      { label: 'GRAVEYARD',color: BURN_COLORS.GRAVEYARD },
      { label: 'COOLDOWN', color: COOLDOWN_COLOR },
      { label: 'BLACKOUT', color: BLACKOUT_COLOR },
    ]
    const lg = svg.append('g').attr('transform', `translate(${W - 300}, 4)`)
    legendData.forEach((d, i) => {
      lg.append('rect').attr('x', i * 60).attr('y', 0).attr('width', 10).attr('height', 8)
        .attr('fill', d.color).attr('rx', 1)
      lg.append('text').attr('x', i * 60 + 13).attr('y', 8)
        .attr('fill', '#64748b').attr('font-size', 8).attr('font-family', 'monospace')
        .text(d.label)
    })

  }, [allSatIds, burnEvents, losWindows, currentTime])

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%', overflow: 'hidden' }}>
      <svg ref={svgRef} style={{ display: 'block', width: '100%', height: '100%' }} />
    </div>
  )
}
