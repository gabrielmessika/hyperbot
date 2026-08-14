# HyperBot — plan de développement et suivi

**Dernière mise à jour :** 11 août 2026

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
| M1L | inventaire et import des archives TRIDENT | Terminé | manifestes, adaptateurs et limites explicites |
| M2 | catalogue de marchés et collector public | Terminé | flux outcomes/HIP-3 enregistré sans ordre |
| M3 | contrôle qualité et rapport quotidien | Terminé | trous, fraîcheur et complétude mesurés |
| M4 | replay déterministe et modèles de file | Terminé | central + pessimiste reproductibles |
| M5 | fair value outcomes et scanner HIP-3 | Terminé | benchmarks OOS sans fuite temporelle |
| M6 | stratégies de quote et superviseur de risque | Terminé | intentions uniquement, caps testés |
| M7 | runner shadow et observabilité | Terminé | logiciel livré ; gate 14 jours en attente |
| M7-Ops | déploiement, preuves M3, API et dashboard | Terminé | collector BTC et observer 3002 actifs ; preuves en collecte |
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

## 5. Lot M1L livré — import des données historiques

Le lot M1L accélère la recherche sans transformer les archives TRIDENT en
preuve d'exécution. Il précède M2 mais n'en remplace aucune exigence.

### M1L.1 Inventaire reproductible

Statut : `Terminé`

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

Livré le 11 août 2026 :

- scanner déterministe et strictement en lecture seule dans
  `src/hyperbot/legacy/manifest.py` ;
- CLI `scripts/inventory_legacy_data.py`, qui refuse d'écrire sous la racine
  TRIDENT ;
- manifest JSON, rapport Markdown et checksums indépendants sous
  `reports/legacy_inventory/` ;
- 58 fichiers inventoriés, 717 705 513 octets, 1 625 170 lignes physiques et
  1 625 167 records valides ;
- zéro record malformé, zéro erreur fatale, zéro doublon SHA-256 exact et une
  capture Nautilus archivée signalée comme candidate de fetch répété ;
- schémas de premier niveau, périodes UTC, cadences médianes et trous temporels
  inférés consignés par fichier.

La mesure des trous reste une heuristique `delta > 3 × médiane`. Sur un flux de
trades irrégulier elle mesure aussi l'inactivité naturelle et ne prouve donc pas
une panne du collector. Les checksums ont été calculés fichier par fichier ;
aucune archive n'a été copiée ou modifiée.

### M1L.2 Adaptateurs de schéma

Statut : `Terminé`

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

Livré le 11 août 2026 :

- `LegacyHip4Adapter`, `LegacyGbotAdapter` et
  `LegacyTridentSnapshotAdapter`, sans import runtime de code TRIDENT ;
- événements immuables pour BBO/profondeur agrégée, trades, quotes paper,
  settlements et features historiques ;
- provenance obligatoire avec niveau B/C, chemins et SHA-256 source, numéro et
  hash du record, sous-record et version d'adaptateur ;
- importer qui revérifie taille, mtime et checksum avant lecture, refuse les
  symlinks et publie les sorties dérivées de façon immuable ;
- flux JSONL séparé pour chaque source rejetée et rapport d'acceptation sous
  `reports/legacy_import/`.

Import complet `legacy-import-b674044f8ed8848f` : 1 430 010 records acceptés,
195 157 rejetés et 1 559 718 événements émis. Tous les rejets sont des BBO GBOT
croisés (`best_bid > best_ask`) ; ils sont exclus plutôt que réparés. Les 21 887
observations HIP-4 sans payload de carnet sont conservées comme carnets vides,
avec champs nuls et flags `empty_book`/`book_payload_absent`.

### M1L.3 Replays autorisés sur legacy

Statut : `Terminé`

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

Livré le 11 août 2026 : `src/hyperbot/legacy/policy.py` centralise une gate
fail-closed. Les usages de recherche autorisés imposent le label
`legacy_research_only`; `optimistic_touch` impose en plus
`legacy_research_only_optimistic_touch`. La file exacte, les fills maker
partiels, les modèles central/pessimiste, une affirmation de rentabilité live
et une promotion canary sont refusés dès qu'une donnée B/C est présente. La
matrice complète et les limites par dataset figurent dans
`reports/legacy_import/coverage.md`.

## 6. Lot M2 — catalogue et collector

### M2.1 Catalogue de marchés

Statut : `Terminé`

- interroger uniquement les endpoints publics Hyperliquid ;
- normaliser core, HIP-3 et outcomes dans un `MarketDefinition` versionné ;
- enregistrer tick, taille minimale, DEX, growth mode, frais effectifs, oracle et
  statut de marché ;
- détecter tout changement de spécification et créer un nouvel événement au
  lieu d'écraser l'ancien ;
- ne jamais construire de client de signature dans le collector.

Critères d'acceptation : fixtures API, marchés inconnus tolérés, changement de
tick détecté, aucun secret requis.

Livré le 11 août 2026 :

- transport HTTPS minimal limité à l'endpoint public `info`, sans SDK de
  signature ni client exchange ;
- normalisation immuable et versionnée des perps core, DEX HIP-3 et deux côtés
  de chaque outcome ;
- asset ID, décimales de taille, incrément minimal, règle de tick, notional
  minimal, DEX, growth mode, barème tier 0, oracle, mark et statut enregistrés ;
- empreinte de spécification indépendante des variations d'oracle, avec nouvel
  événement chaîné à la révision précédente lorsqu'une règle change ;
- champs futurs ignorés, entrées invalides isolées dans des problèmes non
  fatals et absence de `outcomeMeta` tolérée explicitement.

Le snapshot public `catalog-1786437256-76047f05` contient 500 définitions :
232 core, 252 HIP-3 et 16 côtés d'outcomes. Les métadonnées publiques observées
ne donnent pas le tick/lot des outcomes : ils restent nuls avec un quality flag,
sans reconstruction implicite.

### M2.2 Client WebSocket public

Statut : `Terminé`

- une connexion durable avec reconnexion et backoff borné ;
- subscriptions L2/BBO/trades pour une liste blanche de marchés ;
- timestamps exchange, réception murale et monotone ;
- heartbeat et événement explicite de gap/reconnexion ;
- file bornée avec métrique de drops, sans blocage silencieux ;
- persistance via `JsonlEventStore`.

Critères d'acceptation : tests sur serveur WebSocket factice, reconnexion,
message malformé, surcharge de file, arrêt propre et zéro chemin d'ordre.

Livré le 11 août 2026 :

- subscriptions strictement limitées à `l2Book`, `bbo` et `trades`, sur une
  whitelist de coins ;
- horodatages exchange, réception murale et réception monotone sur chaque
  événement brut ;
- reconnexion durable avec backoff exponentiel plafonné, heartbeat applicatif
  `ping`/`pong`, détection stale et événements explicites de gap ;
- file de persistance bornée, compteur de drops et événement `queue_drop`
  persisté directement par le writer sans blocage silencieux ;
- arrêt drainé et protocole de store compatible avec `JsonlEventStore` et le
  store segmenté M2.3 ;
- serveur WebSocket factice couvrant reconnexion, message invalide, heartbeat,
  surcharge et arrêt.

Smoke test public de cinq secondes : BTC, `cash:AMZN` et `#10570`, 119
événements (L2/BBO/trades), zéro drop, zéro malformed et aucun ordre.

### M2.3 Segmentation et intégrité

Statut : `Terminé`

- rotation par date UTC et taille maximale ;
- chaîne de hash entre records ou segments ;
- manifest contenant début/fin, nombre de records, checksum et configuration ;
- reprise après ligne partielle sans réécriture des données valides ;
- compression seulement après fermeture et validation du segment.

Critères d'acceptation : suppression, troncature et corruption détectées ; replay
identique avant/après compression.

Livré le 11 août 2026 :

- rotation à la date UTC ou avant dépassement de taille ;
- séquences continues et chaîne SHA-256 entre chaque record, prolongée entre
  segments ;
- manifest checksummé contenant configuration, début/fin UTC, séquences,
  compteurs, hashes de records, contenu et stockage ;
- récupération limitée à une dernière ligne active partielle : les octets
  valides précédents ne sont pas réécrits et une corruption complète reste une
  erreur ;
- compression gzip déterministe uniquement des segments fermés préalablement
  validés, avec vérification du contenu décompressé avant publication ;
- tests distincts de suppression, troncature, corruption et identité du replay
  avant/après compression.

Le rapport et les limites du lot sont publiés dans
`reports/m2/implementation_report.md`.

## 7. Lots suivants

### M3 — qualité des données

Statut : `Terminé` pour le logiciel ; gate temporelle en collecte.

- calculer couverture, latence p50/p95/p99, gaps et périodes stale ;
- produire spread, profondeur et activité par marché ;
- distinguer absence réelle de messages et panne de collecte ;
- générer un rapport quotidien JSON + Markdown ;
- gate : sept jours sans trou majeur, puis collecte jusqu'à trente jours.

Livré le 11 août 2026 :

- `DailyQualityAnalyzer` calcule couverture, stale, latences p50/p95/p99,
  spreads p10/p50/p90, profondeurs et activité par marché attendu ;
- les gaps distinguent `collector_outage`, `collector_not_running` et
  `market_stale`, sans interpréter une absence naturelle de trades comme une
  panne ;
- les rapports JSON et Markdown portent run, code, configurations, niveau A et
  checksum indépendant ;
- la gate exige sept puis trente journées UTC consécutives qualifiées et
  signale dates manquantes ou journées échouées ;
- CLI locale : `scripts/report_data_quality.py` ; rapport technique :
  `reports/m3/implementation_report.md`.

La capture M2 de cinq secondes est correctement refusée comme journée
qualifiée. Les gates réelles 7/30 jours restent donc non acquises ; aucune
continuité fictive n'a été créée.

### M4 — replay

Statut : `Terminé` pour le moteur ; validation empirique A en attente.

- horloge virtuelle et ordering déterministe ;
- modèle pessimiste : volume devant la quote entièrement consommé ;
- modèle central : file estimée, latence réelle et fills partiels ;
- touch-fill uniquement comme borne optimiste ;
- markouts et PnL économique après frais ;
- résultat bit-à-bit identique pour même code/config/données.

Livré le 11 août 2026 :

- horloge virtuelle monotone et ordering stable indépendant de l'ordre d'entrée ;
- modèles `pessimistic`, `central` et `optimistic_touch` séparés, le dernier
  restant une borne explicitement étiquetée ;
- file visible entièrement consommée au pessimiste, file estimée et fills
  partiels au central, avec latences placement/cancel ;
- refus fail-closed de central/pessimiste sur B/C et en l'absence de preuve L2 ;
- markouts 100 ms/1 s/5 s/30 s dans une tolérance bornée, frais et PnL
  économique 30 s ;
- stress automatiques latence ×2 et frais ×2, hashes de configuration, entrée
  et résultat ;
- runner `scripts/run_hyperbot_replay.py`, fixture M4 et rapport
  `reports/m4/implementation_report.md`.

La reproductibilité logicielle est acquise. Les résultats sur fixtures ne
valident ni edge ni promotion tant que la collecte A n'a pas satisfait M3.

### M5 — recherche d'edge

Statut : `Terminé` pour le laboratoire ; edge OOS non démontré.

- benchmark digital outcome sous volatilité empirique ;
- calibration chronologique puis folds OOS purgés ;
- scanner HIP-3 growth avec coûts effectifs au runtime ;
- journal de toutes les variantes testées ;
- aucune promotion si le profit dépend à plus de 40 % d'un sous-jacent.

Livré le 11 août 2026 :

- benchmark digital à volatilité empirique et calibration isotone ajustée sur
  les seuls folds antérieurs ;
- fenêtres train/calibration/test strictes, purge par date de settlement et
  interdiction des tests OOS chevauchants ;
- Brier, log loss, PnL/frais, PF, drawdown, comparaison au « ne rien faire » et
  au legacy, ventilations marché/sous-jacent/régime/expiration ;
- bootstrap reproductible par clusters journée × marché ;
- scanner HIP-3 growth qui relit frais, statut, growth mode et hash de
  définition au runtime avant chaque décision ;
- journal de variantes append-only et hash-chaîné ;
- gate fail-closed sur trois folds, PF, drawdown, concentration 40 %, replay
  central/pessimiste, stress frais et borne bootstrap ;
- rapport : `reports/m5/implementation_report.md`.

Les fixtures valident l'absence de fuite et les gates. Elles ne démontrent pas
un edge et ne remplacent pas les données A encore en collecte.

### M6 — stratégies et risque

Statut : `Terminé` pour le logiciel ; stratégies non promues.

- stratégies limitées à `QuoteIntent` ;
- superviseur seul autorisé à approuver ou rejeter ;
- worst-case payoff YES/NO et caps corrélés ;
- stale book, orphan order et position mismatch fail-closed ;
- execution gateway shadow sans méthode d'envoi d'ordre.

Livré le 11 août 2026 :

- contrat `Strategy` pur et états immuables ; moteurs outcomes/growth limités à
  des `QuoteIntent`, sans dépendance execution ;
- arrondis tick/lot et notional minimal, filtres stale/croisé/expiration et
  fair values bornées ;
- `RiskSupervisor` seul producteur d'`ApprovedIntent`, avec contrôles ALO,
  version de marché, tick, budgets, inventaires et drawdowns ;
- payoff YES/NO recalculé après fill hypothétique, caps par marché, corrélés et
  agrégés ;
- heartbeat, orphan, perte inconnue et mismatch position fail-closed ;
- hard stop latched, réarmable uniquement par autorisation opérateur explicite ;
- DEX HIP-3 unique, caps gross/net et réduction soft refusée si elle passe sous
  le minimum d'ordre ;
- `ShadowExecutionGateway` sans méthode d'envoi, avec état exchange simulé
  autoritaire au restart ; rapport `reports/m6/implementation_report.md`.

Les moteurs sont toujours désactivés et non promus : M6 prouve les contrats et
les barrières, pas l'edge M5 ni la qualification shadow M7.

### M7 — shadow

Statut : `Terminé` pour le logiciel ; gate réelle de 14 jours en attente.

- quotes calculées mais jamais envoyées ;
- estimation de fill comparée aux markouts observés ;
- restart et réconciliation simulée ;
- quatorze jours sans violation de risque avant toute discussion de canary.

Livré le 11 août 2026 :

- `ShadowRunner` réutilise les contrats Strategy/Risk M6 et journalise intents,
  décisions, actions et quotes `shadow_only` dans les stores HyperBot ;
- évaluation en batch : les approbations hypothétiques précédentes alimentent
  les caps des intents suivants ;
- comparaison des résultats central/pessimiste M4 et événements par quote
  reliant probabilité de fill aux markouts observés ;
- restart fail-closed avec état exchange simulé autoritaire, blocage sur
  position divergente ou ordre orphelin et déblocage seulement après état propre ;
- rapports journaliers JSON/Markdown checksummés avec risque, qualité M3,
  compatibilité replay et stress latence ;
- gate de quatorze journées consécutives, remise à zéro par date manquante ou
  incident ; même acquise, elle n'autorise jamais M8 ;
- rapport : `reports/m7/implementation_report.md`.

La gate réelle reste non acquise : aucun historique de quatorze jours n'a été
simulé comme preuve. Aucun service ni executor live n'a été déployé.

### M7-Ops — exploitation du collector public

Statut : `Terminé` côté logiciel ; installation et smoke test distants en attente.

Livré le 11 août 2026 :

- runtime continu SIGTERM, métriques de connexion et statut atomique ;
- configuration `.env.hyperbot.example` désactivée par défaut, guards live/shadow
  et rejet des secrets de signature HyperBot/TRIDENT ;
- Dockerfile non-root et Compose sous profil explicite `collector`, avec
  filesystem read-only, ressources et logs bornés ;
- maintenance UTC quotidienne idempotente, lecture des seuls segments clôturés,
  rapport M3 checksumé et compression lossless sans suppression des données A ;
- healthcheck fail-closed sur fraîcheur, connexion, feed stale, hash et disque ;
- watchdog de transitions avec cooldown, récupération et webhook HTTPS optionnel,
  sans socket Docker ni valeur sensible dans ses statuts ;
- déploiement par releases sous `/opt/hyperbot`, activation distincte, commandes
  serveur et rollback ;
- observer HTTP standard-library authentifié, API GET/HEAD sans endpoint de
  mutation et dashboard responsive sans contrôle start/stop ;
- profils de collecte disjoints : L2/BBO/trades pour le noyau `depth`,
  BBO/trades pour l'univers `breadth`, avec matrice exacte dans le hash de
  configuration et refus du mélange avec les anciennes variables ;
- port `3002/tcp` public configurable, mot de passe aléatoire hors `.env`,
  secrets Docker et volumes de l'observer strictement en lecture seule ;
- `scripts/fetch_hyperbot_data.sh` avec manifest public, liste exacte, tailles et
  SHA-256 ; exclusion des segments ouverts, symlinks, `.env` et données TRIDENT ;
- runbook `docs/m7_ops_runbook.md` et rapport
  `reports/m7_ops/implementation_report.md`, complétés par le rapport UI/API
  `reports/m7_ops_ui/implementation_report.md`.

La whitelist reste manuelle. Le premier déploiement doit uniquement lancer le
collector et sa maintenance M3. Le runner shadow M7 reste inactif avant les gates
M3/M5 et aucun executor n'est présent.

Activation distante réalisée le 11 août 2026 :

- release `/opt/hyperbot/releases/20260811T141144Z-4e811c0bce9b` ;
- collector public limité à `BTC` et aux canaux `l2Book,bbo,trades` ;
- `live_enabled=false`, `shadow_only=true`, aucun secret de signature ;
- collector et observer Docker healthy, maintenance et watchdog running ;
- observer publié sur `0.0.0.0:3002`, UFW ouvert uniquement pour `3002/tcp` en
  plus des règles existantes ;
- smoke API authentifié 200, dashboard sans auth 401, POST refusé 405 ;
- snapshot du smoke : 591 messages reçus, 774 événements persistés, zéro drop
  et zéro message malformé ;
- premier rapport M3 correctement non qualifié faute de données A pour la
  veille ; gates 7/30 jours non acquises ;
- conteneurs TRIDENT/HIP-4 observés inchangés et aucun executor HyperBot présent.

Extension multi-marchés préparée le 11 août 2026 : quatre marchés `depth` et
vingt marchés `breadth`, soit 52 subscriptions. Les 24 symboles ont été revérifiés
sur le catalogue public courant et observés sur un smoke WebSocket de douze
secondes : 2 787 événements persistés, zéro drop, zéro malformed et aucun marché
manquant. Le release `20260811T144555Z-7c52fbdb22bb` a ensuite remplacé la
whitelist BTC minimale. Le smoke serveur confirme les 52 couples, les quatre
services sains, zéro drop/malformed/reconnexion et l'intégrité du store actif.

La mesure serveur initiale est d'environ 12 Gio/jour bruts et 1,83 Gio/jour
après gzip. Un Volume Hetzner de 100 Go est désormais monté directement sur
`/opt/hyperbot/shared/data` : 93 Gio étaient libres après migration, avec une
réserve fail-closed de 10 Gio. La gate M3 de 30 jours devient soutenable sur
cette projection, mais pas la rétention locale de 60 jours ; un archivage froid
reste à préparer. TRIDENT classique et TRIDENT-HIP4 n'ont pas été supprimés.

La migration a aussi révélé qu'une première maintenance lancée en concurrence
avec le collector pouvait garder le verrou du store pendant une validation et
une compression complètes. Le run de diagnostic concerné a produit des drops et
reste explicitement non qualifié. La compression valide maintenant chaque
segment hors verrou, ne prend le verrou que pour sa publication atomique, et la
maintenance réutilise un jour déjà finalisé même après un changement de config.
Le release final `20260811T155300Z-8e73ae32bdef` expose aussi la profondeur de
file ; son smoke est à zéro drop, malformed, reconnexion et backlog.

L'observabilité sépare désormais les incidents opérationnels actifs des
anomalies du dernier rapport M3. Le compteur principal ne mélange plus une
preuve qualité historique avec l'état courant ; l'API et l'UI exposent la date,
le verdict et le nombre d'anomalies dans un bloc distinct.

Le 12 août 2026, la première journée multi-marchés complète a révélé un second
défaut de passage à l'échelle : M3 matérialisait plusieurs fois les millions
d'événements de la journée et dépassait la limite mémoire de 512 Mio. Docker a
tué puis relancé `maintenance` en boucle, tandis que l'ancien statut `completed`
du 10 août masquait l'incident. Le conteneur a été stoppé isolément à 963
redémarrages ; le collector est resté healthy, sans drop sur son run courant.

Le correctif lit et authentifie désormais chaque segment en flux, agrège les
métriques dans des structures compactes, spill latences et spreads temporairement
et calcule leurs percentiles exacts par tri externe borné. Un premier essai
streaming avait encore OOM au tri final après les 3 156 388 records ; le garde-fou
a limité l'essai à un restart avant le correctif de tri par blocs. La maintenance
publie aussi des heartbeats d'analyse/compression. Une tentative interrompue
n'est plus répétée automatiquement pour la même date sans reprise opérateur.
Le second essai a confirmé un dernier pic après l'analyse : le rapport gardait
chaque gap individuel puis le dupliquait pendant la sérialisation. Le schéma M3
v2 conserve désormais les compteurs, durées, causes et verdicts exhaustifs, mais
borne à 1 000 par marché les détails de gaps retenus dans le JSON ; le nombre
total, le nombre retenu, la limite et l'indicateur de troncature sont explicites.
Ce rapport v2 a franchi la sérialisation serveur, puis la compression historique
a exposé un plateau de 445 Mio : elle chargeait encore simultanément segment
brut, gzip et décompression de contrôle. La compression copie maintenant les
segments par blocs et vérifie en flux leurs hash de stockage, de contenu et de
décompression avant la publication atomique. Après un crash post-rapport, une
reprise opérateur réutilise désormais uniquement un rapport checksumé dont le
schéma, la date, le run, le tier A et la configuration qualité correspondent,
puis reprend directement la compression.
L'API et le dashboard signalent maintenant une maintenance failed, stale, sur
la mauvaise date ou overdue, et l'état global ne peut plus rester `Sain` en
présence de cet incident. Le rapport d'implémentation et de validation se trouve
dans `reports/m7_ops/maintenance_oom_fix_report.md`.

La validation finale du 12 août utilise le release
`20260812T084310Z-82354ea99baa`. La reprise a réutilisé le rapport v2 checksumé,
compressé 28 segments restants avec environ 27 à 38 Mio affichés au lieu du
plateau historique de 445 Mio, puis publié le marker `completed`. Les 3 156 388
événements sont comptés, 56 segments collector clos sont en gzip, le collector
reste à zéro drop/malformed/backlog et les quatre services ont zéro restart/OOM.
L'observer expose zéro incident opérationnel actif. La journée reste
correctement non qualifiée : 317 963 gaps exacts, 24 000 détails retenus et 48
motifs de qualité ; aucune gate ni aucun seuil n'a été assoupli.

Le 13 août, le rapport automatique du 12 août a échoué fail-closed parce que le
stream peu actif `collector-control` gardait encore un segment du 12 août ouvert
à 00:15 UTC. La maintenance finalise désormais, sous le verrou du store, tout
segment actif non vide strictement antérieur au cutoff du rapport avant de le
lire. Le writer continu détecte la rotation externe et poursuit la chaîne sans
restart. Une garde distincte refuse toute analyse de la journée UTC en cours ou
d'une date future. Le déploiement de ce correctif doit recréer uniquement la
maintenance afin de préserver le 13 août comme premier jour M3 candidat.

Le release `/opt/hyperbot/releases/20260813T185440Z-65b43732befa` a été
sélectionné sans `--start-collector`, puis seul le conteneur maintenance a été
recréé. Le collector a conservé son ID, son démarrage du 12 août, son `run_id` et
ses 12 reconnexions ; aucun événement `shutdown`, drop ou malformed n'a été
ajouté. La reprise du 12 août a terminé avec 8 320 373 événements, un rapport v2
checksumé, 94 segments compressés et zéro segment clos brut restant. La
maintenance et le collector sont healthy, l'incident actif est revenu à zéro.
Le 13 août reste donc le premier jour M3 candidat ; son verdict dépendra du
rapport automatique produit après clôture UTC.

Le rapport automatique du 13 août a été publié le 14 août à 00:15 UTC avec
8 228 581 événements, un checksum valide, 9 micro-coupures totalisant 5,392
secondes et zéro incident opérationnel actif. Il est complet mais non qualifié :
les 24 marchés restent sous 99 % de couverture et 22 portent au moins un gap
majeur. Il constitue donc le premier rapport complet à analyser, pas le premier
jour de la gate.

Cette analyse a révélé une attribution incorrecte des gaps avant la première
reconnexion du jour : lorsque la session collector avait commencé la veille,
son événement d'ouverture appartenait au segment UTC précédent et M3 classait
à tort le début de journée en `collector_not_running`. Le modèle reporte
désormais une session à minuit si le premier événement de cycle de vie est un
`disconnected`, et reporte une coupure à minuit si le premier événement est un
`reconnected`. Un `shutdown` isolé reste insuffisant pour supposer une connexion.
Les tests couvrent les deux frontières. Ce
correctif ne modifie ni les durées stale, ni le verdict, ni les seuils 500 ms / 5
s / 99 %, ni le schéma v2 ; le rapport publié du 13 août reste immuable.

Le release `/opt/hyperbot/releases/20260814T083125Z-012ac8d49ba7` a été
sélectionné puis seule la maintenance a été recréée. Le collector conserve son
ID `a6c38019...`, son démarrage du 12 août et son `run_id`; observer et watchdog
conservent aussi leurs conteneurs. La nouvelle machine d'état relue sur les 27
événements de contrôle réels du 13 août produit une première session à 00:00
UTC, 10 sessions et les mêmes 9 coupures pour 5 392 ms. Le checksum du rapport
reste `5cd54902...`, les quatre services sont sans restart/OOM, le collector est
à zéro drop/malformed/backlog et l'observer expose zéro incident actif.

### M8 — canary, bloqué par défaut

Ce lot ne peut pas commencer à la suite d'un simple développement. Il exige :

1. les gates statistiques de la fondation ;
2. une review de sécurité ;
3. un subaccount/API wallet sans retrait ;
4. une autorisation explicite de l'utilisateur ;
5. 100 $ au maximum et ordres de 10 $ au démarrage.

## 8. Séquence de travail recommandée

1. M1L.1 inventaire et manifestes des archives — terminé ;
2. M1L.2 adaptateurs HIP-4 puis GBOT — terminé ;
3. M1L.3 politique et couverture de preuve — terminé ;
4. démarrer les replays legacy de fair value/markout ;
5. M2.1 catalogue et fixtures publiques ;
6. M2.2 client WebSocket factice puis public ;
7. M2.3 segmentation et manifests ;
8. M3 rapport qualité quotidien ;
9. lancer la collecte continue de niveau A ;
10. développer M4 pendant l'accumulation des trente jours ;
11. valider les modèles de fair value sur legacy puis sur niveau A OOS.

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

À ce stade, le collector public M2 et l'observer M7-Ops sont actifs sur le
serveur ; aucun executor live, runner shadow distant ou fetch réel n'est actif.
Les fichiers locaux de données restent sous `data/` et sont ignorés par Git.

M1L lit `/workspaces/trident` sans le modifier. Les archives volumineuses restent
à leur emplacement ; HyperBot versionne seulement manifestes, fixtures minimaux
et rapports. Le run dérivé courant occupe environ 1,9 Gio sous
`data/legacy_imports/` et reste ignoré par Git. Une exécution sans accès à
TRIDENT reste possible pour le package, les tests et le collector.

Le format segmenté et l'outillage M7-Ops sont stabilisés et actifs avec
l'observer public `3002`. Le script de fetch HyperBot reste séparé de
`scripts/fetch_all_data.sh`. Aucun script TRIDENT n'a été modifié.

## 11. Questions ouvertes

- **à résoudre avant 60 jours :** archivage froid checksummé des segments du
  Volume, puisque 100 Go couvrent la gate de 30 jours mais pas 60 jours locaux ;

- liste blanche outcomes initiale : découverte automatique puis validation ou
  configuration manuelle stricte ;
- granularité optimale des snapshots L2 face au volume de stockage ;
- source de référence des frais effectifs HIP-3/deployer ;
- stratégie de synchronisation d'horloge et seuil d'alerte ;
- format long terme : JSONL brut conservé, Parquet dérivé ou les deux.

Ces choix doivent être tranchés avec des mesures M2/M3, pas par anticipation.
