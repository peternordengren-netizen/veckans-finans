---
description: Ta fram rapporterade nyckeltal ur rapporter (en omgång om 20) för en vecka, en morgon eller en uppdatering
argument-hint: "<ISO-vecka, datum eller datumThhmm> <omgång> [endast <tickrar>], t.ex. 2026-W43 1, 2026-10-20 1 eller 2026-10-20T1745 1"
---

Du ska ta fram rapporterade nyckeltal ur bolagens egna rapporter för Veckans finans. Argument: `$ARGUMENTS`:
läge och omgång. Läget är en vecka (`2026-W43 1`, måndagens uppsamling), ett datum (`2026-10-20 1`,
morgonkörningen) eller ett datum med klockslag (`2026-10-20T1745 1`, eftermiddagsuppdateringen eller
"Uppdatera nu"). Morgon och uppdatering tar rapporterna sedan föregående körning.

**Bara vissa rapporter:** står det `endast <tickrar>` efter omgången (t.ex. `2026-10-20 1 endast NOLA B,XANO B`,
tickrarna kommaseparerade) finns utfilen redan och de rapporterna underkändes. Kör validate.py på utfilen först för
att se felen. Gör sedan om **bara** de bolagens rapporter: läs bara deras källtexter och ersätt bara deras poster i
utfilen (med Edit). Ändra, ta bort eller lägg inte till något för övriga bolag – de är redan godkända. Saknas ett
bolags post helt lägger du till den.

Utfil: `data/results/<vecka>/omgang-<omgång>.json` för en vecka, `data/results/morning/<datum>/omgang-<omgång>.json`
för ett datum och `data/results/updates/<datumThhmm>/omgang-<omgång>.json` för en uppdatering. I filen är `"week"`
läget, alltså veckan, datumet eller datumet med klockslag.

## Schemalagd körning

Kommandot körs utan att någon tittar på, med behörighetsläget `dontAsk`. Bara de verktyg och kommandon som står här
är tillåtna; allt annat nekas automatiskt. Försök aldrig ta dig runt ett nekande. Ditt sista svar sparas som rapport.

## Arbetsgång

1. Kör `.venv/Scripts/python scripts/report_list.py <läge> --omgang <omgång>`. Varje rad är en rapport:
   `nr | omgång | ticker | bolag | sektor | nyckeltal | publicerad | text | raw_file | sökväg | rubrik | språk`. Det är exakt
   de rapporter du ska ta med – alla, och inga andra.
2. Läs nyckeltalens namn och typer i `data/sectors.json` (`kpis`). Utöver sektorns nyckeltal finns för alla bolag
   `nettoomsattning` (belopp), `resultat-per-aktie` (per aktie) och `justerat-resultat-per-aktie` (per aktie):
   - `resultat-per-aktie` är alltid det **redovisade** resultatet per aktie, aldrig ett justerat.
   - `justerat-resultat-per-aktie` tar du med **bara** om rapporten anger ett justerat resultat per aktie för
     kvartalet ("justerat", "underliggande", "exklusive/före jämförelsestörande poster", "exklusive engångsposter",
     "adjusted", "underlying" o.d.). Citatet ska innehålla det ordet. Anger rapporten båda, tar du med båda med var
     sitt citat.
3. För varje rapport: läs filen i kolumnen `sökväg` med Read, **högst de första 400 raderna** (rapportens
   sammanfattning och nyckeltalstabell står först). Läs inga andra filer.
4. Ta för varje nyckeltal i radens lista fram **kvartalets** värde och föregående års värde för samma kvartal, om
   rapporten anger dem. Ta inte med ett nyckeltal som inte står i texten. Gissa aldrig och räkna aldrig om.
5. Skriv för varje rapport med nyckeltal raden **"I rapporten"** (`highlight`, se regel 8).
6. Skriv utfilen med Write och validera med `.venv/Scripts/python scripts/validate.py <utfil>`.
   Rätta felen mot källtexten och kör igen, **högst tre valideringskörningar**. Om något fortfarande underkänns:
   stanna, ta inte bort uppgifter för att klara valideringen, och lista felen ordagrant i ditt svar.

## Filformat

```json
{
  "week": "2026-W43",
  "batch": 1,
  "reports": [
    {
      "ticker": "AVARDA",
      "raw_file": "mfn/3ff7c4b4-1410-5745-a8ab-6020e823dd3c.txt",
      "period": "Q3 2026",
      "note": null,
      "highlight": {
        "text": "Orderingången nådde rekordnivå; bolaget räknar med fortsatt svag efterfrågan i Norden.",
        "quotes": [
          "Orderingången nådde den högsta nivån för ett enskilt kvartal",
          "vi räknar med att efterfrågan i Norden förblir svag under de närmaste kvartalen"
        ],
        "tone": "blandad"
      },
      "kpis": [
        {
          "id": "resultat-per-aktie",
          "value": 3.51,
          "unit": "SEK",
          "previous_value": null,
          "change_pct": 30,
          "period_kind": "kvartal",
          "quote": "Resultat per aktie ökade med 30 % till 3,51 SEK"
        }
      ]
    }
  ]
}
```

## Regler

1. **Citat:** `quote` är ett ordagrant utdrag ur rapporttexten, högst 25 ord, kopierat tecken för tecken. Det ska
   innehålla årets värde och föregående års värde (oftast inom parentes: "1 234 MSEK (1 100)") och/eller den angivna
   förändringen i procent. Minst ett av `previous_value` och `change_pct` ska anges, och båda ska stå i citatet.
2. **Värden:** `value` och `previous_value` är talen som de står i citatet, med punkt som decimaltecken
   (`3,51` → `3.51`, `1 234` → `1234`). Negativa värden (förlust) med minustecken.
   - **Engelska rapporter** (`en` i kolumnen `språk`): komma är tusentalsavgränsare och punkt decimaltecken
     (`1,234.5` → `1234.5`, `0.85` → `0.85`). Valideringen läser citatet på samma sätt. `change_pct` bara om rapporten
   själv anger förändringen i procent; för procenttal (marginaler, belåningsgrad) är `change_pct` alltid `null`.
3. **Enhet** (`unit`), som i citatet: belopp `SEK`, `TSEK`, `MSEK` eller `MDSEK` (mkr = MSEK, mdkr/Mdr kr = MDSEK),
   motsvarande för `EUR`, `USD`, `DKK`, `NOK`; per aktie `SEK`/`EUR`/…, eller i hundradelar som i citatet
   (`Euro cent`, `US cent`, `öre`); procenttal `procent`; volym `ton`, `kton`,
   `st`, `MWh`, `GWh`, `boe` …; pris t.ex. `SEK/ton`.
4. **Period:** `period_kind` är `kvartal` för det senaste kvartalet (t.ex. juli–september), `delår` för året hittills
   (t.ex. januari–september) och `helår` för hela året. **Ta kvartalets värde.** Rapporter visar ofta båda; citatet
   ska stå under kvartalets rubrik. Valideringen kontrollerar närmaste periodangivelse före citatet i texten.
   `period` är rapportens kvartal, t.ex. `Q3 2026`.
5. **Inga nyckeltal:** om rapporten saknar alla nyckeltalen (t.ex. investmentbolag som inte redovisar räntenetto,
   eller ett utdrag utan siffror) anger du `"kpis": []` och en kort förklaring i `note`.
6. Skriv bara i `data/results/`. Ändra aldrig källtexten, listan, fönstret (`window.json`) eller valideringen.
7. `raw_file` i utfilen är kolumnen `raw_file` tecken för tecken, inklusive källmappen: `mfn/….txt` eller
   `cision/….txt`, aldrig bara filnamnet. Använd inte kolumnen `sökväg` (den har dessutom `data/…/<vecka>/` före).
8. **I rapporten** (`highlight`): **en hel mening** på svenska, **högst 15 ord**, om det rapporten själv lyfter fram som
   viktigast – rubriken, ingressen, VD:ns kommentar – **och vad den säger om utsikterna** (t.ex. under "Utsikter",
   "Om vi ser framåt", "Marknadsförutsättningar") om den säger något om dem. Ingen egen värdering och inga
   slutsatser som inte står i rapporten.
   - Meningen ska vara grammatiskt fullständig: stor bokstav först, punkt sist, och varje led har sitt verb
     (inte "Svagt kvartal men intäkterna förbättrades och kommersiella framsteg." – där saknas verbet i sista
     ledet). Räcker inte 15 ord: välj det viktigaste och stryk resten, kapa aldrig mitt i. Två korta huvudsatser
     med semikolon går bra. Valideringen underkänner en rad utan punkt sist eller som slutar på t.ex. "och",
     "inom" eller "bland annat".
   - `quotes`: 1–3 ordagranna citat ur rapporttexten (högst 25 ord vardera, kopierade tecken för tecken) som
     belägger raden. Citaten visas inte men valideras som nyheternas: de ska finnas i texten, varje tal i raden ska
     stå exakt så i ett citat (inga avrundningar), och ett "ökade"/"minskade" i raden får inte motsäga citatet.
   - Inga ord om aktien eller kursen och inga råd ("köp", "sälj", "kursmål", "rekommendation" …).
   - `tone`: rapportens ton för bolaget (inte för aktien), som raden och citaten visar: `positiv`, `blandad`
     (både tydligt bra och tydligt dåligt, t.ex. starkt resultat men svag orderingång), `neutral` eller `negativ`.
     Bedöm bara det rapporten själv skriver; väg in utsikterna. Raden och citaten ska bära tonen, så att en läsare
     ser varför.
   - Bara för en rapport utan nyckeltal (`"kpis": []`) får `highlight` vara `null`.

## Svar

Kort: antal rapporter, antal nyckeltal per rapport, rapporter utan nyckeltal (med förklaring), valideringens
resultat (OK efter hur många körningar, eller EJ GODKÄND med felen).
