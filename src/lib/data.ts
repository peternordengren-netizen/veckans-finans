/**
 * Läser veckofiler, kursfiler och sektorlistan vid byggtid.
 *
 * Läge "mock" (astro dev/build --mode mock) läser data/mock/, annars data/weeks/
 * och data/prices/. Rådata och evidence-fältet läses aldrig in i sidan:
 * toWeekView plockar bara ut de fält som visas.
 */
import type { PolicyDecision } from "./policyRate";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const DATA = join(process.cwd(), "data");
const IS_MOCK = import.meta.env.MODE === "mock";
const WEEKS_DIR = IS_MOCK ? join(DATA, "mock", "weeks") : join(DATA, "weeks");
const PRICES_DIR = IS_MOCK ? join(DATA, "mock", "prices") : join(DATA, "prices");
const ANALYSIS_DIR = IS_MOCK ? join(DATA, "mock", "analysis") : join(DATA, "analysis");
const CALENDAR_DIR = IS_MOCK ? join(DATA, "mock", "calendar") : join(DATA, "calendar");
const SIGNALS_DIR = IS_MOCK ? join(DATA, "mock", "signals") : join(DATA, "signals");
const MORNING_DIR = IS_MOCK ? join(DATA, "mock", "morning") : join(DATA, "morning");
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const UPDATE_RE = /^\d{4}-\d{2}-\d{2}T\d{4}$/;

// Morgonrapporten (scripts/morning.py, schema/morning.schema.json).
export interface MorningChange {
  ticker: string;
  name: string;
  holder?: string;
  start_pct: number;
  end_pct: number;
  change_pp: number;
}
export interface MorningReport {
  date: string;
  generated_at: string;
  /** Riksbankens senaste räntebeslut (saknas i äldre filer). */
  policy_decision?: PolicyDecision | null;
  news_since: string;
  reports_today: (CalendarEvent & { kind: "rapport" | "stämma" })[];
  news: { ticker: string; name: string; report_today: boolean; items: { published: string; title: string; url: string; source: string; quote?: string; summary?: string; manual?: boolean }[] }[];
  insider: { threshold_sek: number; days: string[]; days_missing: string[]; companies: InsiderCompany[] };
  short: { available: boolean; error?: boolean; note: string | null; from_as_of: string | null; to_as_of: string | null; positions: MorningChange[]; aggregate: MorningChange[] };
  price: {
    /** Dagsdatans eget datum (scripts/signals.py data_day), aldrig ett antaget. */
    day: string | null;
    /** Förra handelsdagen; skiljer den sig från day visas en varning (saknas i äldre filer). */
    expected_day?: string;
    /** Bolag med kursrad för day respektive expected_day, av bolagen med kurser (saknas i äldre filer). */
    coverage?: { with_day: number; with_expected: number; of: number };
    index_change_pct: number | null;
    thresholds: { volume_ratio: number; near_52w_pct: number };
    companies: { ticker: string; name: string; change_pct: number | null; relative_pp: number | null; volume_ratio: number | null; from_high_pct: number | null; from_low_pct: number | null; signals: ("omsättning" | "52v-högsta" | "52v-lägsta")[] }[];
  };
  errors: string[];
}

/**
 * Claudes användningsgräns nådd i en schemalagd körning (scripts/claude_status.py skriver
 * data/status/claude-limit.json och tar bort den när Claude kört igen), eller null.
 */
export function loadClaudeLimit(): { resetAt: string | null; source: string } | null {
  const path = join(DATA, "status", "claude-limit.json");
  if (!existsSync(path)) return null;
  try {
    const raw = readJson<{ reset_at: string | null; source: string }>(path);
    return { resetAt: raw.reset_at ?? null, source: raw.source };
  } catch {
    return null;
  }
}

// Swing (scripts/swing.py, schema/swing.schema.json) och historiken per etikett (scripts/swing_history.py).
const SWING_DIR = IS_MOCK ? join(DATA, "mock", "swing") : join(DATA, "swing");
export interface SwingRow {
  ticker: string;
  name: string;
  sector: string | null;
  day: string;
  ma20_pct: number;
  ma50_pct: number;
  rsi2: number | null;
  rsi14: number | null;
  atr_pct: number | null;
  volume_ratio: number | null;
  report_published: string | null;
  days_since_report: number | null;
  report_verdict: string | null;
  short_pct: number | null;
  /** Pullback i trend (scripts/pullback.py): tillståndet och nivåerna vid träff; saknas i äldre filer.
   *  bollinger: läget mot Bollingerbanden, gårdagens stängning eller (checked_at satt) senaste kontrollen i dag.
   *  today: status i dag ur senaste Bollingerkontrollen (saknas före dagens första kontroll). */
  pullback?: {
    state: PullbackState | null; levels: PullbackLevels | null; bollinger?: PullbackBollinger; rank?: PullbackRank;
    today?: PullbackToday;
  };
  /** Rekyl efter Bollingerutbrott (scripts/bbrekyl.py): nivåerna vid träff, läget mot banden, rangen och status i dag
   *  (som pullback); saknas i äldre filer. */
  bbrekyl?: {
    levels: BbrekylLevels | null; bollinger?: PullbackBollinger; rank?: BbrekylRank; today?: PullbackToday;
  };
  /** Senaste korsningen mellan 50 och 200 dagars MA de senaste 10 dagarna, och dagar sedan (saknas i äldre filer). */
  ma_cross?: "golden" | "death" | null;
  ma_cross_days?: number | null;
  /** Stöd och motstånd (scripts/levels.py); null utan ATR, saknas i äldre filer. */
  sr?: SupportResistance | null;
  labels: string[];
}
export interface SrLevel {
  kind: "zon" | "20 EMA" | "50 MA" | "200 MA";
  low: number;
  high: number;
  mid: number;
  /** Antal vändningar i zonen (null för medelvärdena). */
  touches: number | null;
  bottoms: number | null;
  tops: number | null;
  dist_pct: number | null;
  dist_atr: number | null;
  /** Stängningen ligger inne i zonen; för motstånd räknas R då till övre kanten (saknas i äldre filer). */
  inside?: boolean;
}
export interface SupportResistance {
  support: SrLevel | null;
  resistance: SrLevel | null;
  /** Gårdagens high. */
  trigger: number;
  /** Stödets nedre kant minus 0,1 ATR: en alternativ stoppnivå (visas i procent, inget eget R). */
  stop: number | null;
  /** Bara i äldre filer (ett eget R på stoppet under stödet); visas inte. Fliken har ett enda R, strategins. */
  resistance_r?: number | null;
}
/** Ett noterat bolag utan rad i Swing (scripts/swing.py "missing"). */
export interface SwingMissing {
  ticker: string;
  name: string;
  /** no_data: Yahoo saknar kursdata; short_history: för kort historik (bars dagar). */
  reason: "no_data" | "short_history";
  bars: number;
}
/** Ett avnoterat bolag (instruments.csv), som hoppas över i Swing. */
export interface SwingDelisted {
  ticker: string;
  name: string;
  last_day: string;
  source: string;
}
export interface PullbackState {
  open: number; high: number; low: number; close: number; prev_open: number; prev_close: number;
  ema20: number; ma50: number; ma200: number;
}
export interface PullbackLevels {
  level: "ema20" | "ma50";
  level_value: number;
  signal: "hammer" | "engulfing" | null;
  /** Gårdagens high vid vändsignal, annars null. */
  trigger: number | null;
  ref: number;
  stop: number;
  risk_pct: number | null;
  risk_atr: number | null;
  top: number;
  r2: number;
  r3: number;
}
/** Nivåerna i Rekyl efter Bollingerutbrott (scripts/bbrekyl.levels). */
export interface BbrekylLevels {
  /** Nivån gårdagens low nådde: mittbandet, 20 EMA eller mellan dem. */
  level: "mid" | "ema20" | "mellan";
  signal: "hammer" | "engulfing" | null;
  trigger: number | null;
  ref: number;
  stop: number;
  risk_pct: number | null;
  risk_atr: number | null;
  /** Utbrottets högsta high och avståndet dit i R. */
  target: number;
  target_r: number;
  r2: number;
  r3: number;
  breakout: {
    day: string; days_ago: number; close: number; upper: number; low: number; volume_ratio: number;
    /** Utbrottsdagen var reaktionsdag för en rapport, med utfallet mot förväntan (verdict null = okänt). */
    report: { published: string; verdict: string | null } | null;
  };
  /** Lägre snittomsättning under rekylen än på utbrottsdagen. */
  lower_volume: boolean;
  /** Volymvillkoren r1–r4 med siffrorna (saknas i äldre filer). */
  volume?: {
    breakout_ratio: number; r1: boolean; red_days: number; red_max_ratio: number | null; r2: boolean;
    rekyl_avg_ratio: number | null; r3: boolean; up_volume: number; down_volume: number; r4: boolean;
  };
}
export interface BbrekylRank {
  rank: number;
  score: number;
  criteria: Record<string, boolean>;
  measures: { signal: boolean; lower_volume: boolean; room_r: number | null; has_resistance: boolean; upper_r: number | null; stop_atr: number | null; ma50_change_pct: number | null };
}
/** Rangordningen bland kandidaterna (scripts/ranking.py): flest uppfyllda kriterier först. */
export interface PullbackRank {
  rank: number;
  score: number;
  criteria: Record<string, boolean>;
  measures: { signal: boolean; room_r: number | null; has_resistance: boolean; support_atr: number | null; upper_r: number | null; stop_atr: number | null; ma50_change_pct: number | null };
}
/** Status i dag för en pullback-kandidat (scripts/bollinger.py day_status), vid kontrollen checked_at. */
export interface PullbackToday {
  high: number;
  low: number;
  /** Senaste kursen vid kontrollen (fördröjd ca 15 minuter). */
  price: number;
  /** Gårdagens high. */
  trigger: number;
  ref: number;
  stop: number;
  ma50: number;
  status: "waiting" | "triggered" | "late" | "below_stop" | "below_ma50";
  /** Kontrollen där dagens high först låg över triggernivån, eller null. */
  trigger_seen_at: string | null;
  /** Dagens omsättning hittills mot normalt vid samma tid (bara Rekyl efter Bollingerutbrott; null utan timdata). */
  volume?: { so_far: number; normal: number; ratio: number; until: string; days: number } | null;
  checked_at: string;
}
/** En pullback-kandidats läge mot Bollingerbanden (scripts/bollinger.py position). */
export interface PullbackBollinger {
  price: number;
  lower: number;
  upper: number;
  /** (kurs − undre) / (övre − undre): 0 = undre bandet, 1 = övre. */
  pct_b: number | null;
  /** (övre − kurs) / R, R = referens − stopp; negativt över bandet. */
  upper_r: number | null;
  at_upper: boolean;
  /** Tiden för kontrollen under dagen; saknas för gårdagens stängning (swingfilen). */
  checked_at?: string;
}
export interface PullbackSummary {
  cases: number;
  decided: number;
  r2_first: number;
  stop_first: number;
  open: number;
  not_triggered: number;
  r2_share: number | null;
  stop_share: number | null;
  mean_net_pct: number | null;
  mean_net_r: number | null;
}
export interface SwingStat {
  n: number;
  better: number | null;
  ci: number | null;
  mean: number | null;
}
export interface SwingView {
  date: string;
  /** Dagsdatans eget datum (scripts/signals.py data_day). */
  asOf: string | null;
  /** Förra handelsdagen; skiljer den sig från asOf visas en varning (null i äldre filer). */
  expectedDay: string | null;
  coverage: { with_day: number; with_expected: number; of: number } | null;
  shortAsOf: string | null;
  thresholds: {
    volume_ratio: number; rsi2_low: number; rsi2_high: number; report_days: number;
    /** Pullback i trend (saknas i äldre filer). */
    pullback?: { touch_pct: number; hammer_shadow: number; hammer_upper: number; stop_days: number; top_days: number; trigger_days: number; max_days: number };
    /** Stöd och motstånd (saknas i äldre filer). */
    levels?: { lookback: number; pivot: number; merge_atr: number; stop_buffer_atr: number };
    /** Rangordningen av Pullback-kandidaterna (saknas i äldre filer). */
    ranking?: { resistance_r: number; support_atr: number; bollinger_r: number; stop_atr: number; ma50_days: number; top: number };
    /** Rekyl efter Bollingerutbrott (saknas i äldre filer). */
    bbrekyl?: {
      breakout_days: number; volume_ratio: number; touch_pct: number; bb_period: number; bb_width: number; volume_days: number;
      base_volume_ratio?: number; red_volume_ratio?: number;
      ranking: { target_r: number; bollinger_r: number; stop_atr: number; ma50_days: number; top: number };
    };
  };
  /** Rangordningens kriterier: nyckel och text (tom i äldre filer). */
  rankingCriteria: Record<string, string>;
  /** Rangkriterierna för Rekyl efter Bollingerutbrott (tom i äldre filer). */
  bbrekylCriteria: Record<string, string>;
  labels: Record<string, string>;
  /** Strategier i rullistan: namn och etiketterna som räknas som träff (saknas i äldre filer). */
  strategies: Record<string, { name: string; labels: string[] }>;
  rows: SwingRow[];
  /** Noterade bolag utan rad i tabellen, och avnoterade bolag (tomma i äldre filer). */
  missing: SwingMissing[];
  delisted: SwingDelisted[];
  /** Senaste Bollingerkontrollen i dag för swingfilens kandidater (scripts/bollinger.py), eller null. */
  bollingerCheck: { checked_at: string; delay_note: string; candidates: number; rows: number } | null;
  history: {
    /** Rekyl efter Bollingerutbrott: 2R före stoppet och tvärtom (saknas i äldre filer). */
    bbrekyl?: {
      thresholds: { max_days: number; trigger_days: number };
      labels: Record<string, PullbackSummary>;
      all: PullbackSummary;
      /** Volymvillkoren var för sig: grundregeln, ett villkor i taget och alla (saknas i äldre filer). */
      rules?: { description: string; texts: Record<string, string> } & Record<string, PullbackSummary | string | Record<string, string>>;
    };
    period: { start: string; end: string };
    method: string;
    base: Record<string, SwingStat>;
    labels: Record<string, Record<string, SwingStat>>;
    /** Pullback i trend: 2R före stoppet och tvärtom (saknas i äldre filer). */
    pullback?: {
      thresholds: { max_days: number; trigger_days: number };
      labels: Record<string, PullbackSummary>;
      all: PullbackSummary;
      /** Stöd nära och motstånd minst 2R bort, mot övriga fall; saknas i äldre filer. */
      sr_filter?: { description: string; with: PullbackSummary; without: PullbackSummary };
      /** Rangordningen: topp N per dag mot övriga fall, och per poäng; saknas i äldre filer. */
      ranking?: {
        description: string;
        top_n: number;
        days: number;
        days_over_top: number;
        top: PullbackSummary;
        others: PullbackSummary;
        by_score: Record<string, PullbackSummary>;
      };
      /** Samma fall med marknadsfilter (indexet över sitt 50- och 200-dagars MA); saknas i äldre filer. */
      market_filter?: {
        description: string;
        days_up: number;
        days: number;
        labels: Record<string, PullbackSummary>;
        all: PullbackSummary;
      };
    };
  } | null;
}

/** Senaste swingfilen (data/swing/<datum>.json) med historiken, eller null. Valfri; en trasig fil visas inte. */
export function loadLatestSwing(): SwingView | null {
  if (!existsSync(SWING_DIR)) return null;
  const files = readdirSync(SWING_DIR)
    .filter((f) => f.endsWith(".json") && DATE_RE.test(f.slice(0, -5)))
    .sort();
  if (files.length === 0) return null;
  const path = join(SWING_DIR, files[files.length - 1]);
  try {
    const raw = readJson<{
      date: string;
      as_of: string | null;
      expected_day?: string;
      coverage?: { with_day: number; with_expected: number; of: number };
      short_as_of: string | null;
      thresholds: SwingView["thresholds"];
      labels: Record<string, string>;
      ranking_criteria?: Record<string, string>;
      bbrekyl_criteria?: Record<string, string>;
      strategies?: SwingView["strategies"];
      companies: SwingRow[];
      missing?: SwingMissing[];
      delisted?: SwingDelisted[];
    }>(path);
    const historyPath = join(SWING_DIR, "history.json");
    const history = existsSync(historyPath) ? readJson<NonNullable<SwingView["history"]>>(historyPath) : null;
    // Bollingerkontrollen under dagen (09:20, 13:20) ersätter gårdagens läge för kandidaterna i samma swingfil.
    const check = IS_MOCK ? null : loadLatestBollinger();
    const pb = check?.pullback && check.pullback.swing_date === raw.date ? check.pullback : null;
    const bbs = check?.bbrekyl && check.bbrekyl.swing_date === raw.date ? check.bbrekyl : null;
    // Kontrollens rad för en strategi: läget mot banden och status i dag ersätter gårdagens läge.
    const merge = <T extends { levels: unknown | null }>(part: T | undefined, r: BollingerCheckRow | undefined): T | undefined => {
      if (!part?.levels || !r || !check) return part;
      const { ticker: _t, name: _n, today, ...pos } = r;
      return {
        ...part,
        bollinger: { ...pos, checked_at: check.checked_at },
        ...(today ? { today: { ...today, checked_at: check.checked_at } } : {}),
      };
    };
    const live = new Map((pb?.rows ?? []).map((r) => [r.ticker, r]));
    const liveBb = new Map((bbs?.rows ?? []).map((r) => [r.ticker, r]));
    const rows = raw.companies.map((c) => ({
      ...c,
      pullback: merge(c.pullback, live.get(c.ticker)),
      bbrekyl: merge(c.bbrekyl, liveBb.get(c.ticker)),
    }));
    return {
      date: raw.date,
      asOf: raw.as_of,
      expectedDay: raw.expected_day ?? null,
      coverage: raw.coverage ?? null,
      shortAsOf: raw.short_as_of,
      thresholds: raw.thresholds,
      rankingCriteria: raw.ranking_criteria ?? {},
      bbrekylCriteria: raw.bbrekyl_criteria ?? {},
      labels: raw.labels,
      strategies: raw.strategies ?? {},
      rows,
      missing: raw.missing ?? [],
      delisted: raw.delisted ?? [],
      bollingerCheck: (pb || bbs) && check
        ? {
          checked_at: check.checked_at, delay_note: check.delay_note,
          candidates: (pb?.candidates ?? 0) + (bbs?.candidates ?? 0), rows: (pb?.rows.length ?? 0) + (bbs?.rows.length ?? 0),
        }
        : null,
      history,
    };
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan Swing:`, err);
    return null;
  }
}

/** Datum med morgonrapport, nyast först. */
export function listMorningDates(): string[] {
  if (!existsSync(MORNING_DIR)) return [];
  return readdirSync(MORNING_DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.slice(0, -5))
    .filter((d) => DATE_RE.test(d))
    .sort()
    .reverse();
}

export function loadMorning(day: string): MorningReport {
  return readJson<MorningReport>(join(MORNING_DIR, `${day}.json`));
}

// Eftermiddagsuppdateringen (scripts/afternoon.py, schema/afternoon.schema.json).
const AFTERNOON_DIR = IS_MOCK ? join(DATA, "mock", "afternoon") : join(DATA, "afternoon");
export interface AfternoonReaction {
  ticker: string;
  name: string;
  scope: string;
  published: string;
  title: string;
  url: string;
  after_close: boolean;
  close: number | null;
  change_pct: number | null;
  relative_pp: number | null;
  /** Analytikerlänkar ur instruments.csv (Placeras taggsida, Affärsvärldens bolagssida); saknas i äldre filer. */
  links?: { placera: string | null; affarsvarlden: string | null };
}
export interface AfternoonReport {
  date: string;
  generated_at: string;
  scopes: string[];
  price_day: string | null;
  index: { ticker: string; name: string; change_pct: number | null };
  companies: AfternoonReaction[];
  errors: string[];
}

function afternoonDays(): string[] {
  if (!existsSync(AFTERNOON_DIR)) return [];
  return readdirSync(AFTERNOON_DIR)
    .filter((f) => f.endsWith(".json") && DATE_RE.test(f.slice(0, -5)))
    .map((f) => f.slice(0, -5))
    .sort();
}

/** Senaste eftermiddagsuppdateringen, eller null. Valfri fil; en trasig fil visas inte. */
export function loadLatestAfternoon(): AfternoonReport | null {
  const days = afternoonDays();
  return days.length > 0 ? loadAfternoon(days[days.length - 1]) : null;
}

/** Senaste eftermiddagsuppdateringen före ett datum, högst fyra dagar bakåt (förra handelsdagen
 * även över en helg och en helgdag) – morgonrapportens kursreaktion – eller null. */
export function loadAfternoonBefore(day: string): AfternoonReport | null {
  const earliest = new Date(`${day}T12:00:00Z`);
  earliest.setUTCDate(earliest.getUTCDate() - 4);
  const from = earliest.toISOString().slice(0, 10);
  const days = afternoonDays().filter((d) => d < day && d >= from);
  return days.length > 0 ? loadAfternoon(days[days.length - 1]) : null;
}

/**
 * Rapportutfallen för kursreaktionens bolag, per ticker: ur körningen bolaget listades i
 * (morgon eller uppdatering), annars ur veckans fil (måndagens uppsamling).
 */
export function loadReactionOutcomes(report: AfternoonReport): Map<string, ReportResult> {
  const out = new Map<string, ReportResult>();
  const scopes = [...new Set(report.companies.map((c) => c.scope))];
  const weeks = [...new Set(report.companies.map((c) => {
    const [y, m, d] = c.scope.slice(0, 10).split("-").map(Number);
    return isoWeek(y, m, d);
  }))];
  for (const scope of [...scopes, ...weeks]) {
    for (const r of loadResults(scope)?.reports ?? []) if (!out.has(r.ticker)) out.set(r.ticker, r);
  }
  return out;
}

function loadAfternoon(day: string): AfternoonReport | null {
  const path = join(AFTERNOON_DIR, `${day}.json`);
  if (!existsSync(path)) return null;
  try {
    return readJson<AfternoonReport>(path);
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan eftermiddagsuppdatering:`, err);
    return null;
  }
}

/** Rapportutfallen från en dags uppdateringar (data/results/updates/<datum>T<hhmm>.json), sammanslagna. */
export function loadUpdateResults(day: string): ResultsView | null {
  const dir = join(RESULTS_DIR, "updates");
  if (!existsSync(dir)) return null;
  const files = readdirSync(dir)
    .filter((f) => f.startsWith(`${day}T`) && f.endsWith(".json"))
    .sort();
  const all = files.map((f) => loadResults(f.slice(0, -5))).filter((r) => r !== null);
  if (all.length === 0) return null;
  return {
    reports: all.flatMap((r) => r.reports).sort((a, b) => a.published.localeCompare(b.published)),
    missingBatches: all.flatMap((r) => r.missingBatches),
    // En underkänd rapport som en senare uppdatering samma dag fått fram visas inte som underkänd.
    rejected: all
      .flatMap((r) => r.rejected)
      .filter((x) => !all.some((r) => r.reports.some((y) => y.ticker === x.ticker))),
  };
}
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
  /** Skillnad mot index i procentenheter; null/saknas om ingen jämförelse finns. */
  relative_pp?: number | null;
}

export interface IndexQuote {
  ticker: string;
  name: string;
  change_pct: number;
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

import type { Tone } from "./tone";
export type { Tone } from "./tone";
import type { CalendarEvent } from "./calendar";
import { shortUntil, type SignalRow } from "./signals";
import type { Consensus } from "./consensus";
import type { RejectedReport, ReportResult, ResultsView } from "./results";
import type { ValuationRow } from "./valuation";
import { isoWeek } from "./weekCheck";

export interface Sentiment {
  label: Tone;
  rationale: string;
}

export interface ItemView {
  id: string;
  sentiment: Sentiment | null;
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
  /** Jämförelseindex för veckan (ur kursfilen), om det kunde hämtas. */
  index: IndexQuote | null;
  policyRate: PolicyRate | null;
  analysis: Analysis | null;
  /** Rapportkalendern för veckan efter, om den finns. */
  calendar: CalendarView | null;
  /** Signalöversikten per bolag, om den finns. */
  signals: SignalsView | null;
  /** Kurs mot vinst i år, om den finns. */
  valuation: ValuationView | null;
  /** Rapportutfall för bolag som rapporterat under veckan, om de finns. */
  results: ResultsView | null;
  sectors: SectorView[];
}

export interface SignalsView {
  generatedAt: string;
  thresholds: {
    volume_ratio: number;
    near_52w_pct: number;
    short_aggregate_pp: number;
    big_move_pp: number;
    pe_up_pct: number;
    /** Blankningssignalen bara vid ändrad sammanlagd blankning (saknas i äldre filer). */
    short_only_aggregate?: boolean;
  };
  rows: SignalRow[];
  /** Blankningsunderlaget gäller bara t.o.m. denna dag (före veckans slut), annars null. */
  shortUntil: string | null;
}

/** Signalfilen är valfri – saknas den, eller är den trasig, visas ingen flik för den. */
export function loadSignals(week: string): SignalsView | null {
  const path = join(SIGNALS_DIR, `${week}.json`);
  if (!existsSync(path)) return null;
  try {
    const raw = readJson<{
      generated_at: string; thresholds: SignalsView["thresholds"]; companies: SignalRow[];
      period: { end: string }; short_as_of?: string | null;
    }>(path);
    return {
      generatedAt: raw.generated_at, thresholds: raw.thresholds, rows: raw.companies,
      shortUntil: shortUntil(raw.short_as_of ?? null, raw.period.end),
    };
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan signaler:`, err);
    return null;
  }
}

export interface CalendarView {
  week: string;
  generatedAt: string;
  /** Datum för konsensusfilen som visas, om någon finns. */
  consensusDate: string | null;
  events: (CalendarEvent & { consensus: Consensus | null })[];
}

/** Veckan efter en veckas söndag ("2026-10-18" -> "2026-W43"). */
export function weekAfter(sunday: string): string {
  const d = new Date(`${sunday}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return isoWeek(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate());
}

const CONSENSUS_DIR = IS_MOCK ? join(DATA, "mock", "consensus") : join(DATA, "consensus");
const RESULTS_DIR = IS_MOCK ? join(DATA, "mock", "results") : join(DATA, "results");
const VALUATION_DIR = IS_MOCK ? join(DATA, "mock", "valuation") : join(DATA, "valuation");

export interface ValuationView {
  asOf: string;
  yearStart: string;
  indexYtd: number | null;
  thresholds: { verdict_pct: number; pe_max: number; big_move_pct: number; eps_change_max_pct: number };
  rows: ValuationRow[];
}

/** Kurs mot vinst (scripts/valuation.py) – valfri fil; saknas den visas ingen flik. */
export function loadValuation(week: string): ValuationView | null {
  const path = join(VALUATION_DIR, `${week}.json`);
  if (!existsSync(path)) return null;
  try {
    const raw = readJson<{ as_of: string; year_start: string; index: { ytd_pct: number | null };
      thresholds: ValuationView["thresholds"]; companies: ValuationRow[] }>(path);
    return { asOf: raw.as_of, yearStart: raw.year_start, indexYtd: raw.index.ytd_pct, thresholds: raw.thresholds,
      rows: raw.companies };
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan Kurs mot vinst:`, err);
    return null;
  }
}

/** Rapportutfall (scripts/results.py) för en vecka, en morgonkörning (datum,
 * data/results/morning/<datum>.json) eller en uppdatering (datumThhmm, data/results/updates/).
 * Valfri fil; saknas den visas ingenting. */
export function loadResults(scope: string): ResultsView | null {
  const path = DATE_RE.test(scope)
    ? join(RESULTS_DIR, "morning", `${scope}.json`)
    : UPDATE_RE.test(scope)
      ? join(RESULTS_DIR, "updates", `${scope}.json`)
      : join(RESULTS_DIR, `${scope}.json`);
  if (!existsSync(path)) return null;
  try {
    const raw = readJson<{ reports: ReportResult[]; missing_batches: number[]; rejected?: RejectedReport[] }>(path);
    return { reports: raw.reports, missingBatches: raw.missing_batches, rejected: raw.rejected ?? [] };
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan rapportutfall:`, err);
    return null;
  }
}

/** Senaste konsensusfilen (scripts/consensus.py): datum och konsensus per ticker, eller null. */
export function loadLatestConsensus(): { date: string; byTicker: Map<string, Consensus> } | null {
  if (!existsSync(CONSENSUS_DIR)) return null;
  const files = readdirSync(CONSENSUS_DIR).filter((f) => DATE_RE.test(f.slice(0, -5)) && f.endsWith(".json")).sort();
  if (files.length === 0) return null;
  try {
    const raw = readJson<{ date: string; companies: Consensus[] }>(join(CONSENSUS_DIR, files[files.length - 1]));
    return { date: raw.date, byTicker: new Map(raw.companies.map((c) => [c.ticker, c])) };
  } catch (err) {
    console.warn("[veckans-finans] kunde inte läsa konsensusfilen, visar kalendern utan konsensus:", err);
    return null;
  }
}

/** Kalenderfilen är valfri – saknas den, eller är den trasig, visas ingen flik för den. */
export function loadCalendar(week: string): CalendarView | null {
  const path = join(CALENDAR_DIR, `${week}.json`);
  if (!existsSync(path)) return null;
  try {
    const raw = readJson<{ week: string; generated_at: string; events: CalendarEvent[] }>(path);
    const consensus = loadLatestConsensus();
    return {
      week: raw.week,
      generatedAt: raw.generated_at,
      consensusDate: consensus?.date ?? null,
      events: raw.events.map((e) => ({
        ...e,
        consensus: e.kind === "rapport" ? (consensus?.byTicker.get(e.ticker) ?? null) : null,
      })),
    };
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan kalender:`, err);
    return null;
  }
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

// Blanknings- och insynsanalys (scripts/registers.py, schema/analysis.schema.json).
export interface ShortChange {
  ticker: string;
  name: string;
  start_pct: number;
  end_pct: number;
  change_pp: number;
  /** Senaste positionsdatum under veckan (positioner ≥ 0,5 %; saknas i äldre filer). */
  last_change_date?: string;
  holders?: { holder: string; start_pct: number; end_pct: number; change_pp: number; date?: string }[];
}
export interface ShortBlock {
  available: boolean;
  note: string | null;
  start_as_of: string | null;
  end_as_of: string | null;
  changes: ShortChange[];
}
export interface InsiderTransaction {
  person: string;
  position: string;
  closely_associated: string;
  side: "köp" | "försäljning";
  transaction_date: string;
  published_date: string;
  volume: number;
  price: number;
  value_sek: number;
  /** Utanför handelsplats enligt FI – ingår i summorna men markeras (saknas i äldre filer). */
  off_exchange?: boolean;
  /** Arbetsdagar från affär till publicering, och om det var fler än tre (saknas i äldre filer). */
  business_days_to_publication?: number;
  late_report?: boolean;
}
export interface InsiderCompany {
  ticker: string;
  name: string;
  buy_value_sek: number;
  sell_value_sek: number;
  net_value_sek: number;
  buyers: string[];
  sellers: string[];
  cluster_buy: boolean;
  cluster_sell: boolean;
  /** Målbolag för uppköpsbud/avnotering enligt veckans nyheter (saknas i äldre filer). */
  under_offer?: boolean;
  transactions: InsiderTransaction[];
}
export interface Analysis {
  week: string;
  top_n: number;
  short_positions: ShortBlock;
  short_aggregate: ShortBlock;
  insider: {
    threshold_sek: number;
    cluster_min_persons: number;
    days_covered: string[];
    days_missing: string[];
    companies: InsiderCompany[];
  };
}

/** Analysfilen är valfri – saknas den, eller är den trasig, visas ingen flik för den. */
export function loadAnalysis(week: string): Analysis | null {
  const path = join(ANALYSIS_DIR, `${week}.json`);
  if (!existsSync(path)) return null;
  try {
    return readJson<Analysis>(path);
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan blankning/insyn:`, err);
    return null;
  }
}

/** Kursfilen är valfri – saknas den, eller är den trasig, visas sidan utan kurser. */
function loadPrices(week: string): { quotes: Record<string, Quote>; index: IndexQuote | null } | null {
  const path = join(PRICES_DIR, `${week}.json`);
  if (!existsSync(path)) return null;
  try {
    const data = readJson<{ quotes: Record<string, Quote>; index?: IndexQuote }>(path);
    return data.quotes ? { quotes: data.quotes, index: data.index ?? null } : null;
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
  sentiment?: { label: Tone; rationale: string; basis: number[] };
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
  const prices = loadPrices(week);
  const quotes = prices?.quotes ?? null;

  const toItem = (it: RawItem): ItemView => ({
    id: it.id,
    // basis (citatindex) används bara av valideringen; evidence läses aldrig in i sidan.
    sentiment: it.sentiment ? { label: it.sentiment.label, rationale: it.sentiment.rationale } : null,
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
    index: prices?.index ?? null,
    policyRate: pr
      ? {
          value: pr.value,
          date: pr.date,
          previousValue: pr.previous_value,
          changedOn: pr.changed_on,
          sourceUrl: pr.source_url,
        }
      : null,
    analysis: loadAnalysis(week),
    calendar: loadCalendar(weekAfter(raw.period.end)),
    signals: loadSignals(week),
    results: loadResults(week),
    valuation: loadValuation(week),
    sectors: sectorViews,
  };
}

export function weekLabel(week: string): string {
  const [year, w] = week.split("-W");
  return `Vecka ${Number(w)}, ${year}`;
}

export function formatDate(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("sv-SE", { day: "numeric", month: "long" });
}

const BOLLINGER_DIR = IS_MOCK ? join(DATA, "mock", "bollinger") : join(DATA, "bollinger");

export interface BollingerRow {
  ticker: string;
  name: string;
  price: number;
  upper: number;
  /** Kursen mot övre bandet i procent (≥ 0 vid eller över, negativ inom 1 % under). */
  vs_upper_pct: number;
}
export interface BollingerCheck {
  date: string;
  checked_at: string;
  delay_note: string;
  thresholds: { period: number; width: number; near_pct: number };
  checked: number;
  missing: number;
  over: BollingerRow[];
  near: BollingerRow[];
  /** Pullback-kandidaterna i dagens swingfil, med kursen mot båda banden (saknas i äldre filer, null utan swingfil). */
  pullback?: BollingerStrategySection | null;
  /** Samma avsnitt för Rekyl efter Bollingerutbrott (saknas i äldre filer, null utan swingfil). */
  bbrekyl?: BollingerStrategySection | null;
}

/** En kandidats rad i Bollingerkontrollen: läget mot banden och status i dag. */
export type BollingerCheckRow = PullbackBollinger & { ticker: string; name: string; today?: Omit<PullbackToday, "checked_at"> | null };
/** En strategis avsnitt i Bollingerkontrollen (scripts/bollinger.py strategy_section). */
export interface BollingerStrategySection {
  swing_date: string;
  candidates: number;
  rows: BollingerCheckRow[];
  /** Föregående kontroll samma dag, ändringarna sedan dess och notisens text (saknas i äldre filer). */
  status_since?: string | null;
  changes?: { ticker: string; name: string; from: string | null; to: string }[];
  notice?: string;
}
/** Senaste Bollingerkontrollen (data/bollinger/<datum>-<hhmm>.json, scripts/bollinger.py), eller null. */
export function loadLatestBollinger(): BollingerCheck | null {
  if (!existsSync(BOLLINGER_DIR)) return null;
  const files = readdirSync(BOLLINGER_DIR).filter((f) => /^\d{4}-\d{2}-\d{2}-\d{4}\.json$/.test(f)).sort();
  if (files.length === 0) return null;
  const path = join(BOLLINGER_DIR, files[files.length - 1]);
  try {
    return readJson<BollingerCheck>(path);
  } catch (err) {
    console.warn(`[veckans-finans] kunde inte läsa ${path}, visar sidan utan Bollingerkontroll:`, err);
    return null;
  }
}
