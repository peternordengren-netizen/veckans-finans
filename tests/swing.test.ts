// Tester för fliken Swing (src/lib/swing.ts). Körs med `npm run test:js`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  decidedShareText, isPullbackLabel, labelClass, labelCounts, levelText, netText, priceText, reportText, riskText, shareText,
  signalText, vsBaseText,
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
