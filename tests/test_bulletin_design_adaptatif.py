# -*- coding: utf-8 -*-
"""
Tests automatisés pour la refonte moderne et adaptative du bulletin scolaire PDF de KLASORA.
Vérifie STRICTEMENT :
- Qu'aucun bulletin ne dépasse 1 seule page A4 portrait (garantie absolue single-page).
- Le dimensionnement adaptatif selon le nombre de matières (1, 5, 10, 15, 20, 24+).
- La robustesse face aux noms d'école et d'élèves très longs.
- La gestion des statuts OFFICIEL vs PROVISOIRE.
- La suppression des éléments parasites (Extrêmes classe, Min/Max).
- L'intégration du QR code unique d'authenticité et du logo circulaire / monogramme.
"""

import io
import re
import unittest
from datetime import date
from reportlab.lib.pagesizes import A4

from app.services import generer_bulletin_pdf


class MockEleve:
    def __init__(self, id=101, nom="DIOP", prenom="Amadou", code_parent="KL-0042", d_nais=date(2011, 5, 12)):
        self.id = id
        self.nom = nom
        self.prenom = prenom
        self.code_parent = code_parent
        self.date_naissance = d_nais
        self.classe = None


def compter_pages_pdf(pdf_bytes):
    """
    Compte le nombre de pages exactes d'un flux PDF ReportLab
    en analysant l'attribut /Count du catalogue de pages.
    """
    matches = re.findall(rb'/Count\s+(\d+)', pdf_bytes)
    if matches:
        return int(matches[0])
    # Fallback si flux non compressé
    page_matches = re.findall(rb'/Type\s*/Page\b', pdf_bytes)
    pages_matches = re.findall(rb'/Type\s*/Pages\b', pdf_bytes)
    return max(len(page_matches) - len(pages_matches), 1)


def extraire_texte_pdf(pdf_bytes):
    """Extrait tout le texte décompressé des flux du document PDF ReportLab."""
    import base64
    import zlib
    textes = []
    pos = 0
    while True:
        idx = pdf_bytes.find(b'stream\n', pos)
        offset = 7
        if idx == -1:
            idx = pdf_bytes.find(b'stream\r\n', pos)
            offset = 8
            if idx == -1:
                break
        end_idx = pdf_bytes.find(b'endstream', idx + offset)
        if end_idx == -1:
            break
        raw = pdf_bytes[idx + offset:end_idx].strip()
        try:
            a85 = base64.a85decode(raw, adobe=True)
            dec = zlib.decompress(a85)
            textes.append(dec)
        except Exception:
            textes.append(raw)
        pos = end_idx + 9
    return b' '.join(textes)


def generer_disciplines_test(n):
    """Génère n disciplines avec des intitulés réalistes et variés."""
    matieres = [
        "Mathématiques", "Français", "Anglais", "Histoire-Géographie",
        "Sciences de la Vie et de la Terre", "Physique-Chimie", "Éducation Physique & Sportive",
        "Informatique & Technologie", "Arts Plastiques", "Éducation Musicale",
        "Espagnol", "Philosophie", "Sciences Économiques & Sociales", "Latin",
        "Instruction Civique & Morale", "Arabe", "Dessin Industriel",
        "Biologie Animale", "Sociologie Moderne", "Statistiques & Probabilités",
        "Géométrie Vectorielle", "Littérature Contemporaine", "Mécanique Rationnelle", "Droit Civil"
    ]
    discs = []
    for i in range(n):
        nom_base = matieres[i % len(matieres)]
        if i >= len(matieres):
            nom_base = f"{nom_base} {i + 1}"
        discs.append({
            'cours_nom': nom_base,
            'professeur_nom': f"Prof. {nom_base[:5]}",
            'moyenne_controles': 12.0 + (i % 6),
            'note_composition': 13.5 + (i % 5),
            'moyenne_semestre': 12.75 + (i % 5),
            'coefficient': 2.0 if i % 2 == 0 else 3.0,
            'points': (12.75 + (i % 5)) * (2.0 if i % 2 == 0 else 3.0),
            'appreciation': "Très bien" if i % 2 == 0 else "Bon travail"
        })
    return discs


class TestBulletinDesignAdaptatif(unittest.TestCase):

    def setUp(self):
        self.eleve_standard = MockEleve()
        self.verif_url = "http://localhost:5007/verifier/bulletin/token-test-secret-123"

    def _generer_et_verifier_une_seule_page(self, n_matieres, nom_ecole="Complexe Scolaire Excellence", eleve=None, est_provisoire=False):
        eleve_actif = eleve or self.eleve_standard
        discs = generer_disciplines_test(n_matieres)
        total_c = sum(d['coefficient'] for d in discs)
        total_p = sum(d['points'] for d in discs)
        moy_gen = round(total_p / total_c, 2) if total_c > 0 else 14.5

        buf = generer_bulletin_pdf(
            eleve=eleve_actif,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=moy_gen,
            nom_ecole=nom_ecole,
            adresse_ecole="BP 10245 Niamey, Quartier Plateau",
            contact_ecole="Tél: +227 20 73 00 00 - Email: contact@ecole-excellence.ne",
            classe_nom="3ème A",
            annee_scolaire_nom="2026-2027",
            periode_nom="1er Semestre",
            rang=3,
            rang_total=28,
            appreciation_generale="Élève sérieux, attentif et régulier. Félicitations du conseil.",
            disciplines=discs,
            total_coefficients=total_c,
            total_points=total_p,
            stats_classe={'effectif_classe': 28},
            nb_absences=2,
            est_provisoire=est_provisoire,
            verification_url=self.verif_url,
        )

        self.assertIsNotNone(buf)
        pdf_bytes = buf.getvalue()
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

        nb_pages = compter_pages_pdf(pdf_bytes)
        self.assertEqual(
            nb_pages, 1,
            f"ÉCHEC CRITIQUE : Le bulletin avec {n_matieres} matières a généré {nb_pages} pages au lieu d'une seule !"
        )
        return pdf_bytes

    def test_01_bulletin_1_matiere_exactement_une_page(self):
        """1. Bulletin avec 1 seule matière : rendu aéré tenant sur strictement 1 page A4."""
        pdf_bytes = self._generer_et_verifier_une_seule_page(1)
        self.assertGreater(len(pdf_bytes), 3000)

    def test_02_bulletin_5_matieres_exactement_une_page(self):
        """2. Bulletin avec 5 matières : tenu sur strictement 1 page A4."""
        pdf_bytes = self._generer_et_verifier_une_seule_page(5)
        self.assertGreater(len(pdf_bytes), 3000)

    def test_03_bulletin_10_matieres_exactement_une_page(self):
        """3. Bulletin avec 10 matières : tenu sur strictement 1 page A4."""
        pdf_bytes = self._generer_et_verifier_une_seule_page(10)
        self.assertGreater(len(pdf_bytes), 3000)

    def test_04_bulletin_15_matieres_exactement_une_page(self):
        """4. Bulletin avec 15 matières : compactage élégant sur strictement 1 page A4."""
        pdf_bytes = self._generer_et_verifier_une_seule_page(15)
        self.assertGreater(len(pdf_bytes), 3000)

    def test_05_bulletin_20_matieres_exactement_une_page(self):
        """5. Bulletin avec 20 matières : ultra-compactage réussi sur strictement 1 page A4."""
        pdf_bytes = self._generer_et_verifier_une_seule_page(20)
        self.assertGreater(len(pdf_bytes), 3000)

    def test_06_bulletin_24_matieres_extreme_exactement_une_page(self):
        """6. Cas extrême avec 24 matières : tient toujours sur exactement 1 page A4 sans coupure."""
        pdf_bytes = self._generer_et_verifier_une_seule_page(24)
        self.assertGreater(len(pdf_bytes), 3000)

    def test_07_nom_ecole_tres_long_ne_deborde_pas(self):
        """7. Nom d'établissement très long : adaptation propre sans créer de 2e page."""
        nom_long = "COMPLEXE SCOLAIRE PRIVÉ INTERNATIONAL D EXCELLENCE ACADÉMIQUE ET DE LEADERSHIP DES JEUNES"
        pdf_bytes = self._generer_et_verifier_une_seule_page(12, nom_ecole=nom_long)
        self.assertGreater(len(pdf_bytes), 3000)

    def test_08_nom_eleve_tres_long_ne_deborde_pas(self):
        """8. Nom et prénom d'élève très longs : restent propres sur 1 seule page."""
        eleve_long = MockEleve(
            nom="MOHAMMED COULIBALY DE LA ROCHEFOUCAULD",
            prenom="JEAN-BAPTISTE SEYDOU MAMADOU SOULEYMANE"
        )
        pdf_bytes = self._generer_et_verifier_une_seule_page(12, eleve=eleve_long)
        self.assertGreater(len(pdf_bytes), 3000)

    def test_09_bulletin_officiel_vs_provisoire_sur_une_page(self):
        """9. Statuts OFFICIEL et PROVISOIRE testés et validés sur 1 page A4."""
        pdf_off = self._generer_et_verifier_une_seule_page(10, est_provisoire=False)
        pdf_prov = self._generer_et_verifier_une_seule_page(10, est_provisoire=True)
        self.assertEqual(compter_pages_pdf(pdf_off), 1)
        self.assertEqual(compter_pages_pdf(pdf_prov), 1)

    def test_10_aucun_element_parasite_dans_le_pdf(self):
        """10. Vérifie l'exclusion formelle des mentions 'Extrêmes classe' et 'Min/Max'."""
        pdf_bytes = self._generer_et_verifier_une_seule_page(8)
        texte = extraire_texte_pdf(pdf_bytes)
        self.assertNotIn(b"Extr\xc3\xaames classe", texte)
        self.assertNotIn(b"Min / Max", texte)
        self.assertNotIn(b"Min: ", texte)

    def test_11_retrocompatibilite_appel_ancien_format(self):
        """11. Rétrocompatibilité totale avec l'ancien appel sans paramètre disciplines."""
        buf = generer_bulletin_pdf(
            eleve=self.eleve_standard,
            notes_par_cours={},
            moyennes_par_cours={"Maths": 15.0, "Français": 14.0, "Anglais": 16.0},
            moyenne_generale=15.0,
            nom_ecole="École Rétro",
            classe_nom="6ème",
            annee_scolaire_nom="2025-2026",
            periode_nom="Semestre 1",
            verification_url=self.verif_url
        )
        self.assertEqual(compter_pages_pdf(buf.getvalue()), 1)

    def test_12_aucun_nom_professeur_dans_le_tableau_disciplines(self):
        """12. Vérifie la suppression du nom du professeur sous les disciplines."""
        discs = [{
            'cours_nom': 'Mathématiques',
            'professeur_nom': 'Prof. Tournesol',
            'moyenne_controles': 14.0,
            'note_composition': 16.0,
            'moyenne_semestre': 15.0,
            'coefficient': 3.0,
            'points': 45.0,
            'appreciation': 'Très bien'
        }]
        buf = generer_bulletin_pdf(
            eleve=self.eleve_standard,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=15.0,
            disciplines=discs,
            verification_url=self.verif_url
        )
        pdf_bytes = buf.getvalue()
        texte = extraire_texte_pdf(pdf_bytes)
        self.assertNotIn(b"Prof. Tournesol", texte)

    def test_13_rang_affiche_sans_ambiguite(self):
        """13. Vérifie l'affichage du rang sans ambiguïté (ex: 1er sur 2 et non 1e / 1)."""
        discs = generer_disciplines_test(3)
        buf = generer_bulletin_pdf(
            eleve=self.eleve_standard,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=15.0,
            rang=1,
            rang_total=1,
            stats_classe={'effectif_classe': 2},
            disciplines=discs,
            verification_url=self.verif_url
        )
        pdf_bytes = buf.getvalue()
        self.assertEqual(compter_pages_pdf(pdf_bytes), 1)
        texte = extraire_texte_pdf(pdf_bytes)
        self.assertNotIn(b"1e / 1", texte)
        self.assertIn(b"1er sur 2", texte)

    def test_14_qr_code_presence_et_legende_courte(self):
        """14. Présence du QR code, mentions neutres officielles et AUCUNE mention de KLASORA."""
        discs = generer_disciplines_test(4)
        buf = generer_bulletin_pdf(
            eleve=self.eleve_standard,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=15.0,
            disciplines=discs,
            verification_url=self.verif_url,
            est_provisoire=False,
        )
        pdf_bytes = buf.getvalue()
        self.assertEqual(compter_pages_pdf(pdf_bytes), 1)
        texte = extraire_texte_pdf(pdf_bytes)
        self.assertIn(b"Scanner", texte)
        self.assertIn(b"officiel", texte)
        self.assertNotIn(b"KLASORA", texte)

    def test_15_bulletin_provisoire_ne_mentionne_pas_officiel_dans_qr(self):
        """15. Un bulletin provisoire ne mentionne pas 'officiel' dans la légende du QR code."""
        discs = generer_disciplines_test(4)
        buf = generer_bulletin_pdf(
            eleve=self.eleve_standard,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=15.0,
            disciplines=discs,
            verification_url=self.verif_url,
            est_provisoire=True,
        )
        pdf_bytes = buf.getvalue()
        self.assertEqual(compter_pages_pdf(pdf_bytes), 1)
        texte = extraire_texte_pdf(pdf_bytes)
        self.assertIn(b"Scanner", texte)
        self.assertIn(b"rifiable", texte)
        self.assertNotIn(b"officiel", texte)
        self.assertNotIn(b"KLASORA", texte)


    def test_16_traitement_logo_circulaire_contain_respecte_contenu(self):
        """16. Vérifie que le logo circulaire utilise la stratégie contain sans crop agressif."""
        from PIL import Image as PILImage
        from app.services import _generer_logo_circulaire

        logo_path = 'app/static/ecoles/1/logo_billet.jpeg'
        buf = _generer_logo_circulaire(logo_path, size_px=160)
        self.assertIsNotNone(buf)
        buf.seek(0)
        img = PILImage.open(buf)
        self.assertEqual(img.size, (160, 160))
        self.assertEqual(img.mode, 'RGBA')

        # Vérifie que les 4 coins extérieurs sont 100% transparents (détourage circulaire parfait)
        for pt in [(0, 0), (159, 0), (0, 159), (159, 159)]:
            self.assertEqual(img.getpixel(pt)[3], 0, f"Le point {pt} devrait être transparent")

        # Vérifie que le disque intérieur a un fond blanc / contenu visible
        center_alpha = img.getpixel((80, 80))[3]
        self.assertEqual(center_alpha, 255)

    def test_17_bulletin_avec_logo_circulaire_sur_une_seule_page(self):
        """17. Vérifie que le bulletin avec logo réel tient sur exactement 1 page A4 (2, 5, 12, 20 matières)."""
        logo_path = 'app/static/ecoles/1/logo_billet.jpeg'
        for n in [2, 5, 12, 20]:
            discs = generer_disciplines_test(n)
            total_c = sum(d['coefficient'] for d in discs)
            total_p = sum(d['points'] for d in discs)
            moy_gen = round(total_p / total_c, 2)
            buf = generer_bulletin_pdf(
                eleve=self.eleve_standard,
                notes_par_cours={},
                moyennes_par_cours={},
                moyenne_generale=moy_gen,
                logo_path=logo_path,
                nom_ecole="CSP LA RESPONSABILITÉ",
                adresse_ecole="BP 10245 Niamey",
                contact_ecole="Tél: +227 96 05 97 16",
                disciplines=discs,
                total_coefficients=total_c,
                total_points=total_p,
                stats_classe={'effectif_classe': 28},
                verification_url=self.verif_url
            )
            self.assertEqual(
                compter_pages_pdf(buf.getvalue()), 1,
                f"Échec : le bulletin avec {n} matières et logo réel a dépassé 1 page !"
            )


if __name__ == '__main__':
    unittest.main()


