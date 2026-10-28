"""
Télécharge QQQ en 5 minutes (séance régulière 9h30-16h00 New York) mois par mois depuis Alpha Vantage.

La version gratuite autorise 25 requêtes par jour : relancer le script chaque jour, il reprend
là où il s'est arrêté (les mois déjà téléchargés sont sautés). Ordre : 2022 d'abord, puis le reste.

Clé API : variable d'environnement ALPHAVANTAGE_KEY, ou fichier data/.av_key (une ligne).

Usage :
  python fetch_alphavantage.py            # télécharge jusqu'à 25 mois manquants
  python fetch_alphavantage.py --merge    # assemble data/qqq_5m/*.csv en data/qqq_5m.csv
"""
import argparse
import csv
import io
import os
import time
import urllib.parse
import urllib.request

OUT_DIR = os.path.join("data", "qqq_5m")


def months(first="2021-11", last="2026-10"):
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    # 2022 en premier : c'est l'année du test de marché baissier
    return [x for x in out if x.startswith("2022")] + [x for x in out if not x.startswith("2022")]


def api_key():
    key = os.environ.get("ALPHAVANTAGE_KEY")
    if not key and os.path.exists(os.path.join("data", ".av_key")):
        key = open(os.path.join("data", ".av_key")).read().strip()
    if not key:
        raise SystemExit("Clé manquante : définir ALPHAVANTAGE_KEY ou créer data/.av_key")
    return key


def fetch(month, key):
    q = urllib.parse.urlencode({"function": "TIME_SERIES_INTRADAY", "symbol": "QQQ", "interval": "5min",
                                "month": month, "outputsize": "full", "extended_hours": "false",
                                "adjusted": "false", "datatype": "csv", "apikey": key})
    with urllib.request.urlopen(f"https://www.alphavantage.co/query?{q}", timeout=60) as r:
        return r.read().decode("utf-8")


def merge():
    rows = []
    for name in sorted(os.listdir(OUT_DIR)):
        with open(os.path.join(OUT_DIR, name), newline="") as f:
            rows += list(csv.DictReader(f))
    rows.sort(key=lambda r: r["timestamp"])
    out = os.path.join("data", "qqq_5m.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows({k: r[k] for k in w.fieldnames} for r in rows)
    print(f"{len(rows)} bougies -> {out} ({rows[0]['timestamp']} -> {rows[-1]['timestamp']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--max", type=int, default=25, help="requêtes max pour cette exécution")
    p = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    if p.merge:
        return merge()

    key, done = api_key(), 0
    todo = [m for m in months() if not os.path.exists(os.path.join(OUT_DIR, f"{m}.csv"))]
    print(f"{len(todo)} mois manquants")
    for m in todo[:p.max]:
        text = fetch(m, key)
        if not text.startswith("timestamp"):
            print(f"{m} : arrêt, réponse inattendue -> {text.strip()[:200]}")
            break
        n = text.count("\n") - 1
        with open(os.path.join(OUT_DIR, f"{m}.csv"), "w", newline="") as f:
            f.write(text)
        done += 1
        print(f"{m} : {n} bougies")
        time.sleep(13)  # 5 requêtes par minute maximum en gratuit
    print(f"{done} mois téléchargés, {len(todo) - done} restants")


if __name__ == "__main__":
    main()
