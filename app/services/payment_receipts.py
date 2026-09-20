import base64
import io
import os
from datetime import datetime
from urllib.parse import quote_plus

import qrcode
from flask import current_app, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from app import db


def receipt_number(paiement):
    return f"{paiement.id:06d}"


def _format_money(value):
    try:
        return f"{float(value):,.0f} FCFA".replace(",", " ")
    except (TypeError, ValueError):
        return ""


def _payment_date_parts(paiement):
    dt = paiement.date_paiement
    if not dt:
        return "", ""
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")


def _logo_info(ecole):
    if not ecole or not getattr(ecole, "logo_path", None):
        return None, None
    logo_path = ecole.logo_path
    if logo_path == "default_logo.png" or logo_path.startswith(("http://", "https://", "/")):
        return None, None
    local_path = os.path.join(current_app.static_folder, logo_path)
    if not os.path.exists(local_path):
        return None, None
    return url_for("static", filename=logo_path), local_path


def _qr_png_buffer(data_url):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(data_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def build_payment_receipt_context(paiement):
    paiement.ensure_verification_token()
    db.session.flush()

    eleve = paiement.eleve
    inscription = paiement.inscription
    ecole = (inscription.ecole if inscription and inscription.ecole else None) or (eleve.ecole if eleve else None)
    classe = inscription.classe if inscription else None
    annee_scolaire = inscription.annee_scolaire if inscription else None
    date_str, heure_str = _payment_date_parts(paiement)
    logo_url, logo_local_path = _logo_info(ecole)
    verification_kwargs = {"token": paiement.verification_token, "_external": True}
    if not current_app.config.get("TESTING"):
        verification_kwargs["_scheme"] = "https"
    verification_url = url_for("main.verifier_recu_public", **verification_kwargs)
    qr_buffer = _qr_png_buffer(verification_url)
    qr_base64 = base64.b64encode(qr_buffer.getvalue()).decode("ascii")
    whatsapp_text = (
        "Bonjour,\n"
        "voici le lien de verification de votre recu KLASORA :\n"
        f"{verification_url}"
    )

    return {
        "paiement": paiement,
        "numero": receipt_number(paiement),
        "date": date_str,
        "heure": heure_str,
        "ecole": ecole,
        "eleve": eleve,
        "inscription": inscription,
        "classe_nom": classe.nom if classe else "Sans classe",
        "annee_scolaire_nom": annee_scolaire.nom if annee_scolaire else "",
        "mois_paye": f"{paiement.mois} {paiement.annee}".strip(),
        "montant": _format_money(paiement.montant),
        "mode_paiement": paiement.mode_paiement or "",
        "statut": paiement.statut or "",
        "reference": paiement.reference or "",
        "verification_url": verification_url,
        "qr_base64": qr_base64,
        "qr_buffer": qr_buffer,
        "logo_url": logo_url,
        "logo_local_path": logo_local_path,
        "whatsapp_url": f"https://wa.me/?text={quote_plus(whatsapp_text)}",
        "generated_at": datetime.utcnow(),
    }


def build_public_receipt_verification_context(paiement):
    date_str, _heure_str = _payment_date_parts(paiement)
    ecole = paiement.inscription.ecole if paiement.inscription and paiement.inscription.ecole else (paiement.eleve.ecole if paiement.eleve else None)
    classe = paiement.inscription.classe if paiement.inscription else None
    return {
        "valide": True,
        "ecole_nom": ecole.nom if ecole else "Etablissement non renseigne",
        "numero": receipt_number(paiement),
        "date": date_str,
        "montant": _format_money(paiement.montant),
        "statut": paiement.statut or "",
        "classe_nom": classe.nom if classe else "",
        "date_verification": datetime.utcnow(),
    }


def generate_payment_receipt_pdf(context):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 18 * mm
    y = height - margin

    pdf.setStrokeColor(colors.HexColor("#D7DEE8"))
    pdf.setLineWidth(1)
    pdf.roundRect(margin, margin, width - (2 * margin), height - (2 * margin), 8, stroke=1, fill=0)

    if context.get("logo_local_path"):
        try:
            pdf.drawImage(ImageReader(context["logo_local_path"]), margin + 8 * mm, y - 20 * mm, width=20 * mm, height=20 * mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    text_x = margin + 34 * mm
    pdf.setFont("Helvetica-Bold", 15)
    pdf.setFillColor(colors.HexColor("#10233F"))
    pdf.drawString(text_x, y - 7 * mm, (context["ecole"].nom if context.get("ecole") else "Etablissement non renseigne")[:70])
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(colors.HexColor("#50617A"))
    school_lines = []
    ecole = context.get("ecole")
    if ecole:
        if ecole.adresse:
            school_lines.append(ecole.adresse)
        contact = " | ".join(v for v in [ecole.telephone, ecole.email] if v)
        if contact:
            school_lines.append(contact)
    for idx, line in enumerate(school_lines[:2]):
        pdf.drawString(text_x, y - (13 + idx * 5) * mm, line[:90])

    pdf.setFillColor(colors.HexColor("#F3F6FB"))
    pdf.roundRect(margin + 8 * mm, y - 45 * mm, width - (2 * margin) - 16 * mm, 16 * mm, 6, stroke=0, fill=1)
    pdf.setFillColor(colors.HexColor("#10233F"))
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(width / 2, y - 36 * mm, "RECU DE PAIEMENT")
    pdf.setFont("Helvetica", 10)
    pdf.drawRightString(width - margin - 12 * mm, y - 36 * mm, f"#{context['numero']}")

    left_x = margin + 12 * mm
    right_x = width / 2 + 8 * mm
    y_info = y - 62 * mm

    def row(x, yy, label, value):
        if not value:
            return yy
        pdf.setFont("Helvetica-Bold", 9)
        pdf.setFillColor(colors.HexColor("#50617A"))
        pdf.drawString(x, yy, label)
        pdf.setFont("Helvetica", 10)
        pdf.setFillColor(colors.HexColor("#10233F"))
        pdf.drawString(x, yy - 5 * mm, str(value)[:44])
        return yy - 13 * mm

    pdf.setFont("Helvetica-Bold", 11)
    pdf.setFillColor(colors.HexColor("#1D4ED8"))
    pdf.drawString(left_x, y_info + 6 * mm, "PAIEMENT")
    pdf.drawString(right_x, y_info + 6 * mm, "ELEVE")

    yy = y_info
    yy = row(left_x, yy, "Date", f"{context['date']} {context['heure']}".strip())
    yy = row(left_x, yy, "Annee scolaire", context["annee_scolaire_nom"])
    yy = row(left_x, yy, "Mois / Objet", context["mois_paye"])
    yy = row(left_x, yy, "Montant", context["montant"])
    yy = row(left_x, yy, "Mode", context["mode_paiement"])
    yy = row(left_x, yy, "Statut", context["statut"])
    row(left_x, yy, "Reference", context["reference"])

    yy = y_info
    eleve = context.get("eleve")
    yy = row(right_x, yy, "Nom", f"{eleve.prenom} {eleve.nom}" if eleve else "")
    row(right_x, yy, "Classe", context["classe_nom"])

    qr_size = 32 * mm
    qr_y = margin + 54 * mm
    context["qr_buffer"].seek(0)
    pdf.drawImage(ImageReader(context["qr_buffer"]), margin + 12 * mm, qr_y, width=qr_size, height=qr_size, preserveAspectRatio=True, mask="auto")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.setFillColor(colors.HexColor("#10233F"))
    pdf.drawString(margin + 50 * mm, qr_y + 22 * mm, "Scanner pour verifier l'authenticite du recu")
    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(colors.HexColor("#50617A"))
    pdf.drawString(margin + 50 * mm, qr_y + 16 * mm, context["verification_url"][:95])

    sig_y = margin + 28 * mm
    pdf.setStrokeColor(colors.HexColor("#9AA7B8"))
    pdf.line(margin + 14 * mm, sig_y, margin + 74 * mm, sig_y)
    pdf.line(width - margin - 74 * mm, sig_y, width - margin - 14 * mm, sig_y)
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(colors.HexColor("#50617A"))
    pdf.drawString(margin + 25 * mm, sig_y - 6 * mm, "Signature caisse")
    pdf.drawString(width - margin - 60 * mm, sig_y - 6 * mm, "Signature parent")
    pdf.setDash(3, 3)
    pdf.roundRect(width / 2 - 23 * mm, margin + 19 * mm, 46 * mm, 18 * mm, 4, stroke=1, fill=0)
    pdf.setDash()
    pdf.drawCentredString(width / 2, margin + 27 * mm, "Cachet etablissement")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer
