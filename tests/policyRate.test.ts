// Tester för sidans text om styrräntan. Körs med `npm run test:js` (node --test, Node 24 tar bort typerna).
import assert from "node:assert/strict";
import { test } from "node:test";

import { changedThisWeek, describePolicyRate } from "../src/lib/policyRate.ts";

const W41 = ["2026-10-05", "2026-10-11"] as const;

test("ändring före veckan: oförändrad sedan ändringsdatumet", () => {
  const pr = { value: 1.75, previousValue: 2, changedOn: "2025-10-01" };
  assert.equal(changedThisWeek(pr, ...W41), false);
  assert.equal(describePolicyRate(pr, ...W41), "oförändrad sedan 1 oktober 2025");
});

test("sänkning under veckan: sänkt från föregående värde", () => {
  const pr = { value: 1.5, previousValue: 1.75, changedOn: "2026-10-08" };
  assert.equal(changedThisWeek(pr, ...W41), true);
  assert.equal(describePolicyRate(pr, ...W41), "sänkt från 1,75 % den 8 oktober 2026");
});

test("höjning på veckans första dag räknas som under veckan", () => {
  const pr = { value: 2, previousValue: 1.75, changedOn: "2026-10-05" };
  assert.equal(describePolicyRate(pr, ...W41), "höjd från 1,75 % den 5 oktober 2026");
});

test("ändring dagen efter veckan räknas inte", () => {
  const pr = { value: 2, previousValue: 1.75, changedOn: "2026-10-12" };
  assert.equal(changedThisWeek(pr, ...W41), false);
});

test("ingen ändring i hämtad period", () => {
  const pr = { value: 1.75, previousValue: null, changedOn: null };
  assert.equal(describePolicyRate(pr, ...W41), "oförändrad under hämtad period");
});
