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
- latences et valeurs de spread spillées dans des fichiers temporaires par
  marché, puis triées par blocs de 50 000 valeurs et fusionnées en flux pour
  conserver les percentiles exacts sans pic mémoire final ;
- agrégats de gaps exhaustifs en mémoire constante et détails individuels bornés
  à 1 000 par marché dans le rapport M3 v2 ; le JSON publie le total exact, le
  nombre de détails retenus, la limite et un indicateur de troncature ;
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
- test de troncature forcée : comptes, durées et gaps majeurs restent exacts
  lorsque les détails sérialisés sont bornés ;
- benchmark synthétique final avec tri externe : 200 000 événements BBO,
  15,916 secondes et pic Python mesuré à 8,475 Mio avec `tracemalloc` ; ce
  résultat mesure le chemin logiciel, pas le débit garanti du serveur ;
- suite complète : 114 tests réussis ; Ruff et Mypy sans erreur.

## Reprise serveur attendue

Le redéploiement doit recréer les quatre services, produire le rapport du
11 août, compresser le backlog et conserver simultanément : collector healthy,
file vide, zéro nouveau drop/malformed, gardes live/shadow intactes et absence
d'executor HyperBot. La journée du 11 août restera non qualifiée en raison des
runs de diagnostic et du démarrage partiel ; elle ne peut pas être transformée
en preuve valide par le correctif.

Un premier redéploiement du correctif streaming a parcouru les 3 156 388 records
du 11 août avec 157 Mio observés, mais la matérialisation finale des listes de
percentiles a encore dépassé 512 Mio. Le garde-fou de reprise a limité cet essai
à un seul restart au lieu d'une nouvelle boucle. Après ajout du tri externe, un
second essai a terminé la passe et les percentiles avec environ 107 Mio observés,
mais a encore dépassé 512 Mio en matérialisant puis en copiant des centaines de
milliers de détails de gaps pour le JSON. Les agrégats exacts et détails bornés
décrits ci-dessus corrigent cette dernière allocation ; ils restent à valider
sur ces mêmes données avant de déclarer l'incident clos.
