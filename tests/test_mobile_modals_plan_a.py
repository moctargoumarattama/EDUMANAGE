"""
Tests unitaires pour la refonte des Modals Mobiles (Plan A - Zéro perte d'espace) :
1. Suppression de la carte redondante 'Année scolaire' dans le modal élève.
2. Formulaire complet en 1 seule inscription (aucun fractionnement / multi-step).
3. Présence des classes responsive modal-fullscreen-md-down sur les 3 modals (élève, prof, emploi).
4. Footer persistant / sticky et scroll fluide intégrés dans le CSS.
"""
import os
import unittest


class TestMobileModalsPlanA(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        self.eleve_modal_path = os.path.join(self.base_dir, 'app', 'templates', 'partials', '_modal_ajouter_eleve.html')
        self.prof_modal_path = os.path.join(self.base_dir, 'app', 'templates', 'partials', '_modal_ajouter_professeur.html')
        self.emploi_modal_path = os.path.join(self.base_dir, 'app', 'templates', 'partials', '_modal_ajouter_emploi.html')
        self.css_path = os.path.join(self.base_dir, 'app', 'static', 'css', 'style.css')

    def test_modal_ajouter_eleve_plan_a(self):
        with open(self.eleve_modal_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. Vérifier la classe responsive fullscreen mobile
        self.assertIn('modal-fullscreen-md-down', content)
        self.assertIn('modal-dialog-scrollable', content)
        self.assertIn('modal-xl', content)

        # 2. Vérifier la suppression définitive de la carte Année scolaire
        self.assertNotIn('<label class="form-label fw-semibold">Année scolaire</label>', content)
        self.assertNotIn('<span class="badge bg-success small">Active</span>', content)

        # 3. Vérifier le formulaire complet en 1 seule étape (tous les champs conservés)
        self.assertIn('id="formAjouterEleve"', content)
        self.assertIn('name="nom"', content)
        self.assertIn('name="prenom"', content)
        self.assertIn('name="genre"', content)
        self.assertIn('name="date_naissance"', content)
        self.assertIn('name="lieu_naissance"', content)
        self.assertIn('name="classe_id"', content)
        self.assertIn('name="frais_annuels"', content)
        self.assertIn('name="parent_id"', content)
        self.assertIn('name="parent_nom"', content)
        self.assertIn('name="parent_telephone"', content)
        self.assertIn('name="code_parent"', content)
        self.assertIn('name="adresse"', content)

        # 4. Bouton de soumission avec son ID et anti double-clic
        self.assertIn('id="btnSubmitAjouterEleve"', content)

    def test_modal_ajouter_professeur_plan_a(self):
        with open(self.prof_modal_path, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('modal-fullscreen-md-down', content)
        self.assertIn('id="formAjouterProfesseur"', content)
        self.assertIn('name="nom"', content)
        self.assertIn('name="prenom"', content)
        self.assertIn('name="date_naissance"', content)
        self.assertIn('name="telephone"', content)
        self.assertIn('name="adresse"', content)
        self.assertIn('name="matieres_enseignees"', content)
        self.assertIn('name="code_prof"', content)
        self.assertIn('id="btnSubmitAjouterProfesseur"', content)

    def test_modal_ajouter_emploi_plan_a(self):
        with open(self.emploi_modal_path, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('modal-fullscreen-md-down', content)
        self.assertIn('id="formAjouterEmploi"', content)
        self.assertIn('name="classe_id"', content)
        self.assertIn('name="professeur_id"', content)
        self.assertIn('name="cours_id"', content)
        self.assertIn('name="jour"', content)
        self.assertIn('name="salle"', content)
        self.assertIn('name="heure_debut"', content)
        self.assertIn('name="heure_fin"', content)
        self.assertIn('id="btnSubmitAjouterEmploi"', content)

    def test_style_css_mobile_modal_rules(self):
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css = f.read()

        self.assertIn('.modal-fullscreen-md-down', css)
        self.assertIn('-webkit-overflow-scrolling: touch', css)
        self.assertIn('position: sticky', css)
        self.assertIn('.modal-section-card', css)


if __name__ == '__main__':
    unittest.main()
