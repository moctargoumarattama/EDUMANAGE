# Audit performances et fluidite - 26 septembre 2026

## Resultat

Les optimisations precedentes sont presentes. Les principaux freins restants
sont le volume du HTML/DOM des listes, la jointure multiple de la fiche complete,
et le traitement de tous les QR avant de rendre la page. La modale reagit au
clic avant la reponse serveur, mais ses reponses concurrentes ne sont pas protegees.

Audit uniquement : aucun fichier applicatif ni donnee metier existante modifie.

## Methode et limites

- Lecture des routes, services, templates, JavaScript et configuration Nginx.
- Client de test Flask, environnement `.venv`, SQLite exclusivement en memoire.
- 50 puis 500 eleves fictifs. Chaque eleve ajoute a un versement ; le premier
  eleve a 60 notes, 30 absences et 9 versements pour tester sa fiche complete.
- Classes fictives par groupes de 25, en plus des deux classes de la fixture.
- QR ecrits dans un repertoire temporaire, sans reutiliser le cache applicatif.
- Trois appels par route : premier appel puis mediane des deux suivants.
- Contextes Flask/SQLAlchemy renouveles a chaque requete.
- Tailles indiquees en octets de reponse non compressee, hors assets CSS/JS.
- Temps locaux cote serveur, sans reseau, rendu navigateur ou charge concurrente.
  Ce ne sont pas des mesures de production ni des mesures INP/LCP.
- Pas de navigateur automatise disponible dans cet environnement. La fluidite
  visuelle et les temps d'interaction sur telephone restent a mesurer.

## Mesures principales

| Parcours | Eleves | Serveur chaud (ms) | SQL/appel | Reponse (octets) | Elements HTML |
| --- | ---: | ---: | ---: | ---: | ---: |
| /eleves | 50 | 41,3 | 17 | 550 199 | 2 400 |
| /eleves | 500 | 256,8 | 17 | 3 563 789 | 16 674 |
| /eleves?page=2 | 500 | 381,5 | 17 | 3 572 789 | 16 674 |
| /paiements | 50 | 55,9 | 14 | 885 341 | 4 508 |
| /paiements | 500 | 444,4 | 14 | 6 690 062 | 36 386 |
| /api/eleves/1/fiche | 500 | 32,5 | 17 | 2 561 | - |
| /voir_eleve/1 | 500 | 1 239,1 | 17 | 361 761 | 1 910 |
| /qrcodes_etudiants | 50 | 338,3 | 8 | 299 371 | 1 078 |
| /qrcodes_etudiants | 500 | 2 870,5 | 8 | 1 603 029 | 5 938 |
| /eleves?search=Nom0499 | 500 | 38,8 | 17 | 275 580 | 1 258 |

Toutes ces reponses ont le statut HTTP 200. Le premier appel QR a 500 eleves
a pris 17 448,4 ms : 450 nouvelles images a calculer et 50 deja en cache.
Les temps QR dependent aussi du disque et de la machine de test.
Nginx prevoit gzip (`deploy/nginx/edumanage.conf:61`) : les tailles ci-dessus
ne sont pas les volumes transferes compresses. Gzip ne diminue pas le DOM.

## Constats prioritaires

### P1 - Pagination des eleves sans reduction du DOM

Sources : `app/routes/eleves.py:188`, `app/templates/eleves.html:182`,
`app/templates/eleves.html:233`.

La route execute `.all()` puis `.paginate()`, mais le template parcourt les
groupes construits avec tous les eleves. La page 2 contient encore 500 fiches.
Le debounce a 280 ms est utile, mais chaque recherche traite toujours la liste
complete et peut ouvrir plusieurs accordions.

Correction conseillee : pagination effective par classe ou par eleve ; charger
les details d'une classe a son ouverture ; conserver la recherche globale cote
serveur. Ne pas paginer le DOM seul tout en limitant la recherche aux lignes visibles.

### P1 - Paiements : tous les eleves et les versements dans le HTML initial

Sources : `app/routes/paiements.py:106`, `app/services/paiements_annuels.py:49`,
`app/templates/paiements.html:394`, `app/templates/paiements.html:494`,
`app/templates/paiements.html:544`.

La page charge toutes les inscriptions et leurs paiements, puis genere leurs
details avant la pagination JavaScript. Les versements existent en version
tableau desktop et cartes mobiles dans le meme document. Avec seulement un
versement par nouvel eleve, on atteint deja 6,69 Mo et 36 386 elements HTML.

Correction conseillee : limiter les inscriptions servies, utiliser des agregats
SQL pour les totaux, charger les versements a la demande et reduire la duplication
responsive. Conserver les totaux globaux distincts des totaux de la page affichee.

### P1 - Fiche complete : jointure notes x absences x paiements encore presente

Source : `app/routes/eleves.py:1231`.

La modale est corrigee mais `/voir_eleve/<id>` et `/eleve/<id>` chargent encore
les trois collections par `joinedload` simultane avant le filtrage annuel Python.
60 notes x 30 absences x 9 paiements donnent 16 200 lignes intermediaires pour un
seul eleve. La fiche complete prend environ 1,24 s contre 33 ms pour la modale.
Le nombre de requetes seul ne permet donc pas de detecter ce probleme.

Correction conseillee : requetes separees, filtrees par inscription/annee consultee,
en preservant explicitement la consultation des archives et les droits existants.

### P1 - QR : cache utile, mais traitement integral a chaque ouverture

Sources : `app/routes/qrcode.py:156`, `app/routes/qrcode.py:168`,
`app/templates/qrcodes_etudiants.html:224`.

Les PNG existants ne sont plus recalcules, mais toutes les images de l'annee sont
encore lues et encodees en base64 dans le HTML. Le filtre de classe agit ensuite
dans le navigateur. Meme avec le cache rempli, 500 eleves prennent environ 2,87 s.

Correction conseillee : filtrer la classe avant de charger les images, utiliser
des URL d'images cacheables et charger les classes a l'ouverture. Prevoir un
parcours explicite pour imprimer toutes les cartes sans alourdir la consultation.

Attention a l'invalidation : `app/services/__init__.py:77` base le cache sur
`eleve.id` et `eleve.updated_at`, alors que le token depend de l'inscription
annuelle (`app/services/bulletin_verification.py:102`). Une nouvelle inscription
sans modification de l'eleve peut reutiliser un ancien QR. Inclure l'inscription
ou une empreinte de l'URL signee dans la cle avant d'etendre ce cache.

### P1 - Clics rapides : une ancienne reponse peut remplacer la fiche demandee

Source : `app/templates/eleves.html:875`.

Chaque ouverture lance un `fetch` sans annulation ni controle de l'identifiant
de la requete. Scenario reproduit en Node avec les reponses controlees : ouverture
A, ouverture B, reponse B puis reponse A ; la fiche finale affiche A. Cela peut
aussi pre-remplir le formulaire de modification avec l'ancienne fiche.

Correction conseillee : `AbortController`, identifiant de requete courante,
annulation a la fermeture et delai maximal. Restaurer l'indicateur de chargement
apres une erreur. Le signal visuel immediat de la modale est deja present.

### P2 - Recherche QR : filtrage, tri et deplacement du DOM a chaque frappe

Sources : `app/static/js/qrcodes.js:133`, `app/static/js/qrcodes.js:158`,
`app/static/js/qrcodes.js:280`.

L'evenement `input` appelle directement `applyFilters`. Cette fonction parcourt
les cartes, ouvre les classes, appelle `sortStudents` puis `updateGridSize`.
Le tri deplace les elements avec `appendChild`, meme quand le tri n'a pas change.

Correction conseillee : debounce 250-300 ms, tri seulement quand son selecteur
change, taille de grille seulement quand le mode change, donnees de recherche
normalisees une fois. Le gain visuel precis reste a mesurer au navigateur.

### P2 - Fraicheur du cache hors ligne decouplee de son contenu

Sources : `app/static/js/offline-manager.js:84`,
`app/static/js/offline-manager.js:111`, `app/static/js/db.js:629`.

Le timestamp est dans localStorage ; le contenu est dans IndexedDB. La deconnexion
purge le contenu sans supprimer le timestamp. Une reconnexion rapide peut donc
ignorer le prechargement et retourner un cache vide jusqu'a expiration des 15 min.
Scenario reproduit : timestamp recent + IndexedDB vide = aucune requete, resultat
null. Deux prechargements simultanes sur cache froid lancent aussi deux requetes,
scenario egalement reproduit.

Correction conseillee : verifier presence et fraicheur du meme enregistrement,
invalider les deux ensemble, partager la promesse de chargement en cours et
prendre en compte le contexte annuel lors d'un changement d'annee active.

## Points secondaires

- Le tableau de bord charge toutes les inscriptions et leurs paiements pour
  calculer les impayes (`app/services/statistiques_annuelles.py:79`). Les acces
  sont groupes, mais le volume traite reste proportionnel a l'etablissement.
  Des agregats SQL sont preferables apres les corrections prioritaires.
- La modale interroge les notes via le calcul de completude puis les recharge
  pour son affichage (`app/routes/eleves.py:625`, `:627`). Mutualisation possible
  en conservant les regles de rattachement des notes anciennes.
- `style.css` pese 194 207 octets ; le logo global 197 685 octets. Plusieurs
  feuilles CSS et polices viennent de CDN. Optimiser ces ressources ensuite,
  sans les presenter comme la cause principale face aux pages de plusieurs Mo.
- Les pages privees restent volontairement non cachees par le service worker.
  Ne pas retablir un cache HTML partage pour donner une impression d'instantaneite.

## Optimisations precedentes constatees

- Video de 29 149 812 octets : `preload="none"`, source en `data-src`, attachement
  a l'ouverture de la modale (`app/templates/base_public.html:153`).
- Envoi SMTP Flask-Mail dans un thread avec contexte d'application.
- Fenetre de 15 minutes pour les prechargements hors ligne, avec les reserves ci-dessus.
- Collections separees pour l'API de fiche eleve ; debounce eleves a 280 ms.
- Cache physique des images QR ; scripts communs differes ; modales mutualisees.

## Verifications

- 23 tests Python passes : `test_performance_phase1.py`, `test_performance_phase2.py`,
  `test_performance_phase3.py`, `test_eleves_inplace_modal.py`.
- `py_compile` passe sur les cinq modules applicatifs examines et le script de mesure.
- `node --check` passe sur les 15 fichiers JS de `app/static/js` et du service worker
  au total, ainsi que sur 11 contenus de scripts inline distincts des pages rendues.
- Trois scenarios JavaScript reproduits avec execution du code existant et doublures
  reseau/DOM : course entre fiches, cache absent avec timestamp recent, double preload.
- Test `tests/test_service_worker.js` en echec : il exige encore v7 et un cache HTML
  prive alors que le service worker utilise v14 et une politique network-only.
  Le test doit etre actualise sans restaurer son ancien comportement de cache.
- Le premier essai avec le Python global etait bloque par l'absence de pandas.
  Les tests ci-dessus ont ensuite passe dans le `.venv` du projet.

Les tests de performance existants verifient notamment la presence de `per_page`
mais pas le nombre effectif de fiches rendues. Ajouter des assertions sur le volume
de reponse et les changements rapides de fiche lors des futures corrections.

## Reproduction locale

Scripts de mesure conserves dans `scratch/` (repertoire deja ignore par Git) :

```powershell
.\.venv\Scripts\python.exe scratch/performance_audit_20260926.py
node scratch/performance_audit_20260926.js
```

Le script Python sonde aussi les rapports : leur cache de processus de 60 secondes
peut conserver les resultats du premier palier apres l'ajout de donnees synthetiques.
Ces mesures ne sont donc pas utilisees pour comparer la montee en charge des rapports.

Ordre conseille : pagination et volume DOM ; fiche complete ; QR par classe ;
protection des clics rapides et cache hors ligne ; mesures navigateur mobile et
reseau reel. Objectif de verification : retour visuel au clic sous 100 ms,
mesure des interactions lentes et absence de reponse obsolete dans les modales.
Ces objectifs ne constituent pas une garantie mesuree sur les telephones des utilisateurs.
