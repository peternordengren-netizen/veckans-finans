---
description: Skriv veckans text (5–8 meningar) om marknadsläget och vad Swing-utvärderingens underlag visar, märkt som tolkning
argument-hint: "<vecka>, t.ex. 2026-W47"
---

Du ska skriva en kort text om marknadsläget och vad den dagliga utvärderingen i fliken Swing visar, för Veckans
finans. Argument: `$ARGUMENTS`, veckan (ISO, t.ex. 2026-W47).

## Schemalagd körning

Kommandot körs utan att någon tittar på, med behörighetsläget `dontAsk`. Bara de verktyg och kommandon som står här
är tillåtna; allt annat nekas automatiskt. Försök aldrig ta dig runt ett nekande. Ditt sista svar sparas som logg.

## Arbetsgång

1. Läs `data/raw/swing_eval/summary.json` (beskrivs i `scripts/swing_eval.py`). Det du behöver:
   - `market_today`: marknadsläget vid senaste stängningen (`regime_text`; indexet mot 50/200 dagars MA i
     `index.vs_ma50_pct`/`vs_ma200_pct`; svängningarna `index.vol20`; andelen bolag över 50 MA
     `breadth.above_ma50_pct` och en vecka tidigare `breadth.week_ago_pct`; `new_highs`/`new_lows`; sektorerna i
     `sectors` med `rs20_pp`; marknadstecknen i `signs`).
   - `market_week_ago`: samma en vecka tidigare, och `market_history` dag för dag.
   - `alerts`: dagens larm (`sector_moves`: sektorer i rörelse med bolagen; `oversold`: kortsiktigt översålda bolag i
     upptrend, med läget och `tried`, de lägen mönstret prövats i).
   - Mönster och lyckade fall räknar bara dagar med full femdagarsuppföljning (`full_days`) och ett fall per uppgång
     (`episodes`); skriv "uppgångar" om dem, inte "fall per dag".
   - `success_recent`: andelen lyckade uppgångar senaste 4 veckorna (`all`), per marknadsläge (`by_regime`, nycklarna
     förklaras i `regime_texts`) och per strategi (`by_strategy`), med `successes` av `cases` och 95 % intervall `ci`.
   - `patterns."2v"`, `"6v"` och `"lage"` (`"3"`/`"5"` dagar): kännetecken bland de 20 % största rörelserna mot index
     jämfört med övriga (`top.share`, `other.share`, `diff_pp`, `cases`).
   Andelar står som tal mellan 0 och 1; skriv dem i procent (0,31 → 31 %).
2. Skriv 5–8 meningar på svenska som beskriver:
   - **marknadsläget** nu (trend, svängningar, bredd, sektorer),
   - **vilka typer av lägen och fall som lyckats** de senaste veckorna (andel lyckade per läge och strategi, med antal
     fall och osäkerhet),
   - **vad som ändrats** mot en vecka tidigare (läge, bredd, sektorer, marknadstecken).
   Skriv lättläst: avrunda andelar till heltal eller en decimal, skriv kännetecken och lägen med vanliga ord utan
   prefix (fastighetsbolagen, inte "Sektor: Fastigheter"), och ta inte med fler tal än som behövs. Ta med antal fall
   när du nämner en andel.
3. Skriv filen `data/swing_eval/$ARGUMENTS.json`:
   ```json
   {"week": "$ARGUMENTS", "label": "Claudes tolkning", "summary_generated_at": "<summary.json:s generated_at>",
    "text": "<5–8 meningar>"}
   ```
4. Kör `.venv/Scripts/python scripts/swing_eval.py --kontrollera $ARGUMENTS`. Rätta och kör igen tills den
   säger att texten är godkänd (högst tre försök).

## Regler (kontrolleras)

- 5–8 meningar. Varje tal i texten måste finnas i `summary.json` (andelar får skrivas i procent, med 0–2 decimaler
  och decimalkomma). Hitta inte på tal, räkna inte fram nya (inga summor, kvoter eller skillnader som inte står i
  filen).
- Texten är din tolkning, märkt så (`label`), och beskrivande. Använd inte orden köp, sälj, kursmål eller
  rekommendation i någon böjning, och ingen uppmaning. "Bäst" bara i avsnittens namn ("Gårdagens bästa rörelser",
  "de 20 % bästa").
- Säg inget om framtiden: läget och mönstren beskriver vad som hänt, och fallen är få och överlappar i tid.
- Skriv inget annat än filen ovan.
