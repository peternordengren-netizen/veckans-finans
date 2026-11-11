// Tester för visningstexterna (inga "pe" på sidan). Körs med `npm run test:js`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { dateRange, direction, pct, relativeToIndex, shortChange, shortDate, shortRange, withoutShortWarnings } from "../src/lib/format.ts";

test("ny innehavare får bara pilen, inte 'ökad'", () => {
  const c = shortChange(0, 1.05, 1.05, { threshold: 0.5, newHolder: true });
  assert.deepEqual(c, { dir: "▲", text: "ny position 1,05 % av aktierna" });
  assert.equal(`${c.dir} ${c.text}`, "▲ ny position 1,05 % av aktierna");
});

test("befintlig innehavare och bolag får pil och ord", () => {
  assert.deepEqual(shortChange(0.68, 0.79, 0.11, { threshold: 0.5, newHolder: true }),
    { dir: "▲ ökad", text: "0,68 % → 0,79 % av aktierna (+0,11)" });
  assert.deepEqual(shortChange(2.03, 0.88, -1.15, { threshold: 0.5, newHolder: true }),
    { dir: "▼ minskad", text: "2,03 % → 0,88 % av aktierna (−1,15)" });
  // Bolag (inte newHolder) som går från under gränsen behåller "ökad".
  assert.deepEqual(shortChange(0, 1.2, 1.2, { threshold: 0.5 }),
    { dir: "▲ ökad", text: "under 0,5 % → 1,2 % av aktierna (+1,2)" });
});

test("datum utan årtal med svensk månadsförkortning", () => {
  assert.equal(shortDate("2026-10-07"), "7 okt");
  assert.equal(shortDate("2026-09-30"), "30 sep");
  assert.equal(shortDate("2026-03-01"), "1 mars");
});

test("datumintervall för insynsaffärer", () => {
  assert.equal(dateRange(["2026-10-07"]), "7 okt");
  assert.equal(dateRange(["2026-10-07", "2026-10-07"]), "7 okt");
  assert.equal(dateRange(["2026-10-09", "2026-10-06", "2026-10-07"]), "6–9 okt");
  assert.equal(dateRange(["2026-10-02", "2026-09-30"]), "30 sep–2 okt");
  assert.equal(dateRange([]), "");
});

test("riktning med pil och ord", () => {
  assert.equal(direction(2.2), "▲ ökad");
  assert.equal(direction(-0.5), "▼ minskad");
  assert.equal(direction(0), "oförändrad");
});

test("skillnad mot index i ord", () => {
  assert.equal(relativeToIndex(-5.3), "5,3 % sämre än index");
  assert.equal(relativeToIndex(2.1), "2,1 % bättre än index");
  assert.equal(relativeToIndex(0), "i linje med index");
});

test("skillnad som avrundas till 0,0 är i linje med index", () => {
  assert.equal(relativeToIndex(0.04), "i linje med index");
  assert.equal(relativeToIndex(-0.04), "i linje med index");
  assert.equal(relativeToIndex(0.05), "0,1 % bättre än index");
});

test("blankning från och till med förändring", () => {
  assert.equal(shortRange(1.1, 3.3, 2.2, { threshold: 0.5 }), "1,1 % → 3,3 % av aktierna (+2,2)");
  assert.equal(shortRange(5.93, 4.28, -1.65, { threshold: 0.5 }), "5,93 % → 4,28 % av aktierna (−1,65)");
});

test("ny innehavare utan tidigare position", () => {
  assert.equal(shortRange(0, 0.6, 0.6, { threshold: 0.5, newHolder: true }), "ny position 0,6 % av aktierna");
});

test("under rapportgränsen skrivs ut i stället för 0 %", () => {
  assert.equal(shortRange(1.05, 0, -1.05, { threshold: 0.5, newHolder: true }), "1,05 % → under 0,5 % av aktierna (−1,05)");
  assert.equal(shortRange(0, 1.2, 1.2, { threshold: 0.5 }), "under 0,5 % → 1,2 % av aktierna (+1,2)");
  assert.equal(shortRange(0.35, 0, -0.35, { threshold: 0.1 }), "0,35 % → under 0,1 % av aktierna (−0,35)");
});

test("procent med en till två decimaler", () => {
  assert.equal(pct(2), "2,0");
  assert.equal(pct(0.68), "0,68");
  assert.equal(pct(2.2), "2,2");
});

test("ingen text innehåller pe eller procentenheter", () => {
  const texts = [relativeToIndex(-1.2), relativeToIndex(0.3), shortRange(1, 2, 1, { threshold: 0.5 }),
    shortRange(0, 0.7, 0.7, { threshold: 0.5, newHolder: true })];
  for (const t of texts) assert.doesNotMatch(t, /\bpe\b|procentenhet/);
});

test("fellistan utan varningar om blankning", () => {
  const errors = ["blankning: Blankningsregistret saknas", "FI blankning: 404", "FI insyn: timeout", "kurser saknas för 2099-11-10"];
  assert.deepEqual(withoutShortWarnings(errors), ["FI insyn: timeout", "kurser saknas för 2099-11-10"]);
});
