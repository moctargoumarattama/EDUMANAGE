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
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from app import db


_PDF_FONT_READY = False


def _setup_pdf_fonts():
    global _PDF_FONT_READY
    if _PDF_FONT_READY:
        return "KlasoraSans", "KlasoraSans-Bold"

    candidates = [
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for regular, bold in candidates:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("KlasoraSans", regular))
            pdfmetrics.registerFont(TTFont("KlasoraSans-Bold", bold))
            _PDF_FONT_READY = True
            return "KlasoraSans", "KlasoraSans-Bold"

    return "Helvetica", "Helvetica-Bold"


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
    whatsapp_text = "Bonjour, voici votre reçu de paiement KLASORA."

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
        "ecole_nom": ecole.nom if ecole else "Établissement non renseigné",
        "numero": receipt_number(paiement),
        "date": date_str,
        "montant": _format_money(paiement.montant),
        "statut": paiement.statut or "",
        "classe_nom": classe.nom if classe else "",
        "date_verification": datetime.utcnow(),
    }


def generate_payment_receipt_pdf(context):
    font_regular, font_bold = _setup_pdf_fonts()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 17 * mm
    top = height - margin
    card_h = 176 * mm
    card_bottom = top - card_h

    pdf.setStrokeColor(colors.HexColor("#D7DEE8"))
    pdf.setLineWidth(1)
    pdf.roundRect(margin, card_bottom, width - (2 * margin), card_h, 8, stroke=1, fill=0)
    pdf.setFillColor(colors.HexColor("#1D4ED8"))
    pdf.rect(margin, top - 5, width - (2 * margin), 5, stroke=0, fill=1)

    if context.get("logo_local_path"):
        try:
            pdf.drawImage(
                ImageReader(context["logo_local_path"]),
                margin + 8 * mm,
                top - 32 * mm,
                width=20 * mm,
                height=20 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

    text_x = margin + 34 * mm
    pdf.setFont(font_bold, 14)
    pdf.setFillColor(colors.HexColor("#10233F"))
    pdf.drawString(text_x, top - 16 * mm, (context["ecole"].nom if context.get("ecole") else "Établissement non renseigné")[:70])
    pdf.setFont(font_regular, 8.5)
    pdf.setFillColor(colors.HexColor("#50617A"))
    school_lines = []
    ecole = context.get("ecole")
    if ecole:
        slogan_val = getattr(ecole, 'devise', '') or getattr(ecole, 'slogan', '')
        if slogan_val:
            school_lines.append(f"« {slogan_val.strip()} »")
        if ecole.adresse:
            school_lines.append(ecole.adresse)
        contact = " | ".join(v for v in [ecole.telephone, ecole.email] if v)
        if contact:
            school_lines.append(contact)
    for idx, line in enumerate(school_lines[:3]):
        pdf.drawString(text_x, top - (21 + idx * 4.5) * mm, line[:90])

    pdf.setFillColor(colors.HexColor("#10233F"))
    pdf.roundRect(width - margin - 42 * mm, top - 28 * mm, 34 * mm, 22 * mm, 5, stroke=0, fill=1)
    pdf.setFillColor(colors.HexColor("#B9C6D8"))
    pdf.setFont(font_bold, 7.5)
    pdf.drawRightString(width - margin - 11 * mm, top - 12 * mm, "REÇU")
    pdf.setFillColor(colors.white)
    pdf.setFont(font_bold, 13)
    pdf.drawRightString(width - margin - 11 * mm, top - 19 * mm, f"#{context['numero']}")
    pdf.setFillColor(colors.HexColor("#8EF0BD"))
    pdf.setFont(font_bold, 7.5)
    pdf.drawRightString(width - margin - 11 * mm, top - 24 * mm, (context["statut"] or "").upper())

    pdf.setFillColor(colors.HexColor("#F3F6FB"))
    pdf.roundRect(margin + 8 * mm, top - 54 * mm, width - (2 * margin) - 16 * mm, 22 * mm, 6, stroke=0, fill=1)
    pdf.setFillColor(colors.HexColor("#10233F"))
    pdf.setFont(font_bold, 16)
    pdf.drawString(margin + 14 * mm, top - 43 * mm, "REÇU DE PAIEMENT")
    pdf.setFont(font_regular, 8.5)
    summary = " • ".join(v for v in [context["mois_paye"], f"Année {context['annee_scolaire_nom']}" if context["annee_scolaire_nom"] else ""] if v)
    pdf.drawString(margin + 14 * mm, top - 49 * mm, summary[:80])
    pdf.setFont(font_bold, 15)
    pdf.setFillColor(colors.HexColor("#0F8F4F"))
    pdf.drawRightString(width - margin - 12 * mm, top - 44 * mm, context["montant"])

    left_x = margin + 12 * mm
    right_x = width / 2 + 8 * mm
    y_info = top - 70 * mm

    def row(x, yy, label, value):
        if not value:
            return yy
        pdf.setFont(font_bold, 8)
        pdf.setFillColor(colors.HexColor("#50617A"))
        pdf.drawString(x, yy, label)
        pdf.setFont(font_regular, 9.3)
        pdf.setFillColor(colors.HexColor("#10233F"))
        pdf.drawString(x, yy - 5 * mm, str(value)[:44])
        return yy - 9 * mm

    pdf.setFont(font_bold, 10.5)
    pdf.setFillColor(colors.HexColor("#1D4ED8"))
    pdf.drawString(left_x, y_info + 6 * mm, "PAIEMENT")
    pdf.drawString(right_x, y_info + 6 * mm, "ÉLÈVE")

    yy = y_info
    yy = row(left_x, yy, "Date", f"{context['date']} {context['heure']}".strip())
    yy = row(left_x, yy, "Année scolaire", context["annee_scolaire_nom"])
    yy = row(left_x, yy, "Mois / Objet", context["mois_paye"])
    yy = row(left_x, yy, "Mode", context["mode_paiement"])
    yy = row(left_x, yy, "Statut", context["statut"])
    row(left_x, yy, "Référence", context["reference"])

    yy = y_info
    eleve = context.get("eleve")
    yy = row(right_x, yy, "Nom", f"{eleve.prenom} {eleve.nom}" if eleve else "")
    row(right_x, yy, "Classe", context["classe_nom"])

    qr_size = 30 * mm
    qr_y = card_bottom + 12 * mm
    context["qr_buffer"].seek(0)
    pdf.setFillColor(colors.HexColor("#F8FBFF"))
    pdf.roundRect(margin + 8 * mm, qr_y - 6 * mm, width - (2 * margin) - 16 * mm, 42 * mm, 6, stroke=0, fill=1)
    pdf.setStrokeColor(colors.HexColor("#DFE7F2"))
    pdf.roundRect(margin + 8 * mm, qr_y - 6 * mm, width - (2 * margin) - 16 * mm, 42 * mm, 6, stroke=1, fill=0)
    pdf.setFillColor(colors.white)
    pdf.roundRect(margin + 14 * mm, qr_y - 1 * mm, 36 * mm, 36 * mm, 6, stroke=0, fill=1)
    pdf.drawImage(ImageReader(context["qr_buffer"]), margin + 17 * mm, qr_y + 2 * mm, width=qr_size, height=qr_size, preserveAspectRatio=True, mask="auto")
    pdf.setFont(font_bold, 10.5)
    pdf.setFillColor(colors.HexColor("#10233F"))
    pdf.drawString(margin + 58 * mm, qr_y + 24 * mm, "Vérification sécurisée")
    pdf.setFont(font_regular, 8.8)
    pdf.setFillColor(colors.HexColor("#50617A"))
    pdf.drawString(margin + 58 * mm, qr_y + 18 * mm, "Scanner pour vérifier ce reçu.")
    pdf.drawString(margin + 58 * mm, qr_y + 12 * mm, "Document vérifiable par QR code.")

    pdf.setFont(font_regular, 7.5)
    pdf.setFillColor(colors.HexColor("#7A8798"))
    pdf.drawCentredString(width / 2, card_bottom + 8 * mm, "Document généré par KLASORA")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer
