# Rapport d'implémentation M5

**Date :** 11 août 2026  
**Statut logiciel :** terminé  
**Edge OOS :** non démontré.

## Outcomes

Le benchmark initial calcule la probabilité d'un digital à dérive nulle à
partir du spot, du strike, du temps restant et d'une volatilité réalisée
annualisée calculable depuis les prix antérieurs. La calibration isotone est
ajustée uniquement sur la fenêtre de calibration de chaque fold.

L'évaluateur impose des fenêtres train/calibration/test strictement
chronologiques. Les observations de calibration dont le settlement empiète sur
la purge avant le test sont exclues, et les tests de folds ne peuvent pas se
chevaucher. Il publie Brier, log loss, PnL maker, benchmark « ne rien faire »,
comparaison legacy, profit factor et drawdown par fold.

Le bootstrap rééchantillonne des clusters journée × marché avec seed enregistré,
pas des fills indépendants. Les résultats sont ventilés par sous-jacent,
marché, régime de volatilité et temps avant expiration.

## Scanner HIP-3

Le scanner recroise chaque observation avec la révision runtime de
`MarketDefinition`. Il refuse un marché inconnu, inactif, non HIP-3, hors growth
mode, sans frais connus, trop serré, trop peu liquide/profond, stale ou affecté
par un incident deployer. Le spread net retranche deux fois le maker fee runtime
et enregistre le hash exact de la définition et la base de frais.

## Discipline de recherche

`VariantJournal` conserve chaque variante dans un JSONL append-only chaîné par
SHA-256, refuse les identifiants dupliqués et détecte corruption ou suppression
intermédiaire. Chaque entrée fixe hypothèse, code, hash de configuration,
périodes train/calibration/test et métriques.

La gate de recherche refuse notamment : moins de trois folds OOS, un fold non
positif, PF insuffisant, drawdown excessif, borne bootstrap non positive,
replay central non positif, pessimiste catastrophique, stress frais sous PF 1
ou contribution d'un sous-jacent supérieure à 40 %. Elle autorise au plus une
étape de recherche shadow ; elle n'autorise jamais M8.

## Vérification et limite

Gate complète après M5 : 67 tests réussis, Ruff sans erreur et Mypy strict sans
erreur, avec non-régression M0–M4.

Les tests prouvent la séparation temporelle et les règles de décision, pas un
edge économique. Les résultats legacy gardent `legacy_research_only`; aucune
promotion réelle ne sera possible avant données A qualifiées et seuils OOS.
