# Rapport d'implémentation — M7-Ops

## Résultat

M7-Ops fournit le paquet d'exploitation nécessaire à une collecte publique
HyperBot continue sur le serveur utilisé par TRIDENT, dans un périmètre
strictement séparé. Tous les composants sont désactivés par défaut et aucun
executor, endpoint d'ordre ou secret de signature n'a été ajouté.

## Livrables

- runtime collector SIGTERM avec statut atomique et métriques de connexion ;
- configuration d'environnement stricte, hashée et fail-closed ;
- healthcheck sur fraîcheur du statut, connexion publique, dernier message,
  guards de mode, hash de configuration et réserve disque ;
- watchdog sans socket Docker, transitions/cooldown/récupération et webhook HTTPS
  optionnel sans fuite de sa valeur ;
- maintenance UTC idempotente : rapport M3 checksumé et compression lossless ;
- lecture ciblée des segments clôturés d'une date UTC sans charger tout
  l'historique en mémoire ;
- export public immuable avec manifest, liste exacte et SHA-256, refus des
  symlinks, traversées, segments ouverts et fichiers d'environnement ;
- Dockerfile non-root et Compose sans port, read-only, capabilities supprimées,
  ressources et logs bornés ;
- image collector sans SDK Hyperliquid ni bibliothèque de signature : seul le
  transport WebSocket public reste installé ;
- déploiement par releases committés, activation séparée et rollback ;
- commandes serveur et fetch distant ;
- runbook `docs/m7_ops_runbook.md`.

## Décisions

- whitelist initiale manuelle stricte ; aucune découverte automatique ne devient
  une subscription sans revue opérateur ;
- `/opt/hyperbot` et projet Compose `hyperbot`, indépendants de TRIDENT ;
- `.env.hyperbot` ne contient aucun secret de trading ;
- données A jamais supprimées automatiquement ; seuil disque fail-closed ;
- déploiement initial limité au collector public et à sa maintenance M3 ; runner
  shadow M7 encore inactif.

## Validation

Les tests couvrent configuration dangereuse, fuite de secret, service WebSocket
factice, arrêt propre, health fail-closed, alertes/cooldown, maintenance
idempotente, segmentation UTC, manifest/export, corruption, symlink et traversée
de chemin. Les scripts shell sont validés syntaxiquement. La régression complète,
Ruff et mypy strict sont exigés avant le commit du lot.

Validation finale locale :

- `uv sync --frozen` : succès ;
- `pytest -q` : 96 tests passés ;
- `ruff check .` : succès ;
- `mypy src` strict : succès sur 36 fichiers source ;
- syntaxe Bash : succès ;
- parsing YAML et invariants statiques du Compose : succès ;
- build sdist/wheel : succès ;
- arbre runtime : `websockets` uniquement, sans SDK Hyperliquid.

Docker n'est pas installé dans l'environnement de développement courant : la
validation réelle `docker compose config/build`, l'installation SSH et le smoke
test serveur restent des étapes opérateur documentées, non simulées comme
acquises.
