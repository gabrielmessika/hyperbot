# Rapport d'implémentation M6

**Date :** 11 août 2026  
**Statut logiciel :** terminé  
**Trading live :** impossible.

## Stratégies pures

`OutcomeMakerStrategy` et `GrowthMakerStrategy` reçoivent un `MarketState`
immuable et retournent exclusivement des `QuoteIntent`. Elles ne dépendent ni
d'un gateway, ni d'un SDK exchange.

Le moteur outcomes inclut frais aller-retour, sélection adverse, coût
d'inventaire, incertitude et marge opérationnelle dans son demi-spread. Il
rejette données stale/unhealthy, carnet croisé et dernière minute avant
expiration. Le moteur growth utilise la médiane oracle/mark/microprice, bornée
autour de l'oracle, puis applique frais runtime et skew d'inventaire. Les tailles
sont arrondies de façon à respecter le notional minimal.

## Superviseur unique

`RiskSupervisor` est la seule classe pouvant produire un `ApprovedIntent`. Il
contrôle avant chaque approbation :

- heartbeat, stale, pertes inconnues et stop journalier ;
- ordre orphelin et divergence position locale/exchange ;
- hash de définition courant, tick et ALO ;
- notional, inventaire, gross/net delta et DEX HIP-3 unique ;
- payoff YES/NO par outcome, perte par marché, perte agrégée et groupe corrélé ;
- drawdown soft et hard.

Le drawdown hard est latched. Une amélioration ultérieure de l'equity ne suffit
pas : seul un `OperatorResetAuthorization` confirmé le réarme, et la condition
de hard stop est réévaluée immédiatement. Une réduction soft qui tomberait sous
le minimum d'ordre est rejetée au lieu de créer un ordre invalide.

## Gateway shadow

`ShadowExecutionGateway` transforme uniquement des approbations en records
`shadow_only`. Il n'expose aucune méthode `send_order`. Sa réconciliation prend
l'état exchange simulé comme autorité, et retourne positions divergentes,
ordres orphelins et ordres locaux absents de l'exchange.

## Vérification

Les tests couvrent stratégies pures, stale/unhealthy, worst-case YES/NO, caps
corrélés, limites growth, changement de tick/spec, heartbeat, orphan, mismatch,
perte inconnue, stop journalier, soft/hard drawdown, reset opérateur et absence
de chemin d'ordre.

Gate complète après M6 : 80 tests réussis, Ruff sans erreur et Mypy strict sans
erreur, avec non-régression intégrale M0–M5.

Cette livraison ne signifie pas que les stratégies ont passé M5. Elles restent
des générateurs d'intentions désactivés destinés au replay et au shadow.
