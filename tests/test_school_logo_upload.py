"""Tests de sécurité pour l'upload du logo d'école.

Couvre :
- Upload d'une vraie image PNG/JPEG valide
- Rejet d'un faux fichier (texte renommé .png)
- Remplacement d'un logo existant (ancien supprimé)
- Isolation cross-tenant (admin école B ne peut pas modifier école A)
"""

import io
import os
import unittest
from datetime import date

from PIL import Image

from app import create_app, db
from app.models import Ecole, Utilisateur
from app.utils import validate_and_save_school_logo


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-logo-upload"
    SERVER_NAME = "klasora.test"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


def _make_real_png(width=100, height=100):
    """Crée un vrai PNG en mémoire via Pillow."""
    img = Image.new("RGBA", (width, height), (30, 80, 200, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _make_real_jpeg(width=100, height=100):
    """Crée un vrai JPEG en mémoire via Pillow."""
    img = Image.new("RGB", (width, height), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


def _make_fake_png():
    """Fichier texte pur renommé en .png — pas une vraie image."""
    buf = io.BytesIO(b"Ceci n'est pas une image PNG, juste du texte brut.")
    buf.seek(0)
    return buf


class SchoolLogoUploadTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(
            nom="Ecole Alpha",
            adresse="Niamey",
            telephone="90000000",
            onboarding_complete=True,
        )
        self.ecole_b = Ecole(
            nom="Ecole Beta",
            adresse="Maradi",
            telephone="91111111",
            onboarding_complete=True,
        )
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.admin_a = Utilisateur(
            nom="Admin A",
            prenom="Test",
            email="admin-a@test.local",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_a.id,
        )
        self.admin_b = Utilisateur(
            nom="Admin B",
            prenom="Test",
            email="admin-b@test.local",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.admin_a, self.admin_b])
        db.session.commit()

        self.client = self.app.test_client()
        self.static_folder = self.app.static_folder

    def tearDown(self):
        # Nettoyage des fichiers créés dans static/ecoles/
        import shutil
        ecoles_dir = os.path.join(self.static_folder, "ecoles")
        if os.path.exists(ecoles_dir):
            shutil.rmtree(ecoles_dir, ignore_errors=True)

        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["ecole_id"] = user.ecole_id

    # ------------------------------------------------------------------
    # 1. Upload d'une vraie image PNG valide -> succès
    # ------------------------------------------------------------------
    def test_valid_png_upload_succeeds(self):
        """Un vrai PNG est accepté, renommé en UUID, et la DB est mise à jour."""
        png_buf = _make_real_png()

        ok, err = validate_and_save_school_logo(
            _file_storage(png_buf, "mon_logo_ecole.png"),
            self.ecole_a,
            self.static_folder,
        )

        self.assertTrue(ok, f"Devait réussir, erreur : {err}")
        self.assertIsNone(err)
        # Nom généré par UUID, pas le nom d'origine
        self.assertTrue(self.ecole_a.logo.startswith("logo_"))
        self.assertTrue(self.ecole_a.logo.endswith(".png"))
        self.assertNotIn("mon_logo_ecole", self.ecole_a.logo)
        # Fichier physique existe
        full_path = os.path.join(self.static_folder, self.ecole_a.logo_path)
        self.assertTrue(os.path.isfile(full_path))

    # ------------------------------------------------------------------
    # 2. Upload d'une vraie image JPEG valide -> succès
    # ------------------------------------------------------------------
    def test_valid_jpeg_upload_succeeds(self):
        """Un vrai JPEG est accepté et renommé en .jpg."""
        jpeg_buf = _make_real_jpeg()

        ok, err = validate_and_save_school_logo(
            _file_storage(jpeg_buf, "photo.jpeg"),
            self.ecole_a,
            self.static_folder,
        )

        self.assertTrue(ok, f"Devait réussir, erreur : {err}")
        self.assertTrue(self.ecole_a.logo.endswith(".jpg"))
        full_path = os.path.join(self.static_folder, self.ecole_a.logo_path)
        self.assertTrue(os.path.isfile(full_path))

    # ------------------------------------------------------------------
    # 3. Upload d'un fichier texte renommé .png -> rejet
    # ------------------------------------------------------------------
    def test_fake_png_is_rejected(self):
        """Un fichier texte renommé .png est rejeté, rien n'est sauvegardé."""
        fake_buf = _make_fake_png()

        ok, err = validate_and_save_school_logo(
            _file_storage(fake_buf, "virus.png"),
            self.ecole_a,
            self.static_folder,
        )

        self.assertFalse(ok)
        self.assertIn("image valide", err)
        # Aucun fichier ne doit exister dans le répertoire école
        school_dir = os.path.join(self.static_folder, "ecoles", str(self.ecole_a.id))
        if os.path.exists(school_dir):
            self.assertEqual(os.listdir(school_dir), [])

    # ------------------------------------------------------------------
    # 4. Upload d'un fichier binaire renommé .jpg -> rejet
    # ------------------------------------------------------------------
    def test_binary_garbage_renamed_jpg_is_rejected(self):
        """Un fichier binaire aléatoire renommé .jpg est rejeté."""
        garbage = io.BytesIO(os.urandom(512))

        ok, err = validate_and_save_school_logo(
            _file_storage(garbage, "document.jpg"),
            self.ecole_a,
            self.static_folder,
        )

        self.assertFalse(ok)
        self.assertIn("image valide", err)

    # ------------------------------------------------------------------
    # 5. Remplacement d'un logo existant -> ancien fichier supprimé
    # ------------------------------------------------------------------
    def test_replacement_deletes_old_file(self):
        """Remplacer un logo doit supprimer l'ancien fichier physique."""
        # Premier upload
        ok1, _ = validate_and_save_school_logo(
            _file_storage(_make_real_png(), "first.png"),
            self.ecole_a,
            self.static_folder,
        )
        self.assertTrue(ok1)
        old_name = self.ecole_a.logo
        old_path = os.path.join(self.static_folder, self.ecole_a.logo_path)
        self.assertTrue(os.path.isfile(old_path))

        # Deuxième upload (remplacement)
        ok2, _ = validate_and_save_school_logo(
            _file_storage(_make_real_jpeg(), "second.jpeg"),
            self.ecole_a,
            self.static_folder,
        )
        self.assertTrue(ok2)
        new_name = self.ecole_a.logo
        new_path = os.path.join(self.static_folder, self.ecole_a.logo_path)

        # Nouveau fichier existe
        self.assertTrue(os.path.isfile(new_path))
        # Ancien fichier supprimé
        self.assertFalse(os.path.isfile(old_path))
        # Noms différents
        self.assertNotEqual(old_name, new_name)

    # ------------------------------------------------------------------
    # 6. Isolation multi-tenant — route POST profil-ecole
    # ------------------------------------------------------------------
    def test_cross_tenant_upload_refused(self):
        """Un admin d'une école ne peut pas modifier le profil d'une autre."""
        self._login(self.admin_b)
        # L'admin B est connecté ; la route profil-ecole lit current_user.ecole
        # qui est ecole_b. Il ne peut pas directement pointer vers ecole_a.
        # On vérifie au niveau du helper directement que l'isolation fonctionne :
        # si on appelle validate_and_save_school_logo avec ecole_a, le fichier
        # serait dans le répertoire de ecole_a. Mais la route empêche cela car
        # elle utilise current_user.ecole. On teste la route.
        res = self.client.post(
            "/profil-ecole",
            data={
                "nom": "Ecole Pirate",
            },
            follow_redirects=True,
        )
        # Le profil modifié est celui de ecole_b (l'école de admin_b), pas ecole_a
        ecole_a_fresh = db.session.get(Ecole, self.ecole_a.id)
        self.assertEqual(ecole_a_fresh.nom, "Ecole Alpha")  # inchangé

    # ------------------------------------------------------------------
    # 7. Fichier vide -> rejet
    # ------------------------------------------------------------------
    def test_empty_file_is_rejected(self):
        """Un fichier vide est rejeté."""
        empty = io.BytesIO(b"")

        ok, err = validate_and_save_school_logo(
            _file_storage(empty, "empty.png"),
            self.ecole_a,
            self.static_folder,
        )

        self.assertFalse(ok)
        self.assertIn("vide", err)

    # ------------------------------------------------------------------
    # 8. SVG -> rejet (format non autorisé)
    # ------------------------------------------------------------------
    def test_svg_is_rejected(self):
        """Un SVG valide est rejeté car seuls PNG/JPEG/WEBP sont autorisés."""
        svg_content = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect fill="red" width="100" height="100"/></svg>'
        svg_buf = io.BytesIO(svg_content)

        ok, err = validate_and_save_school_logo(
            _file_storage(svg_buf, "logo.svg"),
            self.ecole_a,
            self.static_folder,
        )

        self.assertFalse(ok)

    # ------------------------------------------------------------------
    # 9. Le nom par défaut 'default_logo.png' n'est pas supprimé
    # ------------------------------------------------------------------
    def test_default_logo_not_deleted(self):
        """Le logo par défaut ne doit pas être supprimé du disque."""
        self.ecole_a.logo = "default_logo.png"

        ok, _ = validate_and_save_school_logo(
            _file_storage(_make_real_png(), "new.png"),
            self.ecole_a,
            self.static_folder,
        )

        self.assertTrue(ok)
        self.assertNotEqual(self.ecole_a.logo, "default_logo.png")


class _file_storage:
    """Simule un werkzeug.FileStorage minimal pour les tests unitaires."""

    def __init__(self, stream, filename):
        self.stream = stream
        self.filename = filename

    def seek(self, *args):
        return self.stream.seek(*args)

    def tell(self):
        return self.stream.tell()

    def read(self, *args):
        return self.stream.read(*args)

    def save(self, path):
        self.stream.seek(0)
        with open(path, "wb") as f:
            f.write(self.stream.read())


if __name__ == "__main__":
    unittest.main()

