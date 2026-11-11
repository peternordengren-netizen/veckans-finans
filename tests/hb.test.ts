import { test } from "node:test";
import assert from "node:assert/strict";
import { hbToShow, KIND_NAMES, SHOW_DAYS, type HbSummary } from "../src/lib/hb.ts";

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
