// Tester för fliken Swing (src/lib/swing.ts). Körs med `npm run test:js`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  decidedShareText, isPullbackLabel, labelClass, labelCounts, levelText, netText, priceText, reportText, riskText, shareText,
  altStopText, bbLevelText, ciShareText, evalName, evalTraits, ppText, successCount, breakoutLines, criteriaLines, depthsText, momentumText, todayVolumeText, volumeRuleLines, missingText, resistanceRText, todayInvalid, todayLines, todayOrder, TODAY_STATUS,
  bollingerSourceText, chipText, pctBText, rankText, rankTitle, rText, signalText, srDistText, srInsideText, srLevelText, upperRText, vsBaseText,
} from "../src/lib/swing.ts";

test("etiketternas klasser och antal", () => {
  assert.equal(labelClass("utbrott-omsattning"), "breakout");
  assert.equal(labelClass("rsi2-under-10"), "rsi");
  assert.equal(labelClass("trend-ned"), "trend");
  assert.equal(labelClass("efter-rapport-over"), "report");
  assert.deepEqual(
    labelCounts([{ labels: ["utbrott", "trend-upp"] }, { labels: ["trend-upp"] }, { labels: [] }], ["utbrott", "trend-upp", "trend-ned"]),
    [{ label: "utbrott", count: 1 }, { label: "trend-upp", count: 2 }, { label: "trend-ned", count: 0 }],
  );
});

test("historik: andel, osäkerhet, för få fall och skillnad mot alla dagar", () => {
  assert.equal(shareText({ n: 763, better: 0.4928, ci: 0.0355 }), "49,3 % (±3,6)");
  assert.equal(shareText({ n: 7, better: 0.4286, ci: 0.37 }), "42,9 % (för få fall)");
  assert.equal(shareText({ n: 0, better: null, ci: null }), "–");
  assert.equal(shareText(undefined), "–");
  const base = { better: 0.473 };
  assert.equal(vsBaseText({ n: 763, better: 0.493 }, base), "+2,0 pe");
  assert.equal(vsBaseText({ n: 8764, better: 0.472 }, base), "−0,1 pe");
  assert.equal(vsBaseText({ n: 7, better: 0.9 }, base), "–");
});

test("dagar sedan rapport", () => {
  assert.equal(reportText(null, null), "–");
  assert.equal(reportText(0, "över"), "reaktionsdag (över förväntan)");
  assert.equal(reportText(1, null), "1 dag sedan");
  assert.equal(reportText(3, "under"), "3 dagar sedan (under förväntan)");
});

test("pullback i trend: texter", () => {
  assert.equal(labelClass("rekyl-vandsignal"), "pullback");
  assert.equal(labelClass("bollinger-ovre"), "breakout");
  assert.ok(isPullbackLabel("rekyl-i-trend") && !isPullbackLabel("trend-upp"));
  assert.equal(priceText(195.234), "195,23");
  assert.equal(priceText(null), "–");
  assert.equal(signalText("hammer"), "Hammer");
  assert.equal(signalText("engulfing"), "Bullish engulfing");
  assert.equal(signalText(null), "–");
  assert.equal(levelText("ema20"), "20 EMA");
  assert.equal(riskText(2.83, 1.4), "2,8 % · 1,4 ATR");
  assert.equal(riskText(2.83, null), "2,8 %");
  assert.equal(decidedShareText(27, 56), "48,2 % (27 av 56)");
  assert.equal(decidedShareText(3, 10), "30,0 % (3 av 10), för få fall");
  assert.equal(decidedShareText(0, 0), "–");
  assert.equal(netText(0.42, 0.153), "+0,4 % (+0,15 R)");
  assert.equal(netText(-1.2, null), "−1,2 %");
  assert.equal(netText(null, null), "–");
});

test("stöd och motstånd: texter", () => {
  const zon = { kind: "zon", low: 178.5, high: 180.2, touches: 3 };
  assert.equal(srLevelText(zon, "support"), "180,20 (zon, 3 vändningar)"); // övre kanten för stöd
  assert.equal(srLevelText(zon, "resistance"), "178,50 (zon, 3 vändningar)"); // nedre kanten för motstånd
  assert.equal(srLevelText({ kind: "20 EMA", low: 182.39, high: 182.39, touches: null }, "support"), "182,39 (20 EMA)");
  assert.equal(srLevelText({ ...zon, touches: 1 }, "support"), "180,20 (zon, 1 vändning)");
  assert.equal(srLevelText(null, "support"), "–");
  assert.equal(srDistText(1.52, 0.61, "support"), "−1,5 % · 0,6 ATR");
  assert.equal(srDistText(3.2, 1.4, "resistance"), "+3,2 % · 1,4 ATR");
  assert.equal(srDistText(0, 0, "support"), "0,0 % · 0,0 ATR"); // stängningen i zonen
  assert.equal(rText(2.44), "2,4 R");
  assert.equal(rText(null), "–");
});

test("ett enda R: motståndet i strategins R och alternativt stopp i procent", () => {
  assert.equal(resistanceRText({ room_r: 1.39, has_resistance: true }), "1,4 R"); // samma som rangkriteriet
  assert.equal(resistanceRText({ room_r: -0.53, has_resistance: true }), "−0,5 R");
  assert.equal(resistanceRText({ room_r: null, has_resistance: false }), "inget inom 120 d");
  assert.equal(resistanceRText(undefined), "–"); // äldre filer utan mätvärden
  assert.equal(altStopText(98.1, 97.35), "alt. stopp under stöd 97,35 (0,8 %)");
  assert.equal(altStopText(98.1, null), null);
});

test("status i dag för pullback-kandidaterna", () => {
  const base = { high: 98.5, low: 96.8, price: 98.2, trigger_seen_at: null, checked_at: "2026-11-09T13:20:02+01:00" };
  const w = todayLines({ ...base, status: "waiting" });
  assert.equal(w.main, "Väntar på trigger");
  assert.equal(w.seen, null);
  assert.equal(w.prices, "high 98,50 · low 96,80 · senast 98,20 · kl. 13:20");
  const t = todayLines({ ...base, status: "triggered", trigger_seen_at: "2026-11-09T09:20:05+01:00" });
  assert.equal(t.main, "Trigger bruten");
  assert.equal(t.seen, "trigger sedd kl. 09:20");
  assert.equal(todayLines({ ...base, status: "late", trigger_seen_at: "2026-11-09T09:20:05+01:00" }).main, "Sen: mer än 1R över trigger");
  const inv = todayLines({ ...base, status: "below_stop", trigger_seen_at: "2026-11-09T09:20:05+01:00" });
  assert.equal(inv.main, "Ogiltig: under stopp");
  assert.equal(inv.seen, null); // ogiltig: ingen tid för triggern
  assert.equal(todayLines({ ...base, status: "below_ma50" }).main, "Ogiltig: under 50 MA");
  assert.ok(todayInvalid("below_stop") && todayInvalid("below_ma50"));
  assert.ok(!todayInvalid("late") && !todayInvalid(undefined));
  assert.deepEqual(["waiting", "triggered", "late", "below_ma50", "base_broken", "below_stop"].map(todayOrder), [0, 1, 5, 6, 7, 8]);
  assert.equal(TODAY_STATUS.base_broken, "Basen bruten nedåt");
  assert.equal(todayInvalid("base_broken"), true);
  assert.deepEqual(["pivot_broken", "pivot_broken_low_vol", "pivot_broken_no_vol"].map(todayOrder), [2, 3, 4]);
  assert.equal(TODAY_STATUS.pivot_broken_low_vol, "Pivot bruten, låg volym");
  assert.equal(todayOrder(null), null);
  for (const text of Object.values(TODAY_STATUS)) assert.ok(!/köp|sälj|kursmål/i.test(text));
});

test("Rekyl efter Bollingerutbrott: texter", () => {
  const sd = (iso: string) => ({ "2026-11-04": "4 nov" } as Record<string, string>)[iso] ?? iso;
  const b = { day: "2026-11-04", days_ago: 3, volume_ratio: 4.14, report: null };
  assert.deepEqual(breakoutLines(b, true, sd), {
    main: "Utbrott 4 nov (3 d sedan), 4,1× omsättning", report: null, volume: "lägre omsättning i rekylen",
  });
  const r = breakoutLines({ ...b, report: { verdict: "över" } }, false, sd);
  assert.equal(r.report, "rapportdag, över förväntan");
  assert.equal(r.volume, "inte lägre omsättning i rekylen");
  assert.equal(breakoutLines({ ...b, report: { verdict: null } }, true, sd).report, "rapportdag");
  assert.equal(bbLevelText("mid"), "mittbandet");
  assert.equal(bbLevelText("ema20"), "20 EMA");
  assert.equal(bbLevelText("mellan"), "mellan mittbandet och 20 EMA");
  assert.equal(labelClass("bbrekyl-vandsignal"), "pullback");
});

test("Rekyl efter Bollingerutbrott: volymvillkoren och omsättningen i dag", () => {
  const th = { volumeRatio: 2, redRatio: 1.5, required: ["r2"] };
  const v = {
    breakout_ratio: 2.14, r1: true, red_days: 2, red_max_ratio: 0.7, r2: true, rekyl_avg_ratio: 0.67, r3: true,
    up_volume: 2000, down_volume: 1000, r4: true,
  };
  assert.deepEqual(volumeRuleLines(v, th), [
    { ok: true, text: "Utbrottet 2,1× 20-dagarssnittet (rangkriterium: minst 2,0×)" },
    { ok: true, text: "Röda dagar i rekylen högst 0,7× snittet (krav: högst 1,5×)" },
    { ok: true, text: "Rekylen i snitt 0,67× 20-dagarssnittet (rangkriterium: under 1,00×)" },
    { ok: true, text: "Uppdagar 67 % av omsättningen på upp- och neddagar (rangkriterium: minst 50 %)" },
  ]);
  const no = volumeRuleLines({ ...v, r1: false, breakout_ratio: 1.6, red_days: 0, red_max_ratio: null, up_volume: 0, down_volume: 0 }, th);
  assert.equal(no[0].ok, false);
  assert.equal(no[1].text, "Inga röda dagar i rekylen (krav)");
  assert.equal(no[3].text, "Inga upp- eller neddagar i rekylen (rangkriterium)");
  assert.equal(todayVolumeText({ ratio: 1.34, until: "09:00" }), "omsättning hittills 1,3× normalt (t.o.m. 09:00)");
  assert.equal(todayVolumeText(null), null);
});

test("RSI och Stoch RSI i kolumnen Ingång", () => {
  assert.deepEqual(momentumText({ rsi14: 72.4, stoch_k: 85.2, stoch_d: 79.6, warn: true }), {
    text: "RSI(14) 72 · Stoch RSI 85/80", warn: true,
  });
  assert.deepEqual(momentumText({ rsi14: 55, stoch_k: null, stoch_d: null, warn: false }), {
    text: "RSI(14) 55 · Stoch RSI –", warn: false,
  });
  assert.equal(momentumText(null), null);
  assert.equal(momentumText({ rsi14: null, stoch_k: null, stoch_d: null, warn: false }), null);
});

test("Momentum-utbrott (VCP): rekyldjup och kriterier", () => {
  assert.equal(depthsText([15.2, 8.04, 4.4]), "15 % → 8 % → 4,4 %");
  assert.equal(depthsText([10.71, 5.45, 2.2]), "11 % → 5,5 % → 2,2 %");
  assert.deepEqual(criteriaLines({ a: true, b: false }, { a: "Volymen avtar", b: "RS minst 80" }), [
    { ok: true, text: "Volymen avtar" },
    { ok: false, text: "RS minst 80" },
  ]);
  assert.equal(labelClass("vcp-utbrott"), "pullback");
  assert.equal(ciShareText(12, 47, [0.153, 0.397]), "25,5 % (12 av 47; 95 %: 15–40 %)");
  assert.equal(ciShareText(4, 6, [0.3, 0.9]), "66,7 % (4 av 6; 95 %: 30–90 %), för få fall");
  assert.equal(ciShareText(0, 0, null), "–");
});

test("bolag som saknas i Swing", () => {
  assert.equal(missingText({ reason: "no_data", bars: 0 }), "Yahoo saknar kursdata");
  assert.equal(missingText({ reason: "short_history", bars: 40 }), "för kort kurshistorik (40 handelsdagar)");
});

test("golden och death cross: dagar sedan korsningen", () => {
  assert.equal(chipText("golden-cross", "Golden cross", 3), "Golden cross (3 d)");
  assert.equal(chipText("death-cross", "Death cross", 0), "Death cross (i går)");
  assert.equal(chipText("golden-cross", "Golden cross", null), "Golden cross"); // äldre filer
  assert.equal(chipText("trend-upp", "Trend upp", 3), "Trend upp");
  assert.equal(labelClass("golden-cross"), "trend");
  assert.equal(labelClass("death-cross"), "trend");
});

test("Bollingerkolumnen för pullback-kandidater", () => {
  assert.equal(pctBText(0.75), "%B 0,75");
  assert.equal(pctBText(1.2), "%B 1,20");
  assert.equal(pctBText(null), "–");
  assert.equal(upperRText(1.24), "1,2 R kvar");
  assert.equal(upperRText(-0.4), "0,4 R över");
  assert.equal(upperRText(0), "vid bandet");
  assert.equal(upperRText(-0.02), "vid bandet");
  assert.equal(upperRText(null), "–");
  assert.equal(bollingerSourceText(undefined), "stängning");
  assert.equal(bollingerSourceText("2026-11-05T13:20:04+01:00"), "kl. 13:20");
});
test("kursen inne i en zon", () => {
  const z = { kind: "zon", low: 302.49, high: 307.95, inside: true };
  assert.equal(srInsideText(z, "resistance"), "inne i motståndszon 302,49–307,95");
  assert.equal(srInsideText(z, "support"), "inne i stödzon 302,49–307,95");
  assert.equal(srInsideText({ ...z, inside: false }, "support"), null);
  assert.equal(srInsideText(null, "support"), null);
});
test("rang: siffra för de fem första, antal kriterier och tooltip", () => {
  assert.deepEqual(rankText(1, 5, 6, 5), { main: "1", sub: "5 av 6 kriterier" });
  assert.deepEqual(rankText(6, 3, 6, 5), { main: "–", sub: "3 av 6 kriterier" });
  const texts = { a: "Vändsignal", b: "Stoppet högst 1,5 ATR bort" };
  assert.equal(rankTitle({ a: true, b: false }, texts),
    "Uppfyllda kriterier (1 av 2): Vändsignal. Inte uppfyllda: Stoppet högst 1,5 ATR bort. Räknat på gårdagens stängning.");
  assert.equal(rankTitle({ a: false, b: false }, texts).startsWith("Uppfyllda kriterier (0 av 2): inga."), true);
});
test("Swing-utvärderingen: texter", () => {
  const texts = { "strategi:vcp": "Strategi: Momentum-utbrott (VCP)", "etikett:utbrott": "Etikett: Utbrott" };
  assert.equal(evalName("strategi:vcp", texts), "Momentum-utbrott (VCP)");
  assert.equal(evalName("etikett:utbrott", texts), "Utbrott");
  assert.equal(evalName("okänd", texts), "okänd");
  assert.equal(ppText(1.25), "+1,3 pe");
  assert.equal(ppText(-0.4), "−0,4 pe");
  assert.equal(ppText(null), "–");
  assert.deepEqual(
    evalTraits({ rsi14: 72.3, stoch_k: null, support_r: 0.5, resistance_r: 3, volume_ratio: 1.8, report_5d: true }),
    ["RSI(14) 72,3", "omsättning 1,8× snittet", "stöd 0,5 R", "motstånd 3 R", "rapport senaste 5 dagarna"],
  );
});

test("Swing-utvärderingen: filtret för lyckade fall", () => {
  const idx: [string, string[], boolean, string | null, string | null, string][] = [
    ["2026-11-02", ["pullback"], false, "industri", "upp-lugn", "lyckad"],
    ["2026-11-02", ["vcp"], true, "bank", "upp-lugn", "ej"],
    ["2026-11-03", [], true, "industri", "ned-orolig", "lyckad"],
  ];
  assert.deepEqual(successCount(idx, { strategy: "", sector: "", regime: "" }), { successes: 2, cases: 3 });
  assert.deepEqual(successCount(idx, { strategy: "stark", sector: "", regime: "" }), { successes: 1, cases: 2 });
  assert.deepEqual(successCount(idx, { strategy: "pullback", sector: "industri", regime: "upp-lugn" }), { successes: 1, cases: 1 });
  assert.deepEqual(successCount(idx, { strategy: "", sector: "", regime: "ned-orolig" }), { successes: 1, cases: 1 });
});
