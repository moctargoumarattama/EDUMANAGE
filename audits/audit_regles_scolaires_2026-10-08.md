# Audit des règles scolaires — Klasora — 8 octobre 2026

Audit du code actuel, des routes, des services et des tests existants. Le code applicatif et la base locale n'ont pas été modifiés. Les reproductions utilisent des applications Flask minimales avec SQLite en mémoire. Les contrôles de la base réelle utilisent une connexion SQLite `mode=ro` et `PRAGMA query_only=ON`.

Cet audit confirme que la centralisation des règles financières et le verrouillage des archives restent incomplets. Les correctifs antérieurs ne suffisent pas à garantir la cohérence de tous les parcours.

## Priorités

| N° | Priorité | Problème | Conséquence principale |
| --- | --- | --- | --- |
| 1 | Haute | Droits vérifiés sur le payload plutôt que sur l'absence modifiée | Un professeur peut modifier une autre classe |
| 2 | Haute | Publication parent vérifiée sans année ni semestre | Un bulletin provisoire peut être téléchargé |
| 3 | Haute | Archives protégées d'après l'année consultée ou non protégées | Modification/suppression de documents historiques |
| 4 | Haute | Publication sans validation de complétude | Notes manquantes verrouillées et classements officialisés |
| 5 | Haute | Tarifs et soldes calculés à partir de plusieurs sources | Fausse dette ou modification de frais sans effet sur la dette |
| 6 | Haute | Transfert annoncé réussi sans décision enregistrée | Préinscription conservée après une sortie demandée |
| 7 | Moyenne | Moyenne annuelle calculée avec un seul bulletin semestriel | Suggestion de passage fondée sur une année incomplète |
| 8 | Moyenne | Un versement suffit à considérer un mois payé | Relances supprimées malgré un impayé important |
| 9 | Moyenne | Le journal financier valide la transaction de l'appelant | Un rollback ultérieur ne retire pas le paiement |
| 10 | Moyenne | Données parent hors ligne mélangées entre années | Notes et absences anciennes sous la classe actuelle |
| 11 | Moyenne | ETag calculé à partir des effectifs uniquement | Corrections ignorées par le cache hors ligne |
| 12 | Moyenne | Validation des montants et années civiles insuffisante | Tarif invalide ou reçu avec période contradictoire |

## 1. Modification d'une absence hors du périmètre du professeur

**Sources :** `app/routes/sync.py:406`, `:423`, `:437`, `:516`.

Le service vérifie les droits du professeur sur l'élève et le cours du payload. Il charge ensuite `absence_id` uniquement par ID et établissement. Il vérifie l'année de cette absence, mais ne vérifie pas qu'elle appartient à l'élève, au cours ou à l'inscription précédemment autorisés.

**Reproduction :** un professeur fournit son propre élève/cours autorisé, l'ID d'une absence d'un autre professeur dans la même année et `base_version=1`. Le résultat est `status=synced`; le motif et la justification de l'autre absence sont modifiés.

**Correction attendue :** vérifier les droits sur l'objet effectivement chargé; refuser toute discordance entre cet objet et les identifiants fournis. Une version correcte ne doit pas remplacer un contrôle d'autorisation.

## 2. Accès parent à un bulletin non publié

**Sources :** `app/utils.py:483`, `app/routes/bulletins.py:171`, `:200`, `:217`.

`bulletins_accessible_pour_parent()` recherche n'importe quelle période publiée de l'école. L'année et le semestre demandés ne sont pas pris en compte. La route calcule ensuite le PDF sans interdire la période réellement demandée lorsqu'elle n'est pas publiée.

**Reproduction :** S1 de l'année précédente publié; S1 de l'année active non publié. Le contrôle parental retourne `True` et la route atteint le rendu PDF avec une réponse `200`.

**Limite de la reproduction :** les contrôles et requêtes sont réels, mais le calcul et le rendu du PDF sont simulés pour isoler le contrôle d'accès.

**Correction attendue :** autoriser le téléchargement d'après la période précise de l'inscription demandée. Définir explicitement la règle des bulletins historiques non publiés.

## 3. Les archives ne sont pas réellement en lecture seule

**Sources :** `app/services/bulletins_annuels.py:502`, `:519`; `app/routes/bulletins.py:743`, `:760`, `:207`, `:843`, `:1186`.

Trois chemins indépendants contournent la règle d'archivage :

- Les services de modification d'appréciation et de suppression vérifient l'année consultée, puis chargent un bulletin par ID/école sans vérifier son année réelle ni son verrouillage.
- La consultation GET d'un bulletin crée et valide une `PeriodeBulletin` lorsque le nom demandé n'existe pas, y compris pour un parent et une année archivée.
- Les routes de réouverture/publication basculent directement `publie` sans vérifier le statut de l'année correspondante.

**Reproductions :** année active consultée + bulletin de l'année archivée → appréciation changée, puis suppression autorisée. Consultation d'un bulletin historique avec `?periode=Arbitrary new period` → période ajoutée et validée avant l'échec éventuel du calcul. Réouverture d'une période archivée → `publie=False`, réponse `302`.

**Correction attendue :** utiliser l'année de l'objet modifié; appliquer une garde commune aux mutations; supprimer les écritures du parcours GET de consultation.

## 4. Publication officielle sans notes complètes

**Sources :** `app/routes/bulletins.py:774`, `:793`, `:1129`, `:1186`.

Les routes basculent l'état de publication sans appeler `verifier_eligibilite_publication_periode`. Deux routes anciennes acceptent aussi une mutation par GET. Le calcul des rangs pendant la publication intercepte les exceptions et poursuit, ce qui peut laisser une période publiée malgré un calcul incomplet.

**Reproduction :** classe avec un élève inscrit et un cours de mathématiques, zéro note. Le contrôle pédagogique retourne `eligible=False`, mais l'action de publication enregistre `publie=True` et répond `302`.

**Correction attendue :** exiger la validation pédagogique avant toute publication et valider l'ensemble dans une transaction. Couvrir les routes anciennes ainsi que la route récente.

## 5. Plusieurs sources de vérité pour les frais

**Sources :** `app/services/paiements_annuels.py:72`; `app/routes/paiements.py:218`, `:381`, `:531`; `app/routes/eleves.py:897`, `:1042`, `:1091`, `:1586`, `:1783`, `:1819`; `app/services/inscriptions_annuelles.py:228`; `app/services/__init__.py:232`.

Deux défauts se cumulent :

- Modifier les frais dans la fiche élève change seulement `Eleve.frais_annuels`. L'appel à la modification de l'inscription omet le paramètre financier, donc son tarif annuel reste inchangé.
- La synthèse applique scolarité − remise + frais d'inscription, tandis que plusieurs écrans et alertes utilisent encore les anciens frais, sans remise ni frais d'inscription.

**Reproductions :** modification de 100 000 à 175 000 → inscription toujours à 100 000. Avec scolarité 100 000, remise 20 000 et inscription 5 000 → service/assistant à 85 000, formules des écrans de paiement à 100 000. Une scolarité gratuite peut aussi être remplacée par le tarif de secours lorsque le code utilise `or` pour traiter un montant zéro.

**Correction attendue :** faire porter la modification sur l'inscription de l'année choisie et utiliser la même synthèse dans les pages, API, alertes et encaissements. Préserver les montants historiques et distinguer zéro d'une valeur absente.

**Couverture manquante :** le test de modification de fiche vérifie le champ global, sans vérifier la dette annuelle. Le fichier précédemment demandé `tests/test_securite_financiere_concurrence.py` est absent.

## 6. Une préinscription future empêche une sortie ou un transfert

**Sources :** `app/services/passage_annee.py:429`, `:447`.

La présence d'une inscription cible suffit à retourner `ok=True, deja_traite=True`, même pour une décision sans classe cible telle que `transfert` ou `sortie`. La décision source n'est pas vérifiée ni enregistrée.

**Reproduction :** élève préinscrit pour l'année suivante; demande de transfert externe. Le service retourne un succès, mais `decision_fin_annee` reste `None` et la préinscription future subsiste.

**Correction attendue :** considérer l'opération déjà traitée seulement si la décision et les états source/cible correspondent. En présence d'une préinscription contradictoire, retourner un conflit explicite ou appliquer la règle définie d'annulation.

## 7. Moyenne annuelle issue d'un semestre incomplet

**Sources :** `app/services/passage_annee.py:967`, `:991`; `app/routes/annees.py:878`.

Le service calcule `AVG` sur les bulletins disponibles sans exiger S1 et S2. Dès qu'un bulletin est trouvé, l'élève est exclu du calcul de secours à partir des notes.

**Reproduction :** seul bulletin S1 à 16 → moyenne annuelle à 16 et suggestion de passage. Si S2 vaut zéro dans les notes, ces notes restent ignorées; la moyenne calculée reste 16 au lieu de 8 lorsque les deux semestres sont complets et de même poids.

**Correction attendue :** appliquer une seule règle annuelle et exiger la complétude des deux semestres; afficher une moyenne provisoire explicitement lorsque l'année est incomplète.

## 8. Mensualité considérée réglée dès le premier versement

**Sources :** `app/services/__init__.py:235`, `:286`; `app/routes/eleves.py:1607`.

Un mois est considéré payé dès qu'un paiement porte son nom. Les montants ne sont pas comparés au montant exigible du mois. Les alertes ne recherchent ensuite que les noms de mois absents.

**Reproductions :** au mois d'octobre, 1 FCFA suffit à retirer l'alerte d'octobre pour une mensualité théorique de 10 000. En juin, frais annuels de 90 000 et neuf versements de 100 → total payé 900, reste 89 100, aucune alerte financière.

**Correction attendue :** déterminer le retard à partir des montants cumulés exigibles et réglés, ou des échéances explicites, avec un état « partiellement payé ».

## 9. Validation implicite d'un paiement par le journal

**Sources :** `app/services/paiements_annuels.py:271`; `app/__init__.py:246`, `:270`; `app/routes/paiements.py:197`.

Le service appelle le journal de correction, qui fait `db.session.commit()`. Il valide ainsi le paiement et les autres modifications en cours avant que l'appelant décide de valider ou d'annuler sa transaction. L'exception du journal est également interceptée silencieusement.

**Reproduction :** service appelé sur SQLite en mémoire avec un journal reprenant le comportement réel ajout + commit, puis `db.session.rollback()` par l'appelant → le paiement existe encore.

**Correction attendue :** laisser la transaction à un propriétaire unique. Le journal doit participer à la transaction sans la valider lui-même; gérer explicitement les erreurs de traçabilité.

## 10. Consultation parent hors ligne mélangeant l'historique

**Sources :** `app/routes/sync.py:1450`, `:1485`, `:1494`.

La classe exportée provient de l'inscription active, tandis que les notes et absences sont prises sur toute la relation de l'élève. Le payload ne donne pas leur année d'inscription.

**Reproduction :** classe courante 2026-2027, note 16 de 2026, note 5 et absence de 2025 dans le même dossier. Le parcours parent classique filtre par inscription et ne donne donc pas le même résultat.

**Correction attendue :** sélectionner l'inscription consultée pour tous les objets ou structurer l'historique par année. Les exports admin/professeur doivent également utiliser le même périmètre annuel pour classes, cours et élèves.

## 11. Cache hors ligne conservant les données d'avant correction

**Source :** `app/routes/sync.py:1383`.

L'ETag admin dépend des nombres de classes, cours et élèves, mais pas du contenu. Une correction de nom, d'affectation ou de coefficient peut conserver le même ETag.

**Reproduction :** nom d'élève modifié puis GET avec l'ancien `If-None-Match` → `304`. Le client conserve le nom précédent.

**Correction attendue :** calculer l'ETag à partir du contenu exporté ou d'une version couvrant toutes les modifications concernées.

## 12. Validation insuffisante des tarifs et périodes de paiement

**Sources :** `app/routes/eleves.py:1039`, `:1043`, `:1783`; `app/forms.py:234`; `app/services/paiements_annuels.py:249`; `app/services/payment_receipts.py:133`.

- L'API de modification de frais ignore les erreurs de conversion puis annonce un succès : `"17 500"` laisse l'ancien tarif.
- Les tarifs négatifs ou non finis ne sont pas refusés. Reproduction SQLite : `float('nan')` est enregistré comme `NULL` dans le champ global sans exception, puis le tarif de secours peut s'appliquer.
- L'année civile est libre et n'est pas contrôlée par rapport au mois et à l'année scolaire. Reproduction : « Janvier 1900 » accepté sur l'année scolaire active 2026-2027; le reçu reprend cette période contradictoire.

**Correction attendue :** conversion monétaire explicite, contrôle de finitude et de montant positif ou nul selon le champ, erreur claire en cas de saisie invalide; déduction/validation de l'année civile à partir du mois scolaire.

**Limite :** le validateur de paiement accepte également NaN, mais l'insertion SQLite échoue ensuite. Ce dernier cas n'est pas un paiement silencieusement enregistré.

## État de la base locale observé en lecture seule

| Contrôle | Résultat |
| --- | --- |
| Références de paiement dupliquées | 1 groupe, 2 paiements |
| Index unique `uq_paiement_ecole_reference` | Absent |
| Inscription sans tarif annuel ni scolarité explicitement renseignés | 2 |
| Plusieurs années actives dans une même école | 0 |
| Désaccord école/année entre inscription et classe | 0 |
| Désaccord école/élève entre paiement et inscription | 0 |
| Paiement sans inscription | 0 |
| Note ou absence sans inscription | 0 |
| Paiement non positif | 0 |
| Dépassement du montant dû par inscription | 0 |
| Violations de clés étrangères déjà présentes | 0 |

L'absence de l'index unique est un problème distinct de robustesse : la migration laisse volontairement cet index non installé lorsque les références historiques sont dupliquées. Le contrôle applicatif existe, mais la protection de la base annoncée n'est pas effective sur cette installation. Les doublons historiques doivent être examinés sans réécrire arbitrairement les références; une stratégie de contrainte compatible avec l'historique doit être définie. Aucun surpaiement concurrent n'a été reproduit lors de cet audit, et `with_for_update()` seul ne constitue pas un verrou de ligne sous SQLite.

Les deux tarifs absents ne prouvent pas une dette incorrecte : un tarif global peut fournir le secours. Ils rendent toutefois le montant annuel dépendant d'une valeur modifiable hors de l'inscription.

## Ordre de correction proposé

1. Droits sur les absences synchronisées; accès parental à la publication précise.
2. Garde des archives et validation commune à toutes les routes de publication.
3. Unification du tarif annuel, des soldes et des mensualités; transaction financière cohérente.
4. Décisions de fin d'année et calcul des moyennes annuelles.
5. Isolement annuel et invalidation du cache hors ligne; validation des saisies.

Chaque correction doit disposer d'un scénario de non-régression ciblé sur base isolée. Aucune réparation automatique des données historiques n'a été effectuée. Aucun test complet de la suite n'est revendiqué pour cet audit en lecture seule.
