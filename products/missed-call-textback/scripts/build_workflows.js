'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const engineSource = fs.readFileSync(path.join(root, 'logic', 'engine.js'), 'utf8');
const engineSha = crypto.createHash('sha256').update(engineSource).digest('hex');

function embed(tail) {
  return [
    'const engine = (function () {',
    '  const module = { exports: {} };',
    '  /*MCTB_ENGINE_START*/',
    engineSource,
    '  /*MCTB_ENGINE_END*/',
    '  return module.exports;',
    '})();',
    tail.trim(),
  ].join('\n');
}

function workflowBuilder(prefix) {
  const nodes = [];
  const connections = {};
  let n = 1;
  function add(spec) {
    const id = prefix + '-' + String(n).padStart(4, '0') + '-4000-8000-000000000001';
    n += 1;
    const node = {
      id: id,
      name: spec.name,
      type: spec.type,
      typeVersion: spec.typeVersion,
      position: spec.position || [(n - 2) * 260, spec.row || 0],
      parameters: spec.parameters,
    };
    if (spec.webhookId) node.webhookId = spec.webhookId;
    if (spec.credentials) node.credentials = spec.credentials;
    if (spec.onError) node.onError = spec.onError;
    if (spec.notes) node.notes = spec.notes;
    nodes.push(node);
    return node;
  }
  function link(from, to, outputIndex) {
    const index = outputIndex || 0;
    if (!connections[from]) connections[from] = { main: [] };
    while (connections[from].main.length <= index) connections[from].main.push([]);
    connections[from].main[index].push({ node: to, type: 'main', index: 0 });
  }
  return { add: add, link: link, nodes: nodes, connections: connections };
}

const POSTGRES_CREDENTIAL = {
  postgres: {
    id: 'BRIGHTREACH_POSTGRES_CREDENTIAL_ID',
    name: 'BrightReach Postgres',
  },
};

function code(name, tail, notes, row) {
  return {
    name: name,
    type: 'n8n-nodes-base.code',
    typeVersion: 2,
    row: row || 0,
    notes: notes,
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: embed(tail),
    },
  };
}

function selectAs(expression, alias) {
  return 'SELECT ' + expression + ' AS "' + alias + '"';
}

function postgres(name, query, replacement, notes, batch, hooks) {
  const options = {};
  if (replacement) options.queryReplacement = replacement;
  if (batch) options.queryBatching = batch;
  const spec = {
    name: name,
    type: 'n8n-nodes-base.postgres',
    typeVersion: 2.5,
    notes: notes,
    credentials: POSTGRES_CREDENTIAL,
    parameters: {
      operation: 'executeQuery',
      query: query,
      options: options,
    },
  };
  if (hooks && hooks.onError) spec.onError = hooks.onError;
  return spec;
}

function ifYes(name, expression, notes) {
  return {
    name: name,
    type: 'n8n-nodes-base.if',
    typeVersion: 2.2,
    notes: notes,
    parameters: {
      conditions: {
        options: {
          caseSensitive: true,
          leftValue: '',
          typeValidation: 'loose',
          version: 2,
        },
        conditions: [
          {
            id: 'mctb-yes',
            leftValue: expression,
            rightValue: 'yes',
            operator: { type: 'string', operation: 'equals' },
          },
        ],
        combinator: 'and',
      },
      options: {},
    },
  };
}

function webhook(name, method, webhookPath, webhookId, notes, responseMode) {
  return {
    name: name,
    type: 'n8n-nodes-base.webhook',
    typeVersion: 2,
    webhookId: webhookId,
    notes: notes,
    parameters: {
      httpMethod: method,
      path: webhookPath,
      responseMode: responseMode || 'responseNode',
      options: {},
    },
  };
}

function respond(name, bodyExpr, contentType, extra) {
  const entries = [{ name: 'Content-Type', value: contentType }];
  ((extra && extra.headers) || []).forEach((header) => entries.push(header));
  return {
    name: name,
    type: 'n8n-nodes-base.respondToWebhook',
    typeVersion: 1.1,
    parameters: {
      respondWith: 'text',
      responseBody: bodyExpr,
      options: {
        responseCode: extra && extra.code ? extra.code : 200,
        responseHeaders: { entries: entries },
      },
    },
  };
}

function finish(builder, name, id) {
  return {
    name: name,
    id: id,
    active: false,
    nodes: builder.nodes,
    connections: builder.connections,
    settings: { executionOrder: 'v1' },
    meta: {
      product: 'brightreach-missed-call-textback',
      engineSha256: engineSha,
      engineVersion: 'mctb-engine-1',
    },
    pinData: {},
    tags: [],
  };
}

const NORMALIZE = `
const wrapped = engine.unwrapWebhook($input.first().json);
const fields = engine.twilioFields(wrapped.body);
const simulatedNow = ($env.TWILIO_MODE || '') !== 'live' && wrapped.body && wrapped.body.now
  ? String(wrapped.body.now)
  : '';
return [{
  json: {
    to_e164: engine.normalizePhone(fields.to) || '',
    from_e164: engine.normalizePhone(fields.from) || '',
    call_sid: fields.callSid || '',
    call_status: fields.callStatus || '',
    dial_status: fields.dialStatus || '',
    text: fields.text || '',
    message_sid: fields.messageSid || '',
    simulated_now: simulatedNow
  }
}];
`;

function prepareTail(decideName) {
  return `
const decided = $('${decideName}').first().json;
const url = $env.OWNER_ALERT_WEBHOOK_URL || '';
return [{
  json: {
    twiml: decided.twiml,
    shouldNotify: url && decided.ownerWebhook ? 'yes' : 'no',
    ownerWebhook: decided.ownerWebhook || {}
  }
}];
`;
}

function voiceLike(options) {
  const graph = workflowBuilder(options.prefix);
  graph.add(webhook(options.webhookName, 'POST', options.path, options.webhookId, options.webhookNotes));
  graph.add(code(options.normalizeName, NORMALIZE, 'Reads Twilio form fields. Honors body.now only when TWILIO_MODE is not live.'));
  graph.add(postgres(
    options.loadName,
    options.loadQuery,
    '={{ $json.to_e164 }},{{ $json.from_e164 }},{{ $json.call_sid }}',
    'Loads the tenant for this Twilio number. Phones are already normalized, so they contain no commas.'
  ));
  graph.add(code(options.decideName, options.decideTail, options.decideNotes));
  graph.add(ifYes(options.ifName, '={{ $json.save }}', 'Skip the database write when the caller is unknown or this call was already handled.'));
  graph.add(postgres(
    options.applyName,
    selectAs('mctb.' + options.applyFn + '($1::jsonb)', 'result'),
    '={{ JSON.stringify($json.record) }}',
    'One JSON parameter. n8n binds a JSON string as a single value, so commas inside the payload are safe.'
  ));
  graph.add(code(options.prepareName, prepareTail(options.decideName), 'TwiML is the Twilio response. Customer and owner texts in that XML are sent by Twilio itself.'));
  graph.add(ifYes(options.notifyIfName, '={{ $json.shouldNotify }}', 'Optional fan-out when OWNER_ALERT_WEBHOOK_URL is set.'));
  graph.add({
    name: options.notifyName,
    type: 'n8n-nodes-base.httpRequest',
    typeVersion: 4.2,
    onError: 'continueRegularOutput',
    notes: 'Posts the owner alert to OWNER_ALERT_WEBHOOK_URL (Discord, the existing CloudOS owner-notify webhook, or an email gateway). Failure does not change the TwiML response.',
    parameters: {
      method: 'POST',
      url: '={{ $env.OWNER_ALERT_WEBHOOK_URL }}',
      sendBody: true,
      specifyBody: 'json',
      jsonBody: '={{ JSON.stringify($json.ownerWebhook) }}',
      options: { timeout: 15000 },
    },
  });
  graph.add(respond(options.respondName, '={{ $json.twiml }}', 'text/xml; charset=utf-8'));

  graph.link(options.webhookName, options.normalizeName);
  graph.link(options.normalizeName, options.loadName);
  graph.link(options.loadName, options.decideName);
  graph.link(options.decideName, options.ifName);
  graph.link(options.ifName, options.applyName, 0);
  graph.link(options.ifName, options.prepareName, 1);
  graph.link(options.applyName, options.prepareName);
  graph.link(options.prepareName, options.respondName);
  graph.link(options.prepareName, options.notifyIfName);
  graph.link(options.notifyIfName, options.notifyName, 0);
  return finish(graph, options.workflowName, options.workflowId);
}

const voice = voiceLike({
  prefix: 'mctbvoic',
  workflowName: 'BrightReach Missed Call',
  workflowId: 'mctbVoice',
  webhookName: 'Voice Webhook',
  path: 'mctb-voice',
  webhookId: 'mctb-voice-hook',
  webhookNotes: 'Twilio voice webhook. Forward mode texts the caller in this response. Dial mode rings the owner, then mctb-dial-status sends the text.',
  normalizeName: 'Normalize Voice',
  loadName: 'Load Voice Context',
  loadQuery: selectAs('mctb.load_voice_context($1::text, $2::text, $3::text)', 'ctx'),
  decideName: 'Decide Voice',
  decideTail: `
const norm = $('Normalize Voice').first().json;
const now = norm.simulated_now || new Date().toISOString();
const ctx = engine.contextFromLoad($input.first().json.ctx, {
  now: now,
  to: norm.to_e164,
  from: norm.from_e164,
  callSid: norm.call_sid,
  callStatus: norm.call_status,
  dialStatus: norm.dial_status,
  publicBaseUrl: $env.MCTB_PUBLIC_BASE_URL || ''
});
return [{ json: engine.handleVoice(ctx) }];
`,
  decideNotes: 'Chooses forward-mode text-back or a Dial to the owner phone.',
  ifName: 'Save Voice',
  applyName: 'Apply Voice',
  applyFn: 'apply_voice_decision',
  prepareName: 'Prepare Voice Response',
  notifyIfName: 'Notify On Voice',
  notifyName: 'Post Voice Alert',
  respondName: 'Respond Voice',
});

const dial = voiceLike({
  prefix: 'mctbdial',
  workflowName: 'BrightReach Dial Result',
  workflowId: 'mctbDial',
  webhookName: 'Dial Webhook',
  path: 'mctb-dial-status',
  webhookId: 'mctb-dial-hook',
  webhookNotes: 'Twilio Dial action URL. no-answer, busy, failed, and canceled become a missed-call text. completed does not text the caller.',
  normalizeName: 'Normalize Dial',
  loadName: 'Load Dial Context',
  loadQuery: selectAs('mctb.load_voice_context($1::text, $2::text, $3::text)', 'ctx'),
  decideName: 'Decide Dial',
  decideTail: `
const norm = $('Normalize Dial').first().json;
const now = norm.simulated_now || new Date().toISOString();
const ctx = engine.contextFromLoad($input.first().json.ctx, {
  now: now,
  to: norm.to_e164,
  from: norm.from_e164,
  callSid: norm.call_sid,
  callStatus: norm.call_status,
  dialStatus: norm.dial_status,
  publicBaseUrl: $env.MCTB_PUBLIC_BASE_URL || ''
});
return [{ json: engine.handleDialStatus(ctx) }];
`,
  decideNotes: 'Sends the missed-call text only when the owner did not answer.',
  ifName: 'Save Dial',
  applyName: 'Apply Dial',
  applyFn: 'apply_voice_decision',
  prepareName: 'Prepare Dial Response',
  notifyIfName: 'Notify On Dial',
  notifyName: 'Post Dial Alert',
  respondName: 'Respond Dial',
});

const smsGraph = workflowBuilder('mctbsms0');
smsGraph.add(webhook('SMS Webhook', 'POST', 'mctb-sms', 'mctb-sms-hook', 'Twilio messaging webhook. STOP, HELP, and START are handled here. Owner commands are accepted only from the tenant owner phone.'));
smsGraph.add(code('Normalize SMS', NORMALIZE, 'Normalizes the inbound SMS.'));
smsGraph.add(postgres(
  'Load SMS Context',
  selectAs('mctb.load_sms_context($1::text, $2::text)', 'ctx'),
  '={{ $json.to_e164 }},{{ $json.from_e164 }}',
  'Tenant, conversation, suppression, open estimate, and rate-limit counts.'
));
smsGraph.add(code('Decide SMS', `
const norm = $('Normalize SMS').first().json;
const now = norm.simulated_now || new Date().toISOString();
const ctx = engine.contextFromLoad($input.first().json.ctx, {
  now: now,
  to: norm.to_e164,
  from: norm.from_e164,
  body: norm.text,
  messageSid: norm.message_sid,
  publicBaseUrl: $env.MCTB_PUBLIC_BASE_URL || ''
});
return [{ json: engine.handleInboundSms(ctx) }];
`, 'Qualification, compliance keywords, and owner commands.'));
smsGraph.add(ifYes('Save SMS', '={{ $json.save }}', 'Unknown numbers get an empty 200 so Twilio does not retry.'));
smsGraph.add(postgres(
  'Apply SMS',
  selectAs('mctb.apply_sms_decision($1::jsonb)', 'result'),
  '={{ JSON.stringify($json.record) }}',
  'Persists the reply, opt-out, estimate command, and outbound log.'
));
smsGraph.add(code('Prepare SMS Response', prepareTail('Decide SMS'), 'TwiML Message nouns send the reply and the owner alert.'));
smsGraph.add(ifYes('Notify On SMS', '={{ $json.shouldNotify }}', 'Optional owner webhook.'));
smsGraph.add({
  name: 'Post SMS Alert',
  type: 'n8n-nodes-base.httpRequest',
  typeVersion: 4.2,
  onError: 'continueRegularOutput',
  notes: 'Same OWNER_ALERT_WEBHOOK_URL fan-out as the voice workflow.',
  parameters: {
    method: 'POST',
    url: '={{ $env.OWNER_ALERT_WEBHOOK_URL }}',
    sendBody: true,
    specifyBody: 'json',
    jsonBody: '={{ JSON.stringify($json.ownerWebhook) }}',
    options: { timeout: 15000 },
  },
});
smsGraph.add(respond('Respond SMS', '={{ $json.twiml }}', 'text/xml; charset=utf-8'));
smsGraph.link('SMS Webhook', 'Normalize SMS');
smsGraph.link('Normalize SMS', 'Load SMS Context');
smsGraph.link('Load SMS Context', 'Decide SMS');
smsGraph.link('Decide SMS', 'Save SMS');
smsGraph.link('Save SMS', 'Apply SMS', 0);
smsGraph.link('Save SMS', 'Prepare SMS Response', 1);
smsGraph.link('Apply SMS', 'Prepare SMS Response');
smsGraph.link('Prepare SMS Response', 'Respond SMS');
smsGraph.link('Prepare SMS Response', 'Notify On SMS');
smsGraph.link('Notify On SMS', 'Post SMS Alert', 0);
const sms = finish(smsGraph, 'BrightReach Inbound SMS', 'mctbSms');

const followGraph = workflowBuilder('mctbfolo');
followGraph.add({
  name: 'Every Minute',
  type: 'n8n-nodes-base.scheduleTrigger',
  typeVersion: 1.2,
  notes: 'Drains estimate follow-ups and queued texts about once a minute on the client PC. This is the product scheduler, not a Cloud AI OS keepalive, and it must not be pointed at Supabase.',
  parameters: { rule: { interval: [{ field: 'minutes', minutesInterval: 1 }] } },
});
followGraph.add(webhook(
  'Run Follow-ups Webhook',
  'POST',
  'mctb-followups-run',
  'mctb-followups-hook',
  'Manual or test trigger. Responds immediately, then drains the queue. body.now is honored only when TWILIO_MODE is not live.',
  'onReceived'
));
followGraph.add(code('Capture Now', `
const current = $input.first().json || {};
const body = current.body || {};
const simulated = ($env.TWILIO_MODE || '') !== 'live' && body.now ? String(body.now) : '';
return [{ json: { now: simulated || new Date().toISOString() } }];
`, 'Picks the clock used for quiet hours.'));
followGraph.add(postgres(
  'Load Due Work',
  selectAs('mctb.due_work()', 'work'),
  '',
  'Due estimates and queued texts. No parameters.'
));
followGraph.add(code('Plan Sends', `
const work = $input.first().json.work || { followups: [], queued: [] };
const now = $('Capture Now').first().json.now;
const fanout = engine.planWork(work, now, {
  TWILIO_MODE: $env.TWILIO_MODE || '',
  TWILIO_ACCOUNT_SID: $env.TWILIO_ACCOUNT_SID || '',
  TWILIO_AUTH_TOKEN: $env.TWILIO_AUTH_TOKEN || '',
  MCTB_PUBLIC_BASE_URL: $env.MCTB_PUBLIC_BASE_URL || ''
});
return [{ json: { count: fanout.length, fanout: fanout } }];
`, 'Quiet hours, rate limits, STOP suppression, and the 3-step estimate sequence. Always returns one item so the webhook can respond even when nothing is due.'));
followGraph.add(code('Expand Sends', `
const fanout = $input.first().json.fanout || [];
return fanout.map((item) => ({ json: item }));
`, 'One item per text so the Postgres node can mark each row.'));
followGraph.add(ifYes('Use Twilio REST', '={{ $json.live ? \'yes\' : \'no\' }}', 'Live mode calls Twilio. Mock mode records the text in Postgres and does not call Twilio.'));
followGraph.add({
  name: 'Send Twilio SMS',
  type: 'n8n-nodes-base.httpRequest',
  typeVersion: 4.2,
  onError: 'continueRegularOutput',
  notes: 'REST send for follow-ups and queued texts. Missed-call and reply texts use TwiML instead and do not reach this node.',
  parameters: {
    method: 'POST',
    url: '={{ \'https://api.twilio.com/2010-04-01/Accounts/\' + $env.TWILIO_ACCOUNT_SID + \'/Messages.json\' }}',
    sendHeaders: true,
    headerParameters: {
      parameters: [{ name: 'Authorization', value: '={{ $json.auth }}' }],
    },
    sendBody: true,
    contentType: 'form-urlencoded',
    bodyParameters: {
      parameters: [
        { name: 'From', value: '={{ $json.from }}' },
        { name: 'To', value: '={{ $json.to }}' },
        { name: 'Body', value: '={{ $json.body }}' },
      ],
    },
    options: { timeout: 30000 },
  },
});
followGraph.add(code('Adopt Twilio Result', `
const responses = $input.all();
const plans = $('Expand Sends').all();
return responses.map((item, index) => {
  const planned = plans[index] ? plans[index].json : {};
  const sid = (item.json && (item.json.sid || (item.json.body && item.json.body.sid))) || '';
  const mark = Object.assign({}, planned.mark || {}, {
    status: sid ? 'sent' : 'retry',
    provider_sid: sid || null
  });
  if (!sid) {
    mark.next_send_at = new Date(Date.now() + 15 * 60 * 1000).toISOString();
    if (mark.kind === 'followup') {
      mark.estimate_status = 'open';
      mark.step_index = mark.step_index_before;
    }
  }
  return { json: { mark: mark } };
});
`, 'A missing Twilio sid retries in 15 minutes instead of advancing the sequence.'));
followGraph.add(postgres(
  'Mark Outbound',
  selectAs('mctb.mark_outbound($1::jsonb)', 'result'),
  '={{ JSON.stringify($json.mark) }}',
  'Writes the send, deferral, suppression, or rate-limit result.',
  'independently'
));
followGraph.add(postgres(
  'Stamp Followups',
  selectAs("mctb.stamp_heartbeat('followups')", 'result'),
  '',
  'Records that this pass reached the planner. The health check treats a stale stamp as a failed run.'
));
followGraph.link('Every Minute', 'Capture Now');
followGraph.link('Run Follow-ups Webhook', 'Capture Now');
followGraph.link('Capture Now', 'Load Due Work');
followGraph.link('Load Due Work', 'Plan Sends');
followGraph.link('Plan Sends', 'Expand Sends');
followGraph.link('Plan Sends', 'Stamp Followups');
followGraph.link('Expand Sends', 'Use Twilio REST');
followGraph.link('Use Twilio REST', 'Send Twilio SMS', 0);
followGraph.link('Use Twilio REST', 'Mark Outbound', 1);
followGraph.link('Send Twilio SMS', 'Adopt Twilio Result');
followGraph.link('Adopt Twilio Result', 'Mark Outbound');
const followups = finish(followGraph, 'BrightReach Estimate Follow-ups', 'mctbFollowups');

const dashGraph = workflowBuilder('mctbdash');
dashGraph.add(webhook('Dashboard Webhook', 'GET', 'mctb-dashboard', 'mctb-dashboard-hook', 'Password is the unguessable token from setup_tenant.sh. Bookmark the printed URL.'));
dashGraph.add(code('Read Dashboard Token', `
return [{ json: { token: engine.readToken($input.first().json) } }];
`, 'Reads ?token= from the query string.'));
dashGraph.add(postgres(
  'Load Snapshot',
  selectAs('mctb.dashboard_snapshot($1::text)', 'snap'),
  '={{ $json.token }}',
  'Returns null when the token does not match a tenant. Tokens are not commas, so the single parameter is safe.'
));
dashGraph.add(code('Render Dashboard', `
const snap = $input.first().json.snap;
const token = $('Read Dashboard Token').first().json.token;
return [{ json: { html: engine.renderDashboard(snap, token) } }];
`, 'ROI board: missed calls, recovered replies, leads, estimates, won jobs.'));
dashGraph.add(respond('Respond Dashboard', '={{ $json.html }}', 'text/html; charset=utf-8'));
dashGraph.link('Dashboard Webhook', 'Read Dashboard Token');
dashGraph.link('Read Dashboard Token', 'Load Snapshot');
dashGraph.link('Load Snapshot', 'Render Dashboard');
dashGraph.link('Render Dashboard', 'Respond Dashboard');
const dashboard = finish(dashGraph, 'BrightReach Dashboard', 'mctbDashboard');

const actionGraph = workflowBuilder('mctbact');
actionGraph.add(webhook('Action Webhook', 'POST', 'mctb-action', 'mctb-action-hook', 'Dashboard forms post here: log an estimate, text the customer, or mark won/lost.'));
actionGraph.add(code('Read Action', `
const item = $input.first().json;
const wrapped = engine.unwrapWebhook(item);
const body = wrapped.body || {};
return [{
  json: {
    token: engine.readToken(item),
    action: String(body.action || ''),
    customer_name: String(body.customer_name || ''),
    phone: String(body.phone || ''),
    job: String(body.job || ''),
    amount: String(body.amount || ''),
    message: String(body.message || ''),
    estimate_id: String(body.estimate_id || ''),
    simulated_now: ($env.TWILIO_MODE || '') !== 'live' && body.now ? String(body.now) : ''
  }
}];
`, 'Form fields from the dashboard.'));
actionGraph.add(postgres(
  'Load Action Tenant',
  selectAs('mctb.action_context($1::text)', 'tenant_pack'),
  '={{ $json.token }}',
  'Tenant for this dashboard token only.'
));
actionGraph.add(code('Decide Action', `
const form = $('Read Action').first().json;
const loaded = $input.first().json.tenant_pack || {};
const ctx = engine.contextFromLoad(loaded, {
  now: form.simulated_now || new Date().toISOString(),
  token: form.token,
  action: form.action,
  customer_name: form.customer_name,
  phone: form.phone,
  job: form.job,
  amount: form.amount,
  message: form.message,
  estimate_id: form.estimate_id
});
const decision = engine.handleAction(ctx, {
  TWILIO_MODE: $env.TWILIO_MODE || '',
  TWILIO_ACCOUNT_SID: $env.TWILIO_ACCOUNT_SID || '',
  TWILIO_AUTH_TOKEN: $env.TWILIO_AUTH_TOKEN || ''
});
return [{ json: decision }];
`, 'Creates the estimate schedule or the owner reply. Mock mode records the text without calling Twilio.'));
actionGraph.add(ifYes('Save Action', '={{ $json.save }}', 'Invalid forms still render an explanation.'));
actionGraph.add(postgres(
  'Apply Action',
  selectAs('mctb.apply_sms_decision($1::jsonb)', 'result'),
  '={{ JSON.stringify($json.record) }}',
  'Stores the estimate, status change, or mock reply.'
));
actionGraph.add(code('Prepare Action Response', `
const decided = $('Decide Action').first().json;
return [{ json: { html: decided.html || '' } }];
`, 'Sends the browser back to the dashboard.'));
actionGraph.add(respond('Respond Action', '={{ $json.html }}', 'text/html; charset=utf-8'));
actionGraph.link('Action Webhook', 'Read Action');
actionGraph.link('Read Action', 'Load Action Tenant');
actionGraph.link('Load Action Tenant', 'Decide Action');
actionGraph.link('Decide Action', 'Save Action');
actionGraph.link('Save Action', 'Apply Action', 0);
actionGraph.link('Save Action', 'Prepare Action Response', 1);
actionGraph.link('Apply Action', 'Prepare Action Response');
actionGraph.link('Prepare Action Response', 'Respond Action');
const actions = finish(actionGraph, 'BrightReach Dashboard Actions', 'mctbActions');

const CAPTURE_NOW = `
const current = $input.first().json || {};
const body = current.body || {};
const simulated = ($env.TWILIO_MODE || '') !== 'live' && body.now ? String(body.now) : '';
return [{ json: { now: simulated || new Date().toISOString() } }];
`;

const revenueGraph = workflowBuilder('mctbrev0');
revenueGraph.add({
  name: 'Every 15 Minutes',
  type: 'n8n-nodes-base.scheduleTrigger',
  typeVersion: 1.2,
  notes: 'Looks for a Monday 8am weekly report and a last-day-of-month summary in each shop time zone.',
  parameters: { rule: { interval: [{ field: 'minutes', minutesInterval: 15 }] } },
});
revenueGraph.add(webhook(
  'Run Revenue Webhook',
  'POST',
  'mctb-revenue-run',
  'mctb-revenue-hook',
  'Test trigger. body.now is honored only when TWILIO_MODE is not live.',
  'onReceived'
));
revenueGraph.add(code('Capture Revenue Now', CAPTURE_NOW, 'Clock for the shop-local Monday and month-end checks.'));
revenueGraph.add(postgres(
  'Load Due Reports',
  selectAs('mctb.due_revenue_reports($1::timestamptz)', 'reports'),
  '={{ $json.now }}',
  'Shops with the report toggle on, once per week and once per month.'
));
revenueGraph.add(code('Plan Reports', `
const reports = $input.first().json.reports || [];
const now = $('Capture Revenue Now').first().json.now;
const fanout = engine.planRevenueReports(reports, now, {
  MCTB_PUBLIC_BASE_URL: $env.MCTB_PUBLIC_BASE_URL || ''
});
return [{ json: { count: fanout.length, fanout: fanout } }];
`, 'Builds the SMS, skips opted-out owners, and waits out quiet hours. Each text stays under 320 characters.'));
revenueGraph.add(code('Expand Reports', `
const fanout = $input.first().json.fanout || [];
return fanout.map((item) => ({ json: item }));
`, 'One item per report.'));
revenueGraph.add(postgres(
  'Apply Report',
  selectAs('mctb.apply_revenue_report($1::jsonb)', 'result'),
  '={{ JSON.stringify($json) }}',
  'Records the period and queues the owner SMS. The follow-up pass sends the queue.',
  'independently'
));
revenueGraph.add(postgres(
  'Stamp Revenue',
  selectAs("mctb.stamp_heartbeat('revenue')", 'result'),
  '',
  'Health check uses this stamp. A shop with no report due still counts as a successful run.'
));
revenueGraph.link('Every 15 Minutes', 'Capture Revenue Now');
revenueGraph.link('Run Revenue Webhook', 'Capture Revenue Now');
revenueGraph.link('Capture Revenue Now', 'Load Due Reports');
revenueGraph.link('Load Due Reports', 'Plan Reports');
revenueGraph.link('Load Due Reports', 'Stamp Revenue');
revenueGraph.link('Plan Reports', 'Expand Reports');
revenueGraph.link('Expand Reports', 'Apply Report');
const revenue = finish(revenueGraph, 'BrightReach Recovered Revenue', 'mctbRevenue');

const reviewGraph = workflowBuilder('mctbrev1');
reviewGraph.add(webhook(
  'Review Click',
  'GET',
  'mctb-r',
  'mctb-review-hook',
  'Tracked Google review link. Logs the click, then redirects to the shop google_review_url. The path stays static: n8n only serves a :param path under the webhook id, which would 404 the URL in the text.'
));
reviewGraph.add(code('Read Review Token', `
const item = $input.first().json || {};
const query = item.query || {};
const raw = query.t;
const token = String(Array.isArray(raw) ? (raw[0] || '') : (raw || '')).trim();
return [{ json: { token: token } }];
`, 'Token from /webhook/mctb-r?t=<token>.'));
reviewGraph.add(postgres(
  'Log Review Click',
  selectAs('mctb.review_click($1::text)', 'click'),
  '={{ $json.token }}',
  'Sets clicked_at and returns the stored https review URL. Unknown tokens do not redirect.'
));
reviewGraph.add(code('Decide Review Redirect', `
const click = $input.first().json.click || {};
const location = typeof click.location === 'string' ? click.location : '';
const ok = click.ok === true && /^https:\\/\\//i.test(location);
return [{ json: { redirect: ok ? 'yes' : 'no', location: ok ? location : '', body: ok ? '' : 'Unknown review link' } }];
`, 'Only the URL stored on the shop is used.'));
reviewGraph.add(ifYes('Review Found', '={{ $json.redirect }}', 'Unknown or non-https links get a 404.'));
reviewGraph.add(respond('Redirect Review', '={{ $json.body }}', 'text/plain; charset=utf-8', {
  code: 302,
  headers: [{ name: 'Location', value: '={{ $json.location }}' }],
}));
reviewGraph.add(respond('Missing Review', '={{ $json.body }}', 'text/plain; charset=utf-8', { code: 404 }));
reviewGraph.link('Review Click', 'Read Review Token');
reviewGraph.link('Read Review Token', 'Log Review Click');
reviewGraph.link('Log Review Click', 'Decide Review Redirect');
reviewGraph.link('Decide Review Redirect', 'Review Found');
reviewGraph.link('Review Found', 'Redirect Review', 0);
reviewGraph.link('Review Found', 'Missing Review', 1);
const reviewClick = finish(reviewGraph, 'BrightReach Review Link', 'mctbReview');

const healthGraph = workflowBuilder('mctbhlth');
healthGraph.add(webhook(
  'Health Webhook',
  'GET',
  'mctb-health',
  'mctb-health-hook',
  'Public heartbeat for a free uptime monitor. 200 JSON when Postgres answers. ok is false when scheduled workflows are stale.'
));
healthGraph.add(postgres(
  'Load Heartbeat',
  selectAs('mctb.heartbeat()', 'health'),
  '',
  'Database reachability and the age of the last successful follow-up and revenue runs.'
));
healthGraph.add(code('Render Heartbeat', `
const row = $input.first().json.health || {};
const body = {
  ok: engine.heartbeatOk(row),
  db: row.db || 'error',
  version: engine.ENGINE_VERSION
};
return [{ json: { body: JSON.stringify(body) } }];
`, 'UptimeRobot can watch the HTTP status, or the JSON ok field.'));
healthGraph.add(respond('Respond Heartbeat', '={{ $json.body }}', 'application/json; charset=utf-8'));
healthGraph.add({
  name: 'Every 5 Minutes',
  type: 'n8n-nodes-base.scheduleTrigger',
  typeVersion: 1.2,
  notes: 'Alerts MCTB_ADMIN_PHONE and MCTB_ADMIN_EMAIL at most once an hour, then sends a recovery notice.',
  parameters: { rule: { interval: [{ field: 'minutes', minutesInterval: 5 }] } },
});
healthGraph.add(webhook(
  'Run Health Webhook',
  'POST',
  'mctb-health-run',
  'mctb-health-run-hook',
  'Test trigger for the alert pass. body.now is honored only when TWILIO_MODE is not live.',
  'onReceived'
));
healthGraph.add(code('Prep Health', `
const current = $input.first().json || {};
const body = current.body || {};
const simulated = ($env.TWILIO_MODE || '') !== 'live' && body.now ? String(body.now) : '';
return [{
  json: {
    now: simulated || new Date().toISOString(),
    admin_phone: engine.normalizePhone($env.MCTB_ADMIN_PHONE || '') || ''
  }
}];
`, 'Normalizes the admin phone before the database probe.'));
healthGraph.add(postgres(
  'Probe Health',
  selectAs('mctb.health_snapshot($1::text)', 'health'),
  '={{ $json.admin_phone }}',
  'Postgres, heartbeat ages, and the shop line used to text the admin.',
  null,
  { onError: 'continueErrorOutput' }
));
healthGraph.add(code('Read Health', `
const row = $input.first().json.health || null;
return [{ json: { facts: row || { db: 'down' } } }];
`, 'A failed probe becomes db down so the alert can still go out by email.'));
healthGraph.add(code('Decide Health', `
const facts = $input.first().json.facts || { db: 'down' };
const now = $('Prep Health').first().json.now;
const store = $getWorkflowStaticData('global');
const decision = engine.planHealthAlert(facts, store, now, {
  MCTB_ADMIN_PHONE: $env.MCTB_ADMIN_PHONE || '',
  MCTB_ADMIN_EMAIL: $env.MCTB_ADMIN_EMAIL || '',
  N8N_SMTP_HOST: $env.N8N_SMTP_HOST || '',
  SMTP_HOST: $env.SMTP_HOST || '',
  TWILIO_MODE: $env.TWILIO_MODE || '',
  TWILIO_ACCOUNT_SID: $env.TWILIO_ACCOUNT_SID || '',
  TWILIO_AUTH_TOKEN: $env.TWILIO_AUTH_TOKEN || ''
});
const patch = decision.staticPatch || {};
store.status = patch.status;
store.alertOpen = patch.alertOpen;
store.lastAlertAt = patch.lastAlertAt;
if (patch.sender) store.sender = patch.sender;
return [{ json: decision }];
`, 'At most one alert per hour. Quiet hours queue the SMS. Opted-out admin numbers are not texted. Email uses the n8n SMTP credential when N8N_SMTP_HOST or SMTP_HOST is set.'));
healthGraph.add(ifYes('Alerting', '={{ $json.save }}', 'Healthy checks with nothing open do not notify.'));
healthGraph.add(ifYes('Live Health SMS', '={{ $json.live_sms }}', 'Live mode posts to Twilio. Mock mode records the text in Postgres.'));
healthGraph.add({
  name: 'Send Health SMS',
  type: 'n8n-nodes-base.httpRequest',
  typeVersion: 4.2,
  onError: 'continueRegularOutput',
  notes: 'Direct Twilio send so an outage in the follow-up workflow cannot block the alert.',
  parameters: {
    method: 'POST',
    url: '={{ \'https://api.twilio.com/2010-04-01/Accounts/\' + $env.TWILIO_ACCOUNT_SID + \'/Messages.json\' }}',
    sendHeaders: true,
    headerParameters: {
      parameters: [{ name: 'Authorization', value: '={{ $json.auth }}' }],
    },
    sendBody: true,
    contentType: 'form-urlencoded',
    bodyParameters: {
      parameters: [
        { name: 'From', value: '={{ $json.record.sms.from }}' },
        { name: 'To', value: '={{ $json.record.sms.to }}' },
        { name: 'Body', value: '={{ $json.record.sms.body }}' },
      ],
    },
    options: { timeout: 30000 },
  },
});
healthGraph.add(ifYes('Email Health', '={{ $json.send_email }}', 'Skipped unless MCTB_ADMIN_EMAIL and an SMTP host env var are both set.'));
healthGraph.add({
  name: 'Send Health Email',
  type: 'n8n-nodes-base.emailSend',
  typeVersion: 2.1,
  onError: 'continueRegularOutput',
  notes: 'Uses the n8n SMTP credential. Fill it from N8N_SMTP_HOST, N8N_SMTP_PORT, N8N_SMTP_USER, N8N_SMTP_PASS, and N8N_SMTP_SENDER.',
  credentials: {
    smtp: {
      id: 'BRIGHTREACH_SMTP_CREDENTIAL_ID',
      name: 'BrightReach SMTP',
    },
  },
  parameters: {
    fromEmail: '={{ $env.N8N_SMTP_SENDER || $env.SMTP_FROM || \'missed-call@localhost\' }}',
    toEmail: '={{ $json.record.email.to }}',
    subject: '={{ $json.record.email.subject }}',
    emailFormat: 'text',
    text: '={{ $json.record.email.body }}',
    options: {},
  },
});
healthGraph.add(postgres(
  'Record Health',
  selectAs('mctb.record_health_alert($1::jsonb)', 'result'),
  '={{ JSON.stringify($json.record) }}',
  'Stores the alert latch. Mock texts are logged here. Quiet-hours texts are queued.',
  null,
  { onError: 'continueRegularOutput' }
));
healthGraph.link('Health Webhook', 'Load Heartbeat');
healthGraph.link('Load Heartbeat', 'Render Heartbeat');
healthGraph.link('Render Heartbeat', 'Respond Heartbeat');
healthGraph.link('Every 5 Minutes', 'Prep Health');
healthGraph.link('Run Health Webhook', 'Prep Health');
healthGraph.link('Prep Health', 'Probe Health');
healthGraph.link('Probe Health', 'Read Health', 0);
healthGraph.link('Probe Health', 'Read Health', 1);
healthGraph.link('Read Health', 'Decide Health');
healthGraph.link('Decide Health', 'Alerting');
healthGraph.link('Alerting', 'Live Health SMS', 0);
healthGraph.link('Alerting', 'Email Health', 0);
healthGraph.link('Alerting', 'Record Health', 0);
healthGraph.link('Live Health SMS', 'Send Health SMS', 0);
healthGraph.link('Email Health', 'Send Health Email', 0);
const health = finish(healthGraph, 'BrightReach Health', 'mctbHealth');

const outDir = path.join(root, 'workflows');
fs.mkdirSync(outDir, { recursive: true });
const files = [
  ['mctb_voice.json', voice],
  ['mctb_dial_status.json', dial],
  ['mctb_inbound_sms.json', sms],
  ['mctb_followups.json', followups],
  ['mctb_dashboard.json', dashboard],
  ['mctb_actions.json', actions],
  ['mctb_revenue.json', revenue],
  ['mctb_review_click.json', reviewClick],
  ['mctb_health.json', health],
];
files.forEach(([name, value]) => {
  fs.writeFileSync(path.join(outDir, name), JSON.stringify(value, null, 2) + '\n');
});
fs.mkdirSync(path.join(root, 'demo'), { recursive: true });
fs.copyFileSync(path.join(root, 'logic', 'engine.js'), path.join(root, 'demo', 'engine.js'));
process.stdout.write('wrote ' + files.length + ' workflows, engine ' + engineSha.slice(0, 12) + '\n');
