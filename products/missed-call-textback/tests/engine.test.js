'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const engine = require('../logic/engine');

const DAY = '2026-10-03T15:00:00.000Z'; // 10:00 America/Chicago
const NIGHT = '2026-10-03T02:30:00.000Z'; // 21:30 America/Chicago

function caller() {
  return '+14145550123';
}

test('normalizes US numbers and rejects junk', () => {
  assert.equal(engine.normalizePhone('(414) 555-0123'), '+14145550123');
  assert.equal(engine.normalizePhone('14145550123'), '+14145550123');
  assert.equal(engine.normalizePhone('+14145550123'), '+14145550123');
  assert.equal(engine.normalizePhone('555'), null);
  assert.equal(engine.normalizePhone(''), null);
});

test('STOP is the whole message, not a sentence', () => {
  assert.equal(engine.keyword('stop'), 'stop');
  assert.equal(engine.keyword('STOP.'), 'stop');
  assert.equal(engine.keyword('stop by tomorrow'), null);
  assert.equal(engine.keyword('HELP'), 'help');
  assert.equal(engine.keyword('unstop'), 'start');
});

test('forwarded missed call texts the caller and the owner', () => {
  const tenant = engine.exampleTenant();
  const state = engine.freshState();
  const decision = engine.handleVoice(engine.voiceContext(state, tenant, {
    now: DAY,
    from: caller(),
    callSid: 'CA1',
    publicBaseUrl: 'https://text.example',
  }));
  assert.equal(decision.save, 'yes');
  assert.match(decision.twiml, /Northline Heating &amp; Air/);
  assert.match(decision.twiml, /Reply STOP to opt out/);
  assert.match(decision.twiml, /to="\+14145550199"/);
  assert.equal(decision.record.missed, true);
  assert.equal(decision.record.text_sent, true);
  assert.equal(decision.record.conversation.state, 'awaiting_need');
  assert.equal(decision.record.outbound.filter((item) => item.delivery === 'twiml').length, 2);
});

test('opted-out callers are logged and not texted', () => {
  const tenant = engine.exampleTenant();
  const state = engine.freshState();
  state.suppressed = true;
  const decision = engine.handleVoice(engine.voiceContext(state, tenant, {
    now: DAY,
    from: caller(),
    callSid: 'CA2',
  }));
  assert.equal(decision.record.text_sent, false);
  assert.equal(decision.record.suppressed_reason, 'opted_out');
  assert.equal(decision.record.outbound.some((item) => item.to === caller()), false);
  assert.match(decision.record.owner_notification.body, /opted out/);
});

test('dial mode rings first and texts only on no-answer', () => {
  const tenant = engine.exampleTenant({ call_mode: 'dial' });
  const state = engine.freshState();
  const incoming = engine.handleVoice(engine.voiceContext(state, tenant, {
    now: DAY,
    from: caller(),
    callSid: 'CA3',
    publicBaseUrl: 'https://text.example',
  }));
  assert.match(incoming.twiml, /<Dial timeout="20"/);
  assert.doesNotMatch(incoming.twiml, /<Message/);
  assert.equal(incoming.record.missed, false);

  const missed = engine.handleDialStatus(engine.voiceContext(state, tenant, {
    now: DAY,
    from: caller(),
    callSid: 'CA3',
    dialStatus: 'no-answer',
  }));
  assert.equal(missed.record.missed, true);
  assert.match(missed.twiml, /Sorry we missed your call/);

  const answered = engine.handleDialStatus(engine.voiceContext(state, tenant, {
    now: DAY,
    from: caller(),
    callSid: 'CA3',
    dialStatus: 'completed',
  }));
  assert.equal(answered.record.missed, false);
  assert.equal(answered.record.outbound.length, 0);
});

test('dial mode without a public URL does not invent a callback', () => {
  const tenant = engine.exampleTenant({ call_mode: 'dial' });
  const decision = engine.handleVoice(engine.voiceContext(engine.freshState(), tenant, {
    now: DAY,
    from: caller(),
    callSid: 'CA4',
    publicBaseUrl: '',
  }));
  assert.equal(decision.record.suppressed_reason, 'missing_public_base_url');
  assert.doesNotMatch(decision.twiml, /<Dial/);
});

test('qualification collects need, place, and urgency, then hands off', () => {
  const tenant = engine.exampleTenant();
  let state = engine.freshState();
  const opened = engine.handleVoice(engine.voiceContext(state, tenant, { now: DAY, from: caller(), callSid: 'CA5' }));
  state = engine.project(state, opened.record);

  const need = engine.handleInboundSms(engine.smsContext(state, tenant, { now: DAY, from: caller(), body: 'Furnace will not start' }));
  assert.match(need.twiml, /address or ZIP/);
  state = engine.project(state, need.record);

  const place = engine.handleInboundSms(engine.smsContext(state, tenant, { now: DAY, from: caller(), body: '53211' }));
  assert.match(place.twiml, /TODAY, THIS WEEK, or FLEXIBLE/);
  state = engine.project(state, place.record);

  const urgent = engine.handleInboundSms(engine.smsContext(state, tenant, { now: DAY, from: caller(), body: 'today please' }));
  assert.equal(urgent.record.conversation.state, 'handed_off');
  assert.equal(urgent.record.conversation.urgency, 'today');
  assert.match(urgent.record.owner_notification.body, /53211/);
  assert.match(urgent.record.owner_notification.body, /Furnace will not start/);
  assert.match(urgent.twiml, /Dana/);
});

test('STOP, HELP, and START', () => {
  const tenant = engine.exampleTenant();
  let state = engine.project(engine.freshState(), engine.handleVoice(engine.voiceContext(engine.freshState(), tenant, {
    now: DAY, from: caller(), callSid: 'CA6',
  })).record);
  const help = engine.handleInboundSms(engine.smsContext(state, tenant, { now: DAY, from: caller(), body: 'HELP' }));
  assert.match(help.twiml, /Reply STOP to unsubscribe/);
  assert.equal(help.record.suppress, false);

  const stop = engine.handleInboundSms(engine.smsContext(state, tenant, { now: DAY, from: caller(), body: 'STOP' }));
  assert.equal(stop.record.suppress, true);
  assert.match(stop.twiml, /unsubscribed/);
  state = engine.project(state, stop.record);

  const ignored = engine.handleInboundSms(engine.smsContext(state, tenant, { now: DAY, from: caller(), body: 'Are you coming?' }));
  assert.equal(ignored.record.outbound.some((item) => item.to === caller()), false);

  const start = engine.handleInboundSms(engine.smsContext(state, tenant, { now: DAY, from: caller(), body: 'START' }));
  assert.equal(start.record.clear_suppression, true);
  assert.match(start.twiml, /resubscribed/);
});

test('owner commands log estimates, replies, and won jobs', () => {
  const tenant = engine.exampleTenant();
  const state = engine.freshState();
  const estimate = engine.handleInboundSms(engine.smsContext(state, tenant, {
    now: DAY,
    from: tenant.owner_phone,
    body: 'ESTIMATE Jane Doe | 4145550199 | furnace | 4500',
  }));
  assert.equal(estimate.record.new_estimate.customer_name, 'Jane Doe');
  assert.equal(estimate.record.new_estimate.amount_cents, 450000);
  assert.equal(estimate.record.new_estimate.phone, '+14145550199');
  let next = engine.project(state, estimate.record);

  const reply = engine.handleInboundSms(engine.smsContext(next, tenant, {
    now: DAY,
    from: tenant.owner_phone,
    body: 'REPLY +14145550199 We can be there Thursday',
  }));
  assert.equal(reply.record.outbound.some((item) => item.to === '+14145550199' && /Thursday/.test(item.body)), true);
  assert.equal(reply.record.estimate_update.status, 'replied');

  const won = engine.handleInboundSms(engine.smsContext(next, tenant, {
    now: DAY,
    from: tenant.owner_phone,
    body: 'WON 1',
  }));
  assert.equal(won.record.estimate_update.status, 'won');

  const missing = engine.handleInboundSms(engine.smsContext(next, tenant, {
    now: DAY,
    from: tenant.owner_phone,
    body: 'LOST 99',
  }));
  assert.match(missing.twiml, /No estimate 99/);
});

test('customer reply stops an open estimate sequence', () => {
  const tenant = engine.exampleTenant();
  let state = engine.freshState();
  state.estimates.push({ id: 7, status: 'open', phone: caller(), customer_name: 'Jane', job: 'furnace', amount_cents: 100 });
  state.conversation = { state: 'handed_off', need: 'furnace', location: '53211', urgency: 'today' };
  const decision = engine.handleInboundSms(engine.smsContext(state, tenant, { now: DAY, from: caller(), body: 'YES' }));
  assert.equal(decision.record.estimate_update.status, 'replied');
  assert.match(decision.record.owner_notification.body, /YES/);
});

test('quiet hours defer follow-ups and can defer the missed-call text', () => {
  const tenant = engine.exampleTenant({ missed_call_respects_quiet_hours: true });
  const decision = engine.handleVoice(engine.voiceContext(engine.freshState(), tenant, {
    now: NIGHT,
    from: caller(),
    callSid: 'CA7',
  }));
  assert.equal(decision.record.text_sent, false);
  assert.equal(decision.record.suppressed_reason, 'quiet_hours_deferred');
  const queued = decision.record.outbound.find((item) => item.delivery === 'queue');
  assert.equal(queued.send_at, '2026-10-03T13:00:00.000Z');

  const row = {
    estimate_id: 1,
    tenant_id: tenant.id,
    customer_name: 'Jane',
    phone: caller(),
    job: 'furnace',
    amount_cents: 450000,
    step_index: 0,
    created_at: DAY,
    timezone: tenant.timezone,
    quiet_start: '21:00',
    quiet_end: '08:00',
    daily_sms_limit: 200,
    per_number_daily_limit: 12,
    followup_templates: tenant.followup_templates,
    business_name: tenant.business_name,
    booking_link: tenant.booking_link,
    hours_text: tenant.hours_text,
    twilio_number: tenant.twilio_number,
    sent_today: 0,
    sent_to_today: 0,
    suppressed: false,
  };
  const deferred = engine.planOneFollowup(row, NIGHT);
  assert.equal(deferred.send, false);
  assert.equal(deferred.mark.status, 'deferred');
  const sent = engine.planOneFollowup(row, DAY);
  assert.equal(sent.send, true);
  assert.match(sent.body, /\$4,500\.00/);
  assert.match(sent.body, /STOP/);
  assert.equal(sent.mark.step_index, 1);
  assert.equal(sent.mark.next_send_at, '2026-10-05T15:00:00.000Z');
});

test('rate limit blocks the next customer text and a suppressed follow-up stops', () => {
  const tenant = engine.exampleTenant({ daily_sms_limit: 1, per_number_daily_limit: 1 });
  const first = engine.handleVoice(engine.voiceContext(engine.freshState(), tenant, {
    now: DAY, from: caller(), callSid: 'CA8',
  }));
  assert.equal(first.record.text_sent, true);
  assert.equal(first.record.outbound.some((item) => item.to === tenant.owner_phone), false);

  const row = {
    estimate_id: 3,
    tenant_id: tenant.id,
    customer_name: 'Jane',
    phone: caller(),
    job: 'coil',
    amount_cents: 100,
    step_index: 0,
    created_at: DAY,
    timezone: 'America/Chicago',
    quiet_start: '21:00',
    quiet_end: '08:00',
    daily_sms_limit: 1,
    per_number_daily_limit: 12,
    followup_templates: tenant.followup_templates,
    business_name: tenant.business_name,
    booking_link: '',
    hours_text: '',
    twilio_number: tenant.twilio_number,
    sent_today: 1,
    sent_to_today: 0,
    suppressed: false,
  };
  assert.equal(engine.planOneFollowup(row, DAY).mark.status, 'rate_limited');
  assert.equal(engine.planOneFollowup(Object.assign({}, row, { suppressed: true, sent_today: 0 }), DAY).mark.estimate_status, 'opted_out');
});

test('follow-up preview is same day, +2 days, and +5 days', () => {
  const tenant = engine.exampleTenant();
  const preview = engine.previewFollowupSequence(tenant, {
    customer_name: 'Jane Doe',
    phone: caller(),
    job: 'furnace',
    amount_cents: 450000,
  }, DAY);
  assert.deepEqual(preview.map((step) => step.label), ['Same day', '+2 days', '+5 days']);
  assert.equal(preview[1].at, '2026-10-05T15:00:00.000Z');
  assert.equal(preview[2].at, '2026-10-08T15:00:00.000Z');
  assert.match(preview[2].body, /Last note/);
});

test('dashboard escapes tenant content and hides itself from a bad token', () => {
  const html = engine.renderDashboard({
    tenant: engine.exampleTenant({ business_name: '<script>alert(1)</script>' }),
    stats: { missed_calls_7d: 2, won_cents_30d: 450000, jobs_won_30d: 1 },
    calls: [],
    conversations: [],
    estimates: [],
    notifications: [],
  }, 'tok');
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /\$4,500\.00/);
  assert.match(engine.renderDashboard(null, 'nope'), /not valid/);
});

test('business names with ampersands stay valid TwiML', () => {
  const tenant = engine.exampleTenant({ business_name: 'Pike & Sons' });
  const decision = engine.handleVoice(engine.voiceContext(engine.freshState(), tenant, {
    now: DAY, from: caller(), callSid: 'CA9',
  }));
  assert.match(decision.twiml, /Pike &amp; Sons/);
  assert.doesNotMatch(decision.twiml, /Pike & Sons/);
});

test('manual sends to opted-out numbers are refused', () => {
  const tenant = engine.exampleTenant();
  const state = engine.freshState();
  state.suppressedPhones = [caller()];
  const reply = engine.handleInboundSms(engine.smsContext(state, tenant, {
    now: DAY,
    from: tenant.owner_phone,
    body: 'REPLY ' + caller() + ' We can be there Thursday',
  }));
  assert.equal(reply.record.outbound.some((item) => item.to === caller()), false);
  assert.equal(reply.record.estimate_update, null);
  assert.match(reply.twiml, /opted out/);
  assert.match(reply.twiml, /Do not text them/);
  assert.match(reply.twiml, /<Message to="\+14145550199">/);

  const pack = {
    tenant: tenant,
    estimates: [{ id: 3, status: 'open', phone: caller(), customer_name: 'Jane', job: 'furnace' }],
    sent_today: 0,
  };
  const fields = {
    now: DAY,
    token: 'token',
    action: 'reply',
    phone: caller(),
    message: 'Thursday works',
  };
  const allowed = engine.handleAction(engine.contextFromLoad(Object.assign({ suppressed_phones: [] }, pack), fields), { TWILIO_MODE: 'mock' });
  assert.equal(allowed.save, 'yes');
  assert.equal(allowed.record.outbound.some((item) => item.to === caller() && item.purpose === 'owner_reply'), true);

  const refused = engine.handleAction(engine.contextFromLoad(Object.assign({ suppressed_phones: [caller()] }, pack), fields), { TWILIO_MODE: 'mock' });
  assert.equal(refused.save, 'no');
  assert.equal(refused.record, null);
  assert.match(refused.html, /opted out/);
  assert.match(refused.html, /Do not text them/);
});

test('owner alerts are not cut off by the customer per-number cap', () => {
  const tenant = engine.exampleTenant({ per_number_daily_limit: 1 });
  const state = engine.freshState();
  state.sentToday = 12;
  state.sentToNumber[tenant.owner_phone] = 12;
  const decision = engine.handleVoice(engine.voiceContext(state, tenant, {
    now: DAY,
    from: caller(),
    callSid: 'CA-owner-cap',
    publicBaseUrl: 'https://text.example',
  }));
  assert.equal(decision.record.outbound.some((item) => item.to === tenant.owner_phone && item.purpose === 'owner_notify'), true);
  assert.equal(decision.record.outbound.some((item) => item.purpose === 'owner_cap_notice'), false);
});

test('owner cap sends one pause text and then stays quiet', () => {
  const tenant = engine.exampleTenant({
    owner_daily_sms_limit: 2,
    dashboard_path: '/webhook/mctb-dashboard?token=abc',
  });
  let state = engine.freshState();
  function ring(sid, from) {
    const decision = engine.handleVoice(engine.voiceContext(state, tenant, {
      now: DAY,
      from: from,
      callSid: sid,
      publicBaseUrl: 'https://text.example',
    }));
    state = engine.project(state, decision.record);
    return decision;
  }
  assert.equal(ring('CA-p1', '+14145550141').record.outbound.filter((item) => item.purpose === 'owner_notify').length, 1);
  assert.equal(ring('CA-p2', '+14145550142').record.outbound.filter((item) => item.purpose === 'owner_notify').length, 1);
  const paused = ring('CA-p3', '+14145550143');
  assert.equal(paused.record.outbound.some((item) => item.purpose === 'owner_notify'), false);
  const notice = paused.record.outbound.find((item) => item.purpose === 'owner_cap_notice');
  assert.ok(notice);
  assert.equal(notice.to, tenant.owner_phone);
  assert.equal(notice.body, 'Alerts paused for today, see your dashboard: https://text.example/webhook/mctb-dashboard?token=abc');
  assert.equal(paused.record.owner_notification.channel, 'dashboard');
  assert.match(paused.record.owner_notification.body, /Missed call/);
  const quiet = ring('CA-p4', '+14145550144');
  assert.equal(quiet.record.outbound.some((item) => item.to === tenant.owner_phone), false);
  assert.equal(quiet.record.owner_notification.channel, 'dashboard');
});

test('Chicago winter and summer offsets', () => {
  const winter = engine.zonedTimeToUtc(2026, 1, 15, 10, 0, 'America/Chicago');
  const summer = engine.zonedTimeToUtc(2026, 7, 15, 10, 0, 'America/Chicago');
  assert.equal(winter.toISOString(), '2026-01-15T16:00:00.000Z');
  assert.equal(summer.toISOString(), '2026-07-15T15:00:00.000Z');
});
