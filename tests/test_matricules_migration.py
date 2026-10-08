"""Migration des matricules sur des bases SQLite temporaires, sans Flask."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from scripts import migrate_matricules_format as migration


class MatriculesMigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="klasora-matricules-migration-")
        self.database_path = Path(self.temporary.name) / "ancienne.db"
        self.engine = sa.create_engine(sa.URL.create("sqlite", database=str(self.database_path)))
        self.metadata = sa.MetaData()

    def tearDown(self):
        self.engine.dispose()
        self.temporary.cleanup()

    def _schema(self, *, matricule=False, inscriptions=False, sequences=False):
        sa.Table("ecole", self.metadata,
                 sa.Column("id", sa.Integer, primary_key=True),
                 sa.Column("nom", sa.String(100), nullable=False))
        sa.Table("utilisateur", self.metadata,
                 sa.Column("id", sa.Integer, primary_key=True),
                 sa.Column("ecole_id", sa.Integer, nullable=False),
                 sa.Column("mot_de_passe", sa.String(255), nullable=False))
        columns = [
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("ecole_id", sa.Integer, sa.ForeignKey("ecole.id"), nullable=False),
            sa.Column("parent_id", sa.Integer, sa.ForeignKey("utilisateur.id")),
            sa.Column("nom", sa.String(100), nullable=False),
            sa.Column("prenom", sa.String(100), nullable=False),
            sa.Column("date_naissance", sa.Date, nullable=False),
            sa.Column("date_inscription", sa.DateTime),
            sa.Column("updated_at", sa.DateTime),
            sa.Column("annee_premiere_ecole", sa.Integer),
            sa.Column("code_parent", sa.String(10), unique=True),
            sa.Column("frais_annuels", sa.Float),
        ]
        if matricule:
            columns.append(sa.Column("matricule", sa.String(12)))
        sa.Table("eleve", self.metadata, *columns)
        sa.Table("note", self.metadata,
                 sa.Column("id", sa.Integer, primary_key=True),
                 sa.Column("eleve_id", sa.Integer, sa.ForeignKey("eleve.id"), nullable=False),
                 sa.Column("valeur", sa.Float, nullable=False),
                 sa.Column("updated_at", sa.DateTime))
        if inscriptions:
            sa.Table("annee_scolaire", self.metadata,
                     sa.Column("id", sa.Integer, primary_key=True),
                     sa.Column("ecole_id", sa.Integer, nullable=False),
                     sa.Column("date_debut", sa.Date, nullable=False))
            sa.Table("inscriptions", self.metadata,
                     sa.Column("id", sa.Integer, primary_key=True),
                     sa.Column("eleve_id", sa.Integer, sa.ForeignKey("eleve.id"), nullable=False),
                     sa.Column("annee_scolaire_id", sa.Integer, sa.ForeignKey("annee_scolaire.id"), nullable=False))
        if sequences:
            sa.Table("matricule_sequence", self.metadata,
                     sa.Column("ecole_id", sa.Integer, sa.ForeignKey("ecole.id"), primary_key=True),
                     sa.Column("prefixe", sa.String(2), primary_key=True),
                     sa.Column("dernier_numero", sa.Integer, nullable=False))
        self.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(self.metadata.tables["ecole"].insert(), [
                {"id": 1, "nom": "Ecole test A"},
                {"id": 2, "nom": "Ecole test B"},
            ])
            connection.execute(self.metadata.tables["utilisateur"].insert(), {
                "id": 1, "ecole_id": 1, "mot_de_passe": "hash-parent-de-test-inchangé",
            })

    def _eleve(self, eleve_id, **changes):
        values = {
            "id": eleve_id,
            "ecole_id": 1,
            "parent_id": 1,
            "nom": f"Nom{eleve_id}",
            "prenom": f"Prenom{eleve_id}",
            "date_naissance": date(2012, 1, 1),
            "date_inscription": datetime(2024, 9, eleve_id),
            "updated_at": datetime(2025, 2, 3, 4, 5, 6),
            "annee_premiere_ecole": None,
            "code_parent": f"{58000000 + eleve_id:08d}",
            "frais_annuels": 150000.0,
        }
        values.update(changes)
        with self.engine.begin() as connection:
            connection.execute(self.metadata.tables["eleve"].insert(), values)

    def _apply(self):
        with self.engine.connect() as connection:
            # La CLI utilise aussi BEGIN IMMEDIATE pour rendre DDL et données atomiques.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                result = migration.migrate_matricules(connection)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _rows(self, table):
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(
                sa.text(f"SELECT * FROM {table} ORDER BY id")
            ).mappings()]

    def _matricules(self):
        return {row["id"]: row["matricule"] for row in self._rows("eleve")}

    def test_migre_ancien_schema_et_cree_index_et_sequence(self):
        self._schema()
        self._eleve(1)
        self._eleve(2)

        result = self._apply()

        self.assertEqual(result["migres"], 2)
        self.assertEqual(self._matricules(), {1: "24-0001", 2: "24-0002"})
        inspector = sa.inspect(self.engine)
        self.assertIn("matricule", {column["name"] for column in inspector.get_columns("eleve")})
        self.assertTrue(inspector.has_table("matricule_sequence"))
        unique = next(index for index in inspector.get_indexes("eleve")
                      if index["name"] == "uq_eleve_ecole_matricule")
        self.assertTrue(unique["unique"])
        self.assertEqual(unique["column_names"], ["ecole_id", "matricule"])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(sa.text(
                "SELECT dernier_numero FROM matricule_sequence WHERE ecole_id = 1 AND prefixe = '24'"
            )).scalar_one(), 2)

    def test_annee_premiere_inscription_prime_sur_creation_et_champ_legacy(self):
        self._schema(inscriptions=True)
        self._eleve(1, date_inscription=datetime(2026, 1, 5), annee_premiere_ecole=2025)
        self._eleve(2, date_inscription=datetime(2026, 1, 6), annee_premiere_ecole=2023)
        with self.engine.begin() as connection:
            connection.execute(self.metadata.tables["annee_scolaire"].insert(), [
                {"id": 1, "ecole_id": 1, "date_debut": date(2025, 9, 1)},
                {"id": 2, "ecole_id": 1, "date_debut": date(2024, 9, 1)},
            ])
            connection.execute(self.metadata.tables["inscriptions"].insert(), [
                {"id": 1, "eleve_id": 1, "annee_scolaire_id": 1},
                {"id": 2, "eleve_id": 1, "annee_scolaire_id": 2},
            ])

        self._apply()

        self.assertEqual(self._matricules(), {1: "24-0001", 2: "23-0001"})

    def test_fallback_date_creation_puis_2024_si_aucune_information(self):
        self._schema()
        self._eleve(1, date_inscription=datetime(2025, 1, 1))
        self._eleve(2, date_inscription=None)

        self._apply()

        self.assertEqual(self._matricules(), {1: "25-0001", 2: "24-0001"})

    def test_preserve_conformes_et_reprend_compteur_superieur_reserve(self):
        self._schema(matricule=True, sequences=True)
        self._eleve(1, matricule="24-0042")
        self._eleve(2, matricule="ancien-code")
        self._eleve(3, matricule="24-0000")
        with self.engine.begin() as connection:
            connection.execute(self.metadata.tables["matricule_sequence"].insert(), {
                "ecole_id": 1, "prefixe": "24", "dernier_numero": 50,
            })

        self._apply()

        self.assertEqual(self._matricules(), {1: "24-0042", 2: "24-0051", 3: "24-0052"})

    def test_idempotence_preserve_toutes_autres_colonnes_comptes_et_notes(self):
        self._schema()
        self._eleve(1)
        self._eleve(2)
        with self.engine.begin() as connection:
            connection.execute(self.metadata.tables["note"].insert(), {
                "id": 1, "eleve_id": 1, "valeur": 17.5,
                "updated_at": datetime(2025, 2, 1),
            })
        eleves_avant = self._rows("eleve")
        comptes_avant = self._rows("utilisateur")
        notes_avant = self._rows("note")

        self._apply()
        eleves_apres = self._rows("eleve")
        second = self._apply()

        self.assertEqual(second["migres"], 0)
        self.assertEqual(second["a_migrer"], 0)
        self.assertEqual(self._rows("eleve"), eleves_apres)
        self.assertEqual([{key: value for key, value in row.items() if key != "matricule"}
                          for row in eleves_apres], eleves_avant)
        self.assertEqual(self._rows("utilisateur"), comptes_avant)
        self.assertEqual(self._rows("note"), notes_avant)

    def test_unicite_par_ecole_et_compteurs_independants(self):
        self._schema()
        self._eleve(1)
        self._eleve(2, ecole_id=2)

        self._apply()

        self.assertEqual(self._matricules(), {1: "24-0001", 2: "24-0001"})
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(sa.text("""
                    INSERT INTO eleve (id, ecole_id, nom, prenom, date_naissance, matricule)
                    VALUES (3, 1, 'Test', 'Doublon', '2012-01-01', '24-0001')
                """))
        self.assertEqual(len(self._rows("eleve")), 2)

    def test_doublon_conforme_annule_migration_avant_toute_modification(self):
        self._schema(matricule=True)
        self._eleve(1, matricule="24-0001")
        self._eleve(2, matricule="24-0001")
        before = self._rows("eleve")

        with self.assertRaisesRegex(ValueError, "même matricule"):
            self._apply()

        self.assertEqual(self._rows("eleve"), before)
        self.assertFalse(sa.inspect(self.engine).has_table("matricule_sequence"))

    def test_depassement_capacite_annule_aussi_migrations_autres_annees(self):
        self._schema(matricule=True)
        self._eleve(1, date_inscription=datetime(2023, 1, 1), matricule=None)
        self._eleve(2, matricule="24-9999")
        self._eleve(3, matricule=None)
        before = self._rows("eleve")

        with self.assertRaisesRegex(ValueError, "9 999"):
            self._apply()

        self.assertEqual(self._rows("eleve"), before)
        self.assertFalse(sa.inspect(self.engine).has_table("matricule_sequence"))

    def test_erreur_apres_alter_et_donnees_annule_aussi_le_schema(self):
        self._schema()
        self._eleve(1)
        self._eleve(2)
        before = self._rows("eleve")

        def echouer_apres_migration(connection):
            self.assertIn("matricule", {
                column["name"] for column in sa.inspect(connection).get_columns("eleve")
            })
            self.assertEqual(connection.execute(sa.text(
                "SELECT matricule FROM eleve ORDER BY id"
            )).scalars().all(), ["24-0001", "24-0002"])
            self.assertTrue(sa.inspect(connection).has_table("matricule_sequence"))
            raise RuntimeError("Erreur tardive de test")

        with patch.object(migration, "_proteger_permanence", side_effect=echouer_apres_migration) as protection:
            with self.assertRaisesRegex(RuntimeError, "Erreur tardive"):
                # Pas de BEGIN IMMEDIATE dans le test : exercer la protection commune
                # utilisée aussi par Alembic, au-delà du chemin explicite de la CLI.
                with self.engine.begin() as connection:
                    migration.migrate_matricules(connection)
            protection.assert_called_once()

        self.assertEqual(self._rows("eleve"), before)
        inspector = sa.inspect(self.engine)
        self.assertNotIn("matricule", {column["name"] for column in inspector.get_columns("eleve")})
        self.assertFalse(inspector.has_table("matricule_sequence"))

    def test_trigger_bloque_remplacement_et_effacement_sans_bloquer_autres_champs(self):
        self._schema()
        self._eleve(1)
        self._apply()

        for nouveau in ("24-0002", None, "ancien-code"):
            with self.subTest(nouveau=nouveau), self.assertRaises(IntegrityError):
                with self.engine.begin() as connection:
                    connection.execute(sa.text(
                        "UPDATE eleve SET matricule = :nouveau WHERE id = 1"
                    ), {"nouveau": nouveau})
        with self.engine.begin() as connection:
            connection.execute(sa.text("UPDATE eleve SET matricule = matricule, nom = 'Corrige' WHERE id = 1"))
        self.assertEqual(self._matricules()[1], "24-0001")
        self.assertEqual(self._rows("eleve")[0]["nom"], "Corrige")

    def test_previsualisation_ne_modifie_ni_schema_ni_donnees(self):
        self._schema()
        self._eleve(1)
        before = self._rows("eleve")

        with self.engine.connect() as connection:
            result = migration.migrate_matricules(connection, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["a_migrer"], 1)
        self.assertEqual(self._rows("eleve"), before)
        inspector = sa.inspect(self.engine)
        self.assertNotIn("matricule", {column["name"] for column in inspector.get_columns("eleve")})
        self.assertFalse(inspector.has_table("matricule_sequence"))

    def test_cli_sauvegarde_avant_application_et_reexecution_sans_changement(self):
        self._schema()
        self._eleve(1)
        before = self._rows("eleve")
        arguments = ["--database-url", self.engine.url.render_as_string(), "--apply"]
        output = io.StringIO()

        with patch.object(migration, "ROOT", Path(self.temporary.name)), redirect_stdout(output):
            self.assertEqual(migration.main(arguments), 0)

        result = json.loads(output.getvalue())
        backup_path = Path(result["sauvegarde"])
        self.assertTrue(backup_path.is_file())
        backup_engine = sa.create_engine(sa.URL.create("sqlite", database=str(backup_path)))
        try:
            self.assertNotIn("matricule", {column["name"] for column in sa.inspect(backup_engine).get_columns("eleve")})
            with backup_engine.connect() as connection:
                self.assertEqual([dict(row) for row in connection.execute(
                    sa.text("SELECT * FROM eleve ORDER BY id")
                ).mappings()], before)
        finally:
            backup_engine.dispose()
        self.assertEqual(self._matricules(), {1: "24-0001"})
        output = io.StringIO()
        with patch.object(migration, "ROOT", Path(self.temporary.name)), redirect_stdout(output):
            self.assertEqual(migration.main(arguments), 0)
        self.assertEqual(json.loads(output.getvalue())["migres"], 0)


if __name__ == "__main__":
    unittest.main()
