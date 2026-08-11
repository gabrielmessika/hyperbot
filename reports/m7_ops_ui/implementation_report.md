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

Le logiciel et les artefacts de déploiement sont prêts. Aucun déploiement, aucune
ouverture de firewall et aucune activation distante ne sont réalisés par ce lot.
