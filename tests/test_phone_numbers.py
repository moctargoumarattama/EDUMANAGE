import unittest

from app.services.phone_numbers import normaliser_numero_whatsapp, normaliser_telephone_international


class PhoneNumbersTestCase(unittest.TestCase):
    def test_whatsapp_normalises_local_niger_numbers(self):
        self.assertEqual(normaliser_numero_whatsapp("90123456"), "+22790123456")
        self.assertEqual(normaliser_numero_whatsapp("80 12 34 56"), "+22780123456")

    def test_whatsapp_normalises_local_morocco_numbers(self):
        self.assertEqual(normaliser_numero_whatsapp("0770010264"), "+212770010264")
        self.assertEqual(normaliser_numero_whatsapp("06 12 34 56 78"), "+212612345678")
        self.assertNotEqual(normaliser_numero_whatsapp("0770010264"), "+0770010264")

    def test_whatsapp_normalises_international_prefixes(self):
        self.assertEqual(normaliser_numero_whatsapp("+212 7 70 01 02 64"), "+212770010264")
        self.assertEqual(normaliser_numero_whatsapp("00212 7 70 01 02 64"), "+212770010264")
        self.assertEqual(normaliser_numero_whatsapp("+227 90 12 34 56"), "+22790123456")
        self.assertEqual(normaliser_numero_whatsapp("+226 70 12 34 56"), "+22670123456")

    def test_whatsapp_never_returns_plus_zero_number(self):
        for raw in ("+0770010264", "+0 77 00 10 264", "000770010264"):
            numero = normaliser_numero_whatsapp(raw)
            self.assertFalse(numero and numero.startswith("+0"))
            self.assertIsNone(numero)

    def test_storage_normalisation_accepts_morocco_without_breaking_niger_local(self):
        self.assertEqual(normaliser_telephone_international("+227 90 12 34 56"), "90123456")
        self.assertEqual(normaliser_telephone_international("90123456"), "90123456")
        self.assertEqual(normaliser_telephone_international("0770010264"), "+212770010264")
        self.assertIsNone(normaliser_telephone_international("+0770010264"))


if __name__ == "__main__":
    unittest.main()
