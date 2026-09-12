from app import db
from app.models import Classe, Cours
from app.services.classes_annuelles import classe_est_ouverte


def _cours_key(cours):
    return (cours.ecole_id, cours.classe_id, (cours.nom or "").strip().upper())


def valider_classe_pour_nouveau_cours(ecole_id, classe_id):
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, "Classe invalide pour cet etablissement."
    if classe.annee_scolaire and classe.annee_scolaire.statut == "archivee":
        return None, "Impossible de creer un cours dans une annee archivee."
    if not classe_est_ouverte(classe):
        return None, "Impossible de creer un cours dans une classe fermee."
    return classe, None


def get_cours_classes_ouvertes(ecole_id, annee_scolaire_id=None, classe_id=None):
    query = Cours.query.join(Classe, Classe.id == Cours.classe_id).filter(
        Cours.ecole_id == ecole_id,
        Classe.ecole_id == ecole_id,
        Classe.statut == "ouverte",
    )
    if annee_scolaire_id:
        query = query.filter(Classe.annee_scolaire_id == annee_scolaire_id)
    if classe_id:
        query = query.filter(Classe.id == classe_id)
    return query


def preparer_cours_pour_correspondances(ecole_id, source_to_target):
    source_to_target = source_to_target or {}
    if not source_to_target:
        return {"created": [], "existing": [], "skipped_closed_class": []}

    source_classe_ids = list(source_to_target.keys())
    target_classe_ids = [classe.id for classe in source_to_target.values()]
    source_courses = (
        Cours.query
        .filter(Cours.ecole_id == ecole_id, Cours.classe_id.in_(source_classe_ids))
        .order_by(Cours.classe_id.asc(), Cours.nom.asc(), Cours.id.asc())
        .all()
    )
    existing_target_courses = (
        Cours.query
        .filter(Cours.ecole_id == ecole_id, Cours.classe_id.in_(target_classe_ids))
        .all()
    )
    existing = {_cours_key(cours): cours for cours in existing_target_courses}
    result = {"created": [], "existing": [], "skipped_closed_class": []}

    for source_cours in source_courses:
        target_classe = source_to_target.get(source_cours.classe_id)
        if not target_classe:
            continue
        if not classe_est_ouverte(target_classe):
            result["skipped_closed_class"].append(source_cours)
            continue

        key = (ecole_id, target_classe.id, (source_cours.nom or "").strip().upper())
        if key in existing:
            result["existing"].append(existing[key])
            continue

        cours = Cours(
            nom=source_cours.nom,
            description=source_cours.description,
            coefficient=source_cours.coefficient,
            ecole_id=ecole_id,
            classe_id=target_classe.id,
            professeur_id=None,
        )
        db.session.add(cours)
        db.session.flush()
        existing[key] = cours
        result["created"].append(cours)

    return result
