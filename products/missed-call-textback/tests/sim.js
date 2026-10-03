'use strict';

const { execFileSync } = require('child_process');
const vm = require('node:vm');

const DATABASE = process.env.MCTB_TEST_DATABASE || 'mctb_test';
const staticStores = new Map();

function resetWorkflowStaticData() {
  staticStores.clear();
}

function staticBag(workflow) {
  const id = (workflow && (workflow.id || workflow.name)) || 'workflow';
  if (!staticStores.has(id)) staticStores.set(id, { global: {}, node: {} });
  return staticStores.get(id);
}

function psql(database, sql) {
  return execFileSync(
    'sudo',
    ['-n', '-u', 'postgres', 'psql', '-d', database, '-v', 'ON_ERROR_STOP=1', '-q', '-t', '-A', '-c', sql],
    { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 }
  ).trim();
}

function psqlJson(sql) {
  const raw = psql(DATABASE, sql);
  if (!raw) return null;
  return JSON.parse(raw);
}

function resetDatabase(schemaPath) {
  psql('postgres', "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '" + DATABASE + "' AND pid <> pg_backend_pid();");
  psql('postgres', 'DROP DATABASE IF EXISTS ' + DATABASE + ';');
  psql('postgres', 'CREATE DATABASE ' + DATABASE + ';');
  execFileSync(
    'sudo',
    ['-n', '-u', 'postgres', 'psql', '-d', DATABASE, '-v', 'ON_ERROR_STOP=1', '-q', '-f', schemaPath],
    { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 }
  );
}

function quoteJson(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  if (text.includes('$mctb7f3a$')) throw new Error('test payload collided with the SQL delimiter');
  return '$mctb7f3a$' + text + '$mctb7f3a$';
}

function columnKey(query) {
  const trimmed = String(query || '').trim().replace(/;\s*$/, '');
  const quoted = trimmed.match(/\bAS\s+"([^"]+)"\s*$/i);
  if (quoted) return quoted[1];
  const bare = trimmed.match(/\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$/i);
  if (bare) return bare[1];
  const fn = trimmed.match(/\bSELECT\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\(/i);
  return fn ? fn[1] : 'column';
}

function postgresItem(query, raw) {
  const key = columnKey(query);
  if (raw == null || String(raw).trim() === '') return { json: { [key]: null } };
  let value = String(raw).trim();
  try {
    value = JSON.parse(value);
  } catch (err) {
    value = String(raw).trim();
  }
  return { json: { [key]: value } };
}

function bindQuery(query, params) {
  return query.replace(/\$(\d+)(::[a-zA-Z_]+)?/g, (_, number, cast) => {
    const value = params[Number(number) - 1];
    if (value == null) return 'NULL';
    return quoteJson(value) + (cast || '');
  });
}

function makeDollar(outputs, index) {
  return function dollar(name) {
    const produced = outputs[name];
    if (!produced) throw new Error('node has not run yet: ' + name);
    return {
      first: () => produced[0],
      all: () => produced,
      item: produced[index] || produced[0],
    };
  };
}

function runExpression(code, item, env, outputs, index) {
  const sandbox = {
    $json: item ? item.json : {},
    $env: env,
    $: makeDollar(outputs, index),
    JSON: JSON,
    Buffer: Buffer,
    Date: Date,
    Object: Object,
    Number: Number,
    String: String,
    Boolean: Boolean,
    encodeURIComponent: encodeURIComponent,
    console: console,
  };
  return vm.runInNewContext(code, sandbox, { timeout: 5000 });
}

function evalExpr(expr, item, env, outputs, index) {
  if (typeof expr !== 'string') return expr;
  if (!expr.startsWith('=')) return expr;
  const body = expr.slice(1).trim();
  const single = body.match(/^\{\{([\s\S]*)\}\}$/);
  if (single) return runExpression(single[1], item, env, outputs, index);
  return body.replace(/\{\{([\s\S]+?)\}\}/g, (_, code) => String(runExpression(code, item, env, outputs, index)));
}

function queryParams(expr, item, env, outputs, index) {
  const body = String(expr || '').replace(/^=/, '');
  const params = [];
  const pattern = /\{\{([\s\S]+?)\}\}/g;
  let match = pattern.exec(body);
  while (match) {
    params.push(runExpression(match[1], item, env, outputs, index));
    match = pattern.exec(body);
  }
  return params;
}

function runCode(node, items, env, outputs, index, workflow) {
  const jsCode = node.parameters.jsCode;
  const script = new vm.Script('(function(){\n' + jsCode + '\n})()', { filename: node.name + '.js' });
  const sandbox = {
    $json: (items[0] || { json: {} }).json,
    $env: env,
    $input: {
      first: () => items[0] || { json: {} },
      all: () => items,
    },
    $: makeDollar(outputs, index || 0),
    Buffer: Buffer,
    JSON: JSON,
    Date: Date,
    Object: Object,
    Number: Number,
    String: String,
    console: console,
    encodeURIComponent: encodeURIComponent,
    $getWorkflowStaticData: (scope) => {
      const bag = staticBag(workflow);
      return scope === 'node' ? bag.node : bag.global;
    },
  };
  const result = script.runInNewContext(sandbox, { timeout: 8000 });
  if (!Array.isArray(result)) throw new Error(node.name + ' must return an array of items');
  return result;
}

function pathHasParam(webhookPath) {
  return String(webhookPath || '').split('/').some((part) => part.startsWith(':'));
}

// n8n mounts a webhook path that contains :param under the node's webhook id.
// /webhook/mctb-r/:token is served at /webhook/<webhookId>/mctb-r/<token> and 404s
// at /webhook/mctb-r/<token>. A path with no colon is served at /webhook/<path>.
function registeredWebhookPath(node) {
  const raw = String((node.parameters && node.parameters.path) || '').replace(/^\/+|\/+$/g, '');
  if (!pathHasParam(raw)) return raw;
  if (!node.webhookId) return null;
  return node.webhookId + '/' + raw;
}

function requestPathname(requestPath) {
  const pathOnly = String(requestPath || '').split('?')[0];
  return pathOnly.replace(/^\/+/, '').replace(/^webhook\//, '').replace(/\/+$/, '');
}

function matchPathPattern(pattern, actual) {
  const expected = String(pattern || '').split('/');
  const got = String(actual || '').split('/');
  if (!pattern || expected.length !== got.length) return null;
  const params = {};
  for (let i = 0; i < expected.length; i += 1) {
    if (expected[i].startsWith(':')) {
      params[expected[i].slice(1)] = decodeURIComponent(got[i]);
    } else if (expected[i] !== got[i]) {
      return null;
    }
  }
  return params;
}

function matchWebhook(workflow, method, requestPath) {
  const wanted = String(method || 'GET').toUpperCase();
  const actual = requestPathname(requestPath);
  const nodes = (workflow && workflow.nodes) || [];
  for (let i = 0; i < nodes.length; i += 1) {
    const node = nodes[i];
    if (node.type !== 'n8n-nodes-base.webhook') continue;
    const nodeMethod = String((node.parameters && node.parameters.httpMethod) || 'GET').toUpperCase();
    if (nodeMethod !== wanted) continue;
    const pattern = registeredWebhookPath(node);
    const params = matchPathPattern(pattern, actual);
    if (params) return { node: node, params: params };
  }
  return null;
}

function parseRequestTarget(requestUrl) {
  const absolute = /^https?:\/\//i.test(requestUrl) ? requestUrl : 'https://n8n.local' + (String(requestUrl).startsWith('/') ? '' : '/') + requestUrl;
  const url = new URL(absolute);
  const query = {};
  url.searchParams.forEach((value, key) => {
    if (Object.prototype.hasOwnProperty.call(query, key)) {
      query[key] = Array.isArray(query[key]) ? query[key].concat(value) : [query[key], value];
    } else {
      query[key] = value;
    }
  });
  return { pathname: url.pathname, query: query };
}

function runWebhook(workflow, method, requestUrl, env, body) {
  const target = parseRequestTarget(requestUrl);
  const matched = matchWebhook(workflow, method, target.pathname);
  if (!matched) {
    return {
      matched: false,
      outputs: {},
      httpLog: [],
      emailLog: [],
      responses: [{ statusCode: 404, body: 'not found', headers: {}, contentType: '' }],
    };
  }
  const result = runWorkflow(workflow, matched.node.name, {
    headers: {},
    params: matched.params,
    query: target.query,
    body: body || {},
  }, env);
  result.matched = true;
  return result;
}

function runWorkflow(workflow, triggerName, inputJson, env) {
  const nodes = {};
  workflow.nodes.forEach((node) => {
    nodes[node.name] = node;
  });
  const outputs = {};
  const httpLog = [];
  const emailLog = [];
  const responses = [];
  const environment = env || {};

  function exec(name, items) {
    const node = nodes[name];
    if (!node) throw new Error('missing node ' + name);
    let outItems = [];
    if (node.type === 'n8n-nodes-base.code') {
      outItems = runCode(node, items, environment, outputs, 0, workflow);
    } else if (node.type === 'n8n-nodes-base.postgres') {
      const batch = node.parameters.options && node.parameters.options.queryBatching;
      const runOne = (item, index) => {
        const replacement = node.parameters.options && node.parameters.options.queryReplacement;
        const params = replacement ? queryParams(replacement, item, environment, outputs, index) : [];
        const sql = bindQuery(node.parameters.query, params);
        return postgresItem(node.parameters.query, psql(DATABASE, sql));
      };
      try {
        if (environment.MCTB_SIM_DB_DOWN === '1') throw new Error('simulated database outage');
        outItems = batch === 'independently'
          ? items.map((item, index) => runOne(item, index))
          : [runOne(items[0] || { json: {} }, 0)];
      } catch (err) {
        const message = String(err && err.message ? err.message : err);
        if (node.onError === 'continueErrorOutput') {
          const errorItems = [{ json: { error: message } }];
          outputs[name] = errorItems;
          const branches = (workflow.connections[name] && workflow.connections[name].main) || [];
          (branches[1] || []).forEach((edge) => exec(edge.node, errorItems));
          return;
        }
        if (node.onError === 'continueRegularOutput') outItems = [{ json: { error: message } }];
        else throw err;
      }
    } else if (node.type === 'n8n-nodes-base.if') {
      const truthy = [];
      const falsy = [];
      items.forEach((item, index) => {
        const condition = node.parameters.conditions.conditions[0];
        const left = evalExpr(condition.leftValue, item, environment, outputs, index);
        if (String(left) === String(condition.rightValue)) truthy.push(item);
        else falsy.push(item);
      });
      const branches = (workflow.connections[name] && workflow.connections[name].main) || [];
      (branches[0] || []).forEach((edge) => {
        if (truthy.length) exec(edge.node, truthy);
      });
      (branches[1] || []).forEach((edge) => {
        if (falsy.length) exec(edge.node, falsy);
      });
      outputs[name] = items;
      return;
    } else if (node.type === 'n8n-nodes-base.httpRequest') {
      outItems = items.map((item, index) => {
        const url = evalExpr(node.parameters.url, item, environment, outputs, index);
        const body = node.parameters.jsonBody
          ? evalExpr(node.parameters.jsonBody, item, environment, outputs, index)
          : null;
        httpLog.push({ node: name, url: url, body: body, from: item.json.from, to: item.json.to, text: item.json.body });
        if (String(url).includes('api.twilio.com')) return { json: { sid: 'SM_TEST', status: 'queued' } };
        return { json: { ok: true } };
      });
    } else if (node.type === 'n8n-nodes-base.emailSend') {
      outItems = items.map((item, index) => {
        const from = evalExpr(node.parameters.fromEmail, item, environment, outputs, index);
        const to = evalExpr(node.parameters.toEmail, item, environment, outputs, index);
        const subject = evalExpr(node.parameters.subject, item, environment, outputs, index);
        const text = evalExpr(node.parameters.text || node.parameters.html, item, environment, outputs, index);
        emailLog.push({ node: name, from: from, to: to, subject: subject, text: text });
        return { json: { ok: true } };
      });
    } else if (node.type === 'n8n-nodes-base.respondToWebhook') {
      const item = items[0] || { json: {} };
      const options = node.parameters.options || {};
      const entries = ((options.responseHeaders || {}).entries) || [];
      const headers = {};
      entries.forEach((entry) => {
        headers[entry.name] = evalExpr(entry.value, item, environment, outputs, 0);
      });
      const rawCode = options.responseCode == null ? 200 : options.responseCode;
      const statusCode = typeof rawCode === 'number' ? rawCode : Number(evalExpr(String(rawCode), item, environment, outputs, 0));
      responses.push({
        body: evalExpr(node.parameters.responseBody, item, environment, outputs, 0),
        statusCode: statusCode,
        headers: headers,
        contentType: headers['Content-Type'] || '',
      });
      outputs[name] = items;
      return;
    } else {
      throw new Error('simulator does not implement ' + node.type + ' (' + name + ')');
    }
    outputs[name] = outItems;
    if (!outItems.length) return;
    const next = (workflow.connections[name] && workflow.connections[name].main && workflow.connections[name].main[0]) || [];
    next.forEach((edge) => exec(edge.node, outItems));
  }

  outputs[triggerName] = [{ json: inputJson }];
  const next = (workflow.connections[triggerName] && workflow.connections[triggerName].main[0]) || [];
  next.forEach((edge) => exec(edge.node, outputs[triggerName]));
  return { outputs: outputs, httpLog: httpLog, emailLog: emailLog, responses: responses };
}

module.exports = {
  DATABASE: DATABASE,
  psql: psql,
  psqlJson: psqlJson,
  resetDatabase: resetDatabase,
  quoteJson: quoteJson,
  columnKey: columnKey,
  postgresItem: postgresItem,
  runWorkflow: runWorkflow,
  runWebhook: runWebhook,
  matchWebhook: matchWebhook,
  pathHasParam: pathHasParam,
  resetWorkflowStaticData: resetWorkflowStaticData,
};
