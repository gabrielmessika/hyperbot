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
