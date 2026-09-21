import assert from "node:assert/strict";
import test from "node:test";
import {
  assignmentFingerprint,
  extractCourse,
  parseDueDate,
  parseSchoolMessage,
  shouldNotify,
} from "./school.ts";

const now = new Date("2026-08-30T17:00:00.000Z"); // noon in America/Chicago

test("extracts normalized course codes", () => {
  assert.equal(extractCourse("[Canvas] CSC 1351 Assignment 2 due"), "CSC 1351");
  assert.equal(extractCourse("BIOL-1201 lab reminder"), "BIOL 1201");
});

test("parses explicit and relative Central-time due dates", () => {
  assert.equal(parseDueDate("Due September 2, 2026 at 11:59 PM", now), "2026-09-03T04:59:00.000Z");
  assert.equal(parseDueDate("due tomorrow at 8:00 am", now), "2026-08-31T13:00:00.000Z");
  assert.equal(parseDueDate("no deadline supplied", now), null);
});

test("extracts actionable school information deterministically", () => {
  const parsed = parseSchoolMessage({
    id: "msg-1",
    subject: "[MATH 1021] Homework 4 reminder",
    bodyPreview: "Submit the homework by August 31 at 11:59 PM.",
    receivedDateTime: now.toISOString(),
  }, now);
  assert.deepEqual(parsed, {
    course: "MATH 1021",
    task: "Homework 4 reminder",
    dueAt: "2026-09-01T04:59:00.000Z",
    urgency: "medium",
    meaningful: true,
  });
  assert.equal(shouldNotify(parsed, now), true);
});

test("ignores non-actionable noise", () => {
  assert.equal(parseSchoolMessage({
    id: "msg-2",
    subject: "Campus newsletter",
    bodyPreview: "Events and general announcements for this month.",
    receivedDateTime: now.toISOString(),
  }, now), null);
});

test("fingerprints are stable and message-specific", async () => {
  const parsed = {
    course: "CSC 1351", task: "Project 1", dueAt: "2026-09-01T05:00:00.000Z",
    urgency: "medium", meaningful: true,
  };
  assert.equal(await assignmentFingerprint("a", parsed), await assignmentFingerprint("a", parsed));
  assert.notEqual(await assignmentFingerprint("a", parsed), await assignmentFingerprint("b", parsed));
});
