# Rapport d'implémentation — M7-Ops UI/API

## Résultat

Le lot ajoute un service d'observation autonome, sans dépendance frontend ni
client de trading. Il expose un dashboard responsive sur le port public 3002 et
une API JSON strictement en lecture seule. Il ne déploie pas le runner shadow,
ne signe rien et ne contient aucune primitive d'envoi d'ordre.

## Contrat livré

- `/health` public et minimal pour le healthcheck Docker ;
- dashboard et `/api/*` protégés par Basic Auth hors loopback ;
- endpoints de statut, marchés, qualité M3, incidents, stockage, shadow et
  configuration non sensible ;
- lecture limitée aux statuts runtime et aux rapports/manifests JSON vérifiés ;
- historique qualité borné à 90 jours et fichiers JSON bornés à 32 Mio ;
- filtrage défensif des champs portant des noms de secret ;
- GET/HEAD uniquement, toutes les méthodes mutantes refusées avec HTTP 405 ;
- aucun bouton start, stop ou restart dans l'interface.

## Déploiement et isolation

Le service `observer` rejoint le profil Compose `collector`. Il s'exécute
non-root, avec root filesystem read-only, sans capability, sans socket Docker et
avec 0,5 CPU/256 Mio par défaut. Les volumes `data`, `runtime` et `reports` sont
montés `:ro`. Le seul secret monté est le mot de passe UI généré aléatoirement
dans `/opt/hyperbot/shared/ui_password` et absent de `.env`.

Le mapping par défaut est `0.0.0.0:3002:3002`. Basic Auth ne remplace pas le
chiffrement : le runbook impose un reverse proxy TLS, un VPN ou une allowlist IP
pour une exposition Internet appropriée.

## Validation

Les 99 tests du dépôt couvrent notamment la configuration fail-closed, les checksums, la borne
d'historique, la non-divulgation de secrets, l'authentification HTTP, les
headers de sécurité, le dashboard embarqué, les erreurs 400/404 et le refus des
méthodes POST/PUT/PATCH/DELETE/OPTIONS. La régression complète M0 à M7 est
exécutée avant commit.

## État opérationnel

Le release `20260811T141144Z-4e811c0bce9b` est actif depuis le 11 août 2026 sur
le serveur commun à TRIDENT, dans l'arborescence séparée `/opt/hyperbot`.
L'observer est healthy et publié sur `0.0.0.0:3002`; UFW autorise ce seul port
HyperBot. Le smoke externe confirme `/health=200`, dashboard sans auth `401`,
API authentifiée `200` et mutation `POST=405`.

Le collector public BTC est healthy avec, au snapshot de validation, 591
messages reçus, 774 événements persistés, zéro drop et zéro message malformé.
`live_enabled` reste faux, le runner shadow n'est pas déployé et le canary reste
non autorisé. Les conteneurs TRIDENT/HIP-4 présents avant l'activation sont
restés running.
