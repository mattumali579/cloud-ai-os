/**
 * Numbers from AimLock and GestureGate.
 * Kotlin and this file do not share an executable. Do not treat a pass here
 * as a pass of the Android tracker. The page's raycast is idealized 3D.
 * Version 0.30 can use hand z to select its pointer mode. Object boxes remain
 * 2D and cannot measure depth.
 */
export const SPEC = {
  knownReplacementFrames: 2,
  occlusionHoldFrames: 3,
  acquireFrames: 2,
  switchFrames: 2,
  releaseFrames: 4,
  fingerLockHoldsWhenHandDrops: true,
  executablesShared: false,
}

/**
 * Exposes the pointer-mode transition used by the depth lab. This models the
 * v31 decision boundary; it is not MediaPipe and does not prove phone output.
 */
export function depthPointerMode(angleDegrees) {
  const clamped = Math.max(0, Math.min(90, Number(angleDegrees) || 0))
  const radians = clamped * Math.PI / 180
  const projected = Math.cos(radians)
  const depth = Math.sin(radians)
  return {
    angleDegrees: clamped,
    projectedFraction: projected,
    legacyMode: projected < 0.25 ? "not recognized" : "2D ray",
    depthAwareMode: depth > projected * 0.75 ? "fingertip depth cursor" : "2D ray",
  }
}

export function createLockPolicy(spec = SPEC) {
  let lockedId = null
  let fingerLocked = false
  let pendingId = null
  let pendingCount = 0
  let lostFrames = 0
  let closedFrames = 0

  function snapshot(changed, released = false) {
    return {
      lockedId,
      changed,
      released,
      fingerLocked,
      sendsExternalCommand: false,
    }
  }

  function commit(aimed, required, pointing) {
    if (aimed == null || aimed === lockedId) {
      pendingId = null
      pendingCount = 0
      if (pointing && lockedId != null) fingerLocked = true
      return snapshot(false)
    }
    if (pendingId === aimed) pendingCount += 1
    else {
      pendingId = aimed
      pendingCount = 1
    }
    if (pendingCount < required) return snapshot(false)
    lockedId = aimed
    pendingId = null
    pendingCount = 0
    lostFrames = 0
    if (pointing) fingerLocked = true
    return snapshot(true)
  }

  function observe(input) {
    const visible = input.visibleIds || []
    const fingerTargetId = input.fingerTargetId ?? null
    const handPresent = Boolean(input.handPresent)
    const closed = Boolean(input.closed)
    const replacementId = input.replacementId ?? visible.find((id) => id !== lockedId) ?? null
    const currentVisible = lockedId != null && visible.includes(lockedId)

    if (lockedId != null && closed) {
      closedFrames += 1
      pendingId = null
      pendingCount = 0
      if (closedFrames >= spec.releaseFrames) {
        closedFrames = 0
        const changed = lockedId != null
        lockedId = null
        fingerLocked = false
        lostFrames = 0
        return snapshot(changed, true)
      }
      return snapshot(false)
    }
    closedFrames = 0

    if (lockedId != null && !currentVisible) {
      const next = fingerTargetId != null && visible.includes(fingerTargetId)
        ? fingerTargetId
        : replacementId != null && visible.includes(replacementId)
          ? replacementId
          : null
      if (next != null) {
        lostFrames = 0
        return commit(next, spec.knownReplacementFrames, fingerTargetId != null && next === fingerTargetId)
      }
      pendingId = null
      pendingCount = 0
      lostFrames += 1
      if (lostFrames < spec.occlusionHoldFrames) return snapshot(false)
      lockedId = null
      fingerLocked = false
      lostFrames = 0
      return snapshot(true)
    }

    lostFrames = 0
    let aimed = null
    let pointing = false
    if (fingerTargetId != null && visible.includes(fingerTargetId)) {
      aimed = fingerTargetId
      pointing = true
    } else if ((fingerLocked || handPresent) && lockedId != null) {
      aimed = lockedId
    } else if (!handPresent) {
      const center = input.centerId
      aimed = center != null && visible.includes(center) ? center : (visible[0] ?? null)
    }
    const required = lockedId == null ? spec.acquireFrames : spec.switchFrames
    return commit(aimed, required, pointing)
  }

  return {
    observe,
    get lockedId() {
      return lockedId
    },
    get fingerLocked() {
      return fingerLocked
    },
  }
}

/**
 * Camera ticks and detector results are separate.
 * A result whose tick is older than the newest camera tick is dropped, even
 * if it becomes ready later. This is not MediaPipe and it does not share an
 * executable with the Kotlin tracker.
 */
export function createResultGate() {
  let newestTick = 0
  let appliedTick = 0
  let dropped = 0
  const waiting = []

  function noteCameraTick(tick) {
    if (tick > newestTick) newestTick = tick
  }

  function submit(result) {
    waiting.push(result)
  }

  function collect(now) {
    const due = []
    const later = []
    for (const result of waiting) {
      if (result.readyAt <= now) due.push(result)
      else later.push(result)
    }
    waiting.length = 0
    for (const result of later) waiting.push(result)
    const newestDue = due.reduce((max, result) => Math.max(max, result.tick), 0)
    const accepted = []
    for (const result of due.sort((a, b) => a.tick - b.tick)) {
      // Ready results older than the newest ready tick, or older than a tick
      // already applied, do not overwrite that newer tick.
      if (result.tick < newestDue || result.tick < appliedTick) {
        dropped += 1
        continue
      }
      accepted.push(result)
      appliedTick = result.tick
    }
    return accepted
  }

  return {
    noteCameraTick,
    submit,
    collect,
    get dropped() {
      return dropped
    },
    get newestTick() {
      return newestTick
    },
  }
}
