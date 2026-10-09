import io
import base64
import uuid
from datetime import datetime, date
import qrcode
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, g, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_

from app import db
from app.authorization import role_required, tenant_required
from app.models import Ecole, Eleve, Classe, Inscription, AnneeScolaire, CertificatAdministratif
from app.services.annees_scolaires import get_annee_consultee, get_annee_active

certificats_bp = Blueprint('certificats', __name__, url_prefix='/certificats')

TYPES_CERTIFICAT_VALIDES = {'scolarite', 'inscription', 'transfert', 'radiation'}
PREFIX_REFERENCES = {
    'scolarite': 'CS',
    'inscription': 'CI',
    'transfert': 'CR',
    'radiation': 'CR',
}


def _generer_qr_code_base64(url_data: str) -> str:
    """Génère un QR Code sous forme de chaîne base64 PNG prête à l'intégration HTML/DataURI."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(url_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def _generer_reference_unique(ecole_id: int, type_certificat: str, annee: int = None) -> str:
    """Génère la référence officielle incrémentale unique (ex: CS-2026-0001)."""
    if not annee:
        annee = date.today().year

    prefix = PREFIX_REFERENCES.get(type_certificat, 'CS')
    prefix_pattern = f"{prefix}-{annee}-%"

    count = CertificatAdministratif.query.filter(
        CertificatAdministratif.ecole_id == ecole_id,
        CertificatAdministratif.reference.like(prefix_pattern)
    ).count()

    num = count + 1
    ref = f"{prefix}-{annee}-{num:04d}"
    # Sécurité anti-collision
    while CertificatAdministratif.query.filter_by(reference=ref).first():
        num += 1
        ref = f"{prefix}-{annee}-{num:04d}"

    return ref


def _masquer_matricule(matricule: str) -> str:
    """Masque partiellement le matricule pour la protection des données personnelles."""
    if not matricule:
        return "—"
    mat = str(matricule).strip()
    if len(mat) <= 4:
        return f"{mat[:1]}***"
    return f"{mat[:2]}****{mat[-2:]}"


# ===================================================================
# 1. GÉNÉRATION DE CERTIFICAT (POST /certificats/generer)
# ===================================================================
@certificats_bp.route('/generer', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def generer():
    """Génère et enregistre un certificat officiel avec référence unique et jeton QR code."""
    ecole_id = g.ecole_id
    ecole = db.session.get(Ecole, ecole_id)
    if not ecole:
        return jsonify({"success": False, "error": "Établissement introuvable."}), 404

    # Support JSON ou Formulaire classique
    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict()

    eleve_id = data.get('eleve_id')
    if not eleve_id:
        return jsonify({"success": False, "error": "Identifiant élève manquant."}), 400

    try:
        eleve_id = int(eleve_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Identifiant élève invalide."}), 400

    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        return jsonify({"success": False, "error": "Élève introuvable dans cet établissement."}), 404

    type_certificat = (data.get('type_certificat') or 'scolarite').strip().lower()
    if type_certificat not in TYPES_CERTIFICAT_VALIDES:
        return jsonify({"success": False, "error": f"Type de certificat invalide. Choix : {', '.join(TYPES_CERTIFICAT_VALIDES)}"}), 400

    # Résolution de l'année scolaire et de la classe de l'élève
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)
    if type_certificat in ('transfert', 'radiation') and (
        not annee_active or annee_active.statut != 'active'
    ):
        return jsonify({
            "success": False,
            "error": "Un transfert ou une radiation ne peut être décidé que pour l'année scolaire active."
        }), 400
    inscription = Inscription.query.filter_by(
        eleve_id=eleve.id,
        ecole_id=ecole_id,
        annee_scolaire_id=annee_active.id if annee_active else None
    ).first()

    if not inscription:
        # Fallback sur la dernière inscription connue
        inscription = Inscription.query.filter_by(
            eleve_id=eleve.id,
            ecole_id=ecole_id
        ).order_by(Inscription.id.desc()).first()

    # RÈGLE POINT 6 : Certificat de scolarité strictement conditionné à une inscription active
    if type_certificat == 'scolarite':
        if not inscription or inscription.statut not in ('inscrit', 'actif'):
            return jsonify({
                "success": False,
                "error": "Impossible de délivrer un certificat de scolarité pour un élève sans inscription active (statut radié, transféré ou non scolarisé)."
            }), 400

    annee_scolaire_str = (
        inscription.annee_scolaire.nom if (inscription and inscription.annee_scolaire)
        else (annee_active.nom if annee_active else f"{date.today().year}-{date.today().year + 1}")
    )
    classe_nom = inscription.classe.nom if (inscription and inscription.classe) else 'Non assigné'
    niveau = inscription.classe.niveau if (inscription and inscription.classe) else None

    # Champs optionnels
    type_admission = (data.get('type_admission') or 'Inscription').strip()
    if type_admission not in ('Inscription', 'Réinscription'):
        type_admission = 'Inscription'

    # Champs spécifiques pour transfert / radiation
    date_depart = None
    if type_certificat in ('transfert', 'radiation'):
        date_depart_str = data.get('date_depart', '').strip()
        if date_depart_str:
            try:
                date_depart = datetime.strptime(date_depart_str, "%Y-%m-%d").date()
            except ValueError:
                date_depart = date.today()
        else:
            date_depart = date.today()

    etablissement_destination = (data.get('etablissement_destination') or '').strip() or None

    # RÈGLE POINT 7 : Radiation / Transfert effectif liant l'inscription de manière atomique
    if type_certificat in ('transfert', 'radiation'):
        from app.services.inscriptions_annuelles import transferer_ou_radier_eleve
        statut_cible = "radie" if type_certificat == "radiation" else "transfere"
        transf_insc, transf_err = transferer_ou_radier_eleve(
            ecole_id=ecole_id,
            eleve_id=eleve.id,
            statut=statut_cible,
            date_depart=date_depart,
            etablissement_destination=etablissement_destination,
            motif_sortie=f"Délivrance de certificat de {type_certificat}",
            annee_scolaire_id=inscription.annee_scolaire_id if inscription else None
        )
        if transf_err or not transf_insc:
            db.session.rollback()
            return jsonify({"success": False, "error": transf_err or "Aucune inscription à transférer."}), 400
        inscription = transf_insc
        # Normaliser pour le stockage et le rendu sous 'transfert'
        type_certificat = 'transfert'

    # Émission & Ville (prise automatiquement depuis la page profil-école)
    ville_emission = (getattr(ecole, 'ville', None) or data.get('ville_emission') or 'Niamey').strip()
    signataire_nom = (data.get('signataire_nom') or getattr(ecole, 'nom', None) or 'La Direction').strip()
    signataire_titre = (data.get('signataire_titre') or 'La Direction').strip()

    date_emission = date.today()
    date_emission_str = data.get('date_emission', '').strip()
    if date_emission_str:
        try:
            date_emission = datetime.strptime(date_emission_str, "%Y-%m-%d").date()
        except ValueError:
            date_emission = date.today()

    # Génération des identifiants uniques sécurisés
    reference = _generer_reference_unique(ecole_id, type_certificat, annee=date_emission.year)
    code_verification = uuid.uuid4().hex

    certificat = CertificatAdministratif(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        nom_eleve=eleve.nom,
        prenom_eleve=eleve.prenom,
        matricule_eleve=eleve.matricule,
        date_naissance_eleve=eleve.date_naissance,
        lieu_naissance_eleve=eleve.lieu_naissance,
        nationalite_eleve=eleve.nationalite or 'Nigérienne',
        genre_eleve=eleve.genre or 'M',
        nom_pere_eleve=eleve.nom_pere,
        nom_mere_eleve=eleve.nom_mere,
        numero_acte_eleve=eleve.numero_acte,
        type_certificat=type_certificat,
        reference=reference,
        annee_scolaire=annee_scolaire_str,
        classe_nom=classe_nom,
        niveau=niveau,
        type_admission=type_admission,
        date_depart=date_depart,
        etablissement_destination=etablissement_destination,
        ville_emission=ville_emission,
        date_emission=date_emission,
        signataire_nom=signataire_nom,
        signataire_titre=signataire_titre,
        code_verification=code_verification
    )
    db.session.add(certificat)
    db.session.commit()

    print_url = url_for('certificats.imprimer', certificat_id=certificat.id)

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            "success": True,
            "message": f"Certificat {reference} généré avec succès.",
            "certificat": certificat.to_dict(),
            "redirect_url": print_url
        })

    flash(f"Certificat {reference} généré avec succès.", "success")
    return redirect(print_url)


# ===================================================================
# 2. IMPRESSION DU CERTIFICAT A4 (GET /certificats/imprimer/<id>)
# ===================================================================
@certificats_bp.route('/imprimer/<int:certificat_id>', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def imprimer(certificat_id):
    """Rend la vue A4 imprimable officielle conforme aux normes administratives."""
    ecole_id = g.ecole_id
    cert = CertificatAdministratif.query.filter_by(id=certificat_id).first_or_404()

    # Cloisonnement de l'école
    if current_user.role != 'super_admin' and cert.ecole_id != ecole_id:
        abort(403)

    eleve = cert.eleve
    ecole = cert.ecole or db.session.get(Ecole, cert.ecole_id)

    # Résolution du logo comme pour le bulletin scolaire
    logo_url = None
    if ecole:
        if ecole.logo_path and ecole.logo_path != 'default_logo.png':
            logo_url = url_for('static', filename=ecole.logo_path)
        elif ecole.logo and ecole.logo != 'default_logo.png':
            logo_url = url_for('static', filename='uploads/logos/' + ecole.logo)

    nom_ecole = ecole.nom if ecole else "ÉTABLISSEMENT SCOLAIRE"
    mots = [w for w in nom_ecole.replace('-', ' ').split() if w]
    monogramme_initiales = ''.join(w[0].upper() for w in mots[:2]) if mots else 'KL'

    # URL publique absolue pour le QR Code
    verification_url = url_for('certificats.verifier', code=cert.code_verification, _external=True)
    qr_base64 = _generer_qr_code_base64(verification_url)

    return render_template(
        'certificat_print.html',
        cert=cert,
        eleve=eleve,
        ecole=ecole,
        logo_url=logo_url,
        monogramme_initiales=monogramme_initiales,
        verification_url=verification_url,
        qr_base64=qr_base64
    )


# ===================================================================
# 3. PAGE PUBLIQUE DE VÉRIFICATION ANTI-FRAUDE (GET /certificats/verifier/<code>)
# Accessible publiquement sans connexion
# ===================================================================
@certificats_bp.route('/verifier/<string:code>', methods=['GET'])
def verifier(code):
    """Route publique anti-fraude appelée lors du scan du QR code présent sur le certificat."""
    code_propre = (code or '').strip()
    cert = CertificatAdministratif.query.filter_by(code_verification=code_propre).first()

    if not cert:
        return render_template(
            'verifier_certificat.html',
            valide=False,
            code=code_propre,
            cert=None,
            eleve=None,
            ecole=None
        ), 404

    eleve = cert.eleve
    ecole = cert.ecole
    matricule_brut = cert.matricule_eleve or (eleve.matricule if eleve else None)
    matricule_masque = _masquer_matricule(matricule_brut)

    return render_template(
        'verifier_certificat.html',
        valide=True,
        code=code_propre,
        cert=cert,
        eleve=eleve,
        ecole=ecole,
        matricule_masque=matricule_masque
    )


# ===================================================================
# 4. REGISTRE DES CERTIFICATS ÉMIS (GET /certificats/registre)
# ===================================================================
@certificats_bp.route('/registre', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def registre():
    """Affiche le registre d'audit des certificats émis pour l'école."""
    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)

    # Filtres
    q = request.args.get('q', '').strip()
    type_filtre = request.args.get('type', '').strip().lower()
    date_filtre = request.args.get('date', '').strip()

    query = CertificatAdministratif.query.filter_by(ecole_id=ecole_id)

    if type_filtre in TYPES_CERTIFICAT_VALIDES:
        query = query.filter(CertificatAdministratif.type_certificat == type_filtre)

    if date_filtre:
        try:
            d = datetime.strptime(date_filtre, "%Y-%m-%d").date()
            query = query.filter(CertificatAdministratif.date_emission == d)
        except ValueError:
            pass

    if q:
        search_pattern = f"%{q}%"
        query = query.join(Eleve).filter(
            or_(
                CertificatAdministratif.reference.ilike(search_pattern),
                Eleve.nom.ilike(search_pattern),
                Eleve.prenom.ilike(search_pattern),
                Eleve.matricule.ilike(search_pattern)
            )
        )

    certificats = query.order_by(CertificatAdministratif.created_at.desc()).all()

    # Statistiques du registre
    total_count = CertificatAdministratif.query.filter_by(ecole_id=ecole_id).count()
    scolarite_count = CertificatAdministratif.query.filter_by(ecole_id=ecole_id, type_certificat='scolarite').count()
    inscription_count = CertificatAdministratif.query.filter_by(ecole_id=ecole_id, type_certificat='inscription').count()
    transfert_count = CertificatAdministratif.query.filter_by(ecole_id=ecole_id, type_certificat='transfert').count()

    # Élèves pour la modale rapide
    eleves = (
        Eleve.query
        .filter_by(ecole_id=ecole_id)
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )

    return render_template(
        'certificats_registre.html',
        certificats=certificats,
        annee_active=annee_active,
        total_count=total_count,
        scolarite_count=scolarite_count,
        inscription_count=inscription_count,
        transfert_count=transfert_count,
        q=q,
        type_filtre=type_filtre,
        date_filtre=date_filtre,
        eleves=eleves
    )


# ===================================================================
# 5. API HELPER POUR INFOS ÉLÈVE EN MODALE (GET /certificats/eleve/<id>/info)
# ===================================================================
@certificats_bp.route('/eleve/<int:eleve_id>/info', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def eleve_info(eleve_id):
    """Renvoie les données scolaires courantes de l'élève pour pré-remplir la modale."""
    ecole_id = g.ecole_id
    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first_or_404()

    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)
    inscription = Inscription.query.filter_by(
        eleve_id=eleve.id,
        ecole_id=ecole_id,
        annee_scolaire_id=annee_active.id if annee_active else None
    ).first()

    if not inscription:
        inscription = Inscription.query.filter_by(
            eleve_id=eleve.id,
            ecole_id=ecole_id
        ).order_by(Inscription.id.desc()).first()

    classe_nom = inscription.classe.nom if (inscription and inscription.classe) else 'Non assigné'
    niveau = inscription.classe.niveau if (inscription and inscription.classe) else ''
    annee_nom = (
        inscription.annee_scolaire.nom if (inscription and inscription.annee_scolaire)
        else (annee_active.nom if annee_active else f"{date.today().year}-{date.today().year + 1}")
    )

    return jsonify({
        "success": True,
        "eleve": {
            "id": eleve.id,
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "nom_complet": f"{eleve.nom} {eleve.prenom}",
            "matricule": eleve.matricule,
            "date_naissance": eleve.date_naissance.strftime("%d/%m/%Y") if eleve.date_naissance else "",
            "lieu_naissance": eleve.lieu_naissance or "",
            "classe_nom": classe_nom,
            "niveau": niveau,
            "annee_scolaire": annee_nom
        }
    })

