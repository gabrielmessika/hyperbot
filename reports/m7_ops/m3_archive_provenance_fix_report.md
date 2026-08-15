# Rapport M7-Ops — M3 v3, archivage et provenance

**Date :** 15 août 2026

## Résultat

Le défaut M3 provenait d'une confusion entre deux phénomènes : une
indisponibilité prouvée du collector et l'absence naturelle de mise à jour sur
un flux de carnet événementiel. Le schéma v3 conserve les deux mesures mais ne
qualifie la journée qu'avec la disponibilité opérationnelle, la présence des
canaux attendus et la cohérence des timestamps. Les seuils 99 %, 500 ms et 5 s
ne sont pas assouplis.

## Changements opérationnels

- couverture opérationnelle globale, gaps majeurs collector et périodes
  `collector_not_running` explicites ;
- fraîcheur et gaps marché conservés séparément par symbole ;
- maintenance quotidienne dédupliquée après la première tentative du jour ;
- SHA Git complet obligatoire dans le contexte des événements serveur ;
- tier froid séparé, checksumé et rejouable, avec 30 jours chauds et conservation
  froide sans purge automatique ;
- export vérifié capable de sélectionner les segments des deux tiers.

## Sécurité et activation

Le tier froid est désactivé par défaut. Son activation exige un root distinct,
un sentinel de montage et une réserve disque suffisante. Le collector, le
watchdog et l'observer montent l'archive en lecture seule ; seule la maintenance
peut y publier un segment. Aucun executor ou client de signature n'est ajouté.

Les rapports v2 existants restent immuables et ne sont jamais promus par
réinterprétation. L'observer les exclut explicitement du compteur et la gate v3
repart du premier jour complet produit avec le nouveau schéma.
