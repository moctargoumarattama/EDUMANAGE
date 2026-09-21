"""Helpers metier extraits des anciennes routes.

Ce module remplace les petits fichiers *_service.py pour reduire le nombre de
fichiers sans deplacer cette logique dans les routes elles-memes.
"""

import hashlib
import os
import threading
import uuid
from datetime import datetime

from flask import current_app, flash, url_for
from flask_login import current_user
from sqlalchemy import func

from app import db
from app.models import Absence, Cours, Eleve, Inscription, Note, Paiement, Professeur
from app.notifications import envoyer_email
from app.utils import get_ecole_filter_query
from app.services.paiements_annuels import get_mois_scolaires


_stats_cache = {}
CACHE_DURATION = 60
PER_PAGE_ALERTES = 10


def get_cache(user_id, key):
    """Retourne le cache si valide pour un utilisateur."""
    now = datetime.now()
    if user_id in _stats_cache and key in _stats_cache[user_id]:
        data, timestamp = _stats_cache[user_id][key]
        if (now - timestamp).total_seconds() < CACHE_DURATION:
            return data
    return None


def set_cache(user_id, key, data):
    """Enregistre les donnees dans le cache pour un utilisateur."""
    now = datetime.now()
    if user_id not in _stats_cache:
        _stats_cache[user_id] = {}
    _stats_cache[user_id][key] = (data, now)


def check_ecole_access(obj, objet_type="generic"):
    """
    Verifie que l'objet (Eleve, Professeur, Cours) appartient a l'ecole
    de l'utilisateur. Renvoie True si acces autorise, False sinon.
    """
    if current_user.role == 'admin':
        if getattr(obj, 'ecole_id', None) != current_user.ecole_id:
            flash(f"Acces refuse : {objet_type} d'une autre ecole", "danger")
            return False
    elif current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        professeur_id = getattr(professeur, 'id', None)
        if not professeur_id:
            flash("Acces non autorise : profil professeur introuvable", "danger")
            return False
        if isinstance(obj, Cours) and obj.professeur_id != professeur_id:
            flash("Acces non autorise : cours non lie", "danger")
            return False
        if isinstance(obj, Eleve):
            cours_ids = [c.id for c in Cours.query.filter_by(professeur_id=professeur_id).all()]
            eleve_ids = [n.eleve_id for n in Note.query.filter(Note.cours_id.in_(cours_ids)).all()]
            if obj.id not in eleve_ids:
                flash("Acces non autorise : eleve non lie a vos cours", "danger")
                return False
    elif current_user.role == 'parent':
        if getattr(obj, 'parent_id', None) != current_user.id:
            flash("Acces refuse : eleve non lie a ce parent", "danger")
            return False
    return True


def get_qr_cache_path(eleve):
    base_dir = os.path.join(current_app.root_path, "static", "qrcache")
    os.makedirs(base_dir, exist_ok=True)

    key = f"{eleve.id}-{eleve.updated_at}".encode()
    filename = hashlib.md5(key).hexdigest() + ".png"

    return os.path.join(base_dir, filename)


def get_statistics(classes):
    """Helper function to calculate statistics."""
    if not classes:
        return {
            'total_eleves': 0,
            'moyenne_effectif': 0,
            'classes_pleines': 0
        }

    # Précharger en lot si certaines classes n'ont pas encore leur effectif annuel injecté
    classes_list = list(classes)
    non_precharges = [c for c in classes_list if getattr(c, '_effectif_annuel', None) is None and 'inscriptions' not in c.__dict__]
    if non_precharges:
        from app.services.classes_annuelles import precharger_effectifs_classes
        precharger_effectifs_classes(classes_list)

    total_eleves = sum(c.effectif_reel for c in classes_list)
    moyenne_effectif = int(total_eleves / len(classes_list)) if classes_list else 0
    classes_pleines = sum(1 for c in classes_list if c.est_pleine)

    return {
        'total_eleves': total_eleves,
        'moyenne_effectif': moyenne_effectif,
        'classes_pleines': classes_pleines
    }


def generer_alertes_automatiques(ecole_id=None, annee=None, limit=None):
    """
    Génère les alertes scolaires pour une école et une année scolaire donnée.
    Respecte l'ancrage annuel strict : Inscription -> Année consultée.
    """
    from app.utils import get_annee_consultee
    from app.models import AnneeScolaire

    if ecole_id is None:
        ecole_id = getattr(current_user, 'ecole_id', None)

    if not ecole_id:
        return []

    if annee is None:
        annee = get_annee_consultee(ecole_id)

    if not annee:
        return []

    # Année planifiée : préparation uniquement, aucune alerte d'absence, note ou impayé
    if annee.statut == 'planifiee':
        return []

    alertes = []
    maintenant = datetime.now()

    # Construction de la timeline scolaire
    mois_scolaires_liste = get_mois_scolaires(annee)
    month_to_num = {'Janvier': 1, 'Février': 2, 'Mars': 3, 'Avril': 4, 'Mai': 5, 'Juin': 6, 'Juillet': 7, 'Août': 8, 'Septembre': 9, 'Octobre': 10, 'Novembre': 11, 'Décembre': 12}
    timeline = []
    for m_name in mois_scolaires_liste:
        m_num = month_to_num.get(m_name, 1)
        y = annee.date_debut.year if m_num >= 8 else annee.date_fin.year
        timeline.append((m_name, y, m_num))

    is_archivee = (annee.statut == 'archivee')

    # Inscriptions de l'année consultée pour cette école
    inscriptions = (
        Inscription.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
        .options(
            db.joinedload(Inscription.eleve),
            db.joinedload(Inscription.classe),
            db.joinedload(Inscription.notes),
            db.joinedload(Inscription.absences),
            db.joinedload(Inscription.paiements),
        )
        .all()
    )

    def _safe_url_eleve(eleve_id):
        try:
            return url_for('main.voir_eleve', eleve_id=eleve_id)
        except RuntimeError:
            return f"/eleve/{eleve_id}"

    for ins in inscriptions:
        eleve = ins.eleve
        if not eleve:
            continue

        classe_nom = ins.classe.nom if ins.classe else "Sans classe"
        classe_id = ins.classe_id

        # 1. Alertes Notes (Difficultés académiques de l'année consultée)
        notes = [n for n in ins.notes if n.valeur is not None]
        if notes:
            total_pondere = sum(n.valeur * (n.coefficient or 1.0) for n in notes)
            total_coefficients = sum((n.coefficient or 1.0) for n in notes)
            moyenne = round(total_pondere / total_coefficients, 2) if total_coefficients > 0 else 0
            if moyenne < 10:
                type_alerte = 'danger' if moyenne < 8 else 'warning'
                priorite = 3 if moyenne < 8 else 2
                alertes.append({
                    'id': f'note-{ins.id}',
                    'type': type_alerte,
                    'titre': 'Difficulté académique',
                    'message': f"{eleve.prenom} {eleve.nom} ({classe_nom}) a une moyenne générale de {moyenne}/20.",
                    'date': maintenant,
                    'source': 'Notes',
                    'lien': _safe_url_eleve(eleve.id),
                    'eleve_id': eleve.id,
                    'inscription_id': ins.id,
                    'eleve_nom': f"{eleve.prenom} {eleve.nom}",
                    'classe_id': classe_id,
                    'classe_nom': classe_nom,
                    'contact_parent': eleve.contact_parent or eleve.telephone or '',
                    'email_parent': eleve.email_parent or eleve.email or '',
                    'priorite': priorite,
                    'valeur_cle': f"{moyenne}/20",
                    'notifie': False,
                    'historique': is_archivee,
                })

        # 2. Alertes Absences (Absences répétées non justifiées de l'année consultée)
        absences_injustifiees = [a for a in ins.absences if not a.justifiee]
        total_absences = len(absences_injustifiees)
        if total_absences >= 3:
            type_alerte = 'danger' if total_absences >= 5 else 'warning'
            priorite = 3 if total_absences >= 5 else 2
            alertes.append({
                'id': f'absence-{ins.id}',
                'type': type_alerte,
                'titre': 'Absences répétées non justifiées',
                'message': f"{eleve.prenom} {eleve.nom} ({classe_nom}) compte {total_absences} absence(s) non justifiée(s).",
                'date': maintenant,
                'source': 'Absences',
                'lien': _safe_url_eleve(eleve.id),
                'eleve_id': eleve.id,
                'inscription_id': ins.id,
                'eleve_nom': f"{eleve.prenom} {eleve.nom}",
                'classe_id': classe_id,
                'classe_nom': classe_nom,
                'contact_parent': eleve.contact_parent or eleve.telephone or '',
                'email_parent': eleve.email_parent or eleve.email or '',
                'priorite': priorite,
                'valeur_cle': f"{total_absences} absence(s)",
                'notifie': False,
                'historique': is_archivee,
            })

        # 3. Alertes Paiements (Retards de scolarité de l'année consultée)
        frais_annuels = float(ins.frais_annuels if ins.frais_annuels is not None else (eleve.frais_annuels or 150000.0))
        paiements_valides = [p for p in ins.paiements if p.statut not in ('rejete', 'annule')]
        total_paye = sum(float(p.montant or 0) for p in paiements_valides)
        mois_payes = [p.mois for p in paiements_valides if p.mois]

        if is_archivee:
            if total_paye < frais_annuels:
                montant_du = round(frais_annuels - total_paye)
                alertes.append({
                    'id': f'paiement-{ins.id}',
                    'type': 'warning',
                    'titre': 'Retard de paiement de scolarité',
                    'message': f"{eleve.prenom} {eleve.nom} ({classe_nom}) a un impayé de scolarité ({montant_du:,.0f} FCFA).",
                    'date': maintenant,
                    'source': 'Paiements',
                    'lien': _safe_url_eleve(eleve.id),
                    'eleve_id': eleve.id,
                    'inscription_id': ins.id,
                    'eleve_nom': f"{eleve.prenom} {eleve.nom}",
                    'classe_id': classe_id,
                    'classe_nom': classe_nom,
                    'contact_parent': eleve.contact_parent or eleve.telephone or '',
                    'email_parent': eleve.email_parent or eleve.email or '',
                    'priorite': 2,
                    'valeur_cle': f"{montant_du:,.0f} F",
                    'details': {
                        'mois_manquants': [],
                        'nombre_mois_manquants': 1,
                        'montant_total_du': montant_du
                    },
                    'notifie': False,
                    'historique': True,
                })
        else:
            mois_dus = []
            for m_name, y, m_num in timeline:
                if (y, m_num) <= (maintenant.year, maintenant.month):
                    mois_dus.append(m_name)

            mois_manquants = [m for m in mois_dus if m not in mois_payes]
            if mois_manquants and total_paye < frais_annuels:
                frais_mensuels = frais_annuels / len(timeline)
                montant_du = round(min(frais_annuels - total_paye, frais_mensuels * len(mois_manquants)))
                if montant_du > 0:
                    if len(mois_manquants) >= 3:
                        type_alerte = 'danger'
                        priorite = 3
                    elif len(mois_manquants) == 2:
                        type_alerte = 'warning'
                        priorite = 2
                    else:
                        type_alerte = 'info'
                        priorite = 1

                    if len(mois_manquants) == 1:
                        mois_texte = f"le mois de {mois_manquants[0]}"
                    elif len(mois_manquants) <= 3:
                        mois_texte = f"les mois de {', '.join(mois_manquants)}"
                    else:
                        mois_texte = f"{len(mois_manquants)} mois ({mois_manquants[0]} à {mois_manquants[-1]})"

                    alertes.append({
                        'id': f'paiement-{ins.id}',
                        'type': type_alerte,
                        'titre': 'Retard de paiement de scolarité',
                        'message': f"{eleve.prenom} {eleve.nom} ({classe_nom}) a un retard de scolarité pour {mois_texte} ({montant_du:,.0f} FCFA).",
                        'date': maintenant,
                        'source': 'Paiements',
                        'lien': _safe_url_eleve(eleve.id),
                        'eleve_id': eleve.id,
                        'inscription_id': ins.id,
                        'eleve_nom': f"{eleve.prenom} {eleve.nom}",
                        'classe_id': classe_id,
                        'classe_nom': classe_nom,
                        'contact_parent': eleve.contact_parent or eleve.telephone or '',
                        'email_parent': eleve.email_parent or eleve.email or '',
                        'priorite': priorite,
                        'valeur_cle': f"{len(mois_manquants)} mois ({montant_du:,.0f} F)",
                        'details': {
                            'mois_manquants': mois_manquants,
                            'nombre_mois_manquants': len(mois_manquants),
                            'montant_total_du': montant_du
                        },
                        'notifie': False,
                        'historique': False,
                    })

    alertes.sort(key=lambda x: (-x['priorite'], x['date']))

    if limit:
        return alertes[:limit]

    return alertes


def notifier_alertes(alertes):
    app = current_app._get_current_object()

    def worker(alertes_to_notify, flask_app):
        with flask_app.app_context():
            for a in alertes_to_notify:
                if a['type'] in ['danger', 'warning'] and not a.get('notifie'):
                    eleve = Eleve.query.get(a['eleve_id'])

                    message = (
                        f"{a['titre']}\n"
                        f"{a['message']}\n"
                        f"Source: {a['source']}"
                    )

                    email_ok = True
                    if eleve and eleve.email_parent:
                        email_ok = envoyer_email(
                            eleve.email_parent,
                            f"Alerte: {a['titre']}",
                            message,
                            context="alert_parent_notification",
                        )
                        if email_ok:
                            current_app.logger.info("EMAIL_SUCCESS_HANDLED type=alert_parent_notification recipient=%s", eleve.email_parent)
                        else:
                            current_app.logger.warning("EMAIL_FAILED_HANDLED type=alert_parent_notification recipient=%s", eleve.email_parent)

                    a['notifie'] = email_ok

            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    thread = threading.Thread(target=worker, args=(alertes, app))
    thread.start()


def _generer_logo_circulaire(logo_path_or_buf, size_px=160):
    """
    Rend une image de logo parfaitement circulaire avec fond transparent
    et un liseré élégant pour un rendu académique haut de gamme.

    Stratégie 'contain' (sans crop agressif ni débordement) :
    1. Ouvre l'image source et applique l'orientation EXIF.
    2. Convertit en RGBA.
    3. Rognage des marges 100% transparentes si présentes.
    4. Redimensionnement proportionnel (contain) pour que TOUT le contenu
       soit strictement inscrit dans le disque circulaire sans coupure.
    5. Centrage sur un disque circulaire blanc pur.
    6. Application d'un masque circulaire antialiisé (supersampling 2x).
    7. Ajout d'un fin contour circulaire ardoise (#CBD5E1) discret et net.
    """
    import io
    import math
    try:
        from PIL import Image as PILImage, ImageDraw, ImageOps

        if hasattr(logo_path_or_buf, 'seek'):
            logo_path_or_buf.seek(0)

        with PILImage.open(logo_path_or_buf) as orig:
            img = ImageOps.exif_transpose(orig)
            img = img.convert('RGBA')

            # Si l'image possède un canal alpha avec des marges vides, on les rogne
            alpha = img.split()[3]
            alpha_bbox = alpha.getbbox()
            if alpha_bbox:
                w_orig, h_orig = img.size
                crop_box = (
                    max(0, alpha_bbox[0] - 2),
                    max(0, alpha_bbox[1] - 2),
                    min(w_orig, alpha_bbox[2] + 2),
                    min(h_orig, alpha_bbox[3] + 2)
                )
                img = img.crop(crop_box)

            w, h = img.size
            if w <= 0 or h <= 0:
                return None

            # Détection de transparence ou de fond blanc aux 4 coins
            corners = [
                img.getpixel((0, 0)),
                img.getpixel((w - 1, 0)),
                img.getpixel((0, h - 1)),
                img.getpixel((w - 1, h - 1))
            ]
            is_transparent_corner = any(c[3] < 30 for c in corners)
            is_white_corner = all(c[0] > 240 and c[1] > 240 and c[2] > 240 for c in corners)
            is_square = abs(w - h) / max(w, h) < 0.08

            # Rayon utilisable : 88% du rayon du cercle pour un padding respirant et élégant
            r_usable = (size_px / 2.0) * 0.88

            # Si l'image est carrée avec des coins blancs ou transparents,
            # le cadrage proportionnel max(w, h) remplit harmonieusement le disque blanc.
            # Pour toute image rectangulaire ou photo opaque, le ratio diagonal
            # garantit mathématiquement que les 4 coins sont dans le cercle sans coupure.
            if (is_transparent_corner or is_white_corner) and is_square:
                scale = (2.0 * r_usable) / max(w, h)
            else:
                diag = math.hypot(w, h)
                scale = (2.0 * r_usable) / diag

            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))

            resized = img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)

            # Zone carrée sur fond blanc pur (disque interne)
            canvas = PILImage.new('RGBA', (size_px, size_px), (255, 255, 255, 255))
            pos_x = (size_px - new_w) // 2
            pos_y = (size_px - new_h) // 2
            canvas.paste(resized, (pos_x, pos_y), mask=resized)

            # Masque circulaire haute précision supersamplé (2x) pour bord antialiisé
            hi_size = size_px * 2
            mask = PILImage.new('L', (hi_size, hi_size), 0)
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.ellipse((0, 0, hi_size, hi_size), fill=255)
            mask = mask.resize((size_px, size_px), PILImage.Resampling.LANCZOS)

            # Application du masque : transparent à l'extérieur du cercle
            output = PILImage.new('RGBA', (size_px, size_px), (255, 255, 255, 0))
            output.paste(canvas, (0, 0), mask=mask)

            # Liseré circulaire discret et raffiné (#CBD5E1, slate-300)
            draw_border = ImageDraw.Draw(output)
            draw_border.ellipse((1, 1, size_px - 2, size_px - 2), outline=(203, 213, 225, 255), width=2)

            buf = io.BytesIO()
            output.save(buf, format='PNG')
            buf.seek(0)
            return buf
    except Exception:
        return None


def _generer_monogramme_circulaire(nom_ecole, size_px=160):
    """
    Génère un monogramme circulaire élégant aux couleurs académiques de l'école
    si aucun logo n'est téléversé, garantissant un rendu prestigieux universel.
    """
    import io
    try:
        from PIL import Image as PILImage, ImageDraw
        img = PILImage.new('RGBA', (size_px, size_px), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((2, 2, size_px - 3, size_px - 3), fill=(30, 58, 138, 255), outline=(147, 197, 253, 255), width=3)
        words = [w for w in (nom_ecole or 'KL').replace('-', ' ').split() if w]
        initials = ''.join(w[0].upper() for w in words[:2]) if words else 'KL'
        draw.text((size_px // 2, size_px // 2), initials, fill=(255, 255, 255, 255), anchor='mm')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return buf
    except Exception:
        return None


def generer_bulletin_pdf(
    eleve,
    notes_par_cours,
    moyennes_par_cours,
    moyenne_generale,
    logo_path=None,
    nom_ecole=None,
    adresse_ecole=None,
    contact_ecole=None,
    classe_nom=None,
    annee_scolaire_nom=None,
    periode_nom=None,
    rang=None,
    rang_total=None,
    appreciation_generale=None,
    disciplines=None,
    total_coefficients=None,
    total_points=None,
    stats_classe=None,
    nb_absences=None,
    est_provisoire=False,
    verification_url=None,
):
    """
    Génère un bulletin scolaire PDF moderne, prestigieux et adaptatif
    garanti STRICTEMENT sur 1 seule page A4 portrait, quel que soit le nombre de matières.
    """
    import io
    import os
    from datetime import datetime

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    # 1. Calcul du nombre de matières pour le dimensionnement adaptatif
    nb_matieres = len(disciplines) if disciplines is not None else len(moyennes_par_cours or {})
    if nb_matieres == 0:
        nb_matieres = 1

    def _obtenir_parametres_adaptatifs(nb, palier_force=None):
        palier = palier_force
        if palier is None:
            if nb <= 5:
                palier = 1
            elif nb <= 12:
                palier = 2
            elif nb <= 20:
                palier = 3
            else:
                palier = 4

        if palier == 1:
            # Palier 1 : Mode Prestige Aéré (1 à 5 matières) - Remplit 75 à 85% de la hauteur disponible
            if nb <= 3:
                return {
                    'palier': 1,
                    'margin_h': 30, 'margin_v': 24,
                    'logo_pt': 76,
                    'font_school_name': 19.5, 'font_school_sub': 9.0,
                    'font_title': 15.0, 'font_period': 11.0, 'badge_font': 8.5,
                    'font_student_name': 14.5, 'font_student_info': 10.0,
                    'student_padding_v': 22.0,
                    'font_tbl_head': 7.5, 'font_tbl_body': 10.5, 'tbl_padding_v': 24.0,
                    'font_totaux': 10.5, 'font_moy_titre': 12.5, 'font_moy_val': 20.0,
                    'moy_padding_v': 20.0,
                    'qr_pt': 92, 'font_obs_title': 8.5, 'font_obs_body': 10.2,
                    'obs_padding_v': 20.0,
                    'sp_hdr': 15, 'sp_stu': 16, 'sp_tbl': 16, 'sp_syn': 16
                }
            else:
                return {
                    'palier': 1,
                    'margin_h': 30, 'margin_v': 22,
                    'logo_pt': 70,
                    'font_school_name': 18.5, 'font_school_sub': 8.5,
                    'font_title': 14.0, 'font_period': 10.5, 'badge_font': 8.0,
                    'font_student_name': 13.5, 'font_student_info': 9.5,
                    'student_padding_v': 16.0,
                    'font_tbl_head': 8.0, 'font_tbl_body': 10.0, 'tbl_padding_v': 15.0,
                    'font_totaux': 9.5, 'font_moy_titre': 11.5, 'font_moy_val': 17.5,
                    'moy_padding_v': 16.0,
                    'qr_pt': 83, 'font_obs_title': 8.0, 'font_obs_body': 9.5,
                    'obs_padding_v': 16.0,
                    'sp_hdr': 13, 'sp_stu': 15, 'sp_tbl': 15, 'sp_syn': 15
                }
        elif palier == 2:
            # Palier 2 : Mode Standard Confortable (6 à 12 matières) - Suppression du vide en bas
            return {
                'palier': 2,
                'margin_h': 28, 'margin_v': 18,
                'logo_pt': 58,
                'font_school_name': 16.2, 'font_school_sub': 7.5,
                'font_title': 12.5, 'font_period': 9.5, 'badge_font': 7.0,
                'font_student_name': 11.5, 'font_student_info': 8.5,
                'student_padding_v': 10.5,
                'font_tbl_head': 7.4, 'font_tbl_body': 8.8, 'tbl_padding_v': 7.8,
                'font_totaux': 8.8, 'font_moy_titre': 10.5, 'font_moy_val': 15.5,
                'moy_padding_v': 10.5,
                'qr_pt': 70, 'font_obs_title': 7.2, 'font_obs_body': 8.5,
                'obs_padding_v': 10.5,
                'sp_hdr': 11, 'sp_stu': 13, 'sp_tbl': 13, 'sp_syn': 13
            }
        elif palier == 3:
            # Palier 3 : Mode Compact (13 à 17 matières)
            return {
                'palier': 3,
                'margin_h': 26, 'margin_v': 14,
                'logo_pt': 46,
                'font_school_name': 14.0, 'font_school_sub': 7.0,
                'font_title': 11.5, 'font_period': 8.8, 'badge_font': 6.5,
                'font_student_name': 10.5, 'font_student_info': 7.8,
                'student_padding_v': 5.0,
                'font_tbl_head': 7.0, 'font_tbl_body': 7.8, 'tbl_padding_v': 4.5,
                'font_totaux': 8.0, 'font_moy_titre': 9.2, 'font_moy_val': 13.0,
                'moy_padding_v': 5.0,
                'qr_pt': 54, 'font_obs_title': 6.5, 'font_obs_body': 7.5,
                'obs_padding_v': 5.5,
                'sp_hdr': 7, 'sp_stu': 8, 'sp_tbl': 8, 'sp_syn': 8
            }
        else:
            # Palier 4 : Mode Ultra-Compact (18+ matières)
            return {
                'palier': 4,
                'margin_h': 24, 'margin_v': 12,
                'logo_pt': 38,
                'font_school_name': 12.5, 'font_school_sub': 6.0,
                'font_title': 10.0, 'font_period': 8.0, 'badge_font': 5.8,
                'font_student_name': 9.5, 'font_student_info': 7.0,
                'student_padding_v': 2.5,
                'font_tbl_head': 6.2, 'font_tbl_body': 6.8, 'tbl_padding_v': 2.2,
                'font_totaux': 7.0, 'font_moy_titre': 8.0, 'font_moy_val': 10.5,
                'moy_padding_v': 3.0,
                'qr_pt': 43, 'font_obs_title': 5.5, 'font_obs_body': 6.5,
                'obs_padding_v': 3.5,
                'sp_hdr': 4, 'sp_stu': 4.5, 'sp_tbl': 4.5, 'sp_syn': 4.5
            }

    # Boucle de génération avec garantie stricte d'exactement 1 page A4
    for tentative_palier in [None, 2, 3, 4]:
        cfg = _obtenir_parametres_adaptatifs(nb_matieres, palier_force=tentative_palier)
        page_w, page_h = A4
        content_w = page_w - (2 * cfg['margin_h'])

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=cfg['margin_h'], rightMargin=cfg['margin_h'],
            topMargin=cfg['margin_v'], bottomMargin=cfg['margin_v']
        )

        styles = getSampleStyleSheet()
        elements = []

        c_navy = colors.HexColor('#1E3A8A')
        c_blue = colors.HexColor('#2563EB')
        c_amber = colors.HexColor('#D97706')

        # Résolution des coordonnées école
        s_nom = nom_ecole
        s_adr = adresse_ecole
        s_contact = contact_ecole
        if not s_nom or not s_adr or not s_contact:
            if hasattr(eleve, 'ecole') and eleve.ecole:
                s_nom = s_nom or eleve.ecole.nom
                s_adr = s_adr or eleve.ecole.adresse
                s_contact = s_contact or f"Tél: {eleve.ecole.telephone or '-'} &bull; Email: {eleve.ecole.email or '-'}"
            else:
                s_nom = s_nom or "ÉTABLISSEMENT SCOLAIRE"
                s_adr = s_adr or "Adresse de l'établissement"
                s_contact = s_contact or "-"

        # Logo circulaire ou monogramme de secours
        logo_buf = None
        if logo_path and os.path.exists(logo_path):
            logo_buf = _generer_logo_circulaire(logo_path, size_px=int(cfg['logo_pt'] * 2.5))
        if not logo_buf:
            logo_buf = _generer_monogramme_circulaire(s_nom, size_px=int(cfg['logo_pt'] * 2.5))

        logo_img = RLImage(logo_buf, width=cfg['logo_pt'], height=cfg['logo_pt'])

        contact_clean = (s_contact or '').replace(' - ', ' &bull; ')
        school_sub_text = f"{s_adr or ''}"
        if contact_clean and contact_clean != '-':
            school_sub_text += f" &bull; {contact_clean}" if school_sub_text else contact_clean

        s_nom_display = (s_nom or "ÉTABLISSEMENT SCOLAIRE").strip().upper()

        # Ajustement intelligent de la police si le nom d'établissement est exceptionnellement long
        font_school = cfg['font_school_name']
        if len(s_nom_display) > 55:
            font_school = max(font_school * 0.72, 9.5)
        elif len(s_nom_display) > 38:
            font_school = max(font_school * 0.84, 10.5)

        school_p = Paragraph(
            f"<font size='{font_school}' color='#1E3A8A'><b>{s_nom_display}</b></font><br/>"
            f"<font size='{cfg['font_school_sub']}' color='#64748B'>{school_sub_text}</font>",
            ParagraphStyle('SchoolInfo', parent=styles['Normal'], leading=font_school + 2.5)
        )

        per_str = str(periode_nom or "1er Semestre")
        if "1" in per_str:
            per_display = "1ER SEMESTRE"
        elif "2" in per_str:
            per_display = "2ÈME SEMESTRE"
        else:
            per_display = per_str.upper()

        annee_display = str(annee_scolaire_nom or "")

        if est_provisoire:
            badge_html = f"<font size='{cfg['badge_font']}' color='#B45309'><b>● BULLETIN PROVISOIRE</b></font>"
        else:
            badge_html = f"<font size='{cfg['badge_font']}' color='#047857'><b>● BULLETIN OFFICIEL</b></font>"

        title_html = (
            f"{badge_html}<br/>"
            f"<font size='{cfg['font_title']}' color='#1E3A8A'><b>BULLETIN DE NOTES</b></font><br/>"
            f"<font size='{cfg['font_period']}' color='#475569'><b>{per_display}</b>"
            f"{' &bull; ' + annee_display if annee_display else ''}</font>"
        )
        title_p = Paragraph(
            title_html,
            ParagraphStyle('TitleRight', parent=styles['Normal'], alignment=2, leading=cfg['font_title'] + 2.5)
        )

        header_col_logo = cfg['logo_pt'] + 8
        header_col_title = 160
        header_col_school = content_w - (header_col_logo + header_col_title)

        header_table = Table(
            [[logo_img, school_p, title_p]],
            colWidths=[header_col_logo, header_col_school, header_col_title]
        )
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, cfg['sp_hdr']))

        # Ligne de séparation moderne
        divider = Table([['']], colWidths=[content_w], rowHeights=[1.5])
        divider.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), c_blue),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(divider)
        elements.append(Spacer(1, cfg['sp_hdr']))

        # SECTION 2 : CARTE IDENTITÉ ÉLÈVE (STYLE ACADÉMIQUE AVANCÉ)
        nom_prenom = f"{eleve.nom} {eleve.prenom}".strip().upper() if eleve else "ÉLÈVE INCONNU"
        matricule = getattr(eleve, 'matricule', None) or getattr(eleve, 'code_parent', None) or (f"#{eleve.id}" if hasattr(eleve, 'id') and eleve.id else "-")
        classe_affichee = classe_nom or "Non renseignée"
        date_nais = eleve.date_naissance.strftime('%d/%m/%Y') if (eleve and hasattr(eleve, 'date_naissance') and eleve.date_naissance) else "—"

        eff_classe = stats_classe.get('effectif_classe', rang_total) if stats_classe else (rang_total or "—")

        if est_provisoire or rang is None:
            rang_display = "En attente"
        else:
            rang_num_str = "1er" if rang == 1 else f"{rang}e"
            if eff_classe and str(eff_classe).strip() not in ("—", "", "None", "0"):
                eff_int = int(eff_classe) if str(eff_classe).isdigit() else None
                if eff_int is not None:
                    rang_display = f"{rang_num_str} sur {eff_int} élève{'s' if eff_int > 1 else ''}"
                else:
                    rang_display = f"{rang_num_str} sur {eff_classe}"
            else:
                rang_display = rang_num_str

        stu_left = (
            f"<font size='{cfg['font_student_name']}' color='#0F172A'><b>{nom_prenom}</b></font><br/>"
            f"<font size='{cfg['font_student_info']}' color='#64748B'>Matricule : </font>"
            f"<font size='{cfg['font_student_info']}' color='#1E40AF'><b>{matricule}</b></font>"
            f" &nbsp;&bull;&nbsp; "
            f"<font size='{cfg['font_student_info']}' color='#64748B'>Né(e) le : </font>"
            f"<font size='{cfg['font_student_info']}' color='#1E293B'><b>{date_nais}</b></font>"
        )
        stu_right = (
            f"<font size='{cfg['font_student_name']}' color='#1E3A8A'><b>Classe : {classe_affichee}</b></font><br/>"
            f"<font size='{cfg['font_student_info']}' color='#64748B'>Rang : </font>"
            f"<font size='{cfg['font_student_info']}' color='#0F172A'><b>{rang_display}</b></font>"
        )

        stu_p_left = Paragraph(stu_left, ParagraphStyle('StuLeft', parent=styles['Normal'], leading=cfg['font_student_name'] + 2.5))
        stu_p_right = Paragraph(stu_right, ParagraphStyle('StuRight', parent=styles['Normal'], alignment=2, leading=cfg['font_student_name'] + 2.5))

        student_card = Table(
            [[stu_p_left, stu_p_right]],
            colWidths=[content_w * 0.56, content_w * 0.44]
        )
        student_card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
            ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#CBD5E1')),
            ('LINEBEFORE', (0, 0), (0, -1), 3.5, colors.HexColor('#2563EB')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), cfg['student_padding_v']),
            ('BOTTOMPADDING', (0, 0), (-1, -1), cfg['student_padding_v']),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(student_card)
        elements.append(Spacer(1, cfg['sp_stu']))

        # SECTION 3 : TABLEAU DES NOTES (DISCIPLINES SANS NOM DE PROFESSEUR)
        cell_l = ParagraphStyle('TblLeft', parent=styles['Normal'], fontSize=cfg['font_tbl_body'], leading=cfg['font_tbl_body'] + 1.5, alignment=0)
        cell_c = ParagraphStyle('TblCenter', parent=styles['Normal'], fontSize=cfg['font_tbl_body'], leading=cfg['font_tbl_body'] + 1.5, alignment=1)
        cell_head = ParagraphStyle('TblHead', parent=styles['Normal'], fontSize=cfg['font_tbl_head'], leading=cfg['font_tbl_head'] + 1.5, alignment=1, fontName='Helvetica-Bold', textColor=colors.white)
        cell_head_l = ParagraphStyle('TblHeadL', parent=styles['Normal'], fontSize=cfg['font_tbl_head'], leading=cfg['font_tbl_head'] + 1.5, alignment=0, fontName='Helvetica-Bold', textColor=colors.white)

        col_w_disc = content_w * 0.30
        col_w_mc = content_w * 0.115
        col_w_comp = content_w * 0.085
        col_w_ms = content_w * 0.11
        col_w_coef = content_w * 0.07
        col_w_pts = content_w * 0.08
        col_w_app = content_w * 0.24

        tbl_col_widths = [col_w_disc, col_w_mc, col_w_comp, col_w_ms, col_w_coef, col_w_pts, col_w_app]

        data = [[
            Paragraph("<b>DISCIPLINE</b>", cell_head_l),
            Paragraph("<b>CONTRÔLE</b>", cell_head),
            Paragraph("<b>COMP.</b>", cell_head),
            Paragraph("<b>MOYENNE</b>", cell_head),
            Paragraph("<b>COEF.</b>", cell_head),
            Paragraph("<b>POINTS</b>", cell_head),
            Paragraph("<b>APPRÉCIATION</b>", cell_head)
        ]]

        if disciplines is not None:
            for d in disciplines:
                c_nom = d.get('cours_nom') or 'Matière'
                disc_html = f"<b>{c_nom}</b>"

                mc = f"{d['moyenne_controles']:.2f}" if d.get('moyenne_controles') is not None else "—"
                comp = f"{d['note_composition']:.2f}" if d.get('note_composition') is not None else "—"
                ms = f"{d['moyenne_semestre']:.2f}" if d.get('moyenne_semestre') is not None else "En attente"
                coef = f"{d.get('coefficient', 1.0)}"
                pts = f"{d['points']:.2f}" if d.get('points') is not None else "—"
                app = d.get('appreciation', '—')

                ms_cell_html = f"<b>{ms}</b>" if ms != "En attente" else f"<font color='#D97706'>{ms}</font>"

                data.append([
                    Paragraph(disc_html, cell_l),
                    Paragraph(mc, cell_c),
                    Paragraph(comp, cell_c),
                    Paragraph(ms_cell_html, cell_c),
                    Paragraph(coef, cell_c),
                    Paragraph(pts, cell_c),
                    Paragraph(app, cell_c)
                ])
        else:
            for cours in sorted(moyennes_par_cours.keys(), key=lambda x: (x or "").lower()):
                moyenne = moyennes_par_cours.get(cours, 0) or 0.0
                apprec = "Très bien" if moyenne >= 14 else ("Bien" if moyenne >= 12 else ("Passable" if moyenne >= 10 else "Insuffisant"))
                data.append([
                    Paragraph(f"<b>{cours}</b>", cell_l),
                    Paragraph(f"{moyenne:.2f}", cell_c),
                    Paragraph("—", cell_c),
                    Paragraph(f"<b>{moyenne:.2f}</b>", cell_c),
                    Paragraph("1.0", cell_c),
                    Paragraph(f"{moyenne:.2f}", cell_c),
                    Paragraph(apprec, cell_c)
                ])

        # Ligne TOTAL
        tot_coef_str = f"{total_coefficients}" if total_coefficients is not None else "—"
        tot_pts_str = f"{total_points:.2f}" if total_points is not None else "—"

        data.append([
            Paragraph(f"<font size='{cfg['font_totaux']}'><b>TOTAL DES COEFFICIENTS & POINTS</b></font>", cell_l),
            "", "", "",
            Paragraph(f"<font size='{cfg['font_totaux']}'><b>{tot_coef_str}</b></font>", cell_c),
            Paragraph(f"<font size='{cfg['font_totaux']}'><b>{tot_pts_str}</b></font>", cell_c),
            ""
        ])

        notes_table = Table(data, colWidths=tbl_col_widths)
        t_style = [
            ('BACKGROUND', (0, 0), (-1, 0), c_navy),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, 0), cfg['tbl_padding_v'] + 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, 0), cfg['tbl_padding_v'] + 1.5),
            ('TOPPADDING', (0, 1), (-1, -1), cfg['tbl_padding_v']),
            ('BOTTOMPADDING', (0, 1), (-1, -1), cfg['tbl_padding_v']),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('ROWBACKGROUNDS', (1, 1), (-1, -2), [colors.white, colors.HexColor('#F8FAFC')]),
            ('LINEBELOW', (0, 1), (-1, -3), 0.5, colors.HexColor('#E2E8F0')),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F1F5F9')),
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#94A3B8')),
            ('SPAN', (0, -1), (3, -1)),
        ]
        notes_table.setStyle(TableStyle(t_style))
        elements.append(notes_table)
        elements.append(Spacer(1, cfg['sp_tbl']))

        # SECTION 4 : BANNIÈRE MOYENNE GÉNÉRALE DU SEMESTRE
        if est_provisoire:
            titre_moy = "MOYENNE PROVISOIRE DU SEMESTRE"
            moy_gen_str = f"{moyenne_generale:.2f} / 20" if moyenne_generale is not None else "En attente"
            color_moy_box = colors.HexColor('#FFFBEB')
            border_moy_box = c_amber
            mention_str = "En attente"
        else:
            titre_moy = "MOYENNE GÉNÉRALE DU SEMESTRE"
            moy_gen_str = f"{moyenne_generale:.2f} / 20" if moyenne_generale is not None else "Non évalué"
            color_moy_box = colors.HexColor('#EFF6FF')
            border_moy_box = c_blue
            if moyenne_generale is not None:
                if moyenne_generale >= 16:
                    mention_str = "Très bien"
                elif moyenne_generale >= 14:
                    mention_str = "Bien"
                elif moyenne_generale >= 12:
                    mention_str = "Assez bien"
                elif moyenne_generale >= 10:
                    mention_str = "Passable"
                else:
                    mention_str = "Insuffisant"
            else:
                mention_str = "Non évalué"

        moy_left_html = (
            f"<font size='{cfg['font_moy_titre']}' color='#0F172A'><b>{titre_moy}</b></font><br/>"
            f"<font size='{cfg['font_student_info']}' color='#64748B'>Rang : </font>"
            f"<font size='{cfg['font_student_info']}' color='#1E3A8A'><b>{rang_display}</b></font>"
            f" &nbsp;&bull;&nbsp; "
            f"<font size='{cfg['font_student_info']}' color='#64748B'>Mention : </font>"
            f"<font size='{cfg['font_student_info']}' color='#0F172A'><b>{mention_str}</b></font>"
        )
        moy_right_html = (
            f"<font size='{cfg['font_moy_val']}' color='{border_moy_box.hexval()}'><b>{moy_gen_str}</b></font>"
        )

        moy_p_left = Paragraph(moy_left_html, ParagraphStyle('MoyL', parent=styles['Normal'], leading=cfg['font_moy_titre'] + 2.5))
        moy_p_right = Paragraph(moy_right_html, ParagraphStyle('MoyR', parent=styles['Normal'], alignment=2, leading=cfg['font_moy_val'] + 2.5))

        moy_table = Table(
            [[moy_p_left, moy_p_right]],
            colWidths=[content_w * 0.64, content_w * 0.36]
        )
        moy_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), color_moy_box),
            ('BOX', (0, 0), (-1, -1), 1.2, border_moy_box),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), cfg['moy_padding_v']),
            ('BOTTOMPADDING', (0, 0), (-1, -1), cfg['moy_padding_v']),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(moy_table)
        elements.append(Spacer(1, cfg['sp_syn']))

        # SECTION 5 : OBSERVATIONS ET QR CODE DE CERTIFICATION (LÉGENDE ÉPURÉE)
        obs_text = appreciation_generale if appreciation_generale else (
            "Travail satisfaisant" if (moyenne_generale and moyenne_generale >= 10) else "Doit redoubler d'efforts"
        )
        obs_html = f"<font size='{cfg['font_obs_title']}' color='#64748B'><b>OBSERVATIONS & ASSIDUITÉ</b></font><br/>"
        obs_html += f"<font size='{cfg['font_obs_body']}' color='#1E293B'><i>{obs_text}</i></font>"
        if nb_absences is not None:
            obs_html += f"<br/><font size='{cfg['font_obs_body']}' color='#64748B'>Assiduité : </font><font size='{cfg['font_obs_body']}' color='#0F172A'><b>{nb_absences} absence(s)</b></font>"

        obs_p = Paragraph(obs_html, ParagraphStyle('ObsP', parent=styles['Normal'], leading=cfg['font_obs_body'] + 2.5))

        footer_w_qr = 200 if nb_matieres <= 3 else (190 if nb_matieres <= 5 else (175 if nb_matieres <= 12 else 150))
        footer_w_obs = content_w - footer_w_qr

        if verification_url:
            from app.services.bulletin_verification import generer_qr_code_buffer
            qr_buf = generer_qr_code_buffer(verification_url)
            qr_img = RLImage(qr_buf, width=cfg['qr_pt'], height=cfg['qr_pt'])
            qr_sub_size = max(cfg['font_obs_title'] - 0.5, 5.2)

            if est_provisoire:
                qr_sub_txt = "Document vérifiable"
            else:
                qr_sub_txt = "Document officiel vérifiable"

            qr_txt = (
                f"<font size='{cfg['font_obs_title']}' color='#1E3A8A'><b>Scanner pour vérifier</b></font><br/>"
                f"<font size='{cfg['font_obs_title']}' color='#1E3A8A'><b>l’authenticité</b></font><br/>"
                f"<font size='{qr_sub_size}' color='#64748B'><i>{qr_sub_txt}</i></font>"
            )
            qr_p = Paragraph(qr_txt, ParagraphStyle('QrT', parent=styles['Normal'], alignment=1, leading=cfg['font_obs_title'] + 1.8))
            qr_content = Table([[qr_img, qr_p]], colWidths=[cfg['qr_pt'] + 6, footer_w_qr - (cfg['qr_pt'] + 14)])
            qr_content.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            footer_data = [[obs_p, qr_content]]
        else:
            if est_provisoire:
                cert_title = "DOCUMENT PROVISOIRE"
                cert_sub = "Document vérifiable"
            else:
                cert_title = "DOCUMENT OFFICIEL"
                cert_sub = "Authenticité certifiée"

            cert_p = Paragraph(
                f"<font size='{cfg['font_obs_title']}' color='#1E3A8A'><b>{cert_title}</b></font><br/>"
                f"<font size='{cfg['font_obs_body']}' color='#64748B'><i>{cert_sub}</i></font>",
                ParagraphStyle('CertP', parent=styles['Normal'], alignment=1, leading=cfg['font_obs_body'] + 2)
            )
            footer_data = [[obs_p, cert_p]]

        footer_table = Table(footer_data, colWidths=[footer_w_obs, footer_w_qr])
        footer_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#F8FAFC')),
            ('BOX', (0, 0), (0, 0), 0.5, colors.HexColor('#CBD5E1')),
            ('BACKGROUND', (1, 0), (1, 0), colors.white),
            ('BOX', (1, 0), (1, 0), 0.5, colors.HexColor('#CBD5E1')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), cfg.get('obs_padding_v', 8.0)),
            ('BOTTOMPADDING', (0, 0), (-1, -1), cfg.get('obs_padding_v', 8.0)),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(footer_table)

        # Footnote discrète (marque blanche intégrale)
        now_str = datetime.now().strftime('%d/%m/%Y à %H:%M')
        footnote = Paragraph(
            f"<font size='{max(cfg['font_obs_title']-1.5, 5.0)}' color='#94A3B8'>Édité le {now_str}</font>",
            ParagraphStyle('Foot', parent=styles['Normal'], alignment=1, leading=6.5)
        )
        elements.append(Spacer(1, 2))
        elements.append(footnote)

        doc.build(elements)
        if doc.page == 1:
            buffer.seek(0)
            return buffer

    # En cas exceptionnel, retourner le buffer généré au palier le plus compact
    buffer.seek(0)
    return buffer


