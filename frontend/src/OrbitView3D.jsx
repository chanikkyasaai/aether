import React, { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import TWEEN from '@tweenjs/tween.js'

const SCALE = 1.0 / 6378.137  // 1 Three.js unit = 1 Earth radius
const RE = 6378.137

// ECI to Three.js: Three.js uses Y-up, ECI uses Z-up
function toThree(x, y, z) {
  return new THREE.Vector3(x * SCALE, z * SCALE, -y * SCALE)
}

const STATUS_COLORS_3D = {
  NOMINAL: 0x22c55e,
  EVADING: 0xf59e0b,
  RECOVERING: 0x3b82f6,
  EOL: 0x6b7280,
}

const GROUND_STATIONS_3D = [
  { lat: 28.5459, lon: 77.1926 },
  { lat: 12.9716, lon: 77.5946 },
  { lat: 8.5241, lon: 76.9366 },
  { lat: -20.1609, lon: 57.4977 },
  { lat: -0.9145, lon: 134.9098 },
  { lat: 78.2292, lon: 15.4031 },
]

function latLonToECI(lat_deg, lon_deg, alt_km) {
  const lat = lat_deg * Math.PI / 180
  const lon = lon_deg * Math.PI / 180
  const r = RE + alt_km
  return {
    x: r * Math.cos(lat) * Math.cos(lon),
    y: r * Math.cos(lat) * Math.sin(lon),
    z: r * Math.sin(lat)
  }
}

export default function OrbitView3D({ snapshot, status, selectedSat, onClose }) {
  const mountRef = useRef(null)
  const sceneRef = useRef({})

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return

    // Scene setup
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x020810)

    const camera = new THREE.PerspectiveCamera(60, mount.clientWidth / mount.clientHeight, 0.01, 100)
    camera.position.set(0, 0, 3.5)

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setSize(mount.clientWidth, mount.clientHeight)
    renderer.setPixelRatio(window.devicePixelRatio)
    mount.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.05
    controls.minDistance = 1.2
    controls.maxDistance = 10

    // Lighting
    const ambientLight = new THREE.AmbientLight(0x404060, 0.8)
    scene.add(ambientLight)
    const sunLight = new THREE.DirectionalLight(0xffffff, 1.2)
    sunLight.position.set(5, 3, 5)
    scene.add(sunLight)

    // Earth sphere
    const earthGeo = new THREE.SphereGeometry(1.0, 64, 64)
    const earthMat = new THREE.MeshPhongMaterial({
      color: 0x1a3a6b,
      emissive: 0x0a1428,
      specular: 0x112244,
      shininess: 30,
    })
    const earth = new THREE.Mesh(earthGeo, earthMat)
    scene.add(earth)

    // Try to load NASA texture
    const loader = new THREE.TextureLoader()
    loader.load(
      'https://cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/textures/planets/earth_atmos_2048.jpg',
      (texture) => { earthMat.map = texture; earthMat.needsUpdate = true },
      undefined,
      () => {} // fallback on error
    )

    // Atmosphere glow
    const atmGeo = new THREE.SphereGeometry(1.02, 64, 64)
    const atmMat = new THREE.MeshPhongMaterial({
      color: 0x4488ff, transparent: true, opacity: 0.08, side: THREE.BackSide
    })
    scene.add(new THREE.Mesh(atmGeo, atmMat))

    // Grid sphere (subtle lat/lon lines)
    const gridGeo = new THREE.SphereGeometry(1.001, 36, 18)
    const gridMat = new THREE.MeshBasicMaterial({
      color: 0x1e3a5f, wireframe: true, transparent: true, opacity: 0.08
    })
    scene.add(new THREE.Mesh(gridGeo, gridMat))

    // Ground stations
    GROUND_STATIONS_3D.forEach(gs => {
      const pos = latLonToECI(gs.lat, gs.lon, 0)
      const p = toThree(pos.x, pos.y, pos.z)
      const coneGeo = new THREE.ConeGeometry(0.008, 0.03, 8)
      const coneMat = new THREE.MeshBasicMaterial({ color: 0x06b6d4 })
      const cone = new THREE.Mesh(coneGeo, coneMat)
      cone.position.copy(p)
      cone.lookAt(new THREE.Vector3(0, 0, 0))
      cone.rotateX(Math.PI / 2)
      scene.add(cone)
    })

    // Debris cloud — single Points object
    const MAX_DEB = 10000
    const debPositions = new Float32Array(MAX_DEB * 3)
    const debGeo = new THREE.BufferGeometry()
    debGeo.setAttribute('position', new THREE.BufferAttribute(debPositions, 3))
    const debMat = new THREE.PointsMaterial({ size: 0.003, color: 0xcc3322, opacity: 0.5, transparent: true })
    const debPoints = new THREE.Points(debGeo, debMat)
    scene.add(debPoints)

    // Satellite meshes
    const satMeshes = new Map()
    const createSatMesh = (satId, status) => {
      const geo = new THREE.SphereGeometry(0.008, 8, 8)
      const mat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS_3D[status] || STATUS_COLORS_3D.NOMINAL })
      const mesh = new THREE.Mesh(geo, mat)
      scene.add(mesh)
      satMeshes.set(satId, { mesh, status })
      return mesh
    }

    // Selected satellite trail
    const trailPoints = []
    const MAX_TRAIL = 108
    const trailGeo = new THREE.BufferGeometry()
    const trailPositions = new Float32Array(MAX_TRAIL * 3)
    trailGeo.setAttribute('position', new THREE.BufferAttribute(trailPositions, 3))
    const trailMat = new THREE.LineBasicMaterial({ color: 0x64c88c, opacity: 0.7, transparent: true })
    const trailLine = new THREE.Line(trailGeo, trailMat)
    scene.add(trailLine)

    // CDM threat line
    const threatGeo = new THREE.BufferGeometry()
    const threatPositions = new Float32Array(6)
    threatGeo.setAttribute('position', new THREE.BufferAttribute(threatPositions, 3))
    const threatMat = new THREE.LineBasicMaterial({ color: 0xef4444, opacity: 0.8, transparent: true })
    const threatLine = new THREE.Line(threatGeo, threatMat)
    scene.add(threatLine)

    // Burn vector arrow
    const arrowHelper = new THREE.ArrowHelper(
      new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 0), 0.2, 0xfbbf24
    )
    arrowHelper.visible = false
    scene.add(arrowHelper)

    sceneRef.current = {
      scene, camera, renderer, controls, earth, debPoints, debGeo, debPositions,
      satMeshes, trailLine, trailGeo, trailPositions, trailPoints,
      threatLine, threatGeo, threatPositions, arrowHelper
    }

    // Animation loop
    let animId
    const animate = () => {
      animId = requestAnimationFrame(animate)
      TWEEN.update()
      controls.update()
      earth.rotation.y += 0.0001  // slow Earth rotation
      renderer.render(scene, camera)
    }
    animate()

    // Resize handler
    const handleResize = () => {
      if (!mount) return
      camera.aspect = mount.clientWidth / mount.clientHeight
      camera.updateProjectionMatrix()
      renderer.setSize(mount.clientWidth, mount.clientHeight)
    }
    window.addEventListener('resize', handleResize)

    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener('resize', handleResize)
      mount.removeChild(renderer.domElement)
      renderer.dispose()
    }
  }, [])

  // Update scene with new snapshot data
  useEffect(() => {
    const s = sceneRef.current
    if (!s.scene) return

    // Update debris cloud
    const debris = snapshot.debris_cloud || []
    const { debPositions, debGeo } = s
    for (let i = 0; i < Math.min(debris.length, 10000); i++) {
      const d = debris[i]
      // debris_cloud is [id, lat, lon, alt_km] — convert to ECI approximate
      const lat = d[1] * Math.PI / 180
      const lon = d[2] * Math.PI / 180
      const r = (RE + (d[3] || 550)) / RE  // normalized
      const pos = toThree(
        r * RE * Math.cos(lat) * Math.cos(lon),
        r * RE * Math.cos(lat) * Math.sin(lon),
        r * RE * Math.sin(lat)
      )
      debPositions[i * 3] = pos.x
      debPositions[i * 3 + 1] = pos.y
      debPositions[i * 3 + 2] = pos.z
    }
    debGeo.attributes.position.needsUpdate = true
    debGeo.setDrawRange(0, Math.min(debris.length, 10000))

    // Update satellites
    const sats = snapshot.satellites || []
    for (const sat of sats) {
      const lat = sat.lat * Math.PI / 180
      const lon = sat.lon * Math.PI / 180
      const alt = 550  // approximate — use nominal altitude
      const r = (RE + alt) / RE
      const pos = toThree(
        r * RE * Math.cos(lat) * Math.cos(lon),
        r * RE * Math.cos(lat) * Math.sin(lon),
        r * RE * Math.sin(lat)
      )

      let entry = s.satMeshes.get(sat.id)
      if (!entry) {
        const geo = new THREE.SphereGeometry(0.008, 8, 8)
        const mat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS_3D[sat.status] || STATUS_COLORS_3D.NOMINAL })
        const mesh = new THREE.Mesh(geo, mat)
        s.scene.add(mesh)
        entry = { mesh, status: sat.status }
        s.satMeshes.set(sat.id, entry)
      }

      entry.mesh.position.copy(pos)
      if (entry.status !== sat.status) {
        entry.mesh.material.color.setHex(STATUS_COLORS_3D[sat.status] || STATUS_COLORS_3D.NOMINAL)
        entry.status = sat.status
      }

      // Update trail for selected satellite
      if (sat.id === selectedSat) {
        s.trailPoints.push(pos.clone())
        if (s.trailPoints.length > 108) s.trailPoints.shift()
        for (let i = 0; i < s.trailPoints.length; i++) {
          s.trailPositions[i * 3] = s.trailPoints[i].x
          s.trailPositions[i * 3 + 1] = s.trailPoints[i].y
          s.trailPositions[i * 3 + 2] = s.trailPoints[i].z
        }
        s.trailGeo.attributes.position.needsUpdate = true
        s.trailGeo.setDrawRange(0, s.trailPoints.length)
      }
    }
  }, [snapshot, selectedSat])

  // Camera focus on selected satellite
  useEffect(() => {
    const s = sceneRef.current
    if (!s.camera || !selectedSat) return

    const entry = s.satMeshes.get(selectedSat)
    if (!entry) return

    const targetPos = entry.mesh.position.clone()
    const camTarget = targetPos.clone().multiplyScalar(2.5)

    new TWEEN.Tween(s.camera.position)
      .to({ x: camTarget.x, y: camTarget.y, z: camTarget.z }, 1500)
      .easing(TWEEN.Easing.Quadratic.InOut)
      .start()

    new TWEEN.Tween(s.controls.target)
      .to({ x: targetPos.x, y: targetPos.y, z: targetPos.z }, 1500)
      .easing(TWEEN.Easing.Quadratic.InOut)
      .start()
  }, [selectedSat])

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.95)',
      zIndex: 9999, display: 'flex', flexDirection: 'column'
    }}>
      {/* 3D View header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 16px', background: '#060e1c', borderBottom: '1px solid #1e3a5f',
        flexShrink: 0
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{ color: '#06b6d4', fontWeight: 'bold', fontSize: '13px', letterSpacing: '0.2em' }}>
            ◈ AETHER — 3D ORBITAL VIEW
          </span>
          {selectedSat && (
            <span style={{ color: '#f59e0b', fontSize: '11px' }}>TRACKING: {selectedSat}</span>
          )}
        </div>
        <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          <span style={{ color: '#475569', fontSize: '10px' }}>
            ROTATE: drag | ZOOM: scroll | PAN: right-drag
          </span>
          <button
            onClick={onClose}
            style={{
              background: 'rgba(239,68,68,0.15)',
              border: '1px solid #ef4444',
              color: '#ef4444',
              padding: '4px 12px',
              fontSize: '12px',
              cursor: 'pointer',
              borderRadius: '3px',
              fontFamily: 'monospace',
              fontWeight: 'bold'
            }}
          >
            ✕ CLOSE
          </button>
        </div>
      </div>

      {/* Legend */}
      <div style={{
        position: 'absolute', bottom: 16, left: 16, zIndex: 10001,
        background: 'rgba(6,14,28,0.85)', border: '1px solid #1e3a5f',
        padding: '8px 12px', borderRadius: '4px', fontSize: '10px', fontFamily: 'monospace'
      }}>
        {[
          { label: 'NOMINAL', color: '#22c55e' },
          { label: 'EVADING', color: '#f59e0b' },
          { label: 'RECOVERING', color: '#3b82f6' },
          { label: 'EOL', color: '#6b7280' },
          { label: 'DEBRIS', color: '#cc3322' },
          { label: 'GND STATION', color: '#06b6d4' },
          { label: 'BURN VECTOR', color: '#fbbf24' },
        ].map(({ label, color }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />
            <span style={{ color: '#94a3b8' }}>{label}</span>
          </div>
        ))}
      </div>

      {/* Three.js mount */}
      <div ref={mountRef} style={{ flex: 1, position: 'relative' }} />
    </div>
  )
}
