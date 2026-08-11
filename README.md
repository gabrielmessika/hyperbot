# HyperBot

HyperBot est un projet indépendant de recherche et d'automatisation de market
making sur Hyperliquid, conçu pour tester des edges mesurables sans confondre
backtest et performance exécutable.

La source de vérité initiale est
[`HYPERBOT_FOUNDATION.md`](HYPERBOT_FOUNDATION.md). Elle décrit l'architecture,
les hypothèses de rendement, les limites de risque et les critères de promotion
du replay vers le shadow puis le canary.

Le statut d'implémentation et les prochains lots sont suivis dans
[`FOLLOW_UP.md`](FOLLOW_UP.md).

## État actuel

- phase : instrumentation locale et replay, lots M1 à M4 terminés côté logiciel ;
- trading live : désactivé ;
- livré : contrats d'événements, configuration fail-closed, import legacy,
  catalogue public, collector WebSocket et store segmenté intègre ;
- prochaine priorité : recherche d'edge M5 pendant l'accumulation réelle des
  gates qualité M3 sur 7 puis 30 jours ;
- dépôt TRIDENT : référence historique uniquement.

## Données historiques

HyperBot peut utiliser les archives TRIDENT en lecture seule pour démarrer les
replays de fair value, spreads, markouts et benchmarks. Chaque import conserve
sa provenance et son checksum. Ces archives ne remplacent pas les données du
nouveau collector pour simuler la position de file ou autoriser un canary.

La politique complète et le lot d'import `M1L` sont décrits dans
[`FOLLOW_UP.md`](FOLLOW_UP.md).

## Démarrage

```bash
uv sync
uv run pytest
uv run ruff check .
```

Un snapshot du catalogue public M2 se lance sans secret :

```bash
uv run python scripts/snapshot_market_catalog.py
```

Une session bornée du collector public peut ensuite être lancée explicitement :

```bash
uv run python scripts/run_public_collector.py \
  --duration-seconds 10 --coin BTC --coin 'cash:AMZN'
```

Ces commandes n'importent aucun client de signature et ne savent envoyer que
des subscriptions publiques et des heartbeats. Les données brutes sont écrites
dans `data/raw/`, ignoré par Git.

Le rapport qualité d'une journée collectée se génère avec la whitelist exacte :

```bash
uv run python scripts/report_data_quality.py \
  --date 2026-08-11 --market BTC --market 'cash:AMZN'
```

Une capture courte reste volontairement non qualifiée : la gate M3 demande sept
jours UTC consécutifs, puis trente jours de preuve A.

Un replay M4 reproductible et ses stress se lancent sur un fixture explicite :

```bash
uv run python scripts/run_hyperbot_replay.py \
  tests/fixtures/m4/replay_input.json --stress
```

Les modèles central et pessimiste refusent les données B/C ; seul
`optimistic_touch` peut les utiliser avec son label de borne optimiste.

L'inventaire legacy M1L.1 se génère localement, sans écrire dans TRIDENT :

```bash
uv run python scripts/inventory_legacy_data.py
```

Il produit un manifest JSON déterministe, un résumé Markdown et leurs checksums
dans `reports/legacy_inventory/`. Les cadences et trous de ce rapport sont des
inférences de qualité de données, jamais des preuves de fills maker.

L'import normalisé et son rapport de couverture se relancent avec :

```bash
uv run python scripts/import_legacy_data.py
```

Les événements dérivés restent dans `data/legacy_imports/` et portent tous leur
provenance B/C ainsi que `legacy_research_only`. La politique M1L.3 bloque leur
utilisation pour la file exacte, les fills central/pessimiste, la rentabilité
live ou une promotion canary.

Ouvrir `hyperbot.code-workspace` pour charger HyperBot avec TRIDENT comme dossier
de référence séparé.
