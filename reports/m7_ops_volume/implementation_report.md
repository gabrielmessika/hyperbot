# Rapport d'implémentation — Volume de données M7-Ops

## Bascule

Le Volume Hetzner `106591803` fournit 100 Go en EXT4. Il est monté directement
sur `/opt/hyperbot/shared/data` via `/etc/fstab`; le conteneur voit donc `/dev/sdb`
sur `/app/data`. Après migration et compression, environ 93 Gio sont libres.

Avant la bascule, le collector a été arrêté proprement. Les 24 fichiers source
ont été copiés avec propriétaires, modes et attributs, puis comparés avec
`rsync --checksum` : zéro différence. La validation avant et après copie donnait
les mêmes 505 166 records marché, 1 641 contrôles et derniers hashes. La copie
source de 468 Mio reste récupérable dans
`/opt/hyperbot/shared/data.root-backup-volume-migration` ; aucune donnée TRIDENT
ou TRIDENT-HIP4 n'a été modifiée ou supprimée.

## Garde-fou de montage

`HYPERBOT_DATA_MOUNT_SENTINEL` exige le fichier régulier
`/app/data/.hyperbot-volume-106591803`. Tous les services refusent leur
configuration si ce fichier manque, est un symlink, est trop volumineux ou sort
de la hiérarchie data. Un test négatif en production retourne bien le code 2
avec le Volume absent simulé. Le dashboard expose seulement l'activation du
garde-fou, jamais son chemin hôte.

## Incident de diagnostic et correction

Le premier redémarrage sur le Volume a révélé un blocage de persistance pendant
la maintenance initiale : la validation et la compression tenaient le verrou du
stream pendant toute l'opération. Le run a accumulé des drops et est exclu de
toute qualification M3.

La compression travaille désormais hors verrou sur les segments immuables,
revérifie le checksum sous verrou, puis publie atomiquement gzip et manifest. Un
append concurrent est couvert par test. Le marqueur quotidien réutilise aussi
le rapport déjà finalisé après un changement de configuration, au lieu de
relancer une maintenance historique. La profondeur et la capacité de la file de
persistance sont publiées dans le statut et l'UI.

## Validation finale

- release : `/opt/hyperbot/releases/20260811T155300Z-8e73ae32bdef` ;
- 109 tests, Ruff, mypy et syntaxe JavaScript réussis ;
- 24 marchés et 52 couples canal/marché observés, aucun manquant ;
- API authentifiée 200, dashboard sans auth 401, POST 405 ;
- collector et observer healthy, maintenance et watchdog running ;
- zéro drop, message malformé, reconnexion ou backlog sur le run final ;
- validation finale : 574 990 records marché et 1 649 contrôles, chaîne intacte ;
- quatre services TRIDENT-HIP4 restés actifs avec les mêmes uptimes.

La projection courante permet la gate de 30 jours avec le Volume et la réserve
de 10 Gio. Elle ne garantit pas 60 jours locaux : un export froid checksummé
reste nécessaire. Le backup racine ne devra être supprimé qu'après une première
maintenance quotidienne complète et un fetch externe vérifié.
