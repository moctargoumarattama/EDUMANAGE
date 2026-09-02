# Audit de cohérence fonctionnelle EDUMANAGE

Date: 2026-09-01

## Périmètre

Audit conservateur, sans correction fonctionnelle applicative. Les fichiers `config.py`, `app/__init__.py`, `app/models.py`, migrations et base de données n'ont pas été modifiés par l'audit.

Artifacts:

- `audits/coherence_audit.json`: inventaire automatisé complet des routes, templates, formulaires, JS, smoke tests et scans.
- `scripts/audit_coherence.py`: script d'audit non destructif. Il neutralise `app.log_correction` pendant les smoke tests pour éviter les écritures de logs applicatifs.

## Résumé chiffré

- Routes Flask enregistrées: 137
- Endpoints Flask enregistrés: 137
- Templates HTML trouvés: 80
- Templates rendus par routes: 67
- Templates candidats orphelins: 10
- Occurrences `url_for` invalides: 10
- Endpoints invalides distincts: 9
- Formulaires HTML détectés: 54
- Références JS / URL codées en dur détectées: 28
- Incohérences routes-modèles détectées automatiquement: 15 alertes, dont plusieurs faux positifs, mais plusieurs vrais bugs confirmés.
- Occurrences `except/pass/TODO/placeholders`: 294

## Tests exécutés

- `python -m compileall app scripts/audit_coherence.py scripts/compare_routes.py`: OK
- `from app import create_app; app=create_app()`: OK
- `scripts/compare_routes.py`: 137 routes avant, 137 après, 0 route supprimée, 0 route ajoutée.
- Smoke tests Flask `test_client()` sur pages principales par rôle existant: exécutés avec utilisateurs existants.

Rôles réellement présents en base pendant l'audit:

- `super_admin`
- `admin`
- `enseignant`
- `professeur`
- `parent`

Aucun utilisateur `eleve` trouvé en base pendant l'audit, malgré un menu élève dans `base.html`.

## A. Fonctionnalités réellement fonctionnelles

Statut observé par routes, templates et smoke tests:

- Auth basique: `main.login`, `main.logout`, reset password, aide, index.
- Dashboard admin: `main.admin_dashboard` répond en 200 pour admin.
- Dashboard enseignant/professeur: `main.enseignant_dashboard`, `main.enseignant_home` répondent en 200 pour `enseignant` et `professeur`.
- Dashboard parent et portail parent: `main.parent_dashboard`, `main.portal_parent` répondent pour parent.
- Liste élèves: `main.eleves` répond pour admin, enseignant et super_admin.
- Ajout élève: route + template + `EleveForm` existent.
- Liste professeurs: route + template fonctionnent pour admin.
- Cours: liste, ajout, détail, suppression, import/export ont des routes et templates existants.
- Notes: create/read/update/delete/export existent côté routes.
- Absences: create/read/update/delete/export existent côté routes.
- Paiements: create/read/delete/export/reçu PDF existent côté routes.
- Bulletins: liste et génération PDF par élève existent côté routes.
- Années scolaires: gestion et changement d'année existent.
- Classes: liste, ajout, détail, API existent.
- Emplois du temps: liste admin, ajout, modification, suppression existent.
- Recherche: page et endpoint JSON existent.
- QR codes: génération élève et page QR étudiants existent.
- Rapports: page rapports et endpoints stats existent.
- Alertes: page alertes, API alertes et notification test existent.

## B. Fonctionnalités partiellement fonctionnelles

### Élèves

- Gravité: élevée
- Fichier: `app/templates/edit_eleve.html:17`, `app/templates/eleves_en_difficulte.html:70`
- Problème: des templates/boutons appellent `main.edit_eleve` et `main.modifier_eleve`, mais aucune route d'édition élève n'existe.
- Impact: l'UPDATE élève est absent côté route, malgré une interface visuelle ancienne.
- Recommandation: décider si la modification élève doit être reconstruite ou supprimer les vieux templates/liens.

### Utilisateurs

- Gravité: élevée
- Fichier: `app/templates/edit_utilisateur.html:17`
- Problème: le formulaire appelle `main.edit_utilisateur`, endpoint inexistant.
- Impact: l'UPDATE utilisateur est absent côté route.
- Recommandation: reconstruire une vraie route d'édition utilisateur ou supprimer le template obsolète.

### Classes

- Gravité: moyenne
- Fichier: `app/templates/modifier_classe.html`
- Problème: template d'édition classe/élève probablement historique. Aucune route ne le rend.
- Impact: CREATE/READ existent, UPDATE/DELETE classe absents.
- Recommandation: clarifier si gestion classe doit avoir update/delete, sinon supprimer le template.

### Écoles

- Gravité: moyenne
- Problème: CREATE/READ/API/status/delete existent, mais pas de route d'édition classique.
- Recommandation: CRUD partiel acceptable seulement si l'édition n'est pas prévue.

### Professeurs

- Gravité: moyenne
- Problème: CREATE/READ/DELETE/assign classes existent, mais pas de route update professeur.
- Recommandation: reconstruire UPDATE professeur si nécessaire.

### Cours

- Gravité: moyenne
- Problème: CREATE/READ/DELETE/import/export existent, mais pas de route update cours.
- Recommandation: ajouter une vraie route update dans une future phase si attendu.

## C. Fonctionnalités cassées

### Menu élève

- Gravité: élevée
- Fichier: `app/templates/base.html:636`, `641`, `646`, `651`, `656`, `845`, `935`
- Problème: le menu élève référence des endpoints inexistants:
  - `main.eleve_dashboard`
  - `main.mes_notes`
  - `main.mon_bulletin`
  - `main.mon_emploi_temps`
  - `main.mes_alertes`
  - `main.mes_performances`
- Impact: si un utilisateur rôle `eleve` existe, `base.html` peut produire des `BuildError`.
- Classification: probablement menu ancien jamais terminé.
- Recommandation: soit reconstruire l'espace élève, soit retirer cette branche menu.

### API sync en double

- Gravité: élevée
- Fichiers:
  - `app/routes/sync.py:27` endpoint `main.api_sync`
  - `app/blueprints/api_sync.py:8` endpoint `api_sync.sync_data`
- Problème: deux routes POST `/api/sync` sont enregistrées.
- Impact: ambiguïté fonctionnelle. La version `main.api_sync` est enregistrée avant `api_sync.sync_data` et semble être celle effectivement utilisée.
- Incohérences dans l'ancienne version `api_sync.sync_data`:
  - `app/blueprints/api_sync.py:28` utilise `Eleve(matricule=...)`, mais `Eleve.matricule` n'existe pas.
  - `app/blueprints/api_sync.py:42` utilise `Note(..., matiere=...)`, mais `Note.matiere` n'existe pas.
  - `app/blueprints/api_sync.py:48` utilise `Absence(..., date=...)`, mais le modèle utilise `date_absence`.
- Recommandation: probablement supprimer ou désenregistrer l'ancien blueprint sync dans une phase corrective, après décision.

### Templates d'erreur manquants

- Gravité: élevée
- Fichier: `app/routes/errors.py:14`, `app/routes/errors.py:18`
- Problème: les handlers rendent `403.html` et `500.html`, mais ces templates n'existent pas.
- Impact: une erreur 403/500 peut devenir `TemplateNotFound: 500.html`, masquant la vraie erreur.
- Recommandation: créer les templates d'erreur ou modifier les handlers, dans une phase corrective.

### Journalisation sur GET pouvant casser

- Gravité: élevée
- Fichier: `app/routes/professeurs.py:59`, `app/routes/cours.py:154`, routes similaires utilisant `current_app.log_correction`
- Problème: certaines pages GET écrivent dans `JournalCorrection`. Un test ciblé réel sur `super_admin -> main.professeurs` a levé `IntegrityError: NOT NULL constraint failed: journal_corrections.ecole_id`, car la journalisation reçoit `ecole_id=None`.
- Impact: des pages de lecture peuvent produire une erreur serveur pour super_admin ou pour tout contexte sans école courante.
- Recommandation: décider si les consultations doivent être journalisées, puis rendre la journalisation compatible super_admin ou éviter l'écriture sur GET.

### Template appelé mais absent

- Gravité: moyenne
- Fichier: `app/routes/utilisateurs.py:99`
- Problème: `render_template('superadmin_ecoles.html')`, template absent.
- Impact: branche super-admin de `gestion_utilisateurs` cassée.
- Recommandation: reconstruire ce template ou rediriger vers un template existant, après décision.

### Parent dashboard JS notifications

- Gravité: moyenne
- Fichier: `app/templates/parent_dashboard.html:205`
- Problème: `fetch('/notifications')` attend du JSON, mais `main.notifications` retourne `render_template("notifications.html")`.
- Impact: le widget JS notifications parent ne peut pas fonctionner comme écrit.
- Recommandation: utiliser une API JSON existante ou créer une API dédiée dans une phase future.

### Paramètres système

- Gravité: moyenne
- Fichier: `app/templates/parametres.html:555`, `578`, `595`, `621`, `649`
- Problème: fetch vers `/admin/parametres/*`, aucune route correspondante enregistrée.
- Impact: page paramètres est visuellement présente mais non fonctionnelle.
- Classification: probablement ancienne/fausse fonctionnalité.
- Recommandation: supprimer le template si abandonné ou reconstruire une vraie fonctionnalité paramètres.

### Exports dans détail cours

- Gravité: moyenne
- Fichier: `app/templates/cours_details.html:81`
- Problème: lien `main.export_notes_excel` reçoit `id=cours.id`, mais la route `main.export_notes_excel` ne prend aucun paramètre et exporte toutes les notes.
- Impact: bouton "Exporter les notes" du détail cours ne fait probablement pas ce que l'utilisateur attend.
- Recommandation: pointer vers `main.export_notes` pour l'export cours, ou renommer le bouton.

- Gravité: élevée
- Fichier: `app/templates/cours_details.html:151`
- Problème: `main.export_notes_eleve_pdf` attend un id élève, mais reçoit `cours.id`.
- Impact: téléchargement PDF peut sortir le mauvais élève ou 404.
- Recommandation: supprimer ce bloc ou passer un vrai `eleve.id` selon l'intention.

## D. Fonctionnalités probablement abandonnées

- `app/templates/edit_eleve.html`: template jamais rendu, endpoint cible inexistant.
- `app/templates/edit_utilisateur.html`: template jamais rendu, endpoint cible inexistant.
- `app/templates/eleves_en_difficulte.html`: template jamais rendu, contient lien vers endpoint inexistant.
- `app/templates/modifier_classe.html`: template jamais rendu, semble ancien formulaire de modification élève/classe.
- `app/templates/parametres.html`: template jamais rendu, JS appelle des routes inexistantes.
- `app/templates/add_user.html`: template jamais rendu.
- `app/templates/ajouter_utilisateur.html`: template jamais rendu.
- `app/templates/confirmer_suppression_eleve.html`: template jamais rendu.
- `app/templates/bulletin_eleve.html`: template jamais rendu; la route `bulletin_eleve` génère un PDF, elle ne rend pas ce template.
- `app/templates/404.html`: existe, utilisé seulement par handler 404, pas une page fonctionnelle directe.

## E. Code mort probable

- `app/blueprints/api_sync.py`: probablement ancienne version de la synchronisation, en conflit avec `app/routes/sync.py`.
- `app/templates/parametres.html`: page visuelle sans route.
- `app/templates/edit_eleve.html`, `edit_utilisateur.html`, `modifier_classe.html`, `eleves_en_difficulte.html`: interfaces anciennes non raccordées.
- `app/routes/eleves.py:625` et `app/routes/professeurs.py:255`: secondes versions de suppression avec même URL que des routes précédentes.

## F. Routes orphelines ou suspectes

Routes peu ou pas reliées par templates principaux:

- `main.supprimer_eleve_route` et `main.supprimer_professeur_route`: mêmes URLs que `main.supprimer_eleve` / `main.supprimer_professeur`, probablement inatteignables par `url_for`.
- `api_sync.sync_data`: même URL/méthode que `main.api_sync`, probablement shadowed.
- Routes admin maintenance en GET: `admin.clean`, `admin.migrate`, `admin.create_tables`, `admin.optimize`, `admin.init_annees`, `admin.backup_complete`. Elles déclenchent des actions lourdes en GET. Dette sécurité/architecture, non corrigée ici.

## G. Endpoints invalides

Occurrences restantes:

- `app/templates/base.html:636` `main.eleve_dashboard`
- `app/templates/base.html:641` `main.mes_notes`
- `app/templates/base.html:646` `main.mon_bulletin`
- `app/templates/base.html:651` `main.mon_emploi_temps`
- `app/templates/base.html:656` `main.mes_alertes`
- `app/templates/base.html:845` `main.mes_performances`
- `app/templates/base.html:935` `main.eleve_dashboard`
- `app/templates/edit_eleve.html:17` `main.edit_eleve`
- `app/templates/edit_utilisateur.html:17` `main.edit_utilisateur`
- `app/templates/eleves_en_difficulte.html:70` `main.modifier_eleve`

Classification des 9 endpoints distincts:

- `main.edit_eleve`: A, fonctionnalité nécessaire si UPDATE élève attendu, route manquante.
- `main.edit_utilisateur`: A, fonctionnalité nécessaire si UPDATE utilisateur attendu, route manquante.
- `main.eleve_dashboard`: E, menu ancien jamais terminé.
- `main.mes_notes`: E, menu ancien jamais terminé.
- `main.mon_bulletin`: E, menu ancien jamais terminé.
- `main.mon_emploi_temps`: E, menu ancien jamais terminé.
- `main.mes_alertes`: E, menu ancien jamais terminé.
- `main.mes_performances`: E, menu ancien jamais terminé.
- `main.modifier_eleve`: C/A, probablement ancien nom d'endpoint pour édition élève, mais aucune route équivalente réelle.

## H. Incohérences Models/Routes confirmées

- `app/blueprints/api_sync.py:28`: `Eleve(matricule=...)` alors que `Eleve.matricule` n'existe pas.
- `app/blueprints/api_sync.py:42`: `Note(matiere=...)` alors que `Note.matiere` n'existe pas.
- `app/blueprints/api_sync.py:48`: `Absence(date=...)` alors que `Absence.date_absence` existe.
- `app/routes/cours.py:452`: `HistoriqueImport(cours_id=..., ecole_id=..., nb_notes=..., nb_erreurs=..., fichier_erreurs=...)`, mais `HistoriqueImport` ne définit que `fichier`, `date_import`, `utilisateur_id` et `utilisateur`.
- `app/forms.py:185` et `app/routes/notes.py:39`: mélange entre `AnneeScolaire.active` et `AnneeScolaire.statut='active'`. Les deux colonnes existent, mais la logique peut diverger.
- `app/models.py:914-924`: hook `after_insert` de `Eleve` peut créer une `AnneeScolaire` sans `ecole_id`, alors que `ecole_id` est non nullable.
- `app/routes/classes.py:77` et `187`: utilise le rôle `'prof'` et `current_user.classe_id`, mais `Utilisateur.classe_id` n'existe pas. Branche probablement ancienne.
- `app/routes/rapports.py:154`: filtre `Utilisateur.classe_id`, champ inexistant. Peut casser si `classe_filter` est fourni.

Faux positifs écartés:

- `Eleve.generer_code_parent` existe.
- `Professeur.generer_code` existe.
- `Classe.capacite`, `Classe.capacite_max`, `nom_complet`, `effectif_reel` existent.
- `SyncLog` existe dans `models.py`.

## I. Formulaires cassés ou incomplets

- `edit_eleve.html:17`: POST vers endpoint inexistant `main.edit_eleve`.
- `edit_utilisateur.html:17`: POST vers endpoint inexistant `main.edit_utilisateur`.
- `modifier_classe.html:8`: formulaire POST sans route connue qui le rende.
- `parametres.html`: plusieurs boutons JS postent vers routes inexistantes.
- `cours_details.html:200`: import notes est raccordé, mais `app/routes/cours.py:452` utilise `HistoriqueImport` avec mauvais champs, donc l'import avec audit risque de casser après lecture du fichier.
- `notes.html`: formulaire raccordé à `main.notes`, mais la sélection année active dépend de `active=True`, alors que d'autres routes utilisent `statut='active'`.

## J. JavaScript cassé ou suspect

- `app/templates/parametres.html:555`, `578`, `595`, `621`, `649`: routes `/admin/parametres/*` inexistantes.
- `app/templates/parent_dashboard.html:205`: attend JSON sur `/notifications`, mais route HTML.
- `app/static/js/offline-manager.js:219` et `345`: POST `/api/sync`, ambigu à cause des deux routes `/api/sync`.
- `app/templates/eleves.html:69-71`: URLs codées en dur (`/voir_eleve/`, `/bulletin_eleve/`, `/eleve/`). Elles correspondent actuellement aux URLs existantes, mais restent fragiles si les routes changent.
- `app/templates/inscriptions.html:18`, `22` et `app/templates/version.html:49`: chaînes encodées ressemblant à des SVG data URLs mal échappées (`/%3e%3c/svg%3e`), à vérifier visuellement.

## K. Duplications historiques

- Suppression élève:
  - `app/routes/eleves.py:558` `main.supprimer_eleve`
  - `app/routes/eleves.py:625` `main.supprimer_eleve_route`
  - Même URL `/eleve/<int:id>/supprimer`, même méthode POST.
  - La version appelée par `url_for('main.supprimer_eleve')` est la première.
  - La seconde semble ancienne/inutile.

- Suppression professeur:
  - `app/routes/professeurs.py:221` `main.supprimer_professeur`
  - `app/routes/professeurs.py:255` `main.supprimer_professeur_route`
  - Même URL `/professeur/<int:id>/supprimer`, même méthode POST.
  - La seconde semble ancienne/inutile.

- Synchronisation:
  - `app/routes/sync.py:27` `main.api_sync`
  - `app/blueprints/api_sync.py:8` `api_sync.sync_data`
  - Même URL `/api/sync`, même méthode POST.
  - `main.api_sync` semble plus récente et plus cohérente.

## L. Exceptions qui cachent des bugs

Zones les plus préoccupantes:

- `app/routes/utilisateurs.py:103`: englobe `render_template('superadmin_ecoles.html')`, peut cacher le template absent.
- `app/routes/ecoles.py:62`: si la récupération des écoles casse, retourne `admin/ecoles.html` avec liste vide, peut masquer une panne DB.
- `app/routes/notes.py:177`: ajout note catch général, message utilisateur générique.
- `app/routes/cours.py:469`: import notes catch général, peut masquer erreurs de mapping `HistoriqueImport`.
- `app/routes/eleves.py:271`: ajout élève catch général, peut masquer erreurs d'inscription automatique ou hook modèle.
- `app/routes/auth.py:216`: portail parent catch général et redirection, peut cacher erreurs relationnelles.
- `app/middleware.py`: très nombreux `except Exception` et `pass`, notamment autour de l'école courante et filtrage multi-écoles.
- `app/admin/routes.py` et `app/admin/scripts.py`: beaucoup de catches généraux autour de backup/migration/archive.

## M. TODO / placeholders / fausses fonctionnalités

- `app/services/__init__.py:26`: `TELEGRAM_CHAT_ID = "TON_CHAT_ID_GLOBAL"` valeur placeholder.
- `app/templates/parametres.html`: interface paramètres/maintenance/update/backup présente, routes absentes.
- `app/templates/sync_hors_ligne.html:165`: section "Données de test".
- `app/routes/sync.py:180`: type `test` explicitement ignoré.
- `app/templates/alertes.html:381+`: formulaire notification de test, raccordé à `/api/notifications/test`, fonctionnel comme test, pas une vraie gestion de notifications persistantes.
- `app/routes/alertes.py:57`: marquer alerte lue retourne toujours succès sans persistance.

## N. Matrice par rôle

| Fonctionnalité | super_admin | admin | professeur | parent | élève |
|---|---:|---:|---:|---:|---:|
| Connexion | OK | OK | OK | OK | Non prouvé |
| Accueil/dashboard | Partiel | OK | OK | OK | Cassé |
| Écoles | OK | Partiel | Non | Non | Non |
| Utilisateurs | Partiel | Partiel | Non | Non | Non |
| Élèves | Partiel | Partiel | Lecture | Lecture enfants | Cassé |
| Professeurs | Partiel | Partiel | Non | Non | Non |
| Classes | Partiel | Partiel | Lecture | Lecture inattendue | Non |
| Cours | Cassé sans école sélectionnée | Partiel | Partiel | Non | Non |
| Notes | Cassé sans école sélectionnée | Partiel | Partiel | Lecture parent | Menu élève cassé |
| Absences | Cassé sans école sélectionnée | Partiel | Partiel | Lecture parent | Non |
| Paiements | Cassé sans école sélectionnée | Partiel | Non | Lecture parent | Non |
| Bulletins | Partiel | OK | OK | Selon période | Menu élève cassé |
| Emplois du temps | Cassé sans école sélectionnée | OK | OK | Non | Menu élève cassé |
| Rapports | OK | OK | Non | Non | Non |
| Alertes | Cassé sans école sélectionnée | Partiel | Non | Non | Menu élève cassé |

## O. Conclusion

EDUMANAGE est cohérent sur le noyau admin/enseignant/parent pour plusieurs écrans principaux, mais il contient encore beaucoup de restes historiques:

- l'espace élève est visuellement présent mais non implémenté;
- plusieurs templates sont orphelins;
- plusieurs routes ont des doublons historiques;
- la synchronisation offline a deux versions dont une incompatible avec les modèles actuels;
- certains formulaires existent sans route backend;
- certains liens sont valides Flask mais fonctionnellement faux;
- les handlers d'erreur sont eux-mêmes cassés par templates manquants.

Prochaine phase recommandée: nettoyage conservateur du code mort évident et correction des cassures fonctionnelles confirmées, en séparant strictement la future phase sécurité.
