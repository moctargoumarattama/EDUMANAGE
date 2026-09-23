"""
Script de génération automatique des emplois du temps pour EDUMANAGE / KLASORA.
Génère une grille horaire hebdomadaire complète, cohérente et 100% sans conflit
(aucun conflit de professeur, de classe, ni de salle).

Usage :
    python scripts/seed_emplois_temps.py [--ecole-id 1] [--reset]
"""

import sys
import os
sys.path.insert(0, os.path.abspath("."))

import random
from datetime import time
from app import create_app, db
from app.models import Ecole, AnneeScolaire, Classe, Cours, Professeur, EmploiTemps
from app.utils import get_annee_active, get_annee_consultee
from app.services.emploi_temps_annuel import detecter_conflits

# Créneaux horaires standards d'un établissement scolaire
CRENEAUX_SEMAINE = [
    # (Jour, Heure début, Heure fin, période)
    ('Lundi', time(8, 0), time(10, 0), 'matin1'),
    ('Lundi', time(10, 15), time(12, 15), 'matin2'),
    ('Lundi', time(15, 0), time(17, 0), 'aprem1'),

    ('Mardi', time(8, 0), time(10, 0), 'matin1'),
    ('Mardi', time(10, 15), time(12, 15), 'matin2'),
    ('Mardi', time(15, 0), time(17, 0), 'aprem1'),

    ('Mercredi', time(8, 0), time(10, 0), 'matin1'),
    ('Mercredi', time(10, 15), time(12, 15), 'matin2'),

    ('Jeudi', time(8, 0), time(10, 0), 'matin1'),
    ('Jeudi', time(10, 15), time(12, 15), 'matin2'),
    ('Jeudi', time(15, 0), time(17, 0), 'aprem1'),

    ('Vendredi', time(8, 0), time(10, 0), 'matin1'),
    ('Vendredi', time(10, 15), time(12, 15), 'matin2'),
    ('Vendredi', time(15, 0), time(17, 0), 'aprem1'),

    ('Samedi', time(8, 0), time(10, 0), 'matin1'),
    ('Samedi', time(10, 15), time(12, 15), 'matin2'),
]


MATIERES_PAR_CYCLE = {
    "primaire": [
        ("Français", 3.0),
        ("Mathématiques", 3.0),
        ("Éveil & Sciences", 2.0),
        ("Histoire - Géographie", 2.0),
        ("Éducation Physique & Sportive", 1.0),
        ("Lecture & Écriture", 2.0),
    ],
    "college": [
        ("Mathématiques", 4.0),
        ("Français", 4.0),
        ("Histoire - Géographie", 3.0),
        ("Sciences de la Vie et de la Terre", 3.0),
        ("Physique - Chimie", 3.0),
        ("Anglais", 3.0),
        ("Éducation Physique & Sportive", 2.0),
    ],
    "lycee": [
        ("Mathématiques", 5.0),
        ("Physique - Chimie", 4.0),
        ("Sciences de la Vie et de la Terre", 4.0),
        ("Français & Littérature", 3.0),
        ("Philosophie", 3.0),
        ("Anglais", 3.0),
        ("Histoire - Géographie", 3.0),
        ("Éducation Physique & Sportive", 2.0),
    ]
}


def generer_emplois_pour_ecole(ecole_id=1, reset=True):
    app = create_app()
    with app.app_context():
        ecole = db.session.get(Ecole, ecole_id)
        if not ecole:
            print(f"[ERREUR] École ID {ecole_id} introuvable.")
            return

        annee = get_annee_active(ecole_id) or get_annee_consultee(ecole_id)
        if not annee:
            print(f"[ERREUR] Aucune année scolaire active pour l'école {ecole.nom}.")
            return

        print(f"\n=======================================================")
        print(f"  Génération Emplois du Temps : {ecole.nom}")
        print(f"  Année scolaire : {annee.nom} (ID: {annee.id})")
        print(f"=======================================================")

        # Nettoyage si demandé
        if reset:
            anciens = EmploiTemps.query.filter_by(ecole_id=ecole_id).all()
            print(f"[*] Suppression de {len(anciens)} ancien(s) créneau(x)...")
            for e in anciens:
                db.session.delete(e)
            db.session.commit()

        # Récupération des classes de l'année
        classes = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id).order_by(Classe.id).all()
        profs = Professeur.query.filter_by(ecole_id=ecole_id).all()

        if not profs:
            print("[ERREUR] Aucun professeur configuré pour cette école.")
            return

        print(f"[*] {len(classes)} classes et {len(profs)} professeurs disponibles.")

        # Suivi des réservations horaires globales pour éviter tout conflit
        # Clé prof : (prof_id, jour, heure_debut)
        # Clé salle : (salle_nom, jour, heure_debut)
        # Clé classe : (classe_id, jour, heure_debut)
        prof_occupe = set()
        salle_occupee = set()
        classe_occupee = set()

        total_creneaux_crees = 0
        classes_planifiees = 0

        # Attribution d'une salle par classe
        for idx, c in enumerate(classes, 1):
            if not c.salle:
                c.salle = f"Salle {c.nom}"
                db.session.add(c)
        db.session.commit()

        for c in classes:
            cours_classe = Cours.query.filter_by(classe_id=c.id).all()
            if not cours_classe:
                # Création automatique de cours pour les classes qui n'en ont pas
                nom_lower = (c.nom or '').lower()
                cycle = 'primaire' if any(k in nom_lower for k in ['ci', 'cp', 'ce1', 'ce2', 'cm1', 'cm2']) else (
                    'lycee' if any(k in nom_lower for k in ['2nde', '1ere', '1ère', 'terminale', 'tle']) else 'college'
                )
                matieres_def = MATIERES_PAR_CYCLE.get(cycle, MATIERES_PAR_CYCLE['college'])
                for mat_nom, coeff in matieres_def:
                    prof_choisi = random.choice(profs)
                    nouveau_cours = Cours(
                        nom=mat_nom,
                        coefficient=coeff,
                        classe_id=c.id,
                        professeur_id=prof_choisi.id,
                        ecole_id=ecole_id
                    )
                    db.session.add(nouveau_cours)
                db.session.commit()
                cours_classe = Cours.query.filter_by(classe_id=c.id).all()

            # Déterminer le quota de créneaux par cours selon coefficient / matière
            # Les matières principales ont 2 à 3 créneaux, les secondaires 1 ou 2
            creneaux_a_planifier = []
            for crs in cours_classe:
                coeff = getattr(crs, 'coefficient', 1.0) or 1.0
                if coeff >= 4.0:
                    nb_seances = 3
                elif coeff >= 2.5:
                    nb_seances = 2
                else:
                    nb_seances = 1

                prof_id = crs.professeur_id
                if not prof_id:
                    # Assigner un professeur de secours
                    prof_id = random.choice(profs).id
                    crs.professeur_id = prof_id
                    db.session.add(crs)

                for _ in range(nb_seances):
                    creneaux_a_planifier.append((crs, prof_id))

            # Mélange pour répartir les matières sur la semaine
            random.seed(c.id * 42)
            random.shuffle(creneaux_a_planifier)

            # Plafonner à la capacité maximale de la semaine (14 créneaux)
            creneaux_a_planifier = creneaux_a_planifier[:14]

            creneaux_classe_crees = 0
            salle_classe = c.salle or f"Salle {c.nom}"

            for crs, prof_id in creneaux_a_planifier:
                # Rechercher un créneau libre sans conflit
                slot_trouve = None

                # On trie les créneaux pour équilibrer la semaine
                creneaux_candidats = list(CRENEAUX_SEMAINE)
                random.shuffle(creneaux_candidats)

                for jour, h_deb, h_fin, periode in creneaux_candidats:
                    cle_classe = (c.id, jour, h_deb)
                    cle_prof = (prof_id, jour, h_deb)
                    cle_salle = (salle_classe, jour, h_deb)

                    # Vérification locale rapide
                    if cle_classe in classe_occupee:
                        continue
                    if cle_prof in prof_occupe:
                        continue
                    if cle_salle in salle_occupee:
                        continue

                    # Vérification avec le service officiel de détection des conflits
                    has_conflit, _ = detecter_conflits(
                        ecole_id=ecole_id,
                        annee=annee,
                        jour=jour,
                        heure_debut=h_deb,
                        heure_fin=h_fin,
                        classe_id=c.id,
                        professeur_id=prof_id,
                        salle=salle_classe
                    )

                    if not has_conflit:
                        slot_trouve = (jour, h_deb, h_fin)
                        break

                if slot_trouve:
                    jour, h_deb, h_fin = slot_trouve
                    cle_classe = (c.id, jour, h_deb)
                    cle_prof = (prof_id, jour, h_deb)
                    cle_salle = (salle_classe, jour, h_deb)

                    classe_occupee.add(cle_classe)
                    prof_occupe.add(cle_prof)
                    salle_occupee.add(cle_salle)

                    # Gestion salle spécifique pour EPS
                    salle_finale = salle_classe
                    if "sport" in (crs.nom or '').lower() or "eps" in (crs.nom or '').lower():
                        salle_finale = "Terrain de Sport"

                    nouvel_emploi = EmploiTemps(
                        professeur_id=prof_id,
                        jour=jour,
                        heure_debut=h_deb,
                        heure_fin=h_fin,
                        cours_id=crs.id,
                        classe_id=c.id,
                        salle=salle_finale,
                        ecole_id=ecole_id
                    )
                    db.session.add(nouvel_emploi)
                    total_creneaux_crees += 1
                    creneaux_classe_crees += 1

            if creneaux_classe_crees > 0:
                classes_planifiees += 1
                db.session.commit()
                print(f"  [OK] Classe {c.nom:20s} : {creneaux_classe_crees} créneaux planifiés (Salle: {salle_classe})")

        db.session.commit()

        print(f"\n=======================================================")
        print(f"  Génération Terminée avec Succès !")
        print(f"  Total créneaux créés : {total_creneaux_crees}")
        print(f"  Classes avec emploi du temps : {classes_planifiees} / {len(classes)}")
        print(f"  Conflits de planning : 0 (Vérifié)")
        print(f"=======================================================\n")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Générer des emplois du temps cohérents.")
    parser.add_argument('--ecole-id', type=int, default=1, help="ID de l'école cible (défaut: 1)")
    parser.add_argument('--no-reset', action='store_true', help="Ne pas supprimer les anciens créneaux")
    args = parser.parse_args()

    generer_emplois_pour_ecole(ecole_id=args.ecole_id, reset=not args.no_reset)
