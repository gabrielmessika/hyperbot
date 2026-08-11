# Rapport d'implémentation M2

**Date :** 11 août 2026  
**Périmètre :** catalogue public, collector WebSocket public, segmentation et
intégrité append-only.

## Résultat

Le lot M2 est implémenté sans clé, signature, wallet ni endpoint d'ordre. Le
smoke test public du 11 août 2026 a produit :

- 500 définitions de marché : 232 perps core, 252 perps HIP-3 et 16 côtés
  d'outcomes ;
- zéro erreur de catalogue non fatale pendant cette capture ;
- 119 événements WebSocket persistés en cinq secondes sur BTC,
  `cash:AMZN` et `#10570` ;
- 52 événements BTC, 32 événements HIP-3 et 35 événements outcome ;
- 6 événements L2, 20 BBO et 93 trades ;
- zéro drop, zéro message malformé et zéro reconnexion pendant le smoke test ;
- 2 événements de contrôle (`connected`, `shutdown`) et 121 événements totaux
  persistés ;
- validation réussie des chaînes et manifests des trois streams produits.

La capture complète du catalogue est conservée dans
`catalog-1786437256-76047f05.json`, avec un checksum SHA-256 indépendant. Les
données WebSocket brutes sont des artefacts locaux ignorés par Git ; leurs
checksums de segments restent dans les manifests locaux.

## Contrats et limites explicites

- Les frais enregistrés sont des références tier 0 avant remises propres au
  compte, jamais une estimation personnalisée.
- Le tick et le lot des outcomes ne sont pas publiés par les réponses de
  métadonnées observées. Ces champs restent nuls et portent le flag
  `outcome_tick_and_lot_unpublished` ; aucune valeur n'est inventée.
- Bien que la documentation présente `outcomeMeta` comme limité au testnet,
  l'endpoint mainnet public a répondu pendant ce test. Le parseur traite son
  absence comme un problème non fatal afin de rester fail-closed face à une
  évolution d'API.
- Ce smoke test prouve le câblage et l'intégrité, pas la gate de qualité de sept
  jours du lot M3, ni la position de file, ni un fill maker.

## Vérifications automatisées

Les tests couvrent les fixtures core/HIP-3/outcomes, les champs API inconnus,
une modification de tick, l'absence de métadonnées outcome, un serveur
WebSocket factice, reconnexion, heartbeat, message malformé, surcharge bornée,
arrêt drainé, suppression/troncature/corruption de segment, reprise sur ligne
partielle et replay identique après compression.

Résultat final : 50 tests réussis, lint Ruff sans erreur et typage Mypy strict
sans erreur.

## Impact opérationnel

Aucun service n'a été déployé et aucune configuration TRIDENT n'a été modifiée.
Le collector est une commande locale explicite, limitée à `l2Book`, `bbo`,
`trades` et `ping`. Les nouvelles données utilisent l'enveloppe segmentée v2 ;
le store JSONL v1 de M1 reste lisible et inchangé.
