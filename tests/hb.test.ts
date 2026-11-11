import { test } from "node:test";
import assert from "node:assert/strict";
import { dataCells, directionText, hbToShow, KIND_NAMES, SHOW_DAYS, type HbSummary } from "../src/lib/hb.ts";

const s = (kind: HbSummary["kind"], report_date: string, id = report_date): HbSummary => ({
  id, file_name: `${id}.pdf`, kind, title: "x", report_date, headline: "h", points: ["a", "b"], segments: [],
  companies: [], label: "manuellt tillagd", source_url: "https://exempel.test/",
});

test("Handelsbanken: senaste per rapporttyp, så länge den är aktuell", () => {
  const all = [
    s("morgonrapport", "2099-11-10"), s("morgonrapport", "2099-11-11"), s("fastighetsrapport", "2099-05-20"),
    s("fastighetsrapport", "2099-10-15"), s("annan", "2099-09-01"),
  ];
  const shown = hbToShow(all, "2099-11-11");
  assert.deepEqual(shown.map((x) => [x.kind, x.report_date]), [["morgonrapport", "2099-11-11"], ["fastighetsrapport", "2099-10-15"]]);
  // Morgonrapporten visas högst 3 dagar, fastighetsrapporten 200.
  assert.equal(SHOW_DAYS.morgonrapport, 3);
  assert.deepEqual(hbToShow([s("morgonrapport", "2099-11-07")], "2099-11-11"), []);
  assert.deepEqual(hbToShow([s("morgonrapport", "2099-11-12")], "2099-11-11"), []); // framtida datum visas inte
  assert.equal(KIND_NAMES.fastighetsrapport, "Fastighetsrapporten");
});

test("Handelsbanken: tabellen Vad vår data visar och riktningen", () => {
  const sd = (iso: string) => ({ "2099-11-20": "20 nov" } as Record<string, string>)[iso] ?? iso;
  const d = {
    week_change_pct: 1.2, week_rel_index_pp: 0.8, ytd_pct: 3.5, ytd_rel_index_pp: -4.21, short_change_pp: 0.35,
    insider_net_sek: -1250000, insider_buyers: 0, insider_sellers: 1, pe_now: 14.2, pe_change_pct: -8, next_report: "2099-11-20",
  };
  assert.deepEqual(dataCells(d, sd), ["+0,8 pe", "−4,2 pe", "+0,35 pe", "−1,3 mkr netto", "−8,0 %", "20 nov"]);
  assert.deepEqual(dataCells(undefined, sd), ["–", "–", "–", "–", "–", "–"]);
  const empty = { ...d, week_rel_index_pp: null, insider_net_sek: null, next_report: null };
  assert.deepEqual(dataCells(empty, sd).filter((_, i) => [0, 3, 5].includes(i)), ["–", "–", "–"]);
  assert.deepEqual(directionText("negativ"), { text: "negativ", cls: "neg" });
  assert.deepEqual(directionText("blandad"), { text: "blandad", cls: "" });
  for (const c of dataCells(d, sd)) assert.ok(!/köp|sälj/i.test(c));
});
