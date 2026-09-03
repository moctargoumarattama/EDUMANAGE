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
    elif current_user.role in ('enseignant', 'professeur'):
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

    total_eleves = sum(c.effectif for c in classes)
    moyenne_effectif = int(total_eleves / len(classes)) if classes else 0

    classes_pleines = 0
    for c in classes:
        nb_inscriptions = db.session.query(func.count(Inscription.id)).filter_by(classe_id=c.id).scalar()
        if nb_inscriptions >= (c.capacite or 30):
            classes_pleines += 1

    return {
        'total_eleves': total_eleves,
        'moyenne_effectif': moyenne_effectif,
        'classes_pleines': classes_pleines
    }


def generer_alertes_automatiques(limit=None):
    alertes = []
    maintenant = datetime.now()
    mois_courant = maintenant.month
    annee_courante = maintenant.year
    mois_noms = ['Janvier', 'Fevrier', 'Mars', 'Avril', 'Mai', 'Juin',
                 'Juillet', 'Aout', 'Septembre', 'Octobre', 'Novembre', 'Decembre']

    eleves = get_ecole_filter_query(Eleve).all()
    for eleve in eleves:
        notes = Note.query.filter_by(eleve_id=eleve.id).all()
        if notes:
            total_pondere = sum(n.valeur * n.coefficient for n in notes)
            total_coefficients = sum(n.coefficient for n in notes)
            moyenne = round(total_pondere / total_coefficients, 2)
            if moyenne < 10:
                alertes.append({
                    'id': f'note-{eleve.id}-{uuid.uuid4().hex}',
                    'type': 'danger',
                    'titre': 'Eleve en difficulte academique',
                    'message': f'{eleve.prenom} {eleve.nom} ({eleve.classe}) a une moyenne de {moyenne}/20',
                    'date': maintenant,
                    'source': 'Notes',
                    'lien': url_for('main.voir_eleve', eleve_id=eleve.id),
                    'eleve_id': eleve.id,
                    'priorite': 3,
                    'notifie': False
                })

    absences = db.session.query(
        Absence.eleve_id,
        func.count(Absence.id).label('total_absences')
    ).filter_by(justifiee=False).group_by(Absence.eleve_id).all()

    for absence in absences:
        if absence.total_absences >= 3:
            eleve = Eleve.query.get(absence.eleve_id)
            alertes.append({
                'id': f'absence-{eleve.id}-{uuid.uuid4().hex}',
                'type': 'warning',
                'titre': 'Absences repetees non justifiees',
                'message': f'{eleve.prenom} {eleve.nom} ({eleve.classe}) a {absence.total_absences} absences non justifiees',
                'date': maintenant,
                'source': 'Absences',
                'lien': url_for('main.absences'),
                'eleve_id': eleve.id,
                'priorite': 2,
                'notifie': False
            })

    for eleve in eleves:
        paiements_eleve = Paiement.query.filter_by(eleve_id=eleve.id, annee=annee_courante).all()
        mois_payes = [p.mois for p in paiements_eleve]
        mois_manquants = [mois_noms[m-1] for m in range(1, mois_courant) if mois_noms[m-1] not in mois_payes]

        if mois_manquants:
            if len(mois_manquants) >= 3:
                type_alerte = 'danger'
                priorite = 3
            elif len(mois_manquants) == 2:
                type_alerte = 'warning'
                priorite = 2
            else:
                type_alerte = 'info'
                priorite = 1

            message = (f"{eleve.prenom} {eleve.nom} ({eleve.classe}) n'a pas paye "
                       f"{'le mois de ' + mois_manquants[0] if len(mois_manquants)==1 else 'les mois de ' + ', '.join(mois_manquants)}")

            alertes.append({
                'id': f'paiement-{eleve.id}-{uuid.uuid4().hex}',
                'type': type_alerte,
                'titre': 'Paiements en retard',
                'message': message,
                'date': maintenant,
                'source': 'Paiements',
                'lien': url_for('main.paiements'),
                'eleve_id': eleve.id,
                'priorite': priorite,
                'details': {
                    'mois_manquants': mois_manquants,
                    'nombre_mois_manquants': len(mois_manquants),
                    'montant_total_du': eleve.frais_annuels / 12 * len(mois_manquants)
                },
                'notifie': False
            })

    alertes.sort(key=lambda x: (-x['priorite'], x['date']))

    if limit:
        alertes = alertes[:limit]

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
    contact_ecole=None
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
        fontSize=20, textColor=colors.HexColor('#1A5276'),
        spaceAfter=20, alignment=1, fontName='Helvetica-Bold'
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
    title = Paragraph("<b>BULLETIN SCOLAIRE</b>", title_style)

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
    elements.append(Spacer(1, 20))

    student_info = [
        ['INFORMATIONS ELEVE', '', ''],
        ['Nom et Prenom', f"{eleve.nom} {eleve.prenom}", ''],
        ['Classe', eleve.classe.nom if eleve.classe else "Non renseignee", ''],
        ['Date de Naissance', eleve.date_naissance.strftime('%d/%m/%Y') if eleve.date_naissance else "Non renseignee", ''],
        ['Date d edition', datetime.now().strftime('%d/%m/%Y %H:%M'), '']
    ]
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
    elements.append(Spacer(1, 25))

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

    signature_data = [
        ['OBSERVATIONS GENERALES:', ''],
        [Paragraph(f"<i>Moyenne generale : {moyenne_generale:.2f} - {'Tres bon travail' if moyenne_generale >= 12 else 'Satisfaisant' if moyenne_generale >= 10 else 'Doit faire des efforts'}</i>", styles['Italic']), ''],
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
