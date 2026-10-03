#!/usr/bin/env node
// Attach a Postgres credential to the imported BrightReach workflows and activate them.
// n8n's login API has changed across versions. If this fails, attach "BrightReach Postgres"
// in the editor. The product still runs; this script only saves clicking 11 nodes.

const base = (process.env.N8N_BASE_URL || 'http://127.0.0.1:5679').replace(/\/$/, '');
const email = process.env.N8N_EMAIL || '';
const password = process.env.N8N_PASSWORD || '';
const names = [
  'BrightReach Missed Call',
  'BrightReach Dial Result',
  'BrightReach Inbound SMS',
  'BrightReach Estimate Follow-ups',
  'BrightReach Dashboard',
  'BrightReach Dashboard Actions',
];

if (!email || !password) {
  console.error('Set N8N_EMAIL and N8N_PASSWORD (the n8n owner login).');
  process.exit(1);
}

const credentialData = {
  host: process.env.MCTB_PG_HOST || 'db',
  port: Number(process.env.MCTB_PG_PORT || 5432),
  database: process.env.MCTB_PG_DATABASE || 'cloudos',
  user: process.env.MCTB_PG_USER || 'cloudos',
  password: process.env.MCTB_PG_PASSWORD || '',
  ssl: 'disable',
  allowUnauthorizedCerts: false,
  maxConnections: 10,
};

if (!credentialData.password) {
  console.error('Set MCTB_PG_PASSWORD.');
  process.exit(1);
}

async function request(path, options, cookie) {
  const headers = Object.assign({ 'content-type': 'application/json' }, options.headers || {});
  if (cookie) headers.cookie = cookie;
  const response = await fetch(base + path, Object.assign({}, options, { headers: headers }));
  const text = await response.text();
  let body = null;
  if (text) {
    try { body = JSON.parse(text); } catch (error) { body = text; }
  }
  if (!response.ok) {
    throw new Error(response.status + ' ' + path + ' ' + text.slice(0, 400));
  }
  const setCookie = response.headers.getSetCookie ? response.headers.getSetCookie() : [];
  const raw = response.headers.get('set-cookie') || '';
  const cookieHeader = setCookie.length ? setCookie.map((item) => item.split(';')[0]).join('; ') : raw.split(';')[0];
  return { body: body, cookie: cookieHeader || cookie };
}

async function main() {
  const login = await request('/rest/login', {
    method: 'POST',
    body: JSON.stringify({ emailOrLdapLoginId: email, email: email, password: password }),
  });
  const cookie = login.cookie;
  if (!cookie) throw new Error('Login succeeded without a cookie. Attach the credential in the n8n editor instead.');

  const created = await request('/rest/credentials', {
    method: 'POST',
    body: JSON.stringify({
      name: 'BrightReach Postgres',
      type: 'postgres',
      data: credentialData,
    }),
  }, cookie);
  const credentialId = created.body && (created.body.id || (created.body.data && created.body.data.id));
  if (!credentialId) throw new Error('Credential response had no id: ' + JSON.stringify(created.body).slice(0, 400));

  const listed = await request('/rest/workflows?limit=200', { method: 'GET' }, cookie);
  const workflows = listed.body.data || listed.body || [];
  let touched = 0;
  for (const summary of workflows) {
    if (!names.includes(summary.name)) continue;
    const full = await request('/rest/workflows/' + summary.id, { method: 'GET' }, cookie);
    const workflow = full.body.data || full.body;
    (workflow.nodes || []).forEach((node) => {
      if (node.type !== 'n8n-nodes-base.postgres') return;
      node.credentials = { postgres: { id: credentialId, name: 'BrightReach Postgres' } };
    });
    await request('/rest/workflows/' + summary.id, {
      method: 'PATCH',
      body: JSON.stringify({ nodes: workflow.nodes, connections: workflow.connections }),
    }, cookie).catch(async () => request('/rest/workflows/' + summary.id, {
      method: 'PUT',
      body: JSON.stringify(workflow),
    }, cookie));
    await request('/rest/workflows/' + summary.id + '/activate', { method: 'POST', body: '{}' }, cookie)
      .catch(() => request('/rest/workflows/' + summary.id, {
        method: 'PATCH',
        body: JSON.stringify({ active: true }),
      }, cookie));
    touched += 1;
    console.log('linked and activated ' + summary.name);
  }
  if (!touched) {
    console.error('No BrightReach workflows were found. Import them first.');
    process.exit(1);
  }
  console.log('Postgres credential id ' + credentialId + ' attached to ' + touched + ' workflows.');
}

main().catch((error) => {
  console.error(error.message);
  console.error('Attach credential "BrightReach Postgres" to each Postgres node in the editor, then activate the six workflows.');
  process.exit(1);
});
