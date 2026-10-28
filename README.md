# Quant Lab — NQ / MNQ

Recherche et suivi de stratégies intraday sur le Nasdaq 100 (bougies 5 minutes), avec un dashboard
autonome : une seule page HTML, sans installation ni dépendance.

> Projet personnel de recherche. Les résultats sont des backtests et des simulations : ils ne garantissent
> pas les résultats futurs. Rien ici n'est un conseil d'investissement.

## Stratégies

| Stratégie | Statut |
|---|---|
| **ORB 5 minutes** | Règles figées le 2026-10-28, en test réel. Voir [`orb_rules.md`](orb_rules.md) |
| Expansion milieu de séance | Abandonnée : ne se confirme pas en 2021-2026 |
| Post-Displacement (1H) | Archivée : PF 1,13 en walk-forward |

## Le dashboard

`python dashboard.py` recalcule tout (environ 1 minute) et ouvre `dashboard.html` :

- les stratégies dans la barre latérale (onglets sur téléphone) ;
- les chiffres clés par période (2010-2020 / 2021-2026) ;
- le calendrier mensuel du P&L en R, backtest ou paper trading ;
- la courbe du backtest et le suivi du paper trading face à la fourchette attendue (5 %-95 %) ;
- les trades récents ;
- le simulateur LucidFlex 25K (réussite de l'évaluation, causes d'échec, 60 trajectoires, valeur par tentative).

Options : `--mc 2000` (simulation plus rapide), `--risks 100,150,200`, `--out autre.html`, `--no-open`.

La mise en page est dans [`dashboard_template.html`](dashboard_template.html) : `dashboard.py` y remplace le
marqueur de données par le JSON calculé. On peut modifier le design librement sans toucher aux calculs.
Les graphiques sont en SVG, dessinés par la page elle-même ; la barre du haut utilise un effet « verre liquide »
discret (Chrome, Edge, Samsung Internet ; les autres navigateurs gardent un simple flou).

## Fichiers

| Fichier | Rôle |
|---|---|
| `dashboard.py` | Calcule les séries, statistiques, fourchette attendue et simulation Lucid, puis génère la page |
| `dashboard_template.html` | Design du dashboard |
| `orb_rules.md` | Règles figées de l'ORB et ce qu'on peut attendre du test réel |
| `orb_daily.py` | Suivi quotidien de l'ORB (niveaux, taille) et journal `forward_test_log.csv` (`--review`) |
| `orb_strategy.py` | Backtest ORB paramétrable, walk-forward, simulation prop firm |
| `midday_expansion.py` | Stratégie d'expansion en milieu de séance + ORB, simulation LucidFlex |
| `giant_candles.py` | Étude d'événements après une bougie 5 minutes géante |
| `fetch_histdata.py` | Téléchargement NQ 1 min HistData, conversion en 5 min UTC |
| `fetch_alphavantage.py` | Téléchargement QQQ 5 min Alpha Vantage (25 requêtes / jour) |
| `trades_orb_fixed.csv`, `trades_orb_walk_forward.csv` | Résultats exportés par `orb_strategy.py` |

## Installation

Python 3.9 ou plus, bibliothèque standard uniquement.

```bash
python fetch_histdata.py                 # données NQ 5 min dans data/
python dashboard.py                      # génère et ouvre dashboard.html
```

Fichiers attendus dans `data/` : `nq_5m_histdata_2010_2020.csv` et `nq_5m_histdata.csv`.
La stratégie Post-Displacement n'apparaît que si `01_Post_Displacement_Archive/trades/trades_walk_forward_dukascopy.csv` est présent.
Pour Alpha Vantage, mettre la clé dans la variable `ALPHAVANTAGE_KEY` ou dans `data/.av_key`.

Le dossier `data/` (données et clé API) et `dashboard.html` ne sont **pas** versionnés (voir `.gitignore`).
Le journal du test réel `forward_test_log.csv`, lui, est versionné.

## Suivi quotidien de l'ORB

```bash
python orb_daily.py            # après 15h40 Paris : niveaux et taille du jour
python orb_daily.py --review   # au moins une fois par mois : complète le journal
python dashboard.py            # met à jour le dashboard
```

Dans `forward_test_log.csv`, noter dans `reel_pts` le résultat réellement obtenu (en points) pour mesurer le glissement.
