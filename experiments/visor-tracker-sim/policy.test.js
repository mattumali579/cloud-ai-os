import assert from "node:assert/strict"
import fs from "node:fs"
import test from "node:test"
import { createLockPolicy, createResultGate, SPEC } from "./policy.js"

const fileSpec = JSON.parse(fs.readFileSync(new URL("./spec.json", import.meta.url), "utf8"))

test("spec file matches the policy numbers and does not claim a shared executable", () => {
  assert.equal(fileSpec.knownReplacementFrames, SPEC.knownReplacementFrames)
  assert.equal(fileSpec.occlusionHoldFrames, SPEC.occlusionHoldFrames)
  assert.equal(fileSpec.releaseFrames, SPEC.releaseFrames)
  assert.equal(fileSpec.fingerLockHoldsWhenHandDrops, true)
  assert.equal(fileSpec.executablesShared, false)
  assert.equal(SPEC.executablesShared, false)
  assert.match(fileSpec.simulatorRay, /not what the glasses do/i)
  assert.match(fileSpec.glassesRay, /cannot measure depth/i)
})

test("hiding A acquires known replacement B within 2 ticks", () => {
  const policy = createLockPolicy()
  const both = {
    visibleIds: ["A", "B"],
    fingerTargetId: "A",
    handPresent: true,
    replacementId: "B",
  }
  policy.observe(both)
  assert.equal(policy.observe(both).lockedId, "A")
  assert.equal(policy.fingerLocked, true)
  const hidden = {
    visibleIds: ["B"],
    fingerTargetId: null,
    handPresent: false,
    replacementId: "B",
  }
  const first = policy.observe(hidden)
  assert.equal(first.lockedId, "A")
  assert.equal(first.changed, false)
  const second = policy.observe(hidden)
  assert.equal(second.lockedId, "B")
  assert.equal(second.changed, true)
  assert.equal(second.sendsExternalCommand, false)
})

test("occlusion with no candidate holds for 3 ticks", () => {
  const policy = createLockPolicy()
  const picture = { visibleIds: ["A"], fingerTargetId: "A", handPresent: true }
  policy.observe(picture)
  assert.equal(policy.observe(picture).lockedId, "A")
  const empty = { visibleIds: [], fingerTargetId: null, handPresent: false, replacementId: null }
  assert.equal(policy.observe(empty).lockedId, "A")
  assert.equal(policy.observe(empty).lockedId, "A")
  const cleared = policy.observe(empty)
  assert.equal(cleared.lockedId, null)
  assert.equal(cleared.changed, true)
})

test("a finger lock holds when the hand drops", () => {
  const policy = createLockPolicy()
  const picture = {
    visibleIds: ["A", "B"],
    fingerTargetId: "A",
    handPresent: true,
    centerId: "B",
  }
  policy.observe(picture)
  policy.observe(picture)
  const dropped = {
    visibleIds: ["A", "B"],
    fingerTargetId: null,
    handPresent: false,
    centerId: "B",
  }
  assert.equal(policy.observe(dropped).lockedId, "A")
  assert.equal(policy.observe(dropped).lockedId, "A")
  assert.equal(policy.fingerLocked, true)
})

test("one closed frame does not release or send a command", () => {
  const policy = createLockPolicy()
  const picture = { visibleIds: ["A", "B"], fingerTargetId: "A", handPresent: true }
  policy.observe(picture)
  policy.observe(picture)
  const closed = {
    visibleIds: ["A", "B"],
    fingerTargetId: null,
    handPresent: true,
    closed: true,
    replacementId: "B",
  }
  const once = policy.observe(closed)
  assert.equal(once.lockedId, "A")
  assert.equal(once.released, false)
  assert.equal(once.sendsExternalCommand, false)
})

test("a late result does not overwrite a newer camera tick", () => {
  const gate = createResultGate()
  gate.noteCameraTick(1)
  gate.submit({ tick: 1, readyAt: 100, observation: { id: "old" } })
  gate.noteCameraTick(2)
  gate.submit({ tick: 2, readyAt: 150, observation: { id: "new" } })
  assert.equal(gate.collect(50).length, 0)
  const applied = gate.collect(200)
  assert.equal(applied.length, 1)
  assert.equal(applied[0].tick, 2)
  assert.equal(applied[0].observation.id, "new")
  assert.equal(gate.dropped, 1)
  assert.equal(gate.collect(200).length, 0)
})

test("a delayed hide does not replace a newer camera tick", () => {
  const gate = createResultGate()
  const policy = createLockPolicy()
  const both = {
    visibleIds: ["A", "B"],
    fingerTargetId: "A",
    handPresent: true,
    replacementId: "B",
  }
  policy.observe(both)
  assert.equal(policy.observe(both).lockedId, "A")
  gate.noteCameraTick(5)
  gate.submit({
    tick: 4,
    readyAt: 10,
    observation: {
      visibleIds: ["B"],
      fingerTargetId: null,
      handPresent: false,
      replacementId: "B",
    },
  })
  gate.submit({ tick: 5, readyAt: 10, observation: both })
  const applied = gate.collect(10)
  assert.equal(applied.length, 1)
  assert.equal(applied[0].tick, 5)
  assert.equal(policy.observe(applied[0].observation).lockedId, "A")
  assert.equal(gate.dropped, 1)
})
