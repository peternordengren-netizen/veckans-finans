// Tester för fliken Swing (src/lib/swing.ts). Körs med `npm run test:js`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  decidedShareText, isPullbackLabel, labelClass, labelCounts, levelText, netText, priceText, reportText, riskText, shareText,
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