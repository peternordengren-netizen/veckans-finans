"""Tester för scripts/prices.py mot mockveckan, med en påhittad kurskälla (inga nätanrop)."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import prices  # noqa: E402
import validate  # noqa: E402

MOCK_WEEK = json.loads((ROOT / "data" / "mock" / "weeks" / "2026-W40.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 5, 18, 0, tzinfo=prices.STOCKHOLM)  # måndag efter vecka 40


def hist(*rows, currency="SEK"):
    """rows: (iso-datum, stängning[, justerad stängning])"""
    return prices.History(
        rows=[(date.fromisoformat(r[0]), r[1], r[2] if len(r) > 2 else r[1]) for r in rows],
        currency=currency,
    )


# Fredag 25/9 (före veckan) -> fredag 2/10 (veckans sista handelsdag).
NORMAL = hist(("2026-09-24", 99.0), ("2026-09-25", 100.0), ("2026-09-28", 101.0), ("2026-10-02", 104.8))


def fake_fetch(table, calls=None):
    def fetch(ticker, start, end):
        if calls is not None:
            calls.append((ticker, start, end))
        r = table.get(ticker, KeyError(f"okänd ticker {ticker}"))
        if isinstance(r, Exception):
            raise r
        return r

    return fetch


def test_matchade_tickers_ur_mockveckan():
    assert prices.matched_tickers(MOCK_WEEK) == [
        "ALBN.MOCK", "FJLH-B.MOCK", "KUST-A.MOCK", "MOLN.MOCK", "NRSK.MOCK", "VAGN-B.MOCK",
    ]  # Tallmo är "ej i listan" och kommer inte med


def test_veckoforandring_fran_foregaende_veckas_stangning():
    q = prices.week_change(NORMAL, date(2026, 9, 28), date(2026, 10, 4))
    assert q == {"change_pct": 4.8, "close": 104.8, "currency": "SEK"}


def test_forandring_raknas_pa_justerad_stangning_men_visar_faktisk():
    # Utdelning under veckan: faktisk kurs sjunker, justerad stängning gör det inte.
    h = hist(("2026-09-25", 100.0, 95.0), ("2026-10-02", 97.0, 97.0))
    q = prices.week_change(h, date(2026, 9, 28), date(2026, 10, 4))
    assert q["change_pct"] == pytest.approx(2.11)
    assert q["close"] == 97.0


@pytest.mark.parametrize(
    "h, msg",
    [
        (hist(("2026-09-28", 101.0)), "före veckan"),
        (hist(("2026-09-25", 100.0)), "under veckan"),
        (hist(("2026-09-25", 100.0), ("2026-10-02", 101.0), currency=None), "valuta"),
    ],
)
def test_saknad_data_ger_tydligt_fel(h, msg):
    with pytest.raises(ValueError, match=msg):
        prices.week_change(h, date(2026, 9, 28), date(2026, 10, 4))


def test_pagaende_vecka_anvander_bara_kurser_till_och_med_i_dag():
    calls = []
    out = prices.build_prices(
        MOCK_WEEK, fake_fetch({"FJLH-B.MOCK": NORMAL}, calls), today=date(2026, 9, 30),
        now=datetime(2026, 9, 30, 18, 0, tzinfo=prices.STOCKHOLM),
    )
    assert all(end == date(2026, 9, 30) for _, _, end in calls)
    assert out["quotes"]["FJLH-B.MOCK"]["close"] == 101.0  # 2/10 räknas inte ännu


def test_delvisa_fel_hamnar_i_errors_och_filen_ar_giltig(tmp_path):
    out = tmp_path / "p.json"
    table = {"FJLH-B.MOCK": NORMAL, "KUST-A.MOCK": hist(("2026-09-25", 250.0), ("2026-10-02", 245.0))}
    code = prices.main(["2026-W40", "--mock", "--out", str(out)], fetch=fake_fetch(table), now=NOW)
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["mock"] is True
    assert set(data["quotes"]) == {"FJLH-B.MOCK", "KUST-A.MOCK"}
    assert data["quotes"]["KUST-A.MOCK"]["change_pct"] == -2.0
    assert set(data["errors"]) == {"ALBN.MOCK", "MOLN.MOCK", "NRSK.MOCK", "VAGN-B.MOCK"}
    assert validate.validate_prices(data, "2026-W40").errors == []


def test_ingen_lyckad_ticker_skriver_ingen_fil_och_lamnar_befintlig_orord(tmp_path):
    out = tmp_path / "p.json"
    out.write_text('{"befintlig": true}', encoding="utf-8")
    code = prices.main(["2026-W40", "--mock", "--out", str(out)], fetch=fake_fetch({}), now=NOW)
    assert code == 1
    assert out.read_text(encoding="utf-8") == '{"befintlig": true}'


def test_saknad_veckofil_ger_exit_1(tmp_path):
    assert prices.main(["2026-W01", "--mock", "--out", str(tmp_path / "p.json")], fetch=fake_fetch({}), now=NOW) == 1


def test_mock_utan_out_vagras_for_att_skydda_kurerad_mockfil():
    with pytest.raises(SystemExit):
        prices.main(["2026-W40", "--mock"], fetch=fake_fetch({}), now=NOW)
