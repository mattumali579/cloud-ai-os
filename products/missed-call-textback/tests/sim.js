'use strict';

const { execFileSync } = require('child_process');
const vm = require('node:vm');

const DATABASE = process.env.MCTB_TEST_DATABASE || 'mctb_test';

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

function runCode(node, items, env, outputs, index) {
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
  };
  const result = script.runInNewContext(sandbox, { timeout: 8000 });
  if (!Array.isArray(result)) throw new Error(node.name + ' must return an array of items');
  return result;
}

function runWorkflow(workflow, triggerName, inputJson, env) {
  const nodes = {};
  workflow.nodes.forEach((node) => {
    nodes[node.name] = node;
  });
  const outputs = {};
  const httpLog = [];
  const responses = [];
  const environment = env || {};

  function exec(name, items) {
    const node = nodes[name];
    if (!node) throw new Error('missing node ' + name);
    let outItems = [];
    if (node.type === 'n8n-nodes-base.code') {
      outItems = runCode(node, items, environment, outputs, 0);
    } else if (node.type === 'n8n-nodes-base.postgres') {
      const batch = node.parameters.options && node.parameters.options.queryBatching;
      const runOne = (item, index) => {
        const replacement = node.parameters.options && node.parameters.options.queryReplacement;
        const params = replacement ? queryParams(replacement, item, environment, outputs, index) : [];
        const sql = bindQuery(node.parameters.query, params);
        const parsed = psqlJson(sql);
        return { json: parsed || {} };
      };
      outItems = batch === 'independently'
        ? items.map((item, index) => runOne(item, index))
        : [runOne(items[0] || { json: {} }, 0)];
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
    } else if (node.type === 'n8n-nodes-base.respondToWebhook') {
      const item = items[0] || { json: {} };
      responses.push({
        body: evalExpr(node.parameters.responseBody, item, environment, outputs, 0),
        contentType: (((node.parameters.options || {}).responseHeaders || {}).entries || []).map((entry) => entry.value).join('; '),
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
  return { outputs: outputs, httpLog: httpLog, responses: responses };
}

module.exports = {
  DATABASE: DATABASE,
  psql: psql,
  psqlJson: psqlJson,
  resetDatabase: resetDatabase,
  quoteJson: quoteJson,
  runWorkflow: runWorkflow,
};
