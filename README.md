# Veckans finans

Statisk sida som sammanfattar veckans finansnyheter per sektor och kopplar dem till noterade aktier.
Inga betaltjänster eller betal-API:er. **Inget investeringsråd.**

Sidan är bara för eget bruk och läses lokalt på localhost (se [Läsa veckan lokalt](#läsa-veckan-lokalt)).
Den publiceras inte. Veckorna och kurserna ligger bara på den här datorn.

## Dataflöde

| Steg | Skriver | Committas |
|---|---|---|
| `scripts/fetch.py` (RSS: MFN, Cision; API: Riksbankens styrränta) | `data/raw/<vecka>/` | nej, rådatan ligger i det privata repot `veckans-finans-raw` |
| `/sammanfatta-veckan` (Claude Code, manuellt en gång i veckan) | `data/weeks/<vecka>.json` | nej, git-ignorerad, bara lokalt |
| `scripts/prices.py` (yfinance, valfritt, körs lokalt) | `data/prices/<vecka>.json` | nej, git-ignorerad, bara lokalt |
| `scripts/validate.py` | – | – |

Veckor anges som ISO-vecka, t.ex. `2026-W40` (mån–sön, Europe/Stockholm).

### Hämtning (`scripts/fetch.py`)

Körs varannan timme i GitHub Actions i det privata repot `veckans-finans-raw`.
Workflow-mallen ligger i [`deploy/raw-repo/`](deploy/raw-repo/). Lokalt klonas det privata repot in som `data/raw/`.

| Källa | Adress (kontrollerad 2026-10-02) | Text |
|---|---|---|
| MFN | `https://mfn.se/all/s/nordic.rss?limit=200` (odokumenterad, men flödet anger själv adressen); morgonflödet använder `limit=500` (`--mfn-limit`), fortfarande ett anrop | fulltext |
| Cision | `https://news.cision.com/se/ListItems?format=rss` | utdrag, ca 600 tecken |
| Cision, bolagets eget flöde | `https://news.cision.com/<cision_slug>/ListItems?format=rss`, ett anrop per bolag med `cision_slug` i `instruments.csv` (i dag Gentoo Media) | utdrag, poster högst 14 dagar gamla |

Bolag som bara publicerar i Cisions internationella nyhetsrum syns inte i den svenska listan. Gentoo Media publicerar
till exempel under `news.cision.com/gentoo-media-inc`, utan `/se/`. Fyll då i `cision_slug`, bolagets del av adressen
till nyhetsrummet på Cision. Flödets adress är den som RSS-ikonen på bolagets Cision-sida öppnar (kontrollerat för
Gentoo 2026-11-05).

Cision har inga särskilda användarvillkor för news.cision.com, bara innehålls-, integritets- och cookiepolicy.
Om-sidan (`news.cision.com/_editorial/about`) erbjuder prenumeration på ett visst bolags nyheter "by email and RSS",
och robots.txt spärrar ingenting (läst 2026-11-05).
| Riksbanken | `https://api.riksbank.se/swea/v1/Observations/SECBREPOEFF/<från>/<till>`, fem år bakåt i ett anrop (ingen nyckel krävs) | nyckeltal |
| Riksbankens räntebeslut | `https://www.riksbank.se/sv/rss/pressmeddelanden/` (Riksbankens egen RSS, kontrollerad 2026-11-05); för ett nytt beslut också pressmeddelandet, en gång, för dagen beslutet tillämpas | beslut |

Ett pressmeddelande som bara finns på bolagets egen webbplats, till exempel ABB:s 5/11 (ABB lägger bara ut en del
via Cision), kan läggas in för hand med `scripts/add_raw_item.py`:
- Posten får källan "bolag" och märks "manuellt tillagd".
- **Standard är rubrik, tid, länk och en kort sammanfattning med egna ord** (`--summary`, högst 60 ord). Ingen text ur
  pressmeddelandet sparas. Det är det enda tillåtna när källans villkor inte tillåter återgivning. ABB:s "Terms of
  Use" tillåter återgivning bara för journalister och medier och förbjuder scraping (läst 2026-11-05).
- Ordagrann text och ett citat (`--text-file`, `--quote`) kräver `--atergivning-tillaten`, alltså att källans villkor
  uttryckligen tillåter återgivning. Citatet kontrolleras mot texten.
- Posten skrivs till både morgonens och veckans rådata och pushas till det privata repot.
- Morgonrapporten visar sammanfattningen (eller citatet) under rubriken, märkt "manuellt tillagd".
  Veckosammanfattningen behandlar posten som bolagets eget pressmeddelande, men skriver inget utöver sammanfattningen.

- Varje körning gör ett anrop per källa, med en timeout på 30 s och User-Agent `veckans-finans-fetch/0.1 (+repo-URL)`.
- MFN filtreras till svenska eller engelska. `scope SE` sparas alltid, andra scope (CA, DK, FI, IS …) bara när
  avsändaren är ett bolag i `instruments.csv`. Det avgörs av `mfn_slug`, eller av namnet eller ett alias som slug,
  med samma regel som rapportkalendern. Utan filtret föll till exempel Lundin Gold och Lundin Mining (scope CA) och
  Arctic Paper (PL) bort, kontrollerat 2026-11-05. Insynshandel (`sub:ci:insider`) sparas inte.
  Rutinmeddelanden sparas men får `routine: true`. De känns igen på MFN-taggar eller rubrik.
- Varje post hamnar i veckan för sin egen publiceringstid i svensk tid.
- Om samma pressmeddelande finns hos både MFN och Cision behålls MFN-versionen, och Cision-länken sparas i `also_in`.
- En källa som fallerar loggas i `index.json` → `runs`, och de övriga sparas ändå. Det ger exit-kod 0 och en
  `::warning::` i Actions-loggen. Bara om alla källor fallerar blir exit-koden 1 och körningen röd.

## Datamodell

- [`schema/week.schema.json`](schema/week.schema.json): sektorer → nyheter → bolag och indirekt sektorpåverkan.
  Varje nyhet har `evidence`, ordagranna citat ur källtexten (max 25 ord, visas inte på sidan).
  Alla siffror i rubrik och sammanfattning måste finnas exakt i något citat. Jämförelsen sker efter normalisering
  (decimalkomma/punkt, mellanslag och hårt mellanslag som tusentalsavgränsare, %/procent, typografiskt minus),
  så avrundade eller omräknade siffror underkänns. I ett belopp eller värde per aktie (valutaenhet efter talet, eller
  fjolårsvärdet inom parentes efter ett sådant) läses också punkt som tusentalsavgränsare, "2.500 MSEK" = 2 500, och
  kolon som decimaltecken, "0:21 SEK" = 0,21; annars är "08:00" ett klockslag. Rapportutfallens validering använder
  samma funktion (`extract_numbers` i `scripts/validate.py`).
  Valideringen kontrollerar också riktningen. Ett ökningsord (ökade, steg, höjdes, växte, stärktes) får inte stå
  framför ett tal som är negativt i citatet, och ett minskningsord (minskade, sjönk, föll, sänktes, försvagades)
  får inte stå framför ett positivt. Citatets egna riktningsord räknas, så "minskade med 3,2 procent" räknas som negativt.
  Står "till" direkt före talet ("sänktes till 1,75") är det en nivå, och då kontrolleras inte riktningen. "med" kontrolleras.
  **Ordlistan fångar inte allt.** Den matchar på ordstam inom fyra ord före talet i samma mening. Den missar synonymer
  (backade, rasade, lyfte, tappade), riktningsord efter talet ("en ökning på 3 procent" fångas, "3 procents ökning"
  gör det inte) och negationer ("ökade inte"). Undantaget för "till" gör också att en felaktig riktning framför
  en nivå inte fångas. Kontrollen är ett skyddsnät och ersätter inte en egen läsning.
- **Ton per nyhet:** `sentiment` har `label` (positiv, negativ eller neutral), `rationale` och `basis`, där `basis`
  är index till de citat bedömningen bygger på. Tonen gäller nyheten för bolaget, inte aktien. Valideringen kontrollerar
  spårbarheten men inte om bedömningen är riktig:
  - motiveringens siffror och riktningsord finns i just de refererade citaten
  - minst ett innehållsord är gemensamt med citaten
  - motiveringen innehåller inga ord om kurs eller råd
  - rutinmeddelanden är neutrala

  Sidan visar tonen och markerar "avviker mot index" på aktiekortet när tonen och kursrörelsen *relativt index* går åt
  olika håll, utan att påstå något samband. Utan jämförelse mot index markeras inget.
- **Nyckeltal:** `key_figures.policy_rate` innehåller Riksbankens styrränta, alltså senaste observationen i veckan,
  värdet före senaste ändringen och ändringsdatumet, med länk till
  [Riksbankens sida om styrräntan](https://www.riksbank.se/sv/statistik/rantor-och-valutakurser/styrranta-in--och-utlaningsranta/).
  Den visas överst på sidan. Valideringen jämför värdena med rådatans index och kräver att det är veckans senaste
  observation. Makrosammanfattningen får hänvisa till värdena, men styrräntan är aldrig en nyhet.
  Serien har en observation per bankdag, så "föregående värde" betyder värdet före senaste *ändringen*,
  inte gårdagens observation.
  - **Beslut som ännu inte gäller:** serien visar bara gällande ränta. Därför hämtas Riksbankens senaste räntebeslut ur
    RSS-flödet för pressmeddelanden, med dagen det tillämpas ur själva pressmeddelandet ("Beslutet om styrräntan
    tillämpas från den 11 november 2026"). Morgonrapporten för in beslutet (`policy_decision`).
  - På den senaste veckans sida står då "Beslutad: 2,00 % från 11 nov" bredvid gällande ränta, med länk till
    pressmeddelandet, så länge beslutet inte gällde den dag nyckeltalet avser. Ett beslut om oförändrad ränta visas
    inte.
- [`schema/prices.schema.json`](schema/prices.schema.json): kursförändring per `yahoo_ticker`. Sidan fungerar utan filen.
- [`data/instruments.csv`](data/instruments.csv): `namn,alias,ticker,yahoo_ticker,börs,sektor,isin,mfn_slug`, plus de
  valfria kolumnerna `typ`, `placera_tagg`, `afv_aktie` och `cision_slug`. `cision_slug` är bolagets eget nyhetsrum
  hos Cision (`news.cision.com/<slug>`) och hämtas som ett eget flöde.
  `isin` innehåller alla bolagets aktieslag, åtskilda med `|`. Värdena kommer från samma Nasdaq-hämtning (2026-10-05)
  och används för att koppla FI:s blankningsregister och insynsregister till listan. Flera alias
  separeras med `|`. Underhålls manuellt. `sektor` är ett id ur `sectors.json` och kan vara tom. Den styr var en
  nyhet placeras och vilka bolag som får kopplas *indirekt*: ett indirekt bolag måste tillhöra nyhetens sektor eller
  en sektor i dess `sector_impacts`, vilket valideringen kontrollerar.
  Sektorn kommer från Nasdaqs sektor (ICB) enligt följande mappning:
  Energy, Basic Materials och Utilities blir `energi-ravaror`, Industrials blir `industri`,
  Consumer Discretionary och Consumer Staples blir `konsument`, Health Care blir `halsovard`,
  Financials blir `banker-finans`, Real Estate blir `fastigheter`, Technology blir `teknik`
  och Telecommunications blir `telekom`. `mfn_slug` är valfri och anger bolagets slug i MFN:s URL:er (`mfn.se/a/<slug>/…`).
  Poster från de bolagen prioriteras när veckan sammanfattas. Fyll bara i slugs du sett i en faktisk MFN-URL.

  **Källa (2026-10-05):** Nasdaqs lista över aktier, *Large Cap* och *Mid Cap* på Nasdaq Stockholm
  ([nasdaq.com/european-market-activity/shares](https://www.nasdaq.com/european-market-activity/shares)).
  Listan hämtades via samma anrop som sidan själv gör,
  `api.nasdaq.com/api/nordic/screener/shares?category=MAIN_MARKET&market=STO&segment=LARGE_CAP|MID_CAP`.
  Det gav 163 + 140 aktieslag och 265 bolag.
  - **En rad per bolag.** Preferens- och D-aktier tas inte med. Bolag med flera stamaktieslag får slaget med högst
    omsättning enligt samma källa, till exempel `SEB A`, `SHB A`, `ATCO A`, `EPI A`, `INDU C`, `STE R` och `VOLV B`.
  - **`yahoo_ticker`** följer Yahoos format för Stockholm (`SBB B` blir `SBB-B.ST`). Varje ticker verifierades mot
    Yahoo: att den finns, att börsen är `STO`, att valutan stämmer med Nasdaqs och att bolagsnamnet stämmer.
    Ingen ticker underkändes.
  - **`namn`** är Nasdaqs namn utan aktieslag. Nasdaq förkortar ibland ("Fast. Balder", "Sv. Handelsbanken"), så lägg
    gärna till fullständiga namn som alias.
  - **Alias** läggs till maskinellt med [`scripts/add_aliases.py`](scripts/add_aliases.py). Skriptet tar bort ändelser
    som Holding, Group, AB, Bank, Ltd och Oyj, så att "Avanza Bank Holding" ger "Avanza Bank" och "Avanza". Ett alias
    läggs bara till om det är unikt i listan och har minst tre tecken. Krockar skrivs ut för manuell bedömning.
    Kör skriptet igen efter att nya rader lagts till. Valideringen underkänner ett namn eller alias som finns på två rader.
  - **`mfn_slug`** är ifylld för 20 bolag, de vars slug förekom i den hämtade rådatan och exakt motsvarar bolagsnamnet.
    2026-11-05 lades 13 till, där MFN:s slug avviker från namnet:
    - 11 kontrollerade mot avsändarna i rådatan: `byggmastare-anders-j-ahlstrom-holding`, `fagerhultgroup`,
      `emilshus`, `flatcapital`, `viscaria`, `jm`, `linjemontage`, `mtg`, `neobofastigheter`, `stenhus-fastigheter`
      och `tangen`.
    - 2 ur bolagens MFN-adresser: `arion-bank` och `fenix-outdoor`.
  - Novo Nordisk och Salesforce ligger kvar från den ursprungliga listan.
- [`data/sectors.json`](data/sectors.json): fast sektorlista som styr flikarna.
- `data/mock/`: exempeldata (`"mock": true`) med **påhittade bolag och källor** och en egen
  [`instruments.csv`](data/mock/instruments.csv). Valideringen underkänner mockfiler utanför `data/mock/`.

### Kurser (`scripts/prices.py`)

Körs lokalt efter `/sammanfatta-veckan`:

```bash
.venv/Scripts/python -m pip install -r requirements-prices.txt
.venv/Scripts/python scripts/prices.py 2026-W40
```

- Skriptet läser `yahoo_ticker` för matchade bolag i `data/weeks/<vecka>.json` och skriver `data/prices/<vecka>.json`.
- Förändringen räknas från föregående veckas sista stängning till veckans sista stängning, på justerad stängning.
- **Jämförelse mot index:** samma förändring hämtas för **OMX Stockholm PI (`^OMXSPI`)**. Tickern är hittad med
  yfinance Search och kontrollerad 2026-10-12: börs STO, valuta SEK, typ INDEX. `^OMX` (OMXS30) kontrollerades också,
  men ett brett index passar listan bättre. Varje aktie i SEK får `relative_pp`, alltså förändring minus indexets
  förändring i procentenheter. Aktier i andra valutor jämförs inte. Indexet är ett prisindex, medan aktiernas
  förändring räknas på justerad stängning, så veckor med utdelning skiljer de sig något.
- Fail-soft: en ticker som inte går att hämta hamnar i `errors`. Om ingen ticker går att hämta skrivs ingen fil,
  och en befintlig fil lämnas orörd. Sidan fungerar i båda fallen.
- `--mock --out <fil>` läser mockveckan. Den kurerade mockkursfilen skrivs aldrig över.

### Blankning och insyn (`scripts/fi_fetch.py`, `scripts/registers.py`)

`fi_fetch.py` körs en gång per dygn (06:37 svensk tid) i det privata repots Actions, mallen ligger i
[`deploy/raw-repo/.github/workflows/fi.yml`](deploy/raw-repo/.github/workflows/fi.yml). Varje körning gör tre anrop
till Finansinspektionen. Adresserna kommer från knapparna på FI:s egna sidor och kontrollerades 2026-10-12.

| Fil | Adress | Innehåll |
|---|---|---|
| `<vecka>/fi/blankning-<datum>.json` | `https://www.fi.se/BlankningsRegister/GetBlankningsregisterAggregat` och `…/GetAktuellFile` (ODS) | Summa per emittent av alla positioner över 0,1 %, och enskilda positioner över 0,5 % med innehavare och ISIN |
| `<vecka>/fi/insyn-<datum>.json` | `https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search?…&button=export` (CSV, UTF-16) | Insynstransaktioner publicerade det datumet |
| `fi-historik/blankning-historik-<datum>.json` | `…/GetHistFile`, hämtad en gång med `--history` | Historiska positioner över 0,5 % sedan 2025 |

Data dateras till gårdagen, så att lördagsmorgonens bild hamnar i fredagens vecka.

**Fel räknas, också delvisa.** Misslyckas en del (t.ex. blankningen) men inte den andra blir exit-koden 2. Det som
hämtades committas ändå, men Actions-körningen blir röd med `::error::`. Morgonrapporten listar felet under "Fel
vid hämtningen". Saknas registrets ögonblicksbild för förra handelsdagen visas blankningsdelen som en varning
("Blankningsregistret kunde inte hämtas från FI …") i stället för som 0 positioner.

**Sedan 2026-10-29 avvisar FI skriptade nedladdningar av blankningsregistret.** Adresserna ovan är oförändrade;
FI:s egna knappar anropar fortfarande samma `/BlankningsRegister/…`, kontrollerat 2026-11-02. Från webbläsaren
svarar de 200, men från skript, också GitHub Actions, svarar de 404, oavsett User-Agent, Referer och cookies.
Spärren kringgås inte.

Därför hämtas blankningen inte längre, utan läses ur filer som du laddar ner själv:
1. Ladda ner "Aktuella positioner" och "Aggregerade positioner" med knapparna på
   [FI:s sida](https://www.fi.se/sv/vara-register/blankningsregistret/). "Historiska positioner" är valfri.
2. Spara filerna i [`data/fi_manual/`](data/fi_manual/LÄS_MIG.txt). `*.ods` committas aldrig till det publika repot.
3. Nästa morgonkörning läser dem, eller kör direkt `python scripts/fi_fetch.py --las-in`.

Den senast sparade filen av varje sort läses. Datumet är filens eget, det senaste positionsdatumet i filen (FI:s
filer har inget annat datum). Ögonblicksbilden och historiken kopieras till `data/raw/` och pushas till det privata
repot, där veckans analys och signaler läser dem. Actions hämtar bara insynen (`--utan-blankning`).

Saknas en ögonblicksbild för förra handelsdagen visar morgonrapporten en varning med instruktionen. När
blankningsunderlaget slutar före veckans slut märks veckans blankningssignaler "Blankning t.o.m. <dag>"
(`short_as_of` i signalfilen), med en förklaring ovanför signaltabellen.

`registers.py <vecka>` räknar deterministiskt fram `data/analysis/<vecka>.json` (git-ignorerad) för bolagen i
`instruments.csv`, kopplade via `isin`:
- **Blankning över 0,5 %:** läget per innehavare vid veckans slut jämfört med förra veckans slut, ur historikfilen
  och de dagliga ögonblicksbilderna.
- **Blankning, aggregat:** bara från egna ögonblicksbilder. Den första jämförelsen blir alltså veckan efter att
  jobbet startat.
- **Insyn:** förvärv och avyttring av aktier i SEK för mer än 100 000 kr, utom transaktioner kopplade till
  aktieprogram och reviderade poster. Bolag där minst två insynspersoner köpt, respektive sålt, markeras.
  Om ett bolag är målbolag för ett uppköpsbud eller en avnotering, med `bid_target` i veckofilen, visas dess
  försäljningar som "under pågående uppköpsbud". Det kan till exempel vara anslutning till budet och inget fristående
  säljbeslut.

Valideringen kontrollerar att siffrorna hänger ihop: förändring = slut − start, netto = köp − sälj, värde = volym × pris,
tröskeln och markeringarna.

### Rapportkalender (`scripts/report_calendar.py`)

`report_calendar.py <vecka>` skriver `data/calendar/<vecka>.json` (git-ignorerad). Filen innehåller rapportdatum och
stämmor för bolagen i `instruments.csv`. Veckans sida visar den under fliken **Kommande vecka**, med veckan efter
sidans vecka. Veckokörningen skapar den efter sammanfattningen.

- **Yahoo är grunden** (yfinance `Ticker.calendar`, ett anrop per bolag). Datumet kan vara uppskattat tills bolaget
  har meddelat det.
- **MFN och Cision:** inbjudningar till rapportpresentationer och kallelser till stämmor från de senaste sex veckornas
  rådata. Datumet tas ur texten:
  - bara datum efter publiceringsdagen räknas
  - datum i en rapportperiod ("1 januari – 30 september") räknas inte
  - ett datum i rubriken går först, därefter ett datum i samma mening som "rapport"/"publicerar"
  - kapitalmarknadsdagar räknas inte som rapporter
- **Status:**
  - *bekräftat* när Yahoo och MFN anger samma datum
  - *olika datum* när de skiljer sig; båda visas
  - *enligt Yahoo* respektive *enligt MFN* när bara en källa har datumet
  - stämmor kommer bara från MFN
- Valideringen kontrollerar att datumen ligger i veckan, att statusen stämmer med datumen, att bolagen finns i listan
  och att MFN-länkarna finns i rådatan.
- Kalendern säkerhetskopieras inte till det privata repot. Den gäller framåt och skapas om varje gång.

### Konsensus (`scripts/consensus.py`)

`consensus.py` sparar Yahoos konsensus för alla bolag i listan i `data/consensus/<datum>.json` (git-ignorerad,
säkerhetskopieras med `--backup`). Filen innehåller:
- vinst per aktie och omsättning för innevarande kvartal: snitt, lägsta, högsta, antal analytiker, samma kvartal förra
  året och valuta
- nästa rapportdatum, senaste kvartal och redovisningsvaluta

Morgonkörningen sparar en ny fil varje handelsdag. Bolag vars siffror redan var kända när bilden togs märks med
`after_report`, och visas som "konsensus efter rapport" och används aldrig. Det gäller rapporter samma dag före bilden,
och preliminära siffror de senaste 45 dagarna som ännu inte följts av rapporten. En befintlig bild märks om utan ny
hämtning med `consensus.py --markera <datum>`. Det behövs eftersom Yahoos prognos för innevarande kvartal byter
till nästa kvartal när bolaget rapporterat, så förväntan måste sparas före rapporten. "Kommande vecka" visar den senaste
filen med varningar:
- vid färre än 3 analytiker för vinst per aktie
- när bolaget redovisar i annan valuta än kronor
- för fastighetsbolag, där vinst per aktie styrs av värdeförändringar

Sektorernas nyckeltal att bevaka finns under `kpis` i `data/sectors.json` och läses med
`validate.load_sector_kpis()`.

### Rapportutfall (`/rapportutfall`, `scripts/results.py`)

Veckokörningen tar fram nyckeltal ur veckans rapporter med `scripts/run_results.ps1`. Veckans sida visar dem under
fliken **Rapporterade bolag**.

1. `report_list.py <vecka>` listar veckans rapporter för bolag i listan, en per bolag. Den läser både Actions- och
   morgonrådatan. Ett meddelande räknas som rapport om något av följande gäller:
   - Rubriken säger det: "delårsrapport", "Q3-rapport", "resultat för tredje kvartalet" och liknande.
     Engelska rubriker räknas också: "interim report", "quarterly report", "half-year report" och "nine-month
     report". Arctic Papers "consolidated quarterly report for Q3 2026" missades före 2026-11-05.
   - Kalenderregeln uppfylls, för rubriker som bara är en rubrikmening ("Saabs resultat Kv3 2026: …"). Bolaget står i
     kalendern samma dag, enligt dagens morgonrapport eller veckans kalender. Meddelandet kommer från bolaget självt
     (MFN- eller Cision-avsändaren, aldrig en analytiker). Texten nämner minst två rapportnyckeltal och
     rapportperioden.

   En post med rapportrubrik går före en kalenderträff samma dag. Inbjudningar, datumbesked ("…publicerar rapport den
   21 oktober"), preliminära siffror och resultatvarningar räknas aldrig. Rapporterna delas in i omgångar om 20, med
   sektorns nyckeltal plus nettoomsättning och resultat per aktie.
2. `/rapportutfall <vecka> <omgång>` körs med Claude Code (`--model sonnet`) i läget `dontAsk`. Det får bara läsa,
   skriva i `data/results/` och köra `report_list.py` och `validate.py`. Resultatet hamnar i
   `data/results/<vecka>/omgang-<N>.json`. En godkänd omgång körs inte om. Omgången valideras per rapport: underkänns
   enskilda rapporter körs bara de om en gång direkt (`/rapportutfall <läge> <N> endast <tickrar>`), och är filen
   oanvändbar körs hela omgången om. Det som fortfarande är underkänt tas inte med men listas med orsaken under
   "Underkända av valideringen" (`rejected` i resultatfilen); omgångens godkända rapporter tas med ändå.
   Saknar morgonens resultatfil fortfarande en omgång eller rapport, görs steget om vid nästa utlösare.
3. Valideringen (`scripts/validate_results.py`) kontrollerar:
   - att citatet finns ordagrant i rapporten och har högst 25 ord
   - att årets och föregående års tal, och en eventuell procentsats, står i citatet
   - att procentsatsen stämmer med värdena
   - att enheten passar nyckeltalet
   - att perioden stämmer: närmaste periodangivelse före värdet, även inne i citatet, avgör om det är kvartal,
     året hittills eller helår
**Morgonkörningen** gör samma sak varje handelsdag för rapporter sedan förra morgonen (läge `<datum>`):
- tidsfönstret sparas i `data/results/morning/<datum>/window.json`
- rapporterna läses ur både `data/raw/` och `data/morning/raw/`
- utfallen visas överst i morgonrapporten
- om Claude-steget fallerar byggs morgonrapporten ändå

Måndagens körning (läge `<vecka>`) tar bara de bolag som morgnarna missat och slår sedan ihop allt i veckans fil.

4. `results.py <vecka|datum>` slår ihop omgångarna och räknar förändringen mot föregående år. Den jämför resultat per aktie
   och omsättning för kvartalet med Yahoos konsensus från dagen före rapporten (aldrig samma dag eller efter), om valutan stämmer och prognosen
   gäller samma rapport. Inom ±2 % räknas som "i linje". Har konsensus varningen "stor förändring, få analytiker"
   (över ±50 % mot fjol från färre än 3 analytiker) blir bedömningen "osäker konsensus" i stället för över/under.
   Procent mot förväntan visas inte heller i följande fall, i den här ordningen:
   - **Bolagshändelse:** bolaget har en känd bolagshändelse under året (`investigated` eller en justerad händelse
     under `actions` i `data/corporate_actions.json`). Sidan visar "bolagshändelse påverkar jämförelsen".
   - **Teckenbyte eller nära noll:** utfall och förväntan har olika tecken, eller det ena värdet är mindre än en
     tiondel av det andra. Båda värdena visas.
   - **Kontrollera:** avvikelsen är över ±50 % utan någon av förklaringarna. Båda värdena visas.

   Per aktie i eurocent eller öre räknas om till euro eller kronor före jämförelsen.

   Anger rapporten ett justerat resultat per aktie för kvartalet ("justerat", "underliggande", "exklusive
   jämförelsestörande poster" o.d.) tas det fram som ett eget nyckeltal, med ett citat som innehåller ordet. Det är
   det som jämförs med förväntan, eftersom Yahoos förväntan oftast gäller justerat resultat. Annars jämförs det
   redovisade. Sidan visar vilket som användes ("Justerat resultat per aktie mot förväntan"). Förändringen mot
   fjolåret för resultat per aktie gäller alltid det redovisade.
   Filen säkerhetskopieras med `backup_week.py`.

Provkörning W42 (11 rapporter, en omgång): 1,4 minuter, motsvarande 0,29 USD till API-pris med Sonnet.

### Kurs mot vinst (`scripts/valuation.py`)

`valuation.py <vecka>` skriver `data/valuation/<vecka>.json`. Veckans sida visar den under fliken **Kurs mot vinst**,
från årets början till veckans slut:
- kurs i år, och mot OMX Stockholm PI
- vinst per aktie rullande 12 månader vid årets början och nu
- P/E vid båda tidpunkterna och förändringen
- en tolkning: P/E upp mer än 10 % = "kursen före vinsten", ned mer än 10 % = "vinsten före kursen", annars "i takt"

Underlaget:
- **Vinsten** är summan av de fyra senaste rapporterna i Yahoos `earnings_dates`. De måste hänga ihop, med 45–135
  dagar mellan, och den senaste får vara högst 135 dagar gammal. Det kan vara justerad vinst, men samma definition
  används vid båda tidpunkterna.
- **P/E visas inte för:** fastighetsbolag, investmentbolag (kolumnen `typ` i `instruments.csv`), bolag med förlust,
  bolag som redovisar i annan valuta och bolag med för kort vinsthistorik. P/E över 100 visas som "över 100".

**Bolagshändelser:** en enskild dag i år med kursrörelse över ±40 % kräver en verifierad post i
`data/corporate_actions.json`. En post förklarar rörelsen bara om den ligger exakt på bolagets ex-dag; då justeras
kurserna före med faktorn. Tills dess visas varken kurs i år eller P/E, bara "kontrollera bolagshändelse".

En post med faktor 1,0 betyder att rörelsen är verklig och verifierad, till exempel efter en rapport. Den justeras inte
men flaggas inte heller.

**Verifierade** (`actions`):
- **Cyncly:** extrautdelning 13,50 kr per aktie, första dag utan rätt 14 september. Justeras.
- **Nelly:** Q1-rapporten 28 april. Verklig rörelse.
- **Net Insight:** vinstvarning 6 juli kl. 19:10. Verklig rörelse.

**Kända men inte justerbara** (`investigated`). En post med `label` och `yahoo_dates` visar etiketten i stället för
"kontrollera bolagshändelse", men varken kurs i år eller P/E:
- **Electrolux:** företrädesemission, ex-dag 28 maj, men Yahoos fall ligger 19 maj.
- **Volati:** avknoppning av Salix, ex-dag 10 juni, men Yahoos fall ligger 8 juni.
- **Intrum:** företrädesemission. Fallet ligger 1 juni, före stämmans beslut.
- **Morrow Bank:** omnotering från Oslo 9 januari. Det finns inga handelskurser i Stockholm före den dagen.

**Noterade under året** (`listings`, med verifierad första handelsdag) visas som "noterad under året (dag)" i
stället för "kurs saknas". Det gäller Linjemontage (25 sep), Tången Industrikapital (12 juni), Silex (7 maj), Public Property Invest (20 maj), Octave
(25 maj), Stockholm Nordtech (10 juni) och Salix (15 juni). Sampo räknas inte dit, eftersom bolaget handlats som
depåbevis sedan 2022. Yahoos serie för A-aktien börjar först 31 mars, så det står "kurs saknas".

**Nya försök mot Yahoo:** alla hämtningar från Yahoo görs upp till tre gånger med 2 sekunders paus, via
`scripts/retry.py`. Det gäller vinsthistorik, konsensus, rapportdatum och kurser. Varje misslyckat försök loggas på
stderr, till exempel `[försök 1/3] VICO.ST (vinsthistorik): KeyError – nytt försök om 2 s`. Batchhämtningen av kurser
hämtar om de tickers som saknas i svaret.

Veckokörningen kör `valuation.py` före `signals.py`, eftersom signalen "P/E +30 % i år" bygger på den.

### Signaler (`scripts/signals.py`)

`signals.py <vecka>` skriver `data/signals/<vecka>.json` (git-ignorerad) för alla bolag i `instruments.csv`.
Veckans sida visar den under fliken **Signaler**, en sorterbar tabell där filtret "bara bolag med signal denna vecka"
är förvalt. Kursdatan hämtas i ett enda batchanrop till Yahoo (yfinance) för hela listan och jämförelseindexet. Ton
kommer ur veckofilen och insyn och blankning ur analysfilen. Veckokörningen skapar filen efter sammanfattningen och
säkerhetskopierar den med `backup_week.py`. Om du kör `/sammanfatta-veckan` manuellt kör du `signals.py` efteråt.

| Signal | Villkor |
|---|---|
| Insyn | insynstransaktioner över 100 000 kr under veckan (från `registers.py`) |
| Blankning | ändrad position över 0,5 %, eller sammanlagd blankning ändrad minst 0,5 procentenheter |
| Avviker mot index | nyhetens ton och kursen mot index går åt olika håll (samma regel som på aktiekorten) |
| Stor rörelse mot index | veckans kurs skiljer sig mer än ±5 % från index (bara aktier i kronor) |
| P/E +30 % i år | P/E har stigit minst 30 % sedan årets början (ur Kurs mot vinst) |
| Hög omsättning | veckans snittomsättning minst 2 gånger snittet för de 20 handelsdagarna före |
| Nära 52 v högsta/lägsta | fredagens stängning högst 2 % från 52-veckorshögsta respektive -lägsta |

Hög omsättning räknas på veckans snitt, inte på största dagen. I W42 gav största dagen 74 av 266 bolag och
veckosnittet 20. Trösklarna sparas i filen, och valideringen räknar om signalerna från värdena. Jämförelsen mot index
görs bara för `.ST`-tickers, som antas vara i kronor.

### Morgonrapport (`scripts/morning.py`)

`morning.py` skriver `data/morning/<datum>.json` (git-ignorerad) varje handelsdag, utan Claude. Startsidan visar den
senaste morgonrapporten överst, med veckan och flikarna under. Om den inte är dagens rapport, till exempel på helgen
eller före 08:10, står det så under rubriken med datum. Varje dags rapport finns också på `/morgon/` och
`/morgon/<datum>/`. Rapporten innehåller:

- **Rapporter i dag** ur rapportkalendern. Kalendern för innevarande vecka skrivs om varje morgon.
- **Pressmeddelanden** för bolag i listan sedan förra handelsdagen kl. 17:30, utan rutinmeddelanden. Bolag som
  rapporterar i dag står först.
- **Insyn** publicerad sedan förra handelsdagen, med samma filter som veckoanalysen.
- **Blankning:** ändrade positioner över 0,5 % och ändringar i sammanlagd blankning på minst 0,2 procentenheter, mellan
  registrets ögonblicksbilder.
- **Kurser** (dagsdatans datum): bolag med minst 2 gånger snittomsättningen eller högst 2 % från
  52-veckorshögsta/-lägsta.

**Kursdatumet kommer alltid ur datan** (`signals.data_day`). Datumet är den senaste dagen som minst 90 % av bolagen
har en kursrad för, inte det senaste datumet hos något bolag. Det gäller både morgonrapportens kursdel och fliken
Swing, som räknar alla bolag på den dagen.
- Bakgrund: Yahoo har ofta inte gårdagens rader för svenska aktier kl. 08:10, bara för ett fåtal bolag och index.
  Tidigare togs datumet från de bolagen. Kursdelen visade då gårdagens datum men bara 1–2 bolag med kurssignal, och
  Swing visade dagen före under gårdagens datum.
- Felet fanns alla vardagar utom måndagar från 20/10 till 5/11 2026 (på måndagar fanns fredagens kurser). Kursdelen i
  de morgonrapporterna och swingfilerna 30/10, 3/11 och 5/11 byggdes om 2026-11-05 med rader före respektive dag.
- Filerna har `expected_day` (förra handelsdagen) och `coverage`: antal bolag med kursrad för datans dag respektive
  förra handelsdagen.
- Saknas gårdagens kurser hämtar `run_morning.ps1` bara kurserna igen (`morning.py --bara-kurser`) var tionde minut
  till 09:10, och bygger sidan efter varje försök. Sidan visar under tiden datans datum och en varning ("Kurserna
  gäller tisdag 3 nov, inte förra handelsdagen …").
- Kursreaktionen räknar varje bolags kurs på reaktionsdagen och flaggar bolag som saknas. Den har inte haft felet.
  Veckans signalöversikt byggs på måndagar, när fredagens kurser finns.

MFN, Cision, Riksbanken och FI hämtas lokalt till `data/morning/raw/`, med en hämtning per källa och körning. De
hämtas aldrig till `data/raw/`, som är klonen av det privata repot. GitHub Actions schemalagda jobb kan komma timmar
för sent: FI-jobbet "06:37" har committats 10:45–11:15, så morgonrapporten kan inte vänta på dem. Avsändaren av ett
pressmeddelande avgörs av MFN:s eller Cisions bolagsslug, inte av namn i rubriken. Annars skulle till exempel
"Catena Media" räknas som Catena.

### Swing (`scripts/swing.py`, `scripts/swing_history.py`)

Fliken **Swing** är ett informationsverktyg. Den uppdateras varje morgon av `morning.py` med morgonrapportens
dagsdata, utan extra hämtning, och skrivs till `data/swing/<datum>.json`, som är git-ignorerad.

**Lägesmått per bolag**, vid förra handelsdagens stängning:
- kurs mot 20- och 50-dagars glidande medelvärde
- RSI(2) och RSI(14)
- ATR(14) i procent av kursen
- omsättning mot snittet för de 20 dagarna före
- handelsdagar sedan senaste rapporten, med utfall mot förväntan
- sammanlagd blankning enligt Finansinspektionen, där den finns

**Etiketterna** beskriver vad som hänt, och valideringen underkänner ord som uppmanar till köp eller försäljning:
- utbrott över 20-dagarshögsta, med eller utan hög omsättning (≥ 2×)
- RSI(2) under 10 eller över 90
- trend upp eller ned (kurs, 20- och 50-dagars MA i ordning)
- vid övre Bollingerband: gårdagens stängning vid eller över snittet av de 20 senaste stängningarna plus
  2 standardavvikelser (`bb_upper_pct`)
- golden cross / death cross: 50 dagars MA har korsat upp över (ned under) 200 dagars MA någon av de 10 senaste
  handelsdagarna, med antal dagar sedan korsningen (`ma_cross`, `ma_cross_days`). I historiken 24 okt 2025 – 27 okt
  2026 gick bolag med golden cross bättre än index efter 5 dagar i 45,9 % av fallen (± 2,2, n = 1 926) och med death
  cross i 49,3 % (± 2,3, n = 1 805), mot 47,1 % för alla dagar.
- efter rapport, över eller under förväntan, inom fem handelsdagar

Tabellen kan sorteras och filtreras per etikett och har fast rubrikrad.

**Historiken** under tabellen kommer från `scripts/swing_history.py` (`data/swing/history.json`). Den räknas med samma
mått och etiketter på det senaste årets dagsdata, ett batchanrop till Yahoo, och visar hur ofta aktien gått bättre än
index efter 2 och 5 handelsdagar, med antal fall och jämfört med alla aktie-dagar.

Testet 20 okt 2025 – 21 okt 2026 gav ingen etikett som klart slår alla dagar: utbrott med hög omsättning 49,3 % mot
47,3 % efter 5 dagar, ± 3,6 procentenheter. Rapportetiketterna har för få fall, eftersom utfall mot förväntan bara
finns sedan oktober 2026. Historiken räknas om i måndagens veckokörning (`run_weekly.ps1`), så rapportetiketterna
får fler fall efter hand.

#### Stöd och motstånd (`scripts/levels.py`)

Fliken visar för alla bolag närmaste stöd under och närmaste motstånd över gårdagens stängning. Kurserna är justerade
som övriga mått.
1. **Vändpunkter:** de senaste 120 handelsdagarna. En botten är en dag vars low är lägst bland de 3 dagarna före och
   de 3 efter; en topp är en dag vars high är högst bland samma dagar. De 3 sista dagarna kan inte bli vändpunkter.
2. **Zoner:** alla vändpunkter, bottnar och toppar, sorteras efter pris. En zon börjar vid den lägsta, och nästa
   punkt tas med så länge den ligger högst 1 ATR(14) över zonens nedre kant. Antalet vändningar är antalet punkter i
   zonen.
3. **Stöd och motstånd:**
   - En zon under stängningen är stöd och en zon över är motstånd. Ligger stängningen i zonen avgör zonens mitt.
     En bruten topp blir alltså stöd och en bruten botten motstånd.
   - 20 EMA, 50 MA och 200 MA räknas som stöd under kursen och motstånd över.
4. **Per bolag:** närmaste stöd (avstånd till dess övre kant) och motstånd (till dess nedre kant), i procent och i
   ATR.
5. **I strategikolumnerna för Pullback i trend:**
   - Stopp under stöd: stödets nedre kant minus 0,1 ATR.
   - 1 R: triggernivån (gårdagens high) minus det stoppet.
   - Motstånd i R: avståndet från triggernivån till motståndets nedre kant, delat med R.

Valideringen kontrollerar att stödets mitt ligger under motståndets, att stoppet ligger under stödet och att
motståndet i R stämmer. Kanterna kan överlappa när stängningen ligger i en zon.

**Test i historiken:** Pullback-fall med en stödzon högst 1 ATR under stängningen och närmaste motstånd minst 2R över
referensen, mot övriga fall. Strategins egen R och 2R-nivå används. Stödet räknas bara med zoner, eftersom rekylen
till 20 EMA eller 50 MA annars alltid ger ett stöd nära. Resultatet 27 okt 2025 – 28 okt 2026, utan courtage, omräknat
2026-11-05 efter rättningen av medelvärden inne i en zon (se fliken Swing, "Stöd och motstånd"):

| | Avgjorda fall | 2R före stoppet | Snitt per fall |
|---|---|---|---|
| Stöd nära och motstånd minst 2R bort | 237 | 31,2 % (± 5,9) | +0,13 R |
| Övriga | 1 463 | 33,9 % (± 2,4) | +0,01 R |

Kombinationen nådde alltså inte 2R oftare, och skillnaden ligger väl inom osäkerheten. Snittet per fall är högre,
men på 237 fall.

Före rättningen (24 okt 2025 – 27 okt 2026) var siffrorna 30,9 % (230 fall, +0,04 R) mot 33,4 % (1 457 fall, −0,00 R).
Då kunde ett medelvärde inne i en zon räknas som egen nivå, så att stödet hamnade över motståndet (Beijer Alma
2026-11-05: stöd 303,21 och motstånd 302,49). Valideringen underkänner nu ett stöd vars övre kant ligger över
motståndets nedre kant.

#### Bollinger under dagen (`scripts/bollinger.py`, `scripts/run_bollinger.ps1`)

Uppgiften **VeckansFinansBollinger** kör vardagar 09:20 och 13:20, utan Claude.
- Varje kontroll gör ett batchanrop till Yahoo för alla bolag i listan. Kurserna är fördröjda ca 15 minuter, och
  det står i notisen och på sidan.
- Övre bandet: snittet av de 20 senaste stängningskurserna plus 2 standardavvikelser (populationens), med den
  aktuella kursen som den tjugonde.
- Windows-notisen listar bolagen vid eller över bandet och, separat, de som ligger högst 1 % under. Ett eget
  avsnitt listar pullback-kandidaterna vid eller över övre bandet med avståndet i R ("3,4 R över", "vid bandet").
  Ett klick öppnar sidan.
- **Pullback-kandidaterna** är bolagen med en etikett ur strategin Pullback i trend i dagens swingfil. För dem räknas
  båda banden med dagens kurs. Resultatet blir kolumnen "Bollinger" i fliken Swing när strategin är vald, med
  kontrollens tid. Kolumnen visar:
  - %B, kursens läge mellan banden: (kurs − undre) / (övre − undre), där 0 är undre bandet och 1 övre
  - övre bandets nivå
  - avståndet till övre bandet i R: (övre − kurs) / R, med R = referens − stopp ur swingfilen
  - ⚠ när kursen är vid eller över övre bandet

  Före dagens första kontroll visar kolumnen gårdagens stängning, ur swingfilen (`pullback.bollinger`).
- Senaste kontrollen visas överst på startsidan, med tid (`data/bollinger/<datum>-<hhmm>.json`, git-ignorerad).
- Inget körs på börsens stängda dagar eller utanför 09:00–17:30. En missad kontroll tas inte igen senare.
- Logg i `logs/bollinger/`.

Etiketten "Vid övre Bollingerband" ovan räknas på samma sätt men på gårdagens stängning. I historiken gick den
bättre än index efter 5 dagar i 48,3 % av fallen, mot 47,1 % för alla dagar.

#### Strategin "Pullback i trend" (`scripts/pullback.py`)

Strategin är ett val under "Strategi" i flikens rullista. Den visar bolagen med någon av strategins två etiketter och
strategins kolumner. Mått som strategin inte använder (RSI, omsättning, blankning) döljs då, så att tabellen ryms.
Reglerna gäller förra handelsdagen, med kurser justerade som övriga mått. Öppningskursen hämtas i samma batchanrop som
resten av dagsdatan.

1. **Trend:** stängningen över 50 och 200 dagars MA, och 50 dagars MA över 200 dagars MA. Kräver 200 handelsdagar.
2. **Rekyl i trend:** dagens low högst 1 % från 20 dagars EMA eller 50 dagars MA, och stängningen över samma nivå.
   Når rekylen båda nivåerna används den lägre.
3. **Rekyl med vändsignal:** dessutom en hammer eller bullish engulfing. Triggernivån är dagens high.
   - Hammer: nedre skuggan minst 2 × kroppen, och övre skuggan högst 25 % av dagens spann.
   - Bullish engulfing: förra dagen röd och dagen grön, och dagens kropp omsluter förra dagens.
4. **Nivåer per bolag:**
   - Referens: triggernivån vid vändsignal, annars stängningen.
   - Stopp: lägsta low sedan den högsta toppen de senaste 10 dagarna, eller MA-nivån om den ligger lägre.
   - Avstånd till stoppet (1 R) i procent och i ATR(14).
   - Föregående topp: högsta high på 60 dagar.
   - 2R och 3R: referensen plus två respektive tre R.
5. **Historik** (`history.json` → `pullback`): varje dag med etiketten det senaste året, ett fall i taget per bolag.
   - Start: nästa dags öppning. Vid vändsignal i stället när triggernivån nås inom 3 dagar, annars räknas fallet som
     ej utlöst.
   - Avgjort när 2R eller stoppet nås. Nås båda samma dag räknas stoppet först.
   - Utan avgörande efter 60 dagar räknas fallet som öppet.
   - Courtage, spread och glidning ingår inte.
   - Samma fall räknas också med marknadsfilter: bara när OMX Stockholm PI ligger över sitt 50- och 200-dagars MA.

Etiketterna och strategins namn valideras som övriga etiketter, utan uppmanande ord. Nivåerna kontrolleras inbördes:
stoppet ligger under referensen och högst på MA-nivån, 2R och 3R stämmer, och triggernivån är dagens high.

Testet 24 okt 2025 – 27 okt 2026, utan courtage. Med 2R måste mer än 33,3 % av fallen nå 2R först för att gå jämnt ut.

| | Avgjorda fall | 2R före stoppet | Snitt per fall |
|---|---|---|---|
| Rekyl i trend | 1 497 | 32,7 % (± 2,4) | −0,03 R |
| Rekyl med vändsignal | 189 | 36,5 % (± 6,9) | +0,17 R |
| Båda | 1 686 | 33,1 % | +0,00 R |
| Båda, med marknadsfilter | 1 383 | 31,3 % | −0,05 R |
| Rekyl med vändsignal, med marknadsfilter | 157 | 33,8 % (± 7,4) | +0,09 R |

Marknadsfiltret gjorde utfallet sämre. Indexet låg över båda medelvärdena 187 av 252 dagar, så filtret tog bara bort
ungefär en fjärdedel av fallen, och de fallen gick bättre än genomsnittet. Vändsignalen ligger över jämviktsnivån, men
osäkerheten är för stor för att skilja den från rekyler utan vändsignal. Fallen är dessutom inte oberoende, eftersom
många infaller samma dagar.

## Sidan (Astro)

Kräver Node 22 eller senare.

```bash
npm install
npm run dev:mock      # utveckling mot data/mock/ – http://localhost:4321/veckans-finans/
npm run dev           # utveckling mot riktiga data/weeks/
npm run build         # statisk export till dist/ (läser bara data/weeks/ och data/prices/)
npm run preview       # visa dist/ lokalt
```

- `--mode mock` (i `dev:mock`/`build:mock`) är det enda sättet att läsa `data/mock/`. Det vanliga bygget kastar fel
  om en fil i `data/weeks/` är markerad som mock.
- Sidan läser aldrig `evidence` eller `data/raw/`.
- Kursrader visas bara om `data/prices/<vecka>.json` finns. Saknas en ticker i filen står det "Kurs saknas".
- Bolag som inte finns i `instruments.csv` (`"match": "ej i listan"`) listas under "Ej i bevakningslistan" med namn
  och motivering, men utan aktiekort. De kan ändå vara noterade, till exempel på First North.
- Utan JavaScript visas alla sektorer under varandra. Med JavaScript blir de flikar, och `#sektor-<id>` i adressen
  väljer flik.

## Läsa veckan lokalt

### Automatiskt (schemalagda uppgifter)

Skapas med `powershell -NoProfile -File scripts\install_tasks.ps1`:

- **VeckansFinansServer** startar `scripts/serve.ps1` dolt vid inloggning. Sidan finns på
  http://localhost:4321/veckans-finans/. Utskrifterna hamnar i `logs/server.log`. Om Node kraschar startar
  `serve.ps1` om servern efter en minut, högst tre gånger i följd. Omstarten ligger i skriptet eftersom
  `conhost --headless` alltid ger Schemaläggaren felkod 0.
- **VeckansFinansVeckosammanfattning** kör `scripts/run_weekly.ps1` måndagar 08:30 lokal tid och väcker datorn om
  den är ansluten till ström. 08:30 ligger efter att Claudes veckokvot återställts kl. 08:00 och efter
  morgonrapporten. Uppgiften startar också fem minuter efter inloggning och upplåsning, så att en missad måndag tas
  igen, till exempel när datorn låg i viloläge till disk. Varje vecka får högst två automatiska försök. Fler försök
  kör du manuellt med `-Week`. Om senaste veckan saknas visar sidan en varning överst.

  Skriptet väntar först, högst 90 minuter, tills dagens morgonhämtning är klar ("morning.py: exit" i dagens
  morgonlogg), vilken utlösare som än startade det. Inloggning och upplåsning startar annars morgonflödet och
  veckokörningen samtidigt. Sedan kompletterar det veckans rådata med morgonflödets lokala hämtning
  (`scripts/merge_morning_raw.py`).
  Actions-hämtningen har haft luckor: vecka 43 saknades 46 av 68 Large Cap-rapporter. Därefter kör det
  `/sammanfatta-veckan` för förra veckan med den fristående Claude Code-CLI:n, med Opus (`-Model`), i läget `dontAsk`
  och med en uttömmande lista över tillåtna verktyg. Rapportutfallen körs med Sonnet.

  Indexöversiktens första sida listar veckans Large Cap-rapporter (`data/large_cap.json`, från Nasdaqs Large
  Cap-lista). Valideringen underkänner en tom sektor där ett Large Cap-bolag har rapporterat.

  **Claudes användningsgräns:** stoppas ett Claude-steg av `usage_limit_reached` räknas det inte som försök. Det
  gäller veckosammanfattningen och rapportutfallen.
  - Återställningstiden loggas, och sidans varning säger "Claudes användningsgräns nådd – försöker igen efter
    <tid>" (`data/status/claude-limit.json`).
  - En engångsuppgift (`VeckansFinansOmforsok…`) kör steget igen fem minuter efter återställningen och tas sedan bort
    automatiskt.
  - `python scripts/health_check.py` visar kvotens status tillsammans med uppgifterna. Därefter bygger det om sidan. Finns veckofilen redan
  avbryts körningen. Loggarna och slutrapporten (`<vecka>-slutrapport.md`) hamnar i `logs/weekly/`.
- **VeckansFinansMorgonrapport** kör `scripts/run_morning.ps1` vardagar 08:10, så att rapporter som släpps
  07:00–08:00 kommer med före börsöppning. Den väcks och tas igen vid inloggning och upplåsning på samma sätt som
  veckouppgiften, men aldrig före 08:10: en inloggning eller upplåsning tidigare på morgonen gör ingenting, så att
  dagens rapportfönster inte låses innan morgonens rapporter släppts. Claude används inte i själva morgonrapporten,
  så den drar ingenting av abonnemanget. Se [Morgonrapport](#morgonrapport-scriptsmorningpy).
  Loggarna hamnar i `logs/morning/`.
- **VeckansFinansEftermiddag** kör `scripts/run_update.ps1 -Scheduled` vardagar 17:45:
  1. Lokal hämtning till `data/morning/raw/`.
  2. Rapportutfall för rapporterna sedan förra körningens fönster, i läget `<datum>T<hhmm>` under
     `data/results/updates/`. Claude körs bara om fönstret innehåller rapporter.
  3. Dagens kursreaktion för de rapporterande bolagen med `scripts/afternoon.py`, i `data/afternoon/<datum>.json`.
  4. Bygget av sidan.

  **Kurser och nätverk som saknas** räknas som fel, loggas med "FEL:" och görs om:
  - Ingen källa svarar i hämtningen (nätverket saknas): uppdateringen avbryts innan rapportutfallen, så att inget
    tidsfönster låses över rapporter som inte hämtats. Morgonrapporten skrivs, men konsensus och rapportutfall väntar.
  - Kurser saknas (dagens för eftermiddagen, förra handelsdagens för morgonrapporten): filen skrivs med felet och
    `retry` (`afternoon.py`/`morning.py` exit 2).
  - Nästa inloggning eller upplåsning försöker igen. Eftermiddagsuppgiften startas också vid inloggning och
    upplåsning efter 17:45 och kör bara om dagens kursreaktion saknas, togs fram före stängning eller är
    ofullständig (`afternoon.py --klar`), högst fyra gånger per dag. Det som fortfarande saknas nästa morgon tar
    morgonflödet (`afternoon.py --forra-om-saknas`), också då högst fyra försök.

  Tabellen "Kursreaktion" har kolumnerna kurs, dagens förändring, "Vinst/aktie mot förväntan", "Omsättning mot
  förväntan", vinst per aktie mot samma kvartal i fjol ("Mot fjol"), "Nyckeltal mot fjol", "Ton i rapporten" och
  "Analytiker".
  - Mot förväntan visas alltid i procent (aldrig "i linje"), med källan och antalet analytiker under ("Yahoo,
    3 analytiker"). Övriga etiketter (kontrollera, osäker konsensus, teckenbyte, nära noll, bolagshändelse) är
    desamma som i rapportutfallen. En text ovanför tabellen säger att Yahoos konsensus kan skilja sig från den
    konsensus marknaden jämför med (Modular Finance, Bloomberg, FactSet).
  - ▼ markerar en svag punkt: vinst per aktie eller omsättning mer än 5 % under förväntan (inte vid
    bolagshändelse), sektorns nyckeltal sämre än fjol (för belåningsgrad och kreditförluster är högre sämre) eller
    negativ ton. Skälen syns när du håller över markeringen. ⚑ (kursen gick mot utfallet) finns kvar.
  - "Ton i rapporten" visar tonen (positiv, blandad, neutral eller negativ, `highlight.tone`) före raden "I
    rapporten". Tonen sätts av `/rapportutfall`; valideringen kräver den från vecka 44 och att raden delar
    innehållsord med citaten, men kan inte avgöra om tonen är rätt bedömd.
  - "Analytiker" länkar till Placeras taggsida och Affärsvärldens bolagssida, ur kolumnerna `placera_tagg` och
    `afv_aktie` i `instruments.csv`. Sidan hämtar ingenting från sajterna (Placeras villkor förbjuder robotar).
    Adresserna kontrollerades en gång innan de lades in: Placeras taggsida finns i Placeras egen tagg-sitemap,
    och Affärsvärldens bolagssida svarar och innehåller bolagets ISIN. Saknas en kontrollerad adress står "–".
    Nya adresser ska kontrolleras på samma sätt, aldrig gissas.
  - "Nyckeltal mot fjol" är sektorns huvudnyckeltal ur rapportutfallen: det första av sektorns egna nyckeltal
    (`data/sectors.json`, t.ex. orderingång för industri) som rapporten anger för kvartalet, utom det som redan står
    under "Mot fjol". Procenttal (marginaler) visas med fjolårsvärdet: "19,9 % (16,9)".
  - Raden "I rapporten" är en hel mening på högst 15 ord om det rapporten själv lyfter fram, inklusive utsikter, som
    `/rapportutfall` skriver (`highlight`). Den har ordagranna citat som valideras som nyheternas (citatet finns i
    rapporten, varje tal belagt exakt, riktningsord stämmer, inga ord om aktien eller råd). Citaten visas inte. Raden
    krävs från och med vecka 44 2026 för rapporter med nyckeltal. Valideringen underkänner en rad som inte börjar
    med stor bokstav, inte slutar med punkt, slutar på ett ord som kräver fortsättning ("och", "inom", "bland
    annat" …) eller lämnar en parentes öppen. Att meningen är grammatiskt fullständig i övrigt kan den inte avgöra.

  "Mot fjol" har följande regler:
  - Bara kvartalsvärden visas. Valideringen kräver en kvartalsangivelse ("juli–september", "tredje kvartalet" o.d.)
    före citatet för värden per aktie.
  - Fastighetsbolag visar förvaltningsresultat per aktie när rapporten anger det.
  - Vid över ±200 %, nära noll eller teckenbyte visas båda värdena i stället för procent, som i "Kommande vecka".
  - Perioden ("Q3 2026 mot Q3 2025") och nyckeltalet syns som tooltip. Förväntan visas med samma regler och texter som i rapportutfallen, och visar om det
  justerade resultatet jämfördes. Utfallen hämtas när sidan byggs, ur körningens rapportutfall och annars ur veckans
  fil. Indexets utveckling står i texten ovanför. Rader där utfallet var över förväntan men kursen föll, eller under
  förväntan men kursen steg, markeras med ⚑. Tabellen visas som "Kursreaktion i dag" i eftermiddagen. Morgonrapporten
  visar förra handelsdagens tabell med stängningskurser. Missades eftermiddagen, eller saknade den kurser, tar
  morgonflödet fram den med `afternoon.py --forra-om-saknas`.

  Uppdateringen visas som **Eftermiddag** överst på startsidan, ovanför morgonrapporten, tills nästa morgonrapport
  finns. Varje fönster börjar där det senast slutande fönstret slutade, morgon eller uppdatering, så att ingen rapport
  räknas två gånger. Ett bolag som redan fått ett utfall i en annan körning samma eller förra handelsdagen hoppas
  också över, eftersom bolag ibland skickar ut samma rapport igen. Uppgiften väcker datorn och tas igen samma kväll.
  Före 17:45 och när börsen är stängd gör den ingenting. Den har ingen inloggningsutlösare, eftersom morgonflödet tar
  upp det som missats. Loggarna hamnar i `logs/updates/`.
- Skrivbordsgenvägen **Uppdatera nu** kör samma flöde direkt, i ett synligt fönster, och öppnar sidan när den är
  klar. Den kan köras när som helst och hur ofta som helst, och varje körning får ett eget fönster. Ett lås i
  `run_results.ps1` gör att två körningar aldrig tar fram rapportutfall samtidigt.
- Skrivbordsgenvägen **Veckans finans** kör `scripts/open_site.ps1`. Den startar servern om den inte svarar och
  öppnar sidan.

### Manuellt

Arbetsgång varje vecka, efter att veckan är slut. Kör kommandona i repots rot.

1. Hämta rådatan: `git -C data/raw pull`. `/sammanfatta-veckan` gör det också själv.
2. Kör `/sammanfatta-veckan` i Claude Code. Kommandot gör följande:
   - skriver och validerar `data/weeks/<vecka>.json`
   - hämtar kurser till `data/prices/<vecka>.json`
   - räknar fram blankning och insyn till `data/analysis/<vecka>.json` med `registers.py`
   - **säkerhetskopierar** de tre filerna till det privata repot, under `weeks/`, `prices/` och `analysis/` i
     `veckans-finans-raw`, med pull, commit och push
3. Om du vill hämta kurserna igen senare:
   ```bash
   .venv/Scripts/python scripts/prices.py 2026-W42
   ```
   Kopiera då också den nya filen till `data/raw/prices/` och committa och pusha den där.
4. Starta sidan:
   ```bash
   npm run dev
   ```
   Öppna http://localhost:4321/veckans-finans/. Startsidan visar senaste veckan, och äldre veckor finns i
   veckoväljaren eller på `http://localhost:4321/veckans-finans/vecka/<vecka>/`. Stoppa med Ctrl+C.

   Alternativt en byggd version som inte laddar om vid ändringar:
   ```bash
   npm run build
   ```
   ```bash
   npm run preview
   ```
   Den finns på samma adress.

Ingenting committas i det publika repot. `data/weeks/` och `data/prices/` är git-ignorerade, så veckorna kan inte
hamna där av misstag. Säkerhetskopian ligger i det privata repot. För att återställa på en ny dator: klona
`veckans-finans-raw` till `data/raw/` och kopiera `data/raw/weeks/*` till `data/weeks/` och `data/raw/prices/*` till
`data/prices/`.

GitHub Pages är avstängt. [`.github/workflows/pages.yml`](.github/workflows/pages.yml) ligger kvar men är
inaktiverad på github.com. Med git-ignorerade veckor skulle den ändå bara bygga en tom sida.

## Validering

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python scripts/validate.py
.venv/Scripts/python -m pytest -q
```

`validate.py` underkänner bland annat tickers som saknas i `instruments.csv`, bolag som markerats "ej i listan" trots att de finns där,
datum utanför veckan, siffror utan citat och citat som inte står i källtexten.
