# Validation du lot C — pédagogie, confidentialité et cours archivés — 9 octobre 2026

Les constats **5, 7, 8, 10 et 12** de l'audit complémentaire sont corrigés sans migration de schéma ni réparation automatique des dossiers historiques.

| Parcours | Comportement obtenu |
| --- | --- |
| Cours d'une année archivée | La modification, la suppression, l'affectation d'un professeur, l'import de notes et la création dans une classe archivée sont refusés par HTTP 403. La liste et le détail des cours restent consultables en lecture seule. |
| Fiche JSON d'élève consultée par un professeur | La réponse conserve les données pédagogiques et d'assiduité, mais omet `comptabilite` et `eleve.frais_annuels`. Le calcul de synthèse financière et la requête de paiements sont évités. La modale masque le statut de scolarité; l'administration conserve ses données financières. |
| Délibération annuelle | Les matières attendues sont vérifiées dans chaque période officielle. Une matière sans note valide produit `Dossier Incomplet`, liste les matières manquantes et réserve la décision au conseil. Les notes anciennes sans `inscription_id` sont acceptées seulement si l'élève, l'école, l'année, le cours et la période correspondent. |
| Note individuelle de 0/20 | `NoteForm.valeur` utilise `InputRequired` et accepte le zéro explicite; la route d'édition l'enregistre. La plage de 0 à 20 reste contrôlée. |
| Publication des bulletins | Seules les inscriptions `inscrit` et `actif` sont exigées pour la complétude, les classements et l'activation du bouton de publication. Une inscription `annulee`, `transfere` ou `radie` ne bloque plus la publication; la création de notes pour une inscription non scolarisée est refusée. L'export PDF groupé ignore les inscriptions annulées sans bulletin et garde les anciens bulletins existants des élèves transférés ou radiés. |

Les délibérations historiques des élèves transférés ou radiés restent consultables. Aucune matière, note ou date de période absente des données anciennes n'est inventée. Les références initiales de l'audit décrivent l'état avant ces corrections.

## Vérifications

Le nouveau fichier `tests/test_pedagogie_et_confidentialite_lot_c.py` exerce les routes Flask et les services réels avec SQLite en mémoire : cours archivé, fiche professeur sans calcul financier, délibération avec Français manquant malgré deux semestres de Maths, note sans période qui ne doit pas compter pour deux semestres, zéro enregistré, publication avec une inscription annulée (bouton et route) et refus de note pour cette inscription. **7 tests réussis** lors du passage ciblé final.

Les suites ciblées des cours, de la fiche élève/professeur et des règles pédagogiques ont également été exécutées : cours **5/5**, fiche élève/professeur **15/15**, pédagogie historique **34/34** et cas d'export PDF archivé avec une inscription annulée **1/1**. Ces groupes se recoupent avec la suite complète et ne sont pas additionnés à son total.

Suite complète : **567 tests réussis, 12 ignorés, aucun échec ni erreur**, avec code de sortie 0, en 11 minutes et 14 secondes. Les 10 nouveaux cas (7 dans le fichier du lot C et 3 ajoutés à des fichiers existants) sont inclus dans les 567 réussites. La suite émet 68 avertissements, essentiellement des dépréciations de bibliothèques. Les 12 tests ignorés exigent des données de test absentes ou une base PostgreSQL de test non configurée; aucune intégration PostgreSQL réelle n'est revendiquée.

La suite a été exécutée dans une copie isolée sous `scratch/pedagogie_lot_c_suite_20261009_cdb55f578445440b948c24567d608012`, sans bases, sauvegardes ni fichiers `.env` du projet. Le code utilisait `APP_ENV=testing` et SQLite en mémoire. `validation_output.log` et `validation_results.xml` y sont conservés. Les empreintes des 15 fichiers applicatifs et de tests vérifiés correspondent aux fichiers du projet. `git diff --check` passe.
