---
description: Bedöm dagens händelser i sektorn (bolagsspecifik eller branschgemensam) med en menings motivering, märkt som tolkning
argument-hint: "<datum>, t.ex. 2026-11-26"
---

Du ska bedöma händelserna i vyn Swing ("Händelser i sektorn") för Veckans finans. Argument: `$ARGUMENTS`, datumet
som händelsefilen gäller.

## Schemalagd körning

Kommandot körs utan att någon tittar på, med behörighetsläget `dontAsk`. Bara de verktyg och kommandon som står här
är tillåtna; allt annat nekas automatiskt. Försök aldrig ta dig runt ett nekande. Ditt sista svar sparas som logg.

## Arbetsgång

1. Läs `data/events/$ARGUMENTS.json` (beskrivs i `scripts/events.py`). Varje post i `events` är ett bolag som rörde
   sig minst 3 gånger sin ATR mot index samma dag som ett eget pressmeddelande (`news`) eller en rapport (`report`).
2. För varje händelse: läs pressmeddelandena i `news` (filen i `raw_file`, annars bara rubriken `title`). Bedöm om det
   som meddelandet handlar om är
   - `bolagsspecifik`: gäller främst bolaget självt (t.ex. en egen order, en egen rapport med bolagsspecifika
     förklaringar, ett eget avtal, en ledningsförändring, en emission), eller
   - `branschgemensam`: beskriver något som rimligen gäller fler bolag i branschen (t.ex. efterfrågan, priser,
     regler, räntor eller kostnader som hela branschen möter).
   Är det oklart: välj den klass texten stöder bäst och säg i motiveringen vad du grundar dig på.
3. Skriv filen `data/events/$ARGUMENTS.claude.json`:
   ```json
   {"date": "$ARGUMENTS", "judgements": [
     {"ticker": "<händelsens ticker>", "class": "bolagsspecifik", "label": "Claudes tolkning",
      "reason": "<en mening>"}
   ]}
   ```
   En bedömning per händelse, med samma `ticker` som i händelsefilen.
4. Kör `.venv/Scripts/python scripts/events.py --kontrollera $ARGUMENTS`. Rätta och kör igen tills den säger att
   bedömningarna är godkända (högst tre försök).

## Regler (kontrolleras)

- `class` är `bolagsspecifik` eller `branschgemensam`. `reason` är en mening (högst 300 tecken) som säger vad i
  meddelandet bedömningen grundar sig på.
- Bedömningen är din tolkning, märkt så (`label`), och beskrivande. Använd inte orden köp, sälj, kursmål eller
  rekommendation i någon böjning, och ingen uppmaning. Säg inget om framtida kurser.
- Hitta inte på uppgifter som inte står i meddelandet.
- Skriv inget annat än filen ovan.
