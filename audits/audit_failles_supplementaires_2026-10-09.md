# Audit complémentaire de Klasora — 9 octobre 2026

Mise à jour après le lot A : les constats **1, 2, 6 et 9** ont été corrigés. Les 15 tests dédiés passent; la validation complète est suivie dans [le rapport du lot A](validation_lot_a_securite_comptes_2026-10-09.md). Les descriptions et références ci-dessous documentent l'état audité avant ces corrections.

Douze autres défauts ont été confirmés dans les comptes, la paie et les règles pédagogiques. Ce rapport complète celui du 8 octobre : les problèmes d'archives concernent ici les cours et la paie; le problème de délibération concerne des matières manquantes malgré deux semestres évalués.

Le code applicatif et la base réelle n'ont pas été modifiés. Les reproductions utilisent les modèles et les routes ou services réels, avec SQLite en mémoire dans des applications Flask minimales, sans appeler `create_app()`.

| N° | Priorité | Défaut confirmé | Conséquence |
| --- | --- | --- | --- |
| 1 | Haute | Réinitialisation du principal par un admin secondaire | Prise de contrôle du compte principal |
| 2 | Haute | Statut utilisateur bloqué ignoré | Connexion et sessions toujours autorisées |
| 3 | Haute | Versements de salaire remplacés au lieu d'être cumulés | Faux solde et perte d'historique |
| 4 | Haute | Paie archivée modifiable et recalculable | Réécriture des montants historiques |
| 5 | Haute | Cours archivé modifiable | Structure pédagogique historique altérée |
| 6 | Haute | Journal vide alimenté avec des incidents fictifs | Traçabilité trompeuse dans toutes les écoles |
| 7 | Moyenne | Finances familiales exposées au professeur par JSON | Confidentialité différente selon l'interface |
| 8 | Moyenne | Délibération complète malgré des matières manquantes | Suggestion de passage sur un dossier provisoire |
| 9 | Moyenne | Ancienne création admin produisant un compte inutilisable | Compte sans école et mot de passe incompatible |
| 10 | Moyenne | Zéro refusé par le formulaire individuel de note | Saisies incohérentes entre parcours |
| 11 | Moyenne | Paie civile hors de l'année scolaire acceptée | Historique financier mal rattaché |
| 12 | Moyenne | Inscription annulée encore exigée à la publication | Publication bloquée et notes encore autorisées |

Les priorités expriment l'impact dans l'application. Les accès testés nécessitent les rôles indiqués; aucune exploitation anonyme ni incident sur les données réelles n'est revendiqué.

## 1. Un administrateur secondaire peut prendre le compte principal

**Sources :** `app/routes/utilisateurs.py:168`, `:363`, `:375`, `:382`, `:400`.

La liste des utilisateurs masque le principal aux administrateurs secondaires, mais le POST `/admin/utilisateur/<id>/reset-password` vérifie seulement le rôle admin et l'appartenance à l'école. Sa protection spécifique concerne le `super_admin`, pas l'administrateur principal. Le nouveau mot de passe est renvoyé à l'appelant.

**Reproduction :** administrateur secondaire authentifié, requête sur l'ID du principal → HTTP 200; le secret retourné est accepté par `check_mot_de_passe` du principal. L'absence du principal dans la liste ne protège pas un ID accessible par requête directe.

**Impact :** usurpation du principal et accès aux actions qui lui sont réservées, notamment la création d'administrateurs.

**Correction proposée :** définir les droits de l'acteur sur la cible et appliquer la même garde à toutes les mutations sensibles de comptes. La reproduction vérifie le remplacement du secret; elle ne rejoue pas toutes les actions possibles après usurpation.

## 2. Bloquer un utilisateur n'interrompt pas son accès

**Sources :** `app/models.py:324`, `:334`; `app/__init__.py:147`; `app/routes/auth.py:453`, `:484`; `app/middleware.py:433`.

Le champ `Utilisateur.statut` est enregistré, mais ni la connexion ni le chargement des sessions ne refusent `bloque`. Le modèle hérite de `UserMixin` sans adapter `is_active` au statut. Le middleware global contrôle l'état de l'école, pas celui du compte.

**Reproduction :** compte administrateur passé à `bloque` → une session existante atteint une route témoin protégée par les vrais décorateurs avec HTTP 200. Une nouvelle connexion par la vraie route `/login` retourne HTTP 302 avec `_user_id` enregistré.

**Impact :** un utilisateur bloqué conserve les autorisations de son rôle. Le blocage d'un ancien employé ou d'un compte compromis est inefficace.

**Correction proposée :** refuser le compte au login et invalider son authentification lors du chargement de chaque session. Un contrôle limité aux nouvelles connexions laisserait les sessions existantes actives.

## 3. Les versements successifs de salaire s'écrasent

**Sources :** `app/templates/paie_personnel.html:559`; `app/routes/paie_personnel.py:361`, `:363`; `tests/test_paie_personnel.py:268`.

L'écran propose le solde restant lors du deuxième règlement. Le serveur traite pourtant ce montant comme le total payé : `fiche.montant_paye = montant_verse`. Il remplace également la référence et la date du règlement précédent.

**Reproduction :** salaire net de 100 000 FCFA; règlement de 40 000, puis règlement du solde proposé de 60 000 → HTTP 200 aux deux appels, mais total enregistré de 60 000, reste de 40 000 et statut `partiel`.

**Impact :** dette salariale fictive, risque de nouveau paiement d'une somme déjà versée et disparition des détails du premier versement. Le test existant envoie 100 000 au deuxième appel et ne couvre donc pas le parcours proposé par l'écran.

**Correction proposée :** distinguer un nouveau versement d'une correction du total payé; conserver chaque règlement dans un historique et calculer le cumul, avec un traitement explicite des doubles soumissions.

## 4. Les fiches de paie archivées restent modifiables

**Sources :** `app/routes/paie_personnel.py:221`, `:306`, `:354`, `:399`.

Les routes de règlement et d'ajustement chargent la fiche par ID et école sans vérifier le statut de son année réelle. Le calcul mensuel accepte aussi une année archivée consultée et réutilise le contrat actuel du professeur pour mettre à jour la fiche ancienne.

**Reproductions :** année active consultée, fiche d'une année `archivee` → ajustement HTTP 200, net de 80 000 à 110 000; règlement HTTP 200, payé de 80 000 à 1. Avec l'année archivée consultée, recalcul HTTP 200 → salaire de base historique de 80 000 remplacé par le salaire actuel de 100 000.

**Impact :** les documents de paie et montants historiques peuvent être réécrits après archivage.

**Correction proposée :** contrôler l'année de la fiche avant chaque mutation, refuser les recalculs d'archives et conserver les paramètres contractuels historiques. Vérifier uniquement l'année sélectionnée dans la session ne suffit pas.

## 5. Un cours archivé peut encore être modifié

**Sources :** `app/routes/cours.py:429`, `:444`, `:465`.

Quand la classe envoyée est déjà celle du cours, la route évite `valider_classe_pour_nouveau_cours` et pose `classe_error=None`. Aucun contrôle n'examine l'année source du cours avant de modifier ses propriétés.

**Reproduction :** cours Maths dans une classe d'année `archivee`, coefficient 1; formulaire POST administrateur avec la même classe et coefficient 4 → HTTP 302, coefficient 4 persisté.

**Impact :** structure pédagogique historique modifiable; les consultations utilisant le cours courant peuvent reprendre un coefficient ou un libellé différent. La mutation du cours est démontrée, pas la modification d'une moyenne déjà persistée dans un bulletin.

**Correction proposée :** contrôler le statut de l'année source avant toute modification, même si la classe reste identique. Ce chemin est distinct des mutations de bulletins déjà auditées.

## 6. Consulter un journal vide crée des incidents fictifs

**Sources :** `app/routes/utilisateurs.py:448`, `:451`, `:453`, `:456`, `:512`, `:527`, `:538`.

La route `/journaux_corrections` appelle `seed_critical_corrections_if_empty()`. Si la table globale est vide, cette routine crée cinq événements de démonstration pour chaque école : suppression de note, altération de frais, suppression d'élève, modification de coefficient et réévaluation après clôture. Elle emploie des cibles fixes et attribue tous les événements à un utilisateur choisi globalement. Aucun contrôle de mode démonstration ne limite cette écriture.

**Reproduction :** deux écoles et zéro correction → appel de la routine réelle, dix événements persistés, cinq dans chaque école. Le déclenchement par GET a été confirmé par lecture de la route; le rendu complet de la page n'a pas été exécuté.

**Impact :** le journal accuse des utilisateurs d'opérations qui n'ont pas eu lieu. L'ouverture par un administrateur d'une école peut aussi créer des journaux dans les autres écoles. Les opérations décrites ne sont pas exécutées : ce sont les traces qui sont fabriquées.

**Correction proposée :** retirer ce remplissage du parcours métier et réserver les données fictives à une commande de démonstration explicite. Une éventuelle réparation des journaux existants devra identifier précisément les traces concernées avant suppression.

## 7. Le professeur reçoit les finances familiales par JSON

**Sources :** `app/routes/eleves.py:780`, `:883`, `:969`, `:1609`.

La fiche HTML masque explicitement les finances aux professeurs. L'API `/api/eleves/<id>/fiche`, accessible aux professeurs autorisés sur l'élève, calcule et renvoie systématiquement le bloc `comptabilite`.

**Reproduction :** professeur enseignant dans la classe de l'élève → HTTP 200, `total_frais=100000`, `total_paye=20000`, `reste_a_payer=80000`.

**Impact :** accès aux dettes des familles malgré la restriction du parcours HTML. La preuve porte sur un élève de la classe autorisée, pas sur une lecture inter-écoles.

**Correction proposée :** filtrer les champs côté serveur suivant le rôle et éviter le calcul financier pour le professeur.

## 8. La délibération annonce un dossier complet malgré des matières manquantes

**Sources :** `app/services/passage_annee.py:1096`, `:1104`, `:1110`; `app/services/evaluations.py:112`.

Sans bulletin existant, le calcul de secours parcourt les matières ayant des notes. Dès qu'il trouve une moyenne dans chacun des deux semestres, il considère le cursus complet, sans vérifier les matières attendues ni l'officialisation des périodes.

**Reproduction :** Maths et Français attendus; Maths à 18 en S1 et S2, aucune note de Français, périodes non publiées → moyenne annuelle 18, `cursus_incomplet=False`, `statut_deliberation="Complet"`, `suggestion="passage"`. Pour le même dossier, le service de complétude retourne `status="provisoire"`, `missing_subjects_names=["Francais"]` et `is_pedagogically_complete=False`.

**Impact :** suggestion de passage et affichage de complétude contradictoires avec le dossier pédagogique. Aucune décision automatique de passage n'a été exécutée dans la reproduction.

**Correction proposée :** utiliser la complétude canonique pour chaque période avant de produire une délibération définitive. Ce défaut diffère de l'ancien cas à un seul semestre : ici, S1 et S2 existent tous deux.

## 9. L'ancienne création d'administrateur produit un compte inutilisable

**Sources :** `app/routes/utilisateurs.py:104`, `:118`, `:119`, `:130`, `:217`; `app/models.py:383`; `app/routes/auth.py:453`, `:457`.

Deux routes de création restent actives. `/admin/creer_utilisateur` exige le principal et rattache le compte à l'école. `/admin/create_user` accepte aussi un administrateur secondaire, utilise bcrypt et omet `ecole_id`, alors que la vérification des mots de passe utilise Werkzeug.

**Reproduction :** POST de création par un secondaire → HTTP 302 et utilisateur réellement créé, mais `ecole_id=None`. La vérification du secret créé lève `TypeError` avec la version installée de Werkzeug. Même en remplaçant le hash par un format accepté, l'absence d'école ferait refuser la connexion normale.

**Impact :** succès de création trompeur, compte orphelin, connexion inutilisable et parcours qui ne respecte pas la règle du principal. La reproduction ne démontre pas un compte supplémentaire utilisable pour une escalade.

**Correction proposée :** retirer ou rediriger la route ancienne vers le même service de création et les mêmes contrôles. Examiner les comptes existants avant toute réparation automatique.

## 10. Une note de zéro est refusée par le formulaire individuel

**Sources :** `app/forms.py:188`; `app/routes/notes.py:569`; `app/services/notes_annuelles.py:617`.

`NoteForm.valeur` combine `DataRequired()` et `NumberRange(min=0, max=20)`. Après conversion en nombre, `DataRequired()` considère `0.0` comme une valeur absente. Le service de notes accepte pourtant zéro.

**Reproduction :** formulaire recevant `valeur="0"` → conversion en `0.0`, validation refusée avec `This field is required.`.

**Impact :** correction individuelle vers zéro impossible alors que les autres parcours peuvent enregistrer cette valeur. La reproduction cible le validateur réel du formulaire; elle ne prétend pas qu'un zéro déjà stocké est effacé.

**Correction proposée :** utiliser `InputRequired()` pour distinguer une saisie absente d'un zéro, en conservant le contrôle de plage.

## 11. Une paie de janvier 1900 peut être rattachée à 2026–2027

**Sources :** `app/routes/paie_personnel.py:221`, `:226`, `:229`, `:282`.

Le calcul mensuel contrôle le mois entre 1 et 12, mais ne vérifie pas que la période civile appartient aux dates de l'année scolaire sélectionnée. Il rattache ensuite la fiche à cette année.

**Reproduction :** année active 2026–2027; `POST /paie-personnel/calculer-mois` avec janvier 1900 → HTTP 200 et fiche de janvier 1900 créée sous 2026–2027.

**Impact :** historiques et regroupements annuels incohérents, avec pointages recherchés dans une période qui ne correspond pas à l'année scolaire.

**Correction proposée :** valider la période civile suivant une règle explicite de rattachement à l'année scolaire et retourner un conflit pour une période hors périmètre. Ce cas concerne la paie du personnel, distincte des reçus de scolarité du rapport précédent.

## 12. Une inscription annulée bloque encore les bulletins des élèves scolarisés

**Sources :** `app/services/inscriptions_annuelles.py:359`; `app/services/passage_annee.py:454`; `app/services/notes_annuelles.py:202`, `:652`; `app/services/evaluations.py:645`.

`annulee` est un statut valide réellement produit par les services d'annulation. Les listes de notes et le contrôle de publication prennent pourtant toutes les inscriptions de la classe sans exclure ce statut. La présence de l'inscription suffit aussi au contrôle de mutation d'une note.

**Reproduction :** année active, une classe avec un élève inscrit dont la note de Maths est présente et un élève à inscription annulée sans note → les deux figurent dans les inscriptions de notes; la validation de saisie pour l'annulé retourne `(True, None)`; la publication est refusée pour le seul bulletin incomplet de l'élève annulé.

**Impact :** il faut artificiellement noter un dossier annulé pour satisfaire le contrôle, ou la publication reste bloquée malgré les dossiers scolarisés complets.

**Limite :** la fixture crée directement une inscription au statut valide `annulee` dans une année active. Le parcours complet transfert, annulation d'une préinscription puis activation de l'année n'a pas été rejoué; les services produisant ce statut ont été vérifiés séparément.

**Correction proposée :** définir les statuts participant à la scolarité et à la publication, puis appliquer cette sélection aux listes, aux mutations et aux contrôles pédagogiques. Ne pas supprimer l'inscription historique pour contourner le blocage.

## Validation et portée

Les accès et mutations HTTP utilisent les vrais décorateurs présents sur les routes. Les applications de reproduction n'enregistrent pas le middleware global ni le contrôle CSRF; le middleware a été lu pour vérifier qu'il n'ajoute pas de garde utilisateur ou d'archive couvrant les cas signalés. Les défauts de mutation sont testés comme actions d'utilisateurs autorisés dans une école active, hors maintenance, avec configuration terminée. Le CSRF ne corrigerait pas une autorisation insuffisante pour cet acteur authentifié.

Deux scripts de reproduction de l'état initial sont conservés dans `scratch/`, qui est ignoré par Git. Ils ne constituent plus des vérifications du code corrigé; le seed supprimé rend notamment l'ancien script de journal obsolète. Pour le lot A, utiliser `tests/test_securite_comptes_et_audit.py` :

- `python -m scratch.audit_acces_20261009` : compte bloqué, reset du principal et finances professeur; notifications WhatsApp désactivées.
- `python -m scratch.audit_nouvelles_20261009` : journal fictif et ancienne création admin.

Les autres scénarios ont été exécutés par des scripts isolés non conservés. Aucun test complet de la suite et aucun audit exhaustif de production ne sont revendiqués. Les montants présentés sont des données de test; ils ne prouvent pas que ces anomalies ont déjà affecté les données réelles.

Ordre proposé : protéger les comptes; fiabiliser les versements; fermer les mutations d'archives; retirer les faux journaux; harmoniser les droits JSON et les règles de complétude. Chaque correction pourra ensuite être validée par un scénario de non-régression ciblé, sans réparation aveugle des données historiques.
