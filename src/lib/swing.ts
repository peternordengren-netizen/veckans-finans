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
  if (label.startsWith("trend") || label.endsWith("-cross")) return "trend";
  if (label.startsWith("rekyl") || label.startsWith("bbrekyl")) return "pullback";
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

/** Nivån gårdagens low nådde i Rekyl efter Bollingerutbrott: "mittbandet", "20 EMA" eller "mellan mittbandet och 20 EMA". */
export function bbLevelText(l: "mid" | "ema20" | "mellan"): string {
  return l === "mid" ? "mittbandet" : l === "ema20" ? "20 EMA" : "mellan mittbandet och 20 EMA";
}

/** Utbrottsdagen: "Utbrott 4 nov (3 d sedan), 4,1× omsättning", rapportdagen med utfallet mot förväntan, och om
 *  omsättningen i rekylen varit lägre än på utbrottsdagen. */
export function breakoutLines(
  b: { day: string; days_ago: number; volume_ratio: number; report: { verdict: string | null } | null },
  lowerVolume: boolean,
  shortDate: (iso: string) => string,
): { main: string; report: string | null; volume: string } {
  const verdict = b.report?.verdict === "över" || b.report?.verdict === "under" ? `, ${b.report.verdict} förväntan` : "";
  return {
    main: `Utbrott ${shortDate(b.day)} (${b.days_ago} d sedan), ${pctFmt.format(b.volume_ratio)}× omsättning`,
    report: b.report ? `rapportdag${verdict}` : null,
    volume: lowerVolume ? "lägre omsättning i rekylen" : "inte lägre omsättning i rekylen",
  };
}

/** Volymvillkoren i Rekyl efter Bollingerutbrott (scripts/bbrekyl.volume_checks), ett per rad med ✓ (uppfyllt) eller
 *  ✗ (ej uppfyllt) och siffrorna. th: kraven ur swingfilen (utbrottet minst volumeRatio×, röda dagar högst redRatio×). */
export function volumeRuleLines(
  v: {
    breakout_ratio: number; r1: boolean; red_days: number; red_max_ratio: number | null; r2: boolean;
    rekyl_avg_ratio: number | null; r3: boolean; up_volume: number; down_volume: number; r4: boolean;
  },
  th: { volumeRatio: number; redRatio: number },
): { ok: boolean; text: string }[] {
  const x = (n: number) => `${pctFmt.format(n)}×`;
  const total = v.up_volume + v.down_volume;
  return [
    { ok: v.r1, text: `Utbrottet ${x(v.breakout_ratio)} 20-dagarssnittet (krav minst ${x(th.volumeRatio)})` },
    {
      ok: v.r2,
      text: v.red_days === 0 || v.red_max_ratio === null
        ? "Inga röda dagar i rekylen"
        : `Röda dagar i rekylen högst ${x(v.red_max_ratio)} snittet (krav högst ${x(th.redRatio)})`,
    },
    {
      ok: v.r3,
      text: v.rekyl_avg_ratio === null ? "Snittomsättning i rekylen saknas"
        : `Rekylen i snitt ${x(v.rekyl_avg_ratio)} 20-dagarssnittet (krav under 1,0×)`,
    },
    {
      ok: v.r4,
      text: total === 0 ? "Inga upp- eller neddagar i rekylen"
        : `Uppdagar ${Math.round((v.up_volume / total) * 100)} % av omsättningen på upp- och neddagar (krav minst 50 %)`,
    },
  ];
}

/** Dagens omsättning hittills mot normalt vid samma tid: "omsättning hittills 1,3× normalt (t.o.m. 09:15)". */
export function todayVolumeText(v: { ratio: number; until: string } | null | undefined): string | null {
  return v ? `omsättning hittills ${pctFmt.format(v.ratio)}× normalt (t.o.m. ${v.until})` : null;
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

/** Stöd- eller motståndsnivå: "180,20 (zon, 3 vändningar)" eller "182,39 (20 EMA)"; "–" utan nivå. */
export function srLevelText(
  l: { kind: string; low: number; high: number; touches: number | null } | null | undefined,
  side: "support" | "resistance",
): string {
  if (!l) return "–";
  const edge = side === "support" ? l.high : l.low;
  const what = l.kind === "zon" ? `zon, ${l.touches} ${l.touches === 1 ? "vändning" : "vändningar"}` : l.kind;
  return `${priceText(edge)} (${what})`;
}

/** Avstånd till nivån: "−1,5 % · 0,6 ATR" under kursen, "+3,2 % · 1,4 ATR" över. */
export function srDistText(dist_pct: number | null, dist_atr: number | null, side: "support" | "resistance"): string {
  if (dist_pct === null) return "–";
  const sign = side === "support" && dist_pct > 0 ? "−" : side === "resistance" && dist_pct > 0 ? "+" : "";
  const atr = dist_atr === null ? "" : ` · ${pctFmt.format(dist_atr)} ATR`;
  return `${sign}${pctFmt.format(dist_pct)} %${atr}`;
}

/** Kursen inne i zonen: "inne i motståndszon 302,49–307,95" / "inne i stödzon …"; null annars. */
export function srInsideText(
  l: { kind: string; low: number; high: number; inside?: boolean } | null | undefined,
  side: "support" | "resistance",
): string | null {
  if (!l || !l.inside) return null;
  return `inne i ${side === "support" ? "stödzon" : "motståndszon"} ${priceText(l.low)}–${priceText(l.high)}`;
}

/** Rangen i tabellen: rangen för de `top` första ("1"), annars "–"; poängen som "5 av 6 kriterier". */
export function rankText(rank: number, score: number, total: number, top: number): { main: string; sub: string } {
  return { main: rank <= top ? String(rank) : "–", sub: `${score} av ${total} kriterier` };
}

/** Tooltip med uppfyllda och ej uppfyllda kriterier, i kriteriernas ordning. */
export function rankTitle(criteria: Record<string, boolean>, texts: Record<string, string>): string {
  const keys = Object.keys(texts);
  const yes = keys.filter((k) => criteria[k]).map((k) => texts[k]);
  const no = keys.filter((k) => !criteria[k]).map((k) => texts[k]);
  return [
    `Uppfyllda kriterier (${yes.length} av ${keys.length}): ${yes.length ? yes.join("; ") : "inga"}.`,
    no.length ? `Inte uppfyllda: ${no.join("; ")}.` : "",
    "Räknat på gårdagens stängning.",
  ].filter(Boolean).join(" ");
}

/** Avstånd i R: "2,4 R"; "–" utan värde. */
export function rText(r: number | null | undefined): string {
  return r === null || r === undefined ? "–" : `${pctFmt.format(r)} R`;
}

/** Närmaste motstånd i strategins R (scripts/ranking.py room_r, samma R som stoppet, 2R och rangkriteriet):
 *  "1,4 R", "inget inom 120 d" utan motstånd; "–" utan mätvärden (äldre filer). */
export function resistanceRText(m: { room_r: number | null; has_resistance: boolean } | null | undefined): string {
  if (!m) return "–";
  return m.has_resistance ? rText(m.room_r) : "inget inom 120 d";
}

/** Stoppet under närmaste stöd som alternativ stoppnivå, med avståndet från referensen (triggernivån vid
 *  vändsignal) i procent, inget eget R: "alt. stopp under stöd 97,35 (0,8 %)"; null utan nivå. */
export function altStopText(ref: number, stop: number | null | undefined): string | null {
  if (stop === null || stop === undefined || !(ref > 0)) return null;
  return `alt. stopp under stöd ${priceText(stop)} (${pctFmt.format(((ref - stop) / ref) * 100)} %)`;
}

/** Varför ett noterat bolag saknas i tabellen: "Yahoo saknar kursdata" eller "för kort kurshistorik (40 dagar)". */
export function missingText(m: { reason: string; bars: number }): string {
  return m.reason === "no_data" ? "Yahoo saknar kursdata" : `för kort kurshistorik (${m.bars} handelsdagar)`;
}

/** Status i dag för en pullback-kandidat (scripts/bollinger.py TODAY_STATUS). Beskrivande, inga uppmaningar. */
export const TODAY_STATUS: Record<string, string> = {
  waiting: "Väntar på trigger",
  triggered: "Trigger bruten",
  late: "Sen: mer än 1R över trigger",
  below_ma50: "Ogiltig: under 50 MA",
  below_stop: "Ogiltig: under stopp",
};

/** Ogiltig i dag (under stoppet eller 50 MA): sorteras sist i strategin. */
export function todayInvalid(status: string | null | undefined): boolean {
  return status === "below_stop" || status === "below_ma50";
}

/** Sorteringsvärde för kolumnen Status i dag: väntar 0, trigger bruten 1, sen 2, under 50 MA 3, under stopp 4. */
export function todayOrder(status: string | null | undefined): number | null {
  const i = status ? Object.keys(TODAY_STATUS).indexOf(status) : -1;
  return i < 0 ? null : i;
}

/** Cellens rader: statusen, tiden då triggern först sågs ("trigger sedd kl. 09:20"), och dagens kurser. */
export function todayLines(t: { status: string; trigger_seen_at: string | null; high: number; low: number; price: number; checked_at: string }): {
  main: string; seen: string | null; prices: string;
} {
  return {
    main: TODAY_STATUS[t.status] ?? t.status,
    seen: t.trigger_seen_at && t.status !== "below_stop" && t.status !== "below_ma50"
      ? `trigger sedd ${bollingerSourceText(t.trigger_seen_at)}` : null,
    prices: `high ${priceText(t.high)} · low ${priceText(t.low)} · senast ${priceText(t.price)} · ${bollingerSourceText(t.checked_at)}`,
  };
}

/** Läget mellan Bollingerbanden (%B, 0 = undre bandet, 1 = övre): "%B 0,75"; "–" utan värde. */
export function pctBText(b: number | null | undefined): string {
  return b === null || b === undefined
    ? "–"
    : `%B ${new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(b)}`;
}

/** Avståndet från kursen till övre bandet i R: "1,2 R kvar", "0,4 R över", "vid bandet"; "–" utan värde. */
export function upperRText(r: number | null | undefined): string {
  if (r === null || r === undefined) return "–";
  if (Math.abs(r) < 0.05) return "vid bandet"; // avrundas till 0,0 R
  return r > 0 ? `${pctFmt.format(r)} R kvar` : `${pctFmt.format(-r)} R över`;
}

/** Var Bollingerläget kommer ifrån: "kl. 13:20" (kontrollen under dagen) eller "stängning" (gårdagens). */
export function bollingerSourceText(checkedAt: string | null | undefined): string {
  if (!checkedAt) return "stängning";
  const t = new Date(checkedAt).toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Stockholm" });
  return `kl. ${t}`;
}

/** Nivåns slag kort: "zon, 3 vändn." eller "20 EMA". */
export function srKindText(l: { kind: string; touches: number | null }): string {
  return l.kind === "zon" ? `zon, ${l.touches} vändn.` : l.kind;
}

/** Etikettens text i tabellen: golden och death cross med dagar sedan korsningen, "Golden cross (3 d)". */
export function chipText(label: string, name: string, crossDays: number | null | undefined): string {
  if ((label === "golden-cross" || label === "death-cross") && crossDays !== null && crossDays !== undefined) {
    return `${name} (${crossDays === 0 ? "i går" : `${crossDays} d`})`;
  }
  return name;
}
