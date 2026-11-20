import * as THREE from "https://esm.sh/three@0.170.0"
import { createLockPolicy, createResultGate, depthPointerMode } from "./policy.js"

const canvas = document.querySelector("#view")
const fpsEl = document.querySelector("#fps")
const detectEl = document.querySelector("#detect")
const ageEl = document.querySelector("#age")
const lockEl = document.querySelector("#lock")
const switchEl = document.querySelector("#switch")
const statusEl = document.querySelector("#status")
const stageNoteEl = document.querySelector("#stage-note")
const fingerDemoEl = document.querySelector("#finger-demo")
const depthSliderEl = document.querySelector("#depth-slider")
const depthValueEl = document.querySelector("#depth-value")
const depthAngleEl = document.querySelector("#depth-angle")
const legacyPointerEl = document.querySelector("#legacy-pointer")
const depthPointerEl = document.querySelector("#depth-pointer")

function cappedPixelRatio() {
  return Math.min(window.devicePixelRatio || 1, 1)
}

const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "low-power" })
renderer.setPixelRatio(cappedPixelRatio())
renderer.setSize(window.innerWidth, window.innerHeight, false)

const scene = new THREE.Scene()
scene.background = new THREE.Color(0x1a1d23)
const camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight, 0.08, 80)
camera.position.set(0, 1.6, 6)
camera.rotation.order = "YXZ"

scene.add(new THREE.AmbientLight(0xffffff, 0.55))
const sun = new THREE.DirectionalLight(0xfff4e0, 0.9)
sun.position.set(4, 8, 2)
scene.add(sun)

const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(40, 40),
  new THREE.MeshLambertMaterial({ color: 0x3d4450 }),
)
floor.rotation.x = -Math.PI / 2
scene.add(floor)

function wall(x, z, sx, sz, color) {
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(sx, 3.2, sz),
    new THREE.MeshLambertMaterial({ color }),
  )
  mesh.position.set(x, 1.6, z)
  scene.add(mesh)
}
wall(0, -16, 28, 0.4, 0x2c3340)
wall(-14, -4, 0.4, 24, 0x343c4a)
wall(14, -4, 0.4, 24, 0x343c4a)

const targets = [
  makeTarget(1, "crate", new THREE.BoxGeometry(0.9, 0.9, 0.9), 0xc47b4a, -2.2, 0.45, -4),
  makeTarget(2, "sphere", new THREE.SphereGeometry(0.45, 8, 6), 0x4aa3c4, 1.6, 0.7, -8),
  makeTarget(3, "cone", new THREE.ConeGeometry(0.4, 1.1, 7), 0xc4c44a, 0.2, 0.55, -12),
]
const hidden = new Set()
let motionOn = true
const policy = createLockPolicy()
const gate = createResultGate()
let mode = "ideal"
let inferenceDelayMs = 80
let handIntervalMs = 66
let objectIntervalMs = 250
let captureSerial = 0
let lastHandCapture = 0
let lastObjectCapture = 0
let lastAppliedAt = 0
let frameAgeMs = 0
let detectorFrames = 0
let detectorFps = 0
let detectorStamp = performance.now()
const raycaster = new THREE.Raycaster()
const center = new THREE.Vector2(0, 0)
const frustum = new THREE.Frustum()
const projView = new THREE.Matrix4()
const ndc = new THREE.Vector3()

const keys = new Set()
let yaw = 0
let pitch = 0
let lastSwitchMs = 0
let divergeAt = 0
let frames = 0
let fpsStamp = performance.now()
let lastFrame = performance.now()

function makeTarget(id, label, geometry, color, x, y, z) {
  const mesh = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({ color }))
  mesh.position.set(x, y, z)
  mesh.userData = { id, label, baseX: x, baseY: y, baseZ: z }
  scene.add(mesh)
  return mesh
}

canvas.addEventListener("click", () => {
  canvas.requestPointerLock()
})
document.addEventListener("mousemove", (event) => {
  if (document.pointerLockElement !== canvas) return
  yaw -= event.movementX * 0.0022
  pitch -= event.movementY * 0.0022
  pitch = Math.max(-1.2, Math.min(1.2, pitch))
})
document.addEventListener("keydown", (event) => {
  keys.add(event.code)
  if (event.code === "KeyH") hideLocked()
  if (event.code === "KeyR") hidden.clear()
  if (event.code === "KeyM") motionOn = !motionOn
  if (event.code === "Digit1") setMode("ideal")
  if (event.code === "Digit2") setMode("realistic")
  if (event.code.startsWith("Arrow")) event.preventDefault()
})
document.addEventListener("keyup", (event) => keys.delete(event.code))
window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight
  camera.updateProjectionMatrix()
  renderer.setPixelRatio(cappedPixelRatio())
  renderer.setSize(window.innerWidth, window.innerHeight, false)
})

document.querySelector("#mode-ideal").addEventListener("click", () => setMode("ideal"))
document.querySelector("#mode-realistic").addEventListener("click", () => setMode("realistic"))
document.querySelector("#delay-ms").addEventListener("input", readIntervals)
document.querySelector("#hand-ms").addEventListener("input", readIntervals)
document.querySelector("#object-ms").addEventListener("input", readIntervals)
depthSliderEl.addEventListener("input", updateDepthLab)
document.querySelector("#stage-file").addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0]
  if (!file) return
  const data = JSON.parse(await file.text())
  applyStageTimings(data)
})
document.querySelector("#load-sample").addEventListener("click", async () => {
  const response = await fetch("./stage-timings.sample.json")
  applyStageTimings(await response.json())
})

function updateDepthLab() {
  const state = depthPointerMode(depthSliderEl.value)
  const percent = Math.max(0.08, state.projectedFraction)
  fingerDemoEl.style.transform = `scaleX(${percent})`
  depthValueEl.textContent = `${state.angleDegrees.toFixed(0)}°`
  depthAngleEl.textContent = `Finger depth angle ${state.angleDegrees.toFixed(0)}° · projected length ${(state.projectedFraction * 100).toFixed(0)}%`
  legacyPointerEl.textContent = `v30 pointer ${state.legacyMode}`
  depthPointerEl.textContent = `v31 pointer ${state.depthAwareMode}`
}

function setMode(next) {
  mode = next
  captureSerial = 0
  lastAppliedAt = 0
  frameAgeMs = 0
  statusEl.textContent = next === "ideal"
    ? "Ideal mode. Observations are applied on the render frame."
    : "Realistic mode. Hand and object results wait out the inference delay."
}

function readIntervals() {
  inferenceDelayMs = readNumber("#delay-ms", inferenceDelayMs)
  handIntervalMs = readNumber("#hand-ms", handIntervalMs)
  objectIntervalMs = readNumber("#object-ms", objectIntervalMs)
}

function readNumber(selector, fallback) {
  const value = Number(document.querySelector(selector).value)
  return Number.isFinite(value) && value >= 0 ? value : fallback
}

function applyStageTimings(data) {
  if (Number.isFinite(data.handMs)) document.querySelector("#hand-ms").value = String(data.handMs)
  if (Number.isFinite(data.objectMs)) document.querySelector("#object-ms").value = String(data.objectMs)
  if (Number.isFinite(data.delayMs)) document.querySelector("#delay-ms").value = String(data.delayMs)
  readIntervals()
  stageNoteEl.textContent = data.note ||
    "Loaded timings into the realistic intervals. This page does not run MediaPipe."
}

function hideLocked() {
  if (policy.lockedId != null) hidden.add(policy.lockedId)
}

function movePlayer(dt) {
  const speed = keys.has("ShiftLeft") ? 6 : 3.2
  const sin = Math.sin(yaw)
  const cos = Math.cos(yaw)
  let forward = 0
  let strafe = 0
  if (keys.has("KeyW")) forward += 1
  if (keys.has("KeyS")) forward -= 1
  if (keys.has("KeyD")) strafe += 1
  if (keys.has("KeyA")) strafe -= 1
  camera.position.x += (sin * forward + cos * strafe) * speed * dt
  camera.position.z += (cos * forward - sin * strafe) * speed * dt
  camera.position.x = Math.max(-12, Math.min(12, camera.position.x))
  camera.position.z = Math.max(-14, Math.min(12, camera.position.z))
}

function nudgeObjects() {
  const step = keys.has("ArrowLeft") ? -0.04 : keys.has("ArrowRight") ? 0.04 : 0
  const depth = keys.has("ArrowUp") ? -0.04 : keys.has("ArrowDown") ? 0.04 : 0
  if (step === 0 && depth === 0) return
  for (const mesh of targets) {
    mesh.userData.baseX += step
    mesh.userData.baseZ += depth
  }
}

function animateObjects(time) {
  for (const mesh of targets) {
    const data = mesh.userData
    const bob = motionOn && data.id !== 1 ? Math.sin(time * 0.001 + data.id) * 0.35 : 0
    const sway = motionOn && data.id === 2 ? Math.sin(time * 0.0007) * 1.2 : 0
    mesh.position.set(data.baseX + sway, data.baseY + bob, data.baseZ)
    mesh.visible = !hidden.has(data.id)
  }
}

function screenCenterId(visibleMeshes) {
  let best = null
  let bestDistance = Infinity
  for (const mesh of visibleMeshes) {
    ndc.copy(mesh.position).project(camera)
    const distance = ndc.x * ndc.x + ndc.y * ndc.y
    if (distance < bestDistance) {
      bestDistance = distance
      best = mesh.userData.id
    }
  }
  return best
}

function sceneObservation() {
  camera.updateMatrixWorld()
  projView.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse)
  frustum.setFromProjectionMatrix(projView)
  const visibleMeshes = targets.filter((mesh) => mesh.visible && frustum.intersectsObject(mesh))
  raycaster.setFromCamera(center, camera)
  const hits = raycaster.intersectObjects(visibleMeshes, false)
  const hit = hits[0]?.object ?? null
  const visibleIds = visibleMeshes.map((mesh) => mesh.userData.id)
  const centerId = screenCenterId(visibleMeshes)
  const replacementId = visibleIds.find((id) => id !== policy.lockedId) ?? null
  return {
    visibleIds,
    fingerTargetId: hit ? hit.userData.id : null,
    handPresent: hit != null,
    replacementId: centerId != null && centerId !== policy.lockedId ? centerId : replacementId,
    centerId,
    closed: keys.has("KeyF"),
    candidate: hit ? hit.userData.id : centerId,
  }
}

function noteDivergence(observation, now) {
  const before = policy.lockedId
  const candidate = observation.candidate
  if (before != null && candidate != null && candidate !== before) {
    if (divergeAt === 0) divergeAt = now
  } else if (before != null && !observation.visibleIds.includes(before)) {
    if (divergeAt === 0) divergeAt = now
  } else {
    divergeAt = 0
  }
}

function applyObservation(observation, now) {
  const before = policy.lockedId
  const decision = policy.observe(observation)
  if (decision.changed && decision.lockedId !== before) {
    lastSwitchMs = divergeAt === 0 ? 0 : now - divergeAt
    divergeAt = 0
  }
  const lockedMesh = targets.find((mesh) => mesh.userData.id === decision.lockedId)
  lockEl.textContent = lockedMesh
    ? `Lock ${lockedMesh.userData.label}  track ${lockedMesh.userData.id}`
    : "Lock none"
  const hiddenLabel = hidden.size ? ` hidden ${[...hidden].join(",")}` : ""
  const modeLabel = mode === "ideal" ? "Ideal" : "Realistic"
  statusEl.textContent = decision.lockedId == null
    ? `${modeLabel}. No lock.${hiddenLabel} H hides. R restores. No command is sent.`
    : `${modeLabel}. Tracking ${decision.lockedId}.${hiddenLabel} H hides. R restores. No command is sent.`
  return decision
}

function capture(now) {
  captureSerial += 1
  gate.noteCameraTick(captureSerial)
  gate.submit({
    tick: captureSerial,
    readyAt: now + inferenceDelayMs,
    capturedAt: now,
    observation: sceneObservation(),
  })
}

function tick(now) {
  const dt = Math.min(0.05, (now - lastFrame) / 1000)
  lastFrame = now
  camera.rotation.y = yaw
  camera.rotation.x = pitch
  movePlayer(dt)
  nudgeObjects()
  animateObjects(now)

  const live = sceneObservation()
  noteDivergence(live, now)
  if (mode === "ideal") {
    applyObservation(live, now)
    frameAgeMs = 0
    detectorFrames += 1
  } else {
    if (lastHandCapture === 0 || now - lastHandCapture >= handIntervalMs) {
      lastHandCapture = now
      capture(now)
    }
    if (lastObjectCapture === 0 || now - lastObjectCapture >= objectIntervalMs) {
      lastObjectCapture = now
      capture(now)
    }
    const accepted = gate.collect(now)
    for (const result of accepted) {
      applyObservation(result.observation, now)
      lastAppliedAt = result.capturedAt
      detectorFrames += 1
    }
    frameAgeMs = lastAppliedAt === 0 ? 0 : Math.max(0, now - lastAppliedAt)
  }

  switchEl.textContent = `Last switch ${lastSwitchMs.toFixed(0)} ms`
  ageEl.textContent = `Frame age ${frameAgeMs.toFixed(0)} ms`
  frames += 1
  if (now - fpsStamp >= 500) {
    const fps = (frames * 1000) / (now - fpsStamp)
    fpsEl.textContent = `Render FPS ${fps.toFixed(0)}`
    frames = 0
    fpsStamp = now
  }
  if (now - detectorStamp >= 500) {
    detectorFps = (detectorFrames * 1000) / (now - detectorStamp)
    detectEl.textContent = `Detector FPS ${detectorFps.toFixed(1)}`
    detectorFrames = 0
    detectorStamp = now
  }
  renderer.render(scene, camera)
  requestAnimationFrame(tick)
}

requestAnimationFrame(tick)
updateDepthLab()
