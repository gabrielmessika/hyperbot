# Rapport d'implémentation M7

**Date :** 11 août 2026  
**Statut logiciel :** terminé  
**Qualification shadow 14 jours :** non acquise  
**Canary M8 :** bloqué et non autorisé.

## Runner shadow

`ShadowRunner` exécute le même contrat stratégie et le même superviseur M6 que
le replay. Un cycle :

1. calcule des `QuoteIntent` ;
2. les journalise ;
3. fait évaluer le batch séquentiellement par le superviseur, chaque
   approbation hypothétique alimentant les caps de la suivante ;
4. journalise approbations/rejets et actions ;
5. stage uniquement les approbations dans `ShadowExecutionGateway` ;
6. publie des événements `shadow_only` expirables.

Le gateway ne possède toujours aucune méthode d'envoi d'ordre. Les événements
M7 sont acceptés par les stores M1/M2 et portent le contexte run/code/config.

## Fills, markouts et restart

Les résultats central et pessimiste M4 sont comparés par hash, nombre de fills,
notional et markout 30 s. Pour chaque quote, un
`ShadowFillEvaluationEvent` relie la prédiction de fill aux markouts observés
100 ms/1 s/5 s/30 s.

Au restart, la réconciliation M6 rend l'état exchange simulé autoritaire. Toute
position divergente, quote locale manquante ou ordre exchange orphelin bloque
les nouveaux cycles et incrémente les violations. Un nouveau rapprochement
propre est nécessaire pour débloquer le runner.

## Observabilité et gate

Le rapport quotidien JSON/Markdown checksummé enregistre intentions,
approbations/rejets, quotes stagées, fills estimés, markouts négatifs,
violations, divergences restart, qualification M3, compatibilité replay et
stress latence.

La gate exige quatorze dates consécutives avec :

- zéro violation/rejet risque ;
- zéro divergence restart ;
- journée qualité M3 qualifiée ;
- shadow compatible avec le replay central ;
- stress latence tolérable.

Même avec quatorze journées, le résultat est seulement
`eligible_for_canary_discussion`; `canary_authorized` reste toujours faux. M8
exige encore données réelles, review sécurité et autorisation utilisateur
séparée.

## Vérification

Les tests couvrent cycle complet, stores d'audit, batch de caps, absence d'ordre,
fill central versus pessimiste, markouts, restart bloqué/débloqué, état exchange
autoritaire, rapport checksummé, gate 13/14 jours, reset sur incident et blocage
M8.

Gate finale M0–M7 : 86 tests réussis, Ruff sans erreur, Mypy strict sans erreur
et `git diff --check` propre.

La qualification temporelle n'est pas acquise : aucun historique de quatorze
jours n'a été fabriqué. Le runner reste local et aucun service n'a été déployé.
