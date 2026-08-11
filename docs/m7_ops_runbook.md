# Runbook M7-Ops — collecte publique HyperBot

## 1. Périmètre et état de sécurité

M7-Ops prépare HyperBot pour une collecte publique continue sur le même serveur
que TRIDENT, sans partager son répertoire, ses volumes, son projet Docker ou ses
secrets. Il ne déploie ni le runner shadow M7, ni un executor, ni un client de
signature.

Les protections sont cumulatives :

- le profil Compose `collector` est inactif par défaut ;
- `.env.hyperbot` démarre avec `HYPERBOT_COLLECTOR_ENABLED=false` ;
- le processus refuse `live_enabled=true` ou `shadow_only=false` ;
- la présence d'une clé HyperBot ou de `TRIDENT_SECRET_KEY` fait échouer le
  démarrage ;
- seul le port UI/API `3002/tcp` est publié ; il ne donne accès qu'à des lectures ;
- l'authentification du dashboard et de `/api/*` est obligatoire sur le bind
  public ; seul `/health` reste accessible sans identifiants ;
- les conteneurs sont non-root, read-only, sans capability et avec ressources
  bornées ;
- un watchdog séparé observe le statut partagé sans accès au socket Docker ;
- la whitelist de marchés est manuelle et exacte.

Une livraison du code ne constitue pas un déploiement. L'installation distante
et l'activation restent deux actions opérateur séparées.

## 2. Arborescence serveur

Le déploiement utilise exclusivement :

```text
/opt/hyperbot/
├── current -> releases/<release>
├── releases/<release>/
└── shared/
    ├── .env.hyperbot
    ├── ui_password
    ├── data/
    ├── logs/
    ├── reports/
    └── runtime/
```

Il ne lit et n'écrit rien sous `/opt/trident`, `/opt/trident-hip4` ou dans les
volumes Docker correspondants. Le projet Compose porte le nom stable
`hyperbot`. Son unique publication réseau est `0.0.0.0:3002` vers le service
observer. Les volumes data/runtime/reports de cet observer sont montés en
lecture seule.

## 3. Préflight local

Depuis `/workspaces/hyperbot` :

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run mypy src
bash -n deploy.sh scripts/*.sh
git status --short
```

Le worktree doit être propre : `deploy.sh` archive uniquement le commit courant
et refuse les modifications non committées.

Vérifier ensuite la capacité du serveur. Le seuil par défaut exige au moins
10 Gio libres pour déclarer le collector sain. Les limites initiales sont de
1 CPU/768 Mio pour le collector et 0,5 CPU/512 Mio pour la maintenance.

## 4. Installer sans démarrer

```bash
./deploy.sh --host trident-hetzner
```

Cette commande :

1. crée un release immuable sous `/opt/hyperbot/releases/` ;
2. initialise les répertoires partagés ;
3. crée, seulement s'il n'existe pas, un `.env.hyperbot` désactivé et mode 600 ;
4. crée un mot de passe UI aléatoire mode 600 sans l'afficher dans les logs ;
5. valide la configuration Compose et construit l'image ;
6. positionne le lien `current` sans démarrer le collector.

Un déploiement ne copie jamais `.env`, `data/`, `runtime/`, `logs/` ou les
archives TRIDENT : il utilise `git archive` sur le commit courant.

## 5. Configurer la whitelist et activer

Sur le serveur, éditer `/opt/hyperbot/shared/.env.hyperbot` :

```dotenv
HYPERBOT_COLLECTOR_ENABLED=true
HYPERBOT_LIVE_ENABLED=false
HYPERBOT_SHADOW_ONLY=true
HYPERBOT_COLLECTOR_MARKETS=BTC
HYPERBOT_COLLECTOR_CHANNELS=l2Book,bbo,trades
HYPERBOT_UI_HOST=0.0.0.0
HYPERBOT_UI_PORT=3002
HYPERBOT_UI_PUBLISH_HOST=0.0.0.0
HYPERBOT_UI_AUTH_REQUIRED=true
HYPERBOT_UI_AUTH_USERNAME=hyperbot
HYPERBOT_UI_AUTH_PASSWORD_FILE=/opt/hyperbot/shared/ui_password
```

`HYPERBOT_UID` et `HYPERBOT_GID` doivent correspondre au compte de déploiement.
Ne jamais ajouter une seed, clé wallet, clé API de signature ou variable issue
de `.env.trident`.

Une alerte HTTPS externe peut être configurée dans le fichier mode 600
`/opt/hyperbot/shared/alert_webhook_url`, monté uniquement dans le watchdog. Sa
valeur n'apparaît ni dans Compose, ni dans les rapports ou exports. Laisser ce
fichier vide désactive l'envoi externe.

```bash
chmod 600 /opt/hyperbot/shared/alert_webhook_url
$EDITOR /opt/hyperbot/shared/alert_webhook_url
```

Sans webhook, les transitions d'état et rappels de panne restent disponibles
dans les logs et `runtime/watchdog_status.json`.

Avant d'ajouter un outcome ou un marché HIP-3, capturer le catalogue :

```bash
/opt/hyperbot/current/scripts/hyperbot_server.sh catalog
```

Reporter ensuite l'identifiant `coin` exact dans la whitelist, puis démarrer :

```bash
/opt/hyperbot/current/scripts/hyperbot_server.sh start
/opt/hyperbot/current/scripts/hyperbot_server.sh status
/opt/hyperbot/current/scripts/hyperbot_server.sh health
/opt/hyperbot/current/scripts/hyperbot_server.sh ui-health
```

L'ajout ou le retrait d'un marché exige une modification explicite de
`.env.hyperbot` et un `restart`. Un nouveau hash de configuration et un nouveau
`run_id` sont alors produits ; aucune rotation implicite n'est autorisée.

### Accès public au dashboard

Si UFW est actif, ouvrir explicitement le port après avoir choisi la politique
d'accès réseau appropriée :

```bash
sudo ufw allow 3002/tcp
sudo ufw status
```

Le dashboard devient accessible sur `http://<adresse-publique>:3002/`. Le nom
d'utilisateur initial est `hyperbot`. Lire le mot de passe depuis un terminal
administrateur, sans le recopier dans `.env` :

```bash
cat /opt/hyperbot/shared/ui_password
```

Basic Auth protège l'accès mais ne chiffre pas HTTP. Pour un accès à travers
Internet, placer le port derrière un reverse proxy TLS, un VPN ou une allowlist
IP. Ne jamais publier une copie du fichier `ui_password`. Une rotation se fait
en remplaçant ce fichier par une valeur d'au moins 16 caractères puis en
recréant le service `observer`.

L'interface ne contient aucun bouton start/stop/restart. Toute méthode autre que
GET ou HEAD retourne `405 read_only_api`, et il n'existe aucun endpoint d'ordre,
de changement de configuration ou de contrôle de processus.

### API de lecture

| Route | Auth | Contenu |
|---|---|---|
| `/health` | non | santé du seul service observer |
| `/api/overview` | oui | snapshot agrégé du dashboard |
| `/api/status` | oui | collector, maintenance, watchdog et guards |
| `/api/markets` | oui | whitelist et dernier catalogue vérifié |
| `/api/quality/latest` | oui | dernier rapport M3 checksumé |
| `/api/quality/history?limit=30` | oui | historique borné et progression 7/30 jours |
| `/api/incidents` | oui | incidents health/maintenance/qualité |
| `/api/storage` | oui | réserve disque et manifests append-only |
| `/api/shadow` | oui | dernière preuve M7 si elle existe |
| `/api/config` | oui | configuration non sensible effective |
| `/api/endpoints` | oui | contrat des routes de lecture |

Exemple :

```bash
curl --fail --user hyperbot http://127.0.0.1:3002/api/status
```

## 6. Exploitation quotidienne

Commandes disponibles :

```bash
./scripts/hyperbot_server.sh status
./scripts/hyperbot_server.sh health
./scripts/hyperbot_server.sh ui-health
./scripts/hyperbot_server.sh logs
./scripts/hyperbot_server.sh quality --date YYYY-MM-DD
./scripts/hyperbot_server.sh catalog
./scripts/hyperbot_server.sh restart
./scripts/hyperbot_server.sh stop
```

Le service `maintenance` produit à 00:15 UTC le rapport M3 de la veille. Il ne
lit que les segments UTC clôturés, refuse un segment encore mutable, écrit des
rapports déterministes et checksumés, puis compresse sans supprimer les données
brutes. Une deuxième exécution de la même journée réutilise la preuve valide.

Le healthcheck échoue notamment pour :

- statut absent ou stale ;
- collector arrêté ou WebSocket déconnecté ;
- dernier message public trop ancien ;
- divergence du hash de configuration ;
- garde live/shadow incorrecte ;
- réserve disque sous le seuil.

Le watchdog attend 120 secondes au démarrage, alerte sur transition vers
`unhealthy`, répète après un cooldown de 15 minutes et notifie le retour à
`healthy`. Son payload ne contient que l'état de santé, les raisons, les âges,
la réserve disque et le hash de configuration.

Les journaux Docker sont bornés à cinq fichiers de 10 Mio par service. Les
données A ne sont jamais supprimées automatiquement : une alerte disque impose
une intervention et une extension ou un archivage vérifié.

## 7. Fetch vérifié

Depuis le poste de développement :

```bash
./scripts/fetch_hyperbot_data.sh --days 3
./scripts/fetch_hyperbot_data.sh --date 2026-08-10
```

Le serveur construit d'abord un manifest des seuls segments clôturés et rapports
publics sélectionnés. Les `.open`, symlinks, `.env`, clés et données TRIDENT sont
exclus. Le fetch utilise cette liste puis recalcule taille et SHA-256 de chaque
fichier sous `data/server-fetches/<fetch-id>/`.

Une capture répétée garde son propre identifiant et manifest. Aucun résultat ne
doit être promu en baseline avant validation de son checksum et de sa provenance
niveau A.

## 8. Mise à jour et rollback

Une mise à jour inactive :

```bash
./deploy.sh --host trident-hetzner
```

Pour reconstruire et redémarrer un collector déjà explicitement activé :

```bash
./deploy.sh --host trident-hetzner --start-collector
```

Retour au release précédent, sans démarrage implicite :

```bash
./deploy.sh --host trident-hetzner --rollback
```

Ajouter `--start-collector` au rollback seulement après avoir vérifié les trois
gardes d'activation dans `.env.hyperbot`.

## 9. Critères du smoke test distant

Après la première installation :

1. `docker compose ... config --quiet` réussit ;
2. aucun conteneur HyperBot ne tourne avant activation ;
3. après activation, `collector` et `observer` sont healthy, tandis que
   `maintenance`/`watchdog` sont running ;
4. le statut indique `public_only=true`, `live_enabled=false`, aucun drop ;
5. un arrêt SIGTERM produit un événement `shutdown` et clôture les segments ;
6. un redémarrage reprend avec un nouveau `run_id` sans altérer les anciens
   segments ;
7. le rapport M3 de la veille et son SHA-256 sont présents ;
8. un fetch local passe la vérification intégrale ;
9. `/health` répond sans auth, `/api/status` exige l'auth et une requête POST
   retourne 405 ;
10. le dashboard est joignable de l'extérieur sur le port 3002 et ne présente
   aucun contrôle start/stop ;
11. TRIDENT A/C et HIP-4 conservent leurs conteneurs, volumes et healthchecks
   inchangés.

Le smoke test ne doit envoyer aucun ordre. Le runner shadow M7 ne sera déployé
qu'après les gates M3 puis M5 ; M8 reste bloqué.
