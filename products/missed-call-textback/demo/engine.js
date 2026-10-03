'use strict';

// Deterministic Missed-Call Text-Back engine.
// Embedded into n8n Code nodes and used by the static demo.
// No network, no paid APIs, no clock reads except the `now` the caller passes
// (workflow code passes the current time; tests and the demo pass a fixed time).

const ENGINE_VERSION = 'mctb-engine-1';

const DEFAULTS = {
  missed_call_template:
    'Hi, this is {business_name}. Sorry we missed your call. Reply with what you need and we will get you scheduled. Hours: {hours}. Book: {booking_link}. Reply STOP to opt out.',
  qualify_location_prompt: 'Thanks. What is the service address or ZIP code?',
  qualify_urgency_prompt: 'How urgent is this? Reply TODAY, THIS WEEK, or FLEXIBLE.',
  qualify_done_template:
    'Got it. {owner_name} at {business_name} has your request and will follow up shortly. Book now: {booking_link}',
  followup_templates: [
    'Hi {customer_name}, this is {business_name}. Following up on your {job} estimate for {amount}. Reply with any questions or book here: {booking_link}. Reply STOP to opt out.',
    'Hi {customer_name}, {business_name} can still hold the {amount} price for {job}. Want us to get you on the schedule this week? Reply STOP to opt out.',
    'Last note from {business_name} about the {job} estimate ({amount}). Reply YES to lock it in, or STOP to opt out.',
  ],
};

const STOP_WORDS = new Set(['STOP', 'STOPALL', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT']);
const HELP_WORDS = new Set(['HELP', 'INFO']);
const START_WORDS = new Set(['START', 'UNSTOP']);

function createEngine() {
  function normalizePhone(input) {
    if (input == null) return null;
    const raw = String(input).trim();
    if (!raw) return null;
    const hasPlus = raw.startsWith('+');
    const digits = raw.replace(/\D/g, '');
    if (!digits) return null;
    if (hasPlus) {
      if (digits.length >= 10 && digits.length <= 15) return '+' + digits;
      return null;
    }
    if (digits.length === 10) return '+1' + digits;
    if (digits.length === 11 && digits.startsWith('1')) return '+' + digits;
    return null;
  }

  function keyword(body) {
    const cleaned = String(body || '')
      .trim()
      .toUpperCase()
      .replace(/[.!,]+$/g, '')
      .trim();
    if (!cleaned || /\s/.test(cleaned)) return null;
    if (STOP_WORDS.has(cleaned)) return 'stop';
    if (HELP_WORDS.has(cleaned)) return 'help';
    if (START_WORDS.has(cleaned)) return 'start';
    return null;
  }

  function clip(value, max) {
    const text = String(value == null ? '' : value).trim();
    if (text.length <= max) return text;
    return text.slice(0, max);
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function xmlEscape(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&apos;',
    }[ch]));
  }

  function renderTemplate(template, vars) {
    return String(template || '').replace(/\{([a-z0-9_]+)\}/gi, (_, key) => {
      const value = vars[String(key).toLowerCase()];
      return value == null ? '' : String(value);
    });
  }

  function formatMoney(cents) {
    const amount = Math.round(Number(cents) || 0);
    const sign = amount < 0 ? '-' : '';
    const abs = Math.abs(amount);
    const dollars = Math.floor(abs / 100);
    const rem = abs % 100;
    const withCommas = String(dollars).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return sign + '$' + withCommas + '.' + String(rem).padStart(2, '0');
  }

  function dollarsToCents(raw) {
    const cleaned = String(raw == null ? '' : raw).replace(/[$,\s]/g, '');
    if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
    const parts = cleaned.split('.');
    const dollars = parts[0];
    const cents = (parts[1] || '').padEnd(2, '0').slice(0, 2);
    return Number(dollars) * 100 + Number(cents || '0');
  }

  function parseClock(value) {
    const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim());
    if (!match) return null;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (hour > 23 || minute > 59) return null;
    return hour * 60 + minute;
  }

  function pad(number) {
    return String(number).padStart(2, '0');
  }

  function zonedParts(date, timeZone) {
    const fmt = new Intl.DateTimeFormat('en-US', {
      timeZone: timeZone || 'UTC',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    });
    const parts = {};
    fmt.formatToParts(date).forEach((part) => {
      parts[part.type] = part.value;
    });
    return {
      year: Number(parts.year),
      month: Number(parts.month),
      day: Number(parts.day),
      hour: Number(parts.hour),
      minute: Number(parts.minute),
      second: Number(parts.second),
    };
  }

  function zonedTimeToUtc(year, month, day, hour, minute, timeZone) {
    let utc = Date.UTC(year, month - 1, day, hour, minute, 0);
    for (let i = 0; i < 4; i += 1) {
      const parts = zonedParts(new Date(utc), timeZone);
      const asUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
      const target = Date.UTC(year, month - 1, day, hour, minute, 0);
      const delta = target - asUtc;
      if (delta === 0) break;
      utc += delta;
    }
    return new Date(utc);
  }

  function addLocalDays(date, timeZone, days, hour, minute) {
    const parts = zonedParts(date, timeZone);
    const shifted = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + days));
    return zonedTimeToUtc(
      shifted.getUTCFullYear(),
      shifted.getUTCMonth() + 1,
      shifted.getUTCDate(),
      hour,
      minute,
      timeZone
    );
  }

  function inQuietHours(date, timeZone, quietStart, quietEnd) {
    const start = parseClock(quietStart);
    const end = parseClock(quietEnd);
    if (start == null || end == null || start === end) return false;
    const parts = zonedParts(date, timeZone);
    const nowMinutes = parts.hour * 60 + parts.minute;
    if (start < end) return nowMinutes >= start && nowMinutes < end;
    return nowMinutes >= start || nowMinutes < end;
  }

  function nextWindowOpen(date, timeZone, quietStart, quietEnd) {
    const current = new Date(date.getTime());
    if (!inQuietHours(current, timeZone, quietStart, quietEnd)) return current;
    const end = parseClock(quietEnd);
    const hour = Math.floor(end / 60);
    const minute = end % 60;
    const parts = zonedParts(current, timeZone);
    let target = zonedTimeToUtc(parts.year, parts.month, parts.day, hour, minute, timeZone);
    if (target.getTime() <= current.getTime()) {
      target = addLocalDays(current, timeZone, 1, hour, minute);
    }
    return target;
  }

  function formatWhen(iso, timeZone) {
    if (!iso) return '';
    const parts = zonedParts(new Date(iso), timeZone || 'UTC');
    return parts.year + '-' + pad(parts.month) + '-' + pad(parts.day) + ' ' + pad(parts.hour) + ':' + pad(parts.minute);
  }

  function templateVars(tenant, extra) {
    const data = extra || {};
    return {
      business_name: tenant.business_name || '',
      hours: tenant.hours_text || '',
      booking_link: tenant.booking_link || '',
      owner_name: tenant.owner_name || '',
      owner_phone: tenant.owner_phone || '',
      customer_name: data.customer_name || data.name || '',
      job: data.job || '',
      amount: data.amount || '',
      phone: data.phone || '',
      need: data.need || '',
      location: data.location || '',
      urgency: data.urgency || '',
    };
  }

  function canText(tenant, sentToday, sentToRecipient) {
    return Number(sentToday) < Number(tenant.daily_sms_limit) &&
      Number(sentToRecipient) < Number(tenant.per_number_daily_limit);
  }

  function ownerDailyLimit(tenant) {
    const value = Number(tenant && tenant.owner_daily_sms_limit);
    return value > 0 ? value : 60;
  }

  function canTextOwner(tenant, sentToday, sentToOwner) {
    return Number(sentToday) < Number(tenant.daily_sms_limit) &&
      Number(sentToOwner) < ownerDailyLimit(tenant);
  }

  function dashboardLink(ctx) {
    const tenant = (ctx && ctx.tenant) || {};
    const path = (ctx && ctx.dashboardPath) || tenant.dashboard_path || '';
    const base = String((ctx && ctx.publicBaseUrl) || '').replace(/\/$/, '');
    if (base && path.charAt(0) === '/') return base + path;
    if (path) return path;
    if (base) return base + '/webhook/mctb-dashboard';
    return '';
  }

  function ownerPauseBody(ctx) {
    const link = dashboardLink(ctx);
    return link
      ? 'Alerts paused for today, see your dashboard: ' + link
      : 'Alerts paused for today, see your dashboard.';
  }

  function pushOwnerAlert(outbound, counters, ctx, body) {
    const tenant = ctx.tenant;
    const ownerPhone = normalizePhone(tenant.owner_phone);
    if (!ownerPhone || !body) return false;
    if (canTextOwner(tenant, counters.sentToday, counters.sentToOwner)) {
      outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, body, 'owner_notify', 'twiml', null));
      counters.sentToday += 1;
      counters.sentToOwner += 1;
      return true;
    }
    const shopHasRoom = Number(counters.sentToday) < Number(tenant.daily_sms_limit);
    const ownerCapped = Number(counters.sentToOwner) >= ownerDailyLimit(tenant);
    if (shopHasRoom && ownerCapped && !ctx.ownerCapNoticeSent) {
      outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, ownerPauseBody(ctx), 'owner_cap_notice', 'twiml', null));
      counters.sentToday += 1;
      counters.sentToOwner += 1;
      ctx.ownerCapNoticeSent = true;
    }
    return false;
  }

  function buildTwiml(options) {
    const opts = options || {};
    let inner = '';
    if (opts.dial) inner += opts.dial;
    if (opts.say) inner += '<Say>' + xmlEscape(opts.say) + '</Say>';
    (opts.messages || []).forEach((message) => {
      const toAttr = message.to ? ' to="' + xmlEscape(message.to) + '"' : '';
      inner += '<Message' + toAttr + '>' + xmlEscape(message.body) + '</Message>';
    });
    if (opts.hangup) inner += '<Hangup/>';
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + inner + '</Response>';
  }

  function emptyTwiml() {
    return buildTwiml({});
  }

  function helpBody(tenant) {
    return tenant.business_name + ' text line. For help call ' + tenant.owner_phone + '. Reply STOP to unsubscribe. Msg & data rates may apply.';
  }

  function stopBody(tenant) {
    return 'You are unsubscribed from ' + tenant.business_name + ' and will not receive more messages. Reply START to resubscribe.';
  }

  function startBody(tenant) {
    return 'You are resubscribed to ' + tenant.business_name + '. Reply with what you need done. Reply STOP to opt out.';
  }

  function outboundMessage(to, from, body, purpose, delivery, sendAt) {
    return {
      to: to,
      from: from,
      body: body,
      purpose: purpose,
      delivery: delivery,
      send_at: sendAt || null,
    };
  }

  function ownerWebhook(tenant, body, code) {
    return {
      severity: 'info',
      code: code || 'MCTB_LEAD',
      message: body,
      meta: {
        tenant_id: tenant.id || null,
        business_name: tenant.business_name,
      },
    };
  }

  function pack(save, twiml, record, webhook) {
    return {
      save: save ? 'yes' : 'no',
      twiml: twiml,
      record: record,
      ownerWebhook: webhook || null,
    };
  }

  function noTenant() {
    return pack(false, emptyTwiml(), null, null);
  }

  function missedCallRecord(ctx, extra) {
    const tenant = ctx.tenant;
    const kind = extra.kind;
    const from = ctx.from;
    const ownerPhone = normalizePhone(tenant.owner_phone);
    const counters = {
      sentToday: Number(ctx.sentToday || 0),
      sentToCaller: Number(ctx.sentToToday || 0),
      sentToOwner: Number(ctx.sentToOwnerToday || 0),
    };
    const outbound = [];
    let textSent = false;
    let suppressedReason = null;
    let conversation = null;
    const customerBody = renderTemplate(tenant.missed_call_template || DEFAULTS.missed_call_template, templateVars(tenant));

    if (!from) {
      suppressedReason = 'invalid_caller_id';
    } else if (ownerPhone && from === ownerPhone) {
      suppressedReason = 'owner_called';
    } else if (ctx.suppressed) {
      suppressedReason = 'opted_out';
    } else if (ctx.duplicate) {
      suppressedReason = 'duplicate_call';
    } else if (
      tenant.missed_call_respects_quiet_hours &&
      inQuietHours(new Date(ctx.now), tenant.timezone, tenant.quiet_start, tenant.quiet_end)
    ) {
      if (canText(tenant, counters.sentToday, counters.sentToCaller)) {
        const sendAt = nextWindowOpen(new Date(ctx.now), tenant.timezone, tenant.quiet_start, tenant.quiet_end);
        outbound.push(outboundMessage(from, tenant.twilio_number, customerBody, 'missed_call', 'queue', sendAt.toISOString()));
        suppressedReason = 'quiet_hours_deferred';
        if (!ctx.conversation) {
          conversation = { state: 'awaiting_need', need: null, location: null, urgency: null };
        }
      } else {
        suppressedReason = 'rate_limited';
      }
    } else if (!canText(tenant, counters.sentToday, counters.sentToCaller)) {
      suppressedReason = 'rate_limited';
    } else {
      outbound.push(outboundMessage(from, tenant.twilio_number, customerBody, 'missed_call', 'twiml', null));
      counters.sentToday += 1;
      counters.sentToCaller += 1;
      textSent = true;
      if (!ctx.conversation) {
        conversation = { state: 'awaiting_need', need: null, location: null, urgency: null };
      }
    }

    let ownerBody;
    if (!from) {
      ownerBody = 'A call hit ' + tenant.business_name + ' without caller ID. Nothing was texted.';
    } else if (suppressedReason === 'owner_called') {
      ownerBody = 'Your phone called ' + tenant.business_name + '. No customer text was sent.';
    } else if (textSent) {
      ownerBody = 'Missed call at ' + tenant.business_name + ' from ' + from + '. We texted them back.';
    } else if (suppressedReason === 'quiet_hours_deferred') {
      ownerBody = 'Missed call at ' + tenant.business_name + ' from ' + from + '. Text is waiting for quiet hours to end.';
    } else if (suppressedReason === 'opted_out') {
      ownerBody = 'Missed call from ' + from + ', who opted out of texts. Call them by phone. Do not text them.';
    } else if (suppressedReason === 'duplicate_call') {
      ownerBody = null;
    } else {
      ownerBody = 'Missed call at ' + tenant.business_name + ' from ' + from + '. No text sent (' + (suppressedReason || 'unknown') + ').';
    }

    const ownerTexted = ownerBody ? pushOwnerAlert(outbound, counters, ctx, ownerBody) : false;

    const say = kind === 'dial_status'
      ? null
      : 'Thanks for calling ' + tenant.business_name + '. Sorry we missed you. We are sending a text now.';
    const twiml = buildTwiml({
      say: textSent || suppressedReason === 'quiet_hours_deferred' ? say : (kind === 'dial_status' ? null : 'Thanks for calling ' + tenant.business_name + '.'),
      messages: outbound.filter((item) => item.delivery === 'twiml'),
      hangup: true,
    });

    if (ctx.duplicate) {
      return pack(false, buildTwiml({ say: 'Thanks for calling ' + tenant.business_name + '.', hangup: true }), null, null);
    }

    const notification = ownerBody ? { channel: ownerTexted ? 'sms' : 'dashboard', body: ownerBody } : null;
    return pack(true, twiml, {
      kind: kind,
      tenant_id: tenant.id,
      call_sid: ctx.callSid || '',
      from: from || '',
      to: ctx.to || tenant.twilio_number,
      dial_status: extra.dial_status || '',
      missed: extra.missed !== false,
      text_sent: textSent,
      suppressed_reason: suppressedReason,
      conversation: conversation,
      outbound: outbound,
      owner_notification: notification,
    }, notification ? ownerWebhook(tenant, ownerBody, 'MCTB_MISSED_CALL') : null);
  }

  function handleVoice(ctx) {
    if (!ctx || !ctx.tenant) return noTenant();
    const tenant = ctx.tenant;
    if (ctx.duplicate) {
      return pack(false, buildTwiml({ say: 'Thanks for calling ' + tenant.business_name + '.', hangup: true }), null, null);
    }
    if (tenant.call_mode === 'dial') {
      const base = String(ctx.publicBaseUrl || '').replace(/\/$/, '');
      const timeout = Math.min(40, Math.max(5, Number(tenant.ring_timeout_seconds) || 20));
      if (!base) {
        return pack(true, buildTwiml({
          say: 'Thanks for calling ' + tenant.business_name + '. Call routing is not finished yet. Please call back during business hours.',
          hangup: true,
        }), {
          kind: 'incoming',
          tenant_id: tenant.id,
          call_sid: ctx.callSid || '',
          from: ctx.from || '',
          to: ctx.to || tenant.twilio_number,
          dial_status: '',
          missed: false,
          text_sent: false,
          suppressed_reason: 'missing_public_base_url',
          conversation: null,
          outbound: [],
          owner_notification: {
            channel: 'dashboard',
            body: 'A call arrived but MCTB_PUBLIC_BASE_URL is empty, so the owner phone was not rung.',
          },
        }, null);
      }
      const action = base + '/webhook/mctb-dial-status';
      const dial = '<Dial timeout="' + timeout + '" action="' + xmlEscape(action) + '" method="POST"><Number>' +
        xmlEscape(normalizePhone(tenant.owner_phone) || tenant.owner_phone) + '</Number></Dial>';
      return pack(true, buildTwiml({ dial: dial }), {
        kind: 'incoming',
        tenant_id: tenant.id,
        call_sid: ctx.callSid || '',
        from: ctx.from || '',
        to: ctx.to || tenant.twilio_number,
        dial_status: 'ringing',
        missed: false,
        text_sent: false,
        suppressed_reason: null,
        conversation: null,
        outbound: [],
        owner_notification: null,
      }, null);
    }
    return missedCallRecord(ctx, { kind: 'incoming', dial_status: ctx.callStatus || 'forwarded', missed: true });
  }

  function handleDialStatus(ctx) {
    if (!ctx || !ctx.tenant) return noTenant();
    const status = String(ctx.dialStatus || '').toLowerCase();
    const answered = status === 'completed' || status === 'answered';
    if (answered) {
      return pack(true, emptyTwiml(), {
        kind: 'dial_status',
        tenant_id: ctx.tenant.id,
        call_sid: ctx.callSid || '',
        from: ctx.from || '',
        to: ctx.to || ctx.tenant.twilio_number,
        dial_status: status,
        missed: false,
        text_sent: false,
        suppressed_reason: null,
        conversation: null,
        outbound: [],
        owner_notification: null,
      }, null);
    }
    return missedCallRecord(ctx, { kind: 'dial_status', dial_status: status || 'no-answer', missed: true });
  }

  function normalizeUrgency(text) {
    const value = String(text || '').trim().toLowerCase();
    if (/today|asap|emergency|now|urgent/.test(value)) return 'today';
    if (/week/.test(value)) return 'this_week';
    if (/flex|whenever|no rush|not urgent/.test(value)) return 'flexible';
    return clip(text, 80);
  }

  function blankConversation() {
    return { state: 'awaiting_need', need: null, location: null, urgency: null };
  }

  function copyConversation(conversation) {
    const source = conversation || blankConversation();
    return {
      state: source.state || 'awaiting_need',
      need: source.need || null,
      location: source.location || null,
      urgency: source.urgency || null,
    };
  }

  function parseOwnerCommand(text) {
    const trimmed = String(text || '').trim();
    if (!trimmed) return null;
    if (/^(HELP|COMMANDS)$/i.test(trimmed)) return { type: 'help' };
    const won = /^(WON|LOST)\s+(\d+)\s*$/i.exec(trimmed);
    if (won) return { type: won[1].toLowerCase(), id: Number(won[2]) };
    const review = /^(DONE|REVIEW)\s+(\S+)\s*$/i.exec(trimmed);
    if (review) return { type: review[1].toLowerCase(), phone: review[2] };
    const reply = /^REPLY\s+(\S+)\s+([\s\S]+)$/i.exec(trimmed);
    if (reply) return { type: 'reply', phone: reply[1], message: reply[2].trim() };
    if (/^ESTIMATE\b/i.test(trimmed)) {
      const parts = trimmed.replace(/^ESTIMATE\s*/i, '').split('|').map((part) => part.trim());
      if (parts.length !== 4) return { type: 'error', error: 'estimate_format' };
      const phone = normalizePhone(parts[1]);
      const cents = dollarsToCents(parts[3]);
      if (!parts[0] || !phone || !parts[2] || cents == null) return { type: 'error', error: 'estimate_format' };
      return { type: 'estimate', name: clip(parts[0], 80), phone: phone, job: clip(parts[2], 160), amount_cents: cents };
    }
    return null;
  }

  function commandHelp() {
    return 'Commands: ESTIMATE Name | phone | job | amount · WON 12 · LOST 12 · DONE +15551212 · REVIEW +15551212 · REPLY +15551212 your message · HELP';
  }

  function findEstimate(ctx, id) {
    const estimates = ctx.estimates || [];
    for (let i = 0; i < estimates.length; i += 1) {
      if (Number(estimates[i].id) === Number(id)) return estimates[i];
    }
    return null;
  }

  function reviewUrlOk(tenant) {
    return typeof (tenant && tenant.google_review_url) === 'string' && /^https:\/\//i.test(String(tenant.google_review_url).trim());
  }

  function reviewedRecently(ctx, phone) {
    const normalized = normalizePhone(phone);
    if (!normalized) return false;
    const listed = (ctx && ctx.recentReviewPhones) || [];
    for (let i = 0; i < listed.length; i += 1) {
      if (normalizePhone(listed[i]) === normalized) return true;
    }
    return false;
  }

  function reviewSendAt(nowIso, tenant) {
    const target = new Date(new Date(nowIso).getTime() + 2 * 60 * 60 * 1000);
    if (inQuietHours(target, tenant.timezone, tenant.quiet_start, tenant.quiet_end)) {
      return nextWindowOpen(target, tenant.timezone, tenant.quiet_start, tenant.quiet_end).toISOString();
    }
    return target.toISOString();
  }

  function maybeScheduleReview(ctx, phone, estimateId) {
    const normalized = normalizePhone(phone);
    if (!normalized) return { request: null, reason: 'phone' };
    if (!reviewUrlOk(ctx.tenant)) return { request: null, reason: 'no_url' };
    if (isOptedOut(ctx, normalized)) return { request: null, reason: 'opted_out' };
    if (reviewedRecently(ctx, normalized)) return { request: null, reason: 'recent' };
    const id = estimateId == null || estimateId === '' ? null : Number(estimateId);
    return {
      request: {
        phone: normalized,
        estimate_id: id,
        send_at: reviewSendAt(ctx.now, ctx.tenant),
      },
      reason: 'scheduled',
    };
  }

  function initialNextSendAt(nowIso, tenant) {
    const now = new Date(nowIso);
    if (inQuietHours(now, tenant.timezone, tenant.quiet_start, tenant.quiet_end)) {
      return nextWindowOpen(now, tenant.timezone, tenant.quiet_start, tenant.quiet_end).toISOString();
    }
    return now.toISOString();
  }

  function suppressedSet(ctx) {
    const set = {};
    const listed = ctx.suppressedPhones || [];
    listed.forEach((phone) => {
      const normalized = normalizePhone(phone);
      if (normalized) set[normalized] = true;
    });
    if (ctx.suppressed) {
      const current = normalizePhone(ctx.from);
      if (current) set[current] = true;
    }
    return set;
  }

  function isOptedOut(ctx, phone) {
    const normalized = normalizePhone(phone);
    return Boolean(normalized && suppressedSet(ctx)[normalized]);
  }

  function optedOutNotice(phone) {
    return phone + ' opted out of texts. Not sent. Call them by phone. Do not text them.';
  }

  function pushIfAllowed(outbound, counters, tenant, to, body, purpose, delivery, recipientCountKey) {
    const sentTo = counters[recipientCountKey] || 0;
    if (!to || !canText(tenant, counters.sentToday, sentTo)) return false;
    outbound.push(outboundMessage(to, tenant.twilio_number, body, purpose, delivery, delivery === 'queue' ? new Date().toISOString() : null));
    counters.sentToday += 1;
    counters[recipientCountKey] = sentTo + 1;
    return true;
  }

  function finishSms(ctx, details) {
    const tenant = ctx.tenant;
    const messages = (details.outbound || []).filter((item) => item.delivery === 'twiml');
    const notification = details.owner_notification || null;
    return pack(true, buildTwiml({ messages: messages }), {
      tenant_id: tenant.id,
      from: ctx.from || '',
      to: ctx.to || tenant.twilio_number,
      message_sid: ctx.messageSid || '',
      inbound_body: details.inbound_body == null ? String(ctx.body || '') : details.inbound_body,
      inbound_purpose: details.inbound_purpose || 'sms',
      suppress: Boolean(details.suppress),
      clear_suppression: Boolean(details.clear_suppression),
      conversation: details.conversation || null,
      outbound: details.outbound || [],
      new_estimate: details.new_estimate || null,
      estimate_update: details.estimate_update || null,
      review_request: details.review_request || null,
      owner_notification: notification,
    }, notification ? ownerWebhook(tenant, notification.body, details.webhookCode || 'MCTB_SMS') : null);
  }

  function handleOwnerSms(ctx) {
    const tenant = ctx.tenant;
    const ownerPhone = normalizePhone(tenant.owner_phone);
    const parsed = parseOwnerCommand(ctx.body);
    const outbound = [];
    const delivery = 'twiml';
    if (!parsed || parsed.type === 'help') {
      outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, parsed ? commandHelp() : commandHelp(), 'owner_help', delivery, null));
      return finishSms(ctx, {
        inbound_purpose: 'owner_command',
        outbound: outbound,
        inbound_body: ctx.body || '',
        owner_notification: null,
      });
    }
    if (parsed.type === 'error') {
      outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, 'Could not read that. ' + commandHelp(), 'owner_help', delivery, null));
      return finishSms(ctx, { inbound_purpose: 'owner_command', outbound: outbound });
    }
    if (parsed.type === 'estimate') {
      const nextSend = initialNextSendAt(ctx.now, tenant);
      outbound.push(outboundMessage(
        ownerPhone,
        tenant.twilio_number,
        'Estimate saved for ' + parsed.name + ' (' + parsed.phone + '), ' + parsed.job + ' ' + formatMoney(parsed.amount_cents) + '. Follow-up starts ' + formatWhen(nextSend, tenant.timezone) + '.',
        'owner_confirm',
        delivery,
        null
      ));
      return finishSms(ctx, {
        inbound_purpose: 'owner_command',
        outbound: outbound,
        new_estimate: {
          customer_name: parsed.name,
          phone: parsed.phone,
          job: parsed.job,
          amount_cents: parsed.amount_cents,
          next_send_at: nextSend,
        },
        owner_notification: { channel: 'sms', body: 'Estimate logged for ' + parsed.name + '.' },
        webhookCode: 'MCTB_ESTIMATE',
      });
    }
    if (parsed.type === 'won' || parsed.type === 'lost') {
      const estimate = findEstimate(ctx, parsed.id);
      if (!estimate) {
        outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, 'No estimate ' + parsed.id + ' on this business.', 'owner_confirm', delivery, null));
        return finishSms(ctx, { inbound_purpose: 'owner_command', outbound: outbound });
      }
      let review = null;
      let extra = '';
      if (parsed.type === 'won') {
        const scheduled = maybeScheduleReview(ctx, estimate.phone, estimate.id);
        if (scheduled.reason === 'scheduled') {
          review = scheduled.request;
          extra = ' Review text scheduled.';
        }
      }
      const confirm = 'Estimate ' + parsed.id + ' marked ' + parsed.type + '.' + extra;
      outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, confirm, 'owner_confirm', delivery, null));
      return finishSms(ctx, {
        inbound_purpose: 'owner_command',
        outbound: outbound,
        estimate_update: { id: Number(parsed.id), status: parsed.type },
        review_request: review,
        owner_notification: { channel: 'sms', body: confirm },
      });
    }
    if (parsed.type === 'done' || parsed.type === 'review') {
      const phone = normalizePhone(parsed.phone);
      if (!phone) {
        const help = 'Need a mobile number. DONE +15551212 or REVIEW +15551212';
        outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, help, 'owner_help', delivery, null));
        return finishSms(ctx, { inbound_purpose: 'owner_command', outbound: outbound });
      }
      const match = (ctx.estimates || []).find((estimate) => normalizePhone(estimate.phone) === phone);
      const scheduled = maybeScheduleReview(ctx, phone, match ? match.id : null);
      let text;
      if (scheduled.reason === 'scheduled') text = 'Review request scheduled for ' + phone + '.';
      else if (scheduled.reason === 'no_url') text = 'No Google review link is set for ' + tenant.business_name + '. Add one, then send REVIEW ' + phone + '.';
      else if (scheduled.reason === 'opted_out') text = optedOutNotice(phone);
      else if (scheduled.reason === 'recent') text = 'A review request already went to ' + phone + ' in the last 90 days.';
      else text = 'Need a mobile number. DONE +15551212 or REVIEW +15551212';
      outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, text, 'owner_confirm', delivery, null));
      return finishSms(ctx, {
        inbound_purpose: 'owner_command',
        outbound: outbound,
        review_request: scheduled.request,
        owner_notification: { channel: 'sms', body: text },
      });
    }
    if (parsed.type === 'reply') {
      const phone = normalizePhone(parsed.phone);
      if (!phone || !parsed.message) {
        outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, 'Reply needs a phone and a message. ' + commandHelp(), 'owner_help', delivery, null));
        return finishSms(ctx, { inbound_purpose: 'owner_command', outbound: outbound });
      }
      if (isOptedOut(ctx, phone)) {
        outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, optedOutNotice(phone), 'owner_confirm', delivery, null));
        return finishSms(ctx, {
          inbound_purpose: 'owner_command',
          outbound: outbound,
          owner_notification: { channel: 'sms', body: optedOutNotice(phone) },
        });
      }
      const counters = { sentToday: Number(ctx.sentToday || 0), sentToCustomer: Number(ctx.sentToToday || 0) };
      const sent = pushIfAllowed(outbound, counters, tenant, phone, clip(parsed.message, 500), 'owner_reply', delivery, 'sentToCustomer');
      outbound.push(outboundMessage(
        ownerPhone,
        tenant.twilio_number,
        sent ? 'Sent to ' + phone + '.' : 'Not sent to ' + phone + '. Daily text limit reached. Call them instead.',
        'owner_confirm',
        delivery,
        null
      ));
      const open = (ctx.openEstimate && normalizePhone(ctx.openEstimate.phone) === phone) ? ctx.openEstimate : null;
      const match = open || (ctx.estimates || []).find((estimate) => estimate.status === 'open' && normalizePhone(estimate.phone) === phone);
      return finishSms(ctx, {
        inbound_purpose: 'owner_command',
        outbound: outbound,
        estimate_update: match ? { id: Number(match.id), status: 'replied' } : null,
        owner_notification: { channel: 'sms', body: 'Owner reply to ' + phone + ': ' + clip(parsed.message, 240) },
      });
    }
    return finishSms(ctx, { inbound_purpose: 'owner_command', outbound: outbound });
  }

  function handleInboundSms(ctx) {
    if (!ctx || !ctx.tenant) return noTenant();
    const tenant = ctx.tenant;
    const from = ctx.from;
    const ownerPhone = normalizePhone(tenant.owner_phone);
    if (!from) return noTenant();
    if (ownerPhone && from === ownerPhone) return handleOwnerSms(ctx);

    const kind = keyword(ctx.body);
    const outbound = [];
    const counters = {
      sentToday: Number(ctx.sentToday || 0),
      sentToCaller: Number(ctx.sentToToday || 0),
      sentToOwner: Number(ctx.sentToOwnerToday || 0),
    };

    if (kind === 'help') {
      outbound.push(outboundMessage(from, tenant.twilio_number, helpBody(tenant), 'help', 'twiml', null));
      return finishSms(ctx, { inbound_purpose: 'help', outbound: outbound, conversation: null });
    }

    if (kind === 'stop') {
      outbound.push(outboundMessage(from, tenant.twilio_number, stopBody(tenant), 'stop_confirm', 'twiml', null));
      const conversation = copyConversation(ctx.conversation);
      conversation.state = 'opted_out';
      const ownerBody = from + ' opted out of ' + tenant.business_name + ' texts.';
      const ownerTexted = pushOwnerAlert(outbound, counters, ctx, ownerBody);
      return finishSms(ctx, {
        inbound_purpose: 'stop',
        outbound: outbound,
        suppress: true,
        conversation: conversation,
        estimate_update: ctx.openEstimate ? { id: Number(ctx.openEstimate.id), status: 'opted_out' } : null,
        owner_notification: { channel: ownerTexted ? 'sms' : 'dashboard', body: ownerBody },
        webhookCode: 'MCTB_STOP',
      });
    }

    if (ctx.suppressed && kind !== 'start') {
      const ownerBody = 'Opted-out number ' + from + ' texted ' + tenant.business_name + ': "' + clip(ctx.body, 240) + '". Call them by phone. Do not text them.';
      const ownerTexted = pushOwnerAlert(outbound, counters, ctx, ownerBody);
      return finishSms(ctx, {
        inbound_purpose: 'suppressed',
        outbound: outbound,
        owner_notification: { channel: ownerTexted ? 'sms' : 'dashboard', body: ownerBody },
        webhookCode: 'MCTB_OPTED_OUT_REPLY',
      });
    }

    if (kind === 'start') {
      outbound.push(outboundMessage(from, tenant.twilio_number, startBody(tenant), 'start_confirm', 'twiml', null));
      return finishSms(ctx, {
        inbound_purpose: 'start',
        outbound: outbound,
        clear_suppression: true,
        conversation: blankConversation(),
        owner_notification: { channel: 'sms', body: from + ' resubscribed to ' + tenant.business_name + '.' },
      });
    }

    const text = String(ctx.body || '').trim();
    let conversation = copyConversation(ctx.conversation);
    let customerBody = null;
    let purpose = 'qualify';
    let justQualified = false;
    const estimateUpdate = ctx.openEstimate ? { id: Number(ctx.openEstimate.id), status: 'replied' } : null;

    if (!text) {
      customerBody = 'Sorry, we did not catch that. What do you need ' + tenant.business_name + ' to help with?';
      if (!ctx.conversation) conversation = blankConversation();
    } else if (!ctx.conversation || conversation.state === 'awaiting_need' || conversation.state === 'opted_out') {
      conversation.state = 'awaiting_location';
      conversation.need = clip(text, 500);
      customerBody = tenant.qualify_location_prompt || DEFAULTS.qualify_location_prompt;
    } else if (conversation.state === 'awaiting_location') {
      conversation.state = 'awaiting_urgency';
      conversation.location = clip(text, 160);
      customerBody = tenant.qualify_urgency_prompt || DEFAULTS.qualify_urgency_prompt;
    } else if (conversation.state === 'awaiting_urgency') {
      conversation.state = 'handed_off';
      conversation.urgency = normalizeUrgency(text);
      customerBody = renderTemplate(tenant.qualify_done_template || DEFAULTS.qualify_done_template, templateVars(tenant, conversation));
      purpose = 'qualify_done';
      justQualified = true;
    } else {
      customerBody = 'Thanks, we passed that to ' + tenant.owner_name + ' at ' + tenant.business_name + '.';
      purpose = 'relay_ack';
    }

    const allowed = canText(tenant, counters.sentToday, counters.sentToCaller);
    if (!allowed) {
      const ownerBody = 'Text from ' + from + ' was not answered automatically. Daily text limit reached. They said: "' + clip(text, 240) + '".';
      const ownerTexted = pushOwnerAlert(outbound, counters, ctx, ownerBody);
      return finishSms(ctx, {
        inbound_purpose: 'rate_limited',
        outbound: outbound,
        conversation: ctx.conversation ? copyConversation(ctx.conversation) : null,
        estimate_update: estimateUpdate,
        owner_notification: { channel: ownerTexted ? 'sms' : 'dashboard', body: ownerBody },
      });
    }

    outbound.push(outboundMessage(from, tenant.twilio_number, customerBody, purpose, 'twiml', null));
    counters.sentToday += 1;
    counters.sentToCaller += 1;

    const leadLine = justQualified
      ? 'Qualified lead for ' + tenant.business_name + '\nFrom: ' + from + '\nNeed: ' + (conversation.need || '') + '\nArea: ' + (conversation.location || '') + '\nUrgency: ' + (conversation.urgency || '') + '\nReply: REPLY ' + from + ' your message'
      : 'Reply from ' + from + ' to ' + tenant.business_name + ': "' + clip(text, 300) + '"';
    const ownerTexted = pushOwnerAlert(outbound, counters, ctx, leadLine);

    return finishSms(ctx, {
      inbound_purpose: purpose,
      outbound: outbound,
      conversation: conversation,
      estimate_update: estimateUpdate,
      owner_notification: { channel: ownerTexted ? 'sms' : 'dashboard', body: leadLine },
      webhookCode: conversation.state === 'handed_off' ? 'MCTB_QUALIFIED' : 'MCTB_REPLY',
    });
  }

  function followupBody(tenant, estimate, stepIndex) {
    const templates = (tenant.followup_templates && tenant.followup_templates.length)
      ? tenant.followup_templates
      : DEFAULTS.followup_templates;
    const template = templates[stepIndex] || templates[templates.length - 1];
    return renderTemplate(template, templateVars(tenant, {
      customer_name: estimate.customer_name,
      job: estimate.job,
      amount: formatMoney(estimate.amount_cents),
      phone: estimate.phone,
    }));
  }

  function nextFollowupAt(createdAtIso, stepIndexAfterSend, tenant) {
    if (stepIndexAfterSend >= 3) return null;
    const days = stepIndexAfterSend === 1 ? 2 : 5;
    const when = addLocalDays(new Date(createdAtIso), tenant.timezone, days, 10, 0);
    return when.toISOString();
  }

  function planOneFollowup(row, nowIso) {
    const tenant = {
      timezone: row.timezone,
      quiet_start: row.quiet_start,
      quiet_end: row.quiet_end,
      daily_sms_limit: row.daily_sms_limit,
      per_number_daily_limit: row.per_number_daily_limit,
      followup_templates: row.followup_templates,
      business_name: row.business_name,
      hours_text: row.hours_text,
      booking_link: row.booking_link,
      owner_name: row.owner_name,
      twilio_number: row.twilio_number,
    };
    const estimate = {
      id: row.estimate_id,
      customer_name: row.customer_name,
      phone: row.phone,
      job: row.job,
      amount_cents: row.amount_cents,
      created_at: row.created_at,
      step_index: Number(row.step_index || 0),
    };
    const baseMark = {
      kind: 'followup',
      tenant_id: row.tenant_id,
      estimate_id: estimate.id,
      to: estimate.phone,
      from: tenant.twilio_number,
      purpose: 'followup',
      step_index_before: estimate.step_index,
    };
    if (row.suppressed) {
      return {
        send: false,
        mark: Object.assign({}, baseMark, {
          status: 'suppressed',
          estimate_status: 'opted_out',
          step_index: estimate.step_index,
          next_send_at: null,
          body: '',
        }),
      };
    }
    const now = new Date(nowIso);
    if (inQuietHours(now, tenant.timezone, tenant.quiet_start, tenant.quiet_end)) {
      return {
        send: false,
        mark: Object.assign({}, baseMark, {
          status: 'deferred',
          estimate_status: 'open',
          step_index: estimate.step_index,
          next_send_at: nextWindowOpen(now, tenant.timezone, tenant.quiet_start, tenant.quiet_end).toISOString(),
          body: '',
        }),
      };
    }
    if (!canText(tenant, Number(row.sent_today || 0), Number(row.sent_to_today || 0))) {
      return {
        send: false,
        mark: Object.assign({}, baseMark, {
          status: 'rate_limited',
          estimate_status: 'open',
          step_index: estimate.step_index,
          next_send_at: new Date(now.getTime() + 60 * 60 * 1000).toISOString(),
          body: '',
        }),
      };
    }
    if (estimate.step_index >= 3) {
      return {
        send: false,
        mark: Object.assign({}, baseMark, {
          status: 'completed',
          estimate_status: 'completed',
          step_index: estimate.step_index,
          next_send_at: null,
          body: '',
        }),
      };
    }
    const body = followupBody(tenant, estimate, estimate.step_index);
    const stepAfter = estimate.step_index + 1;
    return {
      send: true,
      body: body,
      mark: Object.assign({}, baseMark, {
        status: 'mock_sent',
        estimate_status: stepAfter >= 3 ? 'completed' : 'open',
        step_index: stepAfter,
        next_send_at: nextFollowupAt(estimate.created_at, stepAfter, tenant),
        body: body,
      }),
    };
  }

  function planOneQueued(row, nowIso) {
    const tenant = {
      timezone: row.timezone,
      quiet_start: row.quiet_start,
      quiet_end: row.quiet_end,
      daily_sms_limit: row.daily_sms_limit,
      per_number_daily_limit: row.per_number_daily_limit,
      twilio_number: row.twilio_number || row.from_number,
    };
    const base = {
      kind: 'queue',
      queue_id: row.queue_id,
      tenant_id: row.tenant_id,
      to: row.to_number,
      from: row.from_number,
      body: row.body,
      purpose: row.purpose,
    };
    if (row.suppressed) {
      return { send: false, mark: Object.assign({}, base, { status: 'suppressed', next_send_at: null }) };
    }
    const now = new Date(nowIso);
    if (inQuietHours(now, tenant.timezone, tenant.quiet_start, tenant.quiet_end)) {
      return {
        send: false,
        mark: Object.assign({}, base, {
          status: 'deferred',
          next_send_at: nextWindowOpen(now, tenant.timezone, tenant.quiet_start, tenant.quiet_end).toISOString(),
        }),
      };
    }
    const purpose = String(row.purpose || '');
    const healthAlert = purpose.indexOf('health_') === 0;
    const revenueReport = purpose.indexOf('revenue_report') === 0;
    const allowed = healthAlert
      ? true
      : revenueReport
        ? canTextOwner(
          {
            daily_sms_limit: row.daily_sms_limit,
            owner_daily_sms_limit: row.owner_daily_sms_limit || 60,
          },
          Number(row.sent_today || 0),
          Number(row.sent_to_today || 0)
        )
        : canText(tenant, Number(row.sent_today || 0), Number(row.sent_to_today || 0));
    if (!allowed) {
      return {
        send: false,
        mark: Object.assign({}, base, {
          status: 'rate_limited',
          next_send_at: new Date(now.getTime() + 60 * 60 * 1000).toISOString(),
        }),
      };
    }
    return { send: true, body: row.body, mark: Object.assign({}, base, { status: 'mock_sent', next_send_at: null }) };
  }

  function reviewBody(businessName, link, reminder) {
    if (reminder) {
      return 'Reminder from ' + businessName + ': a short Google review still helps neighbors find us. ' + link + ' Reply STOP to opt out.';
    }
    return 'Thanks for choosing ' + businessName + '. If we earned it, a Google review helps neighbors find us: ' + link + ' Reply STOP to opt out.';
  }

  function planOneReview(row, nowIso, env) {
    const purpose = row.kind === 'reminder' ? 'review_reminder' : 'review_request';
    const base = {
      kind: 'review',
      review_id: row.review_id,
      tenant_id: row.tenant_id,
      to: row.phone,
      from: row.twilio_number,
      purpose: purpose,
    };
    if (row.suppressed) {
      return { send: false, mark: Object.assign({}, base, { status: 'suppressed', next_send_at: null, body: '' }) };
    }
    if (!row.google_review_url || !/^https:\/\//i.test(String(row.google_review_url))) {
      return { send: false, mark: Object.assign({}, base, { status: 'skipped', next_send_at: null, body: '' }) };
    }
    const now = new Date(nowIso);
    const tenant = {
      timezone: row.timezone,
      quiet_start: row.quiet_start,
      quiet_end: row.quiet_end,
      daily_sms_limit: row.daily_sms_limit,
      per_number_daily_limit: row.per_number_daily_limit,
    };
    if (inQuietHours(now, tenant.timezone, tenant.quiet_start, tenant.quiet_end)) {
      return {
        send: false,
        mark: Object.assign({}, base, {
          status: 'deferred',
          next_send_at: nextWindowOpen(now, tenant.timezone, tenant.quiet_start, tenant.quiet_end).toISOString(),
          body: '',
        }),
      };
    }
    if (!canText(tenant, Number(row.sent_today || 0), Number(row.sent_to_today || 0))) {
      return {
        send: false,
        mark: Object.assign({}, base, {
          status: 'rate_limited',
          next_send_at: new Date(now.getTime() + 60 * 60 * 1000).toISOString(),
          body: '',
        }),
      };
    }
    const origin = String((env && env.MCTB_PUBLIC_BASE_URL) || '').replace(/\/$/, '');
    if (!origin || !row.token) {
      return {
        send: false,
        mark: Object.assign({}, base, {
          status: 'deferred',
          next_send_at: new Date(now.getTime() + 60 * 60 * 1000).toISOString(),
          body: '',
        }),
      };
    }
    const link = origin + '/webhook/mctb-r?t=' + encodeURIComponent(row.token);
    const body = reviewBody(row.business_name || 'us', link, row.kind === 'reminder');
    return {
      send: true,
      body: body,
      mark: Object.assign({}, base, { status: 'mock_sent', next_send_at: null, body: body }),
    };
  }

  function basicAuth(env) {
    const sid = env.TWILIO_ACCOUNT_SID || '';
    const token = env.TWILIO_AUTH_TOKEN || '';
    if (!sid || !token) return '';
    return 'Basic ' + Buffer.from(sid + ':' + token).toString('base64');
  }

  function planWork(work, nowIso, env) {
    const source = work || {};
    const followups = source.followups || [];
    const queued = source.queued || [];
    const reviews = source.reviews || [];
    const live = Boolean(env && env.TWILIO_MODE === 'live' && env.TWILIO_ACCOUNT_SID && env.TWILIO_AUTH_TOKEN);
    const auth = live ? basicAuth(env) : '';
    const sentAdjust = {};
    const items = [];

    function bump(row, to) {
      const key = row.tenant_id;
      if (!sentAdjust[key]) sentAdjust[key] = { total: 0, byNumber: {} };
      sentAdjust[key].total += 1;
      sentAdjust[key].byNumber[to] = (sentAdjust[key].byNumber[to] || 0) + 1;
    }

    function adjusted(row, to) {
      const extra = sentAdjust[row.tenant_id] || { total: 0, byNumber: {} };
      return {
        sent_today: Number(row.sent_today || 0) + extra.total,
        sent_to_today: Number(row.sent_to_today || 0) + (extra.byNumber[to] || 0),
      };
    }

    followups.forEach((row) => {
      const counts = adjusted(row, row.phone);
      const planned = planOneFollowup(Object.assign({}, row, counts), nowIso);
      if (!planned || !planned.mark) return;
      if (planned.send) {
        planned.mark.status = live ? 'sent' : 'mock_sent';
        bump(row, row.phone);
      }
      items.push({
        live: Boolean(live && planned.send),
        from: row.twilio_number,
        to: row.phone,
        body: planned.body || '',
        auth: auth,
        mark: planned.mark,
      });
    });

    queued.forEach((row) => {
      const counts = adjusted(row, row.to_number);
      const planned = planOneQueued(Object.assign({}, row, counts), nowIso);
      if (!planned || !planned.mark) return;
      if (planned.send) {
        planned.mark.status = live ? 'sent' : 'mock_sent';
        bump(row, row.to_number);
      }
      items.push({
        live: Boolean(live && planned.send),
        from: row.from_number,
        to: row.to_number,
        body: planned.body || row.body || '',
        auth: auth,
        mark: planned.mark,
      });
    });

    reviews.forEach((row) => {
      const counts = adjusted(row, row.phone);
      const planned = planOneReview(Object.assign({}, row, counts), nowIso, env);
      if (!planned || !planned.mark) return;
      if (planned.send) {
        planned.mark.status = live ? 'sent' : 'mock_sent';
        bump(row, row.phone);
      }
      items.push({
        live: Boolean(live && planned.send),
        from: row.twilio_number,
        to: row.phone,
        body: planned.body || '',
        auth: auth,
        mark: planned.mark,
      });
    });
    return items;
  }

  function previewFollowupSequence(tenant, estimate, createdAtIso) {
    const created = new Date(createdAtIso);
    const firstAt = initialNextSendAt(created.toISOString(), tenant);
    const steps = [
      { label: 'Same day', at: firstAt, step: 0 },
      { label: '+2 days', at: addLocalDays(created, tenant.timezone, 2, 10, 0).toISOString(), step: 1 },
      { label: '+5 days', at: addLocalDays(created, tenant.timezone, 5, 10, 0).toISOString(), step: 2 },
    ];
    return steps.map((step) => ({
      label: step.label,
      at: step.at,
      when: formatWhen(step.at, tenant.timezone),
      body: followupBody(tenant, estimate, step.step),
    }));
  }

  function deliveryFor(env) {
    return env && env.TWILIO_MODE === 'live' && env.TWILIO_ACCOUNT_SID && env.TWILIO_AUTH_TOKEN ? 'queue' : 'mock';
  }

  function handleAction(ctx, env) {
    if (!ctx || !ctx.tenant) {
      return { save: 'no', record: null, html: renderDashboard(null, ''), ownerWebhook: null };
    }
    const tenant = ctx.tenant;
    const action = String(ctx.action || '').trim().toLowerCase();
    const delivery = deliveryFor(env);
    const outbound = [];
    let newEstimate = null;
    let estimateUpdate = null;
    let reviewRequest = null;
    let note = 'Nothing changed.';
    const ownerPhone = normalizePhone(tenant.owner_phone);

    if (action === 'estimate') {
      const phone = normalizePhone(ctx.phone);
      const cents = dollarsToCents(ctx.amount);
      const name = clip(ctx.customer_name, 80);
      const job = clip(ctx.job, 160);
      if (!name || !phone || !job || cents == null) {
        note = 'Name, phone, job, and amount are required.';
      } else {
        const nextSend = initialNextSendAt(ctx.now, tenant);
        newEstimate = {
          customer_name: name,
          phone: phone,
          job: job,
          amount_cents: cents,
          next_send_at: nextSend,
        };
        note = 'Estimate saved for ' + name + '. First follow-up ' + formatWhen(nextSend, tenant.timezone) + '.';
        if (isOptedOut(ctx, phone)) {
          note = 'Estimate saved for ' + name + '. ' + phone + ' opted out, so no follow-up text will be sent. Call them.';
        }
        if (ownerPhone) {
          outbound.push(outboundMessage(ownerPhone, tenant.twilio_number, note, 'owner_confirm', delivery, delivery === 'queue' ? nextSend : null));
        }
      }
    } else if (action === 'reply') {
      const phone = normalizePhone(ctx.phone);
      const message = clip(ctx.message, 500);
      if (!phone || !message) {
        note = 'A phone number and a message are required.';
      } else if (isOptedOut(ctx, phone)) {
        note = optedOutNotice(phone);
      } else if (!canText(tenant, Number(ctx.sentToday || 0), 0)) {
        note = 'Daily text limit reached. Call ' + phone + ' instead.';
      } else {
        outbound.push(outboundMessage(phone, tenant.twilio_number, message, 'owner_reply', delivery, null));
        const match = (ctx.estimates || []).find((estimate) => estimate.status === 'open' && normalizePhone(estimate.phone) === phone);
        if (match) estimateUpdate = { id: Number(match.id), status: 'replied' };
        note = delivery === 'mock'
          ? 'Reply recorded for ' + phone + '.'
          : 'Reply queued for ' + phone + '. It sends on the next follow-up pass (about a minute).';
      }
    } else if (action === 'won' || action === 'lost') {
      const estimate = findEstimate(ctx, ctx.estimate_id);
      if (!estimate) {
        note = 'That estimate was not found.';
      } else {
        estimateUpdate = { id: Number(estimate.id), status: action };
        note = 'Estimate ' + estimate.id + ' marked ' + action + '.';
        if (action === 'won') {
          const scheduled = maybeScheduleReview(ctx, estimate.phone, estimate.id);
          if (scheduled.reason === 'scheduled') {
            reviewRequest = scheduled.request;
            note += ' Review text scheduled.';
          }
        }
      }
    } else {
      note = 'Unknown action.';
    }

    const notification = { channel: 'dashboard', body: note };
    const record = newEstimate || estimateUpdate || outbound.length ? {
      tenant_id: tenant.id,
      from: ownerPhone || '',
      to: tenant.twilio_number,
      message_sid: '',
      inbound_body: null,
      inbound_purpose: 'dashboard',
      suppress: false,
      clear_suppression: false,
      conversation: null,
      outbound: outbound,
      new_estimate: newEstimate,
      estimate_update: estimateUpdate,
      review_request: reviewRequest,
      owner_notification: notification,
    } : null;

    return {
      save: record ? 'yes' : 'no',
      record: record,
      html: renderActionResult(tenant, ctx.token, note),
      ownerWebhook: ownerWebhook(tenant, note, 'MCTB_DASHBOARD'),
    };
  }

  function renderActionResult(tenant, token, note) {
    const back = '/webhook/mctb-dashboard?token=' + encodeURIComponent(token || '');
    return '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' +
      '<meta http-equiv="refresh" content="2;url=' + escapeHtml(back) + '"><title>' + escapeHtml(tenant.business_name) + '</title>' +
      '<style>body{margin:0;background:#241c16;color:#f6f0e6;font-family:Georgia,serif;display:grid;min-height:100vh;place-items:center}main{max-width:28rem;padding:2rem}a{color:#f0a06a}</style></head><body><main><p>' +
      escapeHtml(note) + '</p><p><a href="' + escapeHtml(back) + '">Back to the board</a></p></main></body></html>';
  }

  function stat(label, value) {
    return '<div class="stat"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong></div>';
  }

  function periodValue(stats, key, money) {
    const value = stats && stats[key] != null ? stats[key] : 0;
    return escapeHtml(money ? formatMoney(value) : value);
  }

  function periodTable(periods) {
    const week = (periods && periods.week) || {};
    const month = (periods && periods.month) || {};
    const rows = [
      ['Missed calls', 'missed_calls', false],
      ['Callers texted', 'callers_texted', false],
      ['Conversations recovered', 'recovered', false],
      ['Qualified leads', 'qualified_leads', false],
      ['Estimates sent', 'estimates_sent', false],
      ['Jobs won', 'jobs_won', false],
      ['Won', 'won_cents', true],
      ['Estimated revenue recovered', 'recovered_revenue_cents', true],
    ];
    return '<h2>This week / This month</h2><table><thead><tr><th></th><th>This week</th><th>This month</th></tr></thead><tbody>' +
      rows.map((row) => '<tr><td>' + row[0] + '</td><td>' + periodValue(week, row[1], row[2]) + '</td><td>' + periodValue(month, row[1], row[2]) + '</td></tr>').join('') +
      '</tbody></table>';
  }

  function renderDashboard(snap, token) {
    if (!snap || !snap.tenant) {
      return '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Dashboard link</title></head><body><p>This dashboard link is not valid.</p></body></html>';
    }
    const tenant = snap.tenant;
    const stats = snap.stats || {};
    const tz = tenant.timezone || 'UTC';
    const hidden = '<input type="hidden" name="token" value="' + escapeHtml(token || '') + '">';
    const calls = (snap.calls || []).map((call) => '<tr><td>' + escapeHtml(formatWhen(call.created_at, tz)) + '</td><td>' + escapeHtml(call.from_number) + '</td><td>' + (call.missed ? 'missed' : 'answered') + '</td><td>' + (call.text_sent ? 'texted' : escapeHtml(call.suppressed_reason || '')) + '</td></tr>').join('');
    const leads = (snap.conversations || []).map((lead) => '<tr><td>' + escapeHtml(lead.phone) + '</td><td>' + escapeHtml(lead.state) + '</td><td>' + escapeHtml(lead.need || '') + '</td><td>' + escapeHtml(lead.location || '') + '</td><td>' + escapeHtml(lead.urgency || '') + '</td></tr>').join('');
    const estimates = (snap.estimates || []).map((estimate) => '<tr><td>' + escapeHtml(estimate.id) + '</td><td>' + escapeHtml(estimate.customer_name) + '</td><td>' + escapeHtml(estimate.phone) + '</td><td>' + escapeHtml(estimate.job) + '</td><td>' + escapeHtml(formatMoney(estimate.amount_cents)) + '</td><td>' + escapeHtml(estimate.status) + '</td><td>' + escapeHtml(estimate.next_send_at ? formatWhen(estimate.next_send_at, tz) : '') + '</td><td>' +
      '<form method="post" action="/webhook/mctb-action">' + hidden + '<input type="hidden" name="action" value="won"><input type="hidden" name="estimate_id" value="' + escapeHtml(estimate.id) + '"><button type="submit">Won</button></form>' +
      '<form method="post" action="/webhook/mctb-action">' + hidden + '<input type="hidden" name="action" value="lost"><input type="hidden" name="estimate_id" value="' + escapeHtml(estimate.id) + '"><button type="submit">Lost</button></form></td></tr>').join('');
    const notes = (snap.notifications || []).map((note) => '<li><time>' + escapeHtml(formatWhen(note.created_at, tz)) + '</time> ' + escapeHtml(note.body) + '</li>').join('');
    return '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' +
      escapeHtml(tenant.business_name) + ' · BrightReach</title><style>' +
      'body{margin:0;background:#1c1712;color:#f4efe6;font-family:Georgia,serif}main{max-width:1100px;margin:0 auto;padding:1.2rem}' +
      'h1{font-weight:500;font-size:1.8rem;margin:0}h2{font-size:1.05rem;letter-spacing:.04em;text-transform:uppercase}' +
      '.sub{color:#d9c7ae}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.6rem;margin:1rem 0}' +
      '.stat{background:#2a221b;padding:.8rem;border-top:3px solid #e25b2a}.stat span{display:block;color:#d9c7ae;font-size:.78rem}.stat strong{font-size:1.4rem}' +
      'table{width:100%;border-collapse:collapse;background:#f7f1e4;color:#241c16;font-family:Segoe UI,sans-serif;font-size:.92rem}' +
      'th,td{padding:.45rem .5rem;border-bottom:1px solid #e4d8c4;text-align:left;vertical-align:top}form{display:inline}' +
      'button,input,textarea{font:inherit}input,textarea{width:100%;box-sizing:border-box;margin:.2rem 0 .6rem;padding:.4rem}' +
      '.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}@media(max-width:800px){.grid{grid-template-columns:1fr}}' +
      '.card{background:#f7f1e4;color:#241c16;padding:1rem}a{color:#9a3412}' +
      '</style></head><body><main><p class="sub">BrightReach · Missed-call text-back</p><h1>' + escapeHtml(tenant.business_name) + '</h1>' +
      '<p class="sub">' + escapeHtml(tenant.twilio_number) + ' · ' + escapeHtml(tenant.hours_text || '') + ' · texts ' + escapeHtml(tenant.owner_name) + ' at ' + escapeHtml(tenant.owner_phone) + '</p>' +
      '<section class="stats">' +
      stat('Missed calls, 7 days', stats.missed_calls_7d || 0) +
      stat('Recovered replies, 7 days', stats.recovered_7d || 0) +
      stat('Qualified leads, 30 days', stats.leads_30d || 0) +
      stat('Texts sent, 30 days', stats.texts_30d || 0) +
      stat('Open estimates', stats.estimates_open || 0) +
      stat('Jobs won, 30 days', stats.jobs_won_30d || 0) +
      stat('Won amount, 30 days', formatMoney(stats.won_cents_30d || 0)) +
      stat('Opt-outs', stats.opt_outs || 0) +
      stat('Review requests sent', stats.reviews_sent || 0) +
      stat('Review links clicked', stats.reviews_clicked || 0) +
      '</section>' + periodTable(stats.periods) +
      '<div class="grid"><section class="card"><h2>Log an estimate</h2><form method="post" action="/webhook/mctb-action">' + hidden +
      '<input type="hidden" name="action" value="estimate"><label>Name</label><input name="customer_name" required><label>Phone</label><input name="phone" required>' +
      '<label>Job</label><input name="job" required><label>Amount (dollars)</label><input name="amount" required><button type="submit">Save and schedule follow-up</button></form></section>' +
      '<section class="card"><h2>Text a customer</h2><form method="post" action="/webhook/mctb-action">' + hidden +
      '<input type="hidden" name="action" value="reply"><label>Phone</label><input name="phone" required><label>Message</label><textarea name="message" rows="4" required></textarea><button type="submit">Send through the business number</button></form></section></div>' +
      '<h2>Missed calls</h2><table><thead><tr><th>When</th><th>From</th><th>Call</th><th>Text</th></tr></thead><tbody>' + (calls || '<tr><td colspan="4">No calls yet.</td></tr>') + '</tbody></table>' +
      '<h2>Leads</h2><table><thead><tr><th>Phone</th><th>State</th><th>Need</th><th>Address / ZIP</th><th>Urgency</th></tr></thead><tbody>' + (leads || '<tr><td colspan="5">No conversations yet.</td></tr>') + '</tbody></table>' +
      '<h2>Estimates</h2><table><thead><tr><th>ID</th><th>Name</th><th>Phone</th><th>Job</th><th>Amount</th><th>Status</th><th>Next text</th><th></th></tr></thead><tbody>' + (estimates || '<tr><td colspan="8">No estimates yet.</td></tr>') + '</tbody></table>' +
      '<h2>Owner feed</h2><ul>' + (notes || '<li>No notifications yet.</li>') + '</ul></main></body></html>';
  }

  function freshState() {
    return {
      conversation: null,
      suppressed: false,
      suppressedPhones: [],
      ownerCapNoticeSent: false,
      estimates: [],
      messages: [],
      calls: [],
      notifications: [],
      outbound: [],
      sentToday: 0,
      sentToNumber: {},
      recentReviewPhones: [],
    };
  }

  function project(state, record) {
    const next = {
      conversation: state.conversation,
      suppressed: state.suppressed,
      suppressedPhones: (state.suppressedPhones || []).slice(),
      ownerCapNoticeSent: Boolean(state.ownerCapNoticeSent),
      estimates: state.estimates.map((estimate) => Object.assign({}, estimate)),
      messages: state.messages.slice(),
      calls: state.calls.slice(),
      notifications: state.notifications.slice(),
      outbound: state.outbound.slice(),
      sentToday: state.sentToday,
      sentToNumber: Object.assign({}, state.sentToNumber),
      recentReviewPhones: (state.recentReviewPhones || []).slice(),
    };
    if (!record) return next;
    if (record.conversation) next.conversation = Object.assign({}, record.conversation);
    if (record.suppress) {
      next.suppressed = true;
      const stopped = normalizePhone(record.from);
      if (stopped && next.suppressedPhones.indexOf(stopped) === -1) next.suppressedPhones.push(stopped);
    }
    if (record.clear_suppression) {
      next.suppressed = false;
      const resumed = normalizePhone(record.from);
      next.suppressedPhones = next.suppressedPhones.filter((phone) => phone !== resumed);
    }
    if (record.kind === 'incoming' || record.kind === 'dial_status') {
      next.calls.push({
        from: record.from,
        missed: record.missed,
        text_sent: record.text_sent,
        suppressed_reason: record.suppressed_reason || null,
      });
    }
    if (record.inbound_body) {
      next.messages.push({ direction: 'in', from: record.from, body: record.inbound_body });
    }
    (record.outbound || []).forEach((item) => {
      if (item.delivery === 'twiml' || item.delivery === 'mock') {
        next.sentToday += 1;
        next.sentToNumber[item.to] = (next.sentToNumber[item.to] || 0) + 1;
        next.messages.push({ direction: 'out', to: item.to, body: item.body, purpose: item.purpose });
      }
      if (item.purpose === 'owner_cap_notice') next.ownerCapNoticeSent = true;
      next.outbound.push(item);
    });
    if (record.new_estimate) {
      next.estimates.push(Object.assign({
        id: next.estimates.length + 1,
        status: 'open',
        step_index: 0,
        created_at: record.new_estimate.next_send_at,
      }, record.new_estimate));
    }
    if (record.estimate_update) {
      next.estimates.forEach((estimate) => {
        if (Number(estimate.id) === Number(record.estimate_update.id)) {
          estimate.status = record.estimate_update.status;
          if (['won', 'lost', 'replied', 'opted_out', 'completed'].indexOf(estimate.status) !== -1) {
            estimate.next_send_at = null;
          }
        }
      });
    }
    if (record.review_request && record.review_request.phone) {
      const reviewed = normalizePhone(record.review_request.phone);
      if (reviewed && next.recentReviewPhones.indexOf(reviewed) === -1) next.recentReviewPhones.push(reviewed);
    }
    if (record.owner_notification) next.notifications.push(record.owner_notification);
    return next;
  }

  function voiceContext(state, tenant, fields) {
    const from = fields.from || '';
    return {
      now: fields.now,
      to: fields.to || tenant.twilio_number,
      from: from,
      callSid: fields.callSid || '',
      callStatus: fields.callStatus || '',
      dialStatus: fields.dialStatus || '',
      publicBaseUrl: fields.publicBaseUrl || '',
      tenant: tenant,
      duplicate: Boolean(fields.duplicate),
      suppressed: Boolean(state.suppressed),
      suppressedPhones: (state.suppressedPhones || []).slice(),
      ownerCapNoticeSent: Boolean(state.ownerCapNoticeSent),
      dashboardPath: state.dashboardPath || '',
      conversation: state.conversation,
      sentToday: state.sentToday,
      sentToToday: state.sentToNumber[from] || 0,
      sentToOwnerToday: state.sentToNumber[normalizePhone(tenant.owner_phone)] || 0,
    };
  }

  function smsContext(state, tenant, fields) {
    const from = fields.from || '';
    const open = state.estimates.filter((estimate) => estimate.status === 'open' && normalizePhone(estimate.phone) === from);
    return {
      now: fields.now,
      to: fields.to || tenant.twilio_number,
      from: from,
      body: fields.body || '',
      messageSid: fields.messageSid || '',
      publicBaseUrl: fields.publicBaseUrl || '',
      tenant: tenant,
      suppressed: state.suppressed,
      suppressedPhones: (state.suppressedPhones || []).slice(),
      ownerCapNoticeSent: Boolean(state.ownerCapNoticeSent),
      dashboardPath: state.dashboardPath || '',
      conversation: state.conversation,
      openEstimate: open[0] || null,
      estimates: state.estimates,
      recentReviewPhones: (state.recentReviewPhones || []).slice(),
      sentToday: state.sentToday,
      sentToToday: state.sentToNumber[from] || 0,
      sentToOwnerToday: state.sentToNumber[normalizePhone(tenant.owner_phone)] || 0,
    };
  }

  function contextFromLoad(loaded, fields) {
    const packLoaded = loaded || {};
    return {
      now: fields.now,
      to: fields.to || '',
      from: fields.from || '',
      callSid: fields.callSid || '',
      callStatus: fields.callStatus || '',
      dialStatus: fields.dialStatus || '',
      body: fields.body || '',
      messageSid: fields.messageSid || '',
      publicBaseUrl: fields.publicBaseUrl || '',
      token: fields.token || '',
      action: fields.action || '',
      customer_name: fields.customer_name || '',
      phone: fields.phone || '',
      job: fields.job || '',
      amount: fields.amount || '',
      message: fields.message || '',
      estimate_id: fields.estimate_id || '',
      tenant: packLoaded.tenant || null,
      duplicate: Boolean(packLoaded.duplicate),
      suppressed: Boolean(packLoaded.suppressed),
      suppressedPhones: Array.isArray(packLoaded.suppressed_phones) ? packLoaded.suppressed_phones : [],
      ownerCapNoticeSent: Boolean(packLoaded.owner_cap_notice_sent),
      dashboardPath: packLoaded.dashboard_path || '',
      conversation: packLoaded.conversation || null,
      openEstimate: packLoaded.open_estimate || null,
      estimates: packLoaded.estimates || [],
      recentReviewPhones: Array.isArray(packLoaded.recent_review_phones) ? packLoaded.recent_review_phones : [],
      sentToday: Number(packLoaded.sent_today || 0),
      sentToToday: Number(packLoaded.sent_to_today || 0),
      sentToOwnerToday: Number(packLoaded.sent_to_owner_today || 0),
    };
  }

  function unwrapWebhook(item) {
    if (!item || typeof item !== 'object') return { body: {}, query: {}, headers: {} };
    const envelope = item.body && typeof item.body === 'object' && (item.headers || item.query || item.params);
    if (envelope) return { body: item.body, query: item.query || {}, headers: item.headers || {} };
    return { body: item, query: item.query || {}, headers: item.headers || {} };
  }

  function twilioFields(body) {
    const source = body && typeof body === 'object' ? body : {};
    return {
      from: source.From || source.from || '',
      to: source.To || source.to || '',
      callSid: source.CallSid || source.call_sid || '',
      callStatus: source.CallStatus || source.call_status || '',
      dialStatus: source.DialCallStatus || source.dial_status || '',
      messageSid: source.MessageSid || source.message_sid || '',
      text: typeof source.Body === 'string' ? source.Body : (typeof source.text === 'string' ? source.text : ''),
    };
  }

  function readToken(item) {
    const env = unwrapWebhook(item);
    return String((env.query && env.query.token) || (env.body && env.body.token) || item.token || '').trim();
  }

  function exampleTenant(overrides) {
    return Object.assign({
      id: '00000000-0000-4000-8000-000000000001',
      slug: 'northline',
      business_name: 'Northline Heating & Air',
      twilio_number: '+14145550100',
      owner_name: 'Dana',
      owner_phone: '+14145550199',
      owner_email: 'dana@northline.example',
      timezone: 'America/Chicago',
      hours_text: 'Mon–Sat 7am–7pm',
      booking_link: 'https://northline.example/book',
      call_mode: 'forward',
      ring_timeout_seconds: 20,
      missed_call_template: DEFAULTS.missed_call_template,
      qualify_location_prompt: DEFAULTS.qualify_location_prompt,
      qualify_urgency_prompt: DEFAULTS.qualify_urgency_prompt,
      qualify_done_template: DEFAULTS.qualify_done_template,
      followup_templates: DEFAULTS.followup_templates.slice(),
      quiet_start: '21:00',
      quiet_end: '08:00',
      missed_call_respects_quiet_hours: false,
      daily_sms_limit: 200,
      per_number_daily_limit: 12,
      owner_daily_sms_limit: 60,
      revenue_report_enabled: true,
      google_review_url: null,
      active: true,
    }, overrides || {});
  }

  function countOf(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? String(number) : '0';
  }

  function formatRevenueReport(spec) {
    const input = spec || {};
    const stats = input.stats || {};
    const link = String(input.dashboardUrl || '');
    const period = input.period === 'month' ? 'This month' : 'Last week';
    const name = clip(input.businessName || 'Shop', 24);
    const won = formatMoney(stats.won_cents || 0);
    const recovered = formatMoney(stats.recovered_revenue_cents || 0);
    const full = [
      name + ' ' + period,
      'Missed ' + countOf(stats.missed_calls) + ', texted ' + countOf(stats.callers_texted),
      'Recovered ' + countOf(stats.recovered) + ', leads ' + countOf(stats.qualified_leads),
      'Estimates ' + countOf(stats.estimates_sent) + ', won ' + countOf(stats.jobs_won) + ' (' + won + ')',
      'Est. recovered ' + recovered,
    ];
    const short = [
      period,
      'Missed ' + countOf(stats.missed_calls) + ' texted ' + countOf(stats.callers_texted) + ' recovered ' + countOf(stats.recovered),
      'Leads ' + countOf(stats.qualified_leads) + ' est ' + countOf(stats.estimates_sent) + ' won ' + countOf(stats.jobs_won) + ' ' + won,
      'Est. recovered ' + recovered,
    ];
    function join(lines) {
      const copy = lines.slice();
      if (link) copy.push(link);
      return copy.join('\n');
    }
    const candidates = [join(full), join(short)];
    for (let i = 0; i < candidates.length; i += 1) {
      if (candidates[i].length <= 320) return candidates[i];
    }
    const tail = link ? '\n' + link : '';
    const budget = 320 - tail.length;
    if (budget >= 24) return short.join('\n').slice(0, budget) + tail;
    return (link || short.join('\n')).slice(0, 320);
  }

  function planRevenueReports(rows, nowIso, env) {
    const list = Array.isArray(rows) ? rows : [];
    const base = String((env && env.MCTB_PUBLIC_BASE_URL) || '').replace(/\/$/, '');
    return list.map((row) => {
      const owner = normalizePhone(row.owner_phone);
      const dashboardUrl = base && row.dashboard_token
        ? base + '/webhook/mctb-dashboard?token=' + row.dashboard_token
        : '';
      const body = formatRevenueReport({
        businessName: row.business_name,
        period: row.period,
        stats: row.stats || {},
        dashboardUrl: dashboardUrl,
      });
      const common = {
        tenant_id: row.tenant_id,
        period: row.period,
        period_key: row.period_key,
        body: body,
        to: owner || '',
        from: row.twilio_number,
        purpose: 'revenue_report_' + (row.period || 'week'),
      };
      if (!owner) return Object.assign({}, common, { status: 'skipped', send_at: null });
      if (row.suppressed) return Object.assign({}, common, { status: 'suppressed', send_at: null });
      const now = new Date(nowIso);
      let sendAt = now.toISOString();
      if (inQuietHours(now, row.timezone, row.quiet_start, row.quiet_end)) {
        sendAt = nextWindowOpen(now, row.timezone, row.quiet_start, row.quiet_end).toISOString();
      }
      return Object.assign({}, common, { status: 'queued', send_at: sendAt });
    });
  }

  const FOLLOWUPS_STALE_SECONDS = 600;
  const REVENUE_STALE_SECONDS = 3600;
  const HEALTH_STARTUP_GRACE_SECONDS = 1200;
  const HEALTH_ALERT_COOLDOWN_MS = 60 * 60 * 1000;

  function healthProblems(row) {
    const problems = [];
    if (!row || row.db !== 'ok') {
      problems.push('Postgres is not answering');
      return problems;
    }
    const tenants = Number(row.active_tenants || 0);
    if (tenants === 0) return problems;
    const young = row.oldest_tenant_age_seconds == null || Number(row.oldest_tenant_age_seconds) < HEALTH_STARTUP_GRACE_SECONDS;
    const followupsMissing = row.followups_age_seconds == null;
    const revenueMissing = row.revenue_age_seconds == null;
    if (!(young && followupsMissing) && (followupsMissing || Number(row.followups_age_seconds) >= FOLLOWUPS_STALE_SECONDS)) {
      problems.push('Follow-up workflow has not succeeded in the last 10 minutes');
    }
    if (!(young && revenueMissing) && (revenueMissing || Number(row.revenue_age_seconds) >= REVENUE_STALE_SECONDS)) {
      problems.push('Revenue report workflow has not succeeded in the last hour');
    }
    return problems;
  }

  function heartbeatOk(row) {
    return healthProblems(row).length === 0 && Boolean(row && row.db === 'ok');
  }

  function planHealthAlert(facts, staticState, nowIso, env) {
    const now = new Date(nowIso);
    const fact = facts && facts.db ? facts : { db: 'down' };
    const stored = staticState || {};
    const dbOk = fact.db === 'ok';
    const state = dbOk && fact.state ? fact.state : {
      status: stored.status || 'ok',
      alert_open: Boolean(stored.alertOpen),
      last_alert_at: stored.lastAlertAt || null,
    };
    const problems = healthProblems(fact);
    const healthy = problems.length === 0;
    const detail = problems.join('. ');
    const lastAlertMs = state.last_alert_at ? new Date(state.last_alert_at).getTime() : 0;
    const cooled = !lastAlertMs || (now.getTime() - lastAlertMs) >= HEALTH_ALERT_COOLDOWN_MS;
    let action = 'none';
    if (!healthy && cooled) action = 'alert';
    else if (healthy && state.alert_open) action = 'recovery';

    const environment = env || {};
    const adminPhone = normalizePhone(environment.MCTB_ADMIN_PHONE);
    const adminEmail = String(environment.MCTB_ADMIN_EMAIL || '').trim();
    const smtpHost = String(environment.N8N_SMTP_HOST || environment.SMTP_HOST || '').trim();
    const sender = fact.sender || stored.sender || null;
    const quiet = Boolean(sender && inQuietHours(now, sender.timezone, sender.quiet_start, sender.quiet_end));
    const suppressed = Boolean(sender && sender.suppressed);
    const live = Boolean(environment.TWILIO_MODE === 'live' && environment.TWILIO_ACCOUNT_SID && environment.TWILIO_AUTH_TOKEN);

    function buildSms(kind) {
      if (!adminPhone || !sender || !sender.twilio_number || suppressed) return null;
      const body = kind === 'alert'
        ? 'BrightReach alert: ' + detail
        : 'BrightReach recovered. Postgres and the scheduled workflows are answering again.';
      const purpose = kind === 'alert' ? 'health_alert' : 'health_recovery';
      if (quiet) {
        return {
          delivery: 'queue',
          tenant_id: sender.tenant_id,
          to: adminPhone,
          from: sender.twilio_number,
          body: body,
          purpose: purpose,
          send_at: nextWindowOpen(now, sender.timezone, sender.quiet_start, sender.quiet_end).toISOString(),
        };
      }
      return {
        delivery: live ? 'twilio' : 'mock',
        tenant_id: sender.tenant_id,
        to: adminPhone,
        from: sender.twilio_number,
        body: body,
        purpose: purpose,
        send_at: now.toISOString(),
      };
    }

    function buildEmail(kind) {
      if (!adminEmail || !smtpHost) return null;
      return {
        to: adminEmail,
        subject: kind === 'alert' ? 'BrightReach alert' : 'BrightReach recovered',
        body: kind === 'alert'
          ? detail
          : 'Postgres and the scheduled workflows are answering again.',
      };
    }

    const sms = action === 'none' ? null : buildSms(action);
    const email = action === 'none' ? null : buildEmail(action);
    if (action !== 'none' && !sms && !email) action = 'none';

    let alertOpen = Boolean(state.alert_open);
    let lastAlertAt = state.last_alert_at || null;
    if (action === 'alert') {
      alertOpen = true;
      lastAlertAt = now.toISOString();
    } else if (action === 'recovery') {
      alertOpen = false;
    }

    const rememberedSender = (dbOk && fact.sender) ? fact.sender : (stored.sender || null);
    return {
      action: action,
      save: action === 'none' ? 'no' : 'yes',
      live_sms: sms && sms.delivery === 'twilio' ? 'yes' : 'no',
      send_email: email ? 'yes' : 'no',
      auth: sms && sms.delivery === 'twilio' ? basicAuth(environment) : '',
      record: {
        status: healthy ? 'ok' : 'alerted',
        alert_open: alertOpen,
        last_alert_at: lastAlertAt,
        detail: action === 'recovery' ? 'recovered' : (detail || state.last_detail || ''),
        sms: action === 'none' ? null : sms,
        email: action === 'none' ? null : email,
      },
      staticPatch: {
        status: alertOpen ? 'alerted' : 'ok',
        alertOpen: alertOpen,
        lastAlertAt: lastAlertAt,
        sender: rememberedSender,
      },
    };
  }

  function fallbackTwiml(ownerPhone) {
    const owner = normalizePhone(ownerPhone) || '';
    const voice = '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Sorry we missed you, we\'ll text you right back.</Say><Hangup/></Response>';
    const sms = '<?xml version="1.0" encoding="UTF-8"?><Response><Message to="' + xmlEscape(owner || '+1OWNERNUMBER') + '">Text to this number did not reach the usual handler. From {{From}}: {{Body}}</Message></Response>';
    return { voice: voice, sms: sms };
  }

  return {
    ENGINE_VERSION: ENGINE_VERSION,
    DEFAULTS: DEFAULTS,
    normalizePhone: normalizePhone,
    keyword: keyword,
    renderTemplate: renderTemplate,
    formatMoney: formatMoney,
    dollarsToCents: dollarsToCents,
    inQuietHours: inQuietHours,
    nextWindowOpen: nextWindowOpen,
    addLocalDays: addLocalDays,
    zonedTimeToUtc: zonedTimeToUtc,
    formatWhen: formatWhen,
    escapeHtml: escapeHtml,
    xmlEscape: xmlEscape,
    buildTwiml: buildTwiml,
    handleVoice: handleVoice,
    handleDialStatus: handleDialStatus,
    handleInboundSms: handleInboundSms,
    handleAction: handleAction,
    parseOwnerCommand: parseOwnerCommand,
    planOneFollowup: planOneFollowup,
    planOneQueued: planOneQueued,
    planOneReview: planOneReview,
    planWork: planWork,
    reviewSendAt: reviewSendAt,
    formatRevenueReport: formatRevenueReport,
    planRevenueReports: planRevenueReports,
    healthProblems: healthProblems,
    heartbeatOk: heartbeatOk,
    planHealthAlert: planHealthAlert,
    fallbackTwiml: fallbackTwiml,
    previewFollowupSequence: previewFollowupSequence,
    followupBody: followupBody,
    initialNextSendAt: initialNextSendAt,
    renderDashboard: renderDashboard,
    freshState: freshState,
    project: project,
    voiceContext: voiceContext,
    smsContext: smsContext,
    contextFromLoad: contextFromLoad,
    unwrapWebhook: unwrapWebhook,
    twilioFields: twilioFields,
    readToken: readToken,
    exampleTenant: exampleTenant,
    commandHelp: commandHelp,
  };
}

const api = createEngine();
if (typeof module === 'object' && module && module.exports) {
  module.exports = api;
}
if (typeof globalThis !== 'undefined') {
  globalThis.MctbEngine = api;
}
