---
description: Sammanfatta veckans finansnyheter från data/raw/<vecka>/ till data/weeks/<vecka>.json
argument-hint: "[ISO-vecka, t.ex. 2026-W40 – utelämnas = senaste mappen i data/raw/]"
---

Du ska skriva veckans sammanfattning för Veckans finans. Vecka: `$ARGUMENTS`. Om ingen vecka anges, ta den senaste mappen i `data/raw/`.

## Indata och utdata

- `data/raw/` är en klon av det privata repot `veckans-finans-raw`. Kör `git -C data/raw pull` först. Om det
  misslyckas, fortsätt med det som finns och nämn det i slutrapporten.
- `data/raw/<vecka>/` är den enda tillåtna källan. Använd inte egen kunskap, webbsökning eller andra filer för fakta.
- Läs `data/instruments.csv` (`namn,alias,ticker,yahoo_ticker,börs,sektor,mfn_slug`, alias separeras med `|`, `sektor` är ett id ur `data/sectors.json` och kan vara tom, `mfn_slug` är valfri och ofta tom) och `data/sectors.json`.
- Skriv `data/weeks/<vecka>.json` enligt `schema/week.schema.json`. Läs schemat först.
- Skriv inte `"mock"`. Använd aldrig `data/mock/` som källa.
- Som sista steg hämtas kurser och veckan säkerhetskopieras till det privata repot. Se "Sista steget" nedan.

## Arbetsgång: index först, fulltext bara för urvalet

Veckan har typiskt flera hundra poster. Läs dem inte alla.

1. **Läs `data/raw/<vecka>/index.json`.** Varje post har `title`, `company` (en slug ur källans URL, alltså inte ett
   bolagsnamn), `source`, `published` (svensk tid), `tags`, `routine`, `text_kind` och `path`.
   - `routine: true` betyder kallelser, kommunikéer, återköp, antal aktier, inbjudningar och nyhetsbrev. Välj dem bara
     om de har tydlig betydelse för sektorn.
   - `text_kind: "utdrag"` (Cision) betyder att filen bara innehåller början av pressmeddelandet. Allt du skriver om
     posten, och alla citat, måste rymmas i utdraget.
   - `also_in` visar att samma pressmeddelande även fanns hos en annan källa. Använd posten som den är.
2. **Välj 5–8 nyheter per sektor** utifrån rubrik, bolag och taggar. Välj färre om sektorn inte har så många relevanta
   nyheter, och ta inte med sektorer som saknar relevanta nyheter. Prioritera i den här ordningen:
   1. **Bevakade bolag:** MFN-poster (`source: "mfn"`) vars `company` exakt matchar ett `mfn_slug` i `instruments.csv`,
      om de inte är rutin. Det är bolagen jag valt att bevaka.
   2. Övriga kurspåverkande nyheter: rapporter, order, förvärv, vinstvarningar och ledningsbyten i större bolag, samt makro.

   `mfn_slug` påverkar bara urvalet. En slug-träff betyder att posten är *utgiven av* bolaget på den raden, men övriga
   bolag i texten matchas fortfarande på `namn`/`alias` enligt regel 7. Cision-postens `company` är Cisions egen slug
   och jämförs inte med `mfn_slug`.
3. **Läs sedan bara de valda filerna** (`path`) i fulltext. Om en fil visar sig vara irrelevant byter du till en annan
   kandidat ur indexet.
4. **Riksbankens styrränta är ett nyckeltal, inte en nyhet.** Ta posten med `source: "riksbanken"` och det senaste
   `data.policy_rate.date` som ligger inom veckan, och fyll `key_figures.policy_rate` i veckofilen:
   - `value`, `date`, `previous_value` och `changed_on` kopieras exakt från postens `data.policy_rate`.
   - `source_url` är postens `url`, alltså Riksbankens webbsida. `raw_file` är postens `path`.
   - Gör ingen nyhet av styrräntan. Valideringen underkänner en nyhet med `raw_file` under `riksbanken/`.
   - Makrosammanfattningen hänvisar till nyckeltalet, till exempel "Styrräntan ligger kvar på 1,75 procent
     (se nyckeltal)". Värdena och datumen i nyckeltalet räknas som belägg för makrosammanfattningen, men bara där.
   - **Formulering beroende på ändringsdatumet (`changed_on`):**
     - Om `changed_on` ligger före veckan, eller är `null`, skriver du "oförändrad", till exempel "Styrräntan ligger
       kvar på 1,75 procent, oförändrad sedan 1 oktober 2025". Skriv aldrig "sänkt" eller "höjd" om styrräntan då.
       Valideringen underkänner det. Förväntningar om framtida ändringar ("hushållen tror att räntan höjs") är tillåtna.
     - Om `changed_on` ligger inom veckan har räntan ändrats den här veckan. Då ska det stå först i
       makrosammanfattningen, med "sänkt" eller "höjd" från `previous_value`.
   - Om Riksbanksposter saknas i veckan utelämnar du `key_figures` och nämner det i slutrapporten.
5. **Källfel:** kontrollera `runs` i indexet. Om någon källa har `status: "fel"` i flera körningar i följd kan veckan
   ha luckor. Nämn det i slutrapporten.

## Regler

1. **Språk:** all text på svenska, saklig och neutral. Inga köp- eller säljrekommendationer, inga kursmål och inga förutsägelser om kursrörelser.
2. **Urval:** ta med nyheter som är relevanta för noterade bolag, sektorer eller makro. Hoppa över rutinmeddelanden som kallelser till stämma eller ändrade antal aktier, om de inte har betydelse för sektorn.
3. **Sektorer:** använd bara `id` ur `data/sectors.json`. Ta bara med sektorer som har minst en nyhet. Sektorns `summary` ska vara 2–5 meningar.
4. **Använd bara exakta siffror ur citaten.** Varje tal i `headline`, `summary` och sektorsammanfattningen (belopp, procent, datum, år och antal) ska stå i något av nyhetens `evidence`-citat. Det ska vara samma värde, avrundat eller omräknat är inte tillåtet: skriv "1,75 procent", aldrig "knappt 2 procent". Skriv "2 000 miljoner kronor" om källan säger det, inte "2 miljarder". Om en siffra inte går att belägga med ett citat tar du bort siffran.
5. **Evidence:** citaten ska vara ordagranna utdrag ur källfilens text (under `---`), högst 25 ord vardera, kopierade tecken för tecken. Försök inte rätta stavning eller skiljetecken. `source.raw_file` är postens `path` ur indexet. `source.url` är postens `url` och `source.name` dess `source_name`. Citaten visas inte på sidan, de används bara för kontroll.
6. **Datum:** `published` är datumdelen av raden `Publicerad:` i källfilen (Europe/Stockholm) och måste ligga inom veckan (mån–sön). Kontrollera datumet i filen och anta det inte från filnamnet eller körningstiden.
7. **Bolag:** ta med varje bolag som nämns och som nyheten berör.
   - Matcha mot `instruments.csv` på `namn` eller något `alias`. Vid träff kopierar du `ticker`, `yahoo_ticker` och `börs` (till `exchange`) exakt från raden och sätter `"match": "matchad"`.
   - **Hitta aldrig på tickers.** Om bolaget inte finns i csv:n sätter du `"match": "ej i listan"` och `ticker`/`yahoo_ticker`/`exchange` till `null`. Det gäller även om bolaget är noterat, till exempel på First North eller Small Cap. "Ej i listan" säger bara att bolaget saknas i bevakningslistan, inget om noteringen. Lista sådana bolag i slutrapporten (se nedan) så att jag kan lägga till dem i csv:n.
   - **Avsändare av undersökningar och index kopplas inte.** När ett bolag bara publicerar en undersökning, ett index
     eller en analys om något annat, kopplas avsändaren inte till nyheten, varken direkt eller indirekt. Det gäller till
     exempel Swedbanks inköpschefsindex, SEB:s boprisindikator, bankers konjunkturprognoser och Swecos analyser. Bolaget
     kopplas bara om nyheten handlar om dess egen verksamhet, till exempel dess egna siffror eller affärer.
   - `impact`: `"direkt"` om bolaget är föremål för nyheten, `"indirekt"` om det påverkas via kunder, leverantörer, konkurrenter, räntor eller liknande. `rationale` är en mening.
8. **Indirekt sektorpåverkan:** ange i `sector_impacts` vilka *andra* sektorer nyheten rimligen påverkar, med riktning (`positiv`/`negativ`/`blandad`/`oklar`) och en motivering på en mening. Använd `oklar` hellre än att gissa. En tom lista är okej.
9. **Sektor och indirekta kopplingar via `sektor` i `instruments.csv`:**
   - **Placering:** en nyhet om ett matchat bolag hamnar under bolagets `sektor`, om inte innehållet tydligt hör
     hemma någon annanstans, till exempel makro. Om `sektor` är tom bedömer du utifrån texten.
   - **Indirekta bolag:** ett bolag med `impact: "indirekt"` måste ha en `sektor` som antingen är nyhetens egen sektor,
     till exempel en konkurrent, eller finns i nyhetens `sector_impacts`. Valideringen underkänner annars. Bestäm
     därför först `sector_impacts` och välj sedan indirekta bolag inom de sektorerna, aldrig tvärtom.
   - **Bolag som inte nämns i texten** får läggas till som `indirekt` bara om kopplingen är konkret: samma marknad,
     en namngiven kund, leverantör eller konkurrent, eller direkt räntekänslighet. Motiveringen ska nämna kopplingen.
     Högst två sådana bolag per nyhet. Hellre inga än svaga kopplingar. Att två bolag har samma sektor räcker inte.
10. **Id:** `item.id` ska vara unikt, med gemener och bindestreck, t.ex. `fast-<bolag>-<ämne>`.

## Kontroll innan du är klar

1. Gå igenom varje nyhet och jämför datum, siffror och riktning (ökade/minskade) mot källfilen en gång till.
2. Kör `.venv/Scripts/python scripts/validate.py data/weeks/<vecka>.json`.
3. Rätta felen och kör igen. **Högst tre valideringskörningar totalt.** Rätta genom att korrigera felet mot källtexten:
   rätt siffra, rätt datum, rätt riktningsord eller ett bättre citat.
4. Om något fortfarande underkänns efter tredje körningen ska du **stanna**. Ta inte bort siffror, gör inte texten
   vagare och stryk inte nyheter bara för att få valideringen att gå igenom. Lämna filen som den är och lista de
   kvarvarande felen ordagrant i slutrapporten, så avgör jag hur de ska hanteras.
5. Ändra aldrig källtexten, csv:n eller valideringen.

## Sista steget: kurser och säkerhetskopia

Görs även om valideringen blev **EJ GODKÄND**, så att arbetet inte går förlorat. Statusen anges i commit-meddelandet.

1. **Kurser:** kör `.venv/Scripts/python scripts/prices.py <vecka>`. Skriptet är fail-soft. Exit-kod 1 betyder att
   ingen kurs kunde hämtas och ingen kursfil skrevs. Fortsätt då utan kursfil, och nämn det i slutrapporten.
2. **Säkerhetskopia till det privata repot** `data/raw/` (veckans-finans-raw):
   1. Kontrollera att `git -C data/raw status --porcelain` är tomt. Om det inte är tomt: stanna och rapportera.
      Committa aldrig någon annans ändringar.
   2. Kör `git -C data/raw pull --rebase` för att hämta GitHub Actions senaste hämtningar.
   3. Kopiera `data/weeks/<vecka>.json` till `data/raw/weeks/<vecka>.json`. Kopiera också `data/prices/<vecka>.json` till
      `data/raw/prices/<vecka>.json` om kursfilen finns. Skapa mapparna vid behov och skriv över en tidigare kopia av
      samma vecka.
   4. Lägg bara till just dessa filer: `git -C data/raw add weeks/<vecka>.json prices/<vecka>.json`. Ta med kursfilen
      bara om den finns. Använd aldrig `git add -A` eller `git add .`.
   5. Om något ändrats: `git -C data/raw commit -m "Säkerhetskopia <vecka> (validering OK)"`, eller `(validering EJ
      GODKÄND)`. Om filerna är identiska med förra kopian hoppar du över commiten.
   6. Kör `git -C data/raw push`. Om pushen avvisas för att Actions hunnit pusha emellan: kör
      `git -C data/raw pull --rebase` och försök **en** gång till. Om det fortfarande misslyckas, eller om inloggningen
      saknas: stanna och rapportera. Filerna finns kvar lokalt i `data/weeks/` och `data/prices/`.
3. Committa eller pusha **aldrig** veckofilen eller kursfilen till det publika repot (`veckans-finans`). Där är
   `data/weeks/` och `data/prices/` git-ignorerade, och så ska det förbli.

## Slutrapport till mig

Kort, i chatten:
- antal poster i indexet, antal valda nyheter per sektor, och antal fulltextlästa filer som valdes bort (med en rad om varför)
- källfel eller luckor enligt `runs`, och om `git pull` misslyckades
- bolag markerade "ej i listan", med förslag på om de bör läggas till i `instruments.csv` (du får inte lägga till dem själv)
- matchade bolag vars `mfn_slug` är tom men som hade MFN-poster i veckan: föreslå sluggen ur postens `company`
  (du får inte fylla i den själv)
- valideringens resultat: OK efter hur många körningar, eller **EJ GODKÄND** med alla kvarvarande fel listade
- eventuella varningar från valideringen
- kurser: antal hämtade och antal fel, eller att ingen kursfil skrevs
- säkerhetskopia: commit-hash som pushats till veckans-finans-raw, "oförändrad sedan förra kopian", eller varför den
  inte gick igenom
