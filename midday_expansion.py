"""
Stratégie « expansion de volatilité en milieu de séance » + ORB, avec simulation LucidFlex 25K par jour.

Règles figées (issues de giant_candles.py, aucune optimisation ici) :
  ORB : range 9h30-9h35 New York, ordre stop à la cassure (premier côté cassé avant 12h00),
        stop à l'autre côté du range, sortie à la clôture 15h55.
  MID : bougie 5 min démarrant entre 10h00 et 15h00 New York dont l'amplitude >= 4 x la médiane de la même
        tranche horaire sur les 20 séances précédentes ; dans les 6 bougies suivantes, ordre stop sur la
        première cassure d'un des deux extrêmes, stop à l'autre extrême, sortie à la clôture 15h55.
        Un seul trade MID par jour.
  Coût : --cost-frac x prix par trade (0,0075 % = 1,5 point de NQ à 20 000).

Variantes : ORB seul, MID seul, ORB + MID en parallèle, ORB + MID exclusif (MID seulement si l'ORB est clos).

Simulation LucidFlex 25K, journée par journée (tirage de séances au hasard sur la période choisie) :
  évaluation : objectif +1 250 $, MLL 1 000 $ EOD bloqué à +100 $ dès +1 100 $, perte journalière 600 $,
               consistance 40 %, prix 39 $
  financé    : même MLL (bloqué au premier retrait), scaling 10 micros sous 1 000 $ de profit puis 20,
               retrait si 5 jours >= 100 $ et cycle > 0, min(50 % du profit, 1 000 $) >= 500 $, 90 %, 5 retraits max
  Taille par trade : risque $ / (stop x 2 $ par point MNQ), au moins 1 micro.

Usage :
  python midday_expansion.py
  python midday_expansion.py --risk-usd 100,150 --mc 5000
"""
import argparse
import math
import random
import statistics as st

from giant_candles import load

ORB_MIN, MID_START, MID_END, MID_K, MID_WINDOW, LAST_ORB_ENTRY = 5, 600, 900, 4.0, 6, 720


# ---------- Trades ----------

def manage(day, k, side, entry, stop, cost):
    """Gère une position ouverte sur la bougie k jusqu'au stop ou à la clôture. Seul le stop est testé
    sur la bougie d'entrée (prudent)."""
    risk = (entry - stop) * side
    mae = 0.0
    for j in range(k, len(day)):
        x = day[j]
        mae = max(mae, min((entry - x["l"]) if side == 1 else (x["h"] - entry), risk))
        if (x["l"] <= stop) if side == 1 else (x["h"] >= stop):
            exit_px, exit_m = stop, x["m"] + 5
            break
    else:
        exit_px, exit_m = day[-1]["c"], day[-1]["m"] + 5
    c = cost * entry
    pnl = (exit_px - entry) * side - c
    return {"side": side, "entry": entry, "risk_pts": risk, "pnl_pts": pnl, "mae_pts": mae + c,
            "R": pnl / risk, "entry_m": day[k]["m"], "exit_m": exit_m}


def breakout(day, start, hi, lo, cost, last_entry=None, window=None):
    """Premier côté cassé à partir de la bougie start. Si les deux côtés cassent sur la même bougie : rien."""
    end = len(day) if window is None else min(len(day), start + window)
    for k in range(start, end):
        x = day[k]
        if last_entry is not None and x["m"] >= last_entry:
            return None
        up, dn = x["h"] > hi, x["l"] < lo
        if up and dn:
            return None
        if up:
            return manage(day, k, 1, max(hi, x["o"]), lo, cost)
        if dn:
            return manage(day, k, -1, min(lo, x["o"]), hi, cost)
    return None


def orb_trade(day, cost):
    n = ORB_MIN // 5
    reg = [b for b in day if b["m"] >= 570]
    if len(reg) < n + 2 or reg[0]["m"] != 570:
        return None
    hi, lo = max(b["h"] for b in reg[:n]), min(b["l"] for b in reg[:n])
    if hi <= lo:
        return None
    t = breakout(reg, n, hi, lo, cost, last_entry=LAST_ORB_ENTRY)
    return dict(t, setup="ORB") if t else None


def mid_trade(day, refs, cost, not_before=0):
    """Premier signal MID de la journée dont l'entrée a lieu après not_before (minutes NY)."""
    for i, b in enumerate(day):
        if not MID_START <= b["m"] < MID_END or refs[i] is None or refs[i] <= 0:
            continue
        if (b["h"] - b["l"]) < MID_K * refs[i]:
            continue
        t = breakout(day, i + 1, b["h"], b["l"], cost, window=MID_WINDOW)
        if t and t["entry_m"] >= not_before:
            return dict(t, setup="MID")
        if t:
            continue
    return None


def build_days(bars, cost):
    """{date: {'ORB': trade|None, 'MID': trade|None, 'MID_excl': trade|None}}."""
    hist, days, cur, out = {}, [], [], {}
    refs_all = []
    for b in bars:                                   # médiane de la même tranche sur 20 séances passées
        h = hist.setdefault(b["m"], [])
        refs_all.append(st.median(h[-20:]) if len(h) >= 20 else None)
        h.append(b["h"] - b["l"])
    i = 0
    while i < len(bars):
        j = i
        while j < len(bars) and bars[j]["d"] == bars[i]["d"]:
            j += 1
        day, refs = bars[i:j], refs_all[i:j]
        orb = orb_trade(day, cost)
        mid = mid_trade(day, refs, cost)
        excl = mid_trade(day, refs, cost, not_before=orb["exit_m"] if orb else 0)
        out[bars[i]["d"]] = {"ORB": orb, "MID": mid, "MID_excl": excl}
        i = j
    return out


VARIANTS = {"ORB seul": ("ORB",), "MID seul": ("MID",), "ORB + MID": ("ORB", "MID"),
            "ORB + MID exclusif": ("ORB", "MID_excl")}


def variant_days(days, keys):
    return {d: [v[k] for k in keys if v[k]] for d, v in days.items()}


# ---------- Statistiques ----------

def stats(trades):
    rs = [t["R"] for t in trades]
    if not rs:
        return None
    g, l = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": len(rs), "win": sum(r > 0 for r in rs) / len(rs), "avg": sum(rs) / len(rs),
            "tot": sum(rs), "pf": g / l if l else float("inf"), "dd": dd}


def line(label, s, extra=""):
    if not s:
        return f"{label:<24}{'aucun trade':>12}"
    return (f"{label:<24}{s['n']:>6}{s['win']*100:>7.1f}%{s['avg']:>+8.3f}{s['tot']:>+8.1f}"
            f"{s['pf']:>7.2f}{s['dd']:>9.1f}{extra}")


HEAD = f"{'':<24}{'N':>6}{'Win%':>8}{'R moy':>8}{'R tot':>8}{'PF':>7}{'MaxDD R':>9}"


# ---------- Simulation LucidFlex (par journée) ----------

def day_pnl(trades, risk_usd, cap, shift):
    pnl = low = 0.0
    for t in trades:
        n = min(cap, max(1, math.floor(risk_usd / (t["risk_pts"] * 2.0))))
        p = (t["pnl_pts"] - shift.get(t["setup"], 0.0) * t["risk_pts"]) * n * 2.0
        pnl += p
        low += min(-t["mae_pts"] * n * 2.0, p)
    return max(pnl, -600.0), max(low, -600.0)


def evaluation(pool, risk, p, shift):
    bal = hw = best = 0.0
    for _ in range(p.max_days):
        pnl, low = day_pnl(random.choice(pool), risk, 20, shift)
        floor = 100.0 if hw >= 1100 else hw - 1000
        if bal + low <= floor:
            return False
        bal += pnl
        hw = max(hw, bal)
        best = max(best, pnl)
        if bal >= 1250 and best <= 0.4 * bal:
            return True
    return False


def funded(pool, risk, p, shift):
    bal = hw = cycle = cash = 0.0
    good, payouts, locked = 0, 0, False
    for _ in range(p.max_days):
        pnl, low = day_pnl(random.choice(pool), risk, 10 if bal < 1000 else 20, shift)
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


def lucid(pool, risk, p, shift=None):
    shift = shift or {}
    ok = sum(evaluation(pool, risk, p, shift) for _ in range(p.mc)) / p.mc
    cash = sum(funded(pool, risk, p, shift) for _ in range(p.mc)) / p.mc
    return ok, cash, ok * cash - p.fee


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/nq_5m_histdata_2010_2020.csv,data/nq_5m_histdata.csv")
    ap.add_argument("--cost-frac", type=float, default=0.000075)
    ap.add_argument("--split", default="2021-01-01")
    ap.add_argument("--risk-usd", default="100,150")
    ap.add_argument("--mc", type=int, default=4000)
    ap.add_argument("--max-days", type=int, default=1000)
    ap.add_argument("--fee", type=float, default=39)
    ap.add_argument("--seed", type=int, default=1)
    p = ap.parse_args()
    random.seed(p.seed)

    days = build_days(load(p.csv.split(",")), p.cost_frac)
    print(f"{len(days)} séances, {min(days)} -> {max(days)} | coût {p.cost_frac:.4%} du prix | "
          f"MID : >= {MID_K} x médiane horaire, {MID_START // 60}h00-{MID_END // 60}h00 NY")

    periods = (("DÉCOUVERTE 2010-2020", lambda d: d < p.split), ("CONFIRMATION 2021-2026", lambda d: d >= p.split))
    for name, keep in periods:
        print(f"\n=== {name} ===")
        print(HEAD + f"{'trades/mois':>13}")
        n_days = sum(1 for d in days if keep(d))
        for label, keys in VARIANTS.items():
            ts = [t for d, v in variant_days(days, keys).items() if keep(d) for t in v]
            print(line(label, stats(ts), f"{len(ts) / n_days * 21:>12.1f}"))

    print("\n=== Par année (R total, PF) ===")
    years = sorted({d[:4] for d in days})
    print(f"{'':<24}" + "".join(f"{y:>12}" for y in years))
    for label, keys in VARIANTS.items():
        vd = variant_days(days, keys)
        cells = []
        for y in years:
            s = stats([t for d, v in vd.items() if d[:4] == y for t in v])
            cells.append(f"{s['tot']:>+6.0f} {s['pf']:.2f}" if s else f"{'—':>11}")
        print(f"{label:<24}" + "".join(f"{c:>12}" for c in cells))

    print(f"\n=== LucidFlex 25K sur les séances 2021-2026 ({p.mc} tirages, journée par journée) ===")
    print(f"{'':<24}{'Risque':>8}{'Réussite éval':>15}{'Gain financé':>14}{'Valeur/tentative':>18}{'Sans edge':>11}")
    for label, keys in VARIANTS.items():
        vd = variant_days(days, keys)
        pool = [v for d, v in vd.items() if d >= p.split]
        trades = [t for v in pool for t in v]
        shift = {s: st.mean(t["R"] for t in trades if t["setup"] == s)
                 for s in {t["setup"] for t in trades}}
        for risk in [float(x) for x in p.risk_usd.split(",")]:
            ok, cash, ev = lucid(pool, risk, p)
            _, _, ev0 = lucid(pool, risk, p, shift)
            print(f"{label:<24}{risk:>6.0f} ${ok*100:>13.1f}%{cash:>12.0f} ${ev:>+16.0f} ${ev0:>+9.0f} $")


if __name__ == "__main__":
    main()
