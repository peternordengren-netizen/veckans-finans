// Tester för signaltabellens visningstexter och sortering. Körs med `npm run test:js`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  compareValues, dayMonth, defaultOrder, isStrong, ratio, sek, shortChange, shortUntil, signalChip, signedPct,
} from "../src/lib/signals.ts";

test("insynsetiketten följer nettot", () => {
  assert.deepEqual(signalChip("insyn", { insider_net_sek: 5_642_500 }), { label: "Insynsköp", cls: "insyn-kop" });
  assert.deepEqual(signalChip("insyn", { insider_net_sek: -190_680 }), { label: "Insynsförsäljning", cls: "insyn-salj" });
  assert.deepEqual(signalChip("insyn", { insider_net_sek: 0 }), { label: "Insyn", cls: "insyn" });
  assert.deepEqual(signalChip("omsättning", { insider_net_sek: null }), { label: "Hög omsättning", cls: "omsättning" });
});

test("blankning t.o.m.: signalen märks när underlaget slutar före veckans slut", () => {
  assert.equal(shortUntil("2026-10-27", "2026-11-01"), "2026-10-27");
  assert.equal(shortUntil("2026-11-01", "2026-11-01"), null); // hela veckan täckt
  assert.equal(shortUntil(null, "2026-11-01"), null);
  assert.equal(dayMonth("2026-10-27"), "27/10");
  assert.deepEqual(signalChip("blankning", { insider_net_sek: null }, "2026-10-27"),
    { label: "Blankning t.o.m. 27/10", cls: "blankning" });
  assert.deepEqual(signalChip("blankning", { insider_net_sek: null }), { label: "Blankning", cls: "blankning" });
  assert.equal(signalChip("omsättning", { insider_net_sek: null }, "2026-10-27").label, "Hög omsättning");
});

test("förvald ordning: flest signaler först, sedan namn", () => {
  const rows = [
    { name: "Ödla", signals: ["insyn"] },
    { name: "Beta", signals: [] },
    { name: "Ärla", signals: ["insyn", "omsättning"] },
    { name: "Alfa", signals: ["insyn"] },
    { name: "Zeta", signals: ["insyn", "blankning", "avviker"] },
  ];
  assert.deepEqual(defaultOrder(rows).map((r) => r.name), ["Zeta", "Ärla", "Alfa", "Ödla", "Beta"]);
  assert.equal(rows[0].name, "Ödla"); // originalet ändras inte
});

const MINUS = "−";

test("procent med tecken och tomt värde", () => {
  assert.equal(signedPct(9.04), "+9,0 %");
  assert.equal(signedPct(-5.06), `${MINUS}5,1 %`);
  assert.equal(signedPct(0), "0,0 %");
  assert.equal(signedPct(null), "–");
});

test("omsättning och kronor", () => {
  assert.equal(ratio(3), "3,0×");
  assert.equal(ratio(null), "–");
  assert.equal(sek(229_514_008), "+229,5 mn kr");
  assert.equal(sek(252_815), "+252 815 kr");
  assert.equal(sek(-1_200_000), `${MINUS}1,2 mn kr`);
  assert.equal(sek(0), "0 kr");
});

test("blankning: positioner går före aggregatet", () => {
  assert.equal(shortChange({ short_positions_pp: 0.3, short_aggregate_pp: 0.6 }), 0.3);
  assert.equal(shortChange({ short_positions_pp: null, short_aggregate_pp: 0.6 }), 0.6);
  assert.equal(shortChange({ short_positions_pp: null, short_aggregate_pp: null }), null);
});

test("sortering: saknade värden sist i båda riktningarna, svensk textordning", () => {
  const nums = [3, null, -1, 10];
  assert.deepEqual([...nums].sort((a, b) => compareValues(a, b, true)), [-1, 3, 10, null]);
  assert.deepEqual([...nums].sort((a, b) => compareValues(a, b, false)), [10, 3, -1, null]);
  const names = ["Ödla", "Alfa", "Ärla", "Zeta"];
  assert.deepEqual([...names].sort((a, b) => compareValues(a, b, true)), ["Alfa", "Zeta", "Ärla", "Ödla"]);
});

test("starka signaler: minst två, eller en stark; stor rörelse räknas inte för rapporterande bolag", () => {
  assert.equal(isStrong([], false), false);
  assert.equal(isStrong(["stor-rörelse"], false), false); // en ensam svag signal
  assert.equal(isStrong(["omsättning"], false), false);
  assert.equal(isStrong(["stor-rörelse", "omsättning"], false), true); // två signaler
  for (const s of ["insyn", "avviker", "52v-högsta", "52v-lägsta", "pe-upp", "blankning"] as const) {
    assert.equal(isStrong([s], false), true, s);
  }
  // Rapporterade i veckan: den stora rörelsen är rapportreaktionen och räknas inte.
  assert.equal(isStrong(["stor-rörelse", "omsättning"], true), false);
  assert.equal(isStrong(["stor-rörelse", "insyn"], true), true);
  assert.equal(isStrong(["stor-rörelse", "omsättning", "52v-högsta"], true), true);
});