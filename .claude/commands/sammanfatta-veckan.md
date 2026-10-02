---
description: Sammanfatta veckans finansnyheter från data/raw/<vecka>/ till data/weeks/<vecka>.json
argument-hint: "[ISO-vecka, t.ex. 2026-W40 – utelämnas = senaste mappen i data/raw/]"
---

Du ska skriva veckans sammanfattning för Veckans finans. Vecka: `$ARGUMENTS`. Om ingen vecka anges, ta den senaste mappen i `data/raw/`.

## Indata och utdata

- Läs **alla** filer under `data/raw/<vecka>/`. Det är den enda tillåtna källan. Använd inte egen kunskap, webbsökning eller andra filer för fakta.
- Läs `data/instruments.csv` (`namn,alias,ticker,yahoo_ticker,börs`, alias separeras med `|`) och `data/sectors.json`.
- Skriv `data/weeks/<vecka>.json` enligt `schema/week.schema.json`. Läs schemat först.
- Skriv inte `"mock"`. Använd aldrig `data/mock/` som källa.

## Regler

1. **Språk:** all text på svenska, saklig och neutral. Inga köp- eller säljrekommendationer, inga kursmål och inga förutsägelser om kursrörelser.
2. **Urval:** ta med nyheter som är relevanta för noterade bolag, sektorer eller makro. Hoppa över rutinmeddelanden som kallelser till stämma eller ändrade antal aktier, om de inte har betydelse för sektorn.
3. **Sektorer:** använd bara `id` ur `data/sectors.json`. Ta bara med sektorer som har minst en nyhet. Sektorns `summary` ska vara 2–5 meningar.
4. **Använd bara exakta siffror ur citaten.** Varje tal i `headline`, `summary` och sektorsammanfattningen (belopp, procent, datum, år och antal) ska stå i något av nyhetens `evidence`-citat. Det ska vara samma värde, avrundat eller omräknat är inte tillåtet: skriv "1,75 procent", aldrig "knappt 2 procent". Skriv "2 000 miljoner kronor" om källan säger det, inte "2 miljarder". Om en siffra inte går att belägga med ett citat tar du bort siffran.
5. **Evidence:** citaten ska vara ordagranna utdrag ur källfilen, högst 25 ord vardera, kopierade tecken för tecken. Försök inte rätta stavning eller skiljetecken. `source.raw_file` är sökvägen relativt `data/raw/<vecka>/`. Citaten visas inte på sidan, de används bara för kontroll.
6. **Datum:** `published` är källans publiceringsdatum (Europe/Stockholm) och måste ligga inom veckan (mån–sön). Kontrollera datumet i källtexten och anta det inte från filnamnet.
7. **Bolag:** ta med varje bolag som nämns och som nyheten berör.
   - Matcha mot `instruments.csv` på `namn` eller något `alias`. Vid träff kopierar du `ticker`, `yahoo_ticker` och `börs` (till `exchange`) exakt från raden och sätter `"match": "matchad"`.
   - **Hitta aldrig på tickers.** Om bolaget inte finns i csv:n sätter du `"match": "onoterat"` och `ticker`/`yahoo_ticker`/`exchange` till `null`, även om du tror att bolaget är noterat. Lista sådana bolag i slutrapporten (se nedan) så att jag kan lägga till dem i csv:n.
   - `impact`: `"direkt"` om bolaget är föremål för nyheten, `"indirekt"` om det påverkas via kunder, leverantörer, konkurrenter, räntor eller liknande. `rationale` är en mening.
8. **Indirekt sektorpåverkan:** ange i `sector_impacts` vilka *andra* sektorer nyheten rimligen påverkar, med riktning (`positiv`/`negativ`/`blandad`/`oklar`) och en motivering på en mening. Använd `oklar` hellre än att gissa. En tom lista är okej.
9. **Id:** `item.id` ska vara unikt, med gemener och bindestreck, t.ex. `fast-<bolag>-<ämne>`.

## Kontroll innan du är klar

1. Gå igenom varje nyhet och jämför datum, siffror och riktning (ökade/minskade) mot källfilen en gång till.
2. Kör `.venv/Scripts/python scripts/validate.py data/weeks/<vecka>.json`.
3. Rätta felen och kör igen. **Högst tre valideringskörningar totalt.** Rätta genom att korrigera felet mot källtexten:
   rätt siffra, rätt datum, rätt riktningsord eller ett bättre citat.
4. Om något fortfarande underkänns efter tredje körningen ska du **stanna**. Ta inte bort siffror, gör inte texten
   vagare och stryk inte nyheter bara för att få valideringen att gå igenom. Lämna filen som den är och lista de
   kvarvarande felen ordagrant i slutrapporten, så avgör jag hur de ska hanteras.
5. Ändra aldrig källtexten, csv:n eller valideringen.

## Slutrapport till mig

Kort, i chatten:
- antal sektorer och nyheter, och antal lästa källfiler som inte togs med (med en rad om varför)
- bolag markerade "onoterat", med förslag på om de bör läggas till i `instruments.csv` (du får inte lägga till dem själv)
- valideringens resultat: OK efter hur många körningar, eller **EJ GODKÄND** med alla kvarvarande fel listade
- eventuella varningar från valideringen
