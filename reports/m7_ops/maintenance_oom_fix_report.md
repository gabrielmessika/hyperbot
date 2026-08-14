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
- compression gzip copiée par blocs de 1 Mio et vérifiée en flux, sans conserver
  simultanément le segment brut, le gzip et sa décompression complète ;
- reprise post-rapport sans recalcul : le rapport existant n'est accepté que si
  son checksum, son schéma v2, sa date, son `run_id`, son tier A et son hash de
  configuration qualité correspondent ;
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
- test interdisant `Path.read_bytes()` sur le segment source, le gzip temporaire
  et le gzip publié pendant la compression ;
- test de reprise qui interdit toute nouvelle analyse lorsqu'un rapport
  compatible et checksumé existe déjà sans marker final ;
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
décrits ci-dessus ont ensuite franchi la création du rapport réel. Cette reprise
a révélé un plateau distinct de 445 Mio pendant la compression historique des
segments de 128 Mio ; la copie et les validations gzip en flux décrites ci-dessus
suppriment aussi ce dernier risque avant la validation finale.

## Validation serveur finale

Le release `/opt/hyperbot/releases/20260812T084310Z-82354ea99baa` a repris le
rapport compatible sans nouvelle analyse, puis compressé les 28 segments
restants avec environ 27 à 38 Mio affichés pour le conteneur, zéro événement OOM
et une sortie 0. Le marker quotidien est `completed`, checksum du rapport valide,
avec 3 156 388 événements traités. Les manifests comptent 47 segments gzip de
marché public et 9 segments gzip de contrôle ; aucun segment collector clos brut
ne reste après la passe.

Le smoke final confirme : quatre services actifs, collector et observer healthy,
zéro restart/OOM, zéro drop, zéro malformed, file de persistance vide, 93 Gio
libres et zéro incident opérationnel actif. `live_enabled=false`,
`shadow_only=true`, et aucun executor ou runner shadow HyperBot n'est déployé.

Le rapport v2 fait 4 441 228 octets et reste non qualifié : 317 963 gaps exacts,
24 000 détails retenus, troncature explicitement déclarée et 48 motifs de
qualification. Ce verdict n'a pas été contourné : cette journée perturbée ne
constitue toujours pas une preuve M3 valide.

## Rotation UTC des streams peu actifs

Le run automatique du 13 août à 00:15 UTC a refusé le 12 août parce que le
segment `collector-control` de la veille était encore actif. Ce stream n'avait
pas reçu de nouvel événement après minuit pour déclencher sa rotation naturelle.
La maintenance clôt désormais sous verrou uniquement les segments non vides
strictement antérieurs au cutoff, avant l'analyse. Un test avec deux instances
du store vérifie que le writer déjà actif invalide son cache, reprend la chaîne
et ouvre le segment du nouveau jour sans perte. La maintenance refuse aussi la
journée UTC en cours afin qu'une reprise opérateur ne puisse pas publier une
preuve partielle.

Le correctif a été déployé par sélection du release
`20260813T185440Z-65b43732befa`, sans recréer collector, observer ou watchdog.
Seule la maintenance a reçu la nouvelle image. La reprise explicite du 12 août
a analysé 8 320 373 événements, publié un rapport v2 au checksum valide et
compressé 94 segments, avec environ 106 à 124 Mio observés sur la passe. Le
marker final est `completed`, les deux streams ne gardent plus que leurs
segments actifs du 13 août et l'observer expose zéro incident actif.

La continuité du 13 août est préservée : même ID de conteneur collector, même
`run_id`, même heure de démarrage, zéro restart/OOM, aucune reconnexion ajoutée,
aucun `shutdown`, zéro drop, zéro malformed et file vide. Le rapport du 12 août
reste correctement non qualifié avec 11 pannes collector, 801 347 gaps et 48
motifs ; aucun seuil n'a été modifié.

## Continuité du diagnostic M3 à minuit

Le rapport complet du 13 août a confirmé la rotation, mais a aussi exposé une
erreur limitée à la cause des gaps. Le collector tournait sans redémarrage depuis
le 12 août ; son premier événement de contrôle du 13 août était pourtant un
`disconnected` à 02:20 UTC. Comme l'analyseur ne voyait pas le `connected` du
segment UTC précédent, il attribuait les gaps antérieurs à
`collector_not_running`.

La machine d'état M3 déduit maintenant les deux continuités démontrables par les
événements du jour : un premier `disconnected` clôt une session ouverte avant
minuit, tandis qu'un premier `reconnected` clôt une panne ouverte avant minuit.
Un `shutdown` isolé ne suffit pas à présumer une connexion. Une vraie première
connexion du jour continue de laisser la période antérieure hors session. Deux
tests de frontière et un cas `shutdown` isolé s'ajoutent à celui du temps
réellement hors session ; la suite compte 120 tests réussis, Ruff est propre sur
les fichiers modifiés et Mypy reste sans erreur.

Le changement conserve le schéma M3 v2 et les agrégats de durée : il corrige
l'explication `collector_not_running` / `market_stale` / `collector_outage`, pas
la couverture ni le verdict. Le rapport checksumé du 13 août n'est ni recalculé
ni écrasé.
