/**
 * Visningstexter för procentjämförelser – utan "pe"/"procentenheter" på sidan.
 * Ren modul (testas med node --test). Bara visning; datan är oförändrad.
 */

const nf = (min: number, max: number, sign = false) =>
  new Intl.NumberFormat("sv-SE", {
    minimumFractionDigits: min,
    maximumFractionDigits: max,
    ...(sign ? { signDisplay: "exceptZero" as const } : {}),
  });

const MONTHS = ["jan", "feb", "mars", "apr", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"];

/** "2026-10-07" -> "7 okt" (utan årtal). */
export function shortDate(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
}

/**
 * Datumintervall utan årtal: ett datum -> "7 okt"; samma månad -> "6–9 okt";
 * olika månader -> "30 sep–2 okt". Ordningen i listan spelar ingen roll.
 */
export function dateRange(isoDates: string[]): string {
  const sorted = [...new Set(isoDates)].sort();
  if (sorted.length === 0) return "";
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  if (first === last) return shortDate(first);
  const [, m1, d1] = first.split("-").map(Number);
  const [, m2] = last.split("-").map(Number);
  return m1 === m2 ? `${d1}–${shortDate(last)}` : `${shortDate(first)}–${shortDate(last)}`;
}

/** Riktning med pil och ord, så att den inte bara syns som färg. */
export function direction(change: number): string {
  if (change > 0) return "▲ ökad";
  if (change < 0) return "▼ minskad";
  return "oförändrad";
}

/**
 * Riktning + text för en blankningsförändring. En ny innehavare (newHolder, start 0)
 * får bara pilen: "▲" + "ny position 1,05 % av aktierna" – inte "▲ ökad".
 */
export function shortChange(
  start: number,
  end: number,
  change: number,
  opts: { threshold: number; newHolder?: boolean },
): { dir: string; text: string } {
  const text = shortRange(start, end, change, opts);
  return { dir: opts.newHolder && start === 0 ? "▲" : direction(change), text };
}

/** 2.2 -> "2,2", 0.68 -> "0,68", 2 -> "2,0" */
export function pct(value: number): string {
  return nf(1, 2).format(value);
}

/**
 * Skillnad mot index (data: procentenheter, en decimal):
 * -5.3 -> "5,3 % sämre än index", 2.1 -> "2,1 % bättre än index", 0.0 -> "i linje med index".
 */
export function relativeToIndex(relativePp: number): string {
  const rounded = Math.round(relativePp * 10) / 10;
  if (rounded === 0) return "i linje med index";
  const abs = nf(1, 1).format(Math.abs(rounded));
  return rounded > 0 ? `${abs} % bättre än index` : `${abs} % sämre än index`;
}

/**
 * Blankningsandel från och till, med förändringen inom parentes:
 *   1.1 -> 3.3            "1,1 % → 3,3 % av aktierna (+2,2)"
 * Under rapportgränsen (värdet 0 i datan) skrivs som "under <gräns> %":
 *   0 -> 1.2 (bolag)      "under 0,5 % → 1,2 % av aktierna (+1,2)"
 *   1.05 -> 0             "1,05 % → under 0,5 % av aktierna (−1,05)"
 * Ny innehavare (newHolder och start 0):  "ny position 0,6 % av aktierna"
 */
export function shortRange(
  start: number,
  end: number,
  change: number,
  opts: { threshold: number; newHolder?: boolean },
): string {
  const t = `under ${pct(opts.threshold)} %`;
  if (opts.newHolder && start === 0) return `ny position ${pct(end)} % av aktierna`;
  const from = start === 0 ? t : `${pct(start)} %`;
  const to = end === 0 ? t : `${pct(end)} %`;
  return `${from} → ${to} av aktierna (${nf(1, 2, true).format(change)})`;
}

/**
 * Fellistan i morgon- och eftermiddagsrapporten utan varningar om blankning (saknade FI-filer, säkerhetskopian).
 * Blankningen är borttagen sedan 2026-11-11, men äldre rapporter har kvar felen i filen.
 */
export function withoutShortWarnings(errors: readonly string[]): string[] {
  return errors.filter((e) => !/^(blankning|FI blankning|FI historik)\b/i.test(e));
}
