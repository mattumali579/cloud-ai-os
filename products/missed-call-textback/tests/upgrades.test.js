'use strict';

const assert = require('node:assert/strict');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const test = require('node:test');
const { psql, psqlJson, resetDatabase, quoteJson, runWorkflow, resetWorkflowStaticData } = require('./sim');

const root = path.join(__dirname, '..');
const workflows = {
  followups: requireJson('mctb_followups.json'),
  dashboard: requireJson('mctb_dashboard.json'),
  sms: requireJson('mctb_inbound_sms.json'),
  revenue: requireJson('mctb_revenue.json'),
  review: requireJson('mctb_review_click.json'),
  health: requireJson('mctb_health.json'),
};

function requireJson(name) {
  return JSON.parse(fs.readFileSync(path.join(root, 'workflows', name), 'utf8'));
}

const env = {
  TWILIO_MODE: 'mock',
  TWILIO_ACCOUNT_SID: '',
  TWILIO_AUTH_TOKEN: '',
  MCTB_PUBLIC_BASE_URL: 'https://text.example',
  OWNER_ALERT_WEBHOOK_URL: '',
};

const MONDAY_EARLY = '2026-10-05T12:30:00.000Z'; // 07:30 America/Chicago
const MONDAY_EIGHT = '2026-10-05T13:00:00.000Z'; // 08:00 America/Chicago
const SUNDAY = '2026-10-04T15:00:00.000Z';
const MONTH_END = '2026-10-31T13:00:00.000Z'; // 08:00 America/Chicago, last day of October
const DAY = '2026-10-03T15:00:00.000Z';
const NIGHT = '2026-10-03T02:30:00.000Z';

function envelope(body, query) {
  return { headers: { 'content-type': 'application/x-www-form-urlencoded' }, query: query || {}, body: body || {} };
}

function one(sql) {
  return psqlJson(sql);
}

function reset() {
  resetDatabase(path.join(root, 'sql', '001_schema.sql'));
  resetWorkflowStaticData();
}

test('weekly and month-end revenue texts include the dashboard link and do not repeat', () => {
  reset();
  psql('mctb_test', `
    INSERT INTO mctb.tenants
      (slug, business_name, twilio_number, owner_name, owner_phone, timezone, dashboard_token, revenue_report_enabled)
    VALUES
      ('northline', 'Northline Heating & Air', '+14145550100', 'Dana', '+14145550199', 'America/Chicago', 'token-northline', true)
  `);
  psql('mctb_test', `
    INSERT INTO mctb.calls (tenant_id, call_sid, from_number, to_number, missed, text_sent, created_at)
    SELECT id, 'CA-week', '+14145550123', twilio_number, true, true, '2026-09-30T15:00:00Z'
    FROM mctb.tenants WHERE slug = 'northline';
    INSERT INTO mctb.messages (tenant_id, direction, from_number, to_number, body, purpose, created_at)
    SELECT id, 'in', '+14145550123', twilio_number, 'Furnace will not start', 'qualify', '2026-09-30T15:05:00Z'
    FROM mctb.tenants WHERE slug = 'northline';
    INSERT INTO mctb.conversations (tenant_id, phone, state, need, qualified_at)
    SELECT id, '+14145550123', 'handed_off', 'Furnace will not start', '2026-09-30T15:10:00Z'
    FROM mctb.tenants WHERE slug = 'northline';
    INSERT INTO mctb.estimates (tenant_id, customer_name, phone, job, amount_cents, status, created_at, updated_at)
    SELECT id, 'Jane Doe', '+14145550123', 'furnace', 450000, 'won', '2026-09-30T16:00:00Z', '2026-10-02T15:00:00Z'
    FROM mctb.tenants WHERE slug = 'northline';
    INSERT INTO mctb.estimates (tenant_id, customer_name, phone, job, amount_cents, status, created_at, updated_at)
    SELECT id, 'Walk-in', '+14145550177', 'tune-up', 100000, 'won', '2026-10-01T15:00:00Z', '2026-10-02T16:00:00Z'
    FROM mctb.tenants WHERE slug = 'northline';
  `);

  const early = runWorkflow(workflows.revenue, 'Run Revenue Webhook', envelope({ now: MONDAY_EARLY }), env);
  assert.equal(early.outputs['Plan Reports'][0].json.count, 0);
  const sunday = runWorkflow(workflows.revenue, 'Run Revenue Webhook', envelope({ now: SUNDAY }), env);
  assert.equal(sunday.outputs['Plan Reports'][0].json.count, 0);

  const monday = runWorkflow(workflows.revenue, 'Run Revenue Webhook', envelope({ now: MONDAY_EIGHT }), env);
  assert.equal(monday.outputs['Plan Reports'][0].json.count, 1);
  const queued = one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_queue), 'body', (SELECT body FROM mctb.outbound_queue LIMIT 1), 'purpose', (SELECT purpose FROM mctb.outbound_queue LIMIT 1))");
  assert.equal(Number(queued.n), 1);
  assert.equal(queued.purpose, 'revenue_report_week');
  assert.ok(queued.body.length <= 320);
  assert.match(queued.body, /Missed 1/);
  assert.match(queued.body, /texted 1/);
  assert.match(queued.body, /Recovered 1/);
  assert.match(queued.body, /leads 1/);
  assert.match(queued.body, /Estimates 2/);
  assert.match(queued.body, /won 2 \(\$5,500\.00\)/);
  assert.match(queued.body, /Est\. recovered \$4,500\.00/);
  assert.match(queued.body, /https:\/\/text\.example\/webhook\/mctb-dashboard\?token=token-northline/);

  const again = runWorkflow(workflows.revenue, 'Run Revenue Webhook', envelope({ now: MONDAY_EIGHT }), env);
  assert.equal(again.outputs['Plan Reports'][0].json.count, 0);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_queue))").n), 1);

  psql('mctb_test', "UPDATE mctb.outbound_queue SET send_at = now() - interval '1 minute'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: DAY }), env);
  const sent = one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'revenue_report_week'), 'status', (SELECT status FROM mctb.outbound_queue LIMIT 1))");
  assert.equal(Number(sent.n), 1);
  assert.equal(sent.status, 'sent');

  psql('mctb_test', "DELETE FROM mctb.revenue_reports; DELETE FROM mctb.outbound_queue; UPDATE mctb.tenants SET revenue_report_enabled = false");
  const off = runWorkflow(workflows.revenue, 'Run Revenue Webhook', envelope({ now: MONDAY_EIGHT }), env);
  assert.equal(off.outputs['Plan Reports'][0].json.count, 0);

  psql('mctb_test', 'UPDATE mctb.tenants SET revenue_report_enabled = true');
  psql('mctb_test', `
    INSERT INTO mctb.suppressions (tenant_id, phone, reason)
    SELECT id, owner_phone, 'stop' FROM mctb.tenants WHERE slug = 'northline'
  `);
  const opted = runWorkflow(workflows.revenue, 'Run Revenue Webhook', envelope({ now: MONDAY_EIGHT }), env);
  assert.equal(opted.outputs['Plan Reports'][0].json.count, 1);
  const held = one("SELECT jsonb_build_object('status', status, 'queued', (SELECT count(*) FROM mctb.outbound_queue)) FROM mctb.revenue_reports");
  assert.equal(held.status, 'suppressed');
  assert.equal(Number(held.queued), 0);
  psql('mctb_test', 'DELETE FROM mctb.suppressions');

  const month = runWorkflow(workflows.revenue, 'Run Revenue Webhook', envelope({ now: MONTH_END }), env);
  assert.equal(month.outputs['Plan Reports'][0].json.count, 1);
  const monthBody = one("SELECT jsonb_build_object('body', body, 'purpose', purpose) FROM mctb.outbound_queue ORDER BY id DESC LIMIT 1");
  assert.equal(monthBody.purpose, 'revenue_report_month');
  assert.ok(monthBody.body.length <= 320);
  assert.match(monthBody.body, /This month/);
  assert.match(monthBody.body, /token=token-northline/);

  const board = runWorkflow(workflows.dashboard, 'Dashboard Webhook', envelope({}, { token: 'token-northline' }), env);
  assert.match(board.responses[0].body, /This week \/ This month/);
  assert.match(board.responses[0].body, /Estimated revenue recovered/);
  assert.match(board.responses[0].body, /Review requests sent/);
  assert.match(board.responses[0].body, /Review links clicked/);
});

test('review requests wait two hours, track the click, remind once, and refuse opt-outs', () => {
  reset();
  psql('mctb_test', `
    INSERT INTO mctb.tenants
      (slug, business_name, twilio_number, owner_name, owner_phone, timezone, dashboard_token, google_review_url)
    VALUES
      ('northline', 'Northline Heating & Air', '+14145550100', 'Dana', '+14145550199', 'America/Chicago', 'token-northline',
       'https://search.google.com/local/writereview?placeid=abc')
  `);
  function sms(from, body) {
    return runWorkflow(workflows.sms, 'SMS Webhook', envelope({
      From: from,
      To: '+14145550100',
      Body: body,
      MessageSid: 'SM' + body.length,
    }), env);
  }
  const saved = sms('+14145550199', 'ESTIMATE Jane Doe | 4145550123 | furnace | 4500');
  assert.match(saved.responses[0].body, /Jane Doe/);
  const won = sms('+14145550199', 'WON 1');
  assert.match(won.responses[0].body, /Review text scheduled/);
  const scheduled = one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.review_requests), 'send_at', (SELECT send_at FROM mctb.review_requests LIMIT 1), 'status', (SELECT status FROM mctb.review_requests LIMIT 1))");
  assert.equal(Number(scheduled.n), 1);
  assert.equal(scheduled.status, 'scheduled');
  assert.ok(Math.abs(new Date(scheduled.send_at).getTime() - (Date.now() + 2 * 60 * 60 * 1000)) < 5 * 60 * 1000);

  const again = sms('+14145550199', 'REVIEW 4145550123');
  assert.match(again.responses[0].body, /90 days/);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.review_requests))").n), 1);

  psql('mctb_test', "UPDATE mctb.review_requests SET send_at = now() - interval '1 minute'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: NIGHT }), env);
  const deferred = one("SELECT jsonb_build_object('status', status, 'sent', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'review_request')) FROM mctb.review_requests");
  assert.equal(deferred.status, 'scheduled');
  assert.equal(Number(deferred.sent), 0);

  psql('mctb_test', "UPDATE mctb.review_requests SET send_at = now() - interval '1 minute'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: DAY }), env);
  const sent = one("SELECT jsonb_build_object('status', status, 'body', (SELECT body FROM mctb.outbound_log WHERE purpose = 'review_request' ORDER BY id DESC LIMIT 1), 'token', token) FROM mctb.review_requests");
  assert.equal(sent.status, 'sent');
  assert.match(sent.body, /https:\/\/text\.example\/webhook\/mctb-r\//);
  assert.match(sent.body, /STOP/);
  assert.doesNotMatch(sent.body, /placeid/);

  const click = runWorkflow(workflows.review, 'Review Click', {
    headers: {},
    params: { token: sent.token },
    query: {},
    body: {},
  }, env);
  assert.equal(click.responses[0].statusCode, 302);
  assert.equal(click.responses[0].headers.Location, 'https://search.google.com/local/writereview?placeid=abc');
  const clicked = one("SELECT jsonb_build_object('status', status, 'clicked', clicked_at IS NOT NULL) FROM mctb.review_requests");
  assert.equal(clicked.status, 'clicked');
  assert.equal(clicked.clicked, true);
  psql('mctb_test', "UPDATE mctb.review_requests SET reminder_at = now() - interval '1 minute'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: DAY }), env);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'review_reminder'))").n), 0);

  const missing = runWorkflow(workflows.review, 'Review Click', {
    headers: {},
    params: { token: 'missing-token' },
    query: {},
    body: {},
  }, env);
  assert.equal(missing.responses[0].statusCode, 404);

  psql('mctb_test', `
    INSERT INTO mctb.review_requests (tenant_id, phone, token, status, send_at)
    SELECT id, '+14145550188', 'remind-me', 'sent', now() - interval '4 days'
    FROM mctb.tenants WHERE slug = 'northline'
  `);
  psql('mctb_test', "UPDATE mctb.review_requests SET reminder_at = now() - interval '1 minute', sent_at = now() - interval '4 days' WHERE token = 'remind-me'");
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: DAY }), env);
  const reminder = one("SELECT jsonb_build_object('status', status, 'body', (SELECT body FROM mctb.outbound_log WHERE purpose = 'review_reminder' ORDER BY id DESC LIMIT 1)) FROM mctb.review_requests WHERE token = 'remind-me'");
  assert.equal(reminder.status, 'reminded');
  assert.match(reminder.body, /Reminder from/);
  assert.match(reminder.body, /STOP/);
  runWorkflow(workflows.followups, 'Run Follow-ups Webhook', envelope({ now: DAY }), env);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'review_reminder'))").n), 1);

  const stopped = sms('+14145550150', 'STOP');
  assert.match(stopped.responses[0].body, /unsubscribed/);
  const refused = sms('+14145550199', 'DONE 4145550150');
  assert.match(refused.responses[0].body, /opted out/);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.review_requests WHERE phone = '+14145550150'))").n), 0);

  psql('mctb_test', 'UPDATE mctb.tenants SET google_review_url = NULL');
  const noUrl = sms('+14145550199', 'REVIEW 4145550166');
  assert.match(noUrl.responses[0].body, /No Google review link/);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.review_requests WHERE phone = '+14145550166'))").n), 0);

  const board = runWorkflow(workflows.dashboard, 'Dashboard Webhook', envelope({}, { token: 'token-northline' }), env);
  assert.match(board.responses[0].body, /Review requests sent/);
  assert.match(board.responses[0].body, />2</);
  const stats = one('SELECT mctb.dashboard_snapshot(' + quoteJson('token-northline') + ')');
  assert.equal(Number(stats.stats.reviews_sent), 2);
  assert.equal(Number(stats.stats.reviews_clicked), 1);
});

test('health endpoint, hourly admin alert, and recovery notice', () => {
  reset();
  psql('mctb_test', `
    INSERT INTO mctb.tenants
      (slug, business_name, twilio_number, owner_name, owner_phone, timezone, dashboard_token, created_at)
    VALUES
      ('northline', 'Northline Heating & Air', '+14145550100', 'Dana', '+14145550199', 'America/Chicago', 'token-northline', now() - interval '3 days')
  `);
  const healthEnv = Object.assign({}, env, {
    MCTB_ADMIN_PHONE: '+14145550900',
    MCTB_ADMIN_EMAIL: 'ops@example.com',
    N8N_SMTP_HOST: 'smtp.example.com',
    N8N_SMTP_SENDER: 'alerts@example.com',
  });
  const down = runWorkflow(workflows.health, 'Run Health Webhook', envelope({ now: DAY }), healthEnv);
  assert.equal(down.emailLog.length, 1);
  assert.match(down.emailLog[0].text, /Follow-up workflow/);
  assert.equal(down.emailLog[0].to, 'ops@example.com');
  const sms = one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'health_alert'), 'to', (SELECT to_number FROM mctb.outbound_log WHERE purpose = 'health_alert' LIMIT 1))");
  assert.equal(Number(sms.n), 1);
  assert.equal(sms.to, '+14145550900');

  const quiet = runWorkflow(workflows.health, 'Run Health Webhook', envelope({ now: '2026-10-03T15:10:00.000Z' }), healthEnv);
  assert.equal(quiet.emailLog.length, 0);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'health_alert'))").n), 1);

  psql('mctb_test', "SELECT mctb.stamp_heartbeat('followups'); SELECT mctb.stamp_heartbeat('revenue')");
  const back = runWorkflow(workflows.health, 'Run Health Webhook', envelope({ now: DAY }), healthEnv);
  assert.equal(back.emailLog.length, 1);
  assert.match(back.emailLog[0].subject, /recovered/i);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'health_recovery'))").n), 1);
  const beat = runWorkflow(workflows.health, 'Health Webhook', { headers: {}, query: {}, body: {} }, env);
  assert.equal(beat.responses[0].statusCode, 200);
  const payload = JSON.parse(beat.responses[0].body);
  assert.equal(payload.ok, true);
  assert.equal(payload.db, 'ok');
  assert.equal(payload.version, 'mctb-engine-1');
  assert.match(beat.responses[0].contentType, /application\/json/);

  psql('mctb_test', "UPDATE mctb.workflow_heartbeats SET ran_at = now() - interval '2 hours'");
  const stale = runWorkflow(workflows.health, 'Health Webhook', { headers: {}, query: {}, body: {} }, env);
  assert.equal(JSON.parse(stale.responses[0].body).ok, false);
  assert.equal(stale.responses[0].statusCode, 200);

  psql('mctb_test', "INSERT INTO mctb.suppressions (tenant_id, phone, reason) SELECT id, '+14145550900', 'stop' FROM mctb.tenants");
  psql('mctb_test', "UPDATE mctb.health_state SET alert_open = false, last_alert_at = NULL, status = 'ok'");
  const opted = runWorkflow(workflows.health, 'Run Health Webhook', envelope({ now: '2026-10-03T18:00:00.000Z' }), healthEnv);
  assert.equal(opted.emailLog.length, 1);
  assert.equal(Number(one("SELECT jsonb_build_object('n', (SELECT count(*) FROM mctb.outbound_log WHERE purpose = 'health_alert'))").n), 1);

  resetWorkflowStaticData();
  const outageEnv = Object.assign({}, healthEnv, { MCTB_SIM_DB_DOWN: '1', MCTB_ADMIN_PHONE: '' });
  const outage = runWorkflow(workflows.health, 'Run Health Webhook', envelope({ now: DAY }), outageEnv);
  assert.equal(outage.emailLog.length, 1);
  assert.match(outage.emailLog[0].text, /Postgres is not answering/);
  const repeat = runWorkflow(workflows.health, 'Run Health Webhook', envelope({ now: '2026-10-03T15:05:00.000Z' }), outageEnv);
  assert.equal(repeat.emailLog.length, 0);
});

test('backup scripts dump the mctb schema and keep 14 days', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mctb-backup-'));
  const stub = path.join(dir, 'docker');
  fs.writeFileSync(stub, '#!/bin/sh\necho "-- schema mctb dump"\n');
  fs.chmodSync(stub, 0o755);
  const oldFile = path.join(dir, 'mctb-old.sql');
  const keptFile = path.join(dir, 'mctb-kept.sql');
  fs.writeFileSync(oldFile, 'old');
  fs.writeFileSync(keptFile, 'kept');
  execFileSync('touch', ['-d', '15 days ago', oldFile]);
  execFileSync('touch', ['-d', '3 days ago', keptFile]);
  const output = execFileSync(path.join(root, 'scripts/backup.sh'), {
    encoding: 'utf8',
    env: Object.assign({}, process.env, {
      DOCKER: stub,
      CONTAINER: 'cloudos-db-1',
      PGUSER: 'cloudos',
      PGDATABASE: 'cloudos',
      OUT_DIR: dir,
    }),
  });
  assert.match(output, /Wrote /);
  assert.equal(fs.existsSync(oldFile), false);
  assert.equal(fs.readFileSync(keptFile, 'utf8'), 'kept');
  const dumps = fs.readdirSync(dir).filter((name) => /^mctb-.*\.sql$/.test(name) && name !== 'mctb-kept.sql');
  assert.equal(dumps.length, 1);
  assert.match(fs.readFileSync(path.join(dir, dumps[0]), 'utf8'), /schema mctb/);

  const sh = fs.readFileSync(path.join(root, 'scripts/backup.sh'), 'utf8');
  const ps1 = fs.readFileSync(path.join(root, 'scripts/backup.ps1'), 'utf8');
  assert.match(sh, /pg_dump/);
  assert.match(sh, /--schema=mctb/);
  assert.match(sh, /docker exec|DOCKER/);
  assert.match(sh, /-mtime \+14/);
  assert.match(ps1, /pg_dump/);
  assert.match(ps1, /--schema=mctb/);
  assert.match(ps1, /docker exec/);
  assert.match(ps1, /AddDays\(-14\)/);
  const runbook = fs.readFileSync(path.join(root, 'RUNBOOK.md'), 'utf8');
  assert.match(runbook, /Task Scheduler/);
  assert.match(runbook, /backup\.ps1/);
  assert.match(runbook, /Primary handler fails/);
});
