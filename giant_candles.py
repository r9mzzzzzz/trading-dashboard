"""
Étude d'événements : que fait le Nasdaq après une bougie 5 minutes géante ?

Fenêtre : 8h00 -> 16h00 New York (inclut les annonces macro de 8h30 = 14h30 Paris et l'ouverture
de 9h30 = 15h30 Paris). Données : CSV 5 min en UTC (fetch_histdata.py).

Détection (seuils relatifs uniquement : 80 points en 2012 et en 2026 ne veulent pas dire la même chose) :
  --norm atr : amplitude >= K x ATR(14) des bougies 5 min précédentes
  --norm tod : amplitude >= K x médiane de la même tranche horaire sur les 20 séances précédentes
               (corrige le fait que les bougies de 8h30 et 9h30 sont naturellement plus grandes)

Mesures, dans le sens de la bougie (positif = continuation), en multiples de l'amplitude de la bougie :
  mouvement à +15 min, +30 min, +1 h, +2 h et à la clôture ; probabilité de toucher l'extrémité
  opposée de la bougie (retracement complet) ; comparaison avec des bougies « normales ».

Deux règles simples, une entrée max par bougie dans les 30 min (6 bougies), sortie à la clôture 15h55 :
  A (continuation) : ordre stop à la cassure de l'extrémité dans le sens de la bougie, stop à l'autre extrémité
  B (retournement) : ordre stop à la cassure de l'extrémité opposée, stop à l'extrémité de départ
  Coût par trade : --cost-frac x prix (0,0075 % = 1,5 point de NQ à 20 000).

Usage :
  python giant_candles.py
  python giant_candles.py --norm tod --ks 3,4,5
"""
import argparse
import csv
import statistics as st
from datetime import datetime, timedelta, timezone

from orb_strategy import us_dst

HORIZONS = [("15 min", 3), ("30 min", 6), ("1 h", 12), ("2 h", 24)]


def load(paths, start=480, end=960):
    """Bougies 8h00 -> 15h55 New York, en une seule liste, avec date et minute NY."""
    bars = []
    for path in paths:
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                utc = datetime.fromtimestamp(int(r["time"]), timezone.utc)
                ny = utc + timedelta(hours=-4 if us_dst(utc) else -5)
                m = ny.hour * 60 + ny.minute
                if ny.weekday() < 5 and start <= m < end:
                    bars.append({"d": ny.date().isoformat(), "m": m, "o": float(r["open"]),
                                 "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"])})
    bars.sort(key=lambda b: (b["d"], b["m"]))
    return bars


def reference(bars, norm):
    """Amplitude de référence de chaque bougie, calculée uniquement sur le passé."""
    ref = [None] * len(bars)
    if norm == "atr":
        prev = None
        for i in range(1, len(bars)):
            b, p = bars[i], bars[i - 1]
            tr = max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"]))
            ref[i] = prev
            prev = tr if prev is None else (prev * 13 + tr) / 14
    else:
        hist = {}
        for i, b in enumerate(bars):
            h = hist.setdefault(b["m"], [])
            if len(h) >= 20:
                ref[i] = st.median(h[-20:])
            h.append(b["h"] - b["l"])
    return ref


def bucket(m):
    if 510 <= m < 540:
        return "8h30-9h00 (news)"
    if 570 <= m < 600:
        return "9h30-10h00 (ouverture)"
    return "autres heures"


def analyse(bars, ref, k, lo_ctrl, hi_ctrl, cost_frac):
    events, ctrl = [], []
    day_end = {}
    for i, b in enumerate(bars):
        day_end[b["d"]] = i
    for i, b in enumerate(bars):
        rg = b["h"] - b["l"]
        if ref[i] is None or ref[i] <= 0 or rg <= 0 or b["c"] == b["o"]:
            continue
        ratio = rg / ref[i]
        last = day_end[b["d"]]
        if last - i < 1:
            continue
        side = 1 if b["c"] > b["o"] else -1
        ev = {"d": b["d"], "m": b["m"], "side": side, "rg": rg}
        for name, h in HORIZONS:
            j = min(i + h, last)
            ev[name] = (bars[j]["c"] - b["c"]) * side / rg
        ev["clôture"] = (bars[last]["c"] - b["c"]) * side / rg
        opp = b["l"] if side == 1 else b["h"]
        ev["retrace"] = any((x["l"] <= opp if side == 1 else x["h"] >= opp) for x in bars[i + 1:last + 1])
        if ratio >= k:
            ev["A"] = rule(bars, i, last, side, b["h"], b["l"], cost_frac, follow=True)
            ev["B"] = rule(bars, i, last, side, b["h"], b["l"], cost_frac, follow=False)
            events.append(ev)
        elif lo_ctrl <= ratio < hi_ctrl:
            ctrl.append(ev)
    return events, ctrl


def rule(bars, i, last, side, hi, lo, cost_frac, follow):
    """A : suit la cassure dans le sens de la bougie. B : suit la cassure de l'autre côté."""
    trade_side = side if follow else -side
    level = (hi if trade_side == 1 else lo)        # niveau d'entrée (stop d'entrée)
    stop = (lo if trade_side == 1 else hi)
    for k in range(i + 1, min(i + 7, last + 1)):
        x = bars[k]
        up, dn = x["h"] > hi, x["l"] < lo
        if up and dn:
            return None
        hit = up if trade_side == 1 else dn
        other = dn if trade_side == 1 else up
        if other:
            return None                              # l'autre côté casse d'abord : pas de trade
        if hit:
            entry = max(level, x["o"]) if trade_side == 1 else min(level, x["o"])
            risk = (entry - stop) * trade_side
            for j in range(k, last + 1):
                y = bars[j]
                if (y["l"] <= stop) if trade_side == 1 else (y["h"] >= stop):
                    exit_px, dur = stop, j - k + 1
                    break
            else:
                exit_px, dur = bars[last]["c"], last - k + 1
            pnl = (exit_px - entry) * trade_side - cost_frac * entry
            return {"R": pnl / risk, "dur": dur * 5}
    return None


def summary(rows, label):
    if not rows:
        return f"{label:<26}{'—':>6}"
    def m(key):
        return st.mean(r[key] for r in rows)
    return (f"{label:<26}{len(rows):>6}" + "".join(f"{m(n):>+8.2f}" for n, _ in HORIZONS)
            + f"{m('clôture'):>+9.2f}{sum(r['retrace'] for r in rows) / len(rows) * 100:>10.0f}%")


def strat(rows, key, label):
    ts = [r[key] for r in rows if r.get(key)]
    if not ts:
        return f"{label:<26}{'aucun trade':>12}"
    rs = [t["R"] for t in ts]
    g, l = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    return (f"{label:<26}{len(rs):>6}{sum(x > 0 for x in rs) / len(rs) * 100:>7.1f}%{st.mean(rs):>+8.3f}"
            f"{sum(rs):>+8.1f}{(g / l if l else float('inf')):>7.2f}{st.median(t['dur'] for t in ts):>9.0f} min")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/nq_5m_histdata_2010_2020.csv,data/nq_5m_histdata.csv")
    ap.add_argument("--norm", choices=["atr", "tod"], default="atr")
    ap.add_argument("--ks", default="2.5,3,4")
    ap.add_argument("--cost-frac", type=float, default=0.000075)
    ap.add_argument("--split", default="2021-01-01", help="découverte avant, confirmation après")
    p = ap.parse_args()

    bars = load(p.csv.split(","))
    ref = reference(bars, p.norm)
    print(f"{len(bars)} bougies 5 min (8h00-16h00 NY), {bars[0]['d']} -> {bars[-1]['d']} | "
          f"référence = {'ATR(14)' if p.norm == 'atr' else 'médiane horaire 20 j'} | coût {p.cost_frac:.4%} du prix")

    for k in [float(x) for x in p.ks.split(",")]:
        events, ctrl = analyse(bars, ref, k, 0.8, 1.2, p.cost_frac)
        print(f"\n################ Bougies >= {k} x référence ################")
        for period, test in (("DÉCOUVERTE 2010-2020", lambda r: r["d"] < p.split),
                             ("CONFIRMATION 2021-2026", lambda r: r["d"] >= p.split)):
            ev = [r for r in events if test(r)]
            ct = [r for r in ctrl if test(r)]
            print(f"\n--- {period} ---")
            print("Mouvement après la bougie, dans son sens, en multiples de son amplitude (+ = continuation)")
            print(f"{'':<26}{'N':>6}" + "".join(f"{n:>8}" for n, _ in HORIZONS) + f"{'clôture':>9}{'retrace 100%':>11}")
            print(summary(ct, "bougies normales (contrôle)"))
            print(summary(ev, "bougies géantes"))
            for bk in ("8h30-9h00 (news)", "9h30-10h00 (ouverture)", "autres heures"):
                print(summary([r for r in ev if bucket(r["m"]) == bk], f"  {bk}"))
            print(f"{'Règle (sortie clôture)':<26}{'N':>6}{'Win%':>8}{'R moy':>8}{'R tot':>8}{'PF':>7}{'Durée méd.':>13}")
            print(strat(ev, "A", "A continuation"))
            print(strat(ev, "B", "B retournement"))
            for bk in ("8h30-9h00 (news)", "9h30-10h00 (ouverture)", "autres heures"):
                sub = [r for r in ev if bucket(r["m"]) == bk]
                print(strat(sub, "A", f"  A {bk}"))
                print(strat(sub, "B", f"  B {bk}"))


if __name__ == "__main__":
    main()
