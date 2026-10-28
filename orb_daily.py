"""
Suivi de l'ORB figé (voir orb_rules.md), avec les données 5 min NQ=F de Yahoo (environ 60 jours d'historique).

Attention : Yahoo affiche les futures CME avec ~10 min de retard. Pour passer les ordres, lire les
niveaux sur votre plateforme ; ce script sert à vérifier les niveaux, la taille et à tenir le journal.

Usage :
  python orb_daily.py              # après 15h40 Paris : niveaux, taille, ordres à placer pour la séance du jour
  python orb_daily.py --review     # quand vous voulez (au moins une fois par mois) : ajoute au journal
                                   # toutes les séances terminées qui n'y sont pas encore
"""
import argparse
import csv
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone

from midday_expansion import orb_trade
from orb_strategy import us_dst

LOG = "forward_test_log.csv"
START = "2026-10-29"   # premier jour du test en réel (lendemain du gel des règles)
FIELDS = ["date", "or_haut", "or_bas", "largeur_pts", "micros", "risque_usd", "sens", "entree", "stop",
          "sortie", "raison", "pts_brut", "pts_net_estime", "R_net", "usd_net_estime", "reel_pts", "notes"]


def fetch(symbol="NQ=F"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=60d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    res = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    days = {}
    for i, t in enumerate(res["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
            continue
        utc = datetime.fromtimestamp(t, timezone.utc)
        ny = utc + timedelta(hours=-4 if us_dst(utc) else -5)
        m = ny.hour * 60 + ny.minute
        if 570 <= m < 960:
            days.setdefault(ny.date().isoformat(), []).append(
                {"m": m, "o": q["open"][i], "h": q["high"][i], "l": q["low"][i], "c": q["close"][i]})
    return days, datetime.fromtimestamp(res["timestamp"][-1], timezone.utc)


def levels(bars, p):
    hi, lo = bars[0]["h"], bars[0]["l"]
    width = hi - lo
    n = min(p.max_micros, max(1, math.floor(p.risk_usd / (width * 2.0))))
    return hi, lo, width, n, width * 2.0 * n


def review_day(date, bars, p):
    hi, lo, width, n, risk = levels(bars, p)
    row = {"date": date, "or_haut": hi, "or_bas": lo, "largeur_pts": round(width, 2), "micros": n,
           "risque_usd": round(risk), "reel_pts": "", "notes": ""}
    t = orb_trade(bars, 0.0)
    if t is None:
        row.update(sens="aucun", raison="pas de cassure avant 12h00 ou double cassure")
        return row
    net = t["pnl_pts"] - p.cost_pts
    exit_px = t["entry"] + t["side"] * t["pnl_pts"]
    stop_px = lo if t["side"] == 1 else hi
    row.update(sens="ACHAT" if t["side"] == 1 else "VENTE", entree=round(t["entry"], 2), stop=stop_px,
               sortie=round(exit_px, 2), raison="stop" if abs(exit_px - stop_px) < 1e-6 else "clôture",
               pts_brut=round(t["pnl_pts"], 2), pts_net_estime=round(net, 2),
               R_net=round(net / t["risk_pts"], 2), usd_net_estime=round(net * 2.0 * n))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--risk-usd", type=float, default=100)
    ap.add_argument("--max-micros", type=int, default=10)
    ap.add_argument("--cost-pts", type=float, default=1.5, help="frais + glissement estimés, aller-retour")
    ap.add_argument("--start", default=START, help="ne journaliser qu'à partir de cette date")
    p = ap.parse_args()

    days, last = fetch()
    print(f"Données NQ=F (Yahoo, retard possible ~10 min) jusqu'à {last:%Y-%m-%d %H:%M} UTC")

    if not p.review:
        date = max(days)
        bars = days[date]
        if bars[0]["m"] != 570:
            raise SystemExit(f"Séance {date} : bougie 9h30 New York pas encore disponible.")
        hi, lo, width, n, risk = levels(bars, p)
        print(f"\nSéance {date} | opening range 9h30-9h35 NY : haut {hi:.2f} | bas {lo:.2f} | largeur {width:.2f} pts")
        print(f"Taille : {n} micro(s) MNQ -> risque {risk:.0f} $ (+ frais)"
              + ("  ⚠ 1 micro dépasse 2x le risque prévu : la règle prend quand même le trade" if risk > 2 * p.risk_usd else ""))
        print("Ordres OCO jusqu'à 12h00 NY (18h00 Paris) :")
        print(f"  ACHAT stop {hi + 0.25:.2f}  -> stop de protection {lo:.2f}")
        print(f"  VENTE stop {lo - 0.25:.2f}  -> stop de protection {hi:.2f}")
        print("  Sortie à la clôture de la bougie 15h55 NY (21h55 Paris) si le stop n'est pas touché.")
        return

    rows = {}
    if os.path.exists(LOG):
        with open(LOG, newline="", encoding="utf-8") as f:
            rows = {r["date"]: r for r in csv.DictReader(f)}
    added = []
    for date, bars in sorted(days.items()):
        if date < p.start or date in rows or bars[0]["m"] != 570 or bars[-1]["m"] < 955:
            continue
        rows[date] = review_day(date, bars, p)
        added.append(rows[date])
    with open(LOG, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows[d] for d in sorted(rows))
    for r in added:
        res = f"{r['sens']} {r.get('R_net', '')} R ({r.get('raison', '')})" if r["sens"] != "aucun" else "pas de trade"
        print(f"  ajouté {r['date']} : {res}")
    traded = [r for r in rows.values() if r.get("R_net") not in (None, "")]
    total = sum(float(r["R_net"]) for r in traded)
    print(f"\n{len(added)} séance(s) ajoutée(s). Journal {LOG} : {len(rows)} séances, {len(traded)} trades, "
          f"{total:+.1f} R (référence à 40 trades : 5 % des cas pire que −15 R, médiane +5 R)")


if __name__ == "__main__":
    main()
