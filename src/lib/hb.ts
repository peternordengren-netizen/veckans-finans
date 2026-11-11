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
  label: string;
  source_url: string;
  summarized_at?: string;
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
