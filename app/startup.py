from flask import current_app
from flask_login import current_user
from datetime import datetime
from sqlalchemy.orm import joinedload
from app.middleware import get_ecole_courante, get_ecole_id, log_action

# app/startup.py

# ============================================================
# 🔒 SAFE QUERY — Filtrage global et sécurisé par école
# ============================================================
def safe_query(model, ecole_id=None):
    """
    Retourne une query filtrée par école pour l'utilisateur actuel.
    ✅ Compatible modèles avec ou sans ecole_id direct.
    ✅ Super-admin doit choisir une école.
    """
    from app.models import Eleve, Classe

    try:
        # Si ecole_id est fourni en paramètre (pour corriger_donnees)
        if ecole_id:
            return _filter_by_ecole_id(model, ecole_id)
        
        # Sinon, utilisation normale avec current_user
        ecole = get_ecole_courante()
        if not ecole or isinstance(ecole, tuple):
            current_app.logger.warning(f"[safe_query] Aucun ecole active. Query bloquée pour {model.__name__}")
            return model.query.filter(False)

        return _filter_by_ecole_id(model, ecole.id)

    except Exception as e:
        current_app.logger.error(f"[safe_query] Erreur: {e}")
        return model.query.filter(False)

def _filter_by_ecole_id(model, ecole_id):
    """Filtre une requête par ecole_id"""
    from app.models import Eleve, Classe
    
    # --- Modèle avec ecole_id direct ---
    if hasattr(model, "ecole_id"):
        return model.query.filter_by(ecole_id=ecole_id)

    # --- Modèle lié via eleve -> classe -> ecole ---
    if hasattr(model, "eleve_id"):
        return model.query.join(Eleve).join(Classe).filter(Classe.ecole_id == ecole_id)

    # --- Fallback : aucun champ exploitable → query vide ---
    current_app.logger.warning(f"[safe_query] Impossible de filtrer {model.__name__} automatiquement")
    return model.query.filter(False)

# ============================================================
# 🧹 CORRECTION DES DONNÉES PAR ÉCOLE
# ============================================================
def corriger_donnees():
    """
    Corrige toutes les données orphelines pour chaque école.
    ✅ Sécurisé : ignore les erreurs sur champs absents
    ✅ Multi-écoles : aucune contamination
    """
    from app.models import (
        db, Inscription, Eleve, Classe, Note, Paiement, Absence,
        Alerte, Cours, JournalCorrection, Ecole
    )

    # --- Fonction utilitaire sécurisée pour user_id ---
    def get_user_id():
        try:
            if current_user is not None and getattr(current_user, 'is_authenticated', False):
                return getattr(current_user, 'id', 1)
            return 1  # superadmin par défaut
        except Exception:
            return 1

    current_app.logger.info("🚀 Démarrage de la correction des données par école...")

    ecoles = Ecole.query.all()
    if not ecoles:
        current_app.logger.warning("⚠️ Aucune école trouvée ! Création d'une école par défaut.")
        ecole_defaut = Ecole(nom="École Par Défaut", adresse="Adresse inconnue")
        db.session.add(ecole_defaut)
        db.session.commit()
        ecoles = [ecole_defaut]

    total_inscriptions = total_eleves = total_notes = 0

    for ecole in ecoles:
        current_app.logger.info(f"📘 Correction pour {ecole.nom} (id={ecole.id})")

        # --- Classe par défaut ---
        classe_defaut = Classe.query.filter_by(ecole_id=ecole.id).first()
        if not classe_defaut:
            classe_defaut = Classe(nom="Classe Par Défaut", niveau="6ème", effectif=0, ecole_id=ecole.id)
            db.session.add(classe_defaut)
            log_action(
                module="startup",
                action="Classe",
                details=f"Création classe par défaut {classe_defaut.nom}",
                user_id=get_user_id()
            )
            db.session.commit()

        # --- Élèves ---
        eleves_corriges = 0
        for eleve in safe_query(Eleve, ecole.id).filter_by(classe_id=None).all():
            try:
                eleve.classe_id = classe_defaut.id
                log_action(
                    module="startup",
                    action="Élève",
                    details=f"Élève {eleve.id} affecté à la classe par défaut",
                    user_id=get_user_id()
                )
                eleves_corriges += 1
            except Exception as e:
                current_app.logger.error(f"Erreur sur élève {getattr(eleve, 'id', 'N/A')}: {e}")
        total_eleves += eleves_corriges

        # --- Inscriptions ---
        inscriptions_corrigees = 0
        for ins in safe_query(Inscription, ecole.id).options(joinedload(Inscription.eleve)).all():
            try:
                if not ins.eleve:
                    continue
                if not ins.classe_id:
                    ins.classe_id = getattr(ins.eleve, 'classe_id', classe_defaut.id)
                    log_action(
                        module="startup",
                        action="Inscription",
                        details=f"Inscription {ins.id} corrigée",
                        user_id=get_user_id()
                    )
                    inscriptions_corrigees += 1
            except Exception as e:
                current_app.logger.error(f"Erreur sur inscription {getattr(ins, 'id', 'N/A')}: {e}")
        total_inscriptions += inscriptions_corrigees

        # --- Notes ---
        notes_corrigees = 0
        premier_cours = safe_query(Cours, ecole.id).first()
        if premier_cours:
            for note in safe_query(Note, ecole.id).all():
                try:
                    if not note.cours_id:
                        note.cours_id = premier_cours.id
                        log_action(
                            module="startup",
                            action="Note",
                            details=f"Note {getattr(note, 'id', 'N/A')} corrigée avec cours {premier_cours.id}",
                            user_id=get_user_id()
                        )
                        notes_corrigees += 1
                except Exception as e:
                    current_app.logger.error(f"Erreur sur note {getattr(note, 'id', 'N/A')}: {e}")
        total_notes += notes_corrigees

        # --- Paiements / Absences / Alertes ---
        premier_eleve = safe_query(Eleve, ecole.id).first()
        if premier_eleve:
            for model, name in [(Paiement, "Paiement"), (Absence, "Absence"), (Alerte, "Alerte")]:
                for obj in safe_query(model, ecole.id).all():
                    try:
                        if not getattr(obj, 'eleve_id', None):
                            obj.eleve_id = premier_eleve.id
                            log_action(
                                module="startup",
                                action=name,
                                details=f"{name} {getattr(obj, 'id', 'N/A')} corrigé pour élève {premier_eleve.id}",
                                user_id=get_user_id()
                            )
                    except Exception as e:
                        current_app.logger.error(f"Erreur sur {name} {getattr(obj, 'id', 'N/A')}: {e}")

        # --- Recalcul effectifs ---
        for classe in safe_query(Classe, ecole.id).filter_by(ecole_id=ecole.id).all():
            try:
                classe.effectif = len(classe.eleves) if hasattr(classe, 'eleves') else 0
                log_action(
                    module="startup",
                    action="Classe",
                    details=f"Effectif mis à jour ({classe.effectif}) pour {classe.nom}",
                    user_id=get_user_id()
                )
            except Exception as e:
                current_app.logger.error(f"Erreur sur classe {getattr(classe, 'id', 'N/A')}: {e}")

        db.session.commit()  # Commit par école pour éviter rollback global

    current_app.logger.info(
        f"✅ Correction terminée : {total_inscriptions} inscriptions, "
        f"{total_eleves} élèves, {total_notes} notes corrigées."
    )