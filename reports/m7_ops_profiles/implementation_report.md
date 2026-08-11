# Rapport d'implémentation — profils de collecte M7-Ops

## Objectif

Élargir dès le début la collecte de niveau A sans appliquer le coût du carnet L2
complet à tout l'univers. Le débit BTC observé avant compression était d'environ
0,74 Gio/jour, avec 45 Gio libres et une réserve fail-closed de 10 Gio.

## Profils retenus

Le profil `depth` collecte `l2Book`, `bbo` et `trades` :

- `BTC`, `ETH`, `HYPE`, `SOL`.

Le profil `breadth` collecte seulement `bbo` et `trades` :

- core : `PUMP`, `ZEC`, `XRP`, `LIT`, `KAITO`, `CRV`, `WLD`, `XMR`, `TAO`,
  `ADA`, `LINK`, `PAXG` ;
- HIP-3 : `xyz:SKHX`, `xyz:CL`, `xyz:BRENTOIL`, `xyz:SPCX`, `xyz:SP500`,
  `xyz:XYZ100`, `xyz:SILVER`, `xyz:GOLD`.

La sélection est manuelle et a été revérifiée le 11 août 2026 via l'endpoint
public officiel `metaAndAssetCtxs`. Elle ne constitue pas une promotion
automatique fondée sur le volume du jour.

## Contrat logiciel

- matrices de subscriptions déterministes et incluses dans le hash de config ;
- listes depth/breadth uniques, disjointes et limitées globalement à 1 000
  subscriptions ;
- ancienne configuration markets/channels encore lisible seule pour les tests
  et usages locaux, mais mélange ancien/nouveau refusé ;
- statut runtime et API exposent profils et nombre exact de subscriptions ;
- dashboard affiche le profil et les canaux propres à chaque marché ;
- la maintenance M3 continue d'attendre tous les marchés configurés.

Le test de charge initial a aussi révélé un rescan quadratique du segment actif.
Le store conserve désormais une position d'append vérifiée par identité de
fichier, taille et timestamps ; tout changement coopératif invalide ce cache et
la clôture relit toujours l'intégralité de la chaîne. Le writer groupe jusqu'à
256 événements sous un même verrou/descripteur et effectue un fsync des données
de marché tous les 100 records. Les événements de contrôle restent fsyncés à
chaque écriture. Un arrêt propre fsync et valide le reliquat.

Ce group commit borne à 99 records de marché la fenêtre théorique de perte lors
d'une panne électrique brutale, en échange d'un débit compatible avec l'univers
élargi. Cette fenêtre est versionnée dans le hash de configuration.

Les outcomes ne sont pas ajoutés à une whitelist statique : les IDs actifs
expirent quotidiennement. Une éventuelle collecte outcome devra utiliser une
rotation revue explicitement, sans auto-promotion du catalogue.

## Validation publique

Un smoke WebSocket réel de douze secondes a testé les 52 subscriptions. Les 24
marchés et les trois types de canaux attendus ont produit des événements : 2 787
événements persistés, zéro drop, zéro message malformé et aucun marché absent.
Un second smoke avec le store segmenté, le batch et le group commit actifs a
observé après dix secondes 2 107 événements persistés, une file vide et zéro
drop.

La gate temporelle M3 repart sur la première journée UTC complète utilisant une
configuration homogène. Les captures précédentes restent append-only avec leur
ancien hash et ne sont ni réécrites ni présentées comme preuve de la nouvelle
matrice.

## Redéploiement et capacité mesurée

Le release `/opt/hyperbot/releases/20260811T144555Z-7c52fbdb22bb` a été activé
le 11 août 2026. Le smoke distant a confirmé les 24 marchés et les 52 couples
canal/marché dans le flux brut du nouveau `run_id`, avec zéro drop, zéro message
malformé et zéro reconnexion. L'API authentifiée répond 200, le dashboard sans
authentification répond 401 et POST répond 405. Le store actif et sa chaîne de
hash ont été validés pendant la collecte.

Un premier échantillon serveur projette environ 12 Gio/jour bruts et 1,83
Gio/jour après gzip. Cette extrapolation dépend de l'activité de marché, mais
elle suffit à invalider une attente passive de 30 jours sur le disque actuel :
45 Gio sont libres, dont 10 Gio réservés au fail-closed, et une journée active
peut encore contenir environ 12 Gio non compressés.

L'inventaire strictement read-only du TRIDENT classique mesure 10,52 Go sous
`/opt/trident`, dont 8,14 Go de données et 2,35 Go de logs. La suppression de ses
cinq images Docker arrêtées ajouterait au plus environ 0,7 Go ;
`/opt/trident-hip4` et ses quatre conteneurs actifs sont exclus de ce calcul.
Même après ce gain, la capacité estimée reste d'environ 19 jours avec la réserve
de 10 Gio, donc insuffisante pour la gate M3 de 30 jours. Aucune donnée TRIDENT
n'a été supprimée.
