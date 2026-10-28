"""
Génère dashboard.html (page autonome, aucune installation) et l'ouvre dans le navigateur.

  - barre latérale : les stratégies testées (ORB figé, milieu de séance, Post-Displacement archivé)
  - calendrier mensuel du P&L (backtest ou paper trading)
  - comparaison des performances : courbe backtest, paper trading théorique vs réel, fourchette attendue
  - trades récents
  - simulateur LucidFlex 25K : tirage des séances 2021-2026 par blocs de 5 jours (garde les séries de pertes),
    réussite, causes d'échec, 60 trajectoires, valeur par tentative

Usage :
  python dashboard.py            # recalcule tout (~1 min) et ouvre la page
  python dashboard.py --mc 2000  # simulation plus rapide
"""
import argparse
import csv
import json
import math
import os
import random
import statistics as st
import webbrowser
from datetime import datetime

from giant_candles import load
from midday_expansion import build_days

DATA = ["data/nq_5m_histdata_2010_2020.csv", "data/nq_5m_histdata.csv"]
PD_TRADES = "01_Post_Displacement_Archive/trades/trades_walk_forward_dukascopy.csv"
LOG = "forward_test_log.csv"
TEMPLATE = "dashboard_template.html"   # mise en page : modifiable librement, __DATA__ = données injectées
SPLIT = "2021-01-01"
BLOCK = 5


# ---------- Simulation LucidFlex par blocs de 5 séances ----------

def sized(day, risk, cap):
    pnl = low = 0.0
    for t in day:
        n = min(cap, max(1, math.floor(risk / (t["risk_pts"] * 2.0))))
        p = t["pnl_pts"] * n * 2.0
        pnl += p
        low += min(-t["mae_pts"] * n * 2.0, p)
    return max(pnl, -600.0), max(low, -600.0), pnl < -600.0


def blocks(pool):
    while True:
        s = random.randrange(0, len(pool) - BLOCK)
        yield from pool[s:s + BLOCK]


def evaluation(pool, risk, max_days=500, path=False):
    bal = hw = best = 0.0
    curve, dll = [0.0], 0
    for k, day in zip(range(max_days), blocks(pool)):
        pnl, low, hit = sized(day, risk, 20)
        dll += hit
        floor = 100.0 if hw >= 1100 else hw - 1000
        if bal + low <= floor:
            return ("perte max", k + 1, curve, dll)
        bal += pnl
        hw, best = max(hw, bal), max(best, pnl)
        curve.append(round(bal))
        if bal >= 1250 and best <= 0.4 * bal:
            return ("réussite", k + 1, curve, dll)
    return ("délai dépassé", max_days, curve, dll)


def funded(pool, risk, max_days=1000):
    bal = hw = cycle = cash = 0.0
    good = payouts = 0
    locked = False
    for _, day in zip(range(max_days), blocks(pool)):
        pnl, low, _ = sized(day, risk, 10 if bal < 1000 else 20)
        floor = 100.0 if locked else min(hw, 1100) - 1000
        if bal + low <= floor:
            break
        bal += pnl
        cycle += pnl
        hw = max(hw, bal)
        locked = locked or hw >= 1100
        good += pnl >= 100
        amount = min(0.5 * bal, 1000)
        if good >= 5 and cycle > 0 and amount >= 500:
            cash += 0.9 * amount
            bal -= amount
            payouts += 1
            locked, good, cycle = True, 0, 0.0
            if payouts == 5:
                break
    return cash


def lucid(pool, risks, mc, fee=39):
    out = {}
    for risk in risks:
        res = [evaluation(pool, risk) for _ in range(mc)]
        cash = [funded(pool, risk) for _ in range(mc)]
        ok = sum(r[0] == "réussite" for r in res) / mc
        causes = {c: sum(r[0] == c for r in res) / mc for c in ("réussite", "perte max", "délai dépassé")}
        days_ok = [r[1] for r in res if r[0] == "réussite"]
        out[str(int(risk))] = {
            "pass": ok, "causes": causes, "cash": st.mean(cash), "ev": ok * st.mean(cash) - fee,
            "one_payout": sum(c > 0 for c in cash) / mc,
            "days_median": st.median(days_ok) if days_ok else None,
            "dll_days": st.mean(r[3] for r in res),
            "paths": [r[2] for r in res[:60]]}
    return out


# ---------- Données ----------

def daily_series(days_trades):
    """[[date, R du jour, nb trades]] pour toutes les séances."""
    return [[d, round(sum(t["R"] for t in ts), 3), len(ts)] for d, ts in sorted(days_trades.items())]


def recent_trades(days_trades, n=25):
    out = []
    for d in sorted(days_trades, reverse=True):
        for t in days_trades[d]:
            out.append({"date": d, "setup": t["setup"], "sens": "ACHAT" if t["side"] == 1 else "VENTE",
                        "entree": round(t["entry"], 2), "risque_pts": round(t["risk_pts"], 1),
                        "R": round(t["R"], 2), "sortie_h": f"{t['exit_m'] // 60}h{t['exit_m'] % 60:02d}"})
        if len(out) >= n:
            break
    return out


def period_stats(series):
    def s(rows):
        rs = [r[1] for r in rows if r[2]]
        if not rs:
            return None
        g, l = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
        eq = peak = dd = 0.0
        for x in rs:
            eq += x
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        return {"n": sum(r[2] for r in rows), "win": sum(x > 0 for x in rs) / len(rs), "tot": round(sum(rs), 1),
                "pf": round(g / l, 2) if l else None, "dd": round(dd, 1)}
    return {"2010-2020": s([r for r in series if r[0] < SPLIT]), "2021-2026": s([r for r in series if r[0] >= SPLIT])}


def expected_band(series, horizon=80, mc=4000):
    """Fourchette P5 / P50 / P95 du cumul de R après n trades (tirage des trades 2021-2026)."""
    rs = [r[1] for r in series if r[0] >= SPLIT and r[2]]
    sums = [[0.0] * (horizon + 1) for _ in range(mc)]
    for s in sums:
        for k in range(1, horizon + 1):
            s[k] = s[k - 1] + random.choice(rs)
    band = []
    for k in range(horizon + 1):
        col = sorted(s[k] for s in sums)
        band.append([round(col[int(0.05 * mc)], 2), round(col[mc // 2], 2), round(col[int(0.95 * mc) - 1], 2)])
    return band


def forward_rows():
    if not os.path.exists(LOG):
        return []
    with open(LOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mc", type=int, default=3000)
    ap.add_argument("--risks", default="100,150,200")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--no-open", action="store_true")
    p = ap.parse_args()
    random.seed(1)
    risks = [float(x) for x in p.risks.split(",")]

    print("Chargement des 16 ans de bougies 5 min…")
    days = build_days(load(DATA), 0.000075)
    orb = {d: [v["ORB"]] if v["ORB"] else [] for d, v in days.items()}
    mid = {d: [v["MID"]] if v["MID"] else [] for d, v in days.items()}

    print("Simulation LucidFlex (blocs de 5 séances)…")
    strategies = []
    for sid, name, status, rules, dt in (
        ("orb", "ORB 5 minutes", "Figée — en test réel",
         ["Range 9h30-9h35 New York (15h30 Paris)", "Ordres stop OCO sur les deux côtés jusqu'à 12h00 NY",
          "Stop de l'autre côté du range, sortie à la clôture 15h55 NY", "1 trade par jour, MNQ, 100 $ de risque"], orb),
        ("mid", "Expansion milieu de séance", "Abandonnée — ne se confirme pas en 2021-2026",
         ["Bougie 10h00-15h00 NY >= 4x la médiane de sa tranche horaire",
          "Première cassure d'un extrême dans les 30 min, stop à l'autre extrême", "Sortie à la clôture, 1 trade par jour"], mid)):
        series = daily_series(dt)
        pool = [dt[d] for d in sorted(dt) if d >= SPLIT]
        strategies.append({"id": sid, "name": name, "status": status, "rules": rules, "series": series,
                           "stats": period_stats(series), "recent": recent_trades(dt),
                           "band": expected_band(series) if sid == "orb" else None,
                           "lucid": lucid(pool, risks, p.mc)})
        print(f"  {name} : ok")

    pd_series = {}
    if os.path.exists(PD_TRADES):
        with open(PD_TRADES, newline="") as f:
            for r in csv.DictReader(f):
                d = r["entry_time"][:10]
                pd_series.setdefault(d, [0.0, 0])
                pd_series[d][0] += float(r["R"])
                pd_series[d][1] += 1
        series = [[d, round(v[0], 3), v[1]] for d, v in sorted(pd_series.items())]
        strategies.append({"id": "pd", "name": "Post-Displacement (1H)", "status": "Abandonnée — PF 1,13 en walk-forward",
                           "rules": ["Bougie 1H > 2x ATR, retour vers le VWAP daily", "Walk-forward Dukascopy 2022-2026"],
                           "series": series, "stats": period_stats(series), "recent": [], "band": None, "lucid": None})

    payload = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "strategies": strategies,
               "forward": forward_rows(), "risks": [str(int(r)) for r in risks]}
    with open(TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    html = template.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    with open(p.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {p.out} ({len(html) / 1e6:.1f} Mo)")
    if not p.no_open:
        webbrowser.open("file://" + os.path.abspath(p.out))


if __name__ == "__main__":
    main()
