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
    ├── archive/              # montage froid distinct, optionnel
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

Lors de la toute première installation, `/opt` n'est généralement pas
inscriptible par le compte de déploiement. Créer une fois la racine étroite avec
un compte administrateur, puis rendre la main au compte non-root :

```bash
sudo install -d -o trident-deploy -g trident-deploy -m 0750 /opt/hyperbot
```

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
HYPERBOT_CODE_COMMIT=<sha-git-complet-rempli-par-deploy.sh>
HYPERBOT_COLLECTOR_DEPTH_MARKETS=BTC,ETH,HYPE,SOL
HYPERBOT_COLLECTOR_BREADTH_MARKETS=PUMP,ZEC,XRP,LIT,KAITO,CRV,WLD,XMR,TAO,ADA,LINK,PAXG,xyz:SKHX,xyz:CL,xyz:BRENTOIL,xyz:SPCX,xyz:SP500,xyz:XYZ100,xyz:SILVER,xyz:GOLD
HYPERBOT_PERSISTENCE_BATCH_SIZE=256
HYPERBOT_FSYNC_EVERY_RECORDS=100
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

Le profil `depth` souscrit `l2Book`, `bbo` et `trades`. Le profil `breadth`
souscrit seulement `bbo` et `trades`. Les deux listes doivent être disjointes ;
la présence simultanée des anciennes variables `HYPERBOT_COLLECTOR_MARKETS` ou
`HYPERBOT_COLLECTOR_CHANNELS` provoque un refus fail-closed.

L'ajout ou le retrait d'un marché exige une modification explicite de
`.env.hyperbot` et un `restart`. Un nouveau hash de configuration et un nouveau
`run_id` sont alors produits ; aucune rotation implicite n'est autorisée.
Les outcomes `#...` expirant quotidiennement ne sont pas placés dans cette
whitelist statique : leur rotation devra rester séparée et revue manuellement.

La persistance groupe jusqu'à 256 événements et fsync les données de marché
tous les 100 records ; les contrôles de connexion/gap/shutdown restent fsyncés
immédiatement. Un arrêt propre valide et fsync le reliquat. Augmenter ces valeurs
élargit la fenêtre de perte possible en cas de panne électrique et exige une
nouvelle mesure de débit ainsi qu'une review explicite.

### Volume de données dédié

Quand `/opt/hyperbot/shared/data` est un Volume séparé, créer sur ce Volume un
petit fichier sentinel et configurer son chemin vu du conteneur :

```dotenv
HYPERBOT_DATA_MOUNT_SENTINEL=/app/data/.hyperbot-volume-<id>
```

Le collector, la maintenance, le watchdog et l'observer refusent alors de
démarrer si ce fichier est absent, est un symlink, dépasse 4 Kio ou ne se trouve
pas dans la hiérarchie de `HYPERBOT_DATA_ROOT`. Le répertoire vide présent sous
le point de montage du disque système ne doit jamais contenir ce sentinel : une
absence du Volume reste ainsi fail-closed au lieu de rediriger silencieusement
les écritures vers le disque racine.

Un Volume Hetzner n'est pas inclus dans les snapshots ou backups du serveur.
Les segments clôturés doivent donc aussi être exportés vers un stockage séparé
avec leur manifest et leur SHA-256.

### Tier froid pour une rétention de 60 jours

Le tiering conserve 30 jours sur le Volume chaud et déplace les segments plus
anciens vers un second montage. Il copie chaque gzip dans un fichier temporaire,
revérifie le SHA-256 stocké et le hash du contenu décompressé, publie le manifest
d'archive puis seulement retire la copie chaude. Le manifest du store continue
de référencer le segment dans le tier `archive`, ce qui préserve replay et chaîne
de hashes.

Préparer un Volume distinct sur `/opt/hyperbot/shared/archive`, créer un sentinel
uniquement sur ce Volume, puis configurer :

```dotenv
HYPERBOT_HOST_ARCHIVE_DIR=/opt/hyperbot/shared/archive
HYPERBOT_ARCHIVE_ENABLED=true
HYPERBOT_ARCHIVE_ROOT=/app/archive/collector
HYPERBOT_ARCHIVE_MOUNT_SENTINEL=/app/archive/.hyperbot-archive-volume-<id>
HYPERBOT_HOT_RETENTION_DAYS=30
HYPERBOT_MINIMUM_RETENTION_DAYS=60
```

Le collector, la maintenance, le watchdog et l'observer refusent cette
configuration si le sentinel manque, si les roots chaud/froid se chevauchent ou
si la réserve disque du tier froid passe sous le seuil. Seule la maintenance a
un montage archive en écriture. Les autres services le voient en lecture seule.
L'archive n'est jamais purgée automatiquement ; une politique au-delà de 60
jours exige une décision et une preuve séparées.

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
| `/api/incidents` | oui | incidents opérationnels actifs et anomalies du dernier rapport M3, dans deux blocs séparés |
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
Avant la lecture, il clôt atomiquement sous verrou un segment actif non vide
dont la date UTC est strictement antérieure au cutoff du rapport. Cela permet aux
streams de contrôle peu actifs de changer de journée sans attendre leur prochain
événement. Le segment du jour courant n'est jamais clôturé par ce chemin, et une
demande portant sur la journée courante ou une date future est refusée.

L'analyse journalière lit et valide les segments en flux. Latences et spreads
utilisent un spill temporaire sous `runtime/quality-spill/`, puis un tri externe
exact par blocs bornés ; tous les fichiers temporaires sont supprimés à la fin.
Les compteurs, durées et causes de gaps restent exhaustifs. Le rapport M3 v3
borne seulement les détails individuels à 1 000 par marché et expose
`total_gap_count`, `retained_gap_detail_count`, `gap_detail_limit_per_market` et
`gap_details_truncated` pour rendre tout échantillonnage explicite.
La qualification à 99 % porte sur l'indisponibilité opérationnelle reconstruite
depuis les cycles de vie collector. Un silence BBO/L2 événementiel reste une
mesure de fraîcheur du marché et n'imite plus une panne. Chaque canal de carnet
configuré reste toutefois obligatoire au moins une fois dans la journée ; son
absence échoue fail-closed.
Les rapports des schémas précédents restent consultables, mais ne sont jamais
comptés dans la séquence de promotion du schéma v3.
La compression lit et écrit les segments par blocs de 1 Mio, puis compare en
flux le hash du gzip et le hash du contenu décompressé aux valeurs du manifeste.
Elle ne matérialise jamais un segment complet et ne publie le `.gz` qu'après une
nouvelle vérification du segment source sous verrou.
Le statut `maintenance_status.json` publie un heartbeat pendant l'analyse et
après chaque segment compressé. L'observer déclare un incident si ce heartbeat
devient stale ou si le rapport attendu n'est toujours pas terminé après la grâce
opératoire.

Une date automatique terminée n'est plus relue à chaque poll de 60 secondes.
Elle est tentée une seule fois par processus ; la date suivante réarme
automatiquement le service. Après un restart, une preuve existante est validée
et réutilisée une seule fois. La commande opérateur `quality --date` n'est pas
dédupliquée.

Après une interruption brutale sans marker final, le service continu ne retente
pas indéfiniment la même date. Il reste actif mais fail-closed et exige une revue,
puis une relance explicite :

```bash
./scripts/hyperbot_server.sh quality --date YYYY-MM-DD
```

Cette commande `--once` est la seule reprise automatique contournée ; elle
réutilise un rapport déjà checksumé avant de reprendre la compression, mais
seulement si schéma, date, `run_id`, tier A et hash de configuration qualité
correspondent. Sinon elle échoue fail-closed sans écraser la preuve. Vérifier
ensuite `status`, `health`, `ui-health` et le marker quotidien. Ne jamais
supprimer un segment brut pour débloquer M3.

Le healthcheck échoue notamment pour :

- statut absent ou stale ;
- collector arrêté ou WebSocket déconnecté ;
- dernier message public trop ancien ;
- divergence du hash de configuration ;
- garde live/shadow incorrecte ;
- réserve disque sous le seuil.

Le healthcheck Docker du collector reste volontairement centré sur le flux. La
santé globale de l'interface combine ce résultat avec les incidents de
maintenance `maintenance_failed`, `maintenance_stale`,
`maintenance_wrong_report_date` et `maintenance_overdue`.

Le watchdog attend 120 secondes au démarrage, alerte sur transition vers
`unhealthy`, répète après un cooldown de 15 minutes et notifie le retour à
`healthy`. Son payload ne contient que l'état de santé, les raisons, les âges,
la réserve disque et le hash de configuration.

Le déploiement inscrit le SHA Git complet dans `.hyperbot-code-commit`, le passe
à l'image et l'environnement, puis vérifie sa concordance avant tout start. Les
événements portent `0.1.0+g<sha-complet>` dans `context.code_version`; le statut
expose aussi `code_commit` séparément.

Les journaux Docker sont bornés à cinq fichiers de 10 Mio par service. Les
données A ne sont jamais détruites automatiquement. Une copie chaude n'est
retirée qu'après publication et double vérification de sa copie froide ; sans
archive active, une alerte disque impose une intervention ou une extension.

## 7. Fetch vérifié

Depuis le poste de développement :

```bash
./scripts/fetch_hyperbot_data.sh --days 3
./scripts/fetch_hyperbot_data.sh --date 2026-08-10
```

Le serveur construit d'abord un manifest des seuls segments clôturés et rapports
publics sélectionnés. Les `.open`, symlinks, `.env`, clés et données TRIDENT sont
exclus. Le fetch utilise cette liste dans les tiers chaud et froid, puis
recalcule taille et SHA-256 de chaque fichier sous
`data/server-fetches/<fetch-id>/`.
Le bundle contient aussi une copie immuable et checksumée des manifests du
store au moment de l'export. Après validation des fichiers, le fetch matérialise
ces snapshots dans le payload local. `SegmentedEventStore` peut ainsi revérifier
la chaîne de records, le hash de contenu et le hash de stockage d'une journée
sans dépendre du manifest serveur qui continue d'évoluer.

Seul un rapport M3 au schéma courant, qualifié, checksumé, tier A et portant un
SHA Git complet peut ouvrir le builder replay. Le builder exige en plus un seul
`run_id` collector et la présence de L2 et trades pour le marché. Une journée
reste donc consultable par M3 sans devenir automatiquement une preuve de file
M4.

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

La projection de stockage doit être recalculée après chaque changement de
matrice sur un échantillon réel. Le 11 août 2026, les 52 subscriptions ont
produit environ 12 Gio/jour bruts et 1,83 Gio/jour après gzip. Avec 45 Gio libres
et une réserve fail-closed de 10 Gio, la gate de 30 jours n'est pas soutenable
sur le disque courant. La suppression du TRIDENT classique ne suffirait pas :
elle ne restituerait qu'environ 11 Go et ne doit pas être exécutée comme solution
de capacité sans sauvegarde et décision explicite.

Un Volume Hetzner de 100 Go a ensuite été monté sur
`/opt/hyperbot/shared/data`, avec 93 Gio libres après migration. Le sentinel
`/app/data/.hyperbot-volume-106591803` bloque tout démarrage si le montage est
absent. Cette capacité couvre la gate de 30 jours sur la projection courante,
mais un stockage froid reste nécessaire pour garantir 60 jours avec marge.

La compression des segments fermés valide et prépare désormais chaque fichier
hors du verrou du stream ; le verrou exclusif ne couvre que la revalidation
finale et la publication atomique du gzip et du manifest. La maintenance ne
recalcule pas non plus un jour déjà finalisé après un changement de
configuration. Ces deux propriétés empêchent la maintenance quotidienne de
saturer la file du collector.

## 10. Première activation réalisée

Le 11 août 2026, le release `20260811T141144Z-4e811c0bce9b` a été installé puis
activé avec la seule whitelist `BTC`. Les contrôles observés sont :

- collector et observer healthy ; maintenance et watchdog running ;
- flux public connecté, 591 messages reçus et 774 événements persistés au
  snapshot du smoke test ;
- zéro drop, zéro message malformé et aucune raison de health failure ;
- `/health` public retourne 200, `/` sans auth retourne 401, API authentifiée
  retourne 200 et POST retourne 405 ;
- UFW publie `3002/tcp` et l'accès authentifié via l'adresse publique réussit ;
- `live_enabled=false`, `shadow_only=true`, canary non autorisé ;
- aucun conteneur TRIDENT/HIP-4 n'a été modifié.

Le premier rapport qualité de la veille est logiquement non qualifié, puisque le
collector n'était pas encore actif durant cette fenêtre UTC. La collecte M3
commence avec la journée d'activation ; aucune continuité antérieure n'est
inventée.

## 11. Migration vers le Volume réalisée

Le 11 août 2026, les données ont été copiées vers le Volume `106591803`, puis
comparées par `rsync --checksum` et validées avec le store segmenté avant la
bascule. L'ancienne copie reste disponible sous
`/opt/hyperbot/shared/data.root-backup-volume-migration` jusqu'à une revue
ultérieure explicite. Le release final actif est
`20260811T155300Z-8e73ae32bdef`.
