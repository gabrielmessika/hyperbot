# Import et couverture legacy HyperBot

> Tous les événements de ce rapport restent `legacy_research_only`.

- Run : `legacy-import-b674044f8ed8848f`
- Manifest source : `89d201eb1a4a9b3d406069357be3d33a9e00ce612e78467b80cfc1c2f049a463`
- Fichiers : 58
- Records acceptés : 1430010
- Records rejetés : 195157
- Événements émis : 1559718
- Taille dérivée : 1.7 Gio
- Flux d'erreurs : 93.0 Mio

## Événements normalisés

| Type | Nombre |
|---|---:|
| LegacyBookObservation | 1196480 |
| LegacyFeatureObservation | 14532 |
| LegacyQuoteObservation | 145387 |
| LegacySettlementObservation | 99 |
| LegacyTradeObservation | 203220 |

## Records rejetés

| Motif | Nombre |
|---|---:|
| crossed_book | 195157 |

## Couverture par dataset

| Dataset | Niveau | Connu | Approximé | Absent |
|---|---:|---|---|---|
| gbot_microstructure | C | BBO, profondeur agrégée et trades du 1er avril | activité et markouts sur une fenêtre courte | carnet L2 complet, continuité longue et fills maker |
| hip4_nautilus_books | B | BBO, profondeur agrégée, marché et timestamps publiés | cadence et trous temporels inférés | diffs L2 complets, volume devant une quote et position de file |
| hip4_paper | B | observations, quotes shadow, trades paper et settlements | markouts et reproduction des décisions historiques | ACK/fills maker réels et position de file vérifiable |
| trident_live_snapshots | C | petits snapshots dispersés utilisables comme fixtures | compatibilité de schéma uniquement | continuité, complétude et preuve d'exécution |
| trident_replay_sample | C | features agrégées et snapshots directionnels historiques | régimes et compatibilité de schéma | microstructure de file et preuve d'exécution maker |

## Politique de preuve M1L.3

| Usage | Autorisé avec B/C | Label obligatoire | Motif |
|---|---:|---|---|
| fair_value | oui | legacy_research_only | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| spread_analysis | oui | legacy_research_only | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| aggregated_depth | oui | legacy_research_only | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| markout_analysis | oui | legacy_research_only | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| outcome_parity | oui | legacy_research_only | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| market_selection | oui | legacy_research_only | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| stale_detection | oui | legacy_research_only | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| legacy_bot_reproduction | oui | legacy_research_only | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| optimistic_touch | oui | legacy_research_only_optimistic_touch | les données B/C sont admises uniquement pour la recherche ou comme borne optimiste |
| exact_queue_position | non | — | les données B/C ne prouvent ni file exacte, ni fills centraux/pessimistes, ni rentabilité live, ni promotion |
| partial_maker_fills | non | — | les données B/C ne prouvent ni file exacte, ni fills centraux/pessimistes, ni rentabilité live, ni promotion |
| central_fill_model | non | — | les données B/C ne prouvent ni file exacte, ni fills centraux/pessimistes, ni rentabilité live, ni promotion |
| pessimistic_fill_model | non | — | les données B/C ne prouvent ni file exacte, ni fills centraux/pessimistes, ni rentabilité live, ni promotion |
| live_profitability_claim | non | — | le niveau de données ne suffit pas : les gates OOS, risque et autorisation séparée restent obligatoires |
| canary_promotion | non | — | le niveau de données ne suffit pas : les gates OOS, risque et autorisation séparée restent obligatoires |

## Résultats par fichier

| Source | Niveau | Fichier | Adaptateur | Acceptés | Rejetés | Émis |
|---|---:|---|---|---:|---:|---:|
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/ARB/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5522 | 15989 | 5522 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/AVAX/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5525 | 15986 | 5525 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/BTC/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5547 | 17106 | 5547 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/DOGE/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5506 | 16005 | 5506 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/ETH/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5531 | 17119 | 5531 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/HYPE/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5504 | 16006 | 5504 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/LINK/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5517 | 15994 | 5517 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/NEAR/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5582 | 15929 | 5582 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/OP/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5573 | 15938 | 5573 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/SOL/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5569 | 17081 | 5569 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/SUI/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5512 | 15998 | 5512 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/l2/XRP/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 5504 | 16006 | 5504 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/ARB/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 900 | 0 | 900 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/AVAX/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 2037 | 0 | 2037 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/BTC/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 101016 | 0 | 101016 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/DOGE/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 1983 | 0 | 1983 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/ETH/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 32860 | 0 | 32860 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/HYPE/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 32054 | 0 | 32054 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/LINK/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 2061 | 0 | 2061 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/NEAR/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 741 | 0 | 741 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/OP/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 653 | 0 | 653 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/SOL/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 22906 | 0 | 22906 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/SUI/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 1954 | 0 | 1954 |
| gbot_microstructure | C | `/workspaces/trident/data/gbot_archive/trades/XRP/2026-04-01.jsonl` | LegacyGbotAdapter@1.0.1 | 3953 | 0 | 3953 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-04-05.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 7 | 0 | 16 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-04-23.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 7 | 0 | 7 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-04-24.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 4 | 0 | 4 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-04-27.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 9 | 0 | 9 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-04-29.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 13 | 0 | 20 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-01.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-02.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 7 | 0 | 7 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-03.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 2 | 0 | 2 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-05.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 3 | 0 | 3 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-13.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 8 | 0 | 8 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-16.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-17.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 3 | 0 | 3 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-26.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 5 | 0 | 5 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-27.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 3 | 0 | 3 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-05-29.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 3 | 0 | 3 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-02.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-05.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-07.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-09.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-10.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-12.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-14.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 6 | 0 | 6 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-15.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 3 | 0 | 3 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-18.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-22.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 6 | 0 | 6 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-23.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 12 | 0 | 12 |
| trident_live_snapshots | C | `/workspaces/trident/data/live_snapshots/2026-06-24.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 1 | 0 | 1 |
| hip4_nautilus_books | B | `/workspaces/trident/server-data/hip4/logs/hip4_nautilus_shadow/archive_before_nautilus_ws_clean_20260527T081334Z/book_snapshots.jsonl` | LegacyHip4Adapter@1.0.1 | 1260 | 0 | 1260 |
| hip4_nautilus_books | B | `/workspaces/trident/server-data/hip4/logs/hip4_nautilus_shadow/book_snapshots.jsonl` | LegacyHip4Adapter@1.0.1 | 892492 | 0 | 892492 |
| hip4_paper | B | `/workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/market_observations.jsonl` | LegacyHip4Adapter@1.0.1 | 118168 | 0 | 236336 |
| hip4_paper | B | `/workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/settlements.csv` | LegacyHip4Adapter@1.0.1 | 99 | 0 | 99 |
| hip4_paper | B | `/workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/shadow_maker_quotes.csv` | LegacyHip4Adapter@1.0.1 | 145387 | 0 | 145387 |
| hip4_paper | B | `/workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/trades.csv` | LegacyHip4Adapter@1.0.1 | 102 | 0 | 102 |
| trident_replay_sample | C | `/workspaces/trident/server-data/replay_inputs/special_symbols_hl_15m_30d_20260419.jsonl` | LegacyTridentSnapshotAdapter@1.0.0 | 2881 | 0 | 14405 |

## Limites

Les événements normalisés conservent une référence vérifiable vers la source et n'ajoutent aucun champ absent. Les tailles inconnues restent nulles. Les profondeurs GBOT/HIP-4 sont agrégées et ne reconstruisent pas la file. Les snapshots TRIDENT restent des features historiques.

Les données B/C peuvent alimenter fair value, spreads, profondeur agrégée, markouts, parité, détection stale, reproduction historique et la borne `optimistic_touch`. Elles ne peuvent valider ni position de file, ni fills maker partiels, ni modèles central/pessimiste, ni rentabilité live, ni promotion canary.
