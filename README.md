# HyperBot

HyperBot est un projet indépendant de recherche et d'automatisation de market
making sur Hyperliquid, conçu pour tester des edges mesurables sans confondre
backtest et performance exécutable.

La source de vérité initiale est
[`HYPERBOT_FOUNDATION.md`](HYPERBOT_FOUNDATION.md). Elle décrit l'architecture,
les hypothèses de rendement, les limites de risque et les critères de promotion
du replay vers le shadow puis le canary.

Le statut d'implémentation et les prochains lots sont suivis dans
[`FOLLOW_UP.md`](FOLLOW_UP.md).

## État actuel

- phase : instrumentation locale, lot M1 terminé ;
- trading live : désactivé ;
- livré : contrats d'événements, configuration fail-closed et event store ;
- prochaine priorité : catalogue de marchés et collector public ;
- dépôt TRIDENT : référence historique uniquement.

## Démarrage

```bash
uv sync
uv run pytest
uv run ruff check .
```

Ouvrir `hyperbot.code-workspace` pour charger HyperBot avec TRIDENT comme dossier
de référence séparé.
