# 🎓 EDUMANAGE

### Plateforme de gestion scolaire intelligente

## 🧠 Présentation

**EDUMANAGE** est une application web complète de gestion scolaire permettant de centraliser et automatiser les opérations d’un établissement éducatif.

Elle permet de gérer :

* les élèves
* les enseignants
* les cours
* les notes
* les paiements
* les absences
* les bulletins

Le système est conçu avec une architecture **multi-écoles**, permettant à plusieurs établissements d’utiliser la même plateforme en toute sécurité.

---

## 🚀 Fonctionnalités principales

### 🔐 Authentification & rôles

* Connexion sécurisée avec limitation des tentatives (anti-bruteforce)
* Gestion des rôles :

  * Admin
  * Super Admin
  * Enseignant / Professeur
  * Parent
* Sessions sécurisées

---

### 🏫 Gestion multi-écoles

* Isolation des données par école
* Sécurité avancée par filtrage (`ecole_id`)
* Super admin avec gestion globale

---

### 👨‍🎓 Gestion des élèves

* Ajout / modification / suppression
* Attribution à une classe
* Association à un parent
* Inscription automatique aux cours
* Génération de code parent + QR code
* Notifications email

---

### 👨‍🏫 Gestion des professeurs

* Création de comptes enseignants
* Attribution aux classes et cours
* Génération automatique de code d’accès
* Notification email

---

### 📚 Gestion des cours

* Création et organisation par classe
* Attribution à un professeur
* Gestion des coefficients
* Suivi des activités

---

### 📝 Gestion des notes

* Ajout de notes par enseignant
* Calcul automatique des moyennes
* Filtrage par année scolaire
* Accès parent sécurisé
* Notifications automatiques aux parents

---

### 💰 Gestion des paiements

* Suivi des frais scolaires
* Paiements mensuels
* Statistiques (complet / partiel / impayé)
* Tableau de bord financier

---

### 📊 Export & reporting

* Export PDF (relevé de notes)
* Export Excel (élèves, notes)
* Génération de bulletins
* Rapports détaillés

---

### 📡 API & fonctionnalités avancées

* API JSON pour classes et élèves
* Limitation des requêtes (Flask-Limiter)
* Journalisation des actions (logs)
* Sécurité renforcée multi-écoles 

---

## ⚙️ Technologies utilisées

* **Backend** : Python (Flask)
* **ORM** : SQLAlchemy
* **Authentification** : Flask-Login
* **Sécurité** : Flask-Limiter, Bcrypt
* **Base de données** : SQLite
* **Export** : Pandas, ReportLab
* **Notifications** : Email (SMTP)
* **Autres** : QR Code, JSON API

---

## 🏗️ Architecture

Le projet est structuré en modules :

* `routes.py` → logique principale (auth, élèves, cours, etc.)
* `models.py` → base de données
* `forms.py` → formulaires
* `middleware` → sécurité multi-écoles
* `utils` → fonctions auxiliaires
* `notifications` → email

---

## 🚀 Installation

```bash
git clone https://github.com/moctargoumarattama/EDUMANAGE.git
cd EDUMANAGE
```

Créer un environnement virtuel :

```bash
python -m venv .venv
.venv\Scripts\activate
```

Installer les dépendances :

```bash
pip install -r requirements.txt
```

Configurer les variables d’environnement :

```env
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
```

Lancer l’application :

```bash
python run.py
```

---

## 🔐 Accès

* Les comptes doivent être créés par un administrateur
* Les parents accèdent uniquement aux données de leurs enfants
* Les enseignants accèdent uniquement à leurs classes
* Les données sont isolées par école

---

## 🎯 Objectif du projet

Ce projet a été réalisé dans un cadre pédagogique avec pour objectifs :

* concevoir une application web complète
* maîtriser Flask et SQLAlchemy
* implémenter une architecture multi-utilisateurs
* gérer la sécurité et la séparation des données

---

## 💡 Améliorations futures

* Application mobile (Flutter)
* Tableau de bord analytics avancé
* Notifications en temps réel
* API publique
* Hébergement cloud scalable

---

## 👨‍💻 Auteur

Projet réalisé par **Moctar Goumar Attama**

---

## 📌 Remarque

Ce projet est une solution éducative avancée pouvant évoluer vers une plateforme SaaS professionnelle.
