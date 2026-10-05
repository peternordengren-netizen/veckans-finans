/**
 * Läser veckofiler, kursfiler och sektorlistan vid byggtid.
 *
 * Läge "mock" (astro dev/build --mode mock) läser data/mock/, annars data/weeks/
 * och data/prices/. Rådata och evidence-fältet läses aldrig in i sidan:
 * toWeekView plockar bara ut de fält som visas.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const DATA = join(process.cwd(), "data");
const IS_MOCK = import.meta.env.MODE === "mock";
const WEEKS_DIR = IS_MOCK ? join(DATA, "mock", "weeks") : join(DATA, "weeks");
const PRICES_DIR = IS_MOCK ? join(DATA, "mock", "prices") : join(DATA, "prices");
const WEEK_RE = /^\d{4}-W\d{2}$/;

export type Impact = "direkt" | "indirekt";
export type Direction = "positiv" | "negativ" | "blandad" | "oklar";

export interface Sector {
  id: string;
  name: string;
}

export interface Quote {
  change_pct: number;
  close: number;
  currency: string;
}

export interface ListedCompany {
  name: string;
  ticker: string;
  exchange: string;
  impact: Impact;
  rationale: string;
  quote: Quote | null;
}

export interface NotInListCompany {
  name: string;
  impact: Impact;
  rationale: string;
}

export interface SectorImpact {
  sector: Sector;
  direction: Direction;
  rationale: string;
}

export interface ItemView {
  id: string;
  headline: string;
  summary: string;
  published: string;
  sourceName: string;
  sourceUrl: string;
  listed: ListedCompany[];
  notInList: NotInListCompany[];
  impacts: SectorImpact[];
}

export interface SectorView {
  sector: Sector;
  summary: string;
  items: ItemView[];
}

export interface PolicyRate {
  value: number;
  date: string;
  previousValue: number | null;
  changedOn: string | null;
  sourceUrl: string;
}

export interface WeekView {
  week: string;
  start: string;
  end: string;
  mock: boolean;
  hasPrices: boolean;
  policyRate: PolicyRate | null;
  sectors: SectorView[];
}

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf-8")) as T;
}

export function loadSectors(): Sector[] {
  return readJson<Sector[]>(join(DATA, "sectors.json"));
}

/** Tillgängliga veckor, nyast först. */
export function listWeeks(): string[] {
  if (!existsSync(WEEKS_DIR)) return [];
  return readdirSync(WEEKS_DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.slice(0, -5))
    .filter((w) => WEEK_RE.test(w))
    .sort()
    .reverse();
}

/** Kursfilen är valfri – saknas den, eller är den trasig, visas sidan utan kurser. */
function loadQuotes(week: string): Record<string, Quote> | null {
  const path = join(PRICES_DIR, `${week}.json`);
  if (!existsSync(path)) return null;
  try {
    return readJson<{ quotes: Record<string, Quote> }>(path).quotes ?? null;
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan kurser:`, err);
    return null;
  }
}

// Rå struktur enligt schema/week.schema.json – bara de fält sidan använder.
interface RawCompany {
  name: string;
  match: "matchad" | "ej i listan";
  ticker: string | null;
  yahoo_ticker: string | null;
  exchange: string | null;
  impact: Impact;
  rationale: string;
}
interface RawItem {
  id: string;
  headline: string;
  summary: string;
  published: string;
  source: { name: string; url: string };
  companies: RawCompany[];
  sector_impacts: { sector: string; direction: Direction; rationale: string }[];
}
interface RawWeek {
  week: string;
  period: { start: string; end: string };
  mock?: boolean;
  key_figures?: {
    policy_rate?: {
      value: number;
      date: string;
      previous_value: number | null;
      changed_on: string | null;
      source_url: string;
    };
  };
  sectors: { id: string; summary: string; items: RawItem[] }[];
}

export function loadWeek(week: string): WeekView {
  const raw = readJson<RawWeek>(join(WEEKS_DIR, `${week}.json`));
  if (!IS_MOCK && raw.mock) {
    // Extra spärr utöver validate.py: exempeldata får aldrig publiceras.
    throw new Error(`${week}.json är markerad som mock men ligger i data/weeks/`);
  }

  const sectors = loadSectors();
  const byId = new Map(sectors.map((s) => [s.id, s]));
  const sectorOf = (id: string): Sector => byId.get(id) ?? { id, name: id };
  const quotes = loadQuotes(week);

  const toItem = (it: RawItem): ItemView => ({
    id: it.id,
    headline: it.headline,
    summary: it.summary,
    published: it.published,
    sourceName: it.source.name,
    sourceUrl: it.source.url,
    listed: it.companies
      .filter((c) => c.match === "matchad" && c.ticker)
      .map((c) => ({
        name: c.name,
        ticker: c.ticker!,
        exchange: c.exchange ?? "",
        impact: c.impact,
        rationale: c.rationale,
        quote: (c.yahoo_ticker && quotes?.[c.yahoo_ticker]) || null,
      })),
    notInList: it.companies
      .filter((c) => c.match === "ej i listan")
      .map((c) => ({ name: c.name, impact: c.impact, rationale: c.rationale })),
    impacts: it.sector_impacts.map((s) => ({
      sector: sectorOf(s.sector),
      direction: s.direction,
      rationale: s.rationale,
    })),
  });

  // Flikordningen följer sectors.json, inte filens ordning.
  const order = new Map(sectors.map((s, i) => [s.id, i]));
  const sectorViews = raw.sectors
    .map((s) => ({ sector: sectorOf(s.id), summary: s.summary, items: s.items.map(toItem) }))
    .sort((a, b) => (order.get(a.sector.id) ?? 99) - (order.get(b.sector.id) ?? 99));

  const pr = raw.key_figures?.policy_rate;
  return {
    week: raw.week,
    start: raw.period.start,
    end: raw.period.end,
    mock: Boolean(raw.mock),
    hasPrices: quotes !== null,
    policyRate: pr
      ? {
          value: pr.value,
          date: pr.date,
          previousValue: pr.previous_value,
          changedOn: pr.changed_on,
          sourceUrl: pr.source_url,
        }
      : null,
    sectors: sectorViews,
  };
}

/** 1.75 -> "1,75", 2 -> "2,00" (räntor visas alltid med två decimaler). */
export function formatRate(value: number): string {
  return new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
}

export function formatLongDate(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("sv-SE", { day: "numeric", month: "long", year: "numeric" });
}

export function weekLabel(week: string): string {
  const [year, w] = week.split("-W");
  return `Vecka ${Number(w)}, ${year}`;
}

export function formatDate(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("sv-SE", { day: "numeric", month: "long" });
}
