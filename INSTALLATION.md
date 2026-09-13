# Guide d'Installation Neuve et Reproductible — KLASORA

Ce document décrit la procédure standard et reproductible pour déployer KLASORA à partir d'une **base de données vierge (zéro base)** sans aucune intervention manuelle dans la base de données.

---

## 1. Prérequis

- Python 3.11+
- Environnement virtuel configuré (`.venv`)
- Dépendances installées (`pip install -r requirements.txt`)

---

## 2. Procédure d'Installation en 5 Étapes

### Étape 1 : Partir d'une base vierge
Si une ancienne base de données de développement existe, la supprimer :
```powershell
Remove-Item -Force instance\ecole.db
```

### Étape 2 : Appliquer les migrations Alembic
Exécuter la montée en version complète du schéma relationnel :
```powershell
.\.venv\Scripts\python.exe -m flask db upgrade
```
*Vérification du schéma :*
```powershell
.\.venv\Scripts\python.exe -m flask db current
# Doit afficher : 9bc234dfde56 (head)
```

### Étape 3 : Initialiser les données techniques fondamentales
Lancer la commande canonique KLASORA :
```powershell
.\.venv\Scripts\python.exe -m flask init-system
```
*Alias équivalents supportés :*
- `.\.venv\Scripts\python.exe -m flask init-technical-data`
- `.\.venv\Scripts\python.exe scripts/init_db.py`

**Ce que fait cette étape :**
- Crée et ordonne de façon **idempotente** les 13 niveaux scolaires standards nationaux dans le catalogue technique `NiveauScolaire` :
  - **Primaire** : CI, CP, CE1, CE2, CM1, CM2
  - **Collège** : 6e (6E), 5e (5E), 4e (4E), 3e (3E)
  - **Lycée** : 2nde (2NDE), 1ere (1ERE), Terminale (TERMINALE)
- Assure la présence du compte **Super Administrateur** canonique (`moctargoumarattama@gmail.com`). Le mot de passe par défaut ou la variable d'environnement `SUPERADMIN_PASSWORD` est pris en compte.
- **Pureté absolue garantie** : 0 école, 0 classe, 0 élève, 0 professeur, 0 note, 0 paiement fictif ne sont créés.

### Étape 4 : Démarrage du serveur
```powershell
.\.venv\Scripts\python.exe run.py
```
Accéder à l'application via le navigateur : `http://localhost:5000`

---

## 3. Parcours d'Onboarding de la Première École

1. **Connexion Super-Admin ou Inscription École** :
   - Se connecter avec le Super-Admin ou accéder à `/onboarding`.
2. **Création de l'Établissement & de son Administrateur** :
   - Renseigner le nom de l'école, adresse, téléphone et les identifiants de l'administrateur de l'école.
3. **Création de la Première Année Scolaire** :
   - L'assistant guide l'administrateur vers `/annees/ajouter` pour définir la première année scolaire active (ex. `2026-2027`).
4. **Configuration de la Structure Annuelle** :
   - Sélectionner les niveaux effectivement dispensés pour cette année scolaire via `/annees/<id>/structure`.
   - Les niveaux sélectionnés sont enregistrés dans `AnneeNiveauConfig`.
5. **Fin d'Onboarding** :
   - Dès l'enregistrement de la structure, le système valide `setup_complete = True`.
   - L'administrateur a accès immédiat à toutes les fonctionnalités (`/`, `/annees`, `/classes`, `/eleves`, `/professeurs`) sans aucune boucle de redirection.

---

## 4. Règles Architecturales Clés

1. **Source unique de vérité annuelle** :
   - `AnneeNiveauConfig` est la seule table qui détermine les niveaux scolaires actifs pour une année donnée dans un établissement.
   - `EcoleNiveauConfig` n'est pas requis pour le fonctionnement du système.
2. **Création stricte des classes** :
   - Une classe ne peut être créée que si son niveau est préalablement configuré dans `AnneeNiveauConfig` pour l'école et l'année sélectionnées.
   - Toute tentative de création hors structure est strictement refusée.
3. **Isolation multi-établissements** :
   - Chaque école dispose de ses propres années scolaires, structures annuelles, classes et affectations.

---

## 5. Vérification Automatisée

Pour vérifier la validité de l'installation neuve :
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase5a_installation_neuve.py -q --tb=short
```
*Résultat attendu : `14 passed in < 10s`.*

Vérification de la compilation du code :
```powershell
.\.venv\Scripts\python.exe -m compileall app tests -q
```








