# Validation du lot A — comptes et traçabilité — 9 octobre 2026

Les constats 1, 2, 6 et 9 de l'audit complémentaire ont été corrigés, sans migration ni réparation automatique des données existantes.

| Parcours | Comportement obtenu |
| --- | --- |
| Secondaire vers principal ou autre administrateur | Réinitialisation, profil, blocage et suppression refusés par HTTP 403 avant mutation; tentative journalisée sans secret |
| Principal vers secondaire ou professeur | Réinitialisation autorisée; hachage vérifié par le modèle |
| Principal vers lui-même | Réinitialisation autorisée |
| Superadministrateur vers principal | Réinitialisation autorisée dans l'école sélectionnée |
| Secondaire vers lui-même | Profil et secret personnels autorisés; changement de statut refusé |
| Secondaire vers professeur ou parent | Gestion conservée dans sa propre école, sous réserve des protections existantes |
| Autre école | Compte inaccessible ou mutation refusée |
| Suppression du principal | Protection existante conservée |

La même garde couvre les six routes de mutation de comptes, y compris les variantes `/admin/utilisateur/...` et `/api/users/...`. Les alias des rôles d'administrateur et de superadministrateur sont classés dans la hiérarchie. Les anciens comptes avec un statut NULL restent compatibles.

`Utilisateur.is_active`, le chargeur Flask-Login et `/login` refusent les comptes bloqués. Lors de la requête suivant le blocage, la session est effacée et le cookie de connexion persistante est supprimé. Le cookie persistant seul est également refusé. Réactiver le compte ne restaure pas la session effacée : une nouvelle connexion est nécessaire.

Le GET du journal n'appelle plus de seed. La fonction injectant les cinq incidents fictifs a été supprimée. L'état vide demandé est affiché et les listes d'écoles, les utilisateurs, les événements et les compteurs respectent le même périmètre d'accès. Aucune suppression des traces déjà présentes n'a été effectuée : leur origine doit être vérifiée avant toute réparation.

L'ancienne route `/admin/create_user` vérifie les droits du principal puis redirige son GET ou délègue son POST à la création canonique. Les nouveaux comptes utilisent `set_mot_de_passe()`, reçoivent l'école du principal et gardent le rôle admin indépendamment des champs de rôle ou d'école soumis. Le champ ancien `password` reste compatible. L'unicité globale de l'email est conservée conformément au schéma existant; la comparaison est insensible à la casse.

## Vérifications

`python -m pytest tests/test_securite_comptes_et_audit.py -q` : **15 tests réussis**. La factory, le loader, les routes, les middleware et les templates sont réels; les bases SQLite sont uniquement en mémoire. Les contextes de requête restent indépendants pour réellement exercer le chargement des sessions.

Le fichier couvre les six scénarios demandés et leurs variantes : principal/pairs, opérations autorisées, accès inter-écoles refusé, cookies persistants, connexion bloquée pour les quatre rôles, réactivation, ancienne création et création canonique. Un écouteur SQL vérifie que le GET du journal n'effectue aucune écriture dans les tables.

Suite complète : **548 tests réussis, 12 ignorés, aucun échec ni erreur**, en 13 minutes et 10 secondes, avec code de sortie 0. Les 15 nouveaux tests sont inclus dans ces 548 réussites. Les cas ignorés nécessitent des données de test absentes ou `TEST_POSTGRES_DATABASE_URL`, non configuré pour cette validation; aucune intégration PostgreSQL réelle n'est donc revendiquée. La suite émet aussi 66 avertissements, dont des dépréciations des bibliothèques.

La suite a été exécutée dans une copie du code sous `scratch/comptes_lot_a_suite_20261009_983aa162f1854e2eb5e44236444982f0`, sans copie des bases, sauvegardes ou fichiers `.env`. Les appels à la factory sans configuration utilisent le mode test et SQLite en mémoire. `validation_output.log` et `validation_results.xml` sont conservés dans cette copie. Les empreintes des cinq fichiers applicatifs et du nouveau fichier de tests correspondent au correctif du projet.

`git diff --check` passe. Aucun accès en écriture à la base de travail du projet n'a été nécessaire.
