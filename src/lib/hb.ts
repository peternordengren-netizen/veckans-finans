/**
 * Handelsbanken-sammanfattningar (scripts/hb_manual.py, data/raw/hb/): vilka som visas överst på startsidan och
 * texterna. Ren modul (testas med node --test). Sammanfattningarna är skrivna med egna ord och märkta
 * "manuellt tillagd"; inga citat ur rapporterna.
 */

export interface HbSummary {
  id: string;
  file_name: string;
  kind: "morgonrapport" | "fastighetsrapport" | "annan";
  title: string;
  report_date: string;
  headline: string;
  points: string[];
  rates?: string | null;
  valuations?: string | null;
  segments: { name: string; view: string }[];
  companies: { ticker: string; name: string; note: string }[];
  /** b) Betydelse för aktiemarknaden: Claudes tolkning, inte Handelsbankens (saknas i äldre sammanfattningar). */
  impact?: {
    label: string;
    sectors: { sector: string; direction: Direction; reason: string }[];
    companies: { ticker: string; name: string; direction: Direction; reason: string }[];
  };
  /** c) Vad vår data visar: sidans värden (lagda av skriptet) och Claudes kommentar (saknas i äldre). */
  our_data?: { ticker: string; name: string; diverges: boolean; note: string; data?: HbCompanyData }[];
  data_sources?: { signals: string | null; valuation: string | null; calendar_from: string };
  label: string;
  source_url: string;
  summarized_at?: string;
}

export type Direction = "positiv" | "negativ" | "blandad";

/** Värdena i avsnitt c) (scripts/hb_manual.DATA_FIELDS). */
export interface HbCompanyData {
  week_change_pct: number | null;
  week_rel_index_pp: number | null;
  ytd_pct: number | null;
  ytd_rel_index_pp: number | null;
  short_change_pp: number | null;
  insider_net_sek: number | null;
  insider_buyers: number | null;
  insider_sellers: number | null;
  pe_now: number | null;
  pe_change_pct: number | null;
  next_report: string | null;
}

const MINUS = "−";
const fmt = (v: number, d: number) =>
  new Intl.NumberFormat("sv-SE", { minimumFractionDigits: d, maximumFractionDigits: d }).format(Math.abs(v));
const signed = (v: number | null, d: number, unit: string) =>
  v === null ? "–" : `${v > 0 ? "+" : v < 0 ? MINUS : ""}${fmt(v, d)} ${unit}`;

/** Cellerna i tabellen "Vad vår data visar": kurs mot index (vecka, i år), blankning, insyn, P/E i år, nästa rapport. */
export function dataCells(d: HbCompanyData | undefined, shortDate: (iso: string) => string): string[] {
  if (!d) return ["–", "–", "–", "–", "–", "–"];
  // Insyn: nettobeloppet senaste veckan (förvärv minus avyttringar), i miljoner kronor.
  const insider = d.insider_net_sek === null ? "–" : `${signed(d.insider_net_sek / 1e6, 1, "mkr")} netto`;
  return [
    signed(d.week_rel_index_pp, 1, "pe"),
    signed(d.ytd_rel_index_pp, 1, "pe"),
    signed(d.short_change_pp, 2, "pe"),
    insider,
    signed(d.pe_change_pct, 1, "%"),
    d.next_report ? shortDate(d.next_report) : "–",
  ];
}

/** Riktningens text och färgklass. */
export function directionText(d: Direction): { text: string; cls: string } {
  return d === "positiv" ? { text: "positiv", cls: "pos" } : d === "negativ" ? { text: "negativ", cls: "neg" } : { text: "blandad", cls: "" };
}

/** Hur länge (dagar efter rapportdatumet) en sammanfattning visas överst på startsidan, per rapporttyp. */
export const SHOW_DAYS: Record<HbSummary["kind"], number> = { morgonrapport: 3, fastighetsrapport: 200, annan: 14 };

export const KIND_NAMES: Record<HbSummary["kind"], string> = {
  morgonrapport: "Morgonrapporten",
  fastighetsrapport: "Fastighetsrapporten",
  annan: "Handelsbanken-rapport",
};

/** Den senaste sammanfattningen per rapporttyp som fortfarande visas (rapportdatum högst SHOW_DAYS dagar före today),
 *  i ordningen morgonrapport, fastighetsrapport, annan. today: ÅÅÅÅ-MM-DD. */
export function hbToShow(all: HbSummary[], today: string): HbSummary[] {
  const t = Date.parse(`${today}T00:00:00Z`);
  const out: HbSummary[] = [];
  for (const kind of ["morgonrapport", "fastighetsrapport", "annan"] as const) {
    const latest = all
      .filter((s) => s.kind === kind)
      .sort((a, b) => b.report_date.localeCompare(a.report_date) || (b.summarized_at ?? "").localeCompare(a.summarized_at ?? ""))[0];
    if (!latest) continue;
    const age = (t - Date.parse(`${latest.report_date}T00:00:00Z`)) / 86400000;
    if (age >= 0 && age <= SHOW_DAYS[kind]) out.push(latest);
  }
  return out;
}
