---
description: Skriv veckans text (5–8 meningar) om vad Swing-utvärderingens underlag visar, märkt som tolkning
argument-hint: "<vecka>, t.ex. 2026-W47"
---

Du ska skriva en kort text om vad den dagliga utvärderingen i fliken Swing visar, för Veckans finans. Argument:
`$ARGUMENTS`, veckan (ISO, t.ex. 2026-W47).

## Schemalagd körning

Kommandot körs utan att någon tittar på, med behörighetsläget `dontAsk`. Bara de verktyg och kommandon som står här
är tillåtna; allt annat nekas automatiskt. Försök aldrig ta dig runt ett nekande. Ditt sista svar sparas som logg.

## Arbetsgång

1. Läs `data/raw/swing_eval/summary.json` (beskrivs i `scripts/swing_eval.py`, punkt 3):
   - `top`: Gårdagens bästa rörelser (topp 10 mot index efter 1 dag).
   - `patterns."3"` och `patterns."5"`: för varje kännetecken andelen bland de 20 % med störst rörelse mot index
     (`top.share`, `top.k` av `top.n`, 95 % intervall `top.ci`) jämfört med övriga (`other`), skillnaden i
     procentenheter (`diff_pp`), antal fall (`cases`) och hur många veckor i rad mönstret hållit (`weeks_held`,
     `held` = minst 4).
   - `period`, `snapshots`, `entries`.
   Andelar står som tal mellan 0 och 1; skriv dem i procent (0,31 → 31 %).
2. Skriv 5–8 meningar på svenska om vad underlaget visar: vilka kännetecken som var vanligare bland de största
   rörelserna mot index efter 3 och 5 dagar, hur stort underlaget är och hur osäkert det är (breda intervall, få
   veckor), och vilka mönster som hållit minst 4 veckor (eller att inget gjort det än). Ta med antal fall när du
   nämner en andel.
   Skriv lättläst: avrunda andelar till heltal eller en decimal (29,8 % eller 30 %), skriv kännetecknen med vanliga
   ord utan prefix (fastighetsbolagen, inte "Sektor: Fastigheter"), och ta inte med fler tal än som behövs.
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
- Säg inget om framtiden: mönstren beskriver vad som hänt, och fallen är få och överlappar i tid.
- Skriv inget annat än filen ovan.
