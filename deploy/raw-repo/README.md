# veckans-finans-raw (privat)

Rådata till [Veckans finans](https://github.com/peternordengren-netizen/veckans-finans).
Ska vara **privat**: här ligger fulltext från MFN, utdrag från Cision och Riksbankens styrränta,
och det får inte publiceras.

Rådatan fylls i automatiskt av `.github/workflows/fetch.yml`, som kör `scripts/fetch.py` från det publika repot var
fjärde timme. Ändra inget för hand.

```
<vecka>/index.json            metadata per post och logg över körningar
<vecka>/mfn/<id>.txt          fulltext
<vecka>/cision/<id>.txt       utdrag
<vecka>/riksbanken/<id>.txt   styrräntan (nyckeltal)
weeks/<vecka>.json            säkerhetskopia av veckans sammanfattning
prices/<vecka>.json           säkerhetskopia av veckans kurser
```

`weeks/` och `prices/` skrivs av `/sammanfatta-veckan` som sista steg. De finns bara här och lokalt, och aldrig i
det publika repot.

Lokalt klonas repot in som `data/raw/` i det publika repot. Den mappen är git-ignorerad där.
