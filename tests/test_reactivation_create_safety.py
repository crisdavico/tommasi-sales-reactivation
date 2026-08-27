from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import ReactivationServiceTester


@tagged("post_install", "-at_install")
class TestReactivationCreateSafety(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Service = cls.env["tommasi.reactivation.service"]
        cls.Config = cls.env["tommasi.reactivation.config"]
        cls.Seller = cls.env["tommasi.reactivation.seller"]
        cls.agent_group = cls.env.ref(
            "tommasi_sales_reactivation.group_reactivation_agent"
        )
        cls.seller_user = cls.env["res.users"].create(
            {
                "name": "Safety Seller",
                "login": "safety_seller_test",
                "email": "safety_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.seller_user.partner_id.write(
            {
                "mobile": "+5491199990001",
            }
        )
        config = cls.Config.get_singleton()
        cls.Seller.create({"config_id": config.id, "user_id": cls.seller_user.id})
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Safety Customer",
                "vat": "30-55556666-7",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Safety Product",
                "default_code": "SAFE-001",
                "type": "product",
                "list_price": 100.0,
            }
        )
        cls.stage_pendiente = cls.env.ref(
            "tommasi_sales_reactivation.stage_pendiente_revision"
        )

    def _service(self):
        return ReactivationServiceTester(self.Service.with_user(self.agent_user))

    def setUp(self):
        super().setUp()
        self.agent_user = self.env["res.users"].create(
            {
                "name": "Safety Agent",
                "login": "safety_agent_%s" % self.id(),
                "email": "safety_agent_%s@example.com" % self.id(),
                "groups_id": [(6, 0, [self.agent_group.id])],
            }
        )
        self.env["stock.quant"].search(
            [("product_id", "=", self.product.id)]
        ).unlink()
        self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": self.env.ref("stock.stock_location_stock").id,
                "quantity": 50.0,
            }
        )

    def _operation_key(self, cycle_id="safety-cycle-001"):
        return self._service()._build_reactivation_operation_key(
            cycle_id,
            self.seller_user.id,
            self.customer.id,
        )

    def _valid_create_payload(self, **overrides):
        payload = {
            "customer_id": self.customer.id,
            "seller_id": self.seller_user.id,
            "client_message": "Hola, le escribo de Tommasi con una oferta.",
            "priority_label": "high",
            "source": "Agente Comercial",
            "trigger_type": "inactivity",
            "confidence": 0.85,
            "cycle_id": "safety-cycle-001",
            "operation_key": self._operation_key(),
            "suggested_products": [
                {
                    "product_id": self.product.id,
                    "sku": self.product.default_code,
                    "name": self.product.name,
                    "list_price": 100.0,
                    "available_qty": 10.0,
                }
            ],
        }
        payload.update(overrides)
        return payload

    def test_v2_create_persists_operation_key(self):
        result = self._service().create_crm_opportunity(
            payload=self._valid_create_payload()
        )
        self.assertEqual(result["outcome"], "created")
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        self.assertEqual(lead.reactivation_operation_key, self._operation_key())

    def test_v2_create_includes_opportunity_url(self):
        result = self._service().create_crm_opportunity(
            payload=self._valid_create_payload()
        )
        self.assertEqual(result["outcome"], "created")
        opportunity_id = result["opportunity_id"]
        opportunity_url = result.get("opportunity_url")
        self.assertTrue(opportunity_url)
        self.assertIn("crm.lead", opportunity_url)
        self.assertIn("id=%s" % opportunity_id, opportunity_url)
        self.assertIn("view_type=form", opportunity_url)

    def test_v2_idempotent_replay_returns_existing(self):
        first = self._service().create_crm_opportunity(
            payload=self._valid_create_payload()
        )
        second = self._service().create_crm_opportunity(
            payload=self._valid_create_payload()
        )
        self.assertEqual(first["outcome"], "created")
        self.assertEqual(second["outcome"], "existing")
        self.assertEqual(second["opportunity_id"], first["opportunity_id"])
        self.assertTrue(second.get("opportunity_url"))
        self.assertIn(
            "id=%s" % second["opportunity_id"],
            second["opportunity_url"],
        )
        self.assertEqual(
            self.env["crm.lead"].search_count(
                [
                    ("reactivation_operation_key", "=", self._operation_key()),
                ]
            ),
            1,
        )

    def test_v2_rejects_open_opportunity_for_customer(self):
        self._service().create_crm_opportunity(payload=self._valid_create_payload())
        other_key = self._service()._build_reactivation_operation_key(
            "safety-cycle-002",
            self.seller_user.id,
            self.customer.id,
        )
        result = self._service().create_crm_opportunity(
            payload=self._valid_create_payload(
                cycle_id="safety-cycle-002",
                operation_key=other_key,
            )
        )
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual(result["reason"], "open_opportunity")

    def test_v2_rejects_within_cooldown_after_archived_lead(self):
        first = self._service().create_crm_opportunity(
            payload=self._valid_create_payload()
        )
        lead = self.env["crm.lead"].browse(first["opportunity_id"])
        lead.write({"active": False})
        other_key = self._service()._build_reactivation_operation_key(
            "safety-cycle-003",
            self.seller_user.id,
            self.customer.id,
        )
        result = self._service().create_crm_opportunity(
            payload=self._valid_create_payload(
                cycle_id="safety-cycle-003",
                operation_key=other_key,
            )
        )
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual(result["reason"], "cooldown_active")

    def test_v2_allows_create_after_cooldown_window(self):
        first = self._service().create_crm_opportunity(
            payload=self._valid_create_payload()
        )
        lead = self.env["crm.lead"].browse(first["opportunity_id"])
        lead.write({"active": False})
        config = self.Config.get_singleton()
        old_date = fields.Datetime.now() - timedelta(days=config.cooldown_days + 1)
        self.env.cr.execute(
            "UPDATE crm_lead SET create_date = %s WHERE id = %s",
            (fields.Datetime.to_string(old_date), lead.id),
        )
        lead.invalidate_cache(["create_date"], [lead.id])
        other_key = self._service()._build_reactivation_operation_key(
            "safety-cycle-004",
            self.seller_user.id,
            self.customer.id,
        )
        result = self._service().create_crm_opportunity(
            payload=self._valid_create_payload(
                cycle_id="safety-cycle-004",
                operation_key=other_key,
            )
        )
        self.assertEqual(result["outcome"], "created")

    def test_v2_rejects_when_seller_cap_reached(self):
        config = self.Config.get_singleton()
        config.write({"opportunity_cap_per_seller": 1})
        self._service().create_crm_opportunity(payload=self._valid_create_payload())
        other_customer = self.env["res.partner"].create(
            {
                "name": "Cap Customer",
                "vat": "30-44443333-2",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        other_key = self._service()._build_reactivation_operation_key(
            "safety-cycle-005",
            self.seller_user.id,
            other_customer.id,
        )
        result = self._service().create_crm_opportunity(
            payload=self._valid_create_payload(
                customer_id=other_customer.id,
                cycle_id="safety-cycle-005",
                operation_key=other_key,
            )
        )
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual(result["reason"], "seller_cap_exceeded")

    def test_v2_creates_when_seller_cap_enforcement_disabled(self):
        config = self.Config.get_singleton()
        config.write({"opportunity_cap_per_seller": 1})
        self._service().create_crm_opportunity(payload=self._valid_create_payload())
        other_customer = self.env["res.partner"].create(
            {
                "name": "Cap Bypass Customer",
                "vat": "30-33332222-1",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        other_key = self._service()._build_reactivation_operation_key(
            "safety-cycle-006",
            self.seller_user.id,
            other_customer.id,
        )
        result = self._service().create_crm_opportunity(
            payload=self._valid_create_payload(
                customer_id=other_customer.id,
                cycle_id="safety-cycle-006",
                operation_key=other_key,
                enforce_seller_cap=False,
            )
        )
        self.assertEqual(result["outcome"], "created")
        self.assertTrue(result.get("opportunity_id"))

    def test_v2_batch_wraps_contract_version(self):
        result = self._service().create_crm_opportunity(
            payloads=[self._valid_create_payload()]
        )
        self.assertEqual(result["contract_version"], 2)
        self.assertEqual(result["results"][0]["outcome"], "created")

    def test_dedup_mode_includes_archived_agent_opportunities(self):
        first = self._service().create_crm_opportunity(
            payload=self._valid_create_payload()
        )
        lead = self.env["crm.lead"].browse(first["opportunity_id"])
        lead.write({"active": False})
        result = self._service().get_agent_opportunities(
            customer_ids=[self.customer.id],
            seller_id=self.seller_user.id,
            statuses=["Pendiente de revisión", "Cliente contactado"],
            dedup_mode=True,
        )
        customer_key = str(self.customer.id)
        opportunities = result["results"][customer_key]["opportunities"]
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0]["opportunity_id"], lead.id)
    def test_legacy_create_without_operation_key_unchanged(self):
        payload = self._valid_create_payload()
        payload.pop("operation_key")
        result = self._service().create_crm_opportunity(payload=payload)
        self.assertIn("opportunity_id", result)
        self.assertNotIn("outcome", result)

