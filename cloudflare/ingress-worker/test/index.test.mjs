// node --test suite for cloudos-ingress (Node >= 18: global fetch classes + WebCrypto).
// Run from cloudflare/ingress-worker/:  node --test test/
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";

import workerDefault, {
  verifySignature,
  signBody,
  isAllowedApiPath,
  routeRequest,
  runHeartbeat,
  runScheduled,
  formatChicagoTimestamp,
  hexToBytes,
  timingSafeEqualStr,
} from "../src/index.js";

const SECRET = "test-webhook-secret";
const TOKEN = "test-agent-token";
const ORIGIN = "https://origin.example.com";

const baseEnv = {
  WEBHOOK_SECRET: SECRET,
  AGENT_API_TOKEN: TOKEN,
  ORIGIN_URL: ORIGIN,
  NOTIFY_WEBHOOK_URL: "https://notify.example.com/webhook/health-alert",
  DISCORD_WEBHOOK_URL: "https://discord.example.com/api/webhooks/test",
};

const enc = new TextEncoder();
const hmacHex = (secret, body) => createHmac("sha256", secret).update(body).digest("hex");

/** Recording mock fetch: returns `respond(url, init)` (default 200 {"ok":true}). */
function mockFetch(respond) {
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({ url, init });
    if (respond) return respond(url, init);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  fn.calls = calls;
  return fn;
}

/* ------------------------------------------------------------- HMAC verify */

test("verifySignature accepts a correct hex sha256 HMAC", async () => {
  const body = enc.encode('{"event":"lead.created","data":{}}');
  assert.equal(await verifySignature(SECRET, hmacHex(SECRET, body), body), true);
});

test("verifySignature accepts uppercase hex", async () => {
  const body = enc.encode("payload");
  const sig = hmacHex(SECRET, body).toUpperCase();
  assert.equal(await verifySignature(SECRET, sig, body), true);
});

test("verifySignature rejects tampered body", async () => {
  const sig = hmacHex(SECRET, enc.encode("original"));
  assert.equal(await verifySignature(SECRET, sig, enc.encode("tampered")), false);
});

test("verifySignature rejects wrong secret", async () => {
  const body = enc.encode("payload");
  assert.equal(await verifySignature(SECRET, hmacHex("other-secret", body), body), false);
});

test("verifySignature fails closed on malformed/missing input", async () => {
  const body = enc.encode("payload");
  assert.equal(await verifySignature(SECRET, "", body), false);
  assert.equal(await verifySignature(SECRET, "not-hex!", body), false);
  assert.equal(await verifySignature(SECRET, "deadbeef", body), false); // wrong length
  assert.equal(await verifySignature("", hmacHex(SECRET, body), body), false); // no secret
  assert.equal(await verifySignature(undefined, hmacHex(SECRET, body), body), false);
});

test("signBody round-trips through verifySignature and matches node HMAC", async () => {
  const body = enc.encode('{"severity":"critical"}');
  const sig = await signBody(SECRET, body);
  assert.equal(sig, hmacHex(SECRET, body));
  assert.equal(await verifySignature(SECRET, sig, body), true);
});

test("hexToBytes rejects odd-length and non-hex", () => {
  assert.equal(hexToBytes("abc"), null);
  assert.equal(hexToBytes("zz"), null);
  assert.equal(hexToBytes(""), null);
  assert.deepEqual([...hexToBytes("00ff")], [0, 255]);
});

test("timingSafeEqualStr basic behavior", () => {
  assert.equal(timingSafeEqualStr("Bearer x", "Bearer x"), true);
  assert.equal(timingSafeEqualStr("Bearer x", "Bearer y"), false);
  assert.equal(timingSafeEqualStr("short", "longer-string"), false);
  assert.equal(timingSafeEqualStr("", ""), true);
});

/* ---------------------------------------------------------- path allowlist */

test("isAllowedApiPath allows exactly the contract prefixes", () => {
  for (const p of ["/v1/jobs", "/v1/jobs/abc-123", "/v1/jobs/abc/cancel",
    "/v1/runs", "/v1/quota", "/v1/agent/invoke"]) {
    assert.equal(isAllowedApiPath(p), true, p);
  }
  for (const p of ["/v1/jobsx", "/v1/webhooks/n8n", "/healthz", "/v1/agent",
    "/v1/agents/invoke", "/", "/v1", "/v2/jobs"]) {
    assert.equal(isAllowedApiPath(p), false, p);
  }
});

/* ------------------------------------------------------------------ routes */

test("GET /ping returns 200 {status:ok} without auth", async () => {
  const resp = await routeRequest(new Request("https://w.dev/ping"), baseEnv, mockFetch());
  assert.equal(resp.status, 200);
  assert.deepEqual(await resp.json(), { status: "ok" });
});

test("unknown route returns 404 in contract error shape", async () => {
  const resp = await routeRequest(new Request("https://w.dev/nope"), baseEnv, mockFetch());
  assert.equal(resp.status, 404);
  const body = await resp.json();
  assert.equal(body.error.code, "VALIDATION_ERROR");
  assert.ok(body.error.message);
  assert.deepEqual(Object.keys(body.error).sort(), ["code", "details", "message"]);
});

test("POST /hook/* with valid signature forwards to origin and relays status", async () => {
  const payload = '{"event":"health","data":{"x":1}}';
  const fetch = mockFetch(() =>
    new Response('{"job":{"id":"j1"}}', { status: 201, headers: { "Content-Type": "application/json" } }));
  const req = new Request("https://w.dev/hook/n8n-lead", {
    method: "POST",
    headers: { "X-Signature": hmacHex(SECRET, payload), "Content-Type": "application/json" },
    body: payload,
  });
  const resp = await routeRequest(req, baseEnv, fetch);
  assert.equal(resp.status, 201); // origin status relayed
  assert.equal(fetch.calls.length, 1);
  assert.equal(fetch.calls[0].url, `${ORIGIN}/v1/webhooks/n8n`);
  assert.equal(fetch.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(new TextDecoder().decode(fetch.calls[0].init.body), payload);
});

test("POST /hook/* with invalid signature → 401, origin never called", async () => {
  const fetch = mockFetch();
  const req = new Request("https://w.dev/hook/n8n-lead", {
    method: "POST",
    headers: { "X-Signature": hmacHex("wrong-secret", "{}") },
    body: "{}",
  });
  const resp = await routeRequest(req, baseEnv, fetch);
  assert.equal(resp.status, 401);
  assert.equal((await resp.json()).error.code, "VALIDATION_ERROR");
  assert.equal(fetch.calls.length, 0);
});

test("POST /hook/* with missing signature header → 401", async () => {
  const resp = await routeRequest(
    new Request("https://w.dev/hook/x", { method: "POST", body: "{}" }),
    baseEnv, mockFetch());
  assert.equal(resp.status, 401);
});

test("GET /hook/* is not a route (404)", async () => {
  const resp = await routeRequest(new Request("https://w.dev/hook/x"), baseEnv, mockFetch());
  assert.equal(resp.status, 404);
});

test("/api/* proxies allowlisted path with method, body, and query", async () => {
  const fetch = mockFetch(() =>
    new Response('{"job":{}}', { status: 201, headers: { "Content-Type": "application/json" } }));
  const req = new Request("https://w.dev/api/v1/jobs?limit=5", {
    method: "POST",
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    body: '{"type":"noop","payload":{}}',
  });
  const resp = await routeRequest(req, baseEnv, fetch);
  assert.equal(resp.status, 201);
  assert.equal(fetch.calls[0].url, `${ORIGIN}/v1/jobs?limit=5`);
  assert.equal(fetch.calls[0].init.method, "POST");
  assert.equal(fetch.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(new TextDecoder().decode(fetch.calls[0].init.body), '{"type":"noop","payload":{}}');
});

test("/api/* without bearer → 401, origin never called", async () => {
  const fetch = mockFetch();
  const resp = await routeRequest(new Request("https://w.dev/api/v1/jobs"), baseEnv, fetch);
  assert.equal(resp.status, 401);
  assert.equal((await resp.json()).error.code, "VALIDATION_ERROR");
  assert.equal(fetch.calls.length, 0);
});

test("/api/* with wrong bearer → 401", async () => {
  const resp = await routeRequest(
    new Request("https://w.dev/api/v1/jobs", { headers: { Authorization: "Bearer nope" } }),
    baseEnv, mockFetch());
  assert.equal(resp.status, 401);
});

test("/api/* to non-allowlisted origin path → 404, origin never called", async () => {
  const fetch = mockFetch();
  for (const path of ["/api/v1/webhooks/n8n", "/api/healthz", "/api/v1/jobsx", "/api"]) {
    const resp = await routeRequest(
      new Request(`https://w.dev${path}`, { headers: { Authorization: `Bearer ${TOKEN}` } }),
      baseEnv, fetch);
    assert.equal(resp.status, 404, path);
    assert.equal((await resp.json()).error.code, "VALIDATION_ERROR", path);
  }
  assert.equal(fetch.calls.length, 0);
});

test("/api/* origin fetch failure → 502 DEPENDENCY_UNAVAILABLE", async () => {
  const fetch = async () => { throw new TypeError("fetch failed"); };
  const resp = await routeRequest(
    new Request("https://w.dev/api/v1/quota", { headers: { Authorization: `Bearer ${TOKEN}` } }),
    baseEnv, fetch);
  assert.equal(resp.status, 502);
  assert.equal((await resp.json()).error.code, "DEPENDENCY_UNAVAILABLE");
});

test("default export fetch handler answers /ping (mocked env, no network)", async () => {
  const resp = await workerDefault.fetch(new Request("https://w.dev/ping"), baseEnv, {});
  assert.equal(resp.status, 200);
});

test("POST /heartbeat requires the agent bearer token", async () => {
  const fetch = mockFetch(() => new Response('{"id":"discord-manual-1"}', {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  const unauthorized = await routeRequest(
    new Request("https://w.dev/heartbeat", { method: "POST" }),
    baseEnv,
    fetch,
  );
  assert.equal(unauthorized.status, 401);
  assert.equal(fetch.calls.length, 0);

  const authorized = await routeRequest(
    new Request("https://w.dev/heartbeat", {
      method: "POST",
      headers: { Authorization: `Bearer ${TOKEN}` },
    }),
    baseEnv,
    fetch,
  );
  assert.equal(authorized.status, 200);
  assert.equal((await authorized.json()).sent, true);
  assert.equal(fetch.calls.length, 1);
});

/* --------------------------------------------------------------- scheduled */

test("heartbeat formats America/Chicago time and sends Discord content", async () => {
  const fetch = mockFetch(() => new Response('{"id":"discord-message-1"}', {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  const instant = new Date("2026-08-31T07:30:00.000Z");
  assert.equal(formatChicagoTimestamp(instant), "2026-08-31 02:30 America/Chicago");

  const result = await runHeartbeat(baseEnv, fetch, () => instant);
  assert.deepEqual(result, {
    sent: true,
    status: 200,
    timestamp: "2026-08-31 02:30 America/Chicago",
    message: "Cloud heartbeat OK — 2026-08-31 02:30 America/Chicago",
    discord_message_id: "discord-message-1",
  });
  assert.equal(fetch.calls.length, 1);
  assert.equal(fetch.calls[0].url, `${baseEnv.DISCORD_WEBHOOK_URL}?wait=true`);
  assert.deepEqual(JSON.parse(fetch.calls[0].init.body), { content: result.message });
});

test("heartbeat fails closed when Discord is not configured", async () => {
  const fetch = mockFetch();
  const result = await runHeartbeat(
    { ...baseEnv, DISCORD_WEBHOOK_URL: "" },
    fetch,
    () => new Date("2026-08-31T07:30:00.000Z"),
  );
  assert.equal(result.sent, false);
  assert.equal(result.failure, "not_configured");
  assert.equal(fetch.calls.length, 0);
});

test("scheduled: healthy origin (200) → no notification", async () => {
  const fetch = mockFetch(() => new Response('{"status":"ok","db":true}', { status: 200 }));
  const result = await runScheduled(baseEnv, fetch);
  assert.deepEqual(result, { healthy: true, status: 200, notified: false });
  assert.equal(fetch.calls.length, 1);
  assert.equal(fetch.calls[0].url, `${ORIGIN}/healthz`);
});

test("scheduled: non-200 origin → signed critical notification to NOTIFY_WEBHOOK_URL", async () => {
  const fetch = mockFetch((url) =>
    url === `${ORIGIN}/healthz`
      ? new Response("dead", { status: 503 })
      : new Response("{}", { status: 200 }));
  const result = await runScheduled(baseEnv, fetch, () => new Date("2026-08-28T00:00:00Z"));
  assert.equal(result.healthy, false);
  assert.equal(result.notified, true);
  assert.equal(fetch.calls.length, 2);

  const notify = fetch.calls[1];
  assert.equal(notify.url, baseEnv.NOTIFY_WEBHOOK_URL);
  assert.equal(notify.init.method, "POST");
  const body = JSON.parse(new TextDecoder().decode(notify.init.body));
  assert.equal(body.severity, "critical");
  assert.equal(body.code, "DEPENDENCY_UNAVAILABLE");
  assert.equal(body.message, "origin health check failed");
  assert.equal(body.meta.status, 503);
  assert.equal(body.meta.failure, "non_200");
  // Notification is HMAC-signed so it can pass through this worker's own /hook route.
  assert.equal(
    await verifySignature(SECRET, notify.init.headers["X-Signature"], notify.init.body),
    true);
});

test("scheduled: origin fetch throws → notification with classified failure, no raw error", async () => {
  const fetch = mockFetch((url) => {
    if (url === `${ORIGIN}/healthz`) throw new TypeError(`fetch failed: ${ORIGIN}/healthz`);
    return new Response("{}", { status: 200 });
  });
  const result = await runScheduled(baseEnv, fetch);
  assert.equal(result.notified, true);
  const body = JSON.parse(new TextDecoder().decode(fetch.calls[1].init.body));
  assert.equal(body.meta.failure, "fetch_failed");
  assert.equal(body.meta.status, null);
  // The origin URL (a secret) must never leak into the notification payload.
  assert.equal(JSON.stringify(body).includes(ORIGIN), false);
});

test("scheduled: timeout error is classified as timeout", async () => {
  const fetch = mockFetch((url) => {
    if (url === `${ORIGIN}/healthz`) {
      const e = new Error("The operation was aborted due to timeout");
      e.name = "TimeoutError";
      throw e;
    }
    return new Response("{}", { status: 200 });
  });
  await runScheduled(baseEnv, fetch);
  const body = JSON.parse(new TextDecoder().decode(fetch.calls[1].init.body));
  assert.equal(body.meta.failure, "timeout");
});

test("scheduled: empty NOTIFY_WEBHOOK_URL → fail silent, notified:false", async () => {
  const fetch = mockFetch(() => new Response("dead", { status: 500 }));
  const result = await runScheduled({ ...baseEnv, NOTIFY_WEBHOOK_URL: "" }, fetch);
  assert.equal(result.notified, false);
  assert.equal(fetch.calls.length, 1); // only the health check
});

test("scheduled: notification endpoint failure is swallowed (next cron retries)", async () => {
  const fetch = mockFetch((url) => {
    if (url === `${ORIGIN}/healthz`) return new Response("dead", { status: 500 });
    throw new TypeError("notify endpoint down");
  });
  const result = await runScheduled(baseEnv, fetch);
  assert.equal(result.healthy, false);
  assert.equal(result.notified, false);
});
