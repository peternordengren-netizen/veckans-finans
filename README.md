# Veckans finans

Statisk sida som sammanfattar veckans finansnyheter per sektor och kopplar dem till noterade aktier.
Inga betaltjänster eller betal-API:er. **Inget investeringsråd.**

## Dataflöde

| Steg | Skriver | Committas |
|---|---|---|
| `scripts/fetch.py` (RSS: MFN, Cision; API: Riksbankens styrränta) | `data/raw/<vecka>/` | nej, rådatan ligger i det privata repot `veckans-finans-raw` |
| `/sammanfatta-veckan` (Claude Code, manuellt en gång i veckan) | `data/weeks/<vecka>.json` | ja |
| `scripts/prices.py` (yfinance, valfritt, körs lokalt) | `data/prices/<vecka>.json` | ja |
| `scripts/validate.py` | – | – |

Veckor anges som ISO-vecka, t.ex. `2026-W40` (mån–sön, Europe/Stockholm).

### Hämtning (`scripts/fetch.py`)

Körs var fjärde timme i GitHub Actions i det privata repot `veckans-finans-raw`.
Workflow-mallen ligger i [`deploy/raw-repo/`](deploy/raw-repo/). Lokalt klonas det privata repot in som `data/raw/`.

| Källa | Adress (kontrollerad 2026-10-02) | Text |
|---|---|---|
| MFN | `https://mfn.se/all/s/nordic.rss?limit=200` (odokumenterad, men flödet anger själv adressen) | fulltext |
| Cision | `https://news.cision.com/se/ListItems?format=rss` | utdrag, ca 600 tecken |
| Riksbanken | `https://api.riksbank.se/swea/v1/Observations/SECBREPOEFF/<från>/<till>`, fem år bakåt i ett anrop (ingen nyckel krävs) | nyckeltal |

- Varje körning gör ett anrop per källa, med en timeout på 30 s och User-Agent `veckans-finans-fetch/0.1 (+repo-URL)`.
- MFN filtreras till `scope SE`, svenska eller engelska. Insynshandel (`sub:ci:insider`) sparas inte.
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
  så avrundade eller omräknade siffror underkänns.
  Valideringen kontrollerar också riktningen. Ett ökningsord (ökade, steg, höjdes, växte, stärktes) får inte stå
  framför ett tal som är negativt i citatet, och ett minskningsord (minskade, sjönk, föll, sänktes, försvagades)
  får inte stå framför ett positivt. Citatets egna riktningsord räknas, så "minskade med 3,2 procent" räknas som negativt.
  Står "till" direkt före talet ("sänktes till 1,75") är det en nivå, och då kontrolleras inte riktningen. "med" kontrolleras.
  **Ordlistan fångar inte allt.** Den matchar på ordstam inom fyra ord före talet i samma mening. Den missar synonymer
  (backade, rasade, lyfte, tappade), riktningsord efter talet ("en ökning på 3 procent" fångas, "3 procents ökning"
  gör det inte) och negationer ("ökade inte"). Undantaget för "till" gör också att en felaktig riktning framför
  en nivå inte fångas. Kontrollen är ett skyddsnät och ersätter inte en egen läsning.
- **Nyckeltal:** `key_figures.policy_rate` innehåller Riksbankens styrränta, alltså senaste observationen i veckan,
  värdet före senaste ändringen och ändringsdatumet, med länk till
  [Riksbankens sida om styrräntan](https://www.riksbank.se/sv/statistik/rantor-och-valutakurser/styrranta-in--och-utlaningsranta/).
  Den visas överst på sidan. Valideringen jämför värdena med rådatans index och kräver att det är veckans senaste
  observation. Makrosammanfattningen får hänvisa till värdena, men styrräntan är aldrig en nyhet.
  Serien har en observation per bankdag, så "föregående värde" betyder värdet före senaste *ändringen*,
  inte gårdagens observation.
- [`schema/prices.schema.json`](schema/prices.schema.json): kursförändring per `yahoo_ticker`. Sidan fungerar utan filen.
- [`data/instruments.csv`](data/instruments.csv): `namn,alias,ticker,yahoo_ticker,börs,sektor,mfn_slug`. Flera alias
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
- Fail-soft: en ticker som inte går att hämta hamnar i `errors`. Om ingen ticker går att hämta skrivs ingen fil,
  och en befintlig fil lämnas orörd. Sidan fungerar i båda fallen.
- `--mock --out <fil>` läser mockveckan. Den kurerade mockkursfilen skrivs aldrig över.

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

## Publicering (GitHub Pages)

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) körs vid varje push till `main`, och kan också startas
manuellt. Den validerar data, kör testerna, bygger med `npm run build` och publicerar `dist/` på
https://peternordengren-netizen.github.io/veckans-finans/. Om valideringen eller testerna fallerar publiceras inget.

Arbetsgång per vecka: `/sammanfatta-veckan`, sedan `scripts/prices.py <vecka>`, sedan commit och push av
`data/weeks/` och `data/prices/`.

## Validering

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python scripts/validate.py
.venv/Scripts/python -m pytest -q
```

`validate.py` underkänner bland annat tickers som saknas i `instruments.csv`, bolag som markerats "ej i listan" trots att de finns där,
datum utanför veckan, siffror utan citat och citat som inte står i källtexten.
