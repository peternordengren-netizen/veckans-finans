---
description: Sammanfatta en manuellt nedladdad Handelsbanken-rapport (Morgonrapporten eller Fastighetsrapporten) med egna ord
argument-hint: "<id>, t.ex. 3f9a0c1d2e4b5a6c"
---

Du ska sammanfatta en Handelsbanken-rapport som användaren laddat ner själv, för Veckans finans. Argument:
`$ARGUMENTS`, filens id.

Handelsbankens villkor säger att rapporterna inte får mångfaldigas eller spridas. Därför skriver du **bara med egna
ord**: inga citat, inga citattecken, och ingen följd om åtta ord eller fler som står likadant i rapporten. Det
kontrolleras. Sammanfattningen är beskrivande och ingen uppmaning: använd inte orden köp, sälj eller kursmål i
någon böjning (köpte, köpare, sålde, säljare med flera).

## Schemalagd körning

Kommandot körs utan att någon tittar på, med behörighetsläget `dontAsk`. Bara de verktyg och kommandon som står här
är tillåtna; allt annat nekas automatiskt. Försök aldrig ta dig runt ett nekande. Ditt sista svar sparas som logg.

## Arbetsgång

1. Läs `data/hb/work/<id>.meta.json`: filnamnet, rapporttypen (`kind`), datumet ur filnamnet (`file_date`, kan vara
   null), filens skapandedatum ur metadatan (`file_created`, kan vara null), länken (`source_url`) och utfilen (`out`).
2. Läs rapportens text i `data/hb/work/<id>.txt` (bild för bild eller sida för sida). Läs inget annat om rapporten.
3. Läs `data/instruments.csv` (kolumnerna namn, ticker, sektor) för att koppla bolag som nämns till bevakningslistan,
   och `data/sectors.json` för sektorernas id.
4. Läs `data/hb/work/<id>.data.json` (sökvägen står i meta under `data`): sidans egna värden per bolag (`companies`,
   fälten förklaras under `fields`) och källorna (`sources`).

Sammanfattningen har tre avsnitt:
- a) **Rapporten i korthet**: huvudbudskapet med egna ord (fälten headline, points, rates, valuations, segments,
  companies nedan).
- b) **Betydelse för aktiemarknaden** (`impact`): vilka sektorer och bolag i instruments.csv rapporten berör, med
  riktning `positiv`, `negativ` eller `blandad` och en kort motivering. Det är din tolkning, inte Handelsbankens, och
  märks så. Ta bara med bolag där kopplingen till rapporten är tydlig.
- c) **Vad vår data visar** (`our_data`): en rad per bolag i b) (fler får tas med). `note` beskriver kort vad sidans
  värden visar för bolaget (kurs mot index senaste veckan och i år, insyn, P/E-förändring i år, kommande rapport;
  blankning ingår inte) och pekar ut om rapportens bild och marknadens rörelse går isär (`diverges`: true/false).
  Regeln för `diverges` (kontrolleras): riktningen i b) är `positiv` men kursen ligger minst 5 procentenheter under
  index i år (`ytd_rel_index_pp` ≤ −5) eller minst 3 under senaste veckan (`week_rel_index_pp` ≤ −3), eller
  `negativ` och lika mycket över index. `blandad` och bolag som inte står i b) är false. Går den isär ska note säga
  det. Insyn beskrivs som förvärv eller avyttring.
  **Varje tal i note ska finnas i bolagets värden i data.json** (avrundat till högst två decimaler, med decimalkomma,
  utan tecken; kronbelopp får skrivas i miljoner). Skriv datum som ÅÅÅÅ-MM-DD. Hitta inte på tal och räkna inte om.
  Saknas ett värde (null) säger du att det saknas. Du skriver inte värdena själva; skriptet lägger på dem.
5. Skriv utfilen `data/hb/<id>.json` med Write:

```json
{
  "id": "<id>",
  "file_name": "<filnamnet ur meta>",
  "kind": "<kind ur meta>",
  "title": "<kort rubrik med egna ord, t.ex. Morgonrapporten 11 november>",
  "report_date": "<ÅÅÅÅ-MM-DD: file_date om den finns; annars ett exakt datum i rapporten; annars file_created; annars dagens datum>",
  "headline": "<huvudbudskapet i en till två meningar>",
  "points": ["<2–8 korta punkter med det viktigaste>"],
  "rates": "<synen på räntor och penningpolitik, eller null om rapporten inte tar upp det>",
  "valuations": "<synen på värderingar (fastigheter, aktier eller marknaden), eller null>",
  "segments": [{"name": "<delsegment, t.ex. bostäder, kontor, logistik, handel>", "view": "<synen på segmentet>"}],
  "companies": [{"ticker": "<ticker ur instruments.csv>", "name": "<namn exakt som i instruments.csv>", "note": "<vad rapporten säger om bolaget>"}],
  "impact": {
    "label": "Claudes tolkning, inte Handelsbankens",
    "sectors": [{"sector": "<id ur sectors.json>", "direction": "positiv|negativ|blandad", "reason": "<motivering>"}],
    "companies": [{"ticker": "<ticker>", "name": "<namn som i instruments.csv>", "direction": "positiv|negativ|blandad", "reason": "<motivering>"}]
  },
  "our_data": [{"ticker": "<ticker>", "name": "<namn som i instruments.csv>", "diverges": false, "note": "<vad sidans värden visar, bara tal ur data.json>"}],
  "label": "manuellt tillagd",
  "source_url": "<source_url ur meta>"
}
```

- **Fastighetsrapporten** (`kind` = fastighetsrapport): `headline` är huvudbudskapet, `rates` synen på räntor,
  `valuations` synen på värderingar (fastighetsvärden, direktavkastning, bolagens substansrabatter), `segments` alla
  delsegment som nämns och `companies` alla börsbolag som nämns och finns i instruments.csv. Alla fyra krävs.
- **Morgonrapporten** (`kind` = morgonrapport): `headline` är dagens tema, `points` gårdagens viktigaste händelser,
  marknadsrörelser och dagens kalender; `rates` om räntor och centralbanker tas upp; `segments` kan vara tom.
- Bolag som inte finns i instruments.csv tas inte med i `companies` (du kan nämna dem i en punkt). Gissa inga tickrar.
- Siffror (räntor, procent, prognoser) får stå med, men formulera meningarna själv.

6. Kör `.venv/Scripts/python scripts/hb_manual.py --kontrollera <id>`. Står det UNDERKÄND: rätta det som står efter
   `fel:` i utfilen och kör kontrollen igen, högst tre gånger. Skriv inget annat än utfilen.
7. Avsluta med en rad: GODKÄND eller UNDERKÄND och antalet punkter, segment, berörda bolag och fall där rapporten
   och marknaden går isär.
