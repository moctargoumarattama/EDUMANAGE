"""
Peuplement "plein regime" pour KLASORA.

L'application actuelle affiche les eleves a travers les inscriptions de l'annee
scolaire active. Ce script complete donc les inscriptions actives visibles, pas
seulement la table eleve.

Idempotent: il complete les volumes cibles sans supprimer les donnees existantes.
"""

import os
import random
import sys
from datetime import date, datetime, time

sys.path.insert(0, os.path.abspath("."))

from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import (
    Absence,
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    EmploiTemps,
    Inscription,
    NiveauScolaire,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
    professeur_classes,
)
from app.services.semestres import configurer_semestres_annee, get_periode_semestre


TARGET_ACTIVE_INSCRIPTIONS = int(os.environ.get("SEED_TARGET_ELEVES", "1000"))
TARGET_PROFS = int(os.environ.get("SEED_TARGET_PROFS", "30"))
ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "moctargoumarattama@icloud.com")
DEFAULT_PASSWORD_HASH = generate_password_hash(os.environ.get("SEED_DEFAULT_PASSWORD", "Password123"))

PRENOMS_GARCONS = [
    "Moussa", "Ibrahim", "Oumarou", "Ali", "Aboubacar", "Mamadou", "Youssef",
    "Salif", "Abdoulaye", "Cheick", "Hamadou", "Issoufou", "Mahamadou",
    "Ousmane", "Seydou", "Amadou", "Boubacar", "Souleymane", "Idrissa",
    "Harouna", "Adama", "Bakary", "Modibo", "Lamine", "Tidiane", "Karim",
]

PRENOMS_FILLES = [
    "Fatou", "Amina", "Aichatou", "Mariam", "Zainab", "Khadija", "Binta",
    "Ramatou", "Sadiya", "Kadidjatou", "Hadjara", "Kadiatou", "Fanta",
    "Halima", "Safiatou", "Zeinabou", "Balkissa", "Samira", "Rabi",
    "Maimouna", "Habiba", "Nafissatou", "Roukayatou", "Asmaou",
]

NOMS_FAMILLE = [
    "Diallo", "Traore", "Sow", "Kone", "Kouyate", "Oumarou", "Cisse",
    "Diop", "Toure", "Keita", "Coulibaly", "Camara", "Barry", "Ndiaye",
    "Sylla", "Sangare", "Bah", "Fofana", "Sidibe", "Bamba", "Diarra",
    "Kaba", "Kane", "Ouattara", "Sanogo", "Dembele", "Guindo", "Sawadogo",
]

MATIERES_PAR_CYCLE = {
    "primaire": [
        ("Francais", 3.0),
        ("Mathematiques", 3.0),
        ("Eveil & Sciences", 2.0),
        ("Histoire - Geographie", 2.0),
        ("Education Physique & Sportive", 1.0),
        ("Lecture & Ecriture", 2.0),
    ],
    "college": [
        ("Mathematiques", 4.0),
        ("Francais", 4.0),
        ("Histoire - Geographie", 3.0),
        ("Sciences de la Vie et de la Terre", 3.0),
        ("Physique - Chimie", 3.0),
        ("Anglais", 3.0),
        ("Education Physique & Sportive", 2.0),
    ],
    "lycee": [
        ("Mathematiques", 5.0),
        ("Physique - Chimie", 4.0),
        ("Sciences de la Vie et de la Terre", 4.0),
        ("Francais & Litterature", 3.0),
        ("Philosophie", 3.0),
        ("Anglais", 3.0),
        ("Histoire - Geographie", 3.0),
        ("Education Physique & Sportive", 2.0),
    ],
}

CLASSES_SPECS = [
    ("CI", "CI A", "primaire"), ("CI", "CI B", "primaire"),
    ("CP", "CP A", "primaire"), ("CP", "CP B", "primaire"),
    ("CE1", "CE1 A", "primaire"), ("CE1", "CE1 B", "primaire"),
    ("CE2", "CE2 A", "primaire"), ("CE2", "CE2 B", "primaire"),
    ("CM1", "CM1 A", "primaire"), ("CM1", "CM1 B", "primaire"),
    ("CM2", "CM2 A", "primaire"), ("CM2", "CM2 B", "primaire"),
    ("6E", "6eme A", "college"), ("6E", "6eme B", "college"),
    ("5E", "5eme A", "college"), ("5E", "5eme B", "college"),
    ("4E", "4eme A", "college"), ("4E", "4eme B", "college"),
    ("3E", "3eme A", "college"), ("3E", "3eme B", "college"),
    ("2NDE", "2nde Serie A", "lycee"), ("2NDE", "2nde Serie C", "lycee"),
    ("1ERE", "1ere Serie A", "lycee"), ("1ERE", "1ere Serie C", "lycee"),
    ("TERMINALE", "Terminale Serie A", "lycee"), ("TERMINALE", "Terminale Serie C", "lycee"),
]


def slug(value):
    return "".join(ch.lower() for ch in str(value) if ch.isalnum())


def get_or_create_active_year(ecole_id):
    active_years = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").all()
    if active_years:
        annee = active_years[0]
        for extra in active_years[1:]:
            extra.statut = "planifiee"
        db.session.commit()
        return annee

    annee = AnneeScolaire.query.filter_by(ecole_id=ecole_id, nom="2026-2027").first()
    if not annee:
        annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 15),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole_id,
        )
        db.session.add(annee)
    else:
        annee.statut = "active"
    db.session.commit()
    return annee


def ensure_niveaux():
    defaults = [
        ("CI", "CI", "primaire", 1), ("CP", "CP", "primaire", 2),
        ("CE1", "CE1", "primaire", 3), ("CE2", "CE2", "primaire", 4),
        ("CM1", "CM1", "primaire", 5), ("CM2", "CM2", "primaire", 6),
        ("6E", "6eme", "college", 7), ("5E", "5eme", "college", 8),
        ("4E", "4eme", "college", 9), ("3E", "3eme", "college", 10),
        ("2NDE", "2nde", "lycee", 11), ("1ERE", "1ere", "lycee", 12),
        ("TERMINALE", "Terminale", "lycee", 13),
    ]
    for code, nom, cycle, ordre in defaults:
        niveau = NiveauScolaire.query.filter_by(code=code).first()
        if not niveau:
            db.session.add(NiveauScolaire(code=code, nom=nom, cycle=cycle, ordre=ordre))
    db.session.commit()
    return NiveauScolaire.query.order_by(NiveauScolaire.ordre.asc()).all()


def ensure_semestres(ecole_id, annee):
    configurer_semestres_annee(ecole_id, annee.id, date(annee.date_debut.year + 1, 1, 31), force=True)
    s1 = get_periode_semestre(ecole_id, annee.id, "Semestre 1")
    s2 = get_periode_semestre(ecole_id, annee.id, "Semestre 2")
    if s1:
        s1.periode_active = True
    if s2:
        s2.periode_active = False
    db.session.commit()


def ensure_classes(ecole_id, annee):
    niveaux = ensure_niveaux()
    niveaux_map = {n.code.upper(): n for n in niveaux}
    for niveau in niveaux:
        cfg = AnneeNiveauConfig.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee.id,
            niveau_id=niveau.id,
        ).first()
        if not cfg:
            db.session.add(AnneeNiveauConfig(
                ecole_id=ecole_id,
                annee_scolaire_id=annee.id,
                niveau_id=niveau.id,
                actif=True,
            ))
        else:
            cfg.actif = True

    classes = []
    for code_niv, nom_classe, cycle in CLASSES_SPECS:
        niveau = niveaux_map.get(code_niv)
        classe = Classe.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee.id,
            nom=nom_classe,
        ).first()
        if not classe:
            classe = Classe(
                nom=nom_classe,
                niveau=code_niv,
                niveau_id=niveau.id if niveau else None,
                ecole_id=ecole_id,
                annee_scolaire_id=annee.id,
                statut="ouverte",
                capacite=45,
                capacite_max=45,
                salle=f"S-{len(classes) + 1:02d}",
            )
            db.session.add(classe)
            db.session.flush()
        else:
            classe.statut = "ouverte"
            classe.niveau = classe.niveau or code_niv
            if niveau and not classe.niveau_id:
                classe.niveau_id = niveau.id
            classe.capacite = max(classe.capacite or 0, 45)
            classe.capacite_max = max(classe.capacite_max or 0, 45)
        classes.append((classe, cycle))
    db.session.commit()
    return classes


def ensure_professeurs(ecole_id):
    professeurs = list(Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.id.asc()).all())
    specialites = [
        "Mathematiques", "Francais", "Histoire-Geographie", "SVT", "Physique-Chimie",
        "Anglais", "EPS", "Philosophie", "Informatique", "Primaire",
    ]
    for i in range(len(professeurs) + 1, TARGET_PROFS + 1):
        prenom = random.choice(PRENOMS_GARCONS if i % 2 == 0 else PRENOMS_FILLES)
        nom = random.choice(NOMS_FAMILLE)
        email = f"prof.fullregime.{i:03d}@klasora.local"
        code_prof = f"FULL-PRF-{i:03d}"
        utilisateur = Utilisateur.query.filter_by(email=email).first()
        if not utilisateur:
            utilisateur = Utilisateur(
                nom=nom,
                prenom=prenom,
                email=email,
                mot_de_passe=DEFAULT_PASSWORD_HASH,
                role="professeur",
                ecole_id=ecole_id,
                statut="actif",
                telephone=f"90{i:06d}"[-8:],
            )
            db.session.add(utilisateur)
            db.session.flush()
        professeur = Professeur.query.filter_by(ecole_id=ecole_id, code_prof=code_prof).first()
        if not professeur:
            professeur = Professeur(
                nom=nom,
                prenom=prenom,
                code_prof=code_prof,
                specialite=specialites[(i - 1) % len(specialites)],
                matieres_enseignees=specialites[(i - 1) % len(specialites)],
                telephone=f"90{i:06d}"[-8:],
                email=email,
                utilisateur_id=utilisateur.id,
                ecole_id=ecole_id,
            )
            db.session.add(professeur)
            db.session.flush()
            professeurs.append(professeur)
    db.session.commit()
    return Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.id.asc()).all()


def ensure_cours(ecole_id, classes, professeurs):
    cours_all = []
    for class_index, (classe, cycle) in enumerate(classes):
        if not classe.professeur_id and professeurs:
            classe.professeur_id = professeurs[class_index % len(professeurs)].id
        for idx, (nom_matiere, coeff) in enumerate(MATIERES_PAR_CYCLE[cycle]):
            prof = professeurs[(class_index * 5 + idx) % len(professeurs)]
            cours = Cours.query.filter_by(ecole_id=ecole_id, classe_id=classe.id, nom=nom_matiere).first()
            if not cours:
                cours = Cours(
                    nom=nom_matiere,
                    coefficient=coeff,
                    classe_id=classe.id,
                    professeur_id=prof.id,
                    ecole_id=ecole_id,
                )
                db.session.add(cours)
                db.session.flush()
            else:
                cours.coefficient = coeff
                cours.professeur_id = prof.id
            exists = db.session.execute(
                db.select(professeur_classes).where(
                    professeur_classes.c.professeur_id == prof.id,
                    professeur_classes.c.classe_id == classe.id,
                )
            ).first()
            if not exists:
                db.session.execute(professeur_classes.insert().values(
                    professeur_id=prof.id,
                    classe_id=classe.id,
                    ecole_id=ecole_id,
                ))
            cours_all.append(cours)
    db.session.commit()
    return cours_all


def create_parent_for_student(ecole_id, index, nom, prenom):
    email = f"parent.fullregime.{index:04d}@klasora.local"
    parent = Utilisateur.query.filter_by(email=email).first()
    if parent:
        return parent
    parent = Utilisateur(
        nom=nom,
        prenom=f"Parent {prenom}",
        email=email,
        mot_de_passe=DEFAULT_PASSWORD_HASH,
        role="parent",
        telephone=f"96{index:06d}"[-8:],
        statut="actif",
        ecole_id=ecole_id,
    )
    db.session.add(parent)
    db.session.flush()
    return parent


def unique_code_parent(index):
    base = f"FR{index:06d}"[-8:]
    if not Eleve.query.filter_by(code_parent=base).first():
        return base
    return Eleve.generer_code_parent()


def create_student(ecole_id, index, cycle):
    genre = "M" if random.random() >= 0.5 else "F"
    prenom = random.choice(PRENOMS_GARCONS if genre == "M" else PRENOMS_FILLES)
    nom = random.choice(NOMS_FAMILLE)
    age = random.randint(6, 11) if cycle == "primaire" else (random.randint(12, 15) if cycle == "college" else random.randint(16, 18))
    parent = create_parent_for_student(ecole_id, index, nom, prenom)
    frais = 150000.0 if cycle == "primaire" else (180000.0 if cycle == "college" else 220000.0)
    eleve = Eleve(
        nom=nom,
        prenom=prenom,
        date_naissance=date(2026 - age, random.randint(1, 12), random.randint(1, 28)),
        lieu_naissance=random.choice(["Niamey", "Maradi", "Zinder", "Tahoua"]),
        adresse=random.choice(["Quartier Plateau", "Quartier Yantala", "Quartier Talladje"]),
        telephone=f"97{index:06d}"[-8:],
        contact_parent=parent.telephone,
        email_parent=parent.email,
        genre=genre,
        frais_annuels=frais,
        code_parent=unique_code_parent(index),
        statut="actif",
        ecole_id=ecole_id,
        parent_id=parent.id,
        annee_premiere_ecole=2026,
    )
    db.session.add(eleve)
    db.session.flush()
    return eleve


def ensure_active_inscriptions(ecole_id, annee, classes):
    active_inscriptions = Inscription.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee.id,
    ).order_by(Inscription.id.asc()).all()
    active_eleve_ids = {ins.eleve_id for ins in active_inscriptions}
    candidates = (
        Eleve.query
        .filter(Eleve.ecole_id == ecole_id, ~Eleve.id.in_(active_eleve_ids))
        .order_by(Eleve.id.asc())
        .all()
    )
    class_count = len(classes)
    while len(active_inscriptions) < TARGET_ACTIVE_INSCRIPTIONS:
        classe, cycle = classes[len(active_inscriptions) % class_count]
        eleve = candidates.pop(0) if candidates else create_student(ecole_id, len(active_inscriptions) + 1, cycle)
        inscription = Inscription(
            ecole_id=ecole_id,
            eleve_id=eleve.id,
            classe_id=classe.id,
            annee_scolaire_id=annee.id,
            statut="inscrit",
            frais_annuels=eleve.frais_annuels,
            date_inscription=datetime.combine(annee.date_debut, time(8, 0)),
        )
        db.session.add(inscription)
        db.session.flush()
        active_inscriptions.append(inscription)
        active_eleve_ids.add(eleve.id)
        if len(active_inscriptions) % 100 == 0:
            db.session.commit()
            print(f"  ... {len(active_inscriptions)}/{TARGET_ACTIVE_INSCRIPTIONS} inscriptions actives visibles")
    for idx, ins in enumerate(active_inscriptions):
        classe, _ = classes[idx % class_count]
        ins.statut = "inscrit"
        if ins.classe_id != classe.id:
            ins.classe_id = classe.id
        if ins.eleve:
            class_order = (idx % class_count) + 1
            student_order = (idx // class_count) + 1
            family = NOMS_FAMILLE[(class_order + student_order) % len(NOMS_FAMILLE)]
            ins.eleve.nom = f"{class_order:02d}-{family}"
            ins.eleve.code_parent = f"TEST-{class_order:02d}-{student_order:03d}"
        if ins.frais_annuels is None and ins.eleve:
            ins.frais_annuels = ins.eleve.frais_annuels
    db.session.commit()
    return active_inscriptions


def close_empty_duplicate_classes(ecole_id, annee, classes):
    active_classes = Classe.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee.id,
        statut="ouverte",
    ).all()
    closed = 0
    for classe in active_classes:
        count = Inscription.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee.id,
            classe_id=classe.id,
        ).count()
        if count == 0:
            classe.statut = "fermee"
            closed += 1
    db.session.commit()
    return closed


def ensure_notes(ecole_id, annee, inscriptions, cours_all):
    print("Generation/verification des notes...")
    cours_par_classe = {}
    for cours in cours_all:
        cours_par_classe.setdefault(cours.classe_id, []).append(cours)
    evals = [
        ("Semestre 1", "Devoir", date(annee.date_debut.year, 10, 20), 1.0),
        ("Semestre 1", "Interrogation", date(annee.date_debut.year, 11, 15), 1.0),
        ("Semestre 1", "Examen", date(annee.date_debut.year + 1, 1, 15), 2.0),
        ("Semestre 2", "Devoir", date(annee.date_debut.year + 1, 3, 10), 1.0),
        ("Semestre 2", "Interrogation", date(annee.date_debut.year + 1, 4, 25), 1.0),
    ]
    inscription_ids = [ins.id for ins in inscriptions]
    existing_keys = set()
    if inscription_ids:
        existing_keys = {
            (ins_id, cours_id, periode, type_eval)
            for ins_id, cours_id, periode, type_eval in (
                db.session.query(
                    Note.inscription_id,
                    Note.cours_id,
                    Note.periode,
                    Note.type_evaluation,
                )
                .filter(
                    Note.ecole_id == ecole_id,
                    Note.annee_id == annee.id,
                    Note.inscription_id.in_(inscription_ids),
                )
                .all()
            )
        }
    created = 0
    buffer = []
    for ins in inscriptions:
        for cours in cours_par_classe.get(ins.classe_id, []):
            for periode, type_eval, eval_date, coeff in evals:
                key = (ins.id, cours.id, periode, type_eval)
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                value = max(5.0, min(19.5, round(random.gauss(12.0, 3.2) * 2) / 2))
                buffer.append(Note(
                    valeur=value,
                    coefficient=coeff,
                    type_evaluation=type_eval,
                    periode=periode,
                    date_evaluation=datetime.combine(eval_date, time(9, 0)),
                    annee_id=annee.id,
                    eleve_id=ins.eleve_id,
                    cours_id=cours.id,
                    ecole_id=ecole_id,
                    inscription_id=ins.id,
                ))
                created += 1
                if len(buffer) >= 1000:
                    db.session.bulk_save_objects(buffer)
                    db.session.commit()
                    buffer.clear()
                    print(f"  ... {created} nouvelles notes ajoutees", flush=True)
    if buffer:
        db.session.bulk_save_objects(buffer)
        db.session.commit()
    return created


def ensure_paiements(ecole_id, annee, inscriptions):
    print("Generation/verification des paiements...")
    mois_liste = ["Octobre", "Novembre", "Decembre", "Janvier"]
    inscription_ids = [ins.id for ins in inscriptions]
    existing_keys = set()
    if inscription_ids:
        existing_keys = {
            (ins_id, mois)
            for ins_id, mois in (
                db.session.query(Paiement.inscription_id, Paiement.mois)
                .filter(
                    Paiement.ecole_id == ecole_id,
                    Paiement.inscription_id.in_(inscription_ids),
                )
                .all()
            )
        }
    created = 0
    for ins in inscriptions:
        for mois in mois_liste:
            key = (ins.id, mois)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            month_num = {"Octobre": 10, "Novembre": 11, "Decembre": 12, "Janvier": 1}[mois]
            year_num = annee.date_debut.year if mois != "Janvier" else annee.date_debut.year + 1
            db.session.add(Paiement(
                montant=round((ins.frais_annuels or 150000.0) / 10, 2),
                date_paiement=datetime(year_num, month_num, random.randint(3, 20), 10, 0),
                mode_paiement=random.choice(["Especes", "Virement", "Mobile Money"]),
                statut="paye",
                mois=mois,
                annee=year_num,
                reference=f"FR-{ins.id:05d}-{slug(mois)}",
                eleve_id=ins.eleve_id,
                ecole_id=ecole_id,
                inscription_id=ins.id,
            ))
            created += 1
        if created and created % 1000 == 0:
            db.session.commit()
    db.session.commit()
    return created


def ensure_absences(ecole_id, annee, inscriptions, cours_all):
    print("Generation/verification des absences...")
    cours_par_classe = {}
    for cours in cours_all:
        cours_par_classe.setdefault(cours.classe_id, []).append(cours)
    inscription_ids = [ins.id for ins in inscriptions]
    existing_inscription_ids = set()
    if inscription_ids:
        existing_inscription_ids = {
            row[0]
            for row in (
                db.session.query(Absence.inscription_id)
                .filter(
                    Absence.ecole_id == ecole_id,
                    Absence.inscription_id.in_(inscription_ids),
                )
                .all()
            )
            if row[0] is not None
        }
    created = 0
    for ins in inscriptions[::3]:
        if ins.id in existing_inscription_ids:
            continue
        existing_inscription_ids.add(ins.id)
        cours_classe = cours_par_classe.get(ins.classe_id, [])
        db.session.add(Absence(
            date_absence=date(annee.date_debut.year, random.randint(10, 12), random.randint(1, 28)),
            justifiee=random.choice([True, False]),
            motif=random.choice(["Maladie", "Raison familiale", "Transport", None]),
            eleve_id=ins.eleve_id,
            cours_id=random.choice(cours_classe).id if cours_classe else None,
            ecole_id=ecole_id,
            inscription_id=ins.id,
        ))
        created += 1
        if created % 300 == 0:
            db.session.commit()
    db.session.commit()
    return created


def ensure_emplois(ecole_id, classes, cours_all):
    print("Generation/verification des emplois du temps...")
    jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
    slots = [(8, 10), (10, 12), (14, 16)]
    cours_par_classe = {}
    for cours in cours_all:
        cours_par_classe.setdefault(cours.classe_id, []).append(cours)
    created = 0
    for classe, _ in classes:
        if EmploiTemps.query.filter_by(ecole_id=ecole_id, classe_id=classe.id).count() >= 8:
            continue
        class_courses = cours_par_classe.get(classe.id, [])
        if not class_courses:
            continue
        cursor = 0
        for jour in jours:
            for start, end in slots:
                cours = class_courses[cursor % len(class_courses)]
                if not EmploiTemps.query.filter_by(ecole_id=ecole_id, classe_id=classe.id, jour=jour, heure_debut=time(start, 0)).first():
                    db.session.add(EmploiTemps(
                        professeur_id=cours.professeur_id or classe.professeur_id,
                        jour=jour,
                        heure_debut=time(start, 0),
                        heure_fin=time(end, 0),
                        cours_id=cours.id,
                        classe_id=classe.id,
                        salle=classe.salle or f"S-{classe.id}",
                        ecole_id=ecole_id,
                    ))
                    created += 1
                cursor += 1
    db.session.commit()
    return created


def run_seed():
    random.seed(20260926)
    app = create_app()
    with app.app_context():
        user = Utilisateur.query.filter_by(email=ADMIN_EMAIL).first()
        if not user or not user.ecole_id:
            user = Utilisateur.query.filter(Utilisateur.role == "admin", Utilisateur.ecole_id.isnot(None)).order_by(Utilisateur.id.asc()).first()
        if not user or not user.ecole_id:
            raise RuntimeError("Aucun administrateur avec ecole_id trouve. Cree d'abord une ecole/admin.")

        ecole_id = user.ecole_id
        ecole = db.session.get(Ecole, ecole_id)
        print(f"=== PEUPLEMENT PLEIN REGIME: {ecole.nom} (ecole_id={ecole_id}) ===")

        annee = get_or_create_active_year(ecole_id)
        ensure_semestres(ecole_id, annee)
        classes = ensure_classes(ecole_id, annee)
        professeurs = ensure_professeurs(ecole_id)
        cours_all = ensure_cours(ecole_id, classes, professeurs)
        inscriptions = ensure_active_inscriptions(ecole_id, annee, classes)
        closed_classes = close_empty_duplicate_classes(ecole_id, annee, classes)

        notes_created = ensure_notes(ecole_id, annee, inscriptions, cours_all)
        paiements_created = ensure_paiements(ecole_id, annee, inscriptions)
        absences_created = ensure_absences(ecole_id, annee, inscriptions, cours_all)
        emplois_created = ensure_emplois(ecole_id, classes, cours_all)

        for classe, _ in classes:
            classe.effectif = Inscription.query.filter_by(
                ecole_id=ecole_id,
                annee_scolaire_id=annee.id,
                classe_id=classe.id,
            ).count()
        db.session.commit()

        visible_count = Inscription.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id).count()
        print("=== PEUPLEMENT TERMINE ===")
        print(f"- Annee active: {annee.nom} (id={annee.id})")
        print(f"- Eleves visibles via inscriptions actives: {visible_count}")
        print(f"- Eleves total ecole: {Eleve.query.filter_by(ecole_id=ecole_id).count()}")
        print(f"- Professeurs: {Professeur.query.filter_by(ecole_id=ecole_id).count()}")
        print(f"- Classes ouvertes: {Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id, statut='ouverte').count()} (-{closed_classes} doublons vides fermes)")
        print(f"- Cours: {Cours.query.filter_by(ecole_id=ecole_id).count()}")
        print(f"- Notes total: {Note.query.filter_by(ecole_id=ecole_id).count()} (+{notes_created})")
        print(f"- Paiements total: {Paiement.query.filter_by(ecole_id=ecole_id).count()} (+{paiements_created})")
        print(f"- Absences total: {Absence.query.filter_by(ecole_id=ecole_id).count()} (+{absences_created})")
        print(f"- Emplois du temps total: {EmploiTemps.query.filter_by(ecole_id=ecole_id).count()} (+{emplois_created})")


if __name__ == "__main__":
    run_seed()
