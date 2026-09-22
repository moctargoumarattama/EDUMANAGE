"""
Script de peuplement intensif (Plein Régime) pour KLASORA.
Cible l'école de l'utilisateur moctargoumarattama@icloud.com (ecole_id=1).

Génère :
- 13 Niveaux configurés & 26 Classes structurées
- 30 Professeurs actifs avec comptes utilisateurs & spécialités
- ~160 Cours répartis avec coefficients et professeurs assignés
- 1000 Élèves répartis uniformément avec inscriptions actives
- ~30 000 Notes d'évaluations (Devoirs, Interrogations, Examens) sur Semestre 1 & Semestre 2
- ~500 Historiques de Paiements comptables
- ~300 Saisies d'Absences scolaires
"""

import sys
import os
sys.path.insert(0, os.path.abspath("."))

import random
from datetime import date, timedelta
from app import create_app, db
from app.models import (
    Utilisateur, Ecole, AnneeScolaire, NiveauScolaire, Classe,
    Professeur, Eleve, Inscription, Cours, Note, Paiement, Absence,
    PeriodeBulletin, AnneeNiveauConfig, professeur_classes
)
from app.services.semestres import configurer_semestres_annee, get_periode_semestre
from werkzeug.security import generate_password_hash

PRENOMS_GARCONS = [
    "Moussa", "Ibrahim", "Oumarou", "Ali", "Aboubacar", "Mamadou", "Youssef", "Salif",
    "Abdoulaye", "Cheick", "Hamadou", "Issoufou", "Mahamadou", "Ousmane", "Seydou",
    "Amadou", "Boubacar", "Souleymane", "Idrissa", "Harouna", "Adama", "Bakary", "Modibo",
    "Lamine", "Tidiane", "Tahirou", "Nasser", "Rachid", "Karim", "Bilal"
]

PRENOMS_FILLES = [
    "Fatou", "Amina", "Aïchatou", "Mariam", "Zainab", "Khadija", "Binta", "Ramatou",
    "Sadiya", "Kadidjatou", "Hadjara", "Kadiatou", "Fanta", "Halima", "Safiatou",
    "Zeinabou", "Balkissa", "Samira", "Rabi", "Maimouna", "Habiba", "Nafissatou",
    "Roukayatou", "Asmaou", "Mariama", "Djamila", "Fatimata", "Sali", "Salimata"
]

NOMS_FAMILLE = [
    "Diallo", "Traoré", "Sow", "Koné", "Kouyaté", "Oumarou", "Cissé", "Diop",
    "Touré", "Keïta", "Coulibaly", "Camara", "Barry", "Ndiaye", "Sylla", "Sangaré",
    "Bah", "Fofana", "Sidibé", "Bamba", "Diarra", "Kaba", "Kane", "Ouattara",
    "Sanogo", "Dembélé", "Guindo", "Sawadogo", "Zongo", "Kaboré", "Issaka", "Saley",
    "Garba", "Abdou", "Sanda", "Maina", "Maman", "Zakari", "Yacouba", "Buzu"
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

def run_seed():
    app = create_app()
    with app.app_context():
        user = Utilisateur.query.filter_by(email='moctargoumarattama@icloud.com').first()
        if not user or not user.ecole_id:
            print("Erreur : Utilisateur moctargoumarattama@icloud.com non trouvé ou sans école.")
            return

        ecole_id = user.ecole_id
        ecole = db.session.get(Ecole, ecole_id)
        print(f"=== DÉBUT DU PEUPLEMENT INTENSIF POUR L'ÉCOLE : '{ecole.nom}' (ID={ecole_id}) ===")

        # 1. Année scolaire active
        annee = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut='active').first()
        if not annee:
            annee = AnneeScolaire.query.filter_by(ecole_id=ecole_id).first()
        if not annee:
            annee = AnneeScolaire(
                nom="2026-2027",
                date_debut=date(2026, 9, 15),
                date_fin=date(2027, 6, 30),
                statut="active",
                ecole_id=ecole_id
            )
            db.session.add(annee)
            db.session.commit()
        print(f"Année scolaire active : {annee.nom} (ID={annee.id})")

        # 2. Configuration des Semestres
        fin_s1 = date(2027, 1, 31)
        configurer_semestres_annee(ecole_id, annee.id, fin_s1, force=True)
        s1 = get_periode_semestre(ecole_id, annee.id, "Semestre 1")
        s2 = get_periode_semestre(ecole_id, annee.id, "Semestre 2")
        if s1:
            s1.periode_active = True
            db.session.commit()
        print("Semestres configurés : Semestre 1 (actif) & Semestre 2")

        # 3. Niveaux et Config
        niveaux_db = NiveauScolaire.query.order_by(NiveauScolaire.ordre.asc()).all()
        for niv in niveaux_db:
            anc = AnneeNiveauConfig.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id, niveau_id=niv.id).first()
            if not anc:
                anc = AnneeNiveauConfig(ecole_id=ecole_id, annee_scolaire_id=annee.id, niveau_id=niv.id, actif=True)
                db.session.add(anc)
            else:
                anc.actif = True
        db.session.commit()

        # 4. Création des 26 Classes (2 par niveau)
        classes = []
        niveaux_map = {n.code.upper(): n for n in niveaux_db}
        
        # Définition des structures par niveau
        classes_specs = [
            ("CI", "CI A", "primaire"), ("CI", "CI B", "primaire"),
            ("CP", "CP A", "primaire"), ("CP", "CP B", "primaire"),
            ("CE1", "CE1 A", "primaire"), ("CE1", "CE1 B", "primaire"),
            ("CE2", "CE2 A", "primaire"), ("CE2", "CE2 B", "primaire"),
            ("CM1", "CM1 A", "primaire"), ("CM1", "CM1 B", "primaire"),
            ("CM2", "CM2 A", "primaire"), ("CM2", "CM2 B", "primaire"),
            ("6E", "6ème A", "college"), ("6E", "6ème B", "college"),
            ("5E", "5ème A", "college"), ("5E", "5ème B", "college"),
            ("4E", "4ème A", "college"), ("4E", "4ème B", "college"),
            ("3E", "3ème A", "college"), ("3E", "3ème B", "college"),
            ("2NDE", "2nde Serie A", "lycee"), ("2NDE", "2nde Serie C", "lycee"),
            ("1ERE", "1ère Serie A", "lycee"), ("1ERE", "1ère Serie C", "lycee"),
            ("TERMINALE", "Terminale Serie A", "lycee"), ("TERMINALE", "Terminale Serie C", "lycee"),
        ]

        for code_niv, nom_cls, cycle in classes_specs:
            niv = niveaux_map.get(code_niv)
            cls = Classe.query.filter_by(nom=nom_cls, ecole_id=ecole_id, annee_scolaire_id=annee.id).first()
            if not cls:
                cls = Classe(
                    nom=nom_cls,
                    niveau_id=niv.id if niv else None,
                    niveau=code_niv,
                    ecole_id=ecole_id,
                    annee_scolaire_id=annee.id,
                    statut="ouverte"
                )
                db.session.add(cls)
                db.session.flush()
            classes.append((cls, cycle))
        db.session.commit()
        print(f"Structure de classes validée : {len(classes)} classes enregistrées.")

        # 5. Création des 30 Professeurs
        print("Génération de 30 professeurs...")
        hashed_pwd = generate_password_hash("Password123")
        professeurs = []
        specialites = ["Mathématiques", "Français", "Histoire-Géographie", "SVT", "Physique-Chimie", "Anglais", "EPS", "Philosophie", "Arabe", "Informatique"]

        existing_profs = Professeur.query.filter_by(ecole_id=ecole_id).all()
        current_prof_count = len(existing_profs)
        professeurs.extend(existing_profs)

        for i in range(current_prof_count + 1, 31):
            prenom = random.choice(PRENOMS_GARCONS if i % 2 == 0 else PRENOMS_FILLES)
            nom = random.choice(NOMS_FAMILLE)
            email = f"prof.{prenom.lower()}.{nom.lower()}{i}@klasora.ne"
            code_prof = f"PRF-{i:03d}"

            u = Utilisateur.query.filter_by(email=email).first()
            if not u:
                u = Utilisateur(
                    nom=nom,
                    prenom=prenom,
                    email=email,
                    mot_de_passe=hashed_pwd,
                    role="professeur",
                    ecole_id=ecole_id,
                    statut="actif"
                )
                db.session.add(u)
                db.session.flush()

            p = Professeur.query.filter_by(code_prof=code_prof).first()
            if not p:
                p = Professeur(
                    nom=nom,
                    prenom=prenom,
                    code_prof=code_prof,
                    specialite=random.choice(specialites),
                    telephone=f"9{random.randint(1000000, 9999999)}",
                    email=email,
                    utilisateur_id=u.id,
                    ecole_id=ecole_id
                )
                db.session.add(p)
                db.session.flush()
            professeurs.append(p)

        db.session.commit()
        print(f"Professeurs configurés : {len(professeurs)} professeurs actifs.")

        # 6. Création des Cours (Matières) pour chaque classe & affectation professeurs
        print("Création et affectation des cours dans chaque classe...")
        tous_les_cours = []
        for cls, cycle in classes:
            specs = MATIERES_PAR_CYCLE[cycle]
            for idx, (nom_matiere, coeff) in enumerate(specs):
                prof = professeurs[(cls.id * 3 + idx) % len(professeurs)]
                cours = Cours.query.filter_by(nom=nom_matiere, classe_id=cls.id, ecole_id=ecole_id).first()
                if not cours:
                    cours = Cours(
                        nom=nom_matiere,
                        coefficient=coeff,
                        classe_id=cls.id,
                        professeur_id=prof.id,
                        ecole_id=ecole_id
                    )
                    db.session.add(cours)
                    db.session.flush()
                else:
                    cours.professeur_id = prof.id

                # Rattacher prof à la classe dans professeur_classes si nécessaire
                stmt = db.select(professeur_classes).where(
                    professeur_classes.c.professeur_id == prof.id,
                    professeur_classes.c.classe_id == cls.id
                )
                if not db.session.execute(stmt).first():
                    db.session.execute(professeur_classes.insert().values(
                        professeur_id=prof.id,
                        classe_id=cls.id,
                        ecole_id=ecole_id
                    ))

                tous_les_cours.append(cours)
        db.session.commit()
        print(f"Cours créés : {len(tous_les_cours)} cours répartis sur toutes les classes.")

        # 7. Génération de 1000 Élèves & Inscriptions
        print("Génération de 1000 élèves et inscriptions actives...")
        eleves_existants_count = Eleve.query.filter_by(ecole_id=ecole_id).count()
        nouveaux_a_creer = max(0, 1000 - eleves_existants_count)

        inscriptions_actives = []
        eleves_crees = []

        # Inscriptions des élèves existants d'abord
        existing_eleves = Eleve.query.filter_by(ecole_id=ecole_id).all()
        for idx, el in enumerate(existing_eleves):
            cls, _ = classes[idx % len(classes)]
            insc = Inscription.query.filter_by(eleve_id=el.id, annee_scolaire_id=annee.id, ecole_id=ecole_id).first()
            if not insc:
                insc = Inscription(
                    eleve_id=el.id,
                    classe_id=cls.id,
                    annee_scolaire_id=annee.id,
                    ecole_id=ecole_id,
                    statut="inscrit",
                    date_inscription=date(2026, 9, 15)
                )
                db.session.add(insc)
                db.session.flush()
            inscriptions_actives.append(insc)

        # Création des nouveaux élèves
        for i in range(nouveaux_a_creer):
            genre = "M" if random.random() > 0.5 else "F"
            prenom = random.choice(PRENOMS_GARCONS if genre == "M" else PRENOMS_FILLES)
            nom = random.choice(NOMS_FAMILLE)
            cls, cycle = classes[i % len(classes)]

            # Âge selon le cycle
            if cycle == "primaire":
                age = random.randint(6, 11)
            elif cycle == "college":
                age = random.randint(12, 15)
            else:
                age = random.randint(16, 18)

            date_naiss = date(2026 - age, random.randint(1, 12), random.randint(1, 28))
            code_p = f"P{i+10000:06d}"

            el = Eleve(
                nom=nom,
                prenom=prenom,
                date_naissance=date_naiss,
                genre=genre,
                adresse="Quartier Central, Niamey",
                ecole_id=ecole_id,
                code_parent=code_p
            )
            db.session.add(el)
            db.session.flush()
            eleves_crees.append(el)

            insc = Inscription(
                eleve_id=el.id,
                classe_id=cls.id,
                annee_scolaire_id=annee.id,
                ecole_id=ecole_id,
                statut="inscrit",
                date_inscription=date(2026, 9, 15)
            )
            db.session.add(insc)
            inscriptions_actives.append(insc)

            if i % 200 == 0 and i > 0:
                db.session.commit()
                print(f"  ... {i}/{nouveaux_a_creer} élèves enregistrés.")

        db.session.commit()
        total_eleves = Eleve.query.filter_by(ecole_id=ecole_id).count()
        print(f"Élèves totaux enregistrés : {total_eleves} (Inscriptions active: {len(inscriptions_actives)})")

        # 8. Génération des Notes (~30 000 Évaluations)
        print("Chargement des notes d'évaluations (Plein Régime)...")
        existing_notes_count = Note.query.filter_by(ecole_id=ecole_id).count()
        if existing_notes_count < 10000:
            types_eval = ["Devoir", "Interrogation", "Examen", "TP"]
            notes_a_ajouter = []

            # Regrouper les inscriptions par classe
            inscriptions_par_classe = {}
            for insc in inscriptions_actives:
                inscriptions_par_classe.setdefault(insc.classe_id, []).append(insc)

            # Pour chaque cours de chaque classe, générer des notes pour tous ses élèves
            for cls, _ in classes:
                cours_classe = [c for c in tous_les_cours if c.classe_id == cls.id]
                eleves_classe = inscriptions_par_classe.get(cls.id, [])

                for c in cours_classe:
                    # 3 évaluations au Semestre 1 + 2 au Semestre 2
                    evals_specs = [
                        ("Semestre 1", "Devoir", date(2026, 10, 20), 1.0),
                        ("Semestre 1", "Interrogation", date(2026, 11, 15), 1.0),
                        ("Semestre 1", "Examen", date(2027, 1, 15), 2.0),
                        ("Semestre 2", "Devoir", date(2027, 3, 10), 1.0),
                        ("Semestre 2", "Interrogation", date(2027, 4, 25), 1.0),
                    ]

                    for sem, type_ev, dt_ev, coeff in evals_specs:
                        for insc in eleves_classe:
                            # Note réaliste centrée autour de 12/20 avec écart-type
                            base_note = random.gauss(12.0, 3.5)
                            note_val = max(5.0, min(19.5, round(base_note * 2) / 2))

                            n = Note(
                                valeur=note_val,
                                coefficient=coeff,
                                type_evaluation=type_ev,
                                date_evaluation=dt_ev,
                                periode=sem,
                                eleve_id=insc.eleve_id,
                                cours_id=c.id,
                                ecole_id=ecole_id,
                                inscription_id=insc.id
                            )
                            notes_a_ajouter.append(n)

                            if len(notes_a_ajouter) >= 2000:
                                db.session.bulk_save_objects(notes_a_ajouter)
                                db.session.commit()
                                notes_a_ajouter.clear()

            if notes_a_ajouter:
                db.session.bulk_save_objects(notes_a_ajouter)
                db.session.commit()
                notes_a_ajouter.clear()

        total_notes = Note.query.filter_by(ecole_id=ecole_id).count()
        print(f"Notes d'évaluations chargées : {total_notes} notes en base.")

        # 9. Génération des Paiements & Absences
        print("Chargement des paiements et absences de test...")
        existing_paiements = Paiement.query.filter_by(ecole_id=ecole_id).count()
        if existing_paiements < 500:
            paiements = []
            mois_liste = ["Octobre", "Novembre", "Décembre", "Janvier"]
            for insc in inscriptions_actives[:400]:
                for m in mois_liste:
                    p = Paiement(
                        montant=15000.0,
                        date_paiement=date(2026, 10 if m == "Octobre" else 11 if m == "Novembre" else 12 if m == "Décembre" else 1, 10),
                        mode_paiement=random.choice(["Espèces", "Virement", "Mobile Money"]),
                        statut="validé",
                        mois=m,
                        annee=2026 if m != "Janvier" else 2027,
                        reference=f"PAY-{random.randint(100000, 999999)}",
                        eleve_id=insc.eleve_id,
                        ecole_id=ecole_id,
                        inscription_id=insc.id
                    )
                    paiements.append(p)
            db.session.bulk_save_objects(paiements)
            db.session.commit()

        existing_absences = Absence.query.filter_by(ecole_id=ecole_id).count()
        if existing_absences < 300:
            absences = []
            for insc in inscriptions_actives[:200]:
                a = Absence(
                    date_absence=date(2026, random.randint(10, 12), random.randint(1, 28)),
                    justifiee=random.choice([True, False]),
                    motif="Maladie / Raison familiale" if random.random() > 0.5 else None,
                    eleve_id=insc.eleve_id,
                    ecole_id=ecole_id,
                    inscription_id=insc.id
                )
                absences.append(a)
            db.session.bulk_save_objects(absences)
            db.session.commit()

        print(f"=== PEUPLEMENT PLEIN RÉGIME TERMINÉ AVEC SUCCÈS ! ===")
        print(f"- École : {ecole.nom} (ID={ecole_id})")
        print(f"- Élèves : {Eleve.query.filter_by(ecole_id=ecole_id).count()}")
        print(f"- Professeurs : {Professeur.query.filter_by(ecole_id=ecole_id).count()}")
        print(f"- Classes : {Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id).count()}")
        print(f"- Cours : {Cours.query.filter_by(ecole_id=ecole_id).count()}")
        print(f"- Notes : {Note.query.filter_by(ecole_id=ecole_id).count()}")
        print(f"- Paiements : {Paiement.query.filter_by(ecole_id=ecole_id).count()}")

if __name__ == "__main__":
    run_seed()
