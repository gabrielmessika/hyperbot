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

- phase : instrumentation à shadow, lots M1 à M7 et M7-Ops terminés côté logiciel ;
- trading live : désactivé ;
- livré : contrats d'événements, configuration fail-closed, import legacy,
  catalogue public, collector WebSocket et store segmenté intègre ;
- prochaine priorité : accumuler réellement les gates M3, M5 et M7 ; M8 reste
  bloqué sans review et autorisation séparées ;
- dépôt TRIDENT : référence historique uniquement.

## Déploiement M7-Ops

M7-Ops fournit un déploiement séparé sous `/opt/hyperbot`, désactivé par défaut.
Le premier déploiement construit l'image et installe un `.env.hyperbot` sûr sans
démarrer de service :

```bash
./deploy.sh --host trident-hetzner
```

Après revue de la whitelist et activation explicite sur le serveur, les commandes
`scripts/hyperbot_server.sh` gèrent start/stop/status/health/logs/quality/catalog.
Ces contrôles restent exclusivement opérateur et ne sont jamais exposés dans
l'interface web.

M7-Ops inclut aussi une interface d'observation et une API strictement en lecture
seule sur le port public `3002`. Après activation et ouverture du firewall :

```text
http://<serveur>:3002/
```

L'authentification est obligatoire hors loopback. Le mot de passe aléatoire est
créé dans `/opt/hyperbot/shared/ui_password`, hors Git et hors `.env`. La route
`/health` est publique ; le dashboard et les routes `/api/*` sont protégés.
Les données publiques clôturées se rapatrient avec manifest et SHA-256 :

```bash
./scripts/fetch_hyperbot_data.sh --days 3
```

Le runbook complet, le smoke test et le rollback sont décrits dans
[`docs/m7_ops_runbook.md`](docs/m7_ops_runbook.md).

L'instance serveur a été activée le 11 août 2026. Le collector public et
l'observer sont healthy ; l'interface est
accessible sur `http://46.224.43.198:3002/`. Les gates M3/M5/M7 restent à
acquérir et aucun executor live n'est déployé.

La collecte utilise deux profils explicites : `depth` reçoit
`l2Book+bbo+trades` pour quatre marchés liquides et `breadth` reçoit uniquement
`bbo+trades` pour vingt marchés de screening. Cela conserve la preuve L2 utile
aux replays sans multiplier inutilement le stockage sur tout l'univers.
Un Volume de données dédié peut être protégé par
`HYPERBOT_DATA_MOUNT_SENTINEL` : tous les services refusent de démarrer si le
montage attendu n'est pas présent.

Le runtime supporte aussi un tier d'archive distinct : après vérification
cryptographique, les segments de plus de 30 jours peuvent quitter le Volume
chaud tout en restant rejouables depuis l'archive. Ce tier est fail-closed et
reste désactivé tant qu'un second montage avec sentinel n'est pas configuré.
Chaque collector serveur inscrit le SHA Git complet dans ses événements.

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

Une journée M3 v3 qualifiée peut être exportée puis transformée en dataset M4.
Le fetch restaure un snapshot immuable des manifests du store à côté des
segments vérifiés :

```bash
./scripts/fetch_hyperbot_data.sh --date 2026-08-16

uv run python scripts/build_collector_replay_dataset.py \
  --date 2026-08-16 \
  --market BTC \
  --data-root data/server-fetches/<fetch-id>/payload/data/raw/collector \
  --archive-root data/server-fetches/<fetch-id>/payload/archive/collector \
  --quality-report \
    data/server-fetches/<fetch-id>/payload/data/reviews/quality-2026-08-16-<run>.json
```

Le builder refuse une journée non qualifiée, un rapport sans checksum, un
mélange de commit/configuration/run, une provenance autre que A, une latence
négative ou l'absence de L2/BBO/trades. Le dataset obtenu contient les hashes du
rapport, du manifest et de chaque segment source. Le L2 alimente exclusivement
la preuve de file ; les BBO alimentent les midpoints de markout selon une lecture
`as-of` à fraîcheur bornée. Tous les événements conservent séparément temps
exchange et temps de réception : la génération des probes utilise uniquement
le temps de réception observable, tandis que la file et les fills suivent la
chronologie exchange du venue.

Le replay réel exige ensuite des hypothèses explicites. L'exemple suivant crée
des probes top-of-book espacées de cinq minutes pour mesurer la file et les
markouts ; ces probes ne constituent ni une stratégie ni une preuve d'edge :

```bash
uv run python scripts/run_hyperbot_replay.py \
  data/replay_datasets/<dataset>.json \
  --model central \
  --placement-latency-ms 350 \
  --cancel-latency-ms 350 \
  --maker-fee-bps 1.5 \
  --probe-interval-ms 300000 \
  --probe-ttl-ms 1000 \
  --probe-max-book-age-ms 500 \
  --probe-notional-usd 10 \
  --stress
```

Un replay M4 reproductible et ses stress se lancent sur un fixture explicite :

```bash
uv run python scripts/run_hyperbot_replay.py \
  tests/fixtures/m4/replay_input.json --stress
```

Les modèles central et pessimiste refusent les données B/C ; seul
`optimistic_touch` peut les utiliser avec son label de borne optimiste.

M5 fournit le benchmark digital, l'évaluation walk-forward purgée, le scanner
HIP-3 à coûts runtime et le journal hash-chaîné des variantes. Son statut
logiciel terminé ne constitue pas une validation d'edge : les gates OOS restent
négatives tant que les données A requises ne sont pas disponibles.

M6 sépare strictement stratégie, risque et exécution : les stratégies produisent
des intentions, le superviseur est la seule autorité d'approbation et le gateway
shadow ne possède aucune méthode d'envoi d'ordre. Les hard stops restent latched
jusqu'à une confirmation opérateur explicite.

M7 orchestre ces contrats en shadow, compare les fills M4 aux markouts et
produit une gate de quatorze jours consécutifs. Le logiciel est livré, mais la
gate temporelle n'est pas acquise. Même une gate M7 acquise ne positionne que
pour une discussion : `canary_authorized` reste faux et M8 demeure bloqué.

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
