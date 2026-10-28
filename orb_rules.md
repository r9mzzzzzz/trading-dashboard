# ORB 5 minutes — règles figées

Figées le 2026-10-28. **Ne rien modifier pendant le test en réel** : toute modification remet le compteur à zéro.

## Règles

| | |
|---|---|
| Instrument | MNQ (Micro E-mini Nasdaq 100), contrat le plus actif |
| Opening range (OR) | plus haut / plus bas de la bougie **9h30-9h35 New York** (15h30-15h35 Paris, 14h30-14h35 Paris pendant les ~3 semaines où les heures d'été ne sont pas alignées) |
| Entrée | deux ordres **stop** en OCO : achat 1 tick au-dessus du haut de l'OR, vente 1 tick sous le bas. Le premier exécuté annule l'autre |
| Fin des entrées | si rien n'est déclenché à **12h00 New York**, annuler les deux ordres : pas de trade ce jour-là |
| Stop | l'autre côté de l'OR (achat : stop au bas de l'OR ; vente : stop au haut de l'OR) |
| Cible | aucune |
| Sortie | **clôture de la bougie 15h55 New York** (21h55 Paris), si le stop n'a pas été touché |
| Un trade par jour | jamais de seconde entrée, même si le stop est touché tôt |
| Jours ignorés | une même bougie casse les deux côtés avant l'entrée → pas de trade |
| Taille | `micros = plancher(100 $ / (largeur OR en points x 2 $))`, minimum 1, maximum 10 (scaling LucidFlex sous 1 000 $ de profit) |

Le risque réel par trade = largeur de l'OR x 2 $ x nombre de micros, plus les frais. Avec un OR médian de 52 points, cela fait
environ 105 $ pour 1 micro. Si l'OR dépasse 100 points (environ 10 % des jours), 1 micro risque plus de 200 $ :
la règle backtestée **prend quand même le trade** avec 1 micro.

## Ce que dit le backtest (HistData, coûts = 0,0075 % du prix, environ 1,5 point de NQ)

| Période | Trades | Trades gagnants | R moyen | PF |
|---|---|---|---|---|
| 2010-2020 | 2 431 | 28 % | +0,11 | 1,15 |
| 2021-2026 | 1 355 | 31 % | +0,13 | 1,19 |

- Environ **19 trades par mois** (un trade 90 % des séances).
- **Plus longue série perdante observée : 14 trades d'affilée.** C'est normal pour cette stratégie, pas un signal d'arrêt.
- Années perdantes : 2010 (partielle), 2020. Années quasi nulles : 2017, 2023.

## À quoi s'attendre en réel (tirages des trades 2021-2026)

| Après | 5 % des cas, pire que | Médiane | 5 % des cas, mieux que | Probabilité d'être positif |
|---|---|---|---|---|
| 20 trades (~1 mois) | −11 R | +2 R | +19 R | 59 % |
| 40 trades (~2 mois) | −15 R | +5 R | +28 R | 65 % |
| 60 trades (~3 mois) | −17 R | +7 R | +35 R | 68 % |

**Ce que le test de 1 à 2 mois peut prouver, et ce qu'il ne peut pas prouver :**
- Il ne prouvera **pas** que l'edge existe : même avec un vrai edge, 1 test sur 3 est négatif après 2 mois.
- Il sert à vérifier **l'exécution** : que vous arrivez à placer les ordres à l'heure, que le glissement moyen reste
  **≤ 1,5 point par trade aller-retour**, et que les résultats restent dans la fourchette ci-dessus.
- **Arrêter et revoir** si : le glissement moyen dépasse 2,5 points, ou le résultat après 40 trades est pire que −15 R.

## Lucid (LucidFlex 25K, 39 $)

Simulation sur 2021-2026 au risque de 100 $ : environ **31 % de réussite de l'évaluation**, environ 600 $ encaissés en
moyenne une fois financé, valeur estimée **environ +146 $ par tentative**. Environ 2 tentatives sur 3 échouent :
prévoir le budget de plusieurs tentatives. Ce n'est pas un conseil d'investissement.

Vérifier avant de trader : l'heure limite de clôture des positions chez Lucid, les jours de séance raccourcie
(veille de Thanksgiving, 24 décembre…) et les restrictions éventuelles autour des annonces.

## Suivi

`python orb_daily.py` après 15h40 Paris : niveaux et taille du jour.
`python orb_daily.py --review` quand vous voulez (au moins une fois par mois, Yahoo garde ~60 jours) : ajoute au
journal `forward_test_log.csv` toutes les séances terminées depuis le 2026-10-29 qui n'y sont pas encore.
Notez dans la colonne `reel_pts` le résultat réellement obtenu (en points), pour mesurer le glissement.
`python dashboard.py` : régénère et ouvre `dashboard.html` (calendrier, courbes, trades, simulateur Lucid).
