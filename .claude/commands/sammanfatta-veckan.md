---
description: Sammanfatta veckans finansnyheter från data/raw/<vecka>/ till data/weeks/<vecka>.json
argument-hint: "[ISO-vecka, t.ex. 2026-W40 – utelämnas = senaste mappen i data/raw/]"
---

Du ska skriva veckans sammanfattning för Veckans finans. Vecka: `$ARGUMENTS`. Om ingen vecka anges, ta den senaste mappen i `data/raw/`.

## Schemalagd körning

Varje måndag kör `scripts/run_weekly.ps1` kommandot utan att någon tittar på, med behörighetsläget `dontAsk`.
Bara de verktyg och kommandon som står i den här filen är tillåtna. Allt annat nekas automatiskt, till exempel
PowerShell, webben, andra git-kommandon och skrivning utanför `data/weeks/`. Om något nekas ska du inte försöka ta dig
runt det med ett annat verktyg eller kommando. Nämn det i slutrapporten och fortsätt med resten. Ditt sista svar
sparas som slutrapporten.

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

1. **Läs indexet via översikten:** kör `.venv/Scripts/python scripts/index_overview.py <vecka>` och sedan
   `--sida 2`, `--sida 3` och så vidare tills alla sidor är lästa (första raden visar antalet sidor). Varje rad är
   `nr | källa | publicerad | company | R/- (rutin) | F/U (fulltext/utdrag) | path | rubrik`. Behöver du `url`,
   `tags` eller `also_in` för en vald post läser du den posten i `data/raw/<vecka>/index.json`. Varje post har
   `title`, `company` (en slug ur källans URL, alltså inte ett bolagsnamn), `source`, `published` (svensk tid),
   `tags`, `routine`, `text_kind` och `path`.
   - `routine: true` betyder kallelser, kommunikéer, återköp, antal aktier, inbjudningar och nyhetsbrev. Välj dem bara
     om de har tydlig betydelse för sektorn.
   - `text_kind: "utdrag"` (Cision) betyder att filen bara innehåller början av pressmeddelandet. Allt du skriver om
     posten, och alla citat, måste rymmas i utdraget.
   - `also_in` visar att samma pressmeddelande även fanns hos en annan källa. Använd posten som den är.
2. **Välj 5–8 nyheter per sektor** utifrån rubrik, bolag och taggar. Välj färre om sektorn inte har så många relevanta
   nyheter, och ta inte med sektorer som saknar relevanta nyheter. Prioritera i den här ordningen:
   1. **Large Cap-rapporter:** översiktens första sida listar veckans rapporter från Large Cap-bolag ("Large
      Cap-rapporter i veckan", med sektor och `path`). De går före allt annat i sin sektor. Är det fler än 8 i en
      sektor väljer du de största och mest kurspåverkande, och nämner de övriga kort i sektorsammanfattningen om de
      kan beläggas med citat. **En sektor där ett Large Cap-bolag rapporterat får aldrig vara tom.** Valideringen
      underkänner det.
   2. **Bevakade bolag:** MFN-poster (`source: "mfn"`) vars `company` exakt matchar ett `mfn_slug` i `instruments.csv`,
      om de inte är rutin. Det är bolagen jag valt att bevaka.
   3. Övriga kurspåverkande nyheter: andra rapporter, order, vinstvarningar och ledningsbyten i större bolag, samt
      makro. **Småförvärv** (ett mindre eller onoterat bolag köps upp) kommer sist och tas bara med om sektorn har plats
      kvar efter rapporterna.

   `mfn_slug` påverkar bara urvalet. En slug-träff betyder att posten är *utgiven av* bolaget på den raden, men övriga
   bolag i texten matchas fortfarande på `namn`/`alias` enligt regel 7. Cision-postens `company` är Cisions egen slug
   och jämförs inte med `mfn_slug`.
3. **Läs sedan bara de valda filerna** (`path`) i fulltext. Om en fil visar sig vara irrelevant byter du till en annan
   kandidat ur indexet.
   - Poster med `source: "bolag"` är pressmeddelanden från ett bevakat bolags egen webbplats, inlagda för hand
     (`scripts/add_raw_item.py`) när bolaget inte publicerar via MFN eller Cision, t.ex. ABB. Bolaget står i postens
     `data.ticker`. Behandla dem som bevakade bolags egna pressmeddelanden.
     - `text_kind: "fulltext"`: texten är ordagrann (källans villkor tillåter återgivning).
     - `text_kind: "sammanfattning"`: källans villkor tillåter inte återgivning, så filen har bara rubrik, länk och
       en kort sammanfattning med egna ord ("manuellt tillagd"). Skriv inget utöver det sammanfattningen säger;
       citaten blir ur sammanfattningen.
4. **Riksbankens styrränta är ett nyckeltal, inte en nyhet.** Ta posten med `source: "riksbanken"` och det senaste
   `data.policy_rate.date` som ligger inom veckan, och fyll `key_figures.policy_rate` i veckofilen:
   - `value`, `date`, `previous_value` och `changed_on` kopieras exakt från postens `data.policy_rate`.
   - `source_url` är postens `url`, alltså Riksbankens webbsida. `raw_file` är postens `path`.
   - Gör ingen nyhet av styrräntan. Valideringen underkänner en nyhet med `raw_file` under `riksbanken/`.
   - Poster med `data.policy_decision` (Riksbankens räntebeslut, t.ex. "Styrräntan höjs till 2 procent") är varken
     nyckeltalet eller en nyhet. Sidan visar ett beslut som ännu inte börjat gälla bredvid nyckeltalet.
   - Makrosammanfattningen hänvisar till nyckeltalet, till exempel "Styrräntan ligger kvar på 1,75 procent
     (se nyckeltal)". Värdena och datumen i nyckeltalet räknas som belägg för makrosammanfattningen, men bara där.
   - **Formulering beroende på ändringsdatumet (`changed_on`):**
     - Om `changed_on` ligger före veckan, eller är `null`, skriver du "oförändrad", till exempel "Styrräntan ligger
       kvar på 1,75 procent, oförändrad sedan 1 oktober 2025". Skriv aldrig "sänkt" eller "höjd" om styrräntan då.
       Valideringen underkänner det. Förväntningar om framtida ändringar ("hushållen tror att räntan höjs") är tillåtna.
     - Om `changed_on` ligger inom veckan har räntan ändrats den här veckan. Då ska det stå först i
       makrosammanfattningen, med "sänkt" eller "höjd" från `previous_value`.
   - Om Riksbanksposter saknas i veckan utelämnar du `key_figures` och nämner det i slutrapporten.
5. **Källfel:** kontrollera `runs` i indexet (sista raden i översikten räknar fel per källa). Om någon källa har `status: "fel"` i flera körningar i följd kan veckan
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
   - **Uppköpsbud och avnotering:** när nyheten handlar om ett uppköpsbud, en avnotering eller en tvångsinlösen sätter
     du `"bid_target": true` på **målbolaget**, alltså det bolag som köps upp eller avnoteras, inte budgivaren. Det
     måste vara matchat och direkt berört. Sidan markerar då bolagets insynsförsäljningar samma vecka som "under
     pågående uppköpsbud". Valideringen underkänner `bid_target` om nyheten inte nämner bud, avnotering eller
     tvångsinlösen.
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
10. **Sentiment (ton) per nyhet:** varje nyhet får `sentiment` med `label`, `rationale` och `basis`.
    - `label` är `"positiv"`, `"negativ"` eller `"neutral"` och beskriver **nyhetens ton för bolaget**, alltså om det
      som händer är bra eller dåligt för verksamheten. Det är inte en bedömning av aktien. Använd `neutral` när
      nyheten är blandad, oklar eller rent informativ, och alltid för rutinmeddelanden.
    - `basis` är index, med början på 0, till de citat i `evidence` som bedömningen bygger på. Minst ett.
    - `rationale` är en mening som bygger **bara** på de refererade citaten:
      - Siffror och riktningsord ("ökade", "minskade") måste finnas i just de citaten.
      - Minst ett innehållsord ur citaten ska finnas med.
      - **Inga ord om kursen eller aktien och inga råd**, till exempel kurs, aktien, kursmål, köpläge, undervärderad
        eller rekommendera. Tonen bygger på nyhetens innehåll, aldrig på kursrörelsen.
    - Valideringen kontrollerar spårbarheten men inte om bedömningen är riktig. Läs därför citaten noga.
    - Sidan visar tonen bredvid veckans kursrörelse och markerar "avviker" när de går åt olika håll. Försök inte få
      dem att stämma.
11. **Id:** `item.id` ska vara unikt, med gemener och bindestreck, t.ex. `fast-<bolag>-<ämne>`.

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
1b. **Insyn:** kör `.venv/Scripts/python scripts/registers.py <vecka>`. Analysen räknas fram deterministiskt ur
   FI-datan i `data/raw/`. Du skriver eller ändrar ingenting i den. Den hamnar i `data/analysis/<vecka>.json`. Ta med
   i slutrapporten vad skriptet skriver ut, alltså antal bolag med insyn och vilka insynsdagar som saknas. Blankning
   räknas inte längre (FI tillåter inte automatisk hämtning).
2. **Säkerhetskopia till det privata repot** `data/raw/` (veckans-finans-raw): kör
   `.venv/Scripts/python scripts/backup_week.py <vecka>`. Skriptet gör alla git-steg själv. Det stannar om
   `data/raw/` har oincheckade ändringar, kör `pull --rebase` och kopierar vecko-, kurs- och analysfilen till
   `weeks/`, `prices/` och `analysis/`. Det lägger bara till just de filerna och committar med "Säkerhetskopia
   <vecka> (validering OK|EJ GODKÄND)", där statusen räknas fram av skriptet. Sedan pushar det, med ett nytt försök
   om Actions hunnit pusha emellan. Kör inga egna git-kommandon för säkerhetskopian. Om skriptet misslyckas:
   rapportera dess sista rad. Filerna finns kvar lokalt.
3. Committa eller pusha **aldrig** veckofilen, kursfilen eller analysfilen till det publika repot
   (`veckans-finans`). Där är `data/weeks/`, `data/prices/` och `data/analysis/` git-ignorerade, och så ska det förbli.

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
- insyn: det som `registers.py` skrev ut
- säkerhetskopia: commit-hash som pushats till veckans-finans-raw, "oförändrad sedan förra kopian", eller varför den
  inte gick igenom
