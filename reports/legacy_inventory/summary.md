# Inventaire des données legacy HyperBot

> Données de recherche uniquement. Les niveaux B/C ne valident ni la
> position de file, ni un fill maker central/pessimiste, ni une promotion.

Le rapport est déterministe : il n'enregistre pas l'heure d'exécution.
Les trous sont inférés à partir de la cadence médiane et ne prouvent pas,
à eux seuls, une panne de collecte.

## Résumé

- Fichiers : 58
- Taille : 684.5 Mio
- Lignes physiques : 1625170
- Records valides : 1625167
- Records malformés : 0
- Groupes de doublons exacts : 0
- Captures répétées candidates : 1
- Erreurs fatales : 0
- Avertissements : 0

## Couverture et limites

| Dataset | Niveau | Connu | Approximé | Absent |
|---|---:|---|---|---|
| gbot_microstructure | C | BBO, profondeur agrégée et trades du 1er avril | activité et markouts sur une fenêtre courte | carnet L2 complet, continuité longue et fills maker |
| hip4_nautilus_books | B | BBO, profondeur agrégée, marché et timestamps publiés | cadence et trous temporels inférés | diffs L2 complets, volume devant une quote et position de file |
| hip4_paper | B | observations, quotes shadow, trades paper et settlements | markouts et reproduction des décisions historiques | ACK/fills maker réels et position de file vérifiable |
| trident_live_snapshots | C | petits snapshots dispersés utilisables comme fixtures | compatibilité de schéma uniquement | continuité, complétude et preuve d'exécution |
| trident_replay_sample | C | features agrégées et snapshots directionnels historiques | régimes et compatibilité de schéma | microstructure de file et preuve d'exécution maker |

## Fichiers

| Source | Niv. | Chemin | Taille | SHA-256 | Lignes | Période UTC | Médiane | Trous inférés | Flags |
|---|---:|---|---:|---|---:|---|---:|---:|---|
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/ARB/2026-04-01.jsonl | 4.0 Mio | e9b32e1c4d4f718864d4bc8d02029e5a08eb313deff3eae764ef1bbfe2de9333 | 21511 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/AVAX/2026-04-01.jsonl | 4.0 Mio | 6fca347e58f9f162a693095c5a29ff8976a5b1f096d6d28f0783f7a0af69ed70 | 21511 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/BTC/2026-04-01.jsonl | 4.2 Mio | 3e757c9b743d395f13ad0e69625ec3fdab054b75ba3b8b2ae4de486326d9caa9 | 22653 | 2026-04-01T09:50:40.544000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 6 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/DOGE/2026-04-01.jsonl | 4.2 Mio | da190d456bbf51ab403a71a9a808ae81a66b7c7ccca5476b66f82b7146e253cb | 21511 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/ETH/2026-04-01.jsonl | 4.3 Mio | 5a71eb04368bc590467ae679027df93f8651f8698e683efe2c5ac343d6236cf2 | 22650 | 2026-04-01T09:50:41.054000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 6 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/HYPE/2026-04-01.jsonl | 4.1 Mio | 4d7acc43a42f0f9c534e35bcc937e6829abf942abca5fa45ea9bad44c400d7db | 21510 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/LINK/2026-04-01.jsonl | 4.1 Mio | 58a2fd1e1ec623a192f6d4d266f880a93cc2caac161da26b183524a71e93285c | 21511 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/NEAR/2026-04-01.jsonl | 4.0 Mio | 330674af1bae062de1f0607c4028ccb8fda9ef33ede0e96343bb8ea87a9a8bae | 21511 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/OP/2026-04-01.jsonl | 4.0 Mio | 417942dd5f3fc1841b4ab96adabf1cc7a37610961b864a3b6bc4ab38ba4a91a8 | 21511 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/SOL/2026-04-01.jsonl | 4.2 Mio | 9818b93f04527eaf40ddcfa114bad0926fa31b7fd111bc7503e6b2a81f909d0d | 22650 | 2026-04-01T09:50:41.054000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 6 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/SUI/2026-04-01.jsonl | 4.1 Mio | 3b079f0529785f4f9e076981faee3f158511f97b0b65b6c6fe9655830e0617b9 | 21510 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/l2/XRP/2026-04-01.jsonl | 4.0 Mio | 53e1c7206677a0b2bf2e5c9d5e9ca9d4673743e4851c19b06b0438a66e3f8a05 | 21510 | 2026-04-01T10:22:55.670000Z → 2026-04-01T16:41:00.957000Z | 538.0 ms | 4 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/ARB/2026-04-01.jsonl | 74.6 Kio | 63fc648740be7452dc19aed279ee81cd34e6ddc4456d89f2d038dbfed32f9487 | 900 | 2026-04-01T10:16:53.805000Z → 2026-04-01T16:40:46.425000Z | 10524.0 ms | 180 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/AVAX/2026-04-01.jsonl | 166.7 Kio | 82b5c05fea156d8ea80e8728ca1a3fccee2e213e8cdc27020837fe337a3e18e2 | 2037 | 2026-04-01T10:17:08.744000Z → 2026-04-01T16:40:49.808000Z | 5563.0 ms | 266 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/BTC/2026-04-01.jsonl | 8.3 Mio | 1223bc912db24a4f9c847ef19df92fd51b3b454747c863f92d0ee70667527d91 | 101016 | 2026-04-01T09:50:39.202000Z → 2026-04-01T16:41:01.295000Z | 257.0 ms | 4327 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/DOGE/2026-04-01.jsonl | 168.5 Kio | 3efb075502c3f6f03d4873915c2980a1fe16d5835aa593d379f7e02254e790b7 | 1983 | 2026-04-01T10:18:02.928000Z → 2026-04-01T16:40:51.546000Z | 4420.5 ms | 336 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/ETH/2026-04-01.jsonl | 2.6 Mio | 2cb109b3c2e25b9ed1b4294ee099354f13235238f9b274814fff1fe18cc3732f | 32860 | 2026-04-01T09:50:22.146000Z → 2026-04-01T16:41:01.223000Z | 614.0 ms | 1892 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/HYPE/2026-04-01.jsonl | 2.6 Mio | efe1efd34214068a34504efa073a697998c937cf6ca918b33ddae4b23a0719f6 | 32054 | 2026-04-01T10:22:24.171000Z → 2026-04-01T16:41:01.023000Z | 678.0 ms | 1726 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/LINK/2026-04-01.jsonl | 166.7 Kio | d97b95f3e666450eab37d3de302a8253f10fc2acbb0c34013ac2e58a2cb25e35 | 2061 | 2026-04-01T10:17:14.953000Z → 2026-04-01T16:40:50.357000Z | 5016.5 ms | 264 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/NEAR/2026-04-01.jsonl | 60.7 Kio | 47e72e5362dae2c61cc86ca5f1ba953924020ecfbe7555cf2465c67b633af0da | 741 | 2026-04-01T10:18:44.013000Z → 2026-04-01T16:40:27.741000Z | 11194.0 ms | 179 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/OP/2026-04-01.jsonl | 53.2 Kio | bb8dbc4f15ac4e090a16fe5d9da8aa82babf3c8505671aa894a2908c59d73120 | 653 | 2026-04-01T10:09:42.705000Z → 2026-04-01T16:40:56.788000Z | 20616.5 ms | 72 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/SOL/2026-04-01.jsonl | 1.8 Mio | a8a5b27f772a46414263ba15c6a5cb19f36224d82e642db70934903bdd672da5 | 22906 | 2026-04-01T09:50:24.269000Z → 2026-04-01T16:41:01.023000Z | 712.5 ms | 1669 | legacy_research_only, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/SUI/2026-04-01.jsonl | 160.8 Kio | 548ea887ab6c694a44815c65a75811befe796cd4db67054ea1abc3b87dee7d18 | 1954 | 2026-04-01T10:15:47.253000Z → 2026-04-01T16:40:52.396000Z | 4300.0 ms | 337 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| gbot_microstructure | C | /workspaces/trident/data/gbot_archive/trades/XRP/2026-04-01.jsonl | 321.1 Kio | 55ae4d8e1995ce65b4e59cbeb3fef088adc0e8101f809278e961d0553ffcca71 | 3953 | 2026-04-01T10:19:54.097000Z → 2026-04-01T16:40:57.000000Z | 2722.0 ms | 465 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-04-05.jsonl | 7.1 Kio | 8373d8bbe2b1c2be5cb7b1b80e6d60c6471921349bf329df1235e11cf0f80689 | 7 | 2026-04-05T07:40:00.000000Z → 2026-04-05T15:40:00.000000Z | 1890000.0 ms | 1 | legacy_research_only, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-04-23.jsonl | 11.5 Kio | 92946561c8a4116a27a8828f9f0161841621a622cb548b43fcef60aec7ce3292 | 7 | 2026-04-23T09:24:34.464976Z → 2026-04-23T13:56:21.398262Z | 2417542.171 ms | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-04-24.jsonl | 6.8 Kio | e96312b01aedf76e7419b9c63bcf818bdea048bea7aec92ca15689620db3f89f | 4 | 2026-04-24T14:00:26.298880Z → 2026-04-24T14:05:06.153145Z | 60758.488 ms | 1 | legacy_research_only, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-04-27.jsonl | 15.3 Kio | e5c0af2b8ec955f25823c2c497313cb9261e99029bd633d685716d55622af27c | 9 | 2026-04-27T18:45:22.302689Z → 2026-04-27T22:00:21.111108Z | 586420.232 ms | 3 | legacy_research_only, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-04-29.jsonl | 31.8 Kio | f9dba9b6d2b761ed75b39d47bacbe4fbcd76b2259813403b8807ae3dc8f89cca | 13 | 2026-04-29T08:43:00.000000Z → 2026-04-29T11:07:48.711219Z | 217331.052 ms | 1 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-01.jsonl | 1.7 Kio | 5994807ba11cd1340bdf0b195383306ff980a441d4b627345e6d7e2a80e02126 | 1 | 2026-05-01T22:21:03.596712Z → 2026-05-01T22:21:03.596712Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-02.jsonl | 11.9 Kio | 3bf08186e9c6fd91682c6a81fa2a513690f07efb8e3d2f87c80da257b56d42ae | 7 | 2026-05-02T08:00:20.198754Z → 2026-05-02T15:31:20.762306Z | 213302.323 ms | 1 | legacy_research_only, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-03.jsonl | 3.4 Kio | 9ef2b825c5534dcfbd73ae7aa42d1d34389013a8c4c114eb0ca0f20853e81602 | 2 | 2026-05-03T18:55:38.401755Z → 2026-05-03T18:56:03.598004Z | 25196.249 ms | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-05.jsonl | 5.1 Kio | 61b656e4c31f1899af7eb9f127040363b43058c64878ebe31980526d61c9d248 | 3 | 2026-05-05T20:03:50.445122Z → 2026-05-05T20:11:06.097526Z | 217826.202 ms | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-13.jsonl | 17.2 Kio | 6f85a57c820d943d1dafd630b2251ef71d7b82e7c5c981444ac4e5b484b07ac1 | 8 | 2026-05-13T10:08:49.973853Z → 2026-05-13T20:52:36.248835Z | 2369032.196 ms | 2 | legacy_research_only, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-16.jsonl | 2.1 Kio | b429b769209e75156233e24a26fd9305b8fb6d7e338b5169ef39ab0eb267c806 | 1 | 2026-05-16T12:46:39.139644Z → 2026-05-16T12:46:39.139644Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-17.jsonl | 6.2 Kio | dd422aa556ef262f85e4d67bed70aa6267b6270944adaf62a76ab6cca11441c3 | 3 | 2026-05-17T20:43:47.545576Z → 2026-05-17T20:55:48.550681Z | 360502.552 ms | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-26.jsonl | 10.4 Kio | ad4fa8821a1debbe55e74f5f80d9a7dc820e9da0b1a12c54c44b5b9dca109d05 | 5 | 2026-05-26T19:54:07.195127Z → 2026-05-26T20:44:56.100986Z | 311011.153 ms | 1 | legacy_research_only, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-27.jsonl | 6.2 Kio | 55eb348c143e9d5745be40f9ed7778c4d9e51c028387843b987ec46578ce15b4 | 3 | 2026-05-27T13:31:48.918478Z → 2026-05-27T17:44:34.457856Z | 7582769.689 ms | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-05-29.jsonl | 6.2 Kio | 86b471dcdabb1ca2fa32922ab10c0a32420c3c8c4cae7d92e0e4c45057253866 | 3 | 2026-05-29T08:10:57.955085Z → 2026-05-29T13:11:33.786774Z | 9017915.844 ms | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-02.jsonl | 2.1 Kio | 3171440a370d5e990f79099bfd2cb71170a09ff33d1519a374b4f57f61d81be9 | 1 | 2026-06-02T06:12:21.880085Z → 2026-06-02T06:12:21.880085Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-05.jsonl | 2.1 Kio | c6f70e9c565b68ced392e57eb125bc51241f3c086c77fe05db0953354782b1ea | 1 | 2026-06-05T08:14:17.024040Z → 2026-06-05T08:14:17.024040Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-07.jsonl | 2.1 Kio | e792a4f0b3bab61b68cfb26430517b422ab3433d86201a1a3e35fc4ee827641c | 1 | 2026-06-07T11:07:09.542653Z → 2026-06-07T11:07:09.542653Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-09.jsonl | 2.1 Kio | df81881b2481b026aa36494b85fa56c6f0627cb678a15bc0896a370a2265c67d | 1 | 2026-06-09T05:47:33.660242Z → 2026-06-09T05:47:33.660242Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-10.jsonl | 2.1 Kio | cc4ffe8b6e74b00a71dda7dbd5754884abd8b5cf49b96e057ac423b8efe35f12 | 1 | 2026-06-10T11:54:13.152606Z → 2026-06-10T11:54:13.152606Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-12.jsonl | 2.1 Kio | 9712ff02ebfb18bae537e87106dfc02174d9f5a3caae6b03f9e4389ff9077fa8 | 1 | 2026-06-12T06:16:39.132305Z → 2026-06-12T06:16:39.132305Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-14.jsonl | 12.5 Kio | c517b663bd3e6ead1966e4c77b7ca24009ed1c3ac7ac65cf82fadaf7bf675586 | 6 | 2026-06-14T20:41:51.929416Z → 2026-06-14T20:55:40.197171Z | 42358.984 ms | 2 | legacy_research_only, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-15.jsonl | 6.2 Kio | 2899fc6d576403a614a1784e7a49d19492104568ddaa29f1a480f1cb764a78ca | 3 | 2026-06-15T07:47:00.244719Z → 2026-06-15T07:54:03.121151Z | 211438.216 ms | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-18.jsonl | 2.1 Kio | 50571499f9bc1b3ddb35e85070992fda1970eaf24d3301fa3237d3949aa07490 | 1 | 2026-06-18T08:22:41.914897Z → 2026-06-18T08:22:41.914897Z | — | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-22.jsonl | 12.5 Kio | eeb080a3cd1bd0d707dba2822afac58178a26e12d5ed4f55b2daaaf149c586fe | 6 | 2026-06-22T08:56:33.280555Z → 2026-06-22T15:29:23.055338Z | 4122237.84 ms | 0 | legacy_research_only |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-23.jsonl | 25.0 Kio | ff201a91e5d7a6df8250a64ac9460a007d99048ddeb2498c29cbe83feb81509a | 12 | 2026-06-23T09:13:57.004266Z → 2026-06-23T13:29:03.606439Z | 782743.028 ms | 1 | legacy_research_only, inferred_time_gaps |
| trident_live_snapshots | C | /workspaces/trident/data/live_snapshots/2026-06-24.jsonl | 2.1 Kio | ea345df9b22f9038f68181cd1dede7f8e7185e7720b2e7483782eebaf1d38ed9 | 1 | 2026-06-24T15:24:04.482937Z → 2026-06-24T15:24:04.482937Z | — | 0 | legacy_research_only |
| hip4_nautilus_books | B | /workspaces/trident/server-data/hip4/logs/hip4_nautilus_shadow/archive_before_nautilus_ws_clean_20260527T081334Z/book_snapshots.jsonl | 490.3 Kio | 0483bd1d27513f0941d37e64567fb85602f0f5252dbd1f83fdcbcec0fa3c4620 | 1260 | 2026-05-27T07:49:35.978658Z → 2026-05-27T08:13:09.442000Z | 1842.758 ms | 14 | legacy_research_only, inferred_time_gaps |
| hip4_nautilus_books | B | /workspaces/trident/server-data/hip4/logs/hip4_nautilus_shadow/book_snapshots.jsonl | 388.8 Mio | e23544528c2e64b6c72ae42e93458e4ed6a002993aa4808e75ce933cb435d959 | 892492 | 2026-05-27T08:13:37.566000Z → 2026-07-06T20:10:43.088000Z | 16232.0 ms | 11 | legacy_research_only, out_of_order_timestamps, inferred_time_gaps |
| hip4_paper | B | /workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/market_observations.jsonl | 184.8 Mio | ff1865eddea9195ef678055f78955e00925b9aeb6f81c223ccda3e11acb060d5 | 118168 | 2026-07-05T17:58:27.563577Z → 2026-07-06T20:10:42.905386Z | 511.027 ms | 3287 | legacy_research_only, inferred_time_gaps |
| hip4_paper | B | /workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/settlements.csv | 18.7 Kio | b1e13ba3e1d4790d335e88528a3a2a923285972cb694fa672e3194c13cd3d760 | 100 | 2026-05-24T16:05:45.707728Z → 2026-07-06T06:05:45.133147Z | 8775534.987 ms | 40 | legacy_research_only, inferred_time_gaps |
| hip4_paper | B | /workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/shadow_maker_quotes.csv | 34.2 Mio | 99a25dcb84735313ce2e940d3e282e12a70d8ff1e33d292077da906bfc45b922 | 145388 | 2026-05-24T15:28:06.351727Z → 2026-07-06T20:10:19.476626Z | 12955.204 ms | 1635 | legacy_research_only, inferred_time_gaps |
| hip4_paper | B | /workspaces/trident/server-data/hip4/logs/hip4_outcome_mainnet_paper/trades.csv | 11.9 Kio | e1931d8087090bab45f16b13144035e3429f73558e96d56733e4f1c25f2319b0 | 103 | 2026-05-24T15:28:06.353757Z → 2026-07-06T09:51:52.893511Z | 6619556.805 ms | 42 | legacy_research_only, inferred_time_gaps |
| trident_replay_sample | C | /workspaces/trident/server-data/replay_inputs/special_symbols_hl_15m_30d_20260419.jsonl | 10.2 Mio | 5efedbfdadf3c8b3ad95e3cc70fe66fc962e01c6cb25d2e7ce782f46e936eef7 | 2881 | 2026-03-20T18:29:59.999000Z → 2026-04-19T18:29:59.999000Z | 900000.0 ms | 0 | legacy_research_only |

## Anomalies

Aucune erreur de lecture ou symlink détecté.

## Doublons et captures répétées

- Même nom de capture que `/workspaces/trident/server-data/hip4/logs/hip4_nautilus_shadow/archive_before_nautilus_ws_clean_20260527T081334Z/book_snapshots.jsonl` : `/workspaces/trident/server-data/hip4/logs/hip4_nautilus_shadow/book_snapshots.jsonl`
