# Rapport d'implémentation M4

**Date :** 11 août 2026  
**Statut logiciel :** terminé  
**Preuve de promotion :** non acquise, dépend des données A M3.

## Moteur livré

Le replay M4 fournit une horloge virtuelle monotone et un ordering stable sur
timestamp, séquence source, marché et type d'événement. À entrée, code et
configuration identiques, le résultat et son SHA-256 sont identiques, même si
les événements sont fournis dans un ordre différent.

Trois modèles sont séparés :

- `pessimistic` : tout le volume visible devant la quote doit être consommé par
  un trade agressif avant le premier fill ; les annulations du carnet ne
  réduisent pas cette file ;
- `central` : fraction configurable de la file visible, réduction observable
  lors des updates L2, latence de placement/cancel et fills partiels ;
- `optimistic_touch` : la quote entière est remplie au premier touch/croisement,
  uniquement comme plafond explicitement étiqueté.

Les modèles central et pessimiste appellent la politique de preuve M1L et
refusent les niveaux B/C. L'absence de carnet L2 au moment de l'activation fait
échouer ces modèles au lieu de supposer une file nulle.

Chaque fill enregistre file avant/après, frais et markouts 100 ms, 1 s, 5 s et
30 s. Un markout n'est calculé que si un carnet existe dans la tolérance
configurée autour de l'horizon ; une observation très tardive ne remplit pas
rétroactivement un horizon court. Le PnL économique 30 s retranche tous les
frais et compte les markouts manquants.

Le runner `scripts/run_hyperbot_replay.py` vérifie le fixture d'entrée par
SHA-256, refuse d'écraser un rapport et peut produire automatiquement les stress
latence ×2 et frais ×2.

## Vérification

Les tests couvrent file pessimiste, file centrale, fills partiels, latence de
cancel, absence L2 fail-closed, autorisations A/B/C, borne touch, quatre
markouts, frais, PnL, stress et reproductibilité bit-à-bit.

Gate complète après M4 : 62 tests réussis, Ruff sans erreur et Mypy strict sans
erreur. Tous les tests M0 à M3 sont rejoués dans cette gate.

## Limite

Le fixture M4 valide le moteur, pas une rentabilité. Les modèles central et
pessimiste ne deviendront des preuves empiriques qu'avec des journées A ayant
passé la gate qualité M3. Le touch legacy reste une borne optimiste.
