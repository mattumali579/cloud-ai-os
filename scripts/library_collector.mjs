#!/usr/bin/env node
/**
 * Incremental Intelligence Library collector for n8n (Node in the n8n image).
 * Free official APIs and feeds only. Metadata and short original summaries —
 * never full copyrighted text.
 *
 * ID = sha256(lower(canonical_url).rstrip('/') + '|' + normalized_title)[:16]
 * Content identity = sha256(normalized_title + '|' + normalized_creator)[:16]
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';

const UA = 'IntelligenceLibrary/0.1 (research; +https://localhost)';
const TODAY = new Date().toISOString().slice(0, 10);
const MAX_ABSTRACT = 420;
const COLUMNS = [
  'ID', 'Title', 'Category', 'Source URL', 'Organization', 'Event/Opportunity Date',
  'Location', 'Cost/Compensation', 'Deadline', 'Date Discovered', 'Last Verified',
  'Eligibility', 'Why It Matters', 'Why Matt Qualifies', 'Relevance Score', 'Priority',
  'Next Action', 'Status', 'Notification History', 'Evidence/Notes', 'Contributor',
  'Canonical URL', 'Creator/Publisher', 'Resource Type', 'Collection', 'Topic Tags',
  'Summary', 'Key Takeaways/What You Can Do', 'Difficulty', 'Geography',
  'Publication Date', 'Cost', 'Requirements', 'Relevance', 'Confidence',
  'Verification Status', 'Related Collections/Topic', 'Discovery Source', 'Content Identity',
];

const POS = /\b(construction|constructor|jobsite|job site|excavator|earthwork|estimat|takeoff|take-off|preconstruction|pre-construction|bim\b|building information|digital twin|ifc\b|openbim|quantity survey|general contractor|project engineer|superintendent|osha|real estate|real-estate|multifamily|pro forma|cap rate|development finance|entitlement|zoning|industrial real|data.?center|lidar|photogrammetr|scan.?to.?bim|navisworks|revit|bluebeam|procore|autodesk|robotic(?:s)?|teleoperat|autonomous (?:excav|vehicle|construction|heavy)|machine control|lean construction|last planner|design-build|cm at risk|concrete|rebar|prefab|modular construction|mass timber|construction management|construction spending|housing start)\b/ig;
const NEG = /\b(neural architecture search|compiler design|instruction set|protein folding|cryptocurrency|nft\b|anime|minecraft)\b/i;
const TECH = /\b(robotic|excavator|lidar|digital twin|bim\b|ifc\b|scan-to-bim|machine control|vision|data.?center|prefab|teleoperat|omniverse)\b/i;

const YT_CHANNELS = {
  'UC6n8I1UDTKP1IWjQMg6_TwA': 'The B1M',
  'UCMOqf8ab-42UUQIdVoKwjlQ': 'Practical Engineering',
};

const PODCAST_FEEDS = [
  { url: 'https://www.constructiondive.com/feeds/news/', name: 'Construction Dive', collection: 'A' },
  { url: 'https://www.enr.com/rss/all', name: 'Engineering News-Record', collection: 'B' },
];

const AGENCY_FEEDS = [
  { url: 'https://www.bls.gov/feed/bls_latest.rss', name: 'BLS', collection: 'B' },
  { url: 'https://www.census.gov/economic-indicators/indicator.xml', name: 'U.S. Census Bureau', collection: 'B' },
];

function normSpace(s) { return String(s || '').trim().replace(/\s+/g, ' '); }
function normTitle(s) { return normSpace(s).toLowerCase(); }

function canonicalUrl(url) {
  url = String(url || '').trim();
  if (!url) return '';
  try {
    const u = new URL(url);
    const drop = new Set(['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'fbclid', 'gclid']);
    [...u.searchParams.keys()].forEach((k) => { if (drop.has(k.toLowerCase())) u.searchParams.delete(k); });
    u.hash = '';
    u.hostname = u.hostname.toLowerCase();
    u.protocol = (u.protocol || 'https:').toLowerCase();
    let out = u.toString();
    if (out.endsWith('/')) out = out.slice(0, -1);
    return out;
  } catch {
    return url.replace(/\/$/, '');
  }
}

function sha16(raw) {
  return crypto.createHash('sha256').update(raw, 'utf8').digest('hex').slice(0, 16);
}
function makeId(url, title) {
  return sha16(`${canonicalUrl(url).toLowerCase()}|${normTitle(title)}`);
}
function contentIdentity(title, creator) {
  return sha16(`${normTitle(title)}|${normSpace(creator).toLowerCase()}`);
}
function scoreRelevance(title, text = '') {
  const blob = `${title} ${text}`;
  if (NEG.test(blob) && !POS.test(title)) return 0;
  POS.lastIndex = 0;
  const hits = (blob.match(POS) || []).length;
  if (!hits) return 0;
  let score = Math.min(10, 4 + hits);
  if (/\b(estimat|takeoff|bim|digital twin|excavator|internship|pro forma)\b/i.test(blob)) {
    score = Math.min(10, score + 1);
  }
  return score;
}

function mapCollection(letter, title, summary, rtype) {
  const blob = `${title} ${summary}`;
  if (letter && 'ABCDEFG'.includes(letter)) {
    if (letter === 'B' && TECH.test(blob) && rtype === 'paper') return 'D';
    return letter;
  }
  if (rtype === 'video') return 'A';
  if (rtype === 'repo' || TECH.test(blob)) return 'D';
  if (rtype === 'course') return 'G';
  return 'B';
}

function rec(kwargs) {
  const title = normSpace(kwargs.Title || '');
  const url = canonicalUrl(kwargs['Canonical URL'] || kwargs['Source URL'] || '');
  const creator = normSpace(kwargs['Creator/Publisher'] || kwargs.Organization || '');
  const summary = normSpace(kwargs.Summary || '').slice(0, MAX_ABSTRACT);
  const relevance = Number(kwargs.Relevance || scoreRelevance(title, summary));
  const collection = mapCollection(kwargs.Collection, title, summary, kwargs['Resource Type']);
  return {
    ID: makeId(url, title),
    Title: title,
    Category: kwargs.Category || '',
    'Source URL': url,
    Organization: creator,
    'Event/Opportunity Date': kwargs['Event/Opportunity Date'] || kwargs['Publication Date'] || '',
    Location: kwargs.Location || kwargs.Geography || '',
    'Cost/Compensation': kwargs['Cost/Compensation'] || kwargs.Cost || '',
    Deadline: kwargs.Deadline || '',
    'Date Discovered': kwargs['Date Discovered'] || TODAY,
    'Last Verified': kwargs['Last Verified'] || TODAY,
    Eligibility: kwargs.Eligibility || kwargs.Requirements || '',
    'Why It Matters': kwargs['Why It Matters'] || '',
    'Why Matt Qualifies': kwargs['Why Matt Qualifies'] || '',
    'Relevance Score': relevance,
    Priority: kwargs.Priority || (relevance >= 7 ? 'Relevant' : 'Low'),
    'Next Action': kwargs['Next Action'] || '',
    Status: kwargs.Status || 'Open',
    'Notification History': kwargs['Notification History'] || `Added ${TODAY} by n8n collector.`,
    'Evidence/Notes': kwargs['Evidence/Notes'] || '',
    Contributor: 'n8n',
    'Canonical URL': url,
    'Creator/Publisher': creator,
    'Resource Type': kwargs['Resource Type'] || '',
    Collection: collection,
    'Topic Tags': kwargs['Topic Tags'] || '',
    Summary: summary,
    'Key Takeaways/What You Can Do': kwargs['Key Takeaways/What You Can Do'] || '',
    Difficulty: kwargs.Difficulty || 'intermediate',
    Geography: kwargs.Geography || kwargs.Location || 'Global',
    'Publication Date': kwargs['Publication Date'] || '',
    Cost: kwargs.Cost || kwargs['Cost/Compensation'] || 'Free to access metadata',
    Requirements: kwargs.Requirements || '',
    Relevance: relevance,
    Confidence: kwargs.Confidence || 8,
    'Verification Status': kwargs['Verification Status'] || 'Verified',
    'Related Collections/Topic': kwargs['Related Collections/Topic'] || '',
    'Discovery Source': kwargs['Discovery Source'] || '',
    'Content Identity': contentIdentity(title, creator),
  };
}

async function fetchText(url, { timeout = 20000, accept = '*/*' } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  try {
    const resp = await fetch(url, {
      headers: { 'User-Agent': UA, Accept: accept },
      signal: ctrl.signal,
      redirect: 'follow',
    });
    const text = await resp.text();
    return { status: resp.status, text };
  } catch {
    return { status: 0, text: '' };
  } finally {
    clearTimeout(t);
  }
}

async function fetchJson(url, opts) {
  const { status, text } = await fetchText(url, { ...opts, accept: 'application/json' });
  if (status < 200 || status >= 300 || !text) return null;
  try { return JSON.parse(text); } catch { return null; }
}

async function httpStatus(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  try {
    const head = await fetch(url, { method: 'HEAD', headers: { 'User-Agent': UA }, signal: ctrl.signal, redirect: 'follow' });
    return head.status;
  } catch {
    const get = await fetchText(url, { timeout: 8000 });
    return get.status;
  } finally {
    clearTimeout(t);
  }
}

function loadIds(file) {
  const ids = new Set();
  const cis = new Set();
  if (!file || !fs.existsSync(file)) return { ids, cis };
  const data = JSON.parse(fs.readFileSync(file, 'utf8'));
  const raw = data.ids || data;
  if (Array.isArray(raw)) raw.forEach((x) => ids.add(typeof x === 'string' ? x : x[0]));
  else Object.keys(raw).forEach((k) => ids.add(k));
  (data.content_identities || []).forEach((c) => cis.add(c));
  return { ids, cis };
}

function loadQueue(file) {
  if (!file || !fs.existsSync(file)) return [];
  const data = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (Array.isArray(data.topics)) {
    return data.topics.map((t) => ({
      topic: t.topic || t.Topic || '',
      collection: t.collection || t.Collection || '',
    })).filter((t) => t.topic);
  }
  if (Array.isArray(data)) {
    return data.map((t) => ({ topic: t.topic || t[1] || t[0], collection: t.collection || t[0] || '' }));
  }
  return [];
}

function loadState(file) {
  if (!file || !fs.existsSync(file)) {
    return { topic_index: 0, openalex: {}, arxiv: {}, crossref: {}, github: {} };
  }
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch {
    return { topic_index: 0, openalex: {}, arxiv: {}, crossref: {}, github: {} };
  }
}

function invertAbstract(inv) {
  if (!inv || typeof inv !== 'object') return '';
  const pairs = [];
  for (const [word, positions] of Object.entries(inv)) {
    for (const pos of positions || []) pairs.push([pos, word]);
  }
  pairs.sort((a, b) => a[0] - b[0]);
  return normSpace(pairs.map((p) => p[1]).join(' ')).slice(0, MAX_ABSTRACT);
}

function parseRssItems(xml, limit = 12) {
  if (!xml) return [];
  const items = [];
  const chunks = xml.split(/<item[\s>]/i).slice(1);
  for (const chunk of chunks) {
    const title = (chunk.match(/<title[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/i) || [])[1] || '';
    const link = (chunk.match(/<link[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/link>/i) || [])[1] || '';
    const alt = (chunk.match(/<id>([\s\S]*?)<\/id>/i) || [])[1] || '';
    const summary = (chunk.match(/<(?:description|summary|media:description)[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/(?:description|summary|media:description)>/i) || [])[1] || '';
    const published = (chunk.match(/<(?:pubDate|published|updated)[^>]*>([\s\S]*?)<\/(?:pubDate|published|updated)>/i) || [])[1] || '';
    const clean = (s) => normSpace(String(s).replace(/<[^>]+>/g, ''));
    items.push({
      title: clean(title),
      url: clean(link || alt),
      summary: clean(summary).slice(0, MAX_ABSTRACT),
      published: clean(published).slice(0, 32),
    });
    if (items.length >= limit) break;
  }
  return items.filter((i) => i.title && i.url);
}

// --- Relevance gate (2026-11-23, GB-2) -------------------------------------
// Root cause of ~95% off-topic output: relevance was scored on title+summary
// PLUS the Research Queue topic text (e.g. "Procore", "ULI"), and GitHub/
// Wikipedia/YouTube/feed items got a floor of 6, so almost anything a search
// returned passed. The gate below re-scores on the item's own title/summary
// only and requires a real construction / real-estate / precon / AEC-tech /
// Gulf-region signal. Trusted construction publishers bypass the domain test.
const GATE_DOM = /\b(construction|contractor|subcontractor|jobsite|job site|building information model|bim\b|scan.?to.?bim|takeoff|take-off|preconstruction|pre-construction|quantity survey|cost estimat|estimator|estimating|bid(?:ding)? (?:process|strateg)|real estate|real-estate|multifamily|single-family|housing (?:market|development|construction|supply|starts?|afford)|homebuild|zoning|land use|entitlement|commercial property|cap rate|pro forma|lean construction|last planner|modular (?:construction|building|home)|prefabricat|off-?site construction|mass timber|reinforced concrete|concrete (?:structure|construction|strength|mix|pavement|crack|printing)|3d.?printed (?:concrete|building|house)|rebar|excavator|earthwork|heavy equipment|osha|procore|revit|navisworks|autodesk|bluebeam|data.?center (?:construction|design|campus|build)|construction robot|digital twin|design-build|openbim|ifc\b|boq\b|aec\b|aeco\b|civil engineer|infrastructure (?:project|investment|bill|spending)|bridge (?:construction|design|inspection|rehab)|highway (?:construction|project)|building code|building envelope|hvac|facility management|property (?:management|development|technology)|proptech|contech|architecture firm|general contractor|project manag\w* (?:in|for) construction|superintendent|louisiana|baton rouge|new orleans|lafayette|lake charles|gulf coast|hurricane (?:resilien|damage|recovery)|levee|coastal restoration|internship|apprentice)/i;
const GATE_CTX = /\b(building|project|contractor|site|civil|infrastructure|cost|safety|worker|labor|industry|facility|bridge|structur|aec|architect|engineer|construction|estimat|real estate|housing|property|developer|home|tenant|lease|permit|louisiana|gulf)/i;
const GATE_BAD = /\b(surgery|surgical|patient|clinical|hospitali[sz]|protein|probiotic|enzyme|quantum|lattice|graph construction|feature construction|tree construction|code construction|radio map|polysaccharide|cell(?:s|ular)?\b|tumou?r|cancer|genom|gene expression|algebra|theorem|lemma|manifold|curvature|topolog|obituar|neural architecture search|compiler|cryptocurrency|nft\b|anime|minecraft|video game|poetry|novel\b|theolog|liturg|dental|veterinar|livestock|plant growth|soil microb|mouse model|mice\b|rats?\b|in vitro|in vivo|vaccine|virus|covid|diabetes|alzheimer|brain|neuro\w*|aircraft|aviation|galax\w*|astronom\w*|spacecraft)/i;
const TRUSTED_FEEDS = new Set(['Construction Dive', 'Engineering News-Record', 'The B1M', 'Practical Engineering']);
const GATE = { checked: 0, kept: 0, rejected: {} };
function isPlaceholderSummary(s) {
  return /^(Scholarly work on |Crossref-indexed work on |Public video from |Podcast\/publication RSS item from |Agency RSS item from )/i.test(s || '');
}
function relevanceGate(item) {
  GATE.checked += 1;
  const reject = (why) => { GATE.rejected[why] = (GATE.rejected[why] || 0) + 1; return false; };
  const title = item.Title || '';
  const summary = isPlaceholderSummary(item.Summary) ? '' : (item.Summary || '');
  const blob = `${title} ${summary}`;
  const rtype = item['Resource Type'] || '';
  const trusted = TRUSTED_FEEDS.has(item['Creator/Publisher']);
  if (GATE_BAD.test(title) || GATE_BAD.test(summary.slice(0, 240))) return reject('off_topic_term');
  if (!trusted) {
    if (rtype === 'paper') {
      if (!GATE_DOM.test(title)) return reject('no_domain_in_title');
      if (summary.length < 80) return reject('no_abstract');
      const y = String(item['Publication Date'] || '').match(/(19|20)\d\d/);
      if (!y || Number(y[0]) < 2015) return reject('too_old');
    } else if (rtype === 'repo') {
      const stars = Number((String(item['Evidence/Notes'] || '').match(/stars=(\d+)/) || [])[1] || 0);
      if (stars < 10) return reject('repo_low_stars');
      if (!GATE_DOM.test(summary)) return reject('repo_no_domain_in_description');
    } else if (!GATE_DOM.test(title) && !(GATE_DOM.test(summary) && GATE_CTX.test(title))) {
      return reject('no_domain');
    }
    if (!GATE_CTX.test(blob)) return reject('no_context');
  }
  // Re-score WITHOUT the queue topic text and without artificial floors.
  let rel = scoreRelevance(title, summary);
  if (trusted) rel = Math.max(rel, 6);
  const minRel = rtype === 'paper' ? 7 : 6;
  if (rel < minRel) return reject('low_relevance');
  item.Relevance = rel;
  item['Relevance Score'] = rel;
  item.Priority = rel >= 7 ? 'Relevant' : 'Low';
  GATE.kept += 1;
  return true;
}

function tryAdd(seen, records, item, minRel = 5) {
  if (!item || !item.Title || !item['Canonical URL']) return false;
  if (item.Relevance < minRel && !TRUSTED_FEEDS.has(item['Creator/Publisher'])) return false;
  if (seen.ids.has(item.ID) || seen.cis.has(item['Content Identity'])) return false;
  if (!relevanceGate(item)) return false;
  seen.ids.add(item.ID);
  seen.cis.add(item['Content Identity']);
  records.push(item);
  return true;
}


// Turn a Research Queue phrase into a focused literature query (GB-2).
// Verbose phrases ("Revit and Navisworks learning paths") and bare company
// names ("ULI", "Procore") matched unrelated math/medical work; strip filler,
// and anchor queries with no construction/real-estate term to "construction".
const QUERY_STOP = new Set(['and', 'or', 'the', 'of', 'for', 'to', 'in', 'at', 'a', 'an', 'as', 'by', 'with', 'on', 'learning', 'paths', 'path', 'basics', 'guides', 'guide', 'refreshers', 'explainers', 'explainer', 'primers', 'primer', 'events', 'event', 'activity', 'maps', 'map', 'firms', 'videos', 'video', 'demos', 'demo', 'tutorials', 'interviews', 'podcasts', 'style', 'entry', 'path', 'students', 'student', 'reading', 'use', 'cases', 'case', 'deployments', 'manufacturers', 'programs', 'program', 'meetups', 'roles', 'based', 'free', 'tier', 'tools', 'literature', 'papers', 'research', 'studies', 'methods', 'data', 'reports', 'guidance', 'publications', 'journal']);
function literatureQuery(topic) {
  const words = String(topic || '').replace(/\([^)]*\)/g, ' ').replace(/[^A-Za-z0-9\- ]+/g, ' ')
    .split(/\s+/).filter((w) => w && !QUERY_STOP.has(w.toLowerCase()));
  let q = words.slice(0, 6).join(' ');
  if (!GATE_DOM.test(q)) q = `${q} construction`.trim();
  return q || 'construction';
}

async function collectOpenAlex(topic, collection, seen, records, state, perPage) {
  const pageKey = `${topic}|v2`;
  const cursor = state.openalex[pageKey] || '*';
  if (cursor === 'done') return 0;
  const params = new URLSearchParams({
    search: literatureQuery(topic),
    per_page: String(perPage),
    filter: 'type:article|review|preprint,language:en,has_abstract:true,from_publication_date:2015-01-01',
    cursor,
  });
  const data = await fetchJson(`https://api.openalex.org/works?${params}`);
  if (!data || !Array.isArray(data.results)) return 0;
  let n = 0;
  for (const w of data.results) {
    const title = w.display_name || w.title || '';
    const url = (w.primary_location && w.primary_location.landing_page_url) || w.doi && `https://doi.org/${String(w.doi).replace(/^https?:\/\/doi.org\//, '')}` || w.id || '';
    const authors = (w.authorships || []).slice(0, 4).map((a) => a.author && a.author.display_name).filter(Boolean).join(', ');
    const summary = invertAbstract(w.abstract_inverted_index);
    const item = rec({
      Title: title,
      'Canonical URL': url,
      'Creator/Publisher': authors || 'OpenAlex work',
      Organization: authors,
      'Resource Type': 'paper',
      Collection: collection || 'B',
      'Topic Tags': topic,
      Summary: summary || `Scholarly work on ${topic} indexed by OpenAlex.`,
      'Key Takeaways/What You Can Do': 'Read the abstract and cited works; do not copy the full paper into the library.',
      'Why It Matters': `Peer-reviewed or preprint literature for Research Queue topic: ${topic}.`,
      'Why Matt Qualifies': 'Public metadata; undergraduate-accessible research literacy.',
      'Publication Date': (w.publication_date || w.publication_year || '') + '',
      Relevance: scoreRelevance(title, summary + ' ' + topic),
      Confidence: 8,
      'Verification Status': 'Verified',
      'Related Collections/Topic': topic,
      'Discovery Source': 'OpenAlex API',
      Category: 'Construction & Development',
      'Next Action': 'Skim abstract and save any official dataset or methods cited.',
      'Evidence/Notes': `cited_by=${w.cited_by_count || 0}`,
    });
    if (tryAdd(seen, records, item)) n += 1;
  }
  const next = data.meta && data.meta.next_cursor;
  state.openalex[pageKey] = next || 'done';
  return n;
}

async function collectArxiv(topic, collection, seen, records, state, perPage) {
  const start = Number(state.arxiv[`${topic}|v2`] || 0);
  const q = encodeURIComponent(literatureQuery(topic).split(' ').map((w) => `all:${w}`).join(' AND '));
  const url = `https://export.arxiv.org/api/query?search_query=${q}&start=${start}&max_results=${perPage}&sortBy=submittedDate&sortOrder=descending`;
  const { status, text } = await fetchText(url, { accept: 'application/atom+xml' });
  if (status !== 200 || !text) return 0;
  const entries = text.split(/<entry>/).slice(1);
  let n = 0;
  for (const e of entries) {
    const title = normSpace((e.match(/<title[^>]*>([\s\S]*?)<\/title>/) || [])[1] || '');
    const summary = normSpace((e.match(/<summary[^>]*>([\s\S]*?)<\/summary>/) || [])[1] || '').slice(0, MAX_ABSTRACT);
    const id = normSpace((e.match(/<id>([\s\S]*?)<\/id>/) || [])[1] || '');
    const published = normSpace((e.match(/<published>([\s\S]*?)<\/published>/) || [])[1] || '').slice(0, 10);
    const authors = [...e.matchAll(/<name>([\s\S]*?)<\/name>/g)].map((m) => normSpace(m[1])).slice(0, 4).join(', ');
    const item = rec({
      Title: title,
      'Canonical URL': id,
      'Creator/Publisher': authors || 'arXiv',
      'Resource Type': 'paper',
      Collection: collection || 'B',
      'Topic Tags': topic,
      Summary: summary,
      'Key Takeaways/What You Can Do': 'Read the arXiv abstract; follow the PDF only for personal study, never paste the full text here.',
      'Why It Matters': `Preprint literature for ${topic}.`,
      'Why Matt Qualifies': 'Public preprint metadata.',
      'Publication Date': published,
      Relevance: scoreRelevance(title, summary + ' ' + topic),
      Confidence: 8,
      'Verification Status': 'Verified',
      'Related Collections/Topic': topic,
      'Discovery Source': 'arXiv API',
      Category: 'AI & Technology',
      'Next Action': 'Read abstract and note methods you could try on a small project.',
    });
    if (tryAdd(seen, records, item)) n += 1;
  }
  state.arxiv[`${topic}|v2`] = start + entries.length;
  return n;
}

async function collectCrossref(topic, collection, seen, records, state, perPage) {
  const offset = Number(state.crossref[`${topic}|v2`] || 0);
  const params = new URLSearchParams({ 'query.bibliographic': literatureQuery(topic), filter: 'from-pub-date:2015-01-01,has-abstract:true', rows: String(perPage), offset: String(offset), select: 'DOI,title,author,abstract,created,type,URL' });
  const data = await fetchJson(`https://api.crossref.org/works?${params}`);
  const items = data && data.message && data.message.items;
  if (!Array.isArray(items)) return 0;
  let n = 0;
  for (const w of items) {
    const title = (w.title && w.title[0]) || '';
    const url = w.URL || (w.DOI ? `https://doi.org/${w.DOI}` : '');
    const authors = (w.author || []).slice(0, 4).map((a) => [a.given, a.family].filter(Boolean).join(' ')).join(', ');
    const summary = normSpace(String(w.abstract || '').replace(/<[^>]+>/g, '')).slice(0, MAX_ABSTRACT);
    const item = rec({
      Title: title,
      'Canonical URL': url,
      'Creator/Publisher': authors || 'Crossref work',
      'Resource Type': 'paper',
      Collection: collection || 'B',
      'Topic Tags': topic,
      Summary: summary || `Crossref-indexed work on ${topic}.`,
      'Key Takeaways/What You Can Do': 'Use the DOI landing page; store metadata only.',
      'Why It Matters': `Publisher-registered literature for ${topic}.`,
      'Why Matt Qualifies': 'Public Crossref metadata.',
      'Publication Date': (w.created && w.created['date-time'] || '').slice(0, 10),
      Relevance: scoreRelevance(title, summary + ' ' + topic),
      Confidence: 8,
      'Verification Status': 'Verified',
      'Related Collections/Topic': topic,
      'Discovery Source': 'Crossref API',
      Category: 'Construction & Development',
      'Next Action': 'Open the DOI and capture only the official abstract/metadata.',
    });
    if (tryAdd(seen, records, item)) n += 1;
  }
  state.crossref[`${topic}|v2`] = offset + items.length;
  return n;
}

async function collectGithub(topic, collection, seen, records, state) {
  const page = Number(state.github[topic] || 1);
  const q = encodeURIComponent(`${topic} construction OR BIM OR robotics in:name,description`);
  const url = `https://api.github.com/search/repositories?q=${q}&sort=stars&order=desc&per_page=5&page=${page}`;
  const data = await fetchJson(url);
  const items = data && data.items;
  if (!Array.isArray(items)) return 0;
  let n = 0;
  for (const repo of items) {
    const title = repo.full_name;
    const pageUrl = repo.html_url;
    const summary = normSpace(repo.description || '').slice(0, MAX_ABSTRACT);
    const item = rec({
      Title: title,
      'Canonical URL': pageUrl,
      'Creator/Publisher': (repo.owner && repo.owner.login) || 'GitHub',
      'Resource Type': 'repo',
      Collection: collection === 'G' ? 'G' : 'D',
      'Topic Tags': topic,
      Summary: summary || `Open-source repository related to ${topic}.`,
      'Key Takeaways/What You Can Do': 'Clone or read the README; do not paste large source files into the library.',
      'Why It Matters': `Runnable or inspectable project for ${topic}.`,
      'Why Matt Qualifies': 'Public GitHub repository.',
      'Publication Date': (repo.created_at || '').slice(0, 10),
      Relevance: Math.max(scoreRelevance(title + ' ' + summary, topic), 6),
      Confidence: 8,
      'Verification Status': 'Verified',
      'Related Collections/Topic': topic,
      'Discovery Source': 'GitHub Search API',
      Category: 'AI & Technology',
      'Next Action': 'Read README and note license plus one thing you could try.',
      'Evidence/Notes': `stars=${repo.stargazers_count || 0}`,
    });
    if (tryAdd(seen, records, item)) n += 1;
  }
  state.github[topic] = page + 1;
  return n;
}

async function collectWikipedia(topic, collection, seen, records) {
  const params = new URLSearchParams({ action: 'opensearch', search: topic, limit: '2', namespace: '0', format: 'json' });
  const data = await fetchJson(`https://en.wikipedia.org/w/api.php?${params}`);
  if (!Array.isArray(data) || !Array.isArray(data[1])) return 0;
  let n = 0;
  for (let i = 0; i < data[1].length; i += 1) {
    const title = data[1][i];
    const page = (data[3] && data[3][i]) || `https://en.wikipedia.org/wiki/${encodeURIComponent(title)}`;
    const slug = title.replace(/ /g, '_');
    const sum = await fetchJson(`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(slug)}`);
    if (!sum || sum.type === 'disambiguation') continue;
    const extract = normSpace(sum.extract || data[2][i] || '').slice(0, MAX_ABSTRACT);
    const item = rec({
      Title: sum.title || title,
      'Canonical URL': (sum.content_urls && sum.content_urls.desktop && sum.content_urls.desktop.page) || page,
      'Creator/Publisher': 'Wikipedia contributors',
      Organization: 'Wikipedia',
      'Resource Type': 'report',
      Collection: collection === 'C' ? 'C' : 'G',
      'Topic Tags': topic,
      Summary: extract,
      'Key Takeaways/What You Can Do': 'Use this as a primer, then follow official citations.',
      'Why It Matters': `Neutral primer that opens Research Queue topic: ${topic}.`,
      'Why Matt Qualifies': 'Public encyclopedia page.',
      Difficulty: 'beginner',
      Relevance: Math.max(scoreRelevance(title, extract + ' ' + topic), 6),
      Confidence: 7,
      'Verification Status': 'Verified',
      'Related Collections/Topic': topic,
      'Discovery Source': 'Wikipedia REST API',
      Category: 'Construction & Development',
      Priority: 'Low',
      'Next Action': 'Read the lead section and open 1-2 cited official sources.',
    });
    if (tryAdd(seen, records, item)) n += 1;
  }
  return n;
}

async function collectRssList(feeds, seen, records, rtype, verification, sourceLabel) {
  let n = 0;
  for (const feed of feeds) {
    const { status, text } = await fetchText(feed.url, { accept: 'application/rss+xml, application/atom+xml, text/xml, */*' });
    if (status !== 200) continue;
    for (const it of parseRssItems(text, 8)) {
      const rel = scoreRelevance(it.title, it.summary + ' ' + feed.name);
      if (rel < 5) continue;
      const item = rec({
        Title: it.title,
        'Canonical URL': it.url,
        'Creator/Publisher': feed.name,
        'Resource Type': rtype,
        Collection: feed.collection,
        'Topic Tags': feed.name,
        Summary: it.summary || `${sourceLabel} item from ${feed.name}.`,
        'Key Takeaways/What You Can Do': 'Open the official item and keep only a short original note.',
        'Why It Matters': `Recurring ${sourceLabel.toLowerCase()} that can surface new library leads.`,
        'Why Matt Qualifies': 'Public feed item.',
        'Publication Date': it.published,
        Relevance: rel,
        Confidence: verification === 'Verified' ? 7 : 5,
        'Verification Status': verification,
        'Related Collections/Topic': feed.name,
        'Discovery Source': sourceLabel,
        Category: 'Construction & Development',
        'Next Action': 'Skim and keep only if it teaches a reusable skill or names a primary source.',
      });
      if (tryAdd(seen, records, item)) n += 1;
    }
  }
  return n;
}

async function collectYoutube(seen, records) {
  let n = 0;
  for (const [id, name] of Object.entries(YT_CHANNELS)) {
    const url = `https://www.youtube.com/feeds/videos.xml?channel_id=${id}`;
    const { status, text } = await fetchText(url, { accept: 'application/atom+xml' });
    if (status !== 200) continue;
    const entries = text.split(/<entry>/).slice(1).slice(0, 8);
    for (const e of entries) {
      const title = normSpace((e.match(/<title[^>]*>([\s\S]*?)<\/title>/) || [])[1] || '');
      const link = ((e.match(/<link[^>]*href="([^"]+)"/) || [])[1]) || ((e.match(/<yt:videoId>([^<]+)<\/yt:videoId>/) || [])[1] && `https://www.youtube.com/watch?v=${(e.match(/<yt:videoId>([^<]+)<\/yt:videoId>/) || [])[1]}`) || '';
      const summary = normSpace((e.match(/<media:description[^>]*>([\s\S]*?)<\/media:description>/) || [])[1] || '').slice(0, MAX_ABSTRACT);
      const item = rec({
        Title: title,
        'Canonical URL': link,
        'Creator/Publisher': name,
        'Resource Type': 'video',
        Collection: 'A',
        'Topic Tags': name,
        Summary: summary || `Public video from ${name}.`,
        'Key Takeaways/What You Can Do': 'Watch for one reusable idea; do not transcribe the video into the library.',
        'Why It Matters': 'Visual explainer that can seed further official research.',
        'Why Matt Qualifies': 'Public YouTube metadata via official RSS.',
        Relevance: Math.max(scoreRelevance(title, summary), 6),
        Confidence: 6,
        'Verification Status': 'Preliminary lead',
        'Related Collections/Topic': name,
        'Discovery Source': 'YouTube channel RSS',
        Category: 'Construction & Development',
        'Next Action': 'Watch if the title matches a Research Queue skill gap.',
      });
      if (tryAdd(seen, records, item)) n += 1;
    }
  }
  return n;
}

async function lightLinkCheck(records, limit = 25) {
  let checked = 0;
  for (const r of records) {
    if (checked >= limit) break;
    if (!r['Canonical URL'] || !/^https?:/i.test(r['Canonical URL'])) continue;
    if (r['Verification Status'] !== 'Verified') continue;
    const code = await httpStatus(r['Canonical URL']);
    checked += 1;
    r['Evidence/Notes'] = `${r['Evidence/Notes'] || ''} HTTP ${code}`.trim();
    if (code >= 400) {
      r['Verification Status'] = 'Preliminary lead';
      r.Confidence = Math.min(Number(r.Confidence) || 5, 5);
    }
  }
  return checked;
}

function csvEscape(v) {
  const s = v == null ? '' : String(v);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function writeOutputs(outDir, records, stats) {
  fs.mkdirSync(outDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  const base = path.join(outDir, `n8n-append-${stamp}`);
  const payload = {
    generated: new Date().toISOString(),
    contributor: 'n8n',
    new_count: records.length,
    stats,
    records,
  };
  const lines = [COLUMNS.join(',')];
  for (const r of records) lines.push(COLUMNS.map((c) => csvEscape(r[c])).join(','));
  const jsonPath = `${base}.json`;
  const csvPath = `${base}.csv`;
  fs.writeFileSync(`${jsonPath}.tmp`, JSON.stringify(payload, null, 2), 'utf8');
  fs.writeFileSync(`${csvPath}.tmp`, lines.join('\n'), 'utf8');
  fs.renameSync(`${jsonPath}.tmp`, jsonPath);
  fs.renameSync(`${csvPath}.tmp`, csvPath);
  return { json: jsonPath, csv: csvPath, count: records.length };
}

async function main() {
  const { values } = parseArgs({
    options: {
      queue: { type: 'string', default: '/data/research-alerts/research_queue.json' },
      'existing-ids': { type: 'string', default: '/data/research-alerts/sheet_ids.json' },
      seen: { type: 'string', default: '/data/research-alerts/n8n_seen.json' },
      state: { type: 'string', default: '/data/research-alerts/n8n_collector_state.json' },
      'out-dir': { type: 'string', default: '/data/research-alerts/incoming' },
      'max-new': { type: 'string', default: '70' },
      'topics-per-run': { type: 'string', default: '12' },
      'per-source': { type: 'string', default: '8' },
    },
  });
  const maxNew = Number(values['max-new']) || 70;
  const topicsPerRun = Number(values['topics-per-run']) || 12;
  const perSource = Number(values['per-source']) || 8;

  const queue = loadQueue(values.queue);
  const seen = loadIds(values['existing-ids']);
  const extra = loadIds(values.seen);
  extra.ids.forEach((id) => seen.ids.add(id));
  extra.cis.forEach((id) => seen.cis.add(id));
  const state = loadState(values.state);
  const records = [];
  const stats = { sources: {}, topics_used: [], starting_seen: seen.ids.size, collector_version: 'gated-v2-20261123' };

  const start = Number(state.topic_index || 0);
  const used = [];
  if (queue.length) {
    for (let i = 0; i < Math.min(topicsPerRun, queue.length); i += 1) {
      used.push(queue[(start + i) % queue.length]);
    }
    state.topic_index = (start + used.length) % queue.length;
  }
  stats.topics_used = used.map((t) => t.topic);

  const bump = (name, n) => { stats.sources[name] = (stats.sources[name] || 0) + n; };

  for (const t of used) {
    if (records.length >= maxNew) break;
    bump('openalex', await collectOpenAlex(t.topic, t.collection, seen, records, state, perSource));
    await new Promise((r) => setTimeout(r, 150));
    if (records.length >= maxNew) break;
    bump('crossref', await collectCrossref(t.topic, t.collection, seen, records, state, Math.min(perSource, 6)));
    await new Promise((r) => setTimeout(r, 150));
  }

  for (const t of used.filter((_, i) => i % 2 === 0)) {
    if (records.length >= maxNew) break;
    bump('arxiv', await collectArxiv(t.topic, t.collection, seen, records, state, Math.min(perSource, 6)));
    await new Promise((r) => setTimeout(r, 200));
  }

  const ghTopics = used.filter((t) => ['D', 'G', 'C'].includes(t.collection)).slice(0, 3);
  for (const t of ghTopics) {
    if (records.length >= maxNew) break;
    bump('github', await collectGithub(t.topic, t.collection, seen, records, state));
    await new Promise((r) => setTimeout(r, 2000));
  }

  for (const t of used.slice(0, 8)) {
    if (records.length >= maxNew) break;
    bump('wikipedia', await collectWikipedia(t.topic, t.collection, seen, records));
    await new Promise((r) => setTimeout(r, 120));
  }

  if (records.length < maxNew) bump('youtube_rss', await collectYoutube(seen, records));
  if (records.length < maxNew) bump('podcast_rss', await collectRssList(PODCAST_FEEDS, seen, records, 'podcast', 'Preliminary lead', 'Podcast/publication RSS'));
  if (records.length < maxNew) bump('agency_feeds', await collectRssList(AGENCY_FEEDS, seen, records, 'report', 'Verified', 'Agency RSS'));

  const trimmed = records.slice(0, maxNew);
  stats.link_checks = await lightLinkCheck(trimmed);
  stats.new_count = trimmed.length;
  stats.relevance_gate = GATE;

  // Persist the batch before seen/state. A runner kill after this point can
  // be merged idempotently; the reverse order drops IDs that never hit a file.
  const written = writeOutputs(values['out-dir'], trimmed, stats);

  const seenPayload = {
    updated: new Date().toISOString(),
    ids: Object.fromEntries([...seen.ids].map((id) => [id, true])),
    content_identities: [...seen.cis],
  };
  fs.mkdirSync(path.dirname(values.seen), { recursive: true });
  const seenTmp = `${values.seen}.tmp`;
  const stateTmp = `${values.state}.tmp`;
  fs.writeFileSync(seenTmp, JSON.stringify(seenPayload), 'utf8');
  fs.renameSync(seenTmp, values.seen);
  fs.writeFileSync(stateTmp, JSON.stringify(state, null, 2), 'utf8');
  fs.renameSync(stateTmp, values.state);

  console.log(JSON.stringify({ ok: true, ...written, stats }, null, 2));
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, error: err.message }));
  process.exit(1);
});
