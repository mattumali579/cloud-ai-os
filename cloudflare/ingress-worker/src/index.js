/**
 * cloudos-ingress — Cloudflare Worker (free plan, plain JS, no build step, no deps).
 *
 * Contract: contracts/ARCHITECTURE.md §13 (routes), §6 (error shape), §0 (zero cost).
 *
 * Routes:
 *   GET  /ping     → 200 {"status":"ok"}                       (no auth)
 *   POST /heartbeat → Bearer AGENT_API_TOKEN; send a direct Discord heartbeat.
 *   POST /hook/*   → X-Signature (hex sha256 HMAC of raw body, key WEBHOOK_SECRET)
 *                    → forward raw body to ORIGIN_URL/v1/webhooks/n8n with bearer
 *                    AGENT_API_TOKEN → relay origin status.
 *   *    /api/*    → Authorization: Bearer AGENT_API_TOKEN required; origin path
 *                    (the part after /api) must start with an allowlisted prefix,
 *                    else 404; method + body + query proxied to ORIGIN_URL.
 *   anything else  → 404
 *
 * Scheduled (cron every 30 min): send a timestamped Discord heartbeat directly, then
 * GET ORIGIN_URL/healthz with a 10s timeout. On non-200 or failure, POST a critical
 * DEPENDENCY_UNAVAILABLE notification to NOTIFY_WEBHOOK_URL (signed with WEBHOOK_SECRET
 * so it may point at this worker's own /hook/* route).
 *
 * Deliberately NO Workers AI calls, NO queues, NO durable objects, NO KV: the Worker
 * is a stateless, trivial-CPU ingress. Workers AI is consumed from the VM by the
 * router (Agent 8) via REST — never from here.
 *
 * Secrets (wrangler secret put): WEBHOOK_SECRET, AGENT_API_TOKEN, ORIGIN_URL,
 * NOTIFY_WEBHOOK_URL, DISCORD_WEBHOOK_URL. Secrets never appear in error bodies,
 * logs, or notifications.
 */

/** Origin path prefixes reachable through /api/* (everything else → 404). */
export const API_ALLOWED_PREFIXES = [
  "/v1/jobs",
  "/v1/runs",
  "/v1/quota",
  "/v1/agent/invoke",
];

const HEALTH_TIMEOUT_MS = 10_000;
const CHICAGO_TIME_ZONE = "America/Chicago";

const encoder = new TextEncoder();

/* ------------------------------------------------------------------ helpers */

/** Contract §6 error shape: {"error":{"code","message","details"}}. */
export function errorResponse(code, message, details = {}, status = 400) {
  return jsonResponse({ error: { code, message, details } }, status);
}

export function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Decode a hex string to bytes; null if not valid even-length hex. */
export function hexToBytes(hex) {
  if (typeof hex !== "string" || hex.length === 0 || hex.length % 2 !== 0) return null;
  if (!/^[0-9a-fA-F]+$/.test(hex)) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

export function bytesToHex(bytes) {
  let out = "";
  for (const b of bytes) out += b.toString(16).padStart(2, "0");
  return out;
}

/** Constant-time string equality (length mismatch still scans the longer input). */
export function timingSafeEqualStr(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const ab = encoder.encode(a);
  const bb = encoder.encode(b);
  const len = Math.max(ab.length, bb.length);
  let diff = ab.length === bb.length ? 0 : 1;
  for (let i = 0; i < len; i++) {
    diff |= (ab[i % ab.length] ?? 0) ^ (bb[i % bb.length] ?? 0);
  }
  return diff === 0;
}

async function importHmacKey(secret, usage) {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    [usage],
  );
}

/**
 * Verify that signatureHex is the hex sha256 HMAC of bodyBytes keyed with secret.
 * Uses crypto.subtle.verify, which performs a constant-time comparison.
 * Fails closed: missing/blank secret or malformed signature → false.
 */
export async function verifySignature(secret, signatureHex, bodyBytes) {
  if (!secret || !signatureHex) return false;
  const sig = hexToBytes(signatureHex);
  if (sig === null || sig.length !== 32) return false;
  const key = await importHmacKey(secret, "verify");
  return crypto.subtle.verify("HMAC", key, sig, bodyBytes);
}

/** Hex sha256 HMAC of bodyBytes keyed with secret (used to sign outbound notifications). */
export async function signBody(secret, bodyBytes) {
  const key = await importHmacKey(secret, "sign");
  const mac = await crypto.subtle.sign("HMAC", key, bodyBytes);
  return bytesToHex(new Uint8Array(mac));
}

/** True iff originPath is exactly an allowlisted prefix or a subpath of one. */
export function isAllowedApiPath(originPath) {
  return API_ALLOWED_PREFIXES.some(
    (p) => originPath === p || originPath.startsWith(p + "/"),
  );
}

function originBase(env) {
  return String(env.ORIGIN_URL || "").replace(/\/+$/, "");
}

function configError(missing) {
  // Name of the missing variable only — never its value.
  return errorResponse(
    "INTERNAL_ERROR",
    "worker is not configured",
    { missing },
    500,
  );
}

/* ------------------------------------------------------------------ routing */

/**
 * Pure-ish router: request + env (+ injectable fetch for tests) → Response.
 */
export async function routeRequest(request, env, fetchImpl = globalThis.fetch) {
  const url = new URL(request.url);
  const path = url.pathname;

  if (path === "/ping" && request.method === "GET") {
    return jsonResponse({ status: "ok" });
  }

  if (path === "/heartbeat" && request.method === "POST") {
    return handleHeartbeat(request, env, fetchImpl);
  }

  if (path.startsWith("/hook/") && request.method === "POST") {
    return handleHook(request, env, fetchImpl);
  }

  if (path === "/notify" && request.method === "POST") {
    return handleNotify(request, env, fetchImpl);
  }

  if (path === "/api" || path.startsWith("/api/")) {
    return handleApi(request, url, env, fetchImpl);
  }

  return errorResponse("VALIDATION_ERROR", "route not found", { path }, 404);
}

/**
 * Relay an arbitrary Discord payload from an authenticated cloud caller.
 *
 * Exists so DISCORD_WEBHOOK_URL lives in exactly one place. The scheduled
 * email watcher (GitHub Actions) sends its alerts through here instead of
 * holding a second copy of the webhook: one secret, one place to rotate it,
 * and the Worker's own heartbeat already proves the path is alive.
 */
async function handleNotify(request, env, fetchImpl) {
  const auth = request.headers.get("Authorization") || "";
  const expected = `Bearer ${env.AGENT_API_TOKEN}`;
  if (!env.AGENT_API_TOKEN || !timingSafeEqualStr(auth, expected)) {
    return errorResponse("VALIDATION_ERROR", "invalid or missing bearer token", {}, 401);
  }
  if (!env.DISCORD_WEBHOOK_URL) return configError("DISCORD_WEBHOOK_URL");

  let payload;
  try {
    payload = await request.json();
  } catch {
    return errorResponse("VALIDATION_ERROR", "body must be JSON", {}, 400);
  }

  const hasContent = typeof payload?.content === "string" && payload.content.trim() !== "";
  const hasEmbeds = Array.isArray(payload?.embeds) && payload.embeds.length > 0;
  if (!hasContent && !hasEmbeds) {
    return errorResponse(
      "VALIDATION_ERROR",
      "payload needs a non-empty content string or embeds array",
      {},
      400,
    );
  }

  // Forward only the fields Discord needs. Anything else the caller sent
  // (including anything that could @-mention everyone) is dropped.
  const outbound = {};
  if (hasContent) outbound.content = String(payload.content).slice(0, 2000);
  if (hasEmbeds) outbound.embeds = payload.embeds.slice(0, 10);
  outbound.allowed_mentions = { parse: [] };

  const result = await sendToDiscord(env, outbound, fetchImpl);
  if (!result.sent) {
    return errorResponse(
      "DEPENDENCY_UNAVAILABLE",
      "discord delivery failed",
      { failure: result.failure, status: result.status },
      502,
    );
  }
  return jsonResponse(result);
}

async function handleHeartbeat(request, env, fetchImpl) {
  const auth = request.headers.get("Authorization") || "";
  const expected = `Bearer ${env.AGENT_API_TOKEN}`;
  if (!env.AGENT_API_TOKEN || !timingSafeEqualStr(auth, expected)) {
    return errorResponse(
      "VALIDATION_ERROR",
      "invalid or missing bearer token",
      {},
      401,
    );
  }

  const result = await runHeartbeat(env, fetchImpl);
  if (!result.sent) {
    const notConfigured = result.failure === "not_configured";
    return errorResponse(
      notConfigured ? "INTERNAL_ERROR" : "DEPENDENCY_UNAVAILABLE",
      "heartbeat delivery failed",
      { failure: result.failure, status: result.status },
      notConfigured ? 500 : 502,
    );
  }
  return jsonResponse(result);
}

async function handleHook(request, env, fetchImpl) {
  if (!env.ORIGIN_URL) return configError("ORIGIN_URL");

  const body = new Uint8Array(await request.arrayBuffer());
  const signature = request.headers.get("X-Signature") || "";
  const ok = await verifySignature(env.WEBHOOK_SECRET, signature, body);
  if (!ok) {
    return errorResponse(
      "VALIDATION_ERROR",
      "invalid or missing webhook signature",
      {},
      401,
    );
  }

  try {
    const resp = await fetchImpl(`${originBase(env)}/v1/webhooks/n8n`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.AGENT_API_TOKEN}`,
        "Content-Type": request.headers.get("Content-Type") || "application/json",
        "X-Hook-Path": new URL(request.url).pathname,
      },
      body,
    });
    return relay(resp);
  } catch {
    return errorResponse(
      "DEPENDENCY_UNAVAILABLE",
      "origin unreachable",
      {},
      502,
    );
  }
}

async function handleApi(request, url, env, fetchImpl) {
  if (!env.ORIGIN_URL) return configError("ORIGIN_URL");

  const auth = request.headers.get("Authorization") || "";
  const expected = `Bearer ${env.AGENT_API_TOKEN}`;
  if (!env.AGENT_API_TOKEN || !timingSafeEqualStr(auth, expected)) {
    return errorResponse(
      "VALIDATION_ERROR",
      "invalid or missing bearer token",
      {},
      401,
    );
  }

  const originPath = url.pathname.slice("/api".length) || "/";
  if (!isAllowedApiPath(originPath)) {
    return errorResponse(
      "VALIDATION_ERROR",
      "route not found",
      { path: originPath },
      404,
    );
  }

  const init = {
    method: request.method,
    headers: { Authorization: `Bearer ${env.AGENT_API_TOKEN}` },
  };
  const contentType = request.headers.get("Content-Type");
  if (contentType) init.headers["Content-Type"] = contentType;
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  try {
    const resp = await fetchImpl(originBase(env) + originPath + url.search, init);
    return relay(resp);
  } catch {
    return errorResponse(
      "DEPENDENCY_UNAVAILABLE",
      "origin unreachable",
      {},
      502,
    );
  }
}

/** Relay an origin response: status + body + content type (no other origin headers). */
function relay(resp) {
  return new Response(resp.body, {
    status: resp.status,
    headers: {
      "Content-Type": resp.headers.get("Content-Type") || "application/json",
    },
  });
}

/* ---------------------------------------------------------------- scheduled */

/** Format a stable, minute-precision timestamp in Matt's local time zone. */
export function formatChicagoTimestamp(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: CHICAGO_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute} ${CHICAGO_TIME_ZONE}`;
}

/**
 * POST an already-built payload to DISCORD_WEBHOOK_URL. Never throws.
 * Shared by the cron heartbeat and the authenticated /notify relay so there is
 * exactly one piece of code that knows the webhook URL.
 */
export async function sendToDiscord(env, payload, fetchImpl = globalThis.fetch) {
  if (!env.DISCORD_WEBHOOK_URL) {
    return { sent: false, failure: "not_configured", status: null };
  }
  try {
    const webhookUrl = new URL(env.DISCORD_WEBHOOK_URL);
    webhookUrl.searchParams.set("wait", "true");
    const response = await fetchImpl(webhookUrl.toString(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    });
    if (!response.ok) {
      return { sent: false, failure: "non_2xx", status: response.status };
    }
    let discordMessageId = null;
    try {
      const receipt = await response.json();
      discordMessageId = String(receipt.id || "") || null;
    } catch {
      // Discord returns JSON with wait=true; delivery is still accepted on 204.
    }
    return { sent: true, status: response.status, discord_message_id: discordMessageId };
  } catch (error) {
    const failure = error && error.name === "TimeoutError" ? "timeout" : "fetch_failed";
    return { sent: false, failure, status: null };
  }
}

/** Send the laptop-independent heartbeat straight to Discord. Never throws. */
export async function runHeartbeat(
  env,
  fetchImpl = globalThis.fetch,
  now = () => new Date(),
) {
  const timestamp = formatChicagoTimestamp(now());
  const message = `Cloud heartbeat OK — ${timestamp}`;

  const result = await sendToDiscord(env, { content: message }, fetchImpl);
  if (!result.sent) {
    console.error(
      "cloud heartbeat failed",
      JSON.stringify({ failure: result.failure, status: result.status, timestamp }),
    );
    return { sent: false, failure: result.failure, status: result.status, timestamp };
  }
  console.log(
    "cloud heartbeat sent",
    JSON.stringify({
      status: result.status,
      timestamp,
      discord_message_id: result.discord_message_id,
    }),
  );
  return {
    sent: true,
    status: result.status,
    timestamp,
    message,
    discord_message_id: result.discord_message_id,
  };
}

/**
 * Health cron: GET ORIGIN_URL/healthz (10s timeout). On non-200 or failure, POST
 * a critical notification to NOTIFY_WEBHOOK_URL. The body is signed with
 * WEBHOOK_SECRET (X-Signature) so NOTIFY_WEBHOOK_URL may point either directly at
 * the n8n health-alert webhook or at this worker's own /hook/* front for it.
 * Returns a small result object (used by tests; ignored by the runtime).
 */
export async function runScheduled(env, fetchImpl = globalThis.fetch, now = () => new Date()) {
  let status = null;
  let failure = null;

  if (!env.ORIGIN_URL) {
    failure = "not_configured";
  } else {
    try {
      const resp = await fetchImpl(`${originBase(env)}/healthz`, {
        method: "GET",
        signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
      });
      status = resp.status;
      if (resp.status === 200) return { healthy: true, status, notified: false };
      failure = "non_200";
    } catch (err) {
      // Classify only — raw error text can embed the origin URL (a secret).
      failure = err && err.name === "TimeoutError" ? "timeout" : "fetch_failed";
    }
  }

  const payload = {
    severity: "critical",
    code: "DEPENDENCY_UNAVAILABLE",
    message: "origin health check failed",
    meta: {
      source: "cloudos-ingress",
      check: "healthz",
      status,
      failure,
      timeout_ms: HEALTH_TIMEOUT_MS,
      ts: now().toISOString(),
    },
  };

  if (!env.NOTIFY_WEBHOOK_URL) {
    return { healthy: false, status, notified: false, failure };
  }

  const body = encoder.encode(JSON.stringify(payload));
  const headers = { "Content-Type": "application/json" };
  if (env.WEBHOOK_SECRET) {
    headers["X-Signature"] = await signBody(env.WEBHOOK_SECRET, body);
  }

  try {
    await fetchImpl(env.NOTIFY_WEBHOOK_URL, {
      method: "POST",
      headers,
      body,
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    });
    return { healthy: false, status, notified: true, failure };
  } catch {
    // Nothing else to do on the free tier — next cron tick retries in 15 min.
    return { healthy: false, status, notified: false, failure };
  }
}

/* ----------------------------------------------------------- worker exports */

export default {
  async fetch(request, env, _ctx) {
    try {
      return await routeRequest(request, env);
    } catch {
      return errorResponse("INTERNAL_ERROR", "unhandled error", {}, 500);
    }
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(Promise.all([runHeartbeat(env), runScheduled(env)]));
  },
};
