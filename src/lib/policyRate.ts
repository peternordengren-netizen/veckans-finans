/**
 * Text för styrräntans nyckeltal. Ren modul (inga Astro- eller fs-beroenden)
 * så att den kan testas direkt med `node --test`.
 *
 * Regel: "Sänkt/Höjd från X %" visas bara om ändringen skedde under veckan.
 * Annars "oförändrad sedan <datum>" (eller utan datum om ingen ändring finns
 * i hämtad period).
 */

export interface PolicyRateInput {
  value: number;
  previousValue: number | null;
  changedOn: string | null;
}

/** 1.75 -> "1,75", 2 -> "2,00" (räntor visas alltid med två decimaler). */
export function formatRate(value: number): string {
  return new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
}

export function formatLongDate(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("sv-SE", { day: "numeric", month: "long", year: "numeric" });
}

/** Ändrades räntan under veckan (start–end, ISO-datum inklusive)? */
export function changedThisWeek(pr: PolicyRateInput, weekStart: string, weekEnd: string): boolean {
  return pr.changedOn !== null && pr.previousValue !== null && weekStart <= pr.changedOn && pr.changedOn <= weekEnd;
}

/** T.ex. "oförändrad sedan 1 oktober 2025" eller "sänkt från 2,00 % den 8 oktober 2026". */
export function describePolicyRate(pr: PolicyRateInput, weekStart: string, weekEnd: string): string {
  if (changedThisWeek(pr, weekStart, weekEnd)) {
    const verb = pr.value > pr.previousValue! ? "höjd" : "sänkt";
    return `${verb} från ${formatRate(pr.previousValue!)} % den ${formatLongDate(pr.changedOn!)}`;
  }
  return pr.changedOn ? `oförändrad sedan ${formatLongDate(pr.changedOn)}` : "oförändrad under hämtad period";
}

export interface PolicyDecision {
  value: number;
  decided_on: string;
  effective_from: string;
  change: "höjs" | "sänks" | "oförändrad";
  url: string;
}

const SHORT_MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

/** "2026-11-11" -> "11 nov". */
export function formatShortDate(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${SHORT_MONTHS[m - 1]}`;
}

/**
 * "Beslutad: 2,00 % från 11 nov" när Riksbanken beslutat en ny nivå som ännu inte gäller den dag nyckeltalet avser
 * (beslutet fattat efter att gällande nivå började gälla, och tillämpas efter observationsdagen). Annars null, även
 * för ett beslut om oförändrad ränta.
 */
export function pendingDecisionText(
  decision: PolicyDecision | null | undefined,
  rate: PolicyRateInput & { date: string },
): string | null {
  if (!decision || decision.value === rate.value) return null;
  if (decision.effective_from <= rate.date) return null;
  if (rate.changedOn !== null && decision.decided_on < rate.changedOn) return null;
  return `Beslutad: ${formatRate(decision.value)} % från ${formatShortDate(decision.effective_from)}`;
}
