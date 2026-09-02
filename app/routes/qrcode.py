from . import main
from .common import (
    Eleve,
    current_user,
    login_required,
    os,
    render_template,
    role_required,
    send_file,
)
import qrcode
from app.services import get_qr_cache_path


@main.route('/eleve/<int:id>/qrcode')
@login_required
@role_required('admin')
def generer_qrcode_eleve(id):
    eleve = Eleve.query.get_or_404(id)

    cache_path = get_qr_cache_path(eleve)

    # → Si existe → renvoyer directement
    if os.path.exists(cache_path):
        return send_file(cache_path, mimetype='image/png',
                         download_name=f"qrcode_{eleve.prenom}_{eleve.nom}.png")

    # Sinon générer
    data = (
        f"ÉLÈVE: {eleve.prenom} {eleve.nom}\n"
        f"CLASSE: {eleve.classe.nom if eleve.classe else 'Non renseignée'}\n"
        f"DATE NAISSANCE: {eleve.date_naissance.strftime('%d/%m/%Y') if eleve.date_naissance else 'Non renseignée'}\n"
        f"TÉLÉPHONE: {eleve.telephone or 'Non renseigné'}\n"
        f"EMAIL: {eleve.email or 'Non renseigné'}\n"
    )

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=2
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image()

    img.save(cache_path)

    return send_file(cache_path, mimetype='image/png',
                     download_name=f"qrcode_{eleve.prenom}_{eleve.nom}.png")

@main.route('/qrcodes_etudiants')
@login_required
@role_required('admin', 'enseignant')
def qrcodes_etudiants():
    from collections import defaultdict
    import base64

    etudiants = (
        Eleve.query
        .filter_by(ecole_id=current_user.ecole_id)
        .order_by(Eleve.classe_id, Eleve.nom)
        .all()
    )

    qrcodes_par_classe = defaultdict(list)

    for e in etudiants:
        cache_path = get_qr_cache_path(e)

        # Génère si manquant
        if not os.path.exists(cache_path):
            data = f"{e.prenom} {e.nom}\nClasse: {e.classe.nom if e.classe else 'Non renseignée'}"
            qr = qrcode.make(data)
            qr.save(cache_path)

        # Charger en base64
        with open(cache_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()

        qrcodes_par_classe[e.classe.nom if e.classe else 'Non renseignée']\
            .append({'eleve': e, 'qr': img_data})

    return render_template('qrcodes_etudiants.html', qrcodes_par_classe=qrcodes_par_classe)
