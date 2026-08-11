# Rapport d'implémentation M3

**Date :** 11 août 2026  
**Statut logiciel :** terminé  
**Gate temporelle :** en collecte, non acquise.

## Livraison

M3 transforme les événements publics M2 en un rapport quotidien déterministe
JSON et Markdown. Pour chaque marché attendu, il mesure :

- couverture et durée stale ;
- latences exchange → réception p50/p95/p99 et horloges négatives ;
- spreads p10/p50/p90, profondeur moyenne bid/ask ;
- nombre et notional des trades ;
- gaps mineurs/majeurs et leur cause.

Les périodes sont classées séparément en `collector_outage`,
`collector_not_running` et `market_stale`. Une absence de trades n'est donc pas
assimilée à une panne ; la couverture attendue repose sur les flux de carnet et
les événements de cycle de vie du collector.

La gate codée exige sept journées UTC consécutives qualifiées avant de passer à
la collecte probatoire, puis trente journées consécutives. Une journée échouée
ou manquante remet la séquence consécutive à zéro. Les rapports portent le
niveau A, le run, la version, les hashes de configuration et un checksum propre.

## Vérification

- métriques et percentiles déterministes ;
- panne collector et marché stale distingués ;
- temps hors session explicitement identifié ;
- JSON, Markdown et checksum testés ;
- gates 6/7/30 jours et reset sur échec testés ;
- smoke report sur la capture M2 locale : correctement non qualifié, car la
  capture ne dure que cinq secondes et ne représente pas une journée.

Gate complète après M3 : 54 tests réussis, Ruff sans erreur et Mypy strict sans
erreur. Cela rejoue également les tests M0, M1, M1L et M2.

## Limite

Le logiciel de M3 est terminé, mais HyperBot ne possède pas encore sept jours,
et encore moins trente jours, de données A continues. Aucun résultat M4+ ne
pourra transformer cette absence en preuve de fill ou de promotion.
