# Audit tactile et navigation mobile - 27 septembre 2026

## Diagnostic

Des problemes sont reproductibles dans l'application : l'assistant flottant
recouvre des liens, le bandeau de bienvenue deplace les cartes et aucun etat
de chargement persistant n'accompagne la navigation. Le telephone et le reseau
peuvent amplifier l'attente, mais ne sont pas les seules explications possibles.

Audit uniquement : aucun fichier applicatif ni donnee metier modifie par cet
audit. Scripts et captures dans scratch/ ; rapport dans audits/.
Des modifications concurrentes de style.css, main.js et base.html sont apparues
pendant l'examen. La verification finale tient compte du retour tactile :active,
de touch-action: manipulation et des URL d'assets v15-touch-perf presents alors.
Ces modifications ne sont pas produites par cet audit.

## Constats prioritaires

### P1 - L'assistant masque de vraies zones de navigation

Source : app/templates/components/assistant_widget.html:286 et :1203.

Le bouton Assistant IA est fixe au-dessus du contenu, avec z-index: 1055.
Sur les ecrans simules de 360 et 390 px, elementFromPoint retourne ce bouton
au centre de certaines zones de la carte Classes, avant disparition du bandeau.
Apres disparition du bandeau, la carte Rapports occupe cette zone et subit le
meme recouvrement. Des points de Rapports sont egalement masques a 768 px.
Le toucher dans ces zones cible donc l'assistant, pas le lien attendu.

Correction conseillee : emplacement reserve a l'assistance sur mobile, hors
des liens, ou integration dans la navigation. Verifier le recouvrement pendant
le defilement et avec un positionnement personnalise du bouton deplacable.

### P1 - Les cartes bougent apres l'affichage initial

Sources : app/static/js/mobile-welcome.js:5 et :20 ;
app/static/css/style.css:7868 et :7902 ; app/templates/index.html:148.

Le bandeau Bonjour fait partie du flux de la page. Apres 3 secondes puis 260 ms
de transition, son retrait fait remonter la grille de 113,7 px a 360/390 px et
de 99,3 px a 768 px, mesures a defilement nul dans le navigateur.
Une cible qui change de position pendant que le doigt s'approche peut donner
l'impression d'un clic rate. Ce deplacement est mesure ; un mauvais clic sur
le telephone reel de l'utilisateur n'a pas ete enregistre.

Correction conseillee : garder un accueil compact stable, ou conserver son
espace jusqu'a la prochaine navigation. Eviter de supprimer automatiquement
un bloc qui change la position des actions principales.

### P2 - Apres le toucher, la navigation attend sans indicateur persistant

Sources : app/templates/index.html:281 et :320 ;
app/static/js/klasora-ui.js:143 ; app/static/css/style.css:4126.

Les cartes sont des liens HTML directs. Le gestionnaire de chargement commun
ne traite que les elements data-klasora-action, absents de ces cartes.
Lors d'une navigation avec reponse volontairement retenue 1 seconde, leur
contenu reste identique et aria-busy est absent 150 ms apres le clic.
Le nouvel effet :active signale la pression, mais pas l'attente apres relachement.

Sur la derniere execution Chromium mobile, CPU ralenti x4, le delai entre
touchend et click pour Eleves est de 19,7 a 33,9 ms. Aucun retard impose de
300 ms ni interception de ce clic par l'application n'a ete reproduit.
Ce delai n'est ni l'INP ni le temps jusqu'a l'affichage de la page suivante.

Correction conseillee : afficher immediatement un indicateur discret de
navigation, sans retarder le lien. Le remettre a zero lors du retour navigateur
(pageshow), et preserver les ouvertures dans un nouvel onglet.

### P2 - Le serveur et les pages cibles ajoutent une attente reelle

Sources : app/services/statistiques_annuelles.py:75 ;
app/routes/paiements.py:153 ; app/static/js/offline-manager.js:44 ;
app/routes/sync.py:1275.

Le tableau de bord charge toutes les inscriptions et leurs paiements pour
calculer les compteurs. Paiements est bien pagine, mais charge encore toutes
les inscriptions et leurs eleves pour construire le selecteur du formulaire.
Le prechargement hors ligne appelle aussi /api/admin/offline-data au demarrage
sur cache froid ou expire ; cet appel a ete observe dans le navigateur.
La fenetre de fraicheur de 15 minutes est presente : il ne faut pas attribuer
un prechargement complet a chaque clic. La concurrence sur un vrai reseau lent
reste a mesurer, les appels ne bloquent pas volontairement la navigation.

Mesures du dernier passage, a 1 000 eleves fictifs :

| Destination | Serveur chaud (ms) | SQL/appel | HTML/JSON brut (octets) | Elements HTML |
| --- | ---: | ---: | ---: | ---: |
| Tableau de bord / | 59,3 | 14 | 156 082 | 594 |
| /eleves | 34,5 | 16 | 569 559 | 2 473 |
| /paiements | 99,2 | 18 | 522 617 | 2 983 |
| /api/admin/offline-data | 93,4 | 9 | 107 140 | - |

Deux passages independants ont ete executes. Les medianes chaudes varient de
59 a 125 ms pour /, de 35 a 53 ms pour Eleves, de 99 a 236 ms pour Paiements
et de 93 a 343 ms pour le prechargement. Ces variations locales interdisent
d'en deduire une latence precise en production. Le reseau et le rendu mobile
s'ajoutent a ces temps. Les volumes sont non compresses ; Nginx prevoit gzip.

Correction conseillee : agregats SQL pour les compteurs, recherche d'eleve
a la demande dans Paiements, puis prechargement hors ligne de moindre priorite.

## Points secondaires

- Le widget assistant est inclus sur chaque page (base.html:1096). Son fichier
  source fait 77 485 octets, avec beaucoup de CSS/JS inline. Examiner un
  chargement a la demande et des ressources cacheables. Ce poids source ne
  constitue pas une mesure de transfert du seul widget.
- style.css fait environ 200 Ko et le logo 197 685 octets. Bootstrap, deux
  bibliotheques d'icones et les polices sont demandes a des CDN (base.html:21).
  Leur cout sur un premier chargement mobile reste a mesurer avec le reseau reel.
- Le gestionnaire scroll reecrit padding et backdropFilter de la barre de
  navigation (base.html:975). Une classe changee uniquement au franchissement
  du seuil limiterait les mises a jour ; aucun cout GPU reel n'a ete mesure ici.
- La visite guidee peut afficher une modale apres 600 ms pour les comptes
  concernes (onboarding.js:284). Les essais tactiles l'ont consideree deja
  differee pour isoler les cartes ; ce cas merite un test de premiere visite.
- Le service worker utilise le reseau pour le HTML prive et retente apres
  800 ms en cas d'echec, sans timeout explicite (service-worker.js:80).
  Ce n'est pas un delai systematique de chaque clic. Garder la protection des
  pages authentifiees ; ne pas restaurer un cache HTML partage.

## Methode, limites et verification

- SQLite exclusivement en memoire, fixture existante, puis 50 et 1 000 eleves
  synthetiques, classes de 25 et un versement par eleve ajoute. Aucun WhatsApp.
- Client Flask, contextes renouveles ; trois appels par route, mediane des deux
  derniers. Toutes les routes sondees ont repondu HTTP 200.
- Cours et Professeurs ont aussi ete sondes, mais leur effectif n'a pas ete
  augmente : leurs temps ne representent pas un etablissement complet.
- Chromium Playwright, tactile, tailles 360/390/768 x 844, CPU x4.
  HTML effectivement rendu par Flask, CSS/JS applicatifs, Bootstrap local.
  CDN d'icones et polices neutralises ; service worker desactive et requetes
  servies par des doublures locales. Les captures utilisent les polices de
  secours. Ce protocole isole les interactions, pas le chargement de production.
- Tests de zones couvertes par elementFromPoint, navigation Eleves, deplacement
  du bandeau, absence de debordement horizontal aux trois tailles.
- Aucune erreur JavaScript a la derniere execution. Syntaxe des deux scripts
  d'audit verifiee par py_compile et node --check. Suite pytest non relancee :
  aucune modification de code applicatif dans cette intervention.
- Ni Safari reel, ni telephone utilisateur, ni latence de production mesures.
  Aucun score INP/LCP ou gain de performance reel revendique.

Reproduction et artefacts (scratch/ est ignore par Git) :

```powershell
.\.venv\Scripts\python.exe scratch/audit_mobile_index.py
node scratch/audit_mobile_index.cjs
```

Resultats : scratch/mobile-index-measures.json et mobile-index-browser.json.
Captures : scratch/audit-index-360.png, audit-index-390.png, audit-index-768.png.

Priorite proposee : retirer les recouvrements et stabiliser les cibles ;
indiquer la navigation en cours ; reduire le travail serveur et les ressources ;
mesurer ensuite sur le telephone, avec son navigateur et sa connexion habituels.
