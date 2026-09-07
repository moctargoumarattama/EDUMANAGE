# Guide de Configuration Google Cloud - Intégration Gmail par École (KLASORA)

> **Destinataire :** Propriétaire / Administrateur Système de KLASORA  
> **Objectif :** Activer la fonctionnalité *"Se connecter avec Google"* pour permettre à chaque école de connecter son compte Gmail en 1 clic sans aucune configuration technique requise de leur part.

---

## Vue d'Ensemble de l'Architecture

- **Mails Plateforme (KLASORA Système) :** Gérés par le compte administrateur global (`moctargoumarattama@gmail.com`) via les identifiants SMTP serveur. Utilisés uniquement pour la création d'écoles, les comptes administrateurs et les réinitialisations de mot de passe.
- **Mails Écoles (Par Établissement) :** Gérés directement via l'API Gmail REST avec OAuth 2.0. Chaque établissement autorise son adresse Gmail dédiée (`ecoleA@gmail.com`, `ecoleB@gmail.com`). Utilisés pour les bulletins scolaires, notifications d'absences, reçus et alertes parents.
- **Sécurité des Jetons :** Les jetons de rafraîchissement (*refresh tokens*) sont systématiquement chiffrés au repos en base de données via `cryptography.fernet.Fernet` avec la clé `MAIL_TOKEN_ENCRYPTION_KEY`.

---

## Étape 1 : Créer un Projet sur Google Cloud Platform

1. Rendez-vous sur la [Google Cloud Console](https://console.cloud.google.com/).
2. Connectez-vous avec le compte Google administrateur de KLASORA.
3. Cliquez sur le sélecteur de projet en haut à gauche, puis sur **"Nouveau projet"**.
4. Nommez le projet (ex : `KLASORA Education`) et cliquez sur **Créer**.
5. Assurez-vous que ce nouveau projet est bien sélectionné.

---

## Étape 2 : Activer l'API Gmail

1. Dans le menu de gauche, rendez-vous dans **API et services** > **Bibliothèque**.
2. Dans la barre de recherche, tapez **Gmail API**.
3. Sélectionnez **Gmail API** et cliquez sur le bouton bleu **Activer**.

---

## Étape 3 : Configurer l'Écran de Consentement OAuth

1. Dans le menu de gauche, cliquez sur **API et services** > **Écran de consentement OAuth**.
2. **Type d'utilisateur :**
   - Sélectionnez **Externe** (afin que n'importe quel compte Google d'école puisse se connecter).
   - Cliquez sur **Créer**.
3. **Informations sur l'application :**
   - **Nom de l'application :** `KLASORA`
   - **Adresse e-mail d'assistance utilisateur :** votre adresse de contact (ex : `moctargoumarattama@gmail.com`).
   - **Logo de l'application :** importer le logo de KLASORA si souhaité.
   - **Coordonnées du développeur :** votre adresse e-mail.
   - Cliquez sur **Enregistrer et continuer**.
4. **Champs d'application (Scopes) :**
   - Cliquez sur **Ajouter ou supprimer des champs d'application**.
   - Ajoutez le scope minimal d'envoi Gmail :
     - `https://www.googleapis.com/auth/gmail.send` (Envoyer des e-mails en votre nom)
     - `openid`
     - `.../auth/userinfo.email`
   - Cliquez sur **Mettre à jour**, puis sur **Enregistrer et continuer**.
5. **Utilisateurs tests (Pendant le développement) :**
   - En mode *Test*, ajoutez les adresses Gmail des écoles qui testeront la connexion.
   - Cliquez sur **Enregistrer et continuer**.

---

## Étape 4 : Créer les Identifiants OAuth 2.0 (Client Web)

1. Rendez-vous dans **API et services** > **Identifiants**.
2. En haut de la page, cliquez sur **+ Créer des identifiants** > **ID client OAuth**.
3. **Type d'application :** Sélectionnez **Application Web**.
4. **Nom :** `KLASORA Web Client`.
5. **Origines JavaScript autorisées :**
   - En local : `http://localhost:5000` ou `http://127.0.0.1:5000`
   - En production : `https://votre-domaine-klasora.com`
6. **URI de redirection autorisés (TRÈS IMPORTANT) :**
   - En local : `http://localhost:5000/google/mail/callback`
   - En production : `https://votre-domaine-klasora.com/google/mail/callback`
7. Cliquez sur **Créer**.
8. Une boîte de dialogue affiche votre **ID client** et votre **Code secret du client**.

---

## Étape 5 : Configurer le Fichier `.env` sur le Serveur

Renseignez les variables suivantes dans le fichier `.env` de votre serveur KLASORA :

```env
# Identifiants Google Cloud Console
GOOGLE_CLIENT_ID=votre_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=votre_client_secret
GOOGLE_REDIRECT_URI=http://localhost:5000/google/mail/callback

# Clé de chiffrement des tokens au repos (Fernet 32 octets base64)
MAIL_TOKEN_ENCRYPTION_KEY=
```

> **Astuce pour générer une clé de chiffrement dédiée :**  
> Exécutez dans votre terminal Python :
> ```bash
> python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```
> Copiez la valeur obtenue dans `MAIL_TOKEN_ENCRYPTION_KEY`. Si cette variable n'est pas renseignée en local, KLASORA dérivera automatiquement une clé sécurisée à partir de `SECRET_KEY`.

---

## Étape 6 : Passage en Production (Vérification Google)

En mode développement (*Test*), seuls les comptes ajoutés comme *utilisateurs tests* peuvent se connecter.  
Pour ouvrir la connexion à toutes les écoles publiques sans avertissement Google :
1. Dans l'écran de consentement OAuth, cliquez sur **Publier l'application**.
2. Soumettez la demande de vérification Google :
   - Le scope `https://www.googleapis.com/auth/gmail.send` est considéré comme "sensible" (*sensitive scope*).
   - Renseignez l'URL de votre politique de confidentialité et expliquez que le scope est utilisé uniquement pour l'envoi automatisé des bulletins scolaires et relevés de notes de l'établissement.

---

## Expérience Côté École

Une fois le serveur configuré :
1. L'administrateur de l'école se rend dans **Paramètres** > **E-mail de l'établissement**.
2. Il clique sur le bouton **"Se connecter avec Google"**.
3. Il sélectionne son compte Gmail et autorise l'envoi.
4. Il est redirigé vers KLASORA avec le statut **Gmail connecté ✅**.
5. Il peut cliquer sur **"Envoyer un e-mail de test"** pour valider immédiatement la réception.

