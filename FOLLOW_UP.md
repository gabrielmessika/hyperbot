# HyperBot — plan de développement et suivi

**Dernière mise à jour :** 10 août 2026

**Document stratégique :** [`HYPERBOT_FOUNDATION.md`](HYPERBOT_FOUNDATION.md)

**État global :** Phase 0 démarrée, trading live impossible par construction.

## 1. Rôle de ce document

Ce fichier est le plan d'exécution courant de HyperBot. La fondation définit la
thèse, les contraintes de rendement et les règles de promotion ; ce document
ordonne le développement, consigne les décisions techniques et indique ce qui
est réellement terminé.

Les statuts utilisés sont : `À faire`, `En cours`, `Terminé`, `Bloqué`.

## 2. Principes non négociables

- aucun ordre réel avant une autorisation explicite séparée ;
- aucune dépendance runtime vers TRIDENT ;
- aucune stratégie directionnelle héritée ;
- données brutes append-only et identifiables par run/configuration/code ;
- hypothèses de fill centrales et pessimistes obligatoires ;
- fail-closed sur données stale, perte de flux ou divergence d'état ;
- rendement mesuré après tous les coûts, jamais extrapolé d'un spread affiché.

## 3. État des lots

| Lot | Contenu | Statut | Critère de sortie |
|---|---|---|---|
| M0 | dépôt, workspace, packaging, règles agents | Terminé | tests/lint/types opérationnels |
| M1 | contrats d'événements, config sûre, event store | Terminé | invariants et intégrité couverts par tests |
| M2 | catalogue de marchés et collector public | À faire | flux outcomes/HIP-3 enregistré sans ordre |
| M3 | contrôle qualité et rapport quotidien | À faire | trous, fraîcheur et complétude mesurés |
| M4 | replay déterministe et modèles de file | À faire | central + pessimiste reproductibles |
| M5 | fair value outcomes et scanner HIP-3 | À faire | benchmarks OOS sans fuite temporelle |
| M6 | stratégies de quote et superviseur de risque | À faire | intentions uniquement, caps testés |
| M7 | runner shadow et observabilité | À faire | 14 jours sans violation opérationnelle |
| M8 | canary monétaire | Bloqué | autorisation utilisateur + gates statistiques |

## 4. Lot M1 livré

### Contrats de domaine

`src/hyperbot/models.py` définit des événements immuables :

- `BookEvent` et `BookLevel` ;
- `QuoteIntent` ;
- `OrderLifecycle` ;
- `FillAttribution` avec markouts 100 ms/1 s/5 s/30 s ;
- `OutcomeSettlement` ;
- `EventContext` commun avec `run_id`, version de code, hash de configuration et
  source de temps.

Les prix, tailles, frais et markouts utilisent `Decimal`. La sérialisation JSON
écrit les décimaux comme chaînes afin de ne pas introduire d'arrondi binaire.

### Configuration

`config/hyperbot_research.toml` reprend les limites de la fondation. Le loader :

- rejette les clés inconnues et les sections manquantes ;
- calcule le SHA-256 exact du fichier ;
- contrôle la cohérence des allocations et des caps ;
- refuse `live_enabled = true` ;
- impose `shadow_only = true`, `ALO` et le growth mode dans cette phase.

### Event store

`JsonlEventStore` fournit :

- un fichier JSONL par stream ;
- écriture append-only sous verrou de processus ;
- `fsync` configurable et activé par défaut ;
- enveloppe versionnée ;
- hash SHA-256 du payload vérifié à la lecture ;
- noms de stream contrôlés contre les traversées de chemin.

Limite connue : le store v1 détecte la modification d'un payload, mais pas la
suppression d'une ligne complète. Un hash chaîné et un manifest de segments sont
prévus dans M2 avant la collecte longue.

## 5. Prochain lot — M2 catalogue et collector

### M2.1 Catalogue de marchés

Statut : `À faire`

- interroger uniquement les endpoints publics Hyperliquid ;
- normaliser core, HIP-3 et outcomes dans un `MarketDefinition` versionné ;
- enregistrer tick, taille minimale, DEX, growth mode, frais effectifs, oracle et
  statut de marché ;
- détecter tout changement de spécification et créer un nouvel événement au
  lieu d'écraser l'ancien ;
- ne jamais construire de client de signature dans le collector.

Critères d'acceptation : fixtures API, marchés inconnus tolérés, changement de
tick détecté, aucun secret requis.

### M2.2 Client WebSocket public

Statut : `À faire`

- une connexion durable avec reconnexion et backoff borné ;
- subscriptions L2/BBO/trades pour une liste blanche de marchés ;
- timestamps exchange, réception murale et monotone ;
- heartbeat et événement explicite de gap/reconnexion ;
- file bornée avec métrique de drops, sans blocage silencieux ;
- persistance via `JsonlEventStore`.

Critères d'acceptation : tests sur serveur WebSocket factice, reconnexion,
message malformé, surcharge de file, arrêt propre et zéro chemin d'ordre.

### M2.3 Segmentation et intégrité

Statut : `À faire`

- rotation par date UTC et taille maximale ;
- chaîne de hash entre records ou segments ;
- manifest contenant début/fin, nombre de records, checksum et configuration ;
- reprise après ligne partielle sans réécriture des données valides ;
- compression seulement après fermeture et validation du segment.

Critères d'acceptation : suppression, troncature et corruption détectées ; replay
identique avant/après compression.

## 6. Lots suivants

### M3 — qualité des données

- calculer couverture, latence p50/p95/p99, gaps et périodes stale ;
- produire spread, profondeur et activité par marché ;
- distinguer absence réelle de messages et panne de collecte ;
- générer un rapport quotidien JSON + Markdown ;
- gate : sept jours sans trou majeur, puis collecte jusqu'à trente jours.

### M4 — replay

- horloge virtuelle et ordering déterministe ;
- modèle pessimiste : volume devant la quote entièrement consommé ;
- modèle central : file estimée, latence réelle et fills partiels ;
- touch-fill uniquement comme borne optimiste ;
- markouts et PnL économique après frais ;
- résultat bit-à-bit identique pour même code/config/données.

### M5 — recherche d'edge

- benchmark digital outcome sous volatilité empirique ;
- calibration chronologique puis folds OOS purgés ;
- scanner HIP-3 growth avec coûts effectifs au runtime ;
- journal de toutes les variantes testées ;
- aucune promotion si le profit dépend à plus de 40 % d'un sous-jacent.

### M6 — stratégies et risque

- stratégies limitées à `QuoteIntent` ;
- superviseur seul autorisé à approuver ou rejeter ;
- worst-case payoff YES/NO et caps corrélés ;
- stale book, orphan order et position mismatch fail-closed ;
- execution gateway shadow sans méthode d'envoi d'ordre.

### M7 — shadow

- quotes calculées mais jamais envoyées ;
- estimation de fill comparée aux markouts observés ;
- restart et réconciliation simulée ;
- quatorze jours sans violation de risque avant toute discussion de canary.

### M8 — canary, bloqué par défaut

Ce lot ne peut pas commencer à la suite d'un simple développement. Il exige :

1. les gates statistiques de la fondation ;
2. une review de sécurité ;
3. un subaccount/API wallet sans retrait ;
4. une autorisation explicite de l'utilisateur ;
5. 100 $ au maximum et ordres de 10 $ au démarrage.

## 7. Séquence de travail recommandée

1. M2.1 catalogue et fixtures publiques ;
2. M2.2 client WebSocket factice puis public ;
3. M2.3 segmentation et manifests ;
4. M3 rapport qualité quotidien ;
5. lancer la collecte continue ;
6. développer M4 pendant l'accumulation des trente jours ;
7. seulement ensuite commencer les modèles de fair value.

Cette séquence évite d'optimiser une stratégie avant de connaître la qualité et
la représentativité de ses données.

## 8. Commandes de vérification

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy src
```

## 9. Impact déploiement et fetching

À ce stade : aucun déploiement, aucun service serveur et aucun fetch distant.
Les fichiers de données restent locaux et ignorés par Git.

M2 ajoutera un collector public local. Le service `hyperbot-collector` et le
script `scripts/fetch_hyperbot_data.sh` ne seront créés qu'après stabilisation du
format segmenté. Aucun script TRIDENT ne sera modifié.

## 10. Questions ouvertes non bloquantes

- liste blanche outcomes initiale : découverte automatique puis validation ou
  configuration manuelle stricte ;
- granularité optimale des snapshots L2 face au volume de stockage ;
- source de référence des frais effectifs HIP-3/deployer ;
- stratégie de synchronisation d'horloge et seuil d'alerte ;
- format long terme : JSONL brut conservé, Parquet dérivé ou les deux.

Ces choix doivent être tranchés avec des mesures M2/M3, pas par anticipation.
