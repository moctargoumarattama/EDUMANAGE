# Validation du lot B — paie RH — 9 octobre 2026

Les constats **3, 4 et 11** de l'audit complémentaire sont corrigés, sans migration de schéma ni modification automatique des données historiques.

| Parcours | Comportement obtenu |
| --- | --- |
| Nouveau versement | Le montant saisi s'ajoute au cumul payé. Une mise à jour SQL conditionnelle écrit le cumul, le statut et la trace ensemble; un montant dépassant le net est refusé. |
| Correction du total | Action et route distinctes, avec motif obligatoire. L'ancien et le nouveau total, la date, la référence et l'auteur sont tracés. |
| Historique | Chaque nouveau versement garde son montant, sa date, son mode, sa référence et son auteur. Un solde antérieur non détaillé est identifié sans lui inventer de date ni de référence. L'historique apparaît dans l'écran et le bulletin imprimable. |
| Année archivée | Recalcul, ajustement, versement et correction sont refusés selon l'année réelle de la fiche. L'écran archivé n'expose plus d'action de mutation; le bulletin reste consultable. Le GET de la paie ne rattache plus de fiche ancienne. |
| Contrat pendant consultation archivée | Modification refusée par la route, comme dans l'interface. |
| Mois civil | Le mois demandé doit intersecter les dates officielles de l'année scolaire; les mois hors calendrier renvoient HTTP 400. |
| Recalcul et ajustement | Le nouveau salaire net ne peut pas devenir inférieur au montant déjà versé. Le recalcul mensuel annule l'ensemble du lot dans ce cas. |
| Ancienne fiche sans année | Le recalcul POST ne la rattache que si une seule année scolaire de l'école couvre le mois et correspond à l'année consultée non archivée. Un cas ambigu est refusé. |

Les détails des nouveaux règlements sont conservés dans le champ texte `FichePaiePersonnel.note`, sous forme de journal JSONL séparé du commentaire libre. Le bulletin imprimable affiche le commentaire et l'historique à leurs emplacements respectifs. Aucun historique antérieur absent de la base n'est reconstitué : le système signale seulement le solde initial dont le détail est inconnu.

Le versement conditionnel empêche deux requêtes concurrentes de perdre un acompte ou de dépasser le net. Une **même demande rejouée** reste toutefois un nouveau versement si le solde le permet : il n'existe pas de clé d'idempotence persistante. L'interface désactive le bouton pendant l'envoi, mais cela ne couvre pas tous les rejouements réseau. Les tests SQLite en mémoire ne constituent pas une validation de concurrence PostgreSQL.

## Vérifications

`python -m pytest -q tests/test_paie_rh_securite_avancee.py tests/test_paie_personnel.py` : **17 tests réussis**, dont 9 nouveaux. Ils utilisent les routes Flask et les modèles réels avec SQLite en mémoire. Les scénarios incluent 40 000 + 60 000 = 100 000 FCFA, refus du trop-perçu, historique et correction du total, archives, mois 1900/2035, bornes valides, fiches anciennes ambiguës ou non, et isolation entre écoles.

Suite complète : **557 tests réussis, 12 ignorés, aucun échec ni erreur**, avec code de sortie 0, en 11 minutes et 46 secondes. Les 9 nouveaux tests sont inclus dans les 557 réussites. La suite émet 66 avertissements, principalement des dépréciations de bibliothèques. Les cas ignorés requièrent des données de test absentes ou une base PostgreSQL de test non configurée; aucune validation de concurrence PostgreSQL réelle n'est revendiquée.

La suite a été exécutée dans une copie isolée sous `scratch/paie_lot_b_suite_20261009_31f42d5718df4b0d9256be07e82cdfed`, sans bases, sauvegardes ni fichiers `.env` du projet. Le code de test utilisait `APP_ENV=testing` et SQLite en mémoire. Le journal `validation_output.log` et le résultat `validation_results.xml` y sont conservés. Les empreintes des trois fichiers applicatifs et des deux fichiers de tests correspondent aux fichiers du projet. `git diff --check` passe.
