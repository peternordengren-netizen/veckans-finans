/**
 * Visningstexter för fliken Swing (scripts/swing.py, scripts/swing_history.py). Ren modul (testas med node --test).
 * Etiketterna är beskrivande; inga ord som uppmanar till köp eller försäljning.
 */

/** Under så många fall visas historiken som "för få fall". */
export const MIN_CASES = 30;

/** Etikettens färgklass: utbrott, RSI, trend eller rapport. */
export function labelClass(label: string): string {
  if (label.startsWith("utbrott") || label.startsWith("bollinger")) return "breakout";
  if (label.startsWith("rsi2")) return "rsi";
  if (label.startsWith("trend")) return "trend";
  if (label.startsWith("rekyl")) return "pullback";
  return "report";
}

/** Antal bolag per etikett, i etiketternas ordning. */
export function labelCounts(rows: { labels: string[] }[], order: string[]): { label: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const r of rows) for (const l of r.labels) counts.set(l, (counts.get(l) ?? 0) + 1);
  return order.map((label) => ({ label, count: counts.get(label) ?? 0 }));
}

const pctFmt = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/**
 * Andel fall bättre än index: "49,3 % (±3,6)", med "för få fall" under MIN_CASES och "–" utan fall.
 */
export function shareText(s: { n: number; better: number | null; ci: number | null } | undefined): string {
  if (!s || s.n === 0 || s.better === null) return "–";
  const share = `${pctFmt.format(s.better * 100)} %`;
  if (s.n < MIN_CASES) return `${share} (för få fall)`;
  return s.ci === null ? share : `${share} (±${pctFmt.format(s.ci * 100)})`;
}

/** Skillnad mot alla dagar i procentenheter: "+2,0 pe", "−0,1 pe"; "–" utan underlag eller vid för få fall. */
export function vsBaseText(s: { n: number; better: number | null } | undefined, base: { better: number | null } | undefined): string {
  if (!s || !base || s.better === null || base.better === null || s.n < MIN_CASES) return "–";
  const d = (s.better - base.better) * 100;
  return `${new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 1, maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(d)} pe`;
}

/** "3 dagar sedan (över förväntan)", "i dag", "–". */
export function reportText(days: number | null, verdict: string | null): string {
  if (days === null) return "–";
  const when = days === 0 ? "reaktionsdag" : days === 1 ? "1 dag sedan" : `${days} dagar sedan`;
  return verdict === "över" || verdict === "under" ? `${when} (${verdict} förväntan)` : when;
}

/** Etikettens färgklass för strategin Pullback i trend. */
export function isPullbackLabel(label: string): boolean {
  return label === "rekyl-i-trend" || label === "rekyl-vandsignal";
}

const priceFmt = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Kursnivå med två decimaler, "–" utan värde. */
export function priceText(v: number | null | undefined): string {
  return v === null || v === undefined ? "–" : priceFmt.format(v);
}

/** "Hammer", "Bullish engulfing" eller "–". */
export function signalText(s: "hammer" | "engulfing" | null): string {
  return s === "hammer" ? "Hammer" : s === "engulfing" ? "Bullish engulfing" : "–";
}

/** "20 EMA" eller "50 MA". */
export function levelText(l: "ema20" | "ma50"): string {
  return l === "ema20" ? "20 EMA" : "50 MA";
}

/** Avstånd till stoppet: "2,8 % · 1,4 ATR". */
export function riskText(pct: number | null, atr: number | null): string {
  const p = pct === null ? "–" : `${pctFmt.format(pct)} %`;
  return atr === null ? p : `${p} · ${pctFmt.format(atr)} ATR`;
}

/** Andel av de avgjorda fallen: "48,2 % (27 av 56)", "–" utan avgjorda; "(för få fall)" under MIN_CASES. */
export function decidedShareText(n: number, decided: number): string {
  if (decided === 0) return "–";
  const share = `${pctFmt.format((n / decided) * 100)} % (${n.toLocaleString("sv-SE")} av ${decided.toLocaleString("sv-SE")})`;
  return decided < MIN_CASES ? `${share}, för få fall` : share;
}

/** Snittresultat per avgjort fall (utan courtage): "+0,4 % (+0,15 R)". */
export function netText(pct: number | null, r: number | null): string {
  if (pct === null) return "–";
  const s = (v: number, d: number) =>
    new Intl.NumberFormat("sv-SE", { minimumFractionDigits: d, maximumFractionDigits: d, signDisplay: "exceptZero" }).format(v);
  return r === null ? `${s(pct, 1)} %` : `${s(pct, 1)} % (${s(r, 2)} R)`;
}
