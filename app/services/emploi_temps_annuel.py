"""
Service de gestion annuelle des emplois du temps - KLASORA Phase 3E
Fournit la logique métier d'ancrage annuel, de détection de conflits horaires,
de gestion des droits selon le cycle de vie de l'année scolaire et d'isolation multi-écoles.
"""

from datetime import datetime, time
from app import db
from app.models import (
    EmploiTemps,
    Classe,
    Cours,
    Professeur,
    Ecole,
    Eleve,
    Inscription,
    AnneeScolaire,
)
from app.utils_classes import classes_triees_pedagogique
from app.services.coherence_temporelle import (
    intervalles_se_chevauchent,
    valider_intervalle_heures,
)

MESSAGE_ANNEE_ARCHIVEE = "Cette année est archivée : l'emploi du temps est consultable en lecture seule."
MESSAGE_ANNEE_PLANIFIEE = "Préparation de l'emploi du temps"

JOURS_ORDRE = {
    'Lundi': 1,
    'Mardi': 2,
    'Mercredi': 3,
    'Jeudi': 4,
    'Vendredi': 5,
    'Samedi': 6,
    'Dimanche': 7,
}


def statut_annee_emploi(annee):
    """Retourne le statut de l'année scolaire ou 'inconnue'."""
    if not annee:
        return 'inconnue'
    return annee.statut or 'active'


def peut_modifier_emploi_temps(annee):
    """
    Règle de cycle de vie Phase 3E :
    - active : modification autorisée (gestion courante)
    - planifiee : modification autorisée (préparation de la rentrée)
    - archivee : lecture seule stricte (interdiction d'ajout, modification, suppression)
    """
    if not annee:
        return False
    return annee.statut in ('active', 'planifiee')


def get_creneaux_annee(ecole_id, annee, classe_id=None, professeur_id=None):
    """
    Récupère les créneaux horaires rattachés à l'école et à l'année scolaire spécifiée.
    L'ancrage annuel s'effectue strictement via Classe.annee_scolaire_id.
    """
    if not annee:
        return []

    from sqlalchemy.orm import joinedload
    query = (
        EmploiTemps.query.join(Classe, EmploiTemps.classe_id == Classe.id)
        .options(
            joinedload(EmploiTemps.classe),
            joinedload(EmploiTemps.cours),
            joinedload(EmploiTemps.professeur)
        )
        .filter(
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee.id,
        )
    )

    if classe_id:
        query = query.filter(EmploiTemps.classe_id == classe_id)

    if professeur_id:
        query = query.filter(EmploiTemps.professeur_id == professeur_id)

    creneaux = query.all()

    # Tri standardisé : Jour puis Heure de début
    creneaux.sort(
        key=lambda e: (
            JOURS_ORDRE.get(e.jour, 99),
            e.heure_debut or time.min,
        )
    )
    return creneaux


def detecter_conflits(ecole_id, annee, jour, heure_debut, heure_fin, classe_id, professeur_id, salle=None, exclude_id=None):
    """
    Détecte les conflits horaires dans la même école et la même année scolaire :
    - Règle de chevauchement strict : debut_A < fin_B ET fin_A > debut_B
    - Conflit 1 : même classe + créneaux qui se chevauchent
    - Conflit 2 : même professeur + créneaux qui se chevauchent
    - Conflit 3 : même salle + créneaux qui se chevauchent (si salle renseignée)

    Retourne (has_conflict: bool, error_message: str ou None).
    """
    if not annee:
        return True, "Année scolaire non définie."

    ok_heures, err_heures = valider_intervalle_heures(heure_debut, heure_fin)
    if not ok_heures:
        return True, err_heures

    # Récupérer tous les créneaux du même jour dans la même école et même année scolaire
    creneaux_jour = (
        EmploiTemps.query.join(Classe, EmploiTemps.classe_id == Classe.id)
        .filter(
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee.id,
            EmploiTemps.jour == jour,
        )
        .all()
    )

    salle_clean = salle.strip().lower() if salle and salle.strip() else None

    for c in creneaux_jour:
        if exclude_id and c.id == exclude_id:
            continue

        # Test de chevauchement temporel
        if intervalles_se_chevauchent(heure_debut, heure_fin, c.heure_debut, c.heure_fin):
            c_debut_str = c.heure_debut.strftime('%H:%M') if c.heure_debut else ''
            c_fin_str = c.heure_fin.strftime('%H:%M') if c.heure_fin else ''

            # Conflit classe
            if c.classe_id == classe_id:
                classe_nom = c.classe.nom if c.classe else f"ID {c.classe_id}"
                return True, (
                    f"Conflit de classe : la classe '{classe_nom}' a déjà un cours "
                    f"({c.cours.nom if c.cours else 'Cours'}) de {c_debut_str} à {c_fin_str} le {jour}."
                )

            # Conflit professeur
            if professeur_id and c.professeur_id == professeur_id:
                prof_nom = f"{c.professeur.prenom} {c.professeur.nom}" if c.professeur else f"ID {c.professeur_id}"
                return True, (
                    f"Conflit enseignant : {prof_nom} a déjà un cours avec la classe "
                    f"'{c.classe.nom if c.classe else ''}' de {c_debut_str} à {c_fin_str} le {jour}."
                )

            # Conflit salle
            if salle_clean and c.salle and c.salle.strip().lower() == salle_clean:
                return True, (
                    f"Conflit de salle : la salle '{c.salle.strip()}' est déjà occupée "
                    f"de {c_debut_str} à {c_fin_str} le {jour}."
                )

    return False, None


def valider_et_creer_creneau(ecole_id, annee, classe_id, cours_id, professeur_id, jour, heure_debut, heure_fin, salle=None):
    """
    Valide les contraintes d'intégrité annuelle et crée un nouveau créneau.
    Retourne (creneau, error_message).
    """
    if not peut_modifier_emploi_temps(annee):
        return None, MESSAGE_ANNEE_ARCHIVEE

    # Validation classe
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, "Classe introuvable pour votre établissement."
    if classe.annee_scolaire_id != annee.id:
        return None, "La classe n'appartient pas à l'année scolaire consultée."

    # Validation cours
    cours = Cours.query.filter_by(id=cours_id, ecole_id=ecole_id).first()
    if not cours:
        return None, "Cours introuvable pour votre établissement."
    if cours.classe_id != classe.id:
        return None, "Le cours doit obligatoirement appartenir à la classe sélectionnée."

    # Validation professeur : privilégier le professeur du cours si renseigné
    if professeur_id:
        p_check = Professeur.query.filter_by(id=professeur_id, ecole_id=ecole_id).first()
        if not p_check:
            return None, "Enseignant introuvable pour votre établissement."

    prof_final_id = cours.professeur_id or professeur_id
    professeur = Professeur.query.filter_by(id=prof_final_id, ecole_id=ecole_id).first()
    if not professeur:
        return None, "Enseignant introuvable pour votre établissement."

    # Détection des conflits
    has_conflict, conflict_msg = detecter_conflits(
        ecole_id=ecole_id,
        annee=annee,
        jour=jour,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        classe_id=classe.id,
        professeur_id=professeur.id,
        salle=salle,
    )
    if has_conflict:
        return None, conflict_msg

    creneau = EmploiTemps(
        classe_id=classe.id,
        cours_id=cours.id,
        professeur_id=professeur.id,
        jour=jour,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        salle=salle.strip() if salle and salle.strip() else None,
        ecole_id=ecole_id,
    )
    db.session.add(creneau)
    db.session.commit()
    return creneau, None


def valider_et_modifier_creneau(ecole_id, annee, creneau_id, classe_id, cours_id, professeur_id, jour, heure_debut, heure_fin, salle=None):
    """
    Valide les contraintes d'intégrité annuelle et met à jour un créneau existant.
    Retourne (creneau, error_message).
    """
    if not peut_modifier_emploi_temps(annee):
        return None, MESSAGE_ANNEE_ARCHIVEE

    creneau = EmploiTemps.query.filter_by(id=creneau_id, ecole_id=ecole_id).first()
    if not creneau:
        return None, "Créneau horaire introuvable."

    if creneau.classe.annee_scolaire_id != annee.id:
        return None, "Ce créneau appartient à une autre année scolaire."

    # Validation classe
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, "Classe introuvable pour votre établissement."
    if classe.annee_scolaire_id != annee.id:
        return None, "La classe n'appartient pas à l'année scolaire consultée."

    # Validation cours
    cours = Cours.query.filter_by(id=cours_id, ecole_id=ecole_id).first()
    if not cours:
        return None, "Cours introuvable pour votre établissement."
    if cours.classe_id != classe.id:
        return None, "Le cours doit obligatoirement appartenir à la classe sélectionnée."

    # Validation professeur : privilégier le professeur du cours si renseigné
    if professeur_id:
        p_check = Professeur.query.filter_by(id=professeur_id, ecole_id=ecole_id).first()
        if not p_check:
            return None, "Enseignant introuvable pour votre établissement."

    prof_final_id = cours.professeur_id or professeur_id
    professeur = Professeur.query.filter_by(id=prof_final_id, ecole_id=ecole_id).first()
    if not professeur:
        return None, "Enseignant introuvable pour votre établissement."

    # Détection des conflits
    has_conflict, conflict_msg = detecter_conflits(
        ecole_id=ecole_id,
        annee=annee,
        jour=jour,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        classe_id=classe.id,
        professeur_id=professeur.id,
        salle=salle,
        exclude_id=creneau.id,
    )
    if has_conflict:
        return None, conflict_msg

    creneau.classe_id = classe.id
    creneau.cours_id = cours.id
    creneau.professeur_id = professeur.id
    creneau.jour = jour
    creneau.heure_debut = heure_debut
    creneau.heure_fin = heure_fin
    creneau.salle = salle.strip() if salle and salle.strip() else None
    db.session.commit()
    return creneau, None


def supprimer_creneau(ecole_id, annee, creneau_id):
    """
    Supprime un créneau horaire après validation de l'année et des permissions.
    Retourne (success: bool, error_message: str ou None).
    """
    if not peut_modifier_emploi_temps(annee):
        return False, MESSAGE_ANNEE_ARCHIVEE

    creneau = EmploiTemps.query.filter_by(id=creneau_id, ecole_id=ecole_id).first()
    if not creneau:
        return False, "Créneau horaire introuvable."

    if creneau.classe.annee_scolaire_id != annee.id:
        return False, "Ce créneau appartient à une autre année scolaire."

    db.session.delete(creneau)
    db.session.commit()
    return True, None


def get_classes_pour_utilisateur(ecole_id, annee, user):
    """
    Retourne les classes consultables selon le rôle de l'utilisateur dans l'année consultée :
    - admin / super_admin : toutes les classes de l'école dans l'année consultée
    - professeur : classes où il enseigne dans l'année consultée
    - parent : classes de ses enfants inscrits dans l'année consultée (via Inscription)
    """
    if not annee:
        return []

    from sqlalchemy.orm import joinedload
    if user.role in ('admin', 'super_admin'):
        return (
            classes_triees_pedagogique(
                Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
                .options(joinedload(Classe.niveau_scolaire))
            ).all()
        )

    if user.role == 'professeur':
        prof = Professeur.query.filter_by(utilisateur_id=user.id).first()
        if not prof:
            return []
        # Classes où le professeur dispense un cours dans l'année consultée
        # ou a un créneau d'emploi du temps
        classes_ids = (
            db.session.query(Cours.classe_id)
            .join(Classe, Cours.classe_id == Classe.id)
            .filter(
                Cours.professeur_id == prof.id,
                Classe.ecole_id == ecole_id,
                Classe.annee_scolaire_id == annee.id,
            )
            .union(
                db.session.query(EmploiTemps.classe_id)
                .join(Classe, EmploiTemps.classe_id == Classe.id)
                .filter(
                    EmploiTemps.professeur_id == prof.id,
                    Classe.ecole_id == ecole_id,
                    Classe.annee_scolaire_id == annee.id,
                )
            )
            .all()
        )
        ids = [cid[0] for cid in classes_ids if cid[0]]
        if not ids:
            return []
        return classes_triees_pedagogique(Classe.query.filter(Classe.id.in_(ids)).options(joinedload(Classe.niveau_scolaire))).all()

    if user.role == 'parent':
        # Découplage strict de Eleve.classe_id : source de vérité = Inscription
        enfants = Eleve.query.filter_by(parent_id=user.id, ecole_id=ecole_id).all()
        if not enfants:
            return []
        enfant_ids = [e.id for e in enfants]
        inscriptions = (
            Inscription.query.options(joinedload(Inscription.classe).joinedload(Classe.niveau_scolaire)).filter(
                Inscription.eleve_id.in_(enfant_ids),
                Inscription.annee_scolaire_id == annee.id,
                Inscription.statut == 'active',
            )
            .all()
        )
        classes_dict = {}
        for ins in inscriptions:
            if ins.classe and ins.classe.ecole_id == ecole_id:
                classes_dict[ins.classe.id] = ins.classe
        return sorted(list(classes_dict.values()), key=lambda c: (c.niveau_scolaire.ordre if getattr(c, 'niveau_scolaire', None) else 999, c.nom or ""))

    return []


def donnees_impression_classe(ecole_id, annee, classe_id, user=None):
    """
    Prépare les données pour l'impression ou l'export de l'emploi du temps d'une classe :
    - école
    - année scolaire (nom issu de l'entité annuelle)
    - classe
    - créneaux ordonnés
    Vérifie les permissions si un utilisateur est fourni.
    """
    if not annee:
        return None, "Année scolaire non définie."

    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, "Classe introuvable."
    if classe.annee_scolaire_id != annee.id:
        return None, "La classe ne correspond pas à l'année scolaire demandée."

    if user and user.role not in ('admin', 'super_admin'):
        classes_autorisees = get_classes_pour_utilisateur(ecole_id, annee, user)
        if classe.id not in [c.id for c in classes_autorisees]:
            return None, "Accès non autorisé à l'emploi du temps de cette classe."

    creneaux = get_creneaux_annee(ecole_id, annee, classe_id=classe.id)
    ecole = db.session.get(Ecole, ecole_id)

    # Formatage propre des créneaux de cours (sans secondes :00)
    creneaux_formates = []
    jours_presents = set()
    plages_set = set()

    for c in creneaux:
        h_deb = c.heure_debut.strftime('%H:%M') if c.heure_debut else "00:00"
        h_fin = c.heure_fin.strftime('%H:%M') if c.heure_fin else "00:00"
        plage_label = f"{h_deb} - {h_fin}"
        plages_set.add((c.heure_debut, c.heure_fin, plage_label, h_deb, h_fin))
        if c.jour:
            jours_presents.add(c.jour)

        duree_min = 0
        if c.heure_debut and c.heure_fin:
            duree_min = (c.heure_fin.hour * 60 + c.heure_fin.minute) - (c.heure_debut.hour * 60 + c.heure_debut.minute)

        d = c.to_dict()
        d["heure_debut_court"] = h_deb
        d["heure_fin_court"] = h_fin
        d["plage_horaire"] = plage_label
        d["duree_minutes"] = max(duree_min, 0)
        creneaux_formates.append(d)

    # Jours scolaires ordonnés
    ordre_jours_ref = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi']
    if 'Dimanche' in jours_presents:
        ordre_jours_ref.append('Dimanche')
    jours_semaine = [j for j in ordre_jours_ref if j in jours_presents] or ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi']

    # Plages horaires ordonnées
    plages_triees = sorted(list(plages_set), key=lambda x: (x[0] or datetime.min.time(), x[1] or datetime.min.time()))
    plages_labels = [p[2] for p in plages_triees]

    # Construction de la grille matricielle hebdomadaire : [Plage][Jour] -> liste de cours
    grille = []
    for p_tuple in plages_triees:
        p_label = p_tuple[2]
        h_deb_c = p_tuple[3]
        h_fin_c = p_tuple[4]
        ligne_jours = {}
        for j in jours_semaine:
            cours_cellule = [
                cr for cr in creneaux_formates
                if cr["jour"] == j and cr["plage_horaire"] == p_label
            ]
            ligne_jours[j] = cours_cellule

        grille.append({
            "plage": p_label,
            "heure_debut": h_deb_c,
            "heure_fin": h_fin_c,
            "jours": ligne_jours,
        })

    # Récapitulatif par matière (volume horaire et séances)
    recap_dict = {}
    total_minutes_semaine = 0
    for cr in creneaux_formates:
        c_nom = cr.get("cours_nom") or "Matière non spécifiée"
        prof_nom = cr.get("professeur_nom") or "Non assigné"
        salle_nom = cr.get("salle") or (classe.salle or "-")
        duree = cr.get("duree_minutes", 0)
        total_minutes_semaine += duree

        if c_nom not in recap_dict:
            recap_dict[c_nom] = {
                "nom": c_nom,
                "professeurs": set(),
                "salles": set(),
                "nb_seances": 0,
                "total_minutes": 0,
            }
        recap_dict[c_nom]["nb_seances"] += 1
        recap_dict[c_nom]["total_minutes"] += duree
        if prof_nom and prof_nom != "Non assigné":
            recap_dict[c_nom]["professeurs"].add(prof_nom)
        if salle_nom and salle_nom != "-":
            recap_dict[c_nom]["salles"].add(salle_nom)

    recap_matieres = []
    for k, v in recap_dict.items():
        heures = v["total_minutes"] // 60
        mins = v["total_minutes"] % 60
        duree_str = f"{heures}h{mins:02d}" if mins else f"{heures}h"
        recap_matieres.append({
            "nom": v["nom"],
            "professeurs": ", ".join(v["professeurs"]) if v["professeurs"] else "Non assigné",
            "salles": ", ".join(v["salles"]) if v["salles"] else (classe.salle or "-"),
            "nb_seances": v["nb_seances"],
            "total_heures_str": duree_str,
            "total_minutes": v["total_minutes"],
        })
    recap_matieres.sort(key=lambda x: x["nom"].lower())

    tot_h = total_minutes_semaine // 60
    tot_m = total_minutes_semaine % 60
    total_heures_hebdo_str = f"{tot_h}h{tot_m:02d}" if tot_m else f"{tot_h}h"

    return {
        "ecole": ecole,
        "ecole_nom": ecole.nom if ecole else "Établissement",
        "ecole_devise": (getattr(ecole, 'devise', '') or getattr(ecole, 'slogan', '') or '').strip(),
        "ecole_adresse": getattr(ecole, 'adresse', ''),
        "ecole_telephone": getattr(ecole, 'telephone', ''),
        "ecole_email": getattr(ecole, 'email', ''),
        "ecole_logo_path": getattr(ecole, 'logo_path', None),
        "ecole_logo": getattr(ecole, 'logo', None),
        "annee_scolaire_nom": annee.nom,
        "classe": classe,
        "classe_nom": classe.nom,
        "classe_niveau": (classe.niveau_scolaire.nom if classe.niveau_scolaire else classe.niveau) or "",
        "classe_salle": classe.salle or "",
        "creneaux": creneaux_formates,
        "jours_semaine": jours_semaine,
        "grille": grille,
        "recap_matieres": recap_matieres,
        "total_heures_hebdo_str": total_heures_hebdo_str,
        "total_seances": len(creneaux_formates),
    }, None
