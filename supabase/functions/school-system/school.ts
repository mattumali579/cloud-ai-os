export type Urgency = "high" | "medium" | "normal";

export interface SchoolMessage {
  id: string;
  internetMessageId?: string | null;
  subject: string;
  bodyPreview?: string | null;
  receivedDateTime: string;
  webLink?: string | null;
  from?: { emailAddress?: { address?: string; name?: string } } | null;
}

export interface ParsedAssignment {
  course: string;
  task: string;
  dueAt: string | null;
  urgency: Urgency;
  meaningful: boolean;
}

const ACTION_RE = /\b(?:assignment|quiz|exam|test|project|homework|lab|discussion|paper|essay|presentation|worksheet|submit|submission|complete|deadline|due)\b/i;
const URGENT_RE = /\b(?:urgent|asap|immediately|overdue|final reminder|due (?:today|tonight)|last chance)\b/i;
const NOISE_RE = /\b(?:unsubscribe|newsletter|campus event|career fair|promotion|survey invitation)\b/i;
const COURSE_CODE_RE = /\b([A-Z]{2,5})[\s-]?(\d{3,4}[A-Z]?)\b/;
const MONTHS: Record<string, number> = {
  jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3,
  apr: 4, april: 4, may: 5, jun: 6, june: 6, jul: 7, july: 7,
  aug: 8, august: 8, sep: 9, sept: 9, september: 9, oct: 10,
  october: 10, nov: 11, november: 11, dec: 12, december: 12,
};
const WEEKDAYS: Record<string, number> = {
  sunday: 0, monday: 1, tuesday: 2, wednesday: 3,
  thursday: 4, friday: 5, saturday: 6,
};

function clean(value: string): string {
  return value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function zonedParts(date: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone, year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric", hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute"), second: get("second") };
}

function zonedToUtc(year: number, month: number, day: number, hour: number, minute: number, timeZone: string): Date {
  const guess = Date.UTC(year, month - 1, day, hour, minute, 0);
  const first = zonedParts(new Date(guess), timeZone);
  const firstAsUtc = Date.UTC(first.year, first.month - 1, first.day, first.hour, first.minute, first.second);
  const offset = firstAsUtc - guess;
  const adjusted = guess - offset;
  const second = zonedParts(new Date(adjusted), timeZone);
  const secondAsUtc = Date.UTC(second.year, second.month - 1, second.day, second.hour, second.minute, second.second);
  return new Date(adjusted - (secondAsUtc - guess));
}

function parsedTime(text: string): { hour: number; minute: number } {
  const match = text.match(/\b(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b/i);
  if (!match) return { hour: 23, minute: 59 };
  let hour = Number(match[1]) % 12;
  if (match[3].toLowerCase().startsWith("p")) hour += 12;
  return { hour, minute: Number(match[2] || 0) };
}

export function parseDueDate(text: string, now = new Date(), timeZone = "America/Chicago"): string | null {
  const value = clean(text);
  const lower = value.toLowerCase();
  const local = zonedParts(now, timeZone);
  const time = parsedTime(value);
  let year = local.year;
  let month = local.month;
  let day = local.day;

  const iso = value.match(/\b(20\d{2})-(\d{1,2})-(\d{1,2})\b/);
  const numeric = value.match(/\b(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?\b/);
  const named = value.match(/\b(january|february|march|april|may|june|july|august|september|sept|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b/i);

  if (iso) {
    year = Number(iso[1]); month = Number(iso[2]); day = Number(iso[3]);
  } else if (numeric) {
    month = Number(numeric[1]); day = Number(numeric[2]);
    if (numeric[3]) year = Number(numeric[3]) < 100 ? 2000 + Number(numeric[3]) : Number(numeric[3]);
  } else if (named) {
    month = MONTHS[named[1].toLowerCase()]; day = Number(named[2]);
    if (named[3]) year = Number(named[3]);
  } else if (/\b(?:due\s+)?tomorrow\b/i.test(value)) {
    const next = new Date(Date.UTC(local.year, local.month - 1, local.day) + 86_400_000);
    year = next.getUTCFullYear(); month = next.getUTCMonth() + 1; day = next.getUTCDate();
  } else if (/\b(?:due\s+)?(?:today|tonight)\b/i.test(value)) {
    // local date defaults already set
  } else {
    const weekdayMatch = lower.match(/\b(next\s+)?(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b/);
    if (!weekdayMatch) return null;
    const localMidnight = new Date(Date.UTC(local.year, local.month - 1, local.day));
    const currentDay = localMidnight.getUTCDay();
    let delta = (WEEKDAYS[weekdayMatch[2]] - currentDay + 7) % 7;
    if (delta === 0 || weekdayMatch[1]) delta += 7;
    localMidnight.setUTCDate(localMidnight.getUTCDate() + delta);
    year = localMidnight.getUTCFullYear(); month = localMidnight.getUTCMonth() + 1; day = localMidnight.getUTCDate();
  }

  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  if (!iso && !numeric?.[3] && !named?.[3]) {
    const candidate = zonedToUtc(year, month, day, time.hour, time.minute, timeZone);
    if (candidate.getTime() < now.getTime() - 12 * 60 * 60 * 1000) year += 1;
  }
  const result = zonedToUtc(year, month, day, time.hour, time.minute, timeZone);
  return Number.isNaN(result.getTime()) ? null : result.toISOString();
}

export function extractCourse(subject: string, preview = ""): string {
  const source = `${subject} ${preview}`;
  const code = source.match(COURSE_CODE_RE);
  if (code) return `${code[1]} ${code[2]}`.toUpperCase();
  const bracket = subject.match(/^\s*\[([^\]]{2,60})\]/);
  if (bracket) return clean(bracket[1]);
  const named = source.match(/\b(?:for|in)\s+(?:your\s+)?([A-Z][A-Za-z&' -]{2,50}?)(?:\s+(?:course|class)|[.:,]|\s+(?:is|has|due)\b)/);
  return named ? clean(named[1]) : "Uncategorized";
}

export function extractTask(subject: string): string {
  const stripped = clean(subject)
    .replace(/^\s*\[[^\]]+\]\s*/, "")
    .replace(/^(?:re:|fw:|fwd:|reminder:|urgent:|due:|assignment:)\s*/i, "")
    .replace(/\s+/g, " ")
    .trim();
  return (stripped || "Review school message").slice(0, 300);
}

export function parseSchoolMessage(message: SchoolMessage, now = new Date(), timeZone = "America/Chicago"): ParsedAssignment | null {
  const subject = clean(message.subject || "");
  const preview = clean(message.bodyPreview || "");
  const text = `${subject}. ${preview}`;
  if (!ACTION_RE.test(text) || (NOISE_RE.test(text) && !/\bdue\b/i.test(text))) return null;

  const dueAt = parseDueDate(text, now, timeZone);
  const dueMs = dueAt ? new Date(dueAt).getTime() - now.getTime() : Number.POSITIVE_INFINITY;
  let urgency: Urgency = "normal";
  if (URGENT_RE.test(text) || dueMs <= 24 * 60 * 60 * 1000) urgency = "high";
  else if (/\b(?:important|soon|reminder)\b/i.test(text) || dueMs <= 7 * 24 * 60 * 60 * 1000) urgency = "medium";

  return {
    course: extractCourse(subject, preview),
    task: extractTask(subject),
    dueAt,
    urgency,
    meaningful: Boolean(dueAt) || urgency !== "normal" || /\b(?:assignment|quiz|exam|project|homework|lab|paper|submit)\b/i.test(text),
  };
}

export async function assignmentFingerprint(messageId: string, parsed: ParsedAssignment): Promise<string> {
  const normalized = [messageId, parsed.course, parsed.task, parsed.dueAt || ""].join("|").toLowerCase();
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(normalized));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function shouldNotify(parsed: ParsedAssignment, now = new Date()): boolean {
  if (!parsed.meaningful) return false;
  if (parsed.urgency === "high") return true;
  if (!parsed.dueAt) return /\b(?:exam|quiz|assignment|project|submit)\b/i.test(parsed.task);
  return new Date(parsed.dueAt).getTime() - now.getTime() <= 7 * 24 * 60 * 60 * 1000;
}
