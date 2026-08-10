# HyperBot — reconstruction depuis zéro pour un capital de 1 000 $

**Date de décision :** 10 août 2026

**Statut :** proposition de recherche et préparation d'implémentation, aucun
trading réel autorisé

**Objectif demandé :** utiliser Hyperliquid et viser un rendement supérieur à
30 % par mois à partir de 1 000 $

## 1. Conclusion exécutive

Il n'existe aujourd'hui aucune stratégie TRIDENT, ni aucune proposition
nouvelle décrite ci-dessous, pour laquelle on puisse prédire honnêtement un
rendement supérieur à 30 % par mois avec une fiabilité suffisante.

Le bon cadrage est donc le suivant :

1. **+30 % par mois est une cible de recherche, pas une promesse.** Ce rythme
   transforme théoriquement 1 000 $ en 23 298 $ en douze mois, avant impôts et
   retraits. Il exige soit un edge exceptionnel répété très souvent, soit un
   levier qui rend la ruine probable.
2. **Les stratégies directionnelles historiques de TRIDENT ne doivent pas être
   reprises.** Le réel invalide leurs backtests et leurs promotions successives.
3. **La seule famille qui justifie une nouvelle recherche prioritaire est la
   fourniture de liquidité à rotation rapide**, d'abord sur les outcomes HIP-4,
   ensuite sur certains marchés HIP-3 en growth mode. Le rendement potentiel
   vient de la capture répétée d'un spread, pas d'une prédiction directionnelle
   à fort levier.
4. **Le produit proposé est une suite neuve, `HyperBot`, isolée des pods
   A/C et du bot HIP-4 actuel.** Elle réutilise uniquement les briques éprouvées
   de sécurité, de réconciliation et d'observabilité.
5. **Prévision de planification avant collecte des nouvelles données :** zone
   centrale de **+4 % à +12 % par mois sur le portefeuille**, scénario fort de
   **+15 % à +22 %**, mois défavorable plausible de **-8 % à -15 %**. Le seuil
   de +30 % n'est atteint que dans un scénario stretch précisément mesurable :
   environ 2 rotations quotidiennes de la poche outcomes à 70 points de base
   nets par rotation, plus la contribution HIP-3.

La décision recommandée n'est donc pas de « lancer un bot à +30 % ». Elle est
de construire une machine capable de dire, avec des fills réels et un replay
sans biais, si les deux edges de market making existent. Si les métriques
n'atteignent pas le seuil arithmétique, le système doit déclarer l'objectif
inatteignable au lieu d'ajouter du levier.

## 2. La contrainte mathématique

Un gain de 30 % par mois correspond à :

- 0,878 % composé par jour sur 30 jours ;
- 6,313 % composé par semaine ;
- 2 229,8 % composé par an, soit un multiplicateur de 23,3.

Pour une stratégie de market making :

```text
PnL mensuel
= capital alloué
× turnover rempli quotidien
× 30
× capture nette par dollar rempli
```

Le **turnover rempli** ne désigne pas le volume de quotes posées : il s'agit du
notionnel effectivement exécuté. La **capture nette** est calculée après frais,
sélection adverse, hedges, funding, slippage, sorties forcées et settlement.

Cette décomposition rend la cible falsifiable. Avec 700 $ sur HIP-4 :

```text
700 × 2,0 rotations/jour × 30 × 0,70 % = 294 $
```

Le complément doit venir du moteur HIP-3. Si l'un des deux facteurs — 2,0
rotations ou 70 bps nets — n'est pas observé hors échantillon, le portefeuille
ne peut pas raisonnablement viser +30 % sans prendre un autre type de risque.

## 3. Ce que les tentatives TRIDENT ont réellement appris

### 3.1 Résultats live/paper actuels

La dernière review A/C fiable du 10 août 2026 montre un système techniquement
sain mais économiquement perdant :

| Périmètre | Clôtures/fills comptabilisés | PnL réalisé | Profit factor | Lecture |
|---|---:|---:|---:|---|
| Pod A | 323 | -188,85 $ | environ 0,49 sur le setup dominant | edge directionnel absent |
| Pod C | 187 | -15,29 $ | environ 1,02 sur l'oil short | edge brut presque entièrement évaporé |
| A + C | 510 | -204,14 $ | — | environ -20,4 % du capital cible |

Une nouvelle collecte a été tentée le 10 août, mais la connexion SSH serveur a
été interrompue pendant le rapatriement de plusieurs captures API et états
runtime. Les journaux partiels ne remplacent donc pas la dernière review A/C
complète. Le verdict opérationnel courant est **WARN / fraîcheur non confirmée**,
et non « tout est OK ».

Le `trend_pullback_long` du Pod A concentre 321 clôtures et -183,83 $. Les deux
premiers trades live du chart pattern `double_bottom_long`, pourtant promu sur
un replay local positif de seulement deux trades, totalisent -5,02 $. Le Pod C
oil short, qui était la meilleure piste de recherche, ne conserve plus que
+1,34 $ sur 136 trades ; CL est négatif et Brent compense partiellement.

Sources locales :

- [plan actif TRIDENT](/workspaces/trident/docs/trident_active_plan.md) ;
- [review A/C du 10 août](/workspaces/trident/server-data/reviews/20260810T120656Z/review_summary.md) ;
- [audit oil P109](/workspaces/trident/server-data/reviews/20260810T120656Z/p109_oil_shadow_audit.md) ;
- [audit général historique](/workspaces/trident/docs/resultat_audit.md) ;
- [plan des évolutions robustes](/workspaces/trident/docs/plan_evos_robustes.md).

### 3.2 HIP-4 est prometteur, mais pas validé

La dernière collection HIP-4 locale exploitable s'arrête au 6 juillet 2026 ;
elle n'est donc pas assez fraîche pour une décision live au 10 août. Elle
contient néanmoins le seul résultat local encore intéressant :

| Échantillon | Settlements | PnL net | Profit factor | Brier |
|---|---:|---:|---:|---:|
| historique complet | 99 | +43,66 $ | 1,087 | 0,225 |
| après le 10 juin | 77 | +78,12 $ | 1,224 | 0,222 |

Le résultat est hétérogène : SOL est fortement positif, HYPE légèrement
positif, BTC et ETH négatifs sur l'historique complet. Une politique de sortie
contre-factuelle affiche +279,25 $, mais ce chiffre est essentiellement lié à
l'ancienne politique ; après le 10 juin, elle ne change plus le résultat actif.
Il ne doit donc pas être présenté comme un gain accessible.

La review automatique recommande elle-même de collecter davantage de données,
car le profit factor historique reste inférieur à 1,15. L'audit shadow Nautilus
ne fournit pas un filtre exploitable : les opportunités dites de faible qualité
étaient paradoxalement les plus rentables.

Sources locales :

- [review HIP-4](/workspaces/trident/server-data/hip4/reviews/20260810T125746Z/hip4_outcome_run_review.md) ;
- [audit des politiques HIP-4](/workspaces/trident/server-data/hip4/replay_reports/hip4_policy_market_audit_20260810T125714Z.md).

### 3.3 Leçons à conserver

- Un bon backtest standalone n'est pas une baseline full-bot.
- Deux ou dix trades positifs ne constituent pas une validation.
- Les règles heure/jour/symbole trouvées par balayage massif n'ont pas survécu
  à l'historique long : c'est un cas classique de tests multiples.
- Les filtres LLM n'ont pas créé d'edge hors échantillon ; ils ne doivent pas
  prendre de décisions de trading.
- L'exécution, les caps, la réconciliation et les kill switches sont utiles,
  mais ils ne transforment pas une expectancy négative en stratégie rentable.
- Un nouvel outil doit apprendre des quotes, ordres et positions dans la file,
  et non seulement de chandeliers OHLCV.

## 4. Ce que permet Hyperliquid aujourd'hui

### 4.1 Coûts et mécanique utiles

Au tier 0, les perps validateurs affichent un frais taker de 4,5 bps et maker
de 1,5 bps. Sur un marché HIP-3 ordinaire, les frais utilisateur sont
normalement doublés ; le growth mode réduit ensuite de 90 % les frais de
protocole, rebates et contribution au volume. Hors éventuel frais du deployer,
un ordre maker tier 0 en growth mode revient donc approximativement à 0,3 bps
par fill et un taker à 0,9 bps. La valeur exacte doit être récupérée et vérifiée
au runtime, marché par marché. [Documentation officielle des
frais](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)

Les ordres ALO sont post-only ; IOC, GTC, stops et TWAP existent également.
HyperBot utilisera ALO pour les quotes normales et réservera IOC à une
sortie de risque explicite. [Types d'ordres
officiels](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/order-types)

Hyperliquid ne donne ni programme DMM, ni rebate spécial, ni avantage de
latence réservé aux market makers : la concurrence est ouverte, mais l'edge
doit survivre à la sélection adverse. [Guide officiel de market
making](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/market-making)

### 4.2 Deux niches observables

**Outcomes HIP-4.** Le 10 août 2026, l'API publique exposait notamment des
marchés binaires quotidiens BTC, ETH, SOL et HYPE. Le carnet BTC était serré,
mais ETH, SOL et HYPE présentaient ponctuellement des spreads de plusieurs
centimes. Ce n'est pas une preuve de profit : un spread large peut seulement
signaler une forte toxicité ou peu de fills. C'est toutefois une surface de
recherche compatible avec un petit capital. Les outcomes utilisent un encodage
d'asset et des primitives proches du spot. [Encodage officiel des assets et
outcomes](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids)

**Perps HIP-3 growth mode.** Les grands marchés XYZ observés avaient beaucoup
de volume mais des spreads souvent inférieurs à 1 bp : trop serrés pour un petit
maker sans avantage de file. Certains marchés plus petits de `para` affichaient
des spreads d'environ 3 à 25 bps, avec une profondeur faible et donc un risque
de toxicité élevé. Le moteur devra rechercher l'intersection, rare, entre
spread suffisant, turnover, oracle robuste et risque d'inventaire acceptable.
[Spécification officielle HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals)

### 4.3 Données et infrastructure

L'API WebSocket fournit notamment les carnets L2, trades, BBO, updates d'ordres
et fills. Les limites actuelles autorisent 10 connexions WebSocket, 1 000
subscriptions et 2 000 messages envoyés par minute ; l'architecture doit donc
maintenir quelques connexions durables et éviter le polling REST. [WebSocket et
subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions),
[rate limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)

Les archives officielles S3 sont utiles mais peuvent être incomplètes. Il faut
donc enregistrer localement les diffs de carnet, quotes et fills nécessaires au
replay. [Données historiques
officielles](https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data)

Faire tourner immédiatement un node complet serait disproportionné pour 1 000
$ : la documentation du node annonce, hors validation, environ 16 vCPU, 128 Go
de RAM, 500 Go de stockage et jusqu'à 100 Go de logs par jour. Un node pourra
devenir utile plus tard pour la reconstruction fine des carnets et des TWAP,
mais ne doit pas être une dépendance de la phase initiale. [Dépôt officiel du
node](https://github.com/hyperliquid-dex/node)

### 4.4 Réutilisation contrôlée des données TRIDENT

Les archives accumulées par TRIDENT depuis avril 2026 permettent de démarrer la
recherche HyperBot sans attendre trente jours, mais elles ne sont pas toutes des
données de replay d'exécution. Elles sont classées en trois niveaux :

| Niveau | Données | Utilisation autorisée | Promotion canary |
|---|---|---|---|
| A | nouveau collector HyperBot | replay de file central/pessimiste, validation officielle | oui, après toutes les gates |
| B | archives outcomes HIP-4 | fair value, spreads, markouts, settlements, replay optimiste | non, pas seules |
| C | snapshots/replays A/C et GBOT | fixtures, features, scanner HIP-3, benchmarks historiques | non |

Inventaire de référence au 10 août 2026 :

- [book snapshots HIP-4](/workspaces/trident/server-data/hip4/logs/hip4_nautilus_shadow/book_snapshots.jsonl)
  contient environ 892 000 snapshots outcomes entre le 27 mai et le 6 juillet,
  mais avec des interruptions et des carnets parfois stale ;
- [logs HIP-4 mainnet paper](/workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/)
  contient environ 118 000 observations, 145 000 quotes maker shadow, 102 trades
  paper et 99 settlements ;
- [archive GBOT](/workspaces/trident/data/gbot_archive/) contient environ 68 Mo
  de BBO/profondeur agrégée et de trades sur douze cryptos, principalement
  pendant quelques heures du 1er avril ;
- [replay inputs TRIDENT](/workspaces/trident/server-data/replay_inputs/)
  contient environ 11 Go de snapshots et features agrégées utilisables pour le
  contexte et les anciens replays directionnels ;
- [live snapshots TRIDENT](/workspaces/trident/data/live_snapshots/) ne contient
  que 111 snapshots dispersés et ne constitue pas une série continue.

Ces données permettent immédiatement de tester :

- fair value et calibration des probabilités outcomes ;
- distributions de spread et incohérences YES/NO ;
- markouts après une quote théorique ;
- choix des marchés et régimes de données stale ;
- reproduction des décisions, trades et settlements de l'ancien bot ;
- mécanique déterministe du replay et compatibilité des schémas.

Elles ne suffisent généralement pas à reconstruire :

- le volume exact devant une quote hypothétique ;
- toutes les insertions, annulations et modifications de la file ;
- les fills partiels qu'aurait reçus HyperBot ;
- une continuité de trente jours avec séquences vérifiables ;
- les frais effectifs et changements de spécification à chaque instant.

La taille d'un fichier ou son nombre de lignes ne prouve donc ni sa continuité,
ni sa qualité d'exécution. Les archives B/C peuvent produire une borne
optimiste et accélérer le développement, mais seuls les événements A du nouveau
collector peuvent valider les modèles de fill central et pessimiste.

L'import se fait par adaptateurs explicites et jamais par dépendance runtime :

```text
archives TRIDENT en lecture seule
             │
             ▼
     LegacyDataImporter
             │
             ├── manifest source + checksum + période + qualité
             ▼
 événements normalisés HyperBot, versionnés et reproductibles
```

Les 11 Go de données ne sont pas copiés dans Git. Seuls les manifestes, petits
fixtures et rapports de qualité sont versionnés. Toute transformation conserve
le chemin source, le SHA-256, la période, le nombre de lignes, la fréquence
estimée, les trous détectés et la version de l'adaptateur.

## 5. Architecture proposée : la suite HyperBot

```text
WebSocket + métadonnées + oracle
              │
              ▼
       HyperBot Lab / Event Store
       replay avec file d'attente
              │
       ┌──────┴────────┐
       ▼               ▼
HyperBot Outcomes      HyperBot Growth
   HIP-4              HIP-3
       └──────┬────────┘
              ▼
      HyperBot Risk SUPERVISOR
              │
      shadow → canary → live
```

Les quatre composants sont nécessaires. Les deux stratégies ne peuvent jamais
envoyer directement un ordre à l'exchange : elles produisent une intention de
quote, que le superviseur accepte, réduit ou rejette.

## 6. Outil 1 — HyperBot Lab, laboratoire et jumeau d'exécution

### 6.1 Rôle

HyperBot Lab est le préalable à toute prétention de rendement. Il collecte le flux
réel et rejoue une stratégie avec :

- état L2 du carnet et mises à jour BBO ;
- trades agressifs ;
- oracle, mark, funding et métadonnées de marché ;
- toutes les quotes émises, remplacées ou annulées ;
- accusés de réception, fills partiels, rejets et latence ;
- position estimée dans la file ;
- settlement et résolution des outcomes ;
- configuration et version de code immuables par run.

### 6.2 Pourquoi les replays précédents ne suffisent pas

Un replay sur chandeliers peut décider si le prix futur dépasse un stop, mais
pas si une quote maker aurait été remplie avant une annulation. Un backtest de
market making qui suppose « touché = rempli » surestime systématiquement le
profit et sous-estime la sélection adverse.

Le nouveau replay doit au minimum proposer trois modèles :

1. **pessimiste :** fill seulement quand le volume agressif traverse la quote
   et consomme le volume estimé devant elle ;
2. **central :** file reconstruite avec latence mesurée et fills partiels ;
3. **optimiste borné :** touch fill, affiché uniquement comme plafond et jamais
   utilisé pour une promotion.

Les archives TRIDENT B/C pourront alimenter le modèle optimiste, les markouts et
les tests de logique. Elles ne seront jamais artificiellement enrichies pour
faire croire qu'une position de file inconnue est connue. Les modèles central
et pessimiste destinés à une promotion utiliseront exclusivement des périodes
de niveau A, collectées avec le schéma HyperBot.

Le PnL doit être marqué 100 ms, 1 s, 5 s et 30 s après chaque fill. La dérive
post-fill mesure directement la sélection adverse.

### 6.3 Validation statistique

- séparation chronologique stricte train/calibration/test ;
- folds walk-forward purgés autour du settlement ;
- aucun réglage à partir du test final ;
- bootstrap par journée et par marché, pas par fill indépendant ;
- résultats publiés avec intervalle de confiance, concentration par symbole,
  régime de volatilité et heure avant expiration ;
- comparaison obligatoire au « ne rien faire » et au bot HIP-4 actif ;
- journal de toutes les variantes essayées afin de matérialiser les tests
  multiples.

### 6.4 Critères de sortie de HyperBot Lab

Avant un canary monétaire :

- au moins 30 jours continus de carnet et 99 % de complétude mesurée ;
- au moins 300 settlements outcomes exploitables et 500 round trips simulés
  pour chaque moteur candidat ;
- résultat positif dans chacun de trois folds OOS ;
- profit factor OOS supérieur à 1,20 après tous les coûts ;
- drawdown OOS inférieur à 10 % de la poche concernée ;
- aucun sous-jacent ne contribue plus de 40 % du profit ;
- scénario pessimiste non catastrophique et scénario central positif ;
- calibration de la probabilité de fill contre de petits ordres réels avant
  toute montée en taille.

Les seuils en nombre de trades ne remplacent pas la diversité temporelle. S'ils
ne peuvent pas être atteints en 30 jours, la collecte continue.

## 7. Outil 2 — HyperBot Outcomes, moteur principal HIP-4

### 7.1 Thèse

Le bot HIP-4 actuel achète une opinion directionnelle puis attend une sortie ou
un settlement. HyperBot Outcomes cherchera plutôt à vendre de la liquidité des deux
côtés quand le spread dépasse l'incertitude de fair value, les frais et la
sélection adverse attendue.

Les outcomes binaires ont quatre propriétés intéressantes pour 1 000 $ :

- prix borné entre 0 et 1 ;
- perte maximale calculable avant l'ordre ;
- settlement fréquent, donc rotation possible ;
- relation complémentaire entre YES et NO qui fournit un contrôle de parité.

La difficulté centrale est l'information adverse à proximité de l'expiration.
Le moteur doit préférer rater un fill plutôt que coter une probabilité devenue
obsolète.

### 7.2 Fair value

Pour un outcome de type `prix final > strike`, le modèle initial produit :

```text
p_fair = P(S_expiration > strike | état courant)
```

Features déterministes, sans LLM :

- distance log entre oracle et strike ;
- temps exact restant ;
- volatilité réalisée multi-horizon et asymétrie récente ;
- mark/oracle basis, funding et variation de l'open interest ;
- imbalance L2, trade flow et vitesse de mise à jour ;
- prix complémentaires YES/NO et incohérence de parité ;
- régime propre au sous-jacent et à l'heure avant expiration.

Le premier benchmark est un modèle digital simple sous volatilité empirique.
La probabilité est ensuite recalibrée par isotonic regression ou logistic
calibration sur les folds passés. Un modèle complexe n'est retenu que s'il
améliore Brier, log loss **et** PnL maker OOS.

### 7.3 Construction des quotes

```text
demi_spread_min
= frais aller-retour attendus
+ adverse_selection_q90
+ coût d'inventaire
+ marge d'incertitude du modèle
+ marge opérationnelle

bid = arrondi_au_tick(p_fair - demi_spread - skew_inventaire)
ask = arrondi_au_tick(p_fair + demi_spread - skew_inventaire)
```

Règles impératives :

- ALO uniquement pour l'entrée normale ;
- pas de quote si données âgées de plus de 500 ms, oracle incohérent ou carnet
  croisé ;
- annulation immédiate lors d'un mouvement d'oracle supérieur au seuil de
  volatilité, d'une hausse du flow toxique ou d'une perte WebSocket ;
- quotes YES et NO contrôlées ensemble ;
- rejet de tout cycle dont le pire payoff au settlement dépasse le cap ;
- aucun martingale, aucun doublement après perte ;
- spread élargi et taille réduite à l'approche de l'expiration ;
- si un hedge complémentaire n'est pas disponible en maker, ne pas supposer un
  hedge taker gratuit.

### 7.4 Gestion d'inventaire

L'état de risque pertinent est le payoff dans chaque branche du résultat :

```text
worst_case_pnl = min(pnl_si_YES, pnl_si_NO)
```

Caps initiaux de recherche :

- ordre minimum pratique : 10 $ ;
- inventaire brut maximal : 50 $ par outcome ;
- perte maximale au settlement : 25 $ par marché ;
- 4 marchés simultanés au maximum ;
- pire perte agrégée de settlement : 50 $, dont 30 $ au maximum sur des marchés
  partageant la même expiration ou un même facteur crypto ;
- poche totale : 700 $ uniquement après promotion ;
- aucun nouvel inventaire dans les 60 dernières secondes tant que ce régime
  n'est pas validé séparément.

### 7.5 Prédiction de rendement

Ancrage observé : le bot taker/directionnel HIP-4 a produit +43,66 $ sur un
budget configuré de 500 $ sur son historique complet, soit un profit réel mais
un PF de seulement 1,087. La fenêtre postérieure au 10 juin est meilleure, mais
trop courte et non fraîche. Le maker vise à remplacer une partie du coût de
crossing par de la capture de spread ; il s'expose en échange à moins de fills
et davantage de sélection adverse.

Prévision de planification sur la poche de 700 $, **non encore validée** :

| Scénario | Turnover rempli/jour | Capture nette | PnL mensuel | Portefeuille total |
|---|---:|---:|---:|---:|
| stress | 1,0× | -50 bps | -105 $ | -10,5 % |
| central | 1,0× | +40 bps | +84 $ | +8,4 % |
| fort | 1,5× | +60 bps | +189 $ | +18,9 % |
| seuil cible | 2,0× | +70 bps | +294 $ | +29,4 % |

Le scénario cible n'est pas la prévision centrale. Il devient crédible seulement
si 2,0× et 70 bps sont observés simultanément sur plusieurs folds, puis dans le
canary réel. Un spread affiché de 5 cents n'équivaut pas à 500 bps capturés : le
fill arrive précisément quand le prix risque de devenir faux.

## 8. Outil 3 — HyperBot Growth, moteur secondaire HIP-3

### 8.1 Thèse

Le growth mode réduit fortement le coût protocolaire sur certains perps HIP-3.
Il peut rendre rentable une micro-fourniture de liquidité sur des marchés trop
petits pour les grands market makers, mais assez actifs pour tourner une poche
de 250 $. Le moteur est secondaire car aucun historique local n'en valide encore
l'edge maker.

### 8.2 Sélection dynamique des marchés

Le scanner recalcule toutes les minutes :

- statut growth mode et frais effectifs utilisateur/deployer ;
- spread médian, p10 et p90 sur 1 h et 24 h ;
- profondeur à 5/10/25 bps ;
- volume agressif et nombre de prints ;
- volatilité et gap oracle/mark ;
- funding, open interest et concentration du carnet ;
- fréquence des stale books, halts et changements de spécification.

Filtres initiaux de recherche, à recalibrer sans regarder le test final :

- growth mode obligatoire ;
- spread médian d'au moins 8 bps ;
- au moins 250 000 $ de volume quotidien ;
- profondeur disponible supérieure à 50 fois la taille d'ordre dans ±25 bps ;
- oracle frais et aucun incident de deployer ;
- exclusion si le spread large provient principalement d'un carnet stale.

Ces seuils écartent probablement les grands marchés XYZ très serrés et plusieurs
petits marchés `para` trop illiquides. C'est voulu : le scanner peut conclure
qu'aucun marché n'est tradable.

### 8.3 Quotes et contrôle du risque

Fair value initiale : médiane robuste de l'oracle, du mark et du microprice,
avec borne stricte autour de l'oracle. Les quotes ALO sont asymétriques selon
l'inventaire et le flow. La position est ramenée à zéro par quotes passives ;
une sortie taker est autorisée seulement par le superviseur lorsque le risque
dépasse le coût.

Caps initiaux après promotion :

- poche : 250 $ ;
- ordre : 10 à 20 $ ;
- inventaire par symbole : 50 $ ;
- inventaire brut total : 150 $ ;
- delta net total : 75 $ ;
- un seul DEX HIP-3 au démarrage ;
- aucune dépendance au cross-margin entre DEX indépendants.

HIP-3 comporte des risques spécifiques de deployer, d'oracle et de marge
isolée. Un marché peut être retiré automatiquement sans qu'une stratégie soit
globalement arrêtée.

### 8.4 Overlay TWAP, uniquement après validation

Une recherche récente sur Hyperliquid observe que les TWAP visibles attirent de
la profondeur et modifient l'inclinaison du carnet. Cela justifie une feature
`active_twap_state` pour retirer ou décaler une quote, mais pas une stratégie
autonome : l'article mesure une réponse de liquidité, pas un profit garanti.
[Étude TWAP sur Hyperliquid](https://arxiv.org/abs/2606.15715)

### 8.5 Prédiction de rendement

Prévision de planification sur 250 $, sans edge local encore observé :

| Scénario | Turnover rempli/jour | Capture nette | PnL mensuel | Portefeuille total |
|---|---:|---:|---:|---:|
| stress | 2,0× | -10 bps | -15 $ | -1,5 % |
| central | 4,0× | +2 bps | +6 $ | +0,6 % |
| fort | 5,0× | +5 bps | +18,75 $ | +1,88 % |
| contribution cible | 5,0× | +4 bps | +15 $ | +1,5 % |

HyperBot Growth ne peut pas, seul, atteindre l'objectif. Son intérêt est une petite
source de PnL décorrélée et une utilisation du capital lorsque les outcomes ne
sont pas cotables.

## 9. Outil 4 — HyperBot Risk, superviseur de portefeuille

### 9.1 Responsabilités

- source unique des positions, ordres et soldes réconciliés ;
- contrôle pré-trade du pire payoff et des frais ;
- budgets séparés HIP-4/HIP-3 ;
- annulation globale sur données stale, divergence locale/exchange ou perte de
  heartbeat ;
- calcul du PnL économique, distinct du seul PnL de l'exchange ;
- blocage de toute montée en taille non autorisée ;
- journal immuable de la raison de chaque quote et de chaque rejet.

### 9.2 Limites proposées

| Limite | Valeur initiale | Action |
|---|---:|---|
| perte journalière | 1,5 % de l'equity | arrêt jusqu'à review |
| drawdown mensuel | 8 % | réduction de moitié |
| drawdown absolu | 12 % | arrêt dur |
| perte opérationnelle inconnue | > 5 $ | cancel-all et réconciliation |
| pire perte outcomes corrélée | > 30 $ | réduction/rejet des quotes |
| pire perte outcomes agrégée | > 50 $ | réduction/rejet des quotes |
| données carnet stale | > 500 ms | cancel-all du marché |
| écart position locale/exchange | non nul après retry | arrêt du moteur |
| ordres orphelins | 1 | aucune nouvelle quote |

Un objectif de gain ne suspend jamais ces limites. Si le bot prend du retard
sur +30 %, il ne rattrape pas ce retard par levier ou tailles croissantes.

### 9.3 Allocation

Mode cible, uniquement après promotion indépendante des deux moteurs :

| Poche | Capital | Rôle |
|---|---:|---|
| HyperBot Outcomes | 700 $ | moteur principal |
| HyperBot Growth | 250 $ | diversification/rotation |
| réserve | 50 $ | frais, marge et erreurs d'arrondi |

Le scénario mathématique de +30 % est :

```text
HyperBot Outcomes : 700 × 2,0 × 30 × 0,70 % = 294 $
HyperBot Growth  : 250 × 5,0 × 30 × 0,04 % =  15 $
TOTAL                                             309 $ (+30,9 %)
```

Le scénario central combiné des tableaux est seulement +90 $, soit +9 %.
Le scénario fort est environ +208 $, soit +20,8 %. Cela constitue la prévision
réaliste de travail tant que les données nouvelles n'ont pas déplacé la
distribution.

## 10. Pourquoi les autres propositions ne sont pas prioritaires

### Directionnel, patterns, indicateurs et LLM

Rejetés comme moteur principal. Les nombreuses variantes TRIDENT ont produit
des résultats in-sample attractifs, puis une expectancy live négative ou une
concentration extrême. Un LLM peut résumer une review ou expliquer une anomalie ;
il ne doit pas choisir une entrée.

### Momentum cross-sectionnel

Une étude qui réintroduit liquidité, short availability et risque de
liquidation trouve que beaucoup de portefeuilles crypto momentum deviennent
irréalistes et que l'évidence cross-sectionnelle est faible, même si le
time-series momentum résiste mieux. Cela concorde avec les échecs locaux et ne
justifie pas un nouveau pod directionnel. [Étude sur le momentum crypto avec
contraintes réalistes](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)

### Funding carry

Le funding Hyperliquid est échangé chaque heure entre longs et shorts ; son
composant d'intérêt de base vaut 0,00125 % par heure et le premium peut changer
de signe. Un cash-and-carry spot/perp peut servir de parking opportuniste, mais
les frais, le basis et le risque de legs rendent +30 % mensuel non crédible.
[Mécanisme officiel du
funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding),
[recherche sur les perps et le basis](https://arxiv.org/abs/2212.06888)

### HLP, vaults et copie d'un leader

HLP mutualise market making, liquidations et autres activités de protocole,
avec un lock de quatre jours. Les vaults donnent 10 % des profits au leader.
Ils peuvent devenir des benchmarks, mais ne fournissent ni contrôle de l'edge,
ni garantie de rendement, ni liquidité immédiate suffisante pour un stop de
portefeuille. [Documentation officielle des
vaults](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults),
[HLP](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/protocol-vaults)

### Chasse aux liquidations

Les liquidations sont d'abord envoyées comme ordres de marché au carnet ; HLP
n'intervient en backstop qu'en dessous d'un seuil de marge. Avec 1 000 $, sans
infrastructure de latence validée, le bot serait surtout exposé au flow le plus
toxique. Les événements de liquidation doivent être enregistrés comme feature,
pas poursuivis comme stratégie v1. [Mécanisme officiel des
liquidations](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations)

## 11. Préparation concrète de l'implémentation

### 11.1 Arborescence neuve

```text
src/hyperbot/
├── config.py
├── models.py
├── market_catalog.py
├── event_store.py
├── legacy/
│   ├── manifest.py
│   ├── gbot.py
│   ├── trident_snapshots.py
│   └── hip4.py
├── fair_value/
│   ├── outcomes.py
│   └── hip3.py
├── strategies/
│   ├── outcome_maker.py
│   └── growth_maker.py
├── execution/
│   ├── quote_manager.py
│   └── reconciliation.py
├── risk/
│   └── supervisor.py
├── replay/
│   ├── queue_model.py
│   ├── walk_forward.py
│   └── bootstrap.py
└── reporting.py

src/hyperbot/services/collector.py
src/hyperbot/services/shadow_runner.py
scripts/run_hyperbot_replay.py
scripts/inventory_legacy_data.py
scripts/import_legacy_data.py
scripts/fetch_hyperbot_data.sh
config/hyperbot_research.toml
tests/hyperbot/
data/{raw,replay_reports,reviews}/
```

Cette arborescence évite de modifier les modules Pod A/C actuellement en cours
d'édition. La connexion exchange, les signatures et la réconciliation peuvent
être enveloppées depuis l'existant, mais aucune logique de signal historique
n'est importée.

### 11.2 Contrats de données minimaux

```python
BookEvent(
    exchange_ts_ms, receive_ts_ms, dex, asset, sequence,
    bids, asks, oracle_px, mark_px, config_hash,
)

QuoteIntent(
    strategy, market, side, price, size, ttl_ms,
    fair_value, min_edge_bps, inventory_before, reason_codes,
)

OrderLifecycle(
    client_order_id, exchange_order_id, intent_id,
    sent_ts_ms, ack_ts_ms, cancel_ts_ms, status, reject_reason,
)

FillAttribution(
    order_id, fill_ts_ms, price, size, fee, estimated_queue_ahead,
    markout_100ms, markout_1s, markout_5s, markout_30s,
)

OutcomeSettlement(
    market, expiry_ts_ms, strike, result, payout, settlement_fee,
)

LegacyProvenance(
    dataset_tier, source_path, source_sha256, source_record_number,
    adapter_name, adapter_version, quality_flags,
)
```

Chaque record contient `run_id`, version du code, hash de configuration et
source de temps. Les données brutes sont append-only ; les features dérivées
sont reproductibles et versionnées.

`LegacyProvenance` est obligatoire dans l'enveloppe de tout événement importé.
Il ne modifie pas artificiellement le payload de marché et permet au replay de
refuser automatiquement un dataset B/C lorsqu'un modèle de fill de niveau A est
requis.

### 11.3 Interfaces

```python
class Strategy:
    def on_market_state(self, state) -> list[QuoteIntent]: ...

class RiskSupervisor:
    def approve(self, intent, portfolio) -> ApprovedIntent | Rejection: ...

class ExecutionGateway:
    def reconcile(self) -> ExchangeState: ...
    def apply_quotes(self, approved: list[ApprovedIntent]) -> None: ...

class ReplayFillModel:
    def evaluate(self, order, events, latency) -> list[SimulatedFill]: ...
```

Le même `Strategy` et le même `RiskSupervisor` tournent en replay, shadow et
live. Seuls `ExecutionGateway` et l'horloge changent.

### 11.4 Configuration initiale

```toml
[mode]
live_enabled = false
shadow_only = true

[portfolio]
reference_equity_usd = 1000.0
daily_loss_stop_pct = 1.5
soft_drawdown_pct = 8.0
hard_drawdown_pct = 12.0
unknown_loss_stop_usd = 5.0

[outcome_maker]
enabled = false
research_allocation_usd = 700.0
order_usd = 10.0
max_inventory_per_market_usd = 50.0
max_settlement_loss_per_market_usd = 25.0
max_correlated_settlement_loss_usd = 30.0
max_total_settlement_loss_usd = 50.0
max_markets = 4
stale_book_ms = 500
entry_order_type = "ALO"

[growth_maker]
enabled = false
research_allocation_usd = 250.0
order_usd = 10.0
max_inventory_per_symbol_usd = 50.0
max_gross_inventory_usd = 150.0
max_net_delta_usd = 75.0
min_median_spread_bps = 8.0
min_daily_volume_usd = 250000.0
require_growth_mode = true
entry_order_type = "ALO"
```

Les deux moteurs sont désactivés par défaut. `live_enabled` ne peut pas être
activé par une simple variable distante : il doit exiger une configuration
signée/reviewée et une autorisation explicite.

### 11.5 Tests d'acceptation prioritaires

1. perte de WebSocket : toutes les quotes du marché sont annulées ;
2. ACK tardif après cancel : l'ordre orphelin est détecté et réconcilié ;
3. fill partiel simultané YES/NO : le pire payoff respecte le cap ;
4. changement de tick/deployer fee : aucune quote avec ancien paramètre ;
5. carnet stale : aucun nouvel ordre ;
6. restart au milieu d'une position : l'exchange gagne sur l'état local ;
7. replay répété : résultats bit-à-bit identiques ;
8. latence multipliée par deux : rapport de stress automatique ;
9. frais multipliés par deux : aucune stratégie promue si le PF passe sous 1 ;
10. hard drawdown : impossible de réactiver sans action opérateur explicite.

## 12. Roadmap et décisions de promotion

### Phase 0 — instrumentation locale

- implémenter modèles, event store et collector ;
- inventorier les archives TRIDENT par format, période, checksum et qualité ;
- importer des fixtures et les données HIP-4 utiles via des adaptateurs
  versionnés, sans dépendance runtime vers TRIDENT ;
- capturer outcomes et HIP-3 sans envoyer d'ordre ;
- produire quotidiennement complétude, trous de séquence et statistiques de
  spreads ;
- ne modifier aucun déploiement A/C ou HIP-4 existant.

**Sortie :** sept jours sans trou opérationnel majeur, puis poursuite jusqu'au
minimum statistique de 30 jours.

Les replays legacy peuvent démarrer avant la fin de cette collecte, mais leurs
résultats restent étiquetés `legacy_research_only` et ne comptent pas dans les
gates de promotion relatives aux fills.

### Phase 1 — replays falsifiables

- implémenter le fill model pessimiste et central ;
- benchmarker fair values et règles simples avant tout ML plus complexe ;
- publier les résultats par marché/régime et la liste exhaustive des variantes ;
- estimer la distribution de capture nette et de turnover.

**Sortie :** tous les critères HyperBot Lab de la section 6.4.

### Phase 2 — shadow temps réel

- produire les quotes sans les envoyer ;
- mesurer leur durée de vie, markout et probabilité de fill simulée ;
- comparer chaque jour shadow et replay ;
- tester kill switches et restart.

**Sortie :** au moins 14 jours, zéro violation de risque, métriques compatibles
avec le replay central et stress latence supportable.

### Phase 3 — canary monétaire

Cette phase nécessite une autorisation explicite distincte. Utiliser un
subaccount/API wallet sans droit de retrait, 100 $ au total, ordres de 10 $, un
seul moteur puis un seul marché. Le but n'est pas le rendement : c'est la
calibration de file, des fills et du markout.

**Sortie :** au moins 100 settlements outcomes ou 500 round trips HIP-3, aucune
anomalie de réconciliation, capture réelle dans l'intervalle du replay.

### Phase 4 — montée graduelle

- 100 → 250 → 500 → 1 000 $ ;
- jamais plus d'un doublement après une review complète ;
- chaque moteur doit mériter son allocation indépendamment ;
- retour automatique à l'étape précédente si PF roulant < 1,05, drawdown > 8 %
  ou dérive de capture hors intervalle.

Le seuil de +30 % ne devient un KPI opérationnel que lorsque les valeurs
empiriques de turnover et capture dépassent le scénario cible pendant plusieurs
fenêtres. Avant cela, le KPI principal est la conservation du capital et la
qualité de l'inférence.

## 13. Impact sur déploiement et fetching

La phase 0 reste locale et n'impacte ni le déploiement TRIDENT A/C, ni
TRIDENT-HIP4, ni leurs scripts de fetch.

Pour la phase shadow serveur :

- ajouter un service `hyperbot-collector` séparé et désactivé par défaut ;
- écrire dans `data/`, jamais dans les journaux A/C ou HIP-4 ;
- fournir `scripts/fetch_hyperbot_data.sh` avec manifest et checksums ;
- ne raccorder ce fetch à `scripts/fetch_all_data.sh` qu'après stabilisation ;
- n'inclure aucune clé ou valeur de `.env.trident` dans les rapports ;
- ne déployer aucun executor live avec le collector.

Ainsi, le nouveau projet peut être évalué sans perturber les bots actuels et
sans transformer une expérimentation en activation live implicite.

## 14. Décision recommandée

1. Geler toute nouvelle optimisation directionnelle de Pod A/C comme voie vers
   +30 % par mois.
2. Maintenir HIP-4 actuel en paper/observation ; ne pas extrapoler sa fenêtre du
   10 juin au 6 juillet.
3. Construire d'abord HyperBot Lab et collecter 30 jours de microstructure fraîche.
4. Développer HyperBot Outcomes en premier, HyperBot Growth seulement lorsque le
   collector commun est fiable.
5. Refuser tout live avant les gates OOS, shadow et canary.
6. Après collecte, remplacer les scénarios de cette note par une vraie
   distribution bootstrap. Si la capture/rotation ne permet pas +30 %, conclure
   explicitement que l'objectif n'est pas atteint, sans levier compensatoire.

La proposition maximise la chance de découvrir un edge compatible avec 1 000 $
tout en rendant les hypothèses réfutables. Elle ne garantit pas le résultat ;
elle empêche surtout de confondre à nouveau un backtest prometteur avec un outil
capable de gagner de l'argent en production.
