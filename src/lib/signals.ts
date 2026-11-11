/**
 * Visningstexter och sortering för signaltabellen (scripts/signals.py).
 * Ren modul (testas med node --test).
 */

export type Signal =
  | "insyn"
  | "blankning"
  | "avviker"
  | "stor-rörelse"
  | "omsättning"
  | "pe-upp"
  | "52v-högsta"
  | "52v-lägsta";

export const SIGNAL_LABELS: Record<Signal, string> = {
  insyn: "Insyn",
  blankning: "Blankning",
  avviker: "Avviker mot index",
  "stor-rörelse": "Stor rörelse mot index",
  omsättning: "Hög omsättning",
  "pe-upp": "P/E +30 % i år",
  "52v-högsta": "Nära 52 v högsta",
  "52v-lägsta": "Nära 52 v lägsta",
};

export interface SignalRow {
  ticker: string;
  name: string;
  sector: string | null;
  change_pct: number | null;
  relative_pp: number | null;
  volume_ratio: number | null;
  from_high_pct: number | null;
  from_low_pct: number | null;
  tone: "positiv" | "negativ" | "neutral" | "blandad" | null;
  news: number;
  insider_net_sek: number | null;
  insider_buyers: number;
  insider_sellers: number;
  /** Bara i äldre filer (blankning räknas inte sedan 2026-11-11). */
  short_positions_pp?: number | null;
  short_aggregate_pp?: number | null;
  signals: Signal[];
}

const fmt = (digits: number) =>
  new Intl.NumberFormat("sv-SE", { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: "exceptZero" });

/** "+9,0 %", "−5,1 %", "0,0 %"; tomt (–) om värdet saknas. */
export function signedPct(value: number | null, digits = 1): string {
  return value === null ? "–" : `${fmt(digits).format(value)} %`;
}

/** Omsättning mot snitt: "3,0×". */
export function ratio(value: number | null): string {
  return value === null ? "–" : `${new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value)}×`;
}

/** Insyn netto i kronor, avrundat: "+229,5 mn kr", "+252 815 kr", "−1,2 mn kr", "0 kr". */
export function sek(value: number | null): string {
  if (value === null) return "–";
  if (Math.abs(value) >= 1_000_000) return `${fmt(1).format(value / 1_000_000)} mn kr`;
  return `${fmt(0).format(value)} kr`;
}

/** Blankningsförändring: enskilda positioner över 0,5 % går före aggregatet. */
export function shortChange(row: Pick<SignalRow, "short_positions_pp" | "short_aggregate_pp">): number | null {
  return row.short_positions_pp ?? row.short_aggregate_pp ?? null;
}

/**
 * Etikett och färgklass för en signal i tabellen. Insyn visas som "Insynsköp" eller
 * "Insynsförsäljning" efter nettot; går köp och försäljningar jämnt ut står det bara "Insyn".
 */
export function signalChip(
  signal: Signal, row: Pick<SignalRow, "insider_net_sek">, shortUntilDay: string | null = null,
): { label: string; cls: string } {
  if (signal === "insyn") {
    const net = row.insider_net_sek ?? 0;
    if (net > 0) return { label: "Insynsköp", cls: "insyn-kop" };
    if (net < 0) return { label: "Insynsförsäljning", cls: "insyn-salj" };
    return { label: "Insyn", cls: "insyn" };
  }
  if (signal === "blankning" && shortUntilDay) return { label: `Blankning t.o.m. ${dayMonth(shortUntilDay)}`, cls: signal };
  return { label: SIGNAL_LABELS[signal], cls: signal };
}

/** "2026-10-27" -> "27/10". */
export function dayMonth(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d}/${m}`;
}

/**
 * Blankningsunderlagets sista dag om den ligger före veckans slut (då är blankningssignalen inte aktuell
 * för hela veckan, t.ex. vecka 44 2026 när FI-hämtningen stoppades 29/10), annars null.
 */
export function shortUntil(shortAsOf: string | null, weekEnd: string): string | null {
  return shortAsOf !== null && shortAsOf < weekEnd ? shortAsOf : null;
}

/** Signaler som ensamma räcker för "Starka signaler": de säger något utöver en rapportreaktion. Blankning räknas
 *  inte (sedan 2026-11-11), inte heller i äldre veckor där signalen finns kvar. */
export const STRONG_SIGNALS: ReadonlySet<Signal> = new Set<Signal>([
  "insyn", "avviker", "52v-högsta", "52v-lägsta", "pe-upp",
]);

/**
 * "Starka signaler": minst två signaler, eller minst en stark (STRONG_SIGNALS). För ett bolag som
 * rapporterat under veckan räknas en stor kursrörelse inte – den är rapportreaktionen, som redan syns i
 * kursreaktionen och rapportutfallen. Vecka 43: 163 bolag med signal, varav 102 med en enda.
 */
export function isStrong(signals: readonly Signal[], reportedThisWeek: boolean): boolean {
  // Blankningen räknas aldrig (äldre veckor visar signalen men den gör inte bolaget starkt).
  const counted = signals.filter((s) => s !== "blankning" && !(reportedThisWeek && s === "stor-rörelse"));
  return counted.length >= 2 || counted.some((s) => STRONG_SIGNALS.has(s));
}

/** Tabellens förvalda ordning: flest signaler först, därefter bolagsnamn (svensk ordning). */
export function defaultOrder<T extends { name: string; signals: unknown[] }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => b.signals.length - a.signals.length || a.name.localeCompare(b.name, "sv"));
}

/**
 * Jämför två värden för sortering. Saknade värden hamnar alltid sist, oavsett riktning.
 * Text jämförs på svenska (å, ä, ö sist).
 */
export function compareValues(a: number | string | null, b: number | string | null, ascending: boolean): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  const c = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "sv");
  return ascending ? c : -c;
}
