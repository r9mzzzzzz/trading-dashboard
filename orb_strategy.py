"""
Backtest « Opening Range Breakout » (ORB) sur la session US du Nasdaq — Python pur.
Données : CSV 5 minutes (Dukascopy : timestamp en ms UTC, ou time,open,high,low,close,volume).

Règles (une position max par jour, toujours clôturée le jour même) :
  1. Opening range (OR) = plus haut / plus bas des M premières minutes après 9h30 New York
     (15h30 heure de Paris, sauf pendant les décalages d'heure d'été).
  2. Entrée stop : la première bougie qui dépasse le haut de l'OR -> ACHAT au niveau cassé
     (ou à l'ouverture si gap au-delà) ; idem en vente sous le bas. Pas d'entrée après --last-entry.
     Si une même bougie casse les deux côtés, la journée est ignorée (ordre d'exécution inconnu).
  3. Stop = haut de l'OR - F x largeur de l'OR (F=1 : côté opposé, F=0.5 : milieu) ; miroir en vente.
  4. Cible = RR x risque, ou aucune cible (RR=0) : sortie à la clôture de la bougie 15h55 New York.
  Hypothèses prudentes : sur la bougie d'entrée seul le stop est testé ; stop et cible
  sur la même bougie -> stop.

Walk-forward : sur chaque fenêtre IS, choix de la combinaison (M, F, RR) au meilleur R total,
appliquée à la fenêtre OOS suivante.

Simulation Prop Firm 25K (Monte Carlo sur les trades OOS) :
  objectif +1 250 $, perte max 1 000 $ trailing sur le plus haut solde de fin de journée
  (bloquée au solde de départ), perte journalière 600 $ (liquidation), consistance 40 %
  (le meilleur jour <= 40 % du gain total), max 20 micros. Taille = risque $ / (stop x 2 $ par point MNQ).

Usage :
  python orb_strategy.py --csv data/nq_5m_dukascopy.csv
  python orb_strategy.py --csv data/nq_5m_dukascopy.csv --risk-usd 150 --mc 20000
"""
import argparse
import csv
import math
import random
import statistics as st
from datetime import datetime, timedelta, timezone

MONTH = 30.44  # jours


# ---------- Données ----------

def us_dst(dt_utc):
    """Heure d'été US : du 2e dimanche de mars 2h00 au 1er dimanche de novembre 2h00 (heure locale)."""
    y = dt_utc.year
    mar1, nov1 = datetime(y, 3, 1, tzinfo=timezone.utc), datetime(y, 11, 1, tzinfo=timezone.utc)
    start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)   # 2h00 EST = 7h00 UTC
    end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)         # 2h00 EDT = 6h00 UTC
    return start <= dt_utc < end


def load_sessions(path, tz="utc", shift_min=0):
    """Retourne {date New York: [bougies 9h30 -> 15h55]} avec 'm' = minutes depuis minuit New York.
    tz="ny" : horodatage déjà en heure de New York (Alpha Vantage).
    shift_min : décalage à appliquer si les bougies sont horodatées à leur fin (-5 pour du 5 min)."""
    days = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            r = {k.strip().lower(): v for k, v in r.items()}
            t = r.get("time") or r["timestamp"]
            if t.replace(".", "").isdigit():
                t = int(float(t))
                t = t // 1000 if t > 10**11 else t
                utc = datetime.fromtimestamp(t, timezone.utc)
            else:
                utc = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
            if tz == "ny":
                ny = utc + timedelta(minutes=shift_min)
            else:
                ny = utc + timedelta(hours=-4 if us_dst(utc) else -5, minutes=shift_min)
            m = ny.hour * 60 + ny.minute
            if ny.weekday() >= 5 or not 570 <= m < 960:     # 9h30 -> 16h00, lundi -> vendredi
                continue
            days.setdefault(ny.date().isoformat(), []).append(
                {"m": m, "o": float(r["open"]), "h": float(r["high"]),
                 "l": float(r["low"]), "c": float(r["close"])})
    return {d: sorted(b, key=lambda x: x["m"]) for d, b in sorted(days.items())}


# ---------- Stratégie ----------

def trade_day(date, bars, or_min, stop_frac, rr, last_entry, cost):
    n_or = or_min // 5
    if len(bars) < n_or + 2 or bars[0]["m"] != 570 or bars[n_or - 1]["m"] != 570 + or_min - 5:
        return None                                          # séance incomplète
    orh = max(b["h"] for b in bars[:n_or])
    orl = min(b["l"] for b in bars[:n_or])
    width = orh - orl
    if width <= 0:
        return None
    for k in range(n_or, len(bars)):
        b = bars[k]
        if b["m"] >= last_entry:
            return None
        up, dn = b["h"] > orh, b["l"] < orl
        if up and dn:
            return None
        if up or dn:
            break
    else:
        return None
    side = 1 if up else -1
    entry = max(orh, b["o"]) if side == 1 else min(orl, b["o"])
    stop = orh - stop_frac * width if side == 1 else orl + stop_frac * width
    risk = (entry - stop) * side
    if risk <= 0:
        return None
    target = entry + side * rr * risk if rr else None

    exit_px, why, mae = None, None, 0.0
    for j in range(k, len(bars)):
        x = bars[j]
        adverse = (entry - x["l"]) if side == 1 else (x["h"] - entry)
        mae = max(mae, min(adverse, risk))
        hit_stop = x["l"] <= stop if side == 1 else x["h"] >= stop
        hit_tgt = j > k and target is not None and (x["h"] >= target if side == 1 else x["l"] <= target)
        if hit_stop:
            exit_px, why = stop, "stop"
            break
        if hit_tgt:
            exit_px, why = target, "cible"
            break
    if exit_px is None:
        exit_px, why = bars[-1]["c"], "clôture"
    pnl = (exit_px - entry) * side - cost
    return {"date": date, "side": "LONG" if side == 1 else "SHORT", "or_width": width,
            "entry": entry, "stop": stop, "exit": exit_px, "why": why,
            "risk_pts": risk, "mae_pts": mae + cost, "pnl_pts": pnl, "R": pnl / risk}


def backtest(days, combo, p):
    or_min, stop_frac, rr = combo
    out = []
    for d, bars in days.items():
        t = trade_day(d, bars, or_min, stop_frac, rr, p.last_entry, p.cost_pts)
        if t:
            out.append(t)
    return out


# ---------- Statistiques ----------

def stats(ts):
    if not ts:
        return None
    rs = [t["R"] for t in ts]
    gains, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": len(rs), "win": sum(r > 0 for r in rs) / len(rs), "avgR": sum(rs) / len(rs),
            "totR": sum(rs), "pf": gains / losses if losses else float("inf"), "dd": dd}


HEADER = f"{'':<26}{'N':>5}{'Win%':>8}{'R moy':>8}{'R tot':>8}{'PF':>7}{'MaxDD R':>9}"


def row(label, s):
    if not s:
        return f"{label:<26}{'aucun trade':>12}"
    return (f"{label:<26}{s['n']:>5}{s['win']*100:>7.1f}%{s['avgR']:>8.3f}{s['totR']:>8.1f}"
            f"{s['pf']:>7.2f}{s['dd']:>9.1f}")


def by_year(trades, title):
    print(f"\n=== {title} ===")
    print(HEADER)
    for y in sorted({t["date"][:4] for t in trades}):
        ts = [t for t in trades if t["date"][:4] == y]
        print(row(y, stats(ts)))
        for side in ("LONG", "SHORT"):
            print(row(f"  {side}", stats([t for t in ts if t["side"] == side])))


def label(c):
    return f"OR {c[0]}m, stop {c[1]}, " + (f"RR {c[2]}" if c[2] else "clôture")


# ---------- Walk-forward ----------

def walk_forward(days, combos, p):
    runs = {c: backtest(days, c, p) for c in combos}
    dates = list(days)
    d0, d_end = datetime.fromisoformat(dates[0]), datetime.fromisoformat(dates[-1])
    pick = lambda ts, a, b: [t for t in ts if a <= t["date"] < b]
    iso = lambda d: d.date().isoformat()
    chain, start = [], d0
    print(f"\n=== Walk-Forward : IS {p.wf_train} mois / OOS {p.wf_test} mois | {len(combos)} combinaisons ===")
    print(f"{'Test OOS':<25}{'Réglage choisi':<28}{'IS R':>7}{'OOS N':>7}{'OOS R':>8}{'Cumul':>8}")
    while True:
        is_end = start + timedelta(days=p.wf_train * MONTH)
        oos_end = is_end + timedelta(days=p.wf_test * MONTH)
        if is_end >= d_end:
            break
        scores = {c: stats(pick(runs[c], iso(start), iso(is_end))) for c in combos}
        ok = [c for c in combos if scores[c] and scores[c]["n"] >= p.min_is_trades]
        if ok:
            best = max(ok, key=lambda c: (scores[c]["totR"], scores[c]["pf"]))
            oos = pick(runs[best], iso(is_end), iso(oos_end))
            chain += oos
            print(f"{iso(is_end)} -> {iso(min(oos_end, d_end)):<12}{label(best):<28}"
                  f"{scores[best]['totR']:>+7.1f}{len(oos):>7}{sum(t['R'] for t in oos):>+8.1f}"
                  f"{sum(t['R'] for t in chain):>+8.1f}")
        start += timedelta(days=p.wf_test * MONTH)
    return chain


# ---------- Simulation Prop Firm ----------

def prop_sim(trades, p, risk_usd, shift_r=0.0):
    """Taux de réussite de l'évaluation, en tirant des trades OOS au hasard (avec remise).
    shift_r retire un edge de shift_r R par trade (pour la référence « sans edge »)."""
    pv, wins, fails, needed = 2.0 * p.point_scale, 0, 0, []  # $ par point de la donnée, en MNQ
    for _ in range(p.mc):
        bal = hw = best_day = 0.0
        for k in range(1, p.mc_max_trades + 1):
            t = random.choice(trades)
            n = min(p.max_micros, max(1, math.floor(risk_usd / (t["risk_pts"] * pv))))
            pnl = (t["pnl_pts"] - shift_r * t["risk_pts"]) * n * pv
            low = -t["mae_pts"] * n * pv
            pnl, low = max(pnl, -p.daily_loss), max(min(low, pnl), -p.daily_loss)
            floor = min(hw, p.max_loss) - p.max_loss    # trailing EOD, bloqué au solde de départ
            if bal + low <= floor:
                fails += 1
                break
            bal += pnl
            best_day = max(best_day, pnl)
            hw = max(hw, bal)
            if bal >= p.target and best_day <= p.consistency * bal:
                wins += 1
                needed.append(k)
                break
    return wins / p.mc, fails / p.mc, (st.median(needed) if needed else None)


def funded_sim(trades, p, risk_usd, shift_r=0.0):
    """Phase financée LucidFlex 25K, retraits pris dès qu'ils sont possibles.
    MLL trailing EOD 1 000 $ bloqué à +100 $ (solde > +1 100 $ ou premier retrait), perte journalière
    600 $, scaling 10 micros sous 1 000 $ de profit puis 20, retrait : 5 jours >= 100 $ dans le cycle,
    cycle net > 0, montant = min(50 % du profit, 1 000 $) >= 500 $, 90 % pour le trader, 5 retraits max.
    Retourne (gain moyen encaissé, probabilité d'au moins un retrait)."""
    pv, total, paid_once = 2.0 * p.point_scale, 0.0, 0
    for _ in range(p.mc):
        bal = hw = cycle = 0.0
        good_days, payouts, locked, cash = 0, 0, False, 0.0
        for _k in range(p.mc_max_trades):
            t = random.choice(trades)
            cap = 10 if bal < 1000 else 20
            n = min(cap, max(1, math.floor(risk_usd / (t["risk_pts"] * pv))))
            pnl = (t["pnl_pts"] - shift_r * t["risk_pts"]) * n * pv
            low = -t["mae_pts"] * n * pv
            pnl, low = max(pnl, -p.daily_loss), max(min(low, pnl), -p.daily_loss)
            floor = 100.0 if locked else min(hw, 1100) - 1000
            if bal + low <= floor:
                break
            bal += pnl
            cycle += pnl
            hw = max(hw, bal)
            locked = locked or hw >= 1100
            good_days += pnl >= 100
            amount = min(0.5 * bal, 1000)
            if good_days >= 5 and cycle > 0 and amount >= 500:
                cash += 0.9 * amount
                bal -= amount
                payouts += 1
                locked, good_days, cycle = True, 0, 0.0
                if payouts == 5:
                    break
        total += cash
        paid_once += cash > 0
    return total / p.mc, paid_once / p.mc


def lucid_report(trades, p, per_month):
    mean_r = st.mean(t["R"] for t in trades)
    print(f"\n=== LucidFlex 25K : évaluation + phase financée ({p.mc} tirages, ~{per_month:.0f} trades/mois) ===")
    print(f"Évaluation : +{p.target:.0f} $, perte max {p.max_loss:.0f} $ EOD, journalière {p.daily_loss:.0f} $, "
          f"consistance {p.consistency:.0%} | prix {p.fee:.0f} $")
    print(f"{'Risque':<8}{'Réussite':>9}{'Durée':>9}{'Gain financé':>14}{'1 retrait+':>12}"
          f"{'Valeur/tentative':>18}{'Sans edge':>12}")
    for risk in [float(x) for x in p.risk_usd.split(",")]:
        ok, _, n = prop_sim(trades, p, risk)
        cash, once = funded_sim(trades, p, risk)
        base_ok, _, _ = prop_sim(trades, p, risk, shift_r=mean_r)
        base_cash, _ = funded_sim(trades, p, risk, shift_r=mean_r)
        dur = f"{n / per_month:.1f} m" if n else "-"
        print(f"{risk:>5.0f} $ {ok*100:>8.1f}%{dur:>9}{cash:>13.0f} ${once*100:>11.1f}%"
              f"{ok * cash - p.fee:>+16.0f} ${base_ok * base_cash - p.fee:>+10.0f} $")


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/nq_5m_dukascopy.csv")
    ap.add_argument("--cost-pts", type=float, default=1.5, help="frais + slippage aller-retour, en points")
    ap.add_argument("--last-entry", type=int, default=12 * 60, help="pas d'entrée après (minutes NY), 720 = 12h00")
    ap.add_argument("--or-mins", default="5,15,30")
    ap.add_argument("--stop-fracs", default="0.5,1.0")
    ap.add_argument("--rrs", default="1.5,2,0", help="0 = sortie à la clôture")
    ap.add_argument("--wf-train", type=float, default=6)
    ap.add_argument("--wf-test", type=float, default=2)
    ap.add_argument("--min-is-trades", type=int, default=30)
    ap.add_argument("--risk-usd", default="100,150,200", help="risque par trade testé dans la simulation prop")
    ap.add_argument("--mc", type=int, default=10000)
    ap.add_argument("--mc-max-trades", type=int, default=1500)
    ap.add_argument("--target", type=float, default=1250)
    ap.add_argument("--max-loss", type=float, default=1000)
    ap.add_argument("--daily-loss", type=float, default=600)
    ap.add_argument("--consistency", type=float, default=0.4)
    ap.add_argument("--max-micros", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tz", choices=["utc", "ny"], default="utc", help="fuseau des horodatages du CSV")
    ap.add_argument("--shift-min", type=int, default=0, help="-5 si les bougies sont horodatées à leur fin")
    ap.add_argument("--point-scale", type=float, default=1.0,
                    help="points NQ par point de la donnée (QQQ -> NQ : ~41) pour la simulation en MNQ")
    ap.add_argument("--fee", type=float, default=39, help="prix de l'évaluation (LucidFlex 25K avec coupon)")
    ap.add_argument("--fixed", help="teste un seul réglage figé, ex. 5,1.0,0 (OR min, stop, RR), sans walk-forward")
    p = ap.parse_args()
    random.seed(p.seed)

    days = load_sessions(p.csv, p.tz, p.shift_min)
    dates = list(days)
    print(f"Source : {p.csv} | {len(days)} séances US | {dates[0]} -> {dates[-1]} | coût {p.cost_pts} pts")

    if p.fixed:
        m, f, r = p.fixed.split(",")
        combo = (int(m), float(f), float(r))
        trades = backtest(days, combo, p)
        print(f"\n=== Réglage figé : {label(combo)} ===")
        print(HEADER)
        for side in ("LONG", "SHORT", "ALL"):
            print(row(side, stats([t for t in trades if side == "ALL" or t["side"] == side])))
        by_year(trades, "Par année")
        with open("trades_orb_fixed.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(trades[0].keys()))
            w.writeheader()
            w.writerows(trades)
        lucid_report(trades, p, len(trades) / len(days) * 21)
        print("\nDétail des trades : trades_orb_fixed.csv")
        return

    combos = [(int(m), float(f), float(r)) for m in p.or_mins.split(",")
              for f in p.stop_fracs.split(",") for r in p.rrs.split(",")]
    print(f"\n=== Grille sur tout l'historique (indicatif, biaisé : réglages vus) ===")
    print(HEADER)
    for c in combos:
        print(row(label(c), stats(backtest(days, c, p))))

    chain = walk_forward(days, combos, p)
    print(f"\n=== Chaîne OOS cumulée ===")
    print(HEADER)
    for side in ("LONG", "SHORT", "ALL"):
        print(row(side, stats([t for t in chain if side == "ALL" or t["side"] == side])))
    by_year(chain, "Chaîne OOS par année")

    if chain:
        with open("trades_orb_walk_forward.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(chain[0].keys()))
            w.writeheader()
            w.writerows(chain)

        oos_days = len([d for d in dates if d >= chain[0]["date"]])
        lucid_report(chain, p, len(chain) / oos_days * 21)
        print("\nDétail des trades OOS : trades_orb_walk_forward.csv")


if __name__ == "__main__":
    main()
