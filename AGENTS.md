# HyperBot Agent Instructions

Ces instructions constituent le contexte persistant du projet HyperBot pour les
agents de développement.

## Sources de vérité

- La spécification stratégique est `HYPERBOT_FOUNDATION.md`.
- Le plan d'exécution et statut de développement courant est `FOLLOW_UP.md`.
- En cas de contradiction, la fondation gagne sur la thèse, les limites de
  risque et les gates de promotion ; `FOLLOW_UP.md` gagne sur l'ordre des tâches
  et leur statut.
- Le dossier `../trident` présent dans le workspace est une référence historique
  en lecture seule. Ne jamais le modifier depuis une tâche HyperBot sans demande
  explicite de l'utilisateur.

## Mission et périmètre actuel

- HyperBot est un nouveau projet indépendant de TRIDENT.
- La phase initiale couvre le collector, l'event store, le replay avec modèle de
  file, les stratégies shadow et le superviseur de risque.
- Le lot courant est indiqué dans `FOLLOW_UP.md`; le mettre à jour avec toute
  livraison matérielle ou nouvelle décision technique.
- Le trading live est désactivé. Aucun changement de configuration, refactor ou
  test ne doit pouvoir envoyer un ordre réel sans autorisation explicite.
- Ne pas importer les stratégies, planners ou paramètres directionnels des pods
  TRIDENT. Une brique d'infrastructure peut être extraite seulement si son
  comportement est documenté et couvert par des tests HyperBot.
- Ne pas créer de dépendance runtime vers `../trident`.

## Conventions de développement

- Python 3.11 minimum, package source dans `src/hyperbot/`.
- Code, identifiants et commentaires techniques en anglais. Documentation et
  rapports de recherche en français.
- Utiliser des types explicites et des modèles immuables pour les événements de
  marché, intentions de quote, cycles d'ordre et fills.
- Les données brutes sont append-only. Toute feature dérivée doit être
  reproductible depuis les données brutes et identifier la version du code et
  de la configuration.
- Le même contrat de stratégie et le même superviseur de risque doivent tourner
  en replay, shadow et live ; seuls l'horloge et l'execution gateway changent.
- Toute hypothèse de fill doit être testée avec un modèle central et un modèle
  pessimiste. `touch = fill` ne peut servir que de plafond optimiste.

## Sécurité et secrets

- Ne jamais committer de seed phrase, clé privée, API wallet ou contenu de
  fichier `.env`.
- Les exemples de configuration doivent utiliser des valeurs factices et garder
  `live_enabled = false` et `shadow_only = true`.
- Une perte de flux, une donnée stale, un ordre orphelin ou une divergence de
  position doit provoquer un blocage fail-closed.
- Aucun martingale, rattrapage de performance ou augmentation automatique du
  levier n'est autorisé.

## Données et rapports

- Données locales : `data/`, ignorées par Git sauf manifestes et petits fixtures.
- Rapports de replay : `data/replay_reports/` ou `tmp/`, sans écraser une
  baseline publiée.
- Toute expérience doit enregistrer `run_id`, hash de configuration, version du
  code, période de données, frais, latence et modèle de fill.
- Les preuves historiques TRIDENT restent dans `/workspaces/trident`. Si elles
  deviennent nécessaires au nouveau dépôt, copier un snapshot et son checksum,
  pas un lien mutable implicite.
- Classer chaque dataset : `A` pour le nouveau collector HyperBot, `B` pour les
  archives HIP-4, `C` pour les snapshots/replays A/C et GBOT.
- Les données B/C servent à la pré-recherche, aux markouts, fixtures et replays
  optimistes. Elles ne peuvent jamais valider seules la position de file, un
  fill maker central/pessimiste ou une promotion canary.
- Tout import legacy doit enregistrer chemin source, SHA-256, période, nombre de
  lignes, schéma, fréquence, trous, transformations et version d'adaptateur.
- Ne jamais copier en masse les archives TRIDENT dans Git. Versionner seulement
  manifestes, petits fixtures et rapports de qualité.

## Commandes usuelles

- Installer/synchroniser : `uv sync`
- Tests : `uv run pytest`
- Lint : `uv run ruff check .`
- Format : `uv run ruff format .`
- Types : `uv run mypy src`

## Critères avant livraison

- Ajouter ou mettre à jour les tests proportionnellement au changement.
- Vérifier les chemins replay, shadow et fail-closed concernés.
- Signaler explicitement tout impact sur déploiement, collecte ou format des
  données.
- Préserver les changements non liés déjà présents dans le worktree.
- Ne jamais présenter un scénario de rendement comme une garantie ou une
  validation hors échantillon.
