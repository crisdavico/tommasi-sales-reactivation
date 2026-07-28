from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestCrmLeadReactivation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Lead = cls.env["crm.lead"]
        cls.Config = cls.env["tommasi.reactivation.config"]
        cls.stage_pendiente = cls.env.ref(
            "tommasi_sales_reactivation.stage_pendiente_revision"
        )
        cls.stage_contactado = cls.env.ref(
            "tommasi_sales_reactivation.stage_cliente_contactado"
        )
        cls.config = cls.Config.get_singleton()

    def _create_lead(self, stage, name="Archive cron test lead"):
        return self.Lead.create(
            {
                "name": name,
                "type": "opportunity",
                "stage_id": stage.id,
                "company_id": self.config.company_id.id,
            }
        )

    def _set_create_date(self, lead, days_ago):
        create_date = fields.Datetime.now() - timedelta(days=days_ago)
        self.env.cr.execute(
            "UPDATE crm_lead SET create_date = %s WHERE id = %s",
            (create_date, lead.id),
        )
        lead.invalidate_cache(["create_date"], [lead.id])

    def test_reactivation_fields_default(self):
        lead = self.Lead.create(
            {"name": "Test reactivation lead", "type": "opportunity"}
        )
        self.assertEqual(lead.reactivation_source, "Agente Comercial")
        self.assertFalse(lead.reactivation_is_agent)

    def test_reactivation_fields_writable(self):
        lead = self.Lead.create(
            {
                "name": "Agent opportunity",
                "type": "opportunity",
                "reactivation_is_agent": True,
                "reactivation_attribution_id": "attr-test-001",
                "reactivation_trigger_type": "inactivity",
                "reactivation_confidence": 0.85,
                "reactivation_cycle_id": "cycle-2025-06-23",
                "reactivation_client_message": "Hola, tenemos una oferta para usted.",
            }
        )
        self.assertTrue(lead.reactivation_is_agent)
        self.assertEqual(lead.reactivation_attribution_id, "attr-test-001")
        self.assertEqual(lead.reactivation_trigger_type, "inactivity")
        self.assertEqual(lead.reactivation_confidence, 0.85)
        self.assertEqual(
            lead.reactivation_client_message, "Hola, tenemos una oferta para usted."
        )

    def test_reactivation_attribution_id_unique(self):
        self.Lead.create(
            {
                "name": "First agent opportunity",
                "type": "opportunity",
                "reactivation_is_agent": True,
                "reactivation_attribution_id": "attr-duplicate",
            }
        )
        with mute_logger("odoo.sql_db"):
            with self.assertRaises(IntegrityError):
                with self.cr.savepoint():
                    self.Lead.create(
                        {
                            "name": "Second agent opportunity",
                            "type": "opportunity",
                            "reactivation_is_agent": True,
                            "reactivation_attribution_id": "attr-duplicate",
                        }
                    )

    def test_custom_stages_exist(self):
        self.assertEqual(self.stage_pendiente.name, "Pendiente de revisión")
        self.assertEqual(self.stage_pendiente.sequence, 10)
        self.assertEqual(self.stage_contactado.name, "Cliente contactado")
        self.assertEqual(self.stage_contactado.sequence, 20)

    def test_cron_archives_stale_pendiente_revision_leads(self):
        self.config.write({"pending_review_archive_days": 15})
        lead = self._create_lead(self.stage_pendiente, "Stale pending lead")
        self._set_create_date(lead, 16)

        self.Lead.cron_archive_stale_pendiente_revision()

        self.assertFalse(lead.active)

    def test_cron_keeps_recent_pendiente_revision_leads(self):
        self.config.write({"pending_review_archive_days": 15})
        lead = self._create_lead(self.stage_pendiente, "Recent pending lead")
        self._set_create_date(lead, 5)

        self.Lead.cron_archive_stale_pendiente_revision()

        self.assertTrue(lead.active)

    def test_cron_skips_other_stages(self):
        self.config.write({"pending_review_archive_days": 15})
        lead = self._create_lead(self.stage_contactado, "Old contacted lead")
        self._set_create_date(lead, 30)

        self.Lead.cron_archive_stale_pendiente_revision()

        self.assertTrue(lead.active)

    def test_cron_uses_config_window(self):
        self.config.write({"pending_review_archive_days": 2})
        lead = self._create_lead(self.stage_pendiente, "Config window lead")
        self._set_create_date(lead, 3)

        self.Lead.cron_archive_stale_pendiente_revision()

        self.assertFalse(lead.active)

    def test_native_won_stage_available(self):
        won_stage = self.env.ref("crm.stage_lead4")
        self.assertTrue(won_stage.is_won)
