import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

from app import create_app, db
from app.models import Ecole, Utilisateur, MessageQueue
from app.services.whatsapp_queue import enqueue_message, process_queue, STATUS_SENT, STATUS_FAILED

class TestWhatsappSecurite(unittest.TestCase):
    def setUp(self):
        from app.config import get_config
        config = get_config()
        self.app = create_app(config)
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole Test WA")
        self.ecole.whatsapp_enabled = False
        db.session.add(self.ecole)
        db.session.commit()

        self.user = Utilisateur(
            nom="Admin",
            email="admin_wa@test.com",
            role="admin",
            ecole_id=self.ecole.id,
            telephone="22790000000"
        )
        self.user.set_mot_de_passe("password")
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    @patch('app.routes.whatsapp.requests.get')
    def test_passerelle_status_sync(self, mock_get):
        # 3. Transition de statut connecté -> ecole.whatsapp_enabled passe à True
        from app.routes.whatsapp import get_whatsapp_gateway_status
        
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "CONNECTE", "connected": True}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        status = get_whatsapp_gateway_status(self.ecole.id, include_qr=False)
        self.assertTrue(status["connected"])
        
        ecole = db.session.get(Ecole, self.ecole.id)
        self.assertTrue(ecole.whatsapp_enabled)

        # Deconnexion -> passe à False
        mock_resp.json.return_value = {"status": "DECONNECTE", "connected": False}
        status = get_whatsapp_gateway_status(self.ecole.id, include_qr=False)
        self.assertFalse(status["connected"])
        
        ecole = db.session.get(Ecole, self.ecole.id)
        self.assertFalse(ecole.whatsapp_enabled)

    @patch('app.services.whatsapp_queue.requests.post')
    def test_envoi_baileys_headers(self, mock_post):
        # 1. Verification que le header X-Gateway-Secret est envoye
        from app.services.whatsapp_queue import envoyer_via_baileys
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_post.return_value = mock_resp

        queue_item = MessageQueue(ecole_id=self.ecole.id)
        result = envoyer_via_baileys("22790000000", "test msg", queue_item)
        self.assertTrue(result)
        
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertIn("headers", kwargs)
        self.assertIn("X-Gateway-Secret", kwargs["headers"])

    def test_protection_mots_de_passe(self):
        # 2. Le mot de passe n'est pas conservé en clair dans la table d'historique
        msg = enqueue_message(
            self.ecole.id, 
            "22790000000", 
            "Ton nouveau mot de passe est: secret123", 
            type_message="auth_credentials",
            commit=True
        )
        self.assertIn("secret123", msg.message)

        # On simule un envoi reussi
        def mock_send(dest, text, item):
            return True
        
        process_queue(mock_send, ecole_id=self.ecole.id)
        
        db.session.refresh(msg)
        self.assertEqual(msg.statut, STATUS_SENT)
        self.assertNotIn("secret123", msg.message)
        self.assertIn("[CONFIDENTIEL", msg.message)

if __name__ == '__main__':
    unittest.main()
