"""
Télécharge le Nasdaq 100 (NSXUSD) en 1 minute depuis HistData.com (gratuit, sans compte),
puis construit des bougies 5 minutes horodatées en UTC pour orb_strategy.py.

HistData annonce de l'EST fixe, mais la comparaison avec Dukascopy montre un horodatage en heure de
New York AVEC heure d'été (écart d'1 h en été sinon) : UTC = heure NY + 5 h en hiver, + 4 h en été.

Usage :
  python fetch_histdata.py                      # 2021 -> octobre 2026
  python fetch_histdata.py --years 2021,2022 --months 2026-01,2026-02
"""
import argparse
import csv
import io
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

from orb_strategy import us_dst

BASE = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/nsxusd/"
OUT_DIR = os.path.join("data", "histdata")
UA = {"User-Agent": "Mozilla/5.0"}


def download(period):
    """period = '2022' (année) ou '2026/10' (mois). Retourne le chemin du zip."""
    name = f"NSXUSD_M1_{period.replace('/', '')}.zip"
    path = os.path.join(OUT_DIR, name)
    if os.path.exists(path):
        return path
    page_url = BASE + period
    html = urllib.request.urlopen(urllib.request.Request(page_url, headers=UA), timeout=60).read().decode("utf-8", "ignore")
    form = html[html.find('<form id="file_down"'):]
    form = form[:form.find("</form>")]
    fields = dict(re.findall(r'name="(\w+)" id="\w+" value="([^"]*)"', form))
    req = urllib.request.Request("https://www.histdata.com/get.php", data=urllib.parse.urlencode(fields).encode(),
                                 headers={**UA, "Referer": page_url})
    data = urllib.request.urlopen(req, timeout=120).read()
    if not data.startswith(b"PK"):
        raise SystemExit(f"{period} : réponse inattendue ({data[:120]!r})")
    with open(path, "wb") as f:
        f.write(data)
    print(f"{period} : {len(data) / 1e6:.1f} Mo")
    time.sleep(2)
    return path


def read_m1(path):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        for line in io.TextIOWrapper(z.open(name), encoding="utf-8"):
            parts = line.strip().split(";")
            if len(parts) < 5:
                continue
            ny = datetime.strptime(parts[0], "%Y%m%d %H%M%S").replace(tzinfo=timezone.utc)
            utc = ny + timedelta(hours=5)
            if us_dst(utc):
                utc -= timedelta(hours=1)
            yield int(utc.timestamp()), *map(float, parts[1:5])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2021,2022,2023,2024,2025")
    ap.add_argument("--months", default=",".join(f"2026-{m:02d}" for m in range(1, 11)))
    ap.add_argument("--out", default=os.path.join("data", "nq_5m_histdata.csv"))
    p = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    periods = p.years.split(",") + [m.replace("-", "/") for m in p.months.split(",") if m]
    zips = [download(x) for x in periods]

    bars = {}
    for z in zips:
        for t, o, h, l, c in read_m1(z):
            k = t // 300 * 300
            b = bars.get(k)
            if b is None:
                bars[k] = [o, h, l, c]
            else:
                b[1], b[2], b[3] = max(b[1], h), min(b[2], l), c
    with open(p.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for k in sorted(bars):
            w.writerow([k, *bars[k], 0])
    first, last = min(bars), max(bars)
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"{len(bars)} bougies 5 min -> {p.out} ({iso(first)} -> {iso(last)} UTC)")


if __name__ == "__main__":
    main()
