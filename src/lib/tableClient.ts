/**
 * Klientkod för de breda tabellerna (Signaler, Kurs mot vinst, Kursreaktion): sortering på kolumnrubrik,
 * full fönsterbredd, skuggor vid kanterna när tabellen går att rulla i sidled och en fast rubrikrad
 * under flikraden när man rullar ner (setupStickyHeader).
 *
 * Markup: <th data-type="num|text" aria-sort="none|ascending|descending"><button>, celler med
 * data-v (sorteringsvärde, tomt = saknas), första kolumnen = bolagsnamn (lika värden ordnas
 * efter det). Ytterbehållaren har data-table-outer och den rullande data-table-wrap.
 */
import { compareValues } from "./signals";

export function setupSortable(
  root: HTMLElement,
  onSort?: () => void,
  /** Rader som alltid sorteras sist, oavsett kolumn och riktning (t.ex. ogiltiga kandidater i Swing). */
  sortLast?: (r: HTMLTableRowElement) => boolean,
): void {
  const tbody = root.querySelector("tbody")!;
  const rows = Array.from(tbody.querySelectorAll<HTMLTableRowElement>("tr"));
  const headers = Array.from(root.querySelectorAll<HTMLTableCellElement>("thead th"));
  headers.forEach((th, col) => {
    th.querySelector("button")?.addEventListener("click", () => {
      // Första klicket: text stigande, tal fallande (störst först).
      const current = th.getAttribute("aria-sort");
      const ascending = current === "none" ? th.dataset.type === "text" : current === "descending";
      headers.forEach((h) => h.setAttribute("aria-sort", "none"));
      th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
      const value = (r: HTMLTableRowElement) => {
        const raw = (r.children[col] as HTMLElement).dataset.v ?? "";
        if (raw === "") return null;
        return th.dataset.type === "num" ? Number(raw) : raw;
      };
      // Lika värden ordnas efter bolagsnamn, så att ordningen alltid blir densamma.
      const name = (r: HTMLTableRowElement) => (r.children[0] as HTMLElement).dataset.v ?? "";
      const last = (r: HTMLTableRowElement) => (sortLast?.(r) ? 1 : 0);
      rows.sort((a, b) => last(a) - last(b) || compareValues(value(a), value(b), ascending)
        || compareValues(name(a), name(b), true));
      tbody.append(...rows);
      onSort?.();
    });
  });
}

/**
 * Höjden som en fast rubrikrad ska ligga under: underkanten av flikraden ([data-sticky-tabs]) när den
 * just nu sitter fast överst, annars 0 (t.ex. i morgonrapporten ovanför veckans flikar).
 */
function stickyOffset(): number {
  const tabs = document.querySelector<HTMLElement>("[data-sticky-tabs]");
  if (!tabs || tabs.hidden) return 0;
  const r = tabs.getBoundingClientRect();
  return r.top <= 1 && r.bottom > 0 ? r.bottom : 0;
}

/**
 * Fast rubrikrad: när tabellens rubrik rullat upp under flikraden visas en kopia av den fast (position:
 * fixed) direkt under flikraden. Tabellen rullar i sidled i sin egen behållare, och då fastnar CSS-sticky
 * mot behållaren i stället för sidan; därför en kopia som
 * - får samma kolumnbredder och tabellbredd som originalet,
 * - följer den sidledes rullningen (scrollLeft), med den fasta bolagskolumnen kvar till vänster
 *   (samma CSS: kopian behåller komponentens attribut),
 * - skickar klick på kolumnrubrikerna vidare till originalet (sortering) och byggs om när sorteringen ändras.
 * Kopian är aria-hidden; skärmläsare använder den riktiga rubriken.
 */
export function setupStickyHeader(root: HTMLElement): void {
  const outer = root.querySelector<HTMLElement>("[data-table-outer]");
  const wrap = root.querySelector<HTMLElement>("[data-table-wrap]");
  const table = wrap?.querySelector("table");
  const thead = table?.tHead;
  if (!outer || !wrap || !table || !thead) return;

  const float = document.createElement("div");
  float.setAttribute("aria-hidden", "true");
  float.hidden = true;
  Object.assign(float.style, {
    position: "fixed", zIndex: "20", overflow: "hidden", boxShadow: "0 2px 4px -2px var(--scroll-shadow)",
    borderTopLeftRadius: "0", borderTopRightRadius: "0",
  });
  const inner = document.createElement("div");
  Object.assign(inner.style, { overflow: "hidden", width: "100%" });
  const clone = document.createElement("table");
  for (const a of Array.from(table.attributes)) clone.setAttribute(a.name, a.value);
  clone.style.tableLayout = "fixed";
  inner.append(clone);
  float.append(inner);
  outer.append(float);

  const rebuild = () => {
    const head = thead.cloneNode(true) as HTMLTableSectionElement;
    head.querySelectorAll<HTMLButtonElement>("button").forEach((b, i) => {
      b.tabIndex = -1;
      b.addEventListener("click", () => thead.querySelectorAll<HTMLButtonElement>("button")[i]?.click());
    });
    clone.replaceChildren(head);
  };

  const sync = () => {
    const top = stickyOffset();
    const h = thead.getBoundingClientRect();
    const t = table.getBoundingClientRect();
    const show = h.height > 0 && h.top < top && t.bottom - h.height > top;
    float.hidden = !show;
    if (!show) return;
    const w = wrap.getBoundingClientRect();
    float.style.top = `${top}px`;
    float.style.left = `${w.left + wrap.clientLeft}px`;
    float.style.width = `${wrap.clientWidth}px`;
    clone.style.width = `${table.getBoundingClientRect().width}px`;
    const src = thead.rows[0]?.cells ?? [];
    const dst = clone.tHead?.rows[0]?.cells ?? [];
    for (let i = 0; i < src.length && i < dst.length; i++) {
      const width = `${src[i].getBoundingClientRect().width}px`;
      Object.assign(dst[i].style, { width, minWidth: width, maxWidth: width, boxSizing: "border-box" });
    }
    inner.scrollLeft = wrap.scrollLeft;
  };

  rebuild();
  window.addEventListener("scroll", sync, { passive: true });
  window.addEventListener("resize", sync);
  wrap.addEventListener("scroll", sync, { passive: true });
  new ResizeObserver(sync).observe(table);
  // Sorteringen ändrar aria-sort (och pilarna) i originalet: bygg om kopian.
  new MutationObserver(() => {
    rebuild();
    sync();
  }).observe(thead, { attributes: true, subtree: true, attributeFilter: ["aria-sort"] });
  sync();
}

export function setupFullWidth(root: HTMLElement): void {
  const outer = root.querySelector<HTMLElement>("[data-table-outer]");
  const wrap = root.querySelector<HTMLElement>("[data-table-wrap]");
  if (!outer || !wrap) return;
  // Full fönsterbredd: CSS:en räknar med 100vw minus rullningslistens bredd (--sbw).
  const setScrollbarWidth = () =>
    document.documentElement.style.setProperty("--sbw", `${window.innerWidth - document.documentElement.clientWidth}px`);
  const updateShadows = () => {
    const max = wrap.scrollWidth - wrap.clientWidth;
    outer.classList.toggle("more-right", max > 1 && wrap.scrollLeft < max - 1);
    outer.classList.toggle("more-left", wrap.scrollLeft > 1);
  };
  wrap.addEventListener("scroll", updateShadows, { passive: true });
  window.addEventListener("resize", () => {
    setScrollbarWidth();
    updateShadows();
  });
  // Fliken kan vara dold när sidan laddas; mät om när tabellen får sin storlek.
  new ResizeObserver(updateShadows).observe(wrap);
  new ResizeObserver(updateShadows).observe(wrap.querySelector("table")!);
  setScrollbarWidth();
  updateShadows();
}
