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
- toute donnée legacy porte provenance, checksum, période et niveau A/B/C ;
- aucune archive TRIDENT ne peut valider seule un fill maker ou une promotion ;
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
| M1L | inventaire et import des archives TRIDENT | À faire | manifestes, adaptateurs et limites explicites |
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

## 5. Prochain lot — M1L import des données historiques

Le lot M1L accélère la recherche sans transformer les archives TRIDENT en
preuve d'exécution. Il précède M2 mais n'en remplace aucune exigence.

### M1L.1 Inventaire reproductible

Statut : `À faire`

- scanner en lecture seule les sources autorisées sous `/workspaces/trident` ;
- produire un manifest JSON et un résumé Markdown ;
- enregistrer chemin, taille, SHA-256, première/dernière date, nombre de lignes,
  schéma détecté, fréquence estimée, trous et niveau A/B/C ;
- ne pas calculer un checksum global de répertoire ambigu : chaque fichier et
  chaque manifest doivent être identifiés séparément ;
- signaler les doublons, symlinks et captures issues de fetchs répétés.

Sources prioritaires :

1. HIP-4 Nautilus `book_snapshots.jsonl` ;
2. HIP-4 `market_observations`, `shadow_maker_quotes`, trades et settlements ;
3. GBOT `l2/` et `trades/` du 1er avril ;
4. un petit échantillon représentatif de `server-data/replay_inputs/` ;
5. `data/live_snapshots/`, uniquement comme fixtures faibles.

Critères d'acceptation : inventaire relançable à résultat identique, erreurs de
lecture explicites, aucune copie massive et aucun fichier source modifié.

### M1L.2 Adaptateurs de schéma

Statut : `À faire`

- créer `LegacyGbotAdapter`, `LegacyTridentSnapshotAdapter` et
  `LegacyHip4Adapter` ;
- convertir vers les événements HyperBot sans inventer les champs absents ;
- attacher `legacy_research_only`, source, checksum et version d'adaptateur ;
- conserver le record brut ou une référence vérifiable vers celui-ci ;
- rejeter les timestamps invalides, les prix impossibles et les lignes
  corrompues dans un flux d'erreurs séparé ;
- tester chaque adaptateur avec de petits fixtures versionnés.

Critères d'acceptation : conversion déterministe, rapport des lignes
acceptées/rejetées et aucune dépendance runtime du package vers TRIDENT.

### M1L.3 Replays autorisés sur legacy

Statut : `À faire`

Les données B/C peuvent alimenter :

- évolution de fair value ;
- spreads, profondeur agrégée et markouts ;
- parité YES/NO ;
- choix de marché et détection stale ;
- reproduction de l'ancien bot ;
- modèle `optimistic_touch` clairement étiqueté.

Elles ne peuvent pas alimenter comme vérité :

- position exacte dans la file ;
- fills partiels maker ;
- modèle central/pessimiste de promotion ;
- conclusion de rentabilité live.

Critère de sortie M1L : un rapport de couverture indique précisément ce qui est
connu, approximé ou absent pour chaque dataset, et aucun résultat legacy n'est
présenté sans son niveau de preuve.

## 6. Lot M2 — catalogue et collector

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

## 7. Lots suivants

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

## 8. Séquence de travail recommandée

1. M1L.1 inventaire et manifestes des archives ;
2. M1L.2 adaptateurs HIP-4 puis GBOT ;
3. démarrer les replays legacy de fair value/markout ;
4. M2.1 catalogue et fixtures publiques ;
5. M2.2 client WebSocket factice puis public ;
6. M2.3 segmentation et manifests ;
7. M3 rapport qualité quotidien ;
8. lancer la collecte continue de niveau A ;
9. développer M4 pendant l'accumulation des trente jours ;
10. valider les modèles de fair value sur legacy puis sur niveau A OOS.

Cette séquence évite d'optimiser une stratégie avant de connaître la qualité et
la représentativité de ses données.

## 9. Commandes de vérification

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy src
```

## 10. Impact déploiement et fetching

À ce stade : aucun déploiement, aucun service serveur et aucun fetch distant.
Les fichiers de données restent locaux et ignorés par Git.

M1L lit `/workspaces/trident` sans le modifier. Les archives volumineuses restent
à leur emplacement ; HyperBot versionne seulement manifestes, fixtures minimaux
et rapports. Une exécution sans accès à TRIDENT reste possible pour le package,
les tests et le collector.

M2 ajoutera un collector public local. Le service `hyperbot-collector` et le
script `scripts/fetch_hyperbot_data.sh` ne seront créés qu'après stabilisation du
format segmenté. Aucun script TRIDENT ne sera modifié.

## 11. Questions ouvertes non bloquantes

- liste blanche outcomes initiale : découverte automatique puis validation ou
  configuration manuelle stricte ;
- granularité optimale des snapshots L2 face au volume de stockage ;
- source de référence des frais effectifs HIP-3/deployer ;
- stratégie de synchronisation d'horloge et seuil d'alerte ;
- format long terme : JSONL brut conservé, Parquet dérivé ou les deux.

Ces choix doivent être tranchés avec des mesures M2/M3, pas par anticipation.
