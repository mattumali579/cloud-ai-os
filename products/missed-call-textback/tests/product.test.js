'use strict';

const assert = require('node:assert/strict');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const test = require('node:test');
const { psql, psqlJson, resetDatabase, quoteJson, runWorkflow } = require('./sim');

const root = path.join(__dirname, '..');
const engineSource = fs.readFileSync(path.join(root, 'logic', 'engine.js'), 'utf8');
const workflows = {
  voice: requireJson('mctb_voice.json'),
  dial: requireJson('mctb_dial_status.json'),
  sms: requireJson('mctb_inbound_sms.json'),
  lead: requireJson('mctb_web_lead.json'),
  followups: requireJson('mctb_followups.json'),
  dashboard: requireJson('mctb_dashboard.json'),
  actions: requireJson('mctb_actions.json'),
};

function requireJson(name) {
  return JSON.parse(fs.readFileSync(path.join(root, 'workflows', name), 'utf8'));
}

const env = {
  TWILIO_MODE: 'mock',
  TWILIO_ACCOUNT_SID: '',
  TWILIO_AUTH_TOKEN: '',
  MCTB_PUBLIC_BASE_URL: 'https://text.example',
  OWNER_ALERT_WEBHOOK_URL: 'https://alerts.example/hook',
};

function envelope(body, query) {
  return { headers: { 'content-type': 'application/x-www-form-urlencoded' }, query: query || {}, body: body };
}

function one(sql) {
  const value = psqlJson(sql);
  return value;
}

test('workflow files embed the engine and do not carry live credentials', () => {
  const sha = crypto.createHash('sha256').update(engineSource).digest('hex');
  const demo = fs.readFileSync(path.join(root, 'demo', 'engine.js'), 'utf8');
  assert.equal(demo, engineSource);
  const paths = [];
  Object.entries(workflows).forEach(([key, workflow]) => {
    assert.equal(workflow.meta.engineSha256, sha, key);
    assert.equal(workflow.active, false, key);
    const blob = JSON.stringify(workflow);
    assert.equal(blob.includes('AC' + 'xxxxxxxx'), false);
    assert.match(blob, /BrightReach Postgres/);
    assert.doesNotMatch(blob, /TWILIO_AUTH_TOKEN"\s*:\s*"[^"]+/);
    workflow.nodes.forEach((node) => {
      if (!node.parameters || !node.parameters.jsCode) return;
      const start = node.parameters.jsCode.indexOf('/*MCTB_ENGINE_START*/');
      const end = node.parameters.jsCode.indexOf('/*MCTB_ENGINE_END*/');
      assert.ok(start > 0 && end > start, node.name);
      const embedded = node.parameters.jsCode.slice(start + '/*MCTB_ENGINE_START*/'.length, end).trim();
      assert.equal(embedded, engineSource.trim(), node.name);
    });
    workflow.nodes.forEach((node) => {
      if (node.type === 'n8n-nodes-base.webhook') paths.push(node.parameters.path);
    });
  });
  assert.deepEqual(paths.sort(), [
    'mctb-action',
    'mctb-dashboard',
    'mctb-dial-status',
    'mctb-followups-run',
    'mctb-lead',
    'mctb-sms',
    'mctb-voice',
  ]);
  const follow = workflows.followups;
  assert.ok(follow.nodes.some((node) => node.name === 'Every Minute'));
  assert.ok(follow.connections['Every Minute'].main[0][0].node === 'Capture Now');
  const demoHtml = fs.readFileSync(path.join(root, 'demo', 'index.html'), 'utf8');
  assert.match(demoHtml, /src="\.\/engine\.js"/);
  assert.match(demoHtml, /Miss the call/);
  assert.match(demoHtml, /STOP/);
  const demoJs = fs.readFileSync(path.join(root, 'demo', 'demo.js'), 'utf8');
  assert.match(demoJs, /handleVoice/);
  assert.match(demoJs, /previewFollowupSequence/);
  assert.match(demoJs, /handleInboundSms/);
});

test('simulated Twilio webhooks recover a missed call, qualify it, and follow an estimate', () => {
  resetDatabase(path.join(root, 'sql', '001_schema.sql'));
  const northline = psql('mctb_test', `
    INSERT INTO mctb.tenants
      (slug, business_name, twilio_number, owner_name, owner_phone, owner_email, hours_text, booking_link, call_mode, dashboard_token)
    VALUES
      ('northline', 'Northline Heating & Air', '+14145550100', 'Dana', '+14145550199', 'dana@northline.example',
       'Mon-Sat 7am-7pm', 'https://northline.example/book', 'forward', 'token-northline')
    RETURNING id
  `).trim();
  const pike = psql('mctb_test', `
    INSERT INTO mctb.tenants
      (slug, business_name, twilio_number, owner_name, owner_phone, call_mode, dashboard_token, ring_timeout_seconds)
    VALUES
      ('pike', 'Pike & Sons Plumbing', '+14145550200', 'Luis', '+14145550299', 'dial', 'token-pike', 18)
    RETURNING id
  `).trim();
  assert.notEqual(northline, pike);

  const transcript = [];
  function say(line) {
    transcript.push(line);
  }

  const missed = runWorkflow(workflows.voice, 'Voice Webhook', envelope({
    From: '+14145550123',
    To: '+14145550100',
    CallSid: 'CA100',
    CallStatus: 'no-answer',
  }), env);
  const twiml = missed.responses[0].body;
  assert.match(twiml, /Northline Heating &amp; Air/);
  assert.match(twiml, /Reply STOP to opt out/);
  assert.match(twiml, /<Message to="\+14145550199">/);
  assert.equal(missed.httpLog.length, 1);
  assert.match(missed.httpLog[0].body, /Missed call/);
  say('CALL unanswered from +14145550123');
  say('TWIML to caller: missed-call text including STOP');
  say('TWIML to owner Dana: missed-call alert');
  say('WEBHOOK owner alert: ' + missed.httpLog[0].body);

  const replay = runWorkflow(workflows.voice, 'Voice Webhook', envelope({
    From: '+14145550123',
    To: '+14145550100',
    CallSid: 'CA100',
    CallStatus: 'no-answer',
  }), env);
  assert.match(replay.responses[0].body, /Thanks for calling/);
  assert.equal(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'missed_call'))").n, 1);
  assert.equal(Number(one("SELECT jsonb_build_object('n', count(*)) FROM mctb.outbound_queue WHERE purpose LIKE 'lead_followup%' AND status = 'pending'").n), 2);

  let messageSequence = 0;
  function sms(from, body, fixedSid) {
    messageSequence += 1;
    const sid = fixedSid || 'SM' + String(messageSequence).padStart(8, '0');
    return runWorkflow(workflows.sms, 'SMS Webhook', envelope({
      From: from,
      To: '+14145550100',
      Body: body,
      MessageSid: sid,
    }), env);
  }

  const need = sms('+14145550123', 'Furnace will not start', 'SM-NEED-1');
  assert.match(need.responses[0].body, /what name/i);
  say('CUSTOMER: Furnace will not start');
  say('BUSINESS: asks for customer name');
  const duplicateNeed = sms('+14145550123', 'Furnace will not start', 'SM-NEED-1');
  assert.equal(duplicateNeed.responses[0].body, '<?xml version="1.0" encoding="UTF-8"?><Response></Response>');
  assert.equal(Number(one("SELECT jsonb_build_object('n', count(*)) FROM mctb.messages WHERE twilio_sid = 'SM-NEED-1'").n), 1);
  assert.equal(Number(one("SELECT jsonb_build_object('n', count(*)) FROM mctb.outbound_queue WHERE purpose LIKE 'lead_followup%' AND status = 'cancelled'").n), 2);

  const name = sms('+14145550123', 'Jane Homeowner');
  assert.match(name.responses[0].body, /address or ZIP/);
  say('CUSTOMER: Jane Homeowner');

  const place = sms('+14145550123', '53211');
  assert.match(place.responses[0].body, /TODAY, THIS WEEK, or FLEXIBLE/);
  say('CUSTOMER: 53211');

  const urgent = sms('+14145550123', 'today please');
  assert.match(urgent.responses[0].body, /preferred day or arrival window/i);
  assert.match(urgent.responses[0].body, /to="\+14145550199"/);
  assert.match(urgent.httpLog[0].body, /53211/);
  say('CUSTOMER: today please');
  say('CRM: lead marked qualified');
  say('OWNER ALERT: ' + urgent.httpLog[0].body);

  const booking = sms('+14145550123', 'Tomorrow 8-10am');
  assert.match(booking.responses[0].body, /appointment request/i);
  assert.match(booking.responses[0].body, /to="\+14145550199"/);
  const appointment = one("SELECT jsonb_build_object('status', status, 'window', requested_window) FROM mctb.appointments WHERE phone = '+14145550123' ORDER BY id DESC LIMIT 1");
  assert.equal(appointment.status, 'requested');
  assert.equal(appointment.window, 'Tomorrow 8-10am');
  say('BOOKING REQUESTED: Tomorrow 8-10am');

  const casual = sms('+14145550123', 'stop by tomorrow morning if you can');
  assert.equal(casual.responses[0].body.includes('unsubscribed'), false);
  say('CUSTOMER: stop by tomorrow morning if you can (not an opt-out)');

  const help = sms('+14145550123', 'HELP');
  assert.match(help.responses[0].body, /Reply STOP to unsubscribe/);

  const board = runWorkflow(workflows.dashboard, 'Dashboard Webhook', envelope({}, { token: 'token-northline' }), env);
  assert.match(board.responses[0].body, /Recovered replies/);
  assert.match(board.responses[0].body, /Furnace will not start/);
  assert.match(board.responses[0].body, /53211/);
  const other = runWorkflow(workflows.dashboard, 'Dashboard Webhook', envelope({}, { token: 'token-pike' }), env);
  assert.doesNotMatch(other.responses[0].body, /Furnace/);
  assert.match(other.responses[0].body, /Pike &amp; Sons Plumbing/);
  const bogus = runWorkflow(workflows.dashboard, 'Dashboard Webhook', envelope({}, { token: 'nope' }), env);
  assert.match(bogus.responses[0].body, /not valid/);

  const stats = one('SELECT mctb.dashboard_snapshot(' + quoteJson('token-northline') + ')');
  assert.ok(Number(stats.stats.missed_calls_7d) >= 1);
  assert.ok(Number(stats.stats.recovered_7d) >= 1);
  assert.ok(Number(stats.stats.leads_30d) >= 1);
  assert.ok(Number(stats.stats.booked_30d) >= 1);
  say('CRM UPDATED: missed=' + stats.stats.missed_calls_7d + ' recovered=' + stats.stats.recovered_7d + ' qualified=' + stats.stats.leads_30d + ' booked=' + stats.stats.booked_30d);

  const webLead = runWorkflow(workflows.lead, 'Lead Webhook', envelope({
    token: one("SELECT jsonb_build_object('token', lead_token) FROM mctb.tenants WHERE slug = 'northline'").token,
    external_id: 'website-100',
    name: 'Morgan Website',
    phone: '4145550155',
    email: 'morgan@example.com',
    service: 'AC replacement',
    zip: '53202',
    urgency: 'this week',
    requested_window: 'Friday afternoon',
  }), env);
  assert.deepEqual(JSON.parse(webLead.responses[0].body), { ok: true, qualified: true, booking_status: 'requested' });
  const webAppointment = one("SELECT jsonb_build_object('source', source, 'window', requested_window) FROM mctb.appointments WHERE phone = '+14145550155'");
  assert.equal(webAppointment.source, 'web_form');
  assert.equal(webAppointment.window, 'Friday afternoon');
  const webReplay = runWorkflow(workflows.lead, 'Lead Webhook', envelope({
    token: one("SELECT jsonb_build_object('token', lead_token) FROM mctb.tenants WHERE slug = 'northline'").token,
    external_id: 'website-100',
    name: 'Morgan Website',
    phone: '4145550155',
    service: 'AC replacement',
    zip: '53202',
  }), env);
  assert.equal(JSON.parse(webReplay.responses[0].body).duplicate, true);
  assert.equal(Number(one("SELECT jsonb_build_object('n', count(*)) FROM mctb.web_lead_events WHERE external_id = 'website-100'").n), 1);
  const badWebLead = runWorkflow(workflows.lead, 'Lead Webhook', envelope({
    token: one("SELECT jsonb_build_object('token', lead_token) FROM mctb.tenants WHERE slug = 'northline'").token,
    external_id: 'website-bad',
    name: 'No Phone',
    service: 'AC replacement',
    zip: '53202',
  }), env);
  assert.equal(JSON.parse(badWebLead.responses[0].body).error, 'name_phone_job_location_required');
  assert.equal(Number(one("SELECT jsonb_build_object('n', count(*)) FROM mctb.web_lead_events WHERE external_id = 'website-bad'").n), 0);
  say('WEBSITE FORM: immediate response, qualified, booking requested, contractor notified, CRM updated');

  const retryQueue = one(`
    WITH inserted AS (
      INSERT INTO mctb.outbound_queue (tenant_id, to_number, from_number, body, purpose, send_at)
      VALUES ('${northline}', '+14145550111', '+14145550100', 'retry me', 'retry_test', now())
      RETURNING id, tenant_id
    )
    SELECT jsonb_build_object('id', id, 'tenant', tenant_id)
    FROM inserted
  `);
  for (let attempt = 0; attempt < 5; attempt += 1) {
    psql('mctb_test', 'SELECT mctb.mark_outbound(' + quoteJson({
      kind: 'queue',
      queue_id: retryQueue.id,
      tenant_id: retryQueue.tenant,
      status: 'retry',
      next_send_at: '2026-10-03T16:00:00.000Z',
      error: 'simulated provider failure',
    }) + '::jsonb)');
  }
  const failedQueue = one("SELECT jsonb_build_object('status', status, 'attempts', attempts, 'error', last_error) FROM mctb.outbound_queue WHERE purpose = 'retry_test'");
  assert.equal(failedQueue.status, 'failed');
  assert.equal(Number(failedQueue.attempts), 5);
  assert.match(failedQueue.error, /simulated provider failure/);

  const estimate = runWorkflow(workflows.actions, 'Action Webhook', envelope({
    token: 'token-northline',
    action: 'estimate',
    customer_name: 'Jane Doe',
    phone: '4145550199',
    job: 'furnace replacement',
    amount: '4500',
    now: '2026-10-03T15:00:00.000Z',
  }), env);
  assert.match(estimate.responses[0].body, /Estimate saved for Jane Doe/);
  psql('mctb_test', "UPDATE mctb.estimates SET next_send_at = now() - interval '1 minute' WHERE customer_name = 'Jane Doe'");

  const night = runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({
    now: '2026-10-03T02:30:00.000Z',
  }), env);
  assert.equal(night.httpLog.length, 0);
  const deferred = one("SELECT jsonb_build_object('status', status, 'step', step_index) FROM mctb.estimates WHERE customer_name = 'Jane Doe'");
  assert.equal(deferred.status, 'open');
  assert.equal(Number(deferred.step), 0);
  say('FOLLOW-UP at 21:30 local: deferred until morning');

  psql('mctb_test', "UPDATE mctb.estimates SET next_send_at = now() - interval '1 minute' WHERE customer_name = 'Jane Doe'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: '2026-10-03T15:00:00.000Z' }), env);
  const stepOne = one("SELECT jsonb_build_object('status', status, 'step', step_index, 'body', (SELECT body FROM mctb.outbound_log WHERE purpose = 'followup' ORDER BY id DESC LIMIT 1)) FROM mctb.estimates WHERE customer_name = 'Jane Doe'");
  assert.equal(Number(stepOne.step), 1);
  assert.match(stepOne.body, /\$4,500\.00/);
  assert.match(stepOne.body, /STOP/);
  say('FOLLOW-UP 1 (same day): ' + stepOne.body);

  psql('mctb_test', "UPDATE mctb.estimates SET next_send_at = now() - interval '1 minute' WHERE customer_name = 'Jane Doe'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: '2026-10-05T15:00:00.000Z' }), env);
  psql('mctb_test', "UPDATE mctb.estimates SET next_send_at = now() - interval '1 minute' WHERE status = 'open' AND customer_name = 'Jane Doe'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: '2026-10-08T15:00:00.000Z' }), env);
  const done = one("SELECT jsonb_build_object('status', status, 'step', step_index) FROM mctb.estimates WHERE customer_name = 'Jane Doe'");
  assert.equal(done.status, 'completed');
  assert.equal(Number(done.step), 3);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'followup'))").n), 3);
  say('FOLLOW-UP 2 and 3 sent. Sequence completed.');

  const replyStopped = runWorkflow(workflows.actions, 'Action Webhook', envelope({
    token: 'token-northline',
    action: 'estimate',
    customer_name: 'Chris Pines',
    phone: '4145550177',
    job: 'ac tune-up',
    amount: '189',
    now: '2026-10-03T15:00:00.000Z',
  }), env);
  assert.match(replyStopped.responses[0].body, /Chris Pines/);
  const customerReply = sms('+14145550177', 'Can you do Friday?');
  assert.match(customerReply.responses[0].body, /what name/i);
  psql('mctb_test', "UPDATE mctb.estimates SET next_send_at = now() - interval '1 minute' WHERE customer_name = 'Chris Pines'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: '2026-10-03T15:00:00.000Z' }), env);
  const paused = one("SELECT jsonb_build_object('status', status) FROM mctb.estimates WHERE customer_name = 'Chris Pines'");
  assert.equal(paused.status, 'replied');
  say('Chris replied before the follow-up, so the sequence stopped.');

  const owner = sms('+14145550199', 'ESTIMATE Rita Cole | 4145550166 | water heater | 2100');
  assert.match(owner.responses[0].body, /Rita Cole/);
  const won = runWorkflow(workflows.actions, 'Action Webhook', envelope({
    token: 'token-northline',
    action: 'won',
    estimate_id: String(one("SELECT jsonb_build_object('id', id) FROM mctb.estimates WHERE customer_name = 'Rita Cole'").id),
  }), env);
  assert.match(won.responses[0].body, /marked won/);
  const money = one('SELECT mctb.dashboard_snapshot(' + quoteJson('token-northline') + ')');
  assert.ok(Number(money.stats.jobs_won_30d) >= 1);
  assert.ok(Number(money.stats.won_cents_30d) >= 210000);
  say('WON Rita Cole water heater. Dashboard won amount includes $2,100.00.');

  const stopped = sms('+14145550123', 'STOP');
  assert.match(stopped.responses[0].body, /unsubscribed/);
  const afterStop = runWorkflow(workflows.voice, 'Voice Webhook', envelope({
    From: '+14145550123',
    To: '+14145550100',
    CallSid: 'CA200',
    CallStatus: 'no-answer',
  }), env);
  assert.doesNotMatch(afterStop.responses[0].body, /Sorry we missed your call/);
  assert.match(afterStop.httpLog[0].body, /opted out/);
  say('STOP received. Later missed call was logged and not texted.');

  const dial = runWorkflow(workflows.voice, 'Voice Webhook', envelope({
    From: '+14145550333',
    To: '+14145550200',
    CallSid: 'CA300',
    CallStatus: 'ringing',
  }), env);
  assert.match(dial.responses[0].body, /<Dial timeout="18"/);
  assert.match(dial.responses[0].body, /https:\/\/text\.example\/webhook\/mctb-dial-status/);
  assert.doesNotMatch(dial.responses[0].body, /<Message/);
  const missedDial = runWorkflow(workflows.dial, 'Dial Webhook', envelope({
    From: '+14145550333',
    To: '+14145550200',
    CallSid: 'CA300',
    DialCallStatus: 'no-answer',
  }), env);
  assert.match(missedDial.responses[0].body, /Pike &amp; Sons Plumbing/);
  const answered = runWorkflow(workflows.dial, 'Dial Webhook', envelope({
    From: '+14145550444',
    To: '+14145550200',
    CallSid: 'CA301',
    DialCallStatus: 'completed',
  }), env);
  assert.doesNotMatch(answered.responses[0].body, /<Message/);
  const pikeBoard = one('SELECT mctb.dashboard_snapshot(' + quoteJson('token-pike') + ')');
  assert.equal(Number(pikeBoard.stats.missed_calls_7d), 1);
  assert.equal(pikeBoard.calls.some((call) => call.from_number === '+14145550123'), false);
  say('Pike dial mode: no-answer texted, answered call was not texted, and Northline leads stayed off Pike\'s board.');

  const unknown = runWorkflow(workflows.voice, 'Voice Webhook', envelope({
    From: '+14145550999',
    To: '+19995550000',
    CallSid: 'CA404',
  }), env);
  assert.equal(unknown.responses[0].body, '<?xml version="1.0" encoding="UTF-8"?><Response></Response>');

  const setupOut = execFileSync('sudo', [
    '-n', '-u', 'postgres', 'env',
    'DATABASE_URL=postgresql:///mctb_test',
    'PUBLIC_BASE_URL=https://text.example',
    path.join(root, 'scripts/setup_tenant.sh'),
    '--slug', 'harbor',
    '--business-name', 'Harbor Electric',
    '--twilio-number', '4145550188',
    '--owner-name', 'Sam',
    '--owner-phone', '4145550180',
    '--call-mode', 'dial',
  ], { encoding: 'utf8' });
  assert.match(setupOut, /Dashboard:\s+https:\/\/text\.example\/webhook\/mctb-dashboard\?token=/);
  const harbor = one("SELECT jsonb_build_object('mode', call_mode, 'num', twilio_number) FROM mctb.tenants WHERE slug = 'harbor'");
  assert.equal(harbor.mode, 'dial');
  assert.equal(harbor.num, '+14145550188');
  say('setup_tenant.sh added Harbor Electric on its own number.');

  const artifactDir = '/opt/cursor/artifacts';
  fs.mkdirSync(artifactDir, { recursive: true });
  const text = transcript.join('\n') + '\n';
  fs.writeFileSync(path.join(artifactDir, 'missed-call-e2e.txt'), text);
  assert.ok(text.includes('FOLLOW-UP 1'));
  assert.ok(text.includes('CALL unanswered'));
  assert.ok(text.includes('BOOKING REQUESTED'));
  assert.ok(text.includes('CRM UPDATED'));
});
