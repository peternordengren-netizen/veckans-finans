# Veckans finans

Statisk sida som sammanfattar veckans finansnyheter per sektor och kopplar dem till noterade aktier.
Inga betaltjänster eller betal-API:er. **Inget investeringsråd.**

## Dataflöde

| Steg | Skriver | Committas |
|---|---|---|
| `scripts/fetch.py` (RSS: MFN, Cision; API: Riksbankens styrränta) | `data/raw/<vecka>/` | nej, rådatan ligger i det privata repot `veckans-finans-raw` |
| `/sammanfatta-veckan` (Claude Code, manuellt en gång i veckan) | `data/weeks/<vecka>.json` | ja |
| `scripts/prices.py` (yfinance, valfritt) | `data/prices/<vecka>.json` | ja |
| `scripts/validate.py` | – | – |

Veckor anges som ISO-vecka, t.ex. `2026-W40` (mån–sön, Europe/Stockholm).

### Hämtning (`scripts/fetch.py`)

Körs var fjärde timme i GitHub Actions i det privata repot `veckans-finans-raw`.
Workflow-mallen ligger i [`deploy/raw-repo/`](deploy/raw-repo/). Lokalt klonas det privata repot in som `data/raw/`.

| Källa | Adress (kontrollerad 2026-10-02) | Text |
|---|---|---|
| MFN | `https://mfn.se/all/s/nordic.rss?limit=200` (odokumenterad, men flödet anger själv adressen) | fulltext |
| Cision | `https://news.cision.com/se/ListItems?format=rss` | utdrag, ca 600 tecken |
| Riksbanken | `https://api.riksbank.se/swea/v1/Observations/Latest/SECBREPOEFF` (ingen nyckel krävs) | en mening |

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
- [`schema/prices.schema.json`](schema/prices.schema.json): kursförändring per `yahoo_ticker`. Sidan fungerar utan filen.
- [`data/instruments.csv`](data/instruments.csv): `namn,alias,ticker,yahoo_ticker,börs,mfn_slug`. Flera alias separeras
  med `|`. Underhålls manuellt. `mfn_slug` är valfri och anger bolagets slug i MFN:s URL:er (`mfn.se/a/<slug>/…`).
  Poster från de bolagen prioriteras när veckan sammanfattas. Fyll bara i slugs du sett i en faktisk MFN-URL.
- [`data/sectors.json`](data/sectors.json): fast sektorlista som styr flikarna.
- `data/mock/`: exempeldata (`"mock": true`) med **påhittade bolag och källor** och en egen
  [`instruments.csv`](data/mock/instruments.csv). Valideringen underkänner mockfiler utanför `data/mock/`.

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
- Onoterade bolag listas med namn och motivering, men utan aktiekort.
- Utan JavaScript visas alla sektorer under varandra. Med JavaScript blir de flikar, och `#sektor-<id>` i adressen
  väljer flik.

## Validering

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python scripts/validate.py
.venv/Scripts/python -m pytest -q
```

`validate.py` underkänner bland annat tickers som saknas i `instruments.csv`, bolag som markerats "onoterat" trots att de finns där,
datum utanför veckan, siffror utan citat och citat som inte står i källtexten.
