# Audit global de Klasora : cohérence, couverture fonctionnelle et adaptation au Niger

Date : **9 octobre 2026**. Code inspecté : commit `583269f9`, arbre Git propre au début de l'audit.

## Avis général

**Klasora couvre une partie importante de la gestion quotidienne d'une école, mais ne peut pas encore être présenté comme une gestion scolaire complète, autonome hors ligne et adaptée à tous les établissements du Niger.** Les principales réserves concernent les règles différentes entre saisie et synchronisation, les documents administratifs, la conservation des saisies, les pointages et les communications.

Ce rapport distingue les bugs confirmés, les limites fonctionnelles observées et les points dépendant du déploiement ou des règles de l'établissement. Les douze défauts corrigés dans les lots A/B/C du 9 octobre ne sont pas repris comme défauts encore ouverts. Certains chemins alternatifs échappent cependant aux nouvelles protections.

**Aucun code applicatif ni aucune donnée réelle n'a été modifié.** Seuls ce rapport et des scripts de reproduction dans `scratch/` ont été ajoutés. Aucun envoi WhatsApp/email, démarrage de l'application principale, restauration ou déploiement n'a été effectué.

## Contexte nigérien vérifié

- Le rapport INS sur les TIC 2025, publié en 2026, indique que **39,5 % des personnes de 15 ans et plus** ont utilisé Internet au cours des trois mois précédant l'enquête, avec **63,5 % à Niamey et 17,9 % à Diffa**. Ce ne sont ni des taux de disponibilité du réseau dans les écoles ni une mesure de qualité de connexion. Ils justifient cependant de prévoir des parcours accessibles aux familles sans accès Internet régulier. [Rapport officiel INS, p. 20–21](https://stat-niger.org/wp-content/uploads/rapport_enquete/RAPPORT_FINAL_ENQUETE_TIC_2025_Validation_09_04_2026.pdf).
- Le ministère distingue notamment préscolaire, primaire, secondaire et éducation non formelle à la rentrée 2026–2027. Un catalogue généraliste primaire/collège/lycée ne couvre donc pas à lui seul tout ce périmètre. [Message ministériel de rentrée](https://info.education.gouv.ne/2026/09/30/message-de-madame-la-ministre-de-leducation-nationale-de-lalphabetisation-et-de-la-promotion-des-langues-a-loccasion-de-la-rentree-scolaire-2026-2027/).
- L'enseignement bilingue franco-arabe fait l'objet d'actions nationales spécifiques en 2026 : il doit être considéré comme un cas d'usage réel. [Communication officielle du gouvernement, 24 mars 2026](https://www.gouv.ne/index.php/actualite-des-ministeres/1094-lancement-officiel-de-la-formation-des-enseignants-franco-arabe).
- La HAPDP publie une version consolidée d'avril 2026 de la loi n° 2022-59. Elle traite notamment des formalités, de la conservation et des transferts internationaux de données. La présence d'une politique de confidentialité ne démontre pas l'accomplissement des formalités. [Texte consolidé HAPDP, articles 30–31, 38, 62–63 et 84](https://www.hapdp.ne/uploads/files/Loi-2022-59-consolidee.pdf). Le document précise qu'il est informatif et ne remplace pas le Journal officiel.

**Limite de la vérification locale :** aucun texte officiel établissant les périodes, coefficients et barèmes applicables à chaque cycle et filière pour 2026–2027 n'a été obtenu. Ce rapport ne prétend donc pas que tous les établissements nigériens doivent utiliser trois trimestres, ni que les coefficients présents seraient réglementairement faux.

## Périmètre parcouru

| Domaine | Éléments examinés | Évaluation |
| --- | --- | --- |
| Comptes, rôles, multi-écoles | Modèles, autorisations, middleware, corrections A, secrets dans les notifications | Protections renforcées ; passerelle et secrets à traiter |
| Élèves, parents, matricules | Formulaires, création/import, relations annuelles, certificats, QR | Identité interne structurée ; documents et accès sans téléphone incomplets |
| Classes et cours | Niveaux, sections/séries, affectations, édition, archives | Cas de groupes multiples et mutations de cours incohérents |
| Notes et bulletins | Validateurs, compositions, complétude, publication, périodes, délibérations | Parcours normal renforcé ; synchronisation et exceptions pédagogiques problématiques |
| Présences et emplois du temps | Appel, absences, retards, créneaux, conflits | Contrôles de conflits existants ; réaffectations et calendrier incomplets |
| Finances et reçus | Scolarité, remises, échéanciers, encaissements, reçus, statistiques | Encaissement présent ; idempotence, montants et indicateurs à fiabiliser |
| Personnel et paie | Pointages, validations, archives, heures, versements | Paie corrigée récemment ; données de pointage encore fragiles |
| Communications | Serveur Node, routes Flask, file WhatsApp, email Google/SMTP | Configuration et exploitation de la file incomplètes |
| Hors ligne et mobile | Service worker, IndexedDB, formulaires, cache, synchronisation | Assistance partielle sur pages ouvertes ; autonomie absente |
| Exploitation | Sauvegardes SQLite/PostgreSQL/JSON, restauration, CLI, service systemd, configuration | Plusieurs protections réelles ; reprise complète et planification à établir |
| Rapports, assistant et support | Statistiques annuelles, exports, réponses d'aide, dépendances IA, support | Indicateurs divergents et promesses à aligner sur les capacités |

Il s'agit d'un audit transversal du code et de scénarios ciblés, pas d'une certification ligne par ligne ou d'un test complet de production. Les navigateurs réels, imprimantes, téléphones modestes, charge PostgreSQL et infrastructure nigérienne n'ont pas été mesurés.

## Défauts et incohérences prioritaires

### 1. Haute — La synchronisation accepte des notes interdites dans la saisie normale

**Sources :** `app/routes/sync.py:144`, `:159`, `:162`, `:344` ; contrôles normaux dans `app/services/notes_annuelles.py:585`, `:664`, `:680`.

Reproductions sur SQLite en mémoire : une inscription `annulee` reçoit une note avec le résultat `synced` ; une période `Trimestre 99`, un type inconnu et une date de 1999 sont acceptés sur l'année 2026–2027. Deux compositions de 8 et 18, même élève/matière/semestre mais dates différentes, sont toutes deux acceptées. Le calcul prend ensuite la première composition (`app/services/evaluations.py:88`) : **8 ou 18 selon l'ordre des notes**.

**À faire :** partager les validations métier entre saisie normale et synchronisation, avec une unicité effective des compositions. Le correctif du lot C ne protège pas cette voie alternative.

### 2. Haute — Vider sa file locale efface aussi celle des autres comptes

**Sources :** `app/static/js/db.js:430`, `:436`, `:450` ; `offline-manager.js:778` ; `sync-hors-ligne.js:178`, `:248`.

L'écran filtre les opérations par compte, mais la suppression exécute `store.clear()` sur toute la file. A conserve des notes non synchronisées ; B se connecte sur le même appareil et vide sa file visible : les notes cachées de A disparaissent aussi. Reproduction JavaScript exécutée avec deux propriétaires distincts.

**À faire :** supprimer uniquement les opérations du compte et de l'école sélectionnés, et afficher leur nombre exact avant suppression. Priorité forte pour un téléphone ou ordinateur partagé.

### 3. Haute — La passerelle WhatsApp ne contrôle pas l'identité de l'appelant

**Sources :** `whatsapp-server/server.js:15`, `:164`, `:173`, `:187`, `:213`, `:242`.

Les routes de statut, QR, envoi et déconnexion acceptent directement un identifiant d'école, sans authentification. `app.listen(PORT)` ne limite pas l'écoute à `127.0.0.1`, contrairement au message affiché. Un client ayant accès au port peut agir sur les sessions des écoles connues.

**À faire :** authentifier les appels serveur à serveur, vérifier leur périmètre et limiter explicitement l'écoute/réseau. L'exposition effective sur Internet dépend du pare-feu et du déploiement ; aucun accès extérieur n'a été tenté.

### 4. Haute — Des mots de passe réutilisables restent en clair dans les messages

**Sources :** `app/routes/utilisateurs.py:48`, `:58` ; `app/routes/professeurs.py:77`, `:87` ; `app/services/whatsapp_queue.py:61`, `:68`, `:170` ; `app/models.py:282`.

Les messages de création professeur et de réinitialisation incorporent le mot de passe. La file stocke le texte intégral et conserve ce contenu après envoi. La notification de réinitialisation n'a pas d'expiration définie. Les mots de passe des comptes restent hachés, mais une copie du secret existe ainsi dans la base et ses sauvegardes globales.

**À faire :** utiliser des liens ou codes d'activation temporaires, imposer le changement du secret et limiter la conservation des messages sensibles.

### 5. Haute — Un pointage validé et archivé reste modifiable

**Sources :** `app/routes/pointage_personnel.py:299`, `:349`, `:374`.

La sauvegarde d'une ligne vérifie qu'une année existe, sans refuser une année archivée ni un pointage validé. Reproduction HTTP : pointage archivé et validé, présent huit heures → absent injustifié zéro heure, **HTTP 200** ; `valide=True` et l'ancienne validation sont conservés.

**À faire :** verrouiller les archives et les pointages validés ; organiser une réouverture motivée et tracée. Les gardes ajoutées à la paie ne protègent pas ses données sources.

### 6. Haute — Un certificat de scolarité peut être émis sans inscription valable

**Sources :** `app/routes/certificats.py:122`, `:130`, `:137`, `:190` ; `app/templates/certificat_print.html:326`.

La génération ne vérifie pas le statut de l'inscription. En son absence, elle prend la dernière inscription connue, puis accepte même l'absence totale d'inscription. Reproductions HTTP : inscription annulée → certificat créé ; aucune inscription → certificat créé avec classe `Non assigné`. Le texte certifie pourtant une scolarité régulière et assidue.

**À faire :** demander explicitement l'inscription concernée et appliquer les conditions propres au document. Un certificat historique doit mentionner sa période réelle sans devenir une attestation de scolarité actuelle.

### 7. Haute — Le certificat de radiation ne correspond pas à une sortie enregistrée

**Sources :** `app/routes/certificats.py:153`, `:190`, `:213` ; `app/templates/certificat_print.html:336`, `:339`.

La génération enregistre seulement le certificat. Reproduction : certificat de transfert créé, mais inscription toujours `inscrit`, `date_depart=None`. Le papier affirme que l'élève est définitivement rayé des effectifs.

**À faire :** produire ce document depuis une sortie métier enregistrée, ou effectuer les deux opérations dans une transaction cohérente. Une simple impression ne doit pas annoncer une radiation inexistante.

### 8. Haute — Les certificats émis ne figent pas l'identité de l'élève

**Sources :** `app/models.py:1847`, `:1920` ; `app/routes/certificats.py:235`, `:270` ; `app/templates/certificat_print.html:317`.

La classe et l'année sont copiées à l'émission, mais le nom, la naissance et le matricule sont relus sur l'élève courant. Reproduction : même référence de certificat, identité `INITIAL Eleve` puis `MODIFIE Eleve` après modification de la fiche. Une réimpression ou vérification peut donc différer du papier initial.

**À faire :** conserver un état du document émis, avec une procédure explicite de rectification et d'annulation. La génération ne dispose pas actuellement d'un statut de révocation.

### 9. Haute — Les périodes sont libres à la création, mais limitées à deux semestres ensuite

**Sources :** `app/routes/bulletins.py:1346` ; `app/services/notes_annuelles.py:584`, `:1012` ; `app/services/activation_annee.py:62`.

La création d'une période appelée `Trimestre 1` est permise, puis le validateur des notes répond « Seuls les semestres sont acceptés ». L'activation exige S1/S2. Les dates sont personnalisables ; le modèle pédagogique reste limité à deux semestres.

**À faire :** soit assumer et afficher cette restriction, soit rendre les périodes réellement configurables dans tous les calculs et publications. L'incohérence est confirmée indépendamment du régime réglementaire de l'école.

### 10. Haute — Série de lycée et division de classe sont confondues

**Sources :** `app/services/niveaux.py:181`, `:195`, `:230` ; `app/services/pedagogie_standard.py:121`.

Une seule lettre est acceptée pour la section/série, et une deuxième classe du même niveau avec cette lettre est interdite. `D1` et `A2` sont refusés. Deux groupes de Terminale D ne peuvent donc pas être représentés proprement dans le parcours canonique. Le catalogue pédagogique reconnaît pourtant A1/A2.

**À faire :** distinguer niveau, série/filière et division, avec une unicité sur leur combinaison.

### 11. Haute — Modifier un cours peut désynchroniser le planning et les résultats

**Sources :** `app/routes/cours.py:92`, `:311`, `:479`, `:503` ; `app/models.py:1049` ; `app/services/emploi_temps_annuel.py:83` ; `app/services/bulletins_annuels.py:260`.

Réaffecter un cours de A à B change `Cours.professeur_id`, mais laisse les créneaux chez A et ne revalide pas les conflits de B. Changer la classe du cours laisse également ses créneaux et inscriptions de notes rattachés à l'ancienne classe ; le calcul filtré par la classe actuelle du cours peut ensuite exclure des notes du bulletin source.

**À faire :** bloquer les déplacements incompatibles avec un historique, ou proposer une opération métier qui traite toutes les dépendances et dates d'effet. Constat par lecture croisée, sans reproduction HTTP de ces mutations.

## Hors ligne : capacités réelles et limites

### 12. Haute — Les principaux écrans de saisie ne sont pas branchés à la file hors ligne

**Sources :** `app/static/js/offline-forms.js:209` ; `app/templates/faire_appel.html:34`, `:71`, `:129` ; `app/routes/absences.py:309` ; `app/templates/saisie_notes_classe.html:50` ; `app/templates/notes.html:1697`.

Le branchement des absences attend un ancien formulaire avec action et élève individuel. L'appel réel utilise des cases par inscription, sans action explicite ; `/presence` est exclu par le filtre d'URL. Reproduction JS : aucun gestionnaire hors ligne attaché à l'appel. La saisie des notes par classe utilise également un envoi direct non couvert. L'ajout unitaire de note professeur est branché.

**À faire :** connecter les parcours effectivement utilisés, avec confirmation locale et état de synchronisation par lot.

### 13. Haute — Wi-Fi connecté ne signifie pas serveur disponible

**Sources :** `app/static/js/offline-forms.js:17`, `:48` ; `app/static/js/offline-manager.js:327`.

La mise en file utilise seulement `!navigator.onLine`. Si le Wi-Fi reste connecté mais que le serveur est inaccessible, la soumission part au réseau sans sauvegarde locale. Test JS : navigateur en ligne, serveur déclaré indisponible par le gestionnaire → zéro opération mise en file.

**À faire :** utiliser l'accessibilité effective du serveur et récupérer les échecs de soumission, avec idempotence pour les demandes dont la réponse a été perdue.

### 14. Moyenne — Le mode installé ne permet pas de rouvrir les fonctions scolaires hors ligne

**Sources :** `app/static/service-worker.js:93`, `:101` ; `app/templates/offline.html:145` ; `tests/test_option_b_offline.py` ; `app/ai_service.py:46`.

Toutes les navigations HTML privées restent réseau obligatoire. Après fermeture ou rechargement pendant une coupure, l'utilisateur retrouve un écran d'attente, sans listes ni formulaires. C'est un choix volontaire actuel, dit « Option B ». Il préserve notamment l'absence de pages privées dans le cache, mais ne fournit pas une application scolaire autonome.

L'IA décrit pourtant un accès sans connexion. **À faire :** aligner les promesses sur le périmètre réel ; choisir explicitement entre assistance limitée et application disposant de parcours locaux sécurisés.

### 15. Moyenne — Une panne externe peut faire échouer tout le précache

**Sources :** `app/static/service-worker.js:36`, `:44`, `:45`, `:48`, `:58` ; `app/templates/base.html:25`, `:528`.

Les ressources locales et les CDN Bootstrap/FontAwesome sont chargés dans un seul `cache.addAll()`. L'échec est absorbé, le nouveau worker s'active et les anciens caches sont supprimés. Le lot est atomique : une ressource inaccessible peut empêcher le précache entier. [Référence du groupe W3C](https://github.com/w3c/ServiceWorker/issues/823).

**À faire :** livrer localement les dépendances indispensables et conserver un cache utilisable tant que la préparation du nouveau a échoué.

### 16. Moyenne — Le cache de données devient inutilisable après 24 heures

**Sources :** `app/static/js/offline-manager.js:68`, `:181`, `:202`, `:259`, `:304` ; `app/static/js/db.js:562`, `:578`.

Le commentaire promet une lecture même après expiration hors ligne, mais le lecteur retourne déjà `null` et l'initialisation purge les données expirées. Une coupure de plusieurs jours dépasse donc ce mécanisme. Ce TTL ne supprime pas les opérations en attente.

**À faire :** définir une politique distincte de fraîcheur et de conservation. Aucun export de secours de la file ni demande de stockage persistant du navigateur n'a été identifié ; les saisies locales restent dépendantes de l'appareil.

### Matrice des parcours hors ligne

| Opération | État observé |
| --- | --- |
| Se connecter sans serveur | Non prévu |
| Ouvrir/recharger élèves, bulletins, paiements | Pas de navigation métier hors ligne |
| Ajouter une note unitaire sur page déjà ouverte | Mise en file prévue ; détection et validation à corriger |
| Saisir les notes d'une classe | Parcours non branché à la file |
| Faire l'appel courant | Parcours non branché à la file |
| Inscrire/modifier un élève, gérer cours et administration | Connexion requise explicitement |
| Encaisser et produire un reçu sans serveur | Non couvert par le parcours hors ligne actuel |
| Synchroniser au retour | Présent ; règles scolaires et purge locale défectueuses |
| Faire fonctionner WhatsApp sans accès à son réseau | Non ; une file durable doit attendre le retour |

Un **serveur local encore accessible sur le réseau de l'école** peut continuer à traiter les opérations ordinaires même sans Internet extérieur. Ce scénario est distinct du navigateur sans accès à Flask. Il n'est pas fourni sous forme de déploiement local prêt à installer et synchroniser avec un serveur distant.

## Finances, personnel et communications

### 17. Moyenne — La scolarité gratuite est refusée par le formulaire élève

**Sources :** `app/forms.py:95` ; `app/routes/eleves.py:546`.

Le champ autorise numériquement `min=0`, mais utilise `DataRequired`, qui rejette zéro. Reproduction avec le vrai champ : `0` → « This field is required ». Une exonération complète ne peut pas être saisie proprement par ce formulaire, contrairement au calcul financier qui sait gérer zéro.

**À faire :** distinguer champ absent et montant nul, puis vérifier les parcours de création/import/modification.

### 18. Moyenne — Le tableau de bord peut annoncer une dette soldée

**Sources :** `app/services/statistiques_annuelles.py:84`, `:125` ; `app/services/paiements_annuels.py:72`, `:138`.

Le compteur utilise les frais bruts sans la remise ni les frais d'inscription. Reproduction : scolarité 100 000, remise 20 000, inscription 5 000, versements 85 000 → service financier `complet`, reste zéro ; tableau de bord `paiements_attente=1`.

Le total d'élèves compte aussi toutes les inscriptions annuelles sans distinguer les annulations et départs. **À faire :** utiliser les mêmes définitions financières et afficher séparément effectif actuel, inscriptions cumulées et sortants. Une inscription annulée ne signifie pas automatiquement absence de toute dette : cette règle doit rester explicite.

### 19. Moyenne — Une mensualité devient exigible avant la rentrée

**Source :** `app/services/paiements_annuels.py:489`, `:499`.

Le calcul pose d'abord zéro mois avant le début de l'année, puis force un minimum de un. Reproduction : frais 90 000 sur neuf mois, date antérieure à la rentrée → 10 000 exigibles et retard possible.

**À faire :** conserver zéro, sauf échéance d'avance explicitement configurée. Les mois de facturation restent par ailleurs octobre à juin, avec juillet optionnel (`:27`) ; un calendrier financier propre à l'école manque.

### 20. Haute — Une demande répétée peut compter deux fois un versement

**Sources :** `app/services/paiements_annuels.py:239`, `:245`, `:258` ; `app/routes/paie_personnel.py:512`, `:550`.

Deux encaissements identiques sans référence produisent deux paiements. Deux POST de paie de 20 000 avec la même référence produisent un cumul de 40 000, tant que le solde le permet. Reproductions exécutées. Une référence facultative ou un bouton désactivé ne protège pas d'une réponse réseau perdue puis rejouée.

**À faire :** attribuer une clé persistante à chaque opération et renvoyer le résultat existant lorsqu'elle est rejouée. Pour la paie, cette limite était déjà identifiée après le lot B ; elle demeure ouverte.

### 21. Moyenne — Le montant stocké peut différer du montant affiché sur le reçu

**Sources :** `app/services/paiements_annuels.py:188`, `:239` ; `app/models.py:788` ; `app/services/payment_receipts.py:47`.

La saisie accepte des fractions alors que le reçu arrondit sans décimale. Reproduction : paiement `0.49` enregistré ; reçu `0 FCFA`. Le problème prouvé est la divergence entre transaction et justificatif, sans supposer une perte financière par arrondi binaire.

**À faire :** définir une unité et une précision monétaires uniques pour validation, stockage, affichage, totaux et exports.

### 22. Moyenne — Des horaires invalides deviennent huit heures travaillées

**Source :** `app/routes/pointage_personnel.py:19`.

La durée de secours est huit heures pour une saisie inversée, identique ou invalide. Reproductions : `16:00→08:00`, `08:00→08:00`, `99:99→08:00` → huit heures chacune.

**À faire :** refuser la saisie et définir le traitement des horaires de nuit. Une erreur de saisie ne doit pas créer des heures de paie.

### 23. Moyenne — WhatsApp peut être affiché connecté alors que les notifications restent désactivées

**Sources :** `app/models.py:198` ; `app/routes/whatsapp.py:41` ; `app/templates/admin/whatsapp.html:24` ; `app/routes/paiements.py:79` ; `app/routes/absences.py:67`.

Le champ `whatsapp_enabled` vaut faux à la création. L'appairage lit le statut de la passerelle, mais ne l'active pas. Aucune écriture applicative de ce champ n'a été trouvée dans les routes/formulaires. Les déclencheurs quittent alors silencieusement sans notification.

**À faire :** fournir un parcours d'activation explicite et distinguer appairage, autorisation d'envoi et fonctionnement du traitement de la file.

### 24. Moyenne — Le statut et l'envoi n'utilisent pas la même configuration de passerelle

**Sources :** `app/routes/whatsapp.py:11` ; `app/services/whatsapp_queue.py:16`, `:84`.

Statut, QR et déconnexion utilisent `WHATSAPP_GATEWAY_BASE_URL`. L'envoi impose `127.0.0.1:3001`. Une passerelle déplacée ou conteneurisée peut donc apparaître connectée pendant que les messages partent vers une autre adresse.

**À faire :** partager une configuration et un client uniques pour toutes les opérations.

### 25. Moyenne — Les coupures peuvent épuiser définitivement les tentatives d'envoi

**Sources :** `app/services/whatsapp_queue.py:127`, `:142`, `:156`, `:172` ; `app/cli.py:102`.

Chaque passage du traitement consomme une tentative, y compris si la passerelle est simplement arrêtée ou non appairée. Pas de date de prochaine tentative ni de délai progressif. Reproduction : trois indisponibilités → `echec` ; au retour du service, zéro message repris automatiquement.

Le dépôt fournit une commande de traitement, sans ordonnanceur WhatsApp livré. Sa présence réelle en production reste à vérifier. **À faire :** distinguer indisponibilité temporaire et rejet définitif, planifier les reprises et rendre les échecs exploitables par la direction.

## Sauvegardes, assiduité et règles pédagogiques restantes

### 26. Haute — Une fonction appelée « sauvegarde complète » omet des données essentielles

**Sources :** `app/admin/routes.py:282`, `:287` ; `app/admin/scripts.py:745`, `:768`.

L'ancien export JSON « complet » ne prend que les écoles, années et six tables : classes, élèves, professeurs, notes, absences et utilisateurs. Il omet notamment paiements, inscriptions, cours, bulletins, périodes, pointages et paie.

**À faire :** retirer l'appellation complète ou remplacer ce parcours par une sauvegarde restaurable et exhaustive. Les sauvegardes globales SQLite/PostgreSQL et la sauvegarde par école, distinctes, couvrent beaucoup plus de données : elles ne doivent pas être confondues avec cet export.

### 27. Moyenne — La reprise complète ne couvre pas les fichiers de l'application

**Sources :** `app/admin/scripts.py:23`, `:194`, `:287`, `:946`, `:995`, `:1023` ; `app/models.py:194` ; `whatsapp-server/server.js:13`, `:78`.

Les sauvegardes conservent les chemins de logo, signature et cachet, sans embarquer leurs fichiers. Les identifiants de session WhatsApp sont dans un répertoire séparé. La sauvegarde par école ne prend pas non plus `MessageQueue`.

**À faire :** définir un ensemble de reprise couvrant base, fichiers et configurations nécessaires, protégé hors de la machine principale. Une sauvegarde sur le même disque ne couvre pas sa perte. Aucun exercice de restauration sur une machine vierge ni dispositif externe réel n'a été vérifié.

### 28. Moyenne — L'absence individuelle peut concerner un élève qui n'est plus scolarisé

**Sources :** `app/services/absences_annuelles.py:89`, `:300` ; `app/routes/absences.py:118`, `:592`.

Le validateur individuel ne contrôle ni le statut scolarisé ni la date de départ. Reproduction du vrai validateur : inscription annulée, année/date valides, sans cours → aucune erreur. L'appel collectif filtre pourtant les inscrits. Les parcours appliquent donc des règles différentes ; une fausse absence peut également déclencher une notification.

**À faire :** appliquer une règle commune, tenant compte de la date de scolarisation pour les corrections historiques.

### 29. Moyenne — La pondération des contrôles saisie à l'écran n'a pas l'effet attendu

**Sources :** `app/templates/notes.html:161` ; `app/templates/saisie_notes_classe.html:112` ; `app/services/notes_annuelles.py:350`, `:381`.

Les coefficients sont enregistrés et utilisés par les statistiques, mais volontairement ignorés par la moyenne des contrôles du bulletin. Reproduction : 0 coefficient 1 et 20 coefficient 3 → moyenne des contrôles 10, statistiques 15.

**À faire :** choisir et documenter la règle de l'établissement, puis aligner champ, libellés, statistiques et bulletin. Ce constat ne signifie pas que la moyenne simple serait contraire aux règles nigériennes.

### 30. Moyenne — Dispenses et exceptions individuelles ne sont pas modélisées

**Sources :** `app/services/evaluations.py:38`, `:255`, `:651` ; `app/services/bulletins_annuels.py:241`.

Toutes les matières de la classe sont attendues pour chaque élève. Une dispense d'EPS ou une matière optionnelle sans note laisse le dossier incomplet et peut bloquer la publication collective. Le commentaire mentionne la dispense, mais aucun statut distinct ne la représente.

**À faire :** distinguer note manquante, absence, dispense, non concerné et zéro, avec règles de calcul et justificatifs. Ne pas contourner cette lacune en saisissant une note inventée.

### 31. Moyenne — Le suivi des retards des élèves est annoncé mais incomplet

**Sources :** `app/services/semestres.py:125` ; `app/models.py:859` ; `app/templates/landing.html:90` ; `app/templates/voir_eleve.html:573`.

Le compteur retourne toujours zéro lorsque le calendrier est configuré, sans modèle de retard ou durée pour les élèves. Aucun affichage PDF de ce compteur n'a été identifié : il ne faut pas conclure qu'un faux zéro serait actuellement imprimé. Le retard du personnel dispose, lui, d'un modèle distinct.

**À faire :** implémenter la saisie et les règles de cumul ou retirer la promesse du périmètre disponible.

## Fonctions à compléter selon les établissements ciblés au Niger

Ces limites ne sont pas toutes des bugs : elles définissent ce que le produit peut effectivement prendre en charge.

| Besoin | État observé et conséquence | Source principale |
| --- | --- | --- |
| Préscolaire, alphabétisation, technique/professionnel | Catalogue standard limité à CI–CM2, collège et lycée ; parcours dédiés absents | `app/services/niveaux.py:11`, `:114` |
| Enseignement franco-arabe et langues | Matières personnalisables, mais pas de référentiel franco-arabe identifié ni interface multilingue ; les pages principales sont en français | `app/services/pedagogie_standard.py:23` ; `app/templates/base.html:2` |
| Programme et coefficients validés localement | Catalogue présenté comme officiel sans source normative/version/pays visible ; personnalisation possible | `app/services/pedagogie_standard.py:23` |
| Examens nationaux | Notes de composition et décisions internes présentes ; pas de parcours de candidature, centre, numéro de candidat, session et résultat officiel identifié pour CFEPD/CEPE-FA/BEPC/BAC | `app/models.py` ; `app/services/passage_annee.py:412` |
| Vacances et changements d'emploi du temps en cours d'année | Planning hebdomadaire sans exceptions ni dates d'effet ; une date dans l'année ne signifie pas un jour enseigné | `app/models.py:1045` ; `app/services/absences_annuelles.py:297` |
| Famille sans téléphone utilisable | Nouveau parent : téléphone obligatoire dans formulaire et route ; pas de parcours d'inscription autonome sans ce contact. L'import ne suit pas toutes les mêmes contraintes | `app/forms.py:101` ; `app/routes/eleves.py:588`, `:639` |
| Plusieurs responsables légaux | Un seul `parent_id` par élève, sans droits distincts de responsables multiples ; lien familial et autorisation de consultation à enrichir si nécessaire | `app/models.py:646` ; `app/authorization.py:14` |
| Dossier administratif local | Pas de champs dédiés identifiés pour référence d'acte de naissance, nationalité et découpage administratif ; le matricule est interne à Klasora, pas une identité nationale | `app/models.py:621` ; `app/services/matricule_service.py` |
| Communications sans smartphone/Internet | WhatsApp et email présents ; aucun canal SMS/USSD identifié. Le registre papier et les impressions restent nécessaires pour les familles non connectées | `app/services/whatsapp_queue.py` ; `app/services/google_mail.py` |
| Paiement Mobile Money | Mode de paiement saisissable ; pas de connecteur opérateur, confirmation de transaction, rapprochement ou traitement des remboursements identifié | `app/forms.py:233` ; `app/services/paiements_annuels.py:239` |
| Comptabilité complète | Suivi de scolarité et paie, mais pas de comptabilité générale, caisse avec clôture/écarts, dépenses et rapprochement bancaire identifiés | `app/models.py` ; `app/routes/paiements.py` |
| Paie réglementaire | Base, taux horaire, primes et retenues présents ; pas de moteur nigérien documenté de cotisations/impôts/congés. Les montants ne sont pas certifiés conformes ; aucun taux légal n'est supposé ici | `app/models.py:1728` ; `app/routes/paie_personnel.py:431` |
| Cantine, transport, internat, bibliothèque, santé | Aucun module dédié identifié ; à développer seulement si ces services font partie du périmètre commercial choisi | `app/models.py` et inventaire des routes |
| Statistiques administratives nationales | Exports génériques Excel/PDF présents ; pas de format validé pour un dépôt auprès des services nigériens identifié | `app/routes/rapports.py:284`, `:322` |

L'absence de ces fonctions ne rend pas tout usage impossible. Elle empêche de promettre une couverture générale sans préciser les cycles, services et règles pris en charge.

## Confidentialité, QR et déploiement : vérifications nécessaires

- **Certificats publics :** le serveur calcule un matricule masqué (`app/routes/certificats.py:272`), mais le template affiche `eleve.matricule` intégralement (`app/templates/verifier_certificat.html:161`). Les protections `no-store`/`noindex` des bulletins ne sont pas ajoutées par cette route de certificat. La vérification publique doit montrer uniquement les éléments nécessaires, selon une politique cohérente.
- **Clé QR par défaut :** `app/config.py:106` fournit une clé de signature constante connue. La validation production vérifie la clé Flask mais pas cette clé dédiée (`:181`). Si l'exploitant ne la remplace pas, la signature ne constitue pas une preuve d'origine secrète. Cela ne permet pas à lui seul de créer des notes ou inscriptions. Vérifier la configuration réelle et prévoir la continuité des documents lors d'une rotation.
- **Portée de l'anti-fraude :** le QR du bulletin pointe sur l'inscription et la période (`app/services/bulletin_verification.py:43`), puis consulte le dossier actuel (`app/routes/bulletins.py:1427`). Il ne scelle pas le contenu exact du papier. Un QR valide ne prouve donc pas, à lui seul, que toutes les notes imprimées sont identiques au document émis.
- **Information des familles et conformité :** la politique existe (`app/templates/politique_confidentialite.html:43`, `:159`), mais ne précise pas les durées concrètes, pays d'hébergement ou destinataires WhatsApp/Google. Aucun suivi dédié des autorisations, des demandes de droits ou des preuves de formalités HAPDP n'a été identifié. Ces éléments peuvent exister hors logiciel : leur absence du dépôt ne prouve pas une infraction. Les transferts et la conservation doivent être examinés selon le [texte consolidé HAPDP](https://www.hapdp.ne/uploads/files/Loi-2022-59-consolidee.pdf).
- **Téléphone sur réseau local :** `http://IP_DU_PC:5007` via `run.py` ne fournit pas normalement un contexte sécurisé pour les service workers. L'exception localhost concerne l'appareil lui-même. Prévoir HTTPS reconnu pour les clients du réseau local. [Spécifications W3C Service Workers](https://www.w3.org/TR/service-workers/), [Secure Contexts](https://www.w3.org/TR/secure-contexts/Overview.html).
- **Démarrage sécurisé :** `run.py:12` démarre le serveur de développement avec debug et écoute réseau. Le service systemd fourni lance Gunicorn mais ne force pas `APP_ENV=production` (`deploy/systemd/edumanage.service:9`). La configuration peut être définie ailleurs ; l'état réel n'a pas été inspecté. Une installation scolaire ne doit pas dépendre d'un réglage implicite.
- **Sauvegardes automatiques :** le middleware ne lance pas de sauvegarde en production (`app/middleware.py:383`) et les CLI existent (`app/cli.py:45`). Aucun timer/cron de sauvegarde livré n'a été identifié ; vérifier la planification et les alertes de l'infrastructure. Une option « activée » dans l'interface ne prouve pas que le cron tourne.
- **Assistant IA :** le modèle local Ollama nécessite un service et un modèle installés (`app/ai_service.py:18`). Les réponses structurées peuvent fonctionner sans lui, mais la génération libre dépend de ce service. Il ne faut pas confondre IA locale sur le serveur et interface navigateur autonome sans serveur.

## Points déjà signalés et incertitudes conservées

- L'ETag de synchronisation est basé sur les effectifs (`app/routes/sync.py:1419`) : une correction sans variation d'effectif peut produire un 304 inapproprié. Déjà décrit au point 11 de l'[audit du 8 octobre](audit_regles_scolaires_2026-10-08.md), il ne s'agit pas d'une nouvelle découverte.
- La comparaison des versions avant mutation de synchronisation reste réalisée en Python, sans verrou ou condition SQL atomique identifiée (`app/routes/sync.py:212`, `:274`, `:550`). Un écrasement concurrent est plausible ; **aucun test concurrent PostgreSQL ne l'a confirmé dans cet audit**.
- Aucun volume de données corrompues dans la base réelle n'a été mesuré. Ce rapport décrit les comportements du code, pas des incidents réels déjà survenus.

## Ordre de traitement recommandé

1. **Fiabiliser les données :** validations communes de synchronisation, purge limitée au propriétaire, idempotence des versements, pointages verrouillés et contrôles horaires.
2. **Sécuriser les preuves et accès :** authentification de la passerelle, activation temporaire des comptes, certificats liés à une inscription/sortie réelle et documents figés.
3. **Choisir le périmètre Niger :** types d'établissements, séries/divisions, calendrier/périodes, dispenses, frais et documents validés avec les écoles concernées.
4. **Choisir l'architecture hors ligne :** autonomie navigateur ou serveur local ; couvrir effectivement appel et notes par classe, puis les encaissements si autorisés, avec résolution des conflits.
5. **Rendre l'exploitation vérifiable :** traitement WhatsApp planifié, reprises après coupure, sauvegarde hors machine et exercice de restauration complète ; puis compléter examens/rapports/modules selon le périmètre retenu.

## Validation effectuée

| Preuve conservée | Vérifications exécutées |
| --- | --- |
| `scratch/audit_niger_documents_20261009.py` et `.json` | Vraie route de certificats avec décorateurs, Flask minimal et SQLite mémoire : scolarité annulée/sans inscription, transfert sans sortie, identité dynamique ; comparaison vrais services dashboard/finance ; vrai champ de frais à zéro |
| `scratch/audit_niger_offline_proofs.py` | Vrai traitement des notes synchronisées : inscription annulée, métadonnées invalides, compositions multiples |
| `scratch/audit_niger_offline_proofs.js` | Sources JS réelles avec DOM/IndexedDB simulés : détection serveur, appel non branché et purge des opérations de deux comptes |
| `scratch/audit_niger_finances_communication.py` | Pointage archivé/validé, horaires invalides, versements répétés, fraction monétaire, exigibilité avant rentrée et tentatives WhatsApp ; transport simulé, aucun message envoyé |
| Fonctions pédagogiques extraites par AST | Validateur des périodes, sections de classes, divergence des coefficients et contrôle d'absence sur inscription annulée |

Les scripts utilisent des données fictives, neutralisent dotenv avant les imports applicatifs et n'appellent pas `create_app()`. Les simulations JS ne remplacent pas un essai sur téléphone réel. La suite complète n'a pas été relancée pour cet audit sans changement applicatif ; les validations récentes des lots A/B/C sont documentées séparément.
