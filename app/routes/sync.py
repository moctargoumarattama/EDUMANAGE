import hashlib
import json
import uuid
from datetime import date, datetime

from app.extensions import csrf
from . import main
from .common import (
    Absence,
    AnneeScolaire,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Note,
    Paiement,
    SyncOperationLog,
    can_access_eleve,
    can_manage_cours,
    current_app,
    current_user,
    db,
    joinedload,
    jsonify,
    login_required,
    render_template,
    render_template_string,
    request,
    role_required,
    send_from_directory,
)
from app.services.annees_scolaires import get_annee_consultee
from app.services.absences_annuelles import (
    resolve_annee_absence,
    statut_annee_absences,
    verifier_mutation_absence,
)

@main.route('/api/connectivity', methods=['GET'])
def api_connectivity():
    return jsonify({"online": True}), 200

def _get_sync_eleve_cours(eleve_id, cours_id):
    try:
        eleve_id = int(eleve_id) if eleve_id is not None else None
    except (TypeError, ValueError):
        eleve_id = None
    try:
        cours_id = int(cours_id) if cours_id is not None else None
    except (TypeError, ValueError):
        cours_id = None
    eleve = Eleve.query.get(eleve_id) if eleve_id else None
    cours = Cours.query.get(cours_id) if cours_id else None
    return eleve, cours


def _can_sync_school_item(eleve, cours):
    if not eleve or not cours:
        return False
    if eleve.ecole_id != getattr(current_user, 'ecole_id', None):
        return False
    if cours.ecole_id != getattr(current_user, 'ecole_id', None):
        return False
    if current_user.role == 'admin':
        return True
    if current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        if not professeur:
            return False
        return cours.professeur_id == professeur.id
    return False


def _process_note_item(item, client_op_id):
    if isinstance(item.get('data'), dict):
        merged = dict(item['data'])
        for k in ('client_op_id', 'type', 'force', 'base_version'):
            if k in item and k not in merged:
                merged[k] = item[k]
        item = merged

    required_fields = ['eleve_id', 'cours_id', 'valeur', 'date_evaluation']
    missing = [f for f in required_fields if item.get(f) is None or str(item.get(f)).strip() == '']
    if missing:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': f'Champs obligatoires manquants: {missing}'}

    try:
        valeur = float(item.get('valeur'))
        if valeur < 0 or valeur > 20:
            return {'client_op_id': client_op_id, 'status': 'error', 'message': 'La note doit être comprise entre 0 et 20'}
    except (ValueError, TypeError):
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Valeur de note invalide'}

    try:
        coef = float(item.get('coefficient', 1.0) or 1.0)
    except (ValueError, TypeError):
        coef = 1.0

    date_eval_raw = item.get('date_evaluation')
    if isinstance(date_eval_raw, str):
        try:
            date_eval = datetime.strptime(date_eval_raw.replace('T', ' ')[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                date_eval = datetime.strptime(date_eval_raw[:10], '%Y-%m-%d')
            except ValueError:
                return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Format de date invalide (attendu: AAAA-MM-JJ)'}
    elif isinstance(date_eval_raw, datetime):
        date_eval = date_eval_raw
    else:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Date d\'évaluation invalide'}

    eleve, cours = _get_sync_eleve_cours(item.get('eleve_id'), item.get('cours_id'))
    if not eleve or not cours:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Élève ou cours introuvable'}

    if eleve.ecole_id != current_user.ecole_id or cours.ecole_id != current_user.ecole_id:
        return {'client_op_id': client_op_id, 'status': 'forbidden', 'message': 'Accès non autorisé pour cette école'}

    if current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        if not professeur or cours.professeur_id != professeur.id:
            return {'client_op_id': client_op_id, 'status': 'forbidden', 'message': 'Vous ne pouvez noter que vos propres cours'}
        if not can_access_eleve(eleve):
            return {'client_op_id': client_op_id, 'status': 'forbidden', 'message': 'Élève non autorisé pour ce professeur'}
    elif current_user.role != 'admin':
        return {'client_op_id': client_op_id, 'status': 'forbidden', 'message': 'Rôle non autorisé'}

    inscription = None
    if cours.classe_id:
        classe = cours.classe
        annee_id = classe.annee_scolaire_id if classe else None
        ins_q = Inscription.query.filter_by(
            eleve_id=eleve.id,
            classe_id=cours.classe_id,
            ecole_id=eleve.ecole_id
        )
        if annee_id:
            ins_q = ins_q.filter_by(annee_scolaire_id=annee_id)
        inscription = ins_q.first()
        if not inscription:
            return {'client_op_id': client_op_id, 'status': 'error', 'message': 'L\'élève n\'est pas inscrit dans la classe de ce cours'}

    type_eval = str(item.get('type_evaluation') or 'Devoir')
    periode = str(item.get('periode') or '')

    note_id = item.get('note_id') or item.get('id')
    existing_note = None
    if note_id:
        try:
            existing_note = Note.query.filter_by(id=int(note_id), ecole_id=eleve.ecole_id).first()
        except (ValueError, TypeError):
            pass

    if not existing_note:
        existing_note = Note.query.filter_by(
            eleve_id=eleve.id,
            cours_id=cours.id,
            date_evaluation=date_eval,
            type_evaluation=type_eval,
            ecole_id=eleve.ecole_id
        ).first()

    is_admin = (current_user.role == 'admin')
    is_force = (item.get('force') is True)
    base_version = item.get('base_version')
    if base_version is not None:
        try:
            base_version = int(base_version)
        except (ValueError, TypeError):
            base_version = None

    if existing_note:
        if not existing_note.inscription_id and inscription:
            existing_note.inscription_id = inscription.id
        if not existing_note.annee_id and inscription:
            existing_note.annee_id = inscription.annee_scolaire_id
        current_version = existing_note.sync_version or 1

        # 1. Arbitrage forcé (Réservé exclusivement à l'Admin)
        if is_force:
            if not is_admin:
                return {
                    'client_op_id': client_op_id,
                    'status': 'forbidden',
                    'message': "Seul un administrateur peut arbitrer et forcer la résolution d'un conflit"
                }
            old_val = existing_note.valeur
            existing_note.valeur = valeur
            existing_note.coefficient = coef
            existing_note.sync_version = current_version + 1
            existing_note.last_by_admin = True
            existing_note.updated_at = datetime.utcnow()
            db.session.flush()

            log = SyncOperationLog(
                client_op_id=client_op_id,
                ecole_id=current_user.ecole_id,
                user_id=current_user.id,
                entity_type='note',
                entity_id=existing_note.id,
                status='synced'
            )
            db.session.add(log)
            current_app.logger.info(
                f"Arbitrage Admin validé (force): Note {existing_note.id} modifiée de {old_val} à {valeur} "
                f"v{current_version}->v{existing_note.sync_version} par admin {current_user.id} (op: {client_op_id})"
            )
            return {
                'client_op_id': client_op_id,
                'status': 'synced',
                'message': f'Arbitrage Admin appliqué : note mise à jour ({old_val} -> {valeur}/20)',
                'entity_id': existing_note.id,
                'sync_version': existing_note.sync_version,
                'last_by_admin': True,
                'forced': True
            }

        # 2. Valeur identique -> already_processed (idempotent)
        if abs(existing_note.valeur - valeur) < 0.001:
            log = SyncOperationLog(
                client_op_id=client_op_id,
                ecole_id=current_user.ecole_id,
                user_id=current_user.id,
                entity_type='note',
                entity_id=existing_note.id,
                status='already_processed'
            )
            db.session.add(log)
            return {
                'client_op_id': client_op_id,
                'status': 'already_processed',
                'message': "Note déjà enregistrée à l'identique",
                'entity_id': existing_note.id,
                'sync_version': current_version,
                'last_by_admin': bool(existing_note.last_by_admin)
            }

        # 3. Contrôle de concurrence basé sur la VERSION (Optimistic Locking)
        if base_version is not None:
            if base_version == current_version:
                # La version locale correspond à la version serveur actuelle -> ACCEPTER !
                old_val = existing_note.valeur
                existing_note.valeur = valeur
                existing_note.coefficient = coef
                existing_note.sync_version = current_version + 1
                existing_note.last_by_admin = is_admin
                existing_note.updated_at = datetime.utcnow()
                db.session.flush()

                log = SyncOperationLog(
                    client_op_id=client_op_id,
                    ecole_id=current_user.ecole_id,
                    user_id=current_user.id,
                    entity_type='note',
                    entity_id=existing_note.id,
                    status='synced'
                )
                db.session.add(log)
                current_app.logger.info(
                    f"Note {existing_note.id} mise à jour avec succès de {old_val} à {valeur} "
                    f"(v{current_version}->v{existing_note.sync_version}, last_by_admin={is_admin}) "
                    f"par {current_user.role} {current_user.id} (op: {client_op_id})"
                )
                return {
                    'client_op_id': client_op_id,
                    'status': 'synced',
                    'message': f'Note mise à jour avec succès ({old_val} -> {valeur}/20)',
                    'entity_id': existing_note.id,
                    'sync_version': existing_note.sync_version,
                    'last_by_admin': existing_note.last_by_admin
                }
            else:
                # Conflit de version : base_version != current_version
                current_app.logger.warning(
                    f"Conflit de version note {existing_note.id}: base_version={base_version} != current_version={current_version}"
                )
                return {
                    'client_op_id': client_op_id,
                    'status': 'conflict',
                    'reason': 'version_conflict',
                    'message': (
                        f"Conflit de version: la note sur le serveur est à la version {current_version} "
                        f"(valeur: {existing_note.valeur}/20) alors que votre modification repose sur la version {base_version}."
                    ),
                    'entity_id': existing_note.id,
                    'server_version': current_version,
                    'base_version': base_version,
                    'server_value': existing_note.valeur,
                    'client_value': valeur,
                    'locked_by_admin': bool(existing_note.last_by_admin),
                    'can_arbitrate': is_admin
                }

        # 4. Modification sans base_version spécifiée (tentative d'écrasement non versionnée)
        return {
            'client_op_id': client_op_id,
            'status': 'conflict',
            'reason': 'version_conflict',
            'message': f"Conflit: une note différente ({existing_note.valeur}/20) existe déjà sur le serveur (v{current_version}).",
            'entity_id': existing_note.id,
            'server_version': current_version,
            'base_version': None,
            'server_value': existing_note.valeur,
            'client_value': valeur,
            'locked_by_admin': bool(existing_note.last_by_admin),
            'can_arbitrate': is_admin
        }

    note = Note(
        valeur=valeur,
        coefficient=coef,
        type_evaluation=type_eval,
        periode=periode,
        eleve_id=eleve.id,
        cours_id=cours.id,
        date_evaluation=date_eval,
        ecole_id=eleve.ecole_id,
        annee_id=inscription.annee_scolaire_id if inscription else (cours.classe.annee_scolaire_id if (cours and cours.classe) else None),
        inscription_id=inscription.id if inscription else None,
        sync_version=1,
        last_by_admin=is_admin
    )
    db.session.add(note)
    db.session.flush()

    log = SyncOperationLog(
        client_op_id=client_op_id,
        ecole_id=current_user.ecole_id,
        user_id=current_user.id,
        entity_type='note',
        entity_id=note.id,
        status='synced'
    )
    db.session.add(log)
    return {
        'client_op_id': client_op_id,
        'status': 'synced',
        'message': 'Note synchronisée avec succès',
        'entity_id': note.id,
        'sync_version': 1,
        'last_by_admin': note.last_by_admin
    }


def _process_absence_item(item, client_op_id):
    required_fields = ['eleve_id', 'date_absence']
    missing = [f for f in required_fields if item.get(f) is None or str(item.get(f)).strip() == '']
    if missing:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': f'Champs obligatoires manquants: {missing}'}

    date_abs_raw = item.get('date_absence')
    if isinstance(date_abs_raw, str):
        try:
            date_abs = datetime.strptime(date_abs_raw[:10], '%Y-%m-%d').date()
        except ValueError:
            return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Format de date invalide (attendu: AAAA-MM-JJ)'}
    elif isinstance(date_abs_raw, (datetime, date)):
        date_abs = date_abs_raw if isinstance(date_abs_raw, date) else date_abs_raw.date()
    else:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Date d\'absence invalide'}

    annee_consultee = get_annee_consultee(current_user.ecole_id)
    if not annee_consultee or annee_consultee.statut != 'active':
        return {
            'client_op_id': client_op_id,
            'status': 'forbidden',
            'message': statut_annee_absences(annee_consultee) or "Aucune année active disponible pour les absences."
        }

    eleve, cours, _inscription, annual_error = verifier_mutation_absence(
        current_user.ecole_id,
        annee_consultee,
        current_user,
        item.get('eleve_id'),
        item.get('cours_id'),
        date_abs,
    )
    if annual_error:
        return {'client_op_id': client_op_id, 'status': 'forbidden', 'message': annual_error}

    motif = item.get('motif') or ''
    justifiee = bool(item.get('justifiee', False))

    absence_id = item.get('absence_id') or item.get('id')
    existing_absence = None
    if absence_id:
        try:
            existing_absence = Absence.query.filter_by(id=int(absence_id), ecole_id=eleve.ecole_id).first()
        except (ValueError, TypeError):
            pass

    if not existing_absence:
        existing_absence = Absence.query.filter_by(
            eleve_id=eleve.id,
            cours_id=cours.id if cours else None,
            date_absence=date_abs,
            ecole_id=eleve.ecole_id
        ).first()

    if existing_absence:
        existing_annee = resolve_annee_absence(existing_absence)
        if not existing_annee or existing_annee.id != annee_consultee.id:
            return {
                'client_op_id': client_op_id,
                'status': 'forbidden',
                'message': "Cette absence n'appartient pas à l'année active consultée."
            }

    is_admin = (current_user.role == 'admin')
    is_force = (item.get('force') is True)
    base_version = item.get('base_version')
    if base_version is not None:
        try:
            base_version = int(base_version)
        except (ValueError, TypeError):
            base_version = None

    if existing_absence:
        current_version = existing_absence.sync_version or 1

        # 1. Arbitrage forcé (Réservé exclusivement à l'Admin)
        if is_force:
            if not is_admin:
                return {
                    'client_op_id': client_op_id,
                    'status': 'forbidden',
                    'message': "Seul un administrateur peut arbitrer et forcer la résolution d'un conflit"
                }
            existing_absence.motif = motif
            existing_absence.justifiee = justifiee
            existing_absence.sync_version = current_version + 1
            existing_absence.last_by_admin = True
            existing_absence.updated_at = datetime.utcnow()
            db.session.flush()

            log = SyncOperationLog(
                client_op_id=client_op_id,
                ecole_id=current_user.ecole_id,
                user_id=current_user.id,
                entity_type='absence',
                entity_id=existing_absence.id,
                status='synced'
            )
            db.session.add(log)
            current_app.logger.info(
                f"Arbitrage Admin validé (force): Absence {existing_absence.id} mise à jour "
                f"v{current_version}->v{existing_absence.sync_version} par admin {current_user.id} (op: {client_op_id})"
            )
            return {
                'client_op_id': client_op_id,
                'status': 'synced',
                'message': 'Arbitrage Admin appliqué : absence mise à jour avec succès',
                'entity_id': existing_absence.id,
                'sync_version': existing_absence.sync_version,
                'last_by_admin': True,
                'forced': True
            }

        # 2. Identique -> already_processed
        if existing_absence.motif == motif and existing_absence.justifiee == justifiee:
            log = SyncOperationLog(
                client_op_id=client_op_id,
                ecole_id=current_user.ecole_id,
                user_id=current_user.id,
                entity_type='absence',
                entity_id=existing_absence.id,
                status='already_processed'
            )
            db.session.add(log)
            return {
                'client_op_id': client_op_id,
                'status': 'already_processed',
                'message': "Absence déjà enregistrée à l'identique",
                'entity_id': existing_absence.id,
                'sync_version': current_version,
                'last_by_admin': bool(existing_absence.last_by_admin)
            }

        # 3. Contrôle de version (Optimistic locking)
        if base_version is not None:
            if base_version == current_version:
                existing_absence.motif = motif
                existing_absence.justifiee = justifiee
                existing_absence.sync_version = current_version + 1
                existing_absence.last_by_admin = is_admin
                existing_absence.updated_at = datetime.utcnow()
                db.session.flush()

                log = SyncOperationLog(
                    client_op_id=client_op_id,
                    ecole_id=current_user.ecole_id,
                    user_id=current_user.id,
                    entity_type='absence',
                    entity_id=existing_absence.id,
                    status='synced'
                )
                db.session.add(log)
                current_app.logger.info(
                    f"Absence {existing_absence.id} mise à jour avec succès "
                    f"(v{current_version}->v{existing_absence.sync_version}, last_by_admin={is_admin}) "
                    f"par {current_user.role} {current_user.id} (op: {client_op_id})"
                )
                return {
                    'client_op_id': client_op_id,
                    'status': 'synced',
                    'message': 'Absence mise à jour avec succès',
                    'entity_id': existing_absence.id,
                    'sync_version': existing_absence.sync_version,
                    'last_by_admin': existing_absence.last_by_admin
                }
            else:
                current_app.logger.warning(
                    f"Conflit de version absence {existing_absence.id}: base_version={base_version} != current_version={current_version}"
                )
                return {
                    'client_op_id': client_op_id,
                    'status': 'conflict',
                    'reason': 'version_conflict',
                    'message': (
                        f"Conflit de version: l'absence sur le serveur est à la version {current_version} "
                        f"alors que votre modification repose sur la version {base_version}."
                    ),
                    'entity_id': existing_absence.id,
                    'server_version': current_version,
                    'base_version': base_version,
                    'server_value': f"Justifiée: {existing_absence.justifiee} ({existing_absence.motif})",
                    'client_value': f"Justifiée: {justifiee} ({motif})",
                    'locked_by_admin': bool(existing_absence.last_by_admin),
                    'can_arbitrate': is_admin
                }

        # 4. Sans base_version spécifiée (tentative d'écrasement non versionnée)
        return {
            'client_op_id': client_op_id,
            'status': 'conflict',
            'reason': 'version_conflict',
            'message': "Conflit: une absence différente existe déjà sur le serveur.",
            'entity_id': existing_absence.id,
            'server_version': current_version,
            'base_version': None,
            'server_value': f"Justifiée: {existing_absence.justifiee} ({existing_absence.motif})",
            'client_value': f"Justifiée: {justifiee} ({motif})",
            'locked_by_admin': bool(existing_absence.last_by_admin),
            'can_arbitrate': is_admin
        }

    absence = Absence(
        date_absence=date_abs,
        motif=motif,
        justifiee=justifiee,
        eleve_id=eleve.id,
        cours_id=cours.id if cours else None,
        ecole_id=eleve.ecole_id,
        inscription_id=_inscription.id if _inscription else None,
        sync_version=1,
        last_by_admin=is_admin
    )
    db.session.add(absence)
    db.session.flush()

    log = SyncOperationLog(
        client_op_id=client_op_id,
        ecole_id=current_user.ecole_id,
        user_id=current_user.id,
        entity_type='absence',
        entity_id=absence.id,
        status='synced'
    )
    db.session.add(log)
    return {'client_op_id': client_op_id, 'status': 'synced', 'message': 'Absence enregistrée avec succès', 'entity_id': absence.id, 'sync_version': 1, 'last_by_admin': absence.last_by_admin}


def _process_eleve_creation_item(item, client_op_id, local_uuid_to_id=None):
    if isinstance(item.get('data'), dict):
        merged = dict(item['data'])
        for k in ('client_op_id', 'type', 'force', 'base_version', 'local_student_uuid'):
            if k in item and k not in merged:
                merged[k] = item[k]
        item = merged

    if current_user.role != 'admin':
        return {
            'client_op_id': client_op_id,
            'status': 'forbidden',
            'message': 'Seul un administrateur peut créer des élèves hors ligne'
        }

    nom = (item.get('nom') or '').strip()
    prenom = (item.get('prenom') or '').strip()
    if not nom or not prenom:
        return {
            'client_op_id': client_op_id,
            'status': 'error',
            'message': 'Le nom et le prénom de l\'élève sont obligatoires'
        }

    date_naiss_raw = item.get('date_naissance')
    date_naiss = None
    if isinstance(date_naiss_raw, str) and date_naiss_raw.strip():
        try:
            date_naiss = datetime.strptime(date_naiss_raw[:10], '%Y-%m-%d').date()
        except ValueError:
            return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Format de date de naissance invalide'}
    elif isinstance(date_naiss_raw, (date, datetime)):
        date_naiss = date_naiss_raw if isinstance(date_naiss_raw, date) else date_naiss_raw.date()
    else:
        date_naiss = date(2010, 1, 1)

    genre = item.get('genre', 'M')
    if genre not in ('M', 'F'):
        genre = 'M'

    classe_id = item.get('classe_id')
    if classe_id:
        try:
            classe_id = int(classe_id)
            cl = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first()
            if not cl:
                classe_id = None
        except (ValueError, TypeError):
            classe_id = None

    try:
        frais_annuels = float(item.get('frais_annuels', 150000.0) or 150000.0)
    except (ValueError, TypeError):
        frais_annuels = 150000.0

    code_p = None
    try:
        code_p = Eleve.generer_code_parent()
    except Exception:
        code_p = f"P{uuid.uuid4().hex[:7].upper()}"

    eleve = Eleve(
        nom=nom,
        prenom=prenom,
        date_naissance=date_naiss,
        lieu_naissance=item.get('lieu_naissance', '') or '',
        adresse=item.get('adresse', '') or '',
        telephone=item.get('telephone', '') or '',
        contact_parent=item.get('contact_parent', '') or '',
        email=item.get('email', '') or '',
        email_parent=item.get('email_parent', '') or '',
        genre=genre,
        frais_annuels=frais_annuels,
        code_parent=code_p,
        statut='actif',
        ecole_id=current_user.ecole_id,
        classe_id=classe_id
    )
    db.session.add(eleve)
    db.session.flush()

    local_student_uuid = item.get('local_student_uuid')
    if local_student_uuid and local_uuid_to_id is not None:
        local_uuid_to_id[str(local_student_uuid)] = eleve.id

    log = SyncOperationLog(
        client_op_id=client_op_id,
        ecole_id=current_user.ecole_id,
        user_id=current_user.id,
        entity_type='eleve',
        entity_id=eleve.id,
        status='synced'
    )
    db.session.add(log)

    return {
        'client_op_id': client_op_id,
        'status': 'synced',
        'message': 'Élève créé avec succès',
        'entity_id': eleve.id,
        'eleve_id': eleve.id,
        'local_student_uuid': local_student_uuid,
        'nom': eleve.nom,
        'prenom': eleve.prenom
    }


def _process_eleve_modification_item(item, client_op_id, local_uuid_to_id=None):
    if isinstance(item.get('data'), dict):
        merged = dict(item['data'])
        for k in ('client_op_id', 'type', 'force', 'base_version', 'local_student_uuid'):
            if k in item and k not in merged:
                merged[k] = item[k]
        item = merged

    if current_user.role != 'admin':
        return {
            'client_op_id': client_op_id,
            'status': 'forbidden',
            'message': 'Seul un administrateur peut modifier des élèves hors ligne'
        }

    raw_id = item.get('eleve_id') or item.get('id')
    if raw_id and str(raw_id) in (local_uuid_to_id or {}):
        raw_id = local_uuid_to_id[str(raw_id)]

    try:
        eleve_id = int(raw_id)
    except (ValueError, TypeError):
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Identifiant élève invalide'}

    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=current_user.ecole_id).first()
    if not eleve:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Élève introuvable pour cette école'}

    current_version = SyncOperationLog.query.filter_by(
        entity_type='eleve',
        entity_id=eleve.id,
        ecole_id=current_user.ecole_id,
        status='synced'
    ).count() + 1

    is_admin = (current_user.role == 'admin')
    is_force = (item.get('force') is True)
    base_version = item.get('base_version')
    if base_version is not None:
        try:
            base_version = int(base_version)
        except (ValueError, TypeError):
            base_version = None

    if base_version is not None and base_version != current_version and not is_force:
        return {
            'client_op_id': client_op_id,
            'status': 'conflict',
            'reason': 'version_conflict',
            'message': f"Conflit de version: l'élève sur le serveur est à la version {current_version} alors que votre modification repose sur la version {base_version}.",
            'entity_id': eleve.id,
            'server_version': current_version,
            'base_version': base_version,
            'can_arbitrate': is_admin
        }

    if 'nom' in item and item['nom']:
        eleve.nom = str(item['nom']).strip()
    if 'prenom' in item and item['prenom']:
        eleve.prenom = str(item['prenom']).strip()
    if 'genre' in item and item['genre'] in ('M', 'F'):
        eleve.genre = item['genre']
    if 'adresse' in item:
        eleve.adresse = item['adresse'] or ''
    if 'telephone' in item:
        eleve.telephone = item['telephone'] or ''
    if 'contact_parent' in item:
        eleve.contact_parent = item['contact_parent'] or ''
    if 'email_parent' in item:
        eleve.email_parent = item['email_parent'] or ''
    if 'lieu_naissance' in item:
        eleve.lieu_naissance = item['lieu_naissance'] or ''

    eleve.updated_at = datetime.utcnow()
    db.session.flush()

    log = SyncOperationLog(
        client_op_id=client_op_id,
        ecole_id=current_user.ecole_id,
        user_id=current_user.id,
        entity_type='eleve',
        entity_id=eleve.id,
        status='synced'
    )
    db.session.add(log)

    return {
        'client_op_id': client_op_id,
        'status': 'synced',
        'message': 'Élève mis à jour avec succès',
        'entity_id': eleve.id,
        'sync_version': current_version + 1
    }


def _process_inscription_item(item, client_op_id, local_uuid_to_id=None):
    if isinstance(item.get('data'), dict):
        merged = dict(item['data'])
        for k in ('client_op_id', 'type', 'force', 'base_version', 'local_student_uuid'):
            if k in item and k not in merged:
                merged[k] = item[k]
        item = merged

    if current_user.role != 'admin':
        return {
            'client_op_id': client_op_id,
            'status': 'forbidden',
            'message': 'Seul un administrateur peut enregistrer des inscriptions'
        }

    local_student_uuid = item.get('local_student_uuid')
    raw_eleve_id = item.get('eleve_id')

    if local_student_uuid and str(local_student_uuid) in (local_uuid_to_id or {}):
        raw_eleve_id = local_uuid_to_id[str(local_student_uuid)]
    elif raw_eleve_id and str(raw_eleve_id) in (local_uuid_to_id or {}):
        raw_eleve_id = local_uuid_to_id[str(raw_eleve_id)]

    try:
        eleve_id = int(raw_eleve_id)
    except (ValueError, TypeError):
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Identifiant élève invalide ou non réconcilié'}

    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=current_user.ecole_id).first()
    if not eleve:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Élève introuvable pour cette école'}

    try:
        classe_id = int(item.get('classe_id'))
    except (ValueError, TypeError):
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Identifiant classe invalide'}

    classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first()
    if not classe:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Classe introuvable pour cette école'}

    annee_id = item.get('annee_scolaire_id') or item.get('annee_id')
    annee = None
    if annee_id:
        try:
            annee = AnneeScolaire.query.filter_by(id=int(annee_id), ecole_id=current_user.ecole_id).first()
        except (ValueError, TypeError):
            pass
    if not annee:
        annee = get_annee_consultee(current_user.ecole_id)
    if not annee:
        return {'client_op_id': client_op_id, 'status': 'error', 'message': 'Aucune année scolaire active pour cette école'}

    existing_insc = Inscription.query.filter_by(
        ecole_id=current_user.ecole_id,
        annee_scolaire_id=annee.id,
        eleve_id=eleve.id
    ).first()

    if existing_insc:
        log = SyncOperationLog(
            client_op_id=client_op_id,
            ecole_id=current_user.ecole_id,
            user_id=current_user.id,
            entity_type='inscription',
            entity_id=existing_insc.id,
            status='already_processed'
        )
        db.session.add(log)
        return {
            'client_op_id': client_op_id,
            'status': 'already_processed',
            'message': 'Élève déjà inscrit pour cette année scolaire',
            'entity_id': existing_insc.id,
            'eleve_id': eleve.id,
            'local_student_uuid': local_student_uuid
        }

    try:
        frais = float(item.get('frais_annuels', eleve.frais_annuels or 150000.0) or 150000.0)
    except (ValueError, TypeError):
        frais = 150000.0

    inscription = Inscription(
        ecole_id=current_user.ecole_id,
        eleve_id=eleve.id,
        classe_id=classe.id,
        annee_scolaire_id=annee.id,
        frais_annuels=frais,
        statut='inscrit'
    )
    db.session.add(inscription)

    if annee.statut == 'active':
        eleve.classe_id = classe.id

    db.session.flush()

    log = SyncOperationLog(
        client_op_id=client_op_id,
        ecole_id=current_user.ecole_id,
        user_id=current_user.id,
        entity_type='inscription',
        entity_id=inscription.id,
        status='synced'
    )
    db.session.add(log)

    return {
        'client_op_id': client_op_id,
        'status': 'synced',
        'message': 'Inscription enregistrée avec succès',
        'entity_id': inscription.id,
        'eleve_id': eleve.id,
        'local_student_uuid': local_student_uuid
    }


def _process_paiement_item(item, client_op_id):
    # Règle stricte Phase 5P BIS: Paiements = connexion serveur obligatoire (ONLINE ONLY)
    return {
        'client_op_id': client_op_id,
        'status': 'forbidden',
        'message': 'Connexion Internet requise pour les paiements (opérations financières en ligne uniquement).'
    }


@main.route("/sync-hors-ligne")
@login_required
@role_required('admin', 'professeur')
def sync_hors_ligne():
    return render_template("sync_hors_ligne.html")


@main.route('/api/sync', methods=['POST'])
@csrf.exempt
@login_required
@role_required('admin', 'professeur')
def api_sync():
    """API pour synchroniser les opérations hors ligne avec idempotence et audit"""
    try:
        if current_user.role == 'parent':
            return jsonify({
                'success': False,
                'message': 'Accès refusé : les comptes parents disposent uniquement d\'un accès en lecture seule hors ligne.'
            }), 403

        if not request.is_json:
            return jsonify({
                'success': False, 
                'message': 'Content-Type doit être application/json'
            }), 400

        payload = request.get_json()
        if not payload:
            return jsonify({
                'success': False, 
                'message': 'Aucune donnée reçue'
            }), 400

        # Accepter soit une liste, soit un dictionnaire avec clé 'operations' ou 'items'
        if isinstance(payload, dict):
            data = payload.get('operations') or payload.get('items') or [payload]
        elif isinstance(payload, list):
            data = payload
        else:
            return jsonify({
                'success': False,
                'message': 'Format de données non reconnu'
            }), 400

        current_app.logger.info(f"Sync hors ligne: {len(data)} élément(s) reçus pour user {current_user.id}")

        # Ordonnancement topologique des dépendances : Élèves d'abord, puis inscriptions, puis notes/absences
        def _get_item_priority(it):
            if not isinstance(it, dict):
                return 99
            t = it.get('type')
            if t in ('eleve', 'eleve_creation'):
                return 1
            if t == 'eleve_modification':
                return 2
            if t in ('inscription', 'inscription_annuelle'):
                return 3
            if t in ('note', 'absence', 'test'):
                return 4
            return 5

        data = sorted(data, key=_get_item_priority)

        results = []
        processed_count = 0
        local_uuid_to_id = {}

        for index, item in enumerate(data):
            if not isinstance(item, dict):
                results.append({
                    'client_op_id': f"unknown_{index}",
                    'status': 'error',
                    'message': f"Élément {index}: format invalide"
                })
                continue

            # Idempotence: client_op_id généré obligatoirement à la création locale
            client_op_id = item.get('client_op_id')
            if not client_op_id or not isinstance(client_op_id, str) or not client_op_id.strip():
                results.append({
                    'client_op_id': None,
                    'status': 'error',
                    'message': f"Élément {index}: identifiant d'opération client (client_op_id) obligatoire et manquant"
                })
                continue
            client_op_id = client_op_id.strip()

            if len(client_op_id) > 128:
                results.append({
                    'client_op_id': client_op_id[:32] + '...',
                    'status': 'error',
                    'message': f"Élément {index}: identifiant d'opération trop long (max 128 caractères)"
                })
                continue

            # 1. Vérifier si l'opération a déjà été traitée sur le serveur
            existing_log = SyncOperationLog.query.filter_by(client_op_id=client_op_id).first()
            if existing_log:
                # Sécurité stricte : isolation multi-écoles et inter-utilisateurs
                if existing_log.ecole_id != current_user.ecole_id or existing_log.user_id != current_user.id:
                    current_app.logger.warning(
                        f"Sécurité idempotence: tentative d'accès à client_op_id existant ({client_op_id}) "
                        f"non détenu par user {current_user.id} (école {current_user.ecole_id})"
                    )
                    results.append({
                        'client_op_id': client_op_id,
                        'status': 'forbidden',
                        'message': 'Opération non autorisée pour ce compte'
                    })
                    continue

                if existing_log.entity_type == 'eleve':
                    local_uuid = item.get('local_student_uuid')
                    if local_uuid:
                        local_uuid_to_id[str(local_uuid)] = existing_log.entity_id

                current_app.logger.info(f"Opération {client_op_id} déjà traitée pour user {current_user.id} (statut: {existing_log.status})")
                results.append({
                    'client_op_id': client_op_id,
                    'status': 'already_processed',
                    'message': 'Opération déjà synchronisée précédemment',
                    'entity_id': existing_log.entity_id,
                    'local_student_uuid': item.get('local_student_uuid')
                })
                processed_count += 1
                continue

            # Réconciliation des identifiants temporaires locaux
            if str(item.get('eleve_id')) in local_uuid_to_id:
                item['eleve_id'] = local_uuid_to_id[str(item.get('eleve_id'))]
            if item.get('local_student_uuid') and str(item.get('local_student_uuid')) in local_uuid_to_id and not item.get('eleve_id'):
                item['eleve_id'] = local_uuid_to_id[str(item.get('local_student_uuid'))]

            if isinstance(item.get('data'), dict):
                d = item['data']
                if str(d.get('eleve_id')) in local_uuid_to_id:
                    d['eleve_id'] = local_uuid_to_id[str(d.get('eleve_id'))]
                if d.get('local_student_uuid') and str(d.get('local_student_uuid')) in local_uuid_to_id and not d.get('eleve_id'):
                    d['eleve_id'] = local_uuid_to_id[str(d.get('local_student_uuid'))]

            item_type = item.get('type')

            try:
                with db.session.begin_nested():
                    if item_type in ('eleve', 'eleve_creation'):
                        if item.get('action') == 'update' or (item.get('eleve_id') and not item.get('local_student_uuid') and not item.get('is_creation')):
                            res = _process_eleve_modification_item(item, client_op_id, local_uuid_to_id)
                        else:
                            res = _process_eleve_creation_item(item, client_op_id, local_uuid_to_id)
                    elif item_type == 'eleve_modification':
                        res = _process_eleve_modification_item(item, client_op_id, local_uuid_to_id)
                    elif item_type in ('inscription', 'inscription_annuelle'):
                        res = _process_inscription_item(item, client_op_id, local_uuid_to_id)
                    elif item_type == 'note':
                        res = _process_note_item(item, client_op_id)
                    elif item_type == 'absence':
                        res = _process_absence_item(item, client_op_id)
                    elif item_type == 'paiement':
                        res = _process_paiement_item(item, client_op_id)
                    elif item_type in ('annee', 'annee_scolaire', 'classe', 'structure', 'utilisateur', 'user', 'permission', 'backup', 'restore', 'parametres'):
                        res = {
                            'client_op_id': client_op_id,
                            'status': 'forbidden',
                            'message': 'Connexion Internet requise pour cette opération.'
                        }
                    elif item_type == 'test':
                        res = {'client_op_id': client_op_id, 'status': 'synced', 'message': 'Test synchronisé'}
                    else:
                        res = {'client_op_id': client_op_id, 'status': 'error', 'message': f'Type inconnu: {item_type}'}

                    if res['status'] not in ('synced', 'already_processed'):
                        raise ValueError(res['message'])

                    results.append(res)
                    processed_count += 1

            except ValueError:
                results.append(res)
            except Exception as exc:
                current_app.logger.exception(f"Erreur sync item {client_op_id}")
                results.append({
                    'client_op_id': client_op_id,
                    'status': 'error',
                    'message': "Une erreur interne est survenue lors du traitement de l'opération."
                })

        if processed_count > 0:
            db.session.commit()
            current_app.logger.info(f"Sync terminée avec succès: {processed_count} opération(s) enregistrée(s)")

        errors = [r['message'] for r in results if r['status'] in ('error', 'forbidden', 'conflict')]

        response_data = {
            'success': True,
            'message': f'{processed_count} élément(s) synchronisé(s) avec succès',
            'processed': processed_count,
            'total': len(data),
            'results': results
        }
        if errors:
            response_data['errors'] = errors
            response_data['message'] = f'{processed_count} synchronisé(s), {len(errors)} anomalie(s)'

        response = jsonify(response_data)
        response.headers['Cache-Control'] = 'private, no-cache, no-store, must-revalidate'
        return response, 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erreur générale de synchronisation")
        return jsonify({
            'success': False,
            'message': 'Une erreur interne est survenue lors de la synchronisation.'
        }), 500


@main.route('/api/professeur/offline-data', methods=['GET'])
@login_required
@role_required('professeur')
def api_professeur_offline_data():
    """
    Fournit les données indispensables au travail hors-ligne du professeur:
    - Classes assignées
    - Cours dispensés
    - Élèves de ses classes/cours
    - Année scolaire active & périodes
    """
    try:
        professeur = getattr(current_user, 'professeur_rel', None)
        if not professeur:
            return jsonify({
                'success': False,
                'message': "Profil professeur non trouvé pour ce compte"
            }), 404

        ecole_id = current_user.ecole_id

        # 1. Année scolaire active
        annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut='active').first()

        # 2. Cours du professeur
        cours_query = Cours.query.filter_by(professeur_id=professeur.id, ecole_id=ecole_id).all()
        cours_data = []
        classes_map = {}

        for c in cours_query:
            cours_data.append({
                'id': c.id,
                'nom': c.nom,
                'code': getattr(c, 'code', None) or c.nom,
                'classe_id': c.classe_id,
                'classe_nom': c.classe.nom if c.classe else None,
                'coefficient': getattr(c, 'coefficient', 1.0)
            })
            if c.classe:
                classes_map[c.classe.id] = c.classe

        # 3. Classes accessibles : uniquement via les cours actifs du professeur
        classes_data = [
            {
                'id': cl.id,
                'nom': cl.nom,
                'niveau': getattr(cl, 'niveau', '')
            }
            for cl in classes_map.values()
        ]
        classes_data.sort(key=lambda x: x['nom'])

        # 4. Élèves des classes accessibles
        classe_ids = list(classes_map.keys())
        eleves_data = []
        if classe_ids:
            annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()
            annee_id = annee_active.id if annee_active else None

            inscriptions_q = Inscription.query.filter(
                Inscription.ecole_id == ecole_id,
                Inscription.classe_id.in_(classe_ids),
                Inscription.statut != 'desinscrit'
            )
            if annee_id:
                inscriptions_q = inscriptions_q.filter(Inscription.annee_scolaire_id == annee_id)

            inscriptions = inscriptions_q.options(db.joinedload(Inscription.eleve), db.joinedload(Inscription.classe)).all()
            seen_eleve_ids = set()
            for insc in inscriptions:
                e = insc.eleve
                if e and e.id not in seen_eleve_ids:
                    seen_eleve_ids.add(e.id)
                    eleves_data.append({
                        'id': e.id,
                        'nom': e.nom,
                        'prenom': e.prenom,
                        'matricule': getattr(e, 'matricule', None) or f"EL-{e.id}",
                        'classe_id': insc.classe_id,
                        'classe_nom': insc.classe.nom if insc.classe else None
                    })

        # 5. Périodes d'évaluation
        periodes = ['Trimestre 1', 'Trimestre 2', 'Trimestre 3', 'Semestre 1', 'Semestre 2']

        response = jsonify({
            'success': True,
            'timestamp': datetime.utcnow().isoformat(),
            'user': {
                'id': current_user.id,
                'nom': current_user.nom,
                'role': current_user.role,
                'ecole_id': ecole_id,
                'professeur_id': professeur.id
            },
            'annee_active': {
                'id': annee_active.id if annee_active else None,
                'nom': annee_active.nom if annee_active else "Année en cours"
            },
            'classes': classes_data,
            'cours': cours_data,
            'eleves': eleves_data,
            'periodes': periodes,
            'types_evaluation': ['Devoir', 'Interrogation', 'Examen', 'TP']
        })
        response.headers['Cache-Control'] = 'private, no-cache, no-store, must-revalidate'
        return response, 200

    except Exception as e:
        current_app.logger.exception("Erreur récupération données hors-ligne professeur")
        return jsonify({
            'success': False,
            'message': "Une erreur interne est survenue lors de la récupération des données hors-ligne."
        }), 500


@main.route('/api/admin/offline-data', methods=['GET'])
@login_required
@role_required('admin')
def api_admin_offline_data():
    """
    Fournit les données indispensables au travail hors-ligne de l'administrateur:
    - Classes de son établissement
    - Cours dispensés avec professeurs assignés
    - Élèves de l'établissement (avec filtrage optionnel par classe_id=X et payload compact)
    - Support du cache conditionnel HTTP via ETag (304 Not Modified)
    - Année scolaire active & périodes
    """
    try:
        ecole_id = current_user.ecole_id

        # Paramètre optionnel de filtrage par classe pour optimiser les gros établissements
        classe_id_param = request.args.get('classe_id', type=int)

        # 1. Année scolaire active
        annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut='active').first()

        # 2. Classes de l'établissement
        classes_query = Classe.query.filter_by(ecole_id=ecole_id).order_by(Classe.nom).all()
        classes_data = [
            {
                'id': cl.id,
                'nom': cl.nom,
                'niveau': getattr(cl, 'niveau', ''),
                'effectif': len(cl.eleves) if hasattr(cl, 'eleves') else 0
            }
            for cl in classes_query
        ]

        # 3. Cours de l'établissement avec professeurs associés
        cours_q = Cours.query.filter_by(ecole_id=ecole_id)
        if classe_id_param:
            cours_q = cours_q.filter_by(classe_id=classe_id_param)
        cours_query = cours_q.order_by(Cours.nom).all()
        cours_data = [
            {
                'id': c.id,
                'nom': c.nom,
                'code': getattr(c, 'code', None) or c.nom,
                'classe_id': c.classe_id,
                'classe_nom': c.classe.nom if c.classe else None,
                'professeur_id': c.professeur_id,
                'professeur_nom': f"{c.professeur.nom} {c.professeur.prenom}" if c.professeur else None,
                'coefficient': getattr(c, 'coefficient', 1.0)
            }
            for c in cours_query
        ]

        annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()
        annee_id = annee_active.id if annee_active else None

        inscriptions_q = Inscription.query.filter(
            Inscription.ecole_id == ecole_id,
            Inscription.statut != 'desinscrit'
        )
        if annee_id:
            inscriptions_q = inscriptions_q.filter(Inscription.annee_scolaire_id == annee_id)
        if classe_id_param:
            inscriptions_q = inscriptions_q.filter(Inscription.classe_id == classe_id_param)

        inscriptions = inscriptions_q.options(db.joinedload(Inscription.eleve), db.joinedload(Inscription.classe)).all()
        seen_eleve_ids = set()
        eleves_data = []
        for insc in inscriptions:
            e = insc.eleve
            if e and e.id not in seen_eleve_ids:
                seen_eleve_ids.add(e.id)
                eleves_data.append({
                    'id': e.id,
                    'nom': e.nom,
                    'prenom': e.prenom,
                    'matricule': getattr(e, 'matricule', None) or f"EL-{e.id}",
                    'classe_id': insc.classe_id,
                    'classe_nom': insc.classe.nom if insc.classe else None
                })

        # 5. Périodes d'évaluation & types
        periodes = ['Trimestre 1', 'Trimestre 2', 'Trimestre 3', 'Semestre 1', 'Semestre 2']

        # Calcul ETag pour mise en cache conditionnelle HTTP 304
        etag_data = {
            'e': ecole_id,
            'c': classe_id_param,
            'cl_count': len(classes_data),
            'co_count': len(cours_data),
            'el_count': len(eleves_data),
            'a': annee_active.id if annee_active else 0
        }
        etag = f'"{hashlib.md5(json.dumps(etag_data, sort_keys=True).encode("utf-8")).hexdigest()}"'

        if_none_match = request.headers.get('If-None-Match')
        if if_none_match and if_none_match.strip() == etag:
            res = current_app.response_class('', status=304)
            res.headers['ETag'] = etag
            res.headers['Cache-Control'] = 'private, no-cache, must-revalidate'
            return res

        payload = {
            'success': True,
            'timestamp': datetime.utcnow().isoformat(),
            'classe_id_filtered': classe_id_param,
            'user': {
                'id': current_user.id,
                'nom': current_user.nom,
                'prenom': getattr(current_user, 'prenom', ''),
                'role': current_user.role,
                'ecole_id': ecole_id
            },
            'annee_active': {
                'id': annee_active.id if annee_active else None,
                'nom': annee_active.nom if annee_active else "Année en cours"
            },
            'classes': classes_data,
            'cours': cours_data,
            'eleves': eleves_data,
            'periodes': periodes,
            'types_evaluation': ['Devoir', 'Interrogation', 'Examen', 'TP']
        }

        response = jsonify(payload)
        response.headers['ETag'] = etag
        response.headers['Cache-Control'] = 'private, no-cache, must-revalidate'
        return response, 200

    except Exception as e:
        current_app.logger.exception("Erreur récupération données hors-ligne administrateur")
        return jsonify({
            'success': False,
            'message': "Une erreur interne est survenue lors de la récupération des données administrateur."
        }), 500


@main.route('/api/parent/offline-data', methods=['GET'])
@login_required
@role_required('parent')
def api_parent_offline_data():
    """
    Fournit les données indispensables au travail / consultation hors-ligne du parent:
    - Ses propres enfants uniquement
    - Leurs classes, cours, notes et absences
    """
    try:
        ecole_id = current_user.ecole_id
        enfants = Eleve.query.filter_by(parent_id=current_user.id, ecole_id=ecole_id).all()

        enfants_data = []
        for e in enfants:
            enfants_data.append({
                'id': e.id,
                'nom': e.nom,
                'prenom': e.prenom,
                'classe_nom': e.classe.nom if e.classe else None,
                'classe_id': e.classe_id,
                'notes': [
                    {
                        'id': n.id,
                        'cours_nom': n.cours.nom if n.cours else None,
                        'valeur': n.valeur,
                        'coefficient': n.coefficient,
                        'type_evaluation': n.type_evaluation,
                        'periode': n.periode,
                        'date_evaluation': n.date_evaluation.isoformat() if n.date_evaluation else None
                    }
                    for n in e.notes
                ],
                'absences': [
                    {
                        'id': a.id,
                        'date_absence': a.date_absence.isoformat() if a.date_absence else None,
                        'motif': a.motif,
                        'justifiee': a.justifiee
                    }
                    for a in e.absences
                ]
            })

        response = jsonify({
            'success': True,
            'timestamp': datetime.utcnow().isoformat(),
            'user': {
                'id': current_user.id,
                'nom': current_user.nom,
                'role': 'parent',
                'ecole_id': ecole_id
            },
            'enfants': enfants_data
        })
        response.headers['Cache-Control'] = 'private, no-cache, no-store, must-revalidate'
        return response, 200

    except Exception as e:
        current_app.logger.exception("Erreur récupération données hors-ligne parent")
        return jsonify({
            'success': False,
            'message': "Une erreur interne est survenue lors de la récupération des données parent."
        }), 500


@main.route('/manifest.json')
def manifest_json():
    """Servir le Web App Manifest PWA"""
    response = send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response


@main.route('/service-worker.js')
def service_worker():
    """Servir le Service Worker avec portée globale"""
    response = send_from_directory('static', 'service-worker.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@main.route('/offline')
def offline_page():
    """Page affichée quand l'utilisateur est hors ligne"""
    return render_template('offline.html')


@main.route("/recherche_json")
@login_required
@role_required('admin')
def recherche_json():
    inscription_id = request.args.get("inscription_id", type=int)
    if not inscription_id:
        return {"error": "inscription_id manquant"}, 400

    ins = (
        Inscription.query
        .join(Eleve)
        .filter(Inscription.id == inscription_id, Eleve.ecole_id == current_user.ecole_id)
        .first()
    )
    if not ins:
        return {"error": "Inscription introuvable"}, 404

    eleve = ins.eleve

    annee_premiere_ecole = "N/A"
    if eleve and eleve.inscriptions:
        premiere_inscription = min(
            [i for i in eleve.inscriptions if i.annee_scolaire],
            key=lambda i: i.annee_scolaire.date_debut,
            default=None
        )
        if premiere_inscription and premiere_inscription.annee_scolaire:
            annee_premiere_ecole = premiere_inscription.annee_scolaire.nom

    return {
        "id": ins.id,
        "eleve_prenom": eleve.prenom if eleve else None,
        "eleve_nom": eleve.nom if eleve else None,
        "classe": ins.classe.nom if ins.classe else None,
        "annee_scolaire": ins.annee_scolaire.nom if ins.annee_scolaire else None,
        "parent_nom": eleve.parent.nom if eleve and eleve.parent else None,
        "annee_premiere_ecole": annee_premiere_ecole,
        "notes": [
            {
                "cours": note.cours.nom if note.cours else None,
                "valeur": note.valeur,
                "periode": note.periode
            }
            for note in eleve.notes
        ]
        if eleve and eleve.notes
        else []
    }
