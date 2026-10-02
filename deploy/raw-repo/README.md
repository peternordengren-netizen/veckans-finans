# veckans-finans-raw (privat)

Rådata till [Veckans finans](https://github.com/peternordengren-netizen/veckans-finans).
Ska vara **privat**: här ligger fulltext från MFN, utdrag från Cision och Riksbankens styrränta,
och det får inte publiceras.

Filerna fylls i automatiskt av `.github/workflows/fetch.yml`, som kör `scripts/fetch.py` från det publika repot var
fjärde timme. Ändra inget för hand.

```
<vecka>/index.json            metadata per post och logg över körningar
<vecka>/mfn/<id>.txt          fulltext
<vecka>/cision/<id>.txt       utdrag
<vecka>/riksbanken/<id>.txt   styrräntan
```

Lokalt klonas repot in som `data/raw/` i det publika repot. Den mappen är git-ignorerad där.
