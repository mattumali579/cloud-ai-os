'use strict';

const assert = require('node:assert/strict');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const test = require('node:test');
const { psql, psqlJson, resetDatabase, quoteJson, runWorkflow, columnKey, postgresItem } = require('./sim');

const root = path.join(__dirname, '..');
const engineSource = fs.readFileSync(path.join(root, 'logic', 'engine.js'), 'utf8');
const workflows = {
  voice: requireJson('mctb_voice.json'),
  dial: requireJson('mctb_dial_status.json'),
  sms: requireJson('mctb_inbound_sms.json'),
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

function codeTail(node) {
  const code = (node.parameters && node.parameters.jsCode) || '';
  const marker = '/*MCTB_ENGINE_END*/';
  const at = code.lastIndexOf(marker);
  return at >= 0 ? code.slice(at + marker.length) : code;
}

function inputKeysRead(workflow, startName) {
  const nodes = {};
  workflow.nodes.forEach((node) => {
    nodes[node.name] = node;
  });
  const keys = [];
  const seen = new Set();
  function walk(name) {
    if (seen.has(name)) return;
    seen.add(name);
    const node = nodes[name];
    if (!node) return;
    if (node.type === 'n8n-nodes-base.code') {
      const re = /\$input\.first\(\)\.json\.([A-Za-z_][A-Za-z0-9_]*)/g;
      const tail = codeTail(node);
      let match = re.exec(tail);
      while (match) {
        keys.push(match[1]);
        match = re.exec(tail);
      }
      return;
    }
    const branches = (workflow.connections[name] && workflow.connections[name].main) || [];
    branches.forEach((edges) => {
      (edges || []).forEach((edge) => walk(edge.node));
    });
  }
  const branches = (workflow.connections[startName] && workflow.connections[startName].main) || [];
  branches.forEach((edges) => {
    (edges || []).forEach((edge) => walk(edge.node));
  });
  return keys;
}

test('postgres queries use the column alias real n8n returns', () => {
  const bare = postgresItem("SELECT jsonb_build_object('ctx', '{}'::jsonb)", '{"ctx":{"tenant":null}}');
  assert.deepEqual(Object.keys(bare.json), ['jsonb_build_object']);
  assert.equal(bare.json.ctx, undefined);
  assert.equal(columnKey('SELECT mctb.due_work()'), 'due_work');
  const aliased = postgresItem('SELECT mctb.load_voice_context($1::text, $2::text, $3::text) AS "ctx"', '{"tenant":{"business_name":"Northline"}}');
  assert.deepEqual(Object.keys(aliased.json), ['ctx']);
  assert.equal(aliased.json.ctx.tenant.business_name, 'Northline');
  assert.equal(postgresItem('SELECT mctb.dashboard_snapshot($1::text) AS "snap"', '').json.snap, null);

  let count = 0;
  Object.entries(workflows).forEach(([name, workflow]) => {
    workflow.nodes.forEach((node) => {
      if (node.type !== 'n8n-nodes-base.postgres') return;
      count += 1;
      const query = node.parameters.query.trim();
      const alias = query.match(/\bAS\s+"([^"]+)"\s*$/);
      assert.ok(alias, name + ' / ' + node.name + ' has no AS "alias": ' + query);
      assert.equal(columnKey(query), alias[1], node.name);
      assert.doesNotMatch(query, /jsonb_build_object\s*\(/);
      inputKeysRead(workflow, node.name).forEach((key) => {
        assert.equal(alias[1], key, name + ' / ' + node.name + ' alias ' + alias[1] + ' but the next code reads ' + key);
      });
    });
  });
  assert.equal(count, 11);
  const helper = fs.readFileSync(path.join(root, 'scripts/setup_tenant.ps1'), 'utf8');
  assert.match(helper, /docker exec/);
  assert.match(helper, /mctb\.tenants/);
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
  const loaded = missed.outputs['Load Voice Context'][0].json;
  assert.deepEqual(Object.keys(loaded), ['ctx']);
  assert.equal(loaded.ctx.tenant.business_name, 'Northline Heating & Air');
  const applied = missed.outputs['Apply Voice'][0].json;
  assert.equal(applied.ok, undefined);
  assert.equal(applied.result.ok, true);
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

  function sms(from, body) {
    return runWorkflow(workflows.sms, 'SMS Webhook', envelope({
      From: from,
      To: '+14145550100',
      Body: body,
      MessageSid: 'SM' + body.length + from.slice(-4),
    }), env);
  }

  const need = sms('+14145550123', 'Furnace will not start');
  assert.match(need.responses[0].body, /address or ZIP/);
  say('CUSTOMER: Furnace will not start');
  say('BUSINESS: asks for address or ZIP');

  const place = sms('+14145550123', '53211');
  assert.match(place.responses[0].body, /TODAY, THIS WEEK, or FLEXIBLE/);
  say('CUSTOMER: 53211');

  const urgent = sms('+14145550123', 'today please');
  assert.match(urgent.responses[0].body, /Dana/);
  assert.match(urgent.responses[0].body, /to="\+14145550199"/);
  assert.match(urgent.httpLog[0].body, /53211/);
  say('CUSTOMER: today please');
  say('BUSINESS: handed off to Dana');
  say('OWNER ALERT: ' + urgent.httpLog[0].body);

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
  say('DASHBOARD missed=' + stats.stats.missed_calls_7d + ' recovered=' + stats.stats.recovered_7d + ' leads=' + stats.stats.leads_30d);

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
  const due = night.outputs['Load Due Work'][0].json;
  assert.ok(Array.isArray(due.work.followups));
  assert.equal(due.jsonb_build_object, undefined);
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
  assert.match(customerReply.responses[0].body, /address or ZIP/);
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

  const stillLead = one('SELECT mctb.dashboard_snapshot(' + quoteJson('token-northline') + ')');
  assert.ok(Number(stillLead.stats.leads_30d) >= 1);
  const opted = one("SELECT jsonb_build_object('state', state, 'qualified', qualified_at IS NOT NULL) FROM mctb.conversations WHERE phone = '+14145550123'");
  assert.equal(opted.state, 'opted_out');
  assert.equal(opted.qualified, true);
  say('Qualified lead still counts after STOP. State is opted_out.');

  const dashReply = runWorkflow(workflows.actions, 'Action Webhook', envelope({
    token: 'token-northline',
    action: 'reply',
    phone: '+14145550123',
    message: 'We can still come by',
  }), env);
  assert.match(dashReply.responses[0].body, /opted out/);
  assert.match(dashReply.responses[0].body, /Do not text them/);
  const ownerReply = sms('+14145550199', 'REPLY +14145550123 Still on the way');
  assert.match(ownerReply.responses[0].body, /opted out/);
  assert.doesNotMatch(ownerReply.responses[0].body, /<Message to="\+14145550123">/);
  const leaked = one("SELECT jsonb_build_object('log', (SELECT count(*) FROM mctb.outbound_log WHERE to_number = '+14145550123' AND purpose = 'owner_reply'), 'queued', (SELECT count(*) FROM mctb.outbound_queue WHERE to_number = '+14145550123'), 'msgs', (SELECT count(*) FROM mctb.messages WHERE to_number = '+14145550123' AND purpose = 'owner_reply'))");
  assert.equal(Number(leaked.log), 0);
  assert.equal(Number(leaked.queued), 0);
  assert.equal(Number(leaked.msgs), 0);
  say('Dashboard text and owner REPLY to the opted-out number were refused and not logged as sent.');

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

  psql('mctb_test', "UPDATE mctb.tenants SET owner_daily_sms_limit = 1 WHERE slug = 'northline'");
  const pauseCall = runWorkflow(workflows.voice, 'Voice Webhook', envelope({
    From: '+14145550150',
    To: '+14145550100',
    CallSid: 'CA-cap-1',
    CallStatus: 'no-answer',
  }), env);
  assert.match(pauseCall.responses[0].body, /Alerts paused for today, see your dashboard: https:\/\/text\.example\/webhook\/mctb-dashboard\?token=token-northline/);
  assert.equal((pauseCall.responses[0].body.match(/<Message to="\+14145550199">/g) || []).length, 1);
  const pauseAgain = runWorkflow(workflows.voice, 'Voice Webhook', envelope({
    From: '+14145550151',
    To: '+14145550100',
    CallSid: 'CA-cap-2',
    CallStatus: 'no-answer',
  }), env);
  assert.doesNotMatch(pauseAgain.responses[0].body, /Alerts paused for today/);
  assert.doesNotMatch(pauseAgain.responses[0].body, /<Message to="\+14145550199">/);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'owner_cap_notice'))").n), 1);
  say('Owner alert cap sent one pause text and did not repeat it.');

  const artifactDir = process.env.MCTB_ARTIFACT_DIR || os.tmpdir();
  fs.mkdirSync(artifactDir, { recursive: true });
  const text = transcript.join('\n') + '\n';
  fs.writeFileSync(path.join(artifactDir, 'missed-call-e2e.txt'), text);
  assert.ok(text.includes('FOLLOW-UP 1'));
});
