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

    total_eleves = sum(c.effectif_reel for c in classes)
    moyenne_effectif = int(total_eleves / len(classes)) if classes else 0

    classes_pleines = 0
    for c in classes:
        if c.est_pleine:
            classes_pleines += 1

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
    mois_courant = maintenant.month
    mois_noms = ['Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
                 'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre']

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
        paiements_valides = [p for p in ins.paiements if p.statut != 'rejete']
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
            mois_manquants = [mois_noms[m-1] for m in range(1, mois_courant) if mois_noms[m-1] not in mois_payes]
            if mois_manquants and total_paye < frais_annuels:
                frais_mensuels = frais_annuels / 10
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

                    if eleve and eleve.email_parent:
                        envoyer_email(
                            eleve.email_parent,
                            f"Alerte: {a['titre']}",
                            message
                        )

                    a['notifie'] = True

            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    thread = threading.Thread(target=worker, args=(alertes, app))
    thread.start()


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
):
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=50, bottomMargin=50,
        leftMargin=45, rightMargin=45
    )

    styles = getSampleStyleSheet()
    elements = []

    title_style = ParagraphStyle(
        'Title', parent=styles['Heading1'],
        fontSize=18, textColor=colors.HexColor('#1A5276'),
        spaceAfter=15, alignment=1, fontName='Helvetica-Bold'
    )
    subtitle_style = ParagraphStyle(
        'Subtitle', parent=styles['Heading2'],
        fontSize=14, textColor=colors.HexColor('#2E86C1'),
        spaceAfter=10, fontName='Helvetica-Bold', alignment=1
    )
    header_style = ParagraphStyle(
        'HeaderStyle', parent=styles['Normal'],
        fontSize=11, textColor=colors.HexColor('#34495E'), spaceAfter=6
    )
    normal_center = ParagraphStyle('normal_center', parent=styles['Normal'], alignment=1)

    if not nom_ecole or not adresse_ecole or not contact_ecole:
        if hasattr(eleve, 'ecole') and eleve.ecole:
            nom_ecole = nom_ecole or eleve.ecole.nom
            adresse_ecole = adresse_ecole or eleve.ecole.adresse
            contact_ecole = contact_ecole or f"Tel: {eleve.ecole.telephone or '-'} - Email: {eleve.ecole.email or '-'}"
        else:
            nom_ecole = nom_ecole or "ECOLE INCONNUE"
            adresse_ecole = adresse_ecole or "Non renseignee"
            contact_ecole = contact_ecole or "-"

    logo_cell = Image(logo_path, width=80, height=80) if logo_path and os.path.exists(logo_path) else Paragraph("", styles['Normal'])
    school_info = Paragraph(f"<b>{nom_ecole}</b><br/><font size='10'>{adresse_ecole}<br/>{contact_ecole}</font>", header_style)
    
    title_text = "<b>BULLETIN SCOLAIRE</b>"
    if annee_scolaire_nom:
        title_text += f"<br/><font size='11' color='#5D6D7E'>{annee_scolaire_nom}</font>"
    title = Paragraph(title_text, title_style)

    header_data = [[logo_cell, school_info, title]]
    header_table = Table(header_data, colWidths=[80, 230, 180])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12)
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph("<hr width='100%' color='#3498DB' size='2'/>", styles['Normal']))
    elements.append(Spacer(1, 15))

    classe_affichee = classe_nom or (eleve.classe.nom if (eleve and eleve.classe) else "Non renseignee")
    student_info = [
        ['INFORMATIONS ELEVE', '', ''],
        ['Nom et Prenom', f"{eleve.nom} {eleve.prenom}" if eleve else "-", ''],
        ['Classe', classe_affichee, ''],
    ]
    if annee_scolaire_nom:
        student_info.append(['Annee Scolaire', annee_scolaire_nom, ''])
    if periode_nom:
        student_info.append(['Periode', periode_nom, ''])
    if rang:
        rang_str = f"{rang}e sur {rang_total}" if rang_total else f"{rang}e"
        student_info.append(['Rang', rang_str, ''])

    student_info.extend([
        ['Date de Naissance', eleve.date_naissance.strftime('%d/%m/%Y') if (eleve and eleve.date_naissance) else "Non renseignee", ''],
        ['Date d edition', datetime.now().strftime('%d/%m/%Y %H:%M'), '']
    ])

    student_table = Table(student_info, colWidths=[150, 220, 120])
    student_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2980B9')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#BDC3C7')),
        ('ROWBACKGROUNDS', (1, 1), (-1, -1), [colors.white, colors.HexColor('#F8F9F9')])
    ]))
    elements.append(student_table)
    elements.append(Spacer(1, 20))

    elements.append(Paragraph("RESULTATS SCOLAIRES", subtitle_style))

    data = [['Matiere', 'Professeur', 'Moyenne', 'Appreciation']]

    for cours in sorted(moyennes_par_cours.keys(), key=lambda x: (x or "").lower()):
        moyenne = moyennes_par_cours.get(cours, 0) or 0.0
        if moyenne >= 16:
            appreciation = "Excellent"
        elif moyenne >= 14:
            appreciation = "Tres bien"
        elif moyenne >= 12:
            appreciation = "Bien"
        elif moyenne >= 10:
            appreciation = "Assez bien"
        else:
            appreciation = "Insuffisant"

        prof_nom = 'Non assigne'
        notes_for_course = notes_par_cours.get(cours, [])
        for n in notes_for_course:
            if getattr(n, 'cours', None) and getattr(n.cours, 'professeur', None):
                p = n.cours.professeur
                if p and getattr(p, 'prenom', None) or getattr(p, 'nom', None):
                    prof_nom = f"{p.prenom or ''} {p.nom or ''}".strip()
                    break

        moyenne_cell = Paragraph(f"<b>{moyenne:.2f}</b>", normal_center)
        data.append([cours, prof_nom, moyenne_cell, appreciation])

    overall_row = ['', '', Paragraph(f"<b>{moyenne_generale:.2f}</b>", normal_center), '']
    data.append(overall_row)

    table = Table(data, colWidths=[180, 130, 70, 130])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F618D')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#7F8C8D')),
        ('ROWBACKGROUNDS', (1, 1), (-1, -2), [colors.white, colors.HexColor('#F8F9F9')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#EBF5FB')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('SPAN', (0, -1), (1, -1)),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 30))

    obs_text = appreciation_generale if appreciation_generale else (
        f"Moyenne generale : {moyenne_generale:.2f} - {'Tres bon travail' if moyenne_generale >= 12 else 'Satisfaisant' if moyenne_generale >= 10 else 'Doit faire des efforts'}"
    )
    signature_data = [
        ['OBSERVATIONS GENERALES:', ''],
        [Paragraph(f"<i>{obs_text}</i>", styles['Italic']), ''],
        ['', 'Le Directeur'],
        ['', '_________________________']
    ]
    signature_table = Table(signature_data, colWidths=[330, 150])
    signature_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('TOPPADDING', (0, 0), (-1, -1), 6)
    ]))
    elements.append(signature_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer
