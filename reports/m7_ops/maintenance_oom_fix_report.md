# Correctif M7-Ops — OOM de la maintenance M3

## Incident

Le 12 août 2026, `hyperbot-maintenance` a tenté de produire le rapport UTC du
11 août sur la première capture multi-marchés complète. Le processus chargeait
tous les records JSONL, construisait un objet `PublicMarketDataEvent` par record,
puis dupliquait les références dans des tuples et listes par marché. Avec une
limite Docker de 512 Mio, le kernel a tué le conteneur en environ 22 à 25
secondes. La politique `unless-stopped` a provoqué 963 redémarrages avant
l'intervention.

Le collector, l'observer et le watchdog sont restés actifs. Le run collector
courant exposait zéro drop, zéro message malformé et une file vide. Le rapport
M3 du 11 août n'a toutefois pas été produit et la compression des segments
fermés n'a pas progressé.

## Correction

- lecture et vérification SHA-256/hash-chain ligne par ligne des segments clos,
  bruts ou gzip, sans `read_bytes()` du segment complet ;
- chemin `analyze_ordered` à une passe pour les données append-only du collector ;
- latences stockées en tableaux `int64`, sommes et compteurs agrégés en ligne ;
- valeurs de spread spillées dans des fichiers temporaires par marché, puis
  triées un marché à la fois pour conserver les percentiles exacts en `Decimal` ;
- heartbeats atomiques pendant l'analyse et après chaque segment compressé ;
- statut persistant `running` avant le travail : après un kill brutal, le service
  refuse de boucler sur la même date et attend une reprise `quality --date` ;
- détection observer des états failed, stale, wrong-date et overdue ;
- verdict global du dashboard dépendant aussi des incidents opérationnels.

Les données A restent append-only. Le correctif ne supprime aucun record, ne
change pas le modèle de qualité M3 et ne déploie ni runner shadow ni executor.

## Validation locale

- équivalence exacte entre analyse batch et analyse ordonnée sur les métriques,
  gaps, percentiles et sommes ;
- lecture UTC testée sur segment gzip sans chargement intégral ;
- tests de maintenance idempotente et de compression conservés ;
- tests des incidents stale et overdue ajoutés ;
- benchmark synthétique : 200 000 événements BBO, 13,511 secondes et pic Python
  mesuré à 24,552 Mio avec `tracemalloc` ; ce résultat mesure le chemin logiciel,
  pas le débit garanti du serveur ;
- suite complète, lint, types et syntaxe shell à exécuter avant déploiement.

## Reprise serveur attendue

Le redéploiement doit recréer les quatre services, produire le rapport du
11 août, compresser le backlog et conserver simultanément : collector healthy,
file vide, zéro nouveau drop/malformed, gardes live/shadow intactes et absence
d'executor HyperBot. La journée du 11 août restera non qualifiée en raison des
runs de diagnostic et du démarrage partiel ; elle ne peut pas être transformée
en preuve valide par le correctif.
