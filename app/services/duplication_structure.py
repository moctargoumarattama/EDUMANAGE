"""
app/services/duplication_structure.py
=============================================================
KLASORA — Chantier C
Service de duplication automatique de la structure pédagogique
(niveaux, classes et périodes scolaires) vers une année planifiée.

Règles métier :
  - L'année cible doit impérativement être au statut 'planifiee'.
  - L'année cible ne doit contenir aucune classe préalable (anti-doublon).
  - Les niveaux scolaires actifs de l'année source sont activés sur la cible.
  - Les classes sont clonées à effectif zéro, statut 'ouverte', prêtes pour la rentrée.
  - Les périodes scolaires (semestres/trimestres) sont clonées avec dates projetées.
  - Toute l'opération est atomique sous transaction SQL.
=============================================================
"""

from datetime import date, datetime, timedelta

from app import db
from app.models import AnneeNiveauConfig, AnneeScolaire, Classe, PeriodeBulletin
from app.services.classes_annuelles import STATUT_CLASSE_OUVERTE
from app.services.cours_annuels import preparer_cours_pour_correspondances
from app.utils_classes import classes_triees_pedagogique


def dupliquer_structure_annee(annee_source_id: int, annee_cible_id: int, ecole_id: int) -> tuple[bool, str]:
    """
    Duplique la structure pédagogique (niveaux, classes et périodes scolaires)
    d'une année source vers une année cible au statut 'planifiee'.

    Vérifications préalables :
      - ecole_id valide et années appartenant à l'école.
      - annee_cible.statut == 'planifiee' (interdit si active ou archivee).
      - annee_cible ne possède encore aucune classe.
      - annee_source possède au moins une classe.

    Retourne : (success: bool, message: str)
    """
    if not ecole_id:
        return False, "Établissement non spécifié."

    # 1. Validation de l'année source
    annee_source = AnneeScolaire.query.filter_by(id=annee_source_id, ecole_id=ecole_id).first()
    if not annee_source:
        return False, "Année scolaire source introuvable pour cet établissement."

    # 2. Validation de l'année cible
    annee_cible = AnneeScolaire.query.filter_by(id=annee_cible_id, ecole_id=ecole_id).first()
    if not annee_cible:
        return False, "Année scolaire cible introuvable pour cet établissement."

    if annee_cible.id == annee_source.id:
        return False, "L'année source et l'année cible doivent être différentes."

    if annee_cible.statut != "planifiee":
        return False, (
            f"Impossible de reconduire la structure : l'année cible '{annee_cible.nom}' est au statut "
            f"'{annee_cible.statut}'. La reconduction est strictement réservée aux années planifiées."
        )

    # 3. Contrôle anti-doublon : l'année cible doit être vide
    nb_classes_existantes = Classe.query.filter_by(
        ecole_id=ecole_id, annee_scolaire_id=annee_cible.id
    ).count()
    if nb_classes_existantes > 0:
        return False, (
            f"L'année cible possède déjà {nb_classes_existantes} classe(s). "
            "Pour éviter tout doublon accidentel, la reconduction automatique ne peut s'effectuer "
            "que sur une année ne contenant encore aucune classe."
        )

    # 4. Classes sources disponibles
    classes_source = classes_triees_pedagogique(
        Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_source.id)
    ).all()
    if not classes_source:
        return False, f"L'année source '{annee_source.nom}' ne possède aucune classe à reconduire."

    try:
        # A. Cloner / activer les niveaux scolaires
        configs_source = AnneeNiveauConfig.query.filter_by(
            ecole_id=ecole_id, annee_scolaire_id=annee_source.id, actif=True
        ).all()
        niveaux_ids = {cfg.niveau_id for cfg in configs_source}
        for c in classes_source:
            if c.niveau_id:
                niveaux_ids.add(c.niveau_id)

        for nid in niveaux_ids:
            cfg_cible = AnneeNiveauConfig.query.filter_by(
                ecole_id=ecole_id, annee_scolaire_id=annee_cible.id, niveau_id=nid
            ).first()
            if not cfg_cible:
                cfg_cible = AnneeNiveauConfig(
                    ecole_id=ecole_id,
                    annee_scolaire_id=annee_cible.id,
                    niveau_id=nid,
                    actif=True,
                )
                db.session.add(cfg_cible)
            else:
                cfg_cible.actif = True

        db.session.flush()

        # B. Cloner les classes
        source_to_target = {}
        classes_creees = []
        for src_c in classes_source:
            new_c = Classe(
                nom=src_c.nom,
                niveau=src_c.niveau,
                niveau_id=src_c.niveau_id,
                section=src_c.section,
                effectif=0,
                capacite=src_c.capacite or src_c.capacite_max or 30,
                capacite_max=src_c.capacite_max or src_c.capacite or 30,
                statut=STATUT_CLASSE_OUVERTE,
                ecole_id=ecole_id,
                salle=src_c.salle,
                professeur_id=None,
                annee_scolaire_id=annee_cible.id,
            )
            db.session.add(new_c)
            db.session.flush()
            source_to_target[src_c.id] = new_c
            classes_creees.append(new_c)

        # Cloner également les cours habituels associés aux classes
        try:
            preparer_cours_pour_correspondances(
                ecole_id=ecole_id,
                source_to_target=source_to_target,
            )
        except Exception:
            pass

        # C. Cloner / projeter les périodes scolaires (Semestres/Trimestres)
        periodes_source = (
            PeriodeBulletin.query.filter_by(ecole_id=ecole_id, annee_id=annee_source.id)
            .order_by(PeriodeBulletin.date_debut.asc(), PeriodeBulletin.id.asc())
            .all()
        )

        # Nettoyage préventif sur l'année cible
        PeriodeBulletin.query.filter_by(ecole_id=ecole_id, annee_id=annee_cible.id).delete()

        periodes_creees = []
        if periodes_source and any(p.date_debut and p.date_fin for p in periodes_source):
            if annee_source.date_debut and annee_cible.date_debut:
                delta_days = (annee_cible.date_debut - annee_source.date_debut).days
            else:
                delta_days = 365

            for p in periodes_source:
                proj_debut = (
                    p.date_debut + timedelta(days=delta_days) if p.date_debut else annee_cible.date_debut
                )
                proj_fin = (
                    p.date_fin + timedelta(days=delta_days) if p.date_fin else annee_cible.date_fin
                )

                if annee_cible.date_debut and annee_cible.date_fin:
                    proj_debut = max(annee_cible.date_debut, min(proj_debut, annee_cible.date_fin))
                    proj_fin = max(proj_debut, min(proj_fin, annee_cible.date_fin))

                new_p = PeriodeBulletin(
                    nom=p.nom,
                    annee_id=annee_cible.id,
                    ecole_id=ecole_id,
                    date_debut=proj_debut,
                    date_fin=proj_fin,
                    publie=False,
                    periode_active=False,
                )
                db.session.add(new_p)
                periodes_creees.append(new_p)
        else:
            # Périodes par défaut : Semestre 1 et Semestre 2
            if annee_cible.date_debut and annee_cible.date_fin:
                total_duree = (annee_cible.date_fin - annee_cible.date_debut).days
                fin_s1 = annee_cible.date_debut + timedelta(days=total_duree // 2)
                debut_s2 = fin_s1 + timedelta(days=1)
            else:
                fin_s1 = None
                debut_s2 = None

            p1 = PeriodeBulletin(
                nom="Semestre 1",
                annee_id=annee_cible.id,
                ecole_id=ecole_id,
                date_debut=annee_cible.date_debut,
                date_fin=fin_s1,
                publie=False,
                periode_active=False,
            )
            p2 = PeriodeBulletin(
                nom="Semestre 2",
                annee_id=annee_cible.id,
                ecole_id=ecole_id,
                date_debut=debut_s2,
                date_fin=annee_cible.date_fin,
                publie=False,
                periode_active=False,
            )
            db.session.add_all([p1, p2])
            periodes_creees.extend([p1, p2])

        db.session.commit()
        nb_classes = len(classes_creees)
        nb_periodes = len(periodes_creees)
        return True, f"{nb_classes} classes et {nb_periodes} périodes créées avec succès."

    except Exception as exc:
        db.session.rollback()
        return False, f"Erreur lors de la reconduction de la structure : {exc}"

