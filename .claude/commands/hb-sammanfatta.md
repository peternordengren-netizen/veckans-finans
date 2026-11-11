---
description: Sammanfatta en manuellt nedladdad Handelsbanken-rapport (Morgonrapporten eller Fastighetsrapporten) med egna ord
argument-hint: "<id>, t.ex. 3f9a0c1d2e4b5a6c"
---

Du ska sammanfatta en Handelsbanken-rapport som användaren laddat ner själv, för Veckans finans. Argument:
`$ARGUMENTS`, filens id.

Handelsbankens villkor säger att rapporterna inte får mångfaldigas eller spridas. Därför skriver du **bara med egna
ord**: inga citat, inga citattecken, och ingen följd om åtta ord eller fler som står likadant i rapporten. Det
kontrolleras. Sammanfattningen är beskrivande och ingen uppmaning: använd inte orden köp, sälj eller kursmål.

## Schemalagd körning

Kommandot körs utan att någon tittar på, med behörighetsläget `dontAsk`. Bara de verktyg och kommandon som står här
är tillåtna; allt annat nekas automatiskt. Försök aldrig ta dig runt ett nekande. Ditt sista svar sparas som logg.

## Arbetsgång

1. Läs `data/hb/work/<id>.meta.json`: filnamnet, rapporttypen (`kind`), datumet ur filnamnet (`file_date`, kan vara
   null), länken (`source_url`) och utfilen (`out`).
2. Läs rapportens text i `data/hb/work/<id>.txt` (bild för bild eller sida för sida). Läs inget annat om rapporten.
3. Läs `data/instruments.csv` (kolumnerna namn, ticker) för att koppla bolag som nämns till bevakningslistan.
4. Skriv utfilen `data/hb/<id>.json` med Write:

```json
{
  "id": "<id>",
  "file_name": "<filnamnet ur meta>",
  "kind": "<kind ur meta>",
  "title": "<kort rubrik med egna ord, t.ex. Morgonrapporten 11 november>",
  "report_date": "<rapportens datum ÅÅÅÅ-MM-DD; file_date om den finns, annars datumet i rapporten>",
  "headline": "<huvudbudskapet i en till två meningar>",
  "points": ["<2–8 korta punkter med det viktigaste>"],
  "rates": "<synen på räntor och penningpolitik, eller null om rapporten inte tar upp det>",
  "valuations": "<synen på värderingar (fastigheter, aktier eller marknaden), eller null>",
  "segments": [{"name": "<delsegment, t.ex. bostäder, kontor, logistik, handel>", "view": "<synen på segmentet>"}],
  "companies": [{"ticker": "<ticker ur instruments.csv>", "name": "<namn exakt som i instruments.csv>", "note": "<vad rapporten säger om bolaget>"}],
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

5. Kör `.venv/Scripts/python scripts/hb_manual.py --kontrollera <id>`. Står det UNDERKÄND: rätta det som står efter
   `fel:` i utfilen och kör kontrollen igen, högst tre gånger. Skriv inget annat än utfilen.
6. Avsluta med en rad: GODKÄND eller UNDERKÄND och antalet punkter, segment och bolag.
