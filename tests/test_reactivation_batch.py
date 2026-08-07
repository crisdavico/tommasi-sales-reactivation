from datetime import date, timedelta
from unittest.mock import patch

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTester,
    post_test_out_invoice,
)
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestReactivationServiceBatch(TransactionCase):
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
                "name": "Batch Seller",
                "login": "batch_seller_test",
                "email": "batch_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.seller_user.partner_id.write(
            {
                "mobile": "+5491122334455",
            }
        )
        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Batch Agent",
                "login": "batch_agent_test",
                "email": "batch_agent_test@example.com",
                "groups_id": [(6, 0, [cls.agent_group.id])],
            }
        )
        config = cls.Config.get_singleton()
        cls.Seller.create({"config_id": config.id, "user_id": cls.seller_user.id})
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Batch Customer A",
                "vat": "30-10101010-1",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls.customer_b = cls.env["res.partner"].create(
            {
                "name": "Batch Customer B",
                "vat": "30-20202020-2",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls.customer_c = cls.env["res.partner"].create(
            {
                "name": "Batch Customer C",
                "vat": "30-30303030-3",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Batch Product",
                "default_code": "BATCH-001",
                "type": "product",
                "list_price": 100.0,
            }
        )
        cls.stage_pendiente = cls.env.ref(
            "tommasi_sales_reactivation.stage_pendiente_revision"
        )
        cls.other_seller = cls.env["res.users"].create(
            {
                "name": "Batch Other Seller",
                "login": "batch_other_seller_test",
                "email": "batch_other_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.foreign_customer = cls.env["res.partner"].create(
            {
                "name": "Batch Foreign Customer",
                "vat": "30-40404040-4",
                "user_id": cls.other_seller.id,
                "customer_rank": 1,
            }
        )
        for partner in (cls.customer, cls.customer_b, cls.customer_c):
            cls._bootstrap_qualify_partner(partner, cls.product)

    @classmethod
    def _bootstrap_qualify_partner(cls, partner, product, invoice_count=3):
        commercial = partner.commercial_partner_id
        commercial.sudo().write({"user_id": cls.seller_user.id, "customer_rank": 1})
        today = date.today()
        for index in range(invoice_count):
            invoice_date = fields.Date.to_string(
                today - timedelta(days=90 + 30 * index)
            )
            move = (
                cls.env["account.move"]
                .sudo()
                .create(
                    {
                        "move_type": "out_invoice",
                        "partner_id": commercial.id,
                        "invoice_date": invoice_date,
                        "date": invoice_date,
                        "invoice_line_ids": [
                            (
                                0,
                                0,
                                {
                                    "product_id": product.id,
                                    "quantity": 1,
                                    "price_unit": 100.0,
                                    "tax_ids": [(6, 0, [])],
                                },
                            )
                        ],
                    }
                )
            )
            post_test_out_invoice(move, seller=cls.seller_user)

    def _service(self):
        return ReactivationServiceTester(self.Service.with_user(self.agent_user))

    def _add_product_stock(self, product, quantity=10.0):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        self.env["stock.quant"].sudo().create(
            {
                "product_id": product.id,
                "location_id": warehouse.lot_stock_id.id,
                "quantity": quantity,
            }
        )

    def _valid_create_payload(self, customer=None, **overrides):
        customer = customer or self.customer
        payload = {
            "customer_id": customer.id,
            "seller_id": self.seller_user.id,
            "client_message": "Hola, le escribo de Tommasi con una oferta.",
            "priority_label": "high",
            "source": "Agente Comercial",
            "trigger_type": "inactivity",
            "confidence": 0.85,
            "cycle_id": "batch-test-cycle",
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

    def _create_agent_lead(self, customer):
        return (
            self.env["crm.lead"]
            .with_context(reactivation_seller_id=self.seller_user.id)
            .create(
                {
                    "name": "Batch agent opportunity",
                    "type": "opportunity",
                    "partner_id": customer.id,
                    "user_id": self.seller_user.id,
                    "stage_id": self.stage_pendiente.id,
                    "reactivation_is_agent": True,
                    "reactivation_attribution_id": "batch-attr-%s" % customer.id,
                    "reactivation_client_message": "Hola %s" % customer.name,
                    "reactivation_evidence_summary": "Evidencia %s" % customer.name,
                }
            )
        )

    # -- get_agent_opportunities batch ---------------------------------

    def test_scalar_get_agent_opportunities_unchanged(self):
        lead = self._create_agent_lead(self.customer)
        result = self._service().get_agent_opportunities(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
            statuses=["Pendiente de revisión"],
            include_client_message=True,
        )
        self.assertEqual(result["customer_id"], self.customer.id)
        self.assertEqual(result["seller_id"], self.seller_user.id)
        self.assertEqual(len(result["opportunities"]), 1)
        self.assertEqual(result["opportunities"][0]["opportunity_id"], lead.id)
        self.assertEqual(
            result["opportunities"][0]["client_message"], "Hola %s" % self.customer.name
        )
        self.assertEqual(
            result["opportunities"][0]["evidence_summary"],
            "Evidencia %s" % self.customer.name,
        )

    def test_batch_get_agent_opportunities_grouped_results(self):
        lead_a = self._create_agent_lead(self.customer)
        self._create_agent_lead(self.customer_b)
        result = self._service().get_agent_opportunities(
            customer_ids=[self.customer.id, self.customer_b.id, self.customer_c.id],
            seller_id=self.seller_user.id,
            statuses=["Pendiente de revisión"],
        )
        self.assertEqual(result["seller_id"], self.seller_user.id)
        self.assertIn(str(self.customer.id), result["results"])
        self.assertIn(str(self.customer_b.id), result["results"])
        self.assertIn(str(self.customer_c.id), result["results"])
        self.assertEqual(len(result["results"][str(self.customer.id)]["opportunities"]), 1)
        self.assertEqual(
            result["results"][str(self.customer.id)]["opportunities"][0]["opportunity_id"],
            lead_a.id,
        )
        self.assertEqual(
            len(result["results"][str(self.customer_b.id)]["opportunities"]), 1
        )
        self.assertEqual(
            result["results"][str(self.customer_c.id)]["opportunities"], []
        )

    def test_batch_get_agent_opportunities_stage_xml_id_lookup_cached(self):
        self._create_agent_lead(self.customer)
        self._create_agent_lead(self.customer_b)
        service = self._service()._service
        with patch.object(
            type(service),
            "_stage_xml_id_map_for",
            wraps=service._stage_xml_id_map_for,
        ) as map_lookup:
            result = self._service().get_agent_opportunities(
                customer_ids=[self.customer.id, self.customer_b.id],
                seller_id=self.seller_user.id,
                statuses=["Pendiente de revisión"],
            )
        self.assertEqual(len(result["results"][str(self.customer.id)]["opportunities"]), 1)
        self.assertEqual(len(result["results"][str(self.customer_b.id)]["opportunities"]), 1)
        self.assertEqual(
            result["results"][str(self.customer.id)]["opportunities"][0]["stage_xml_id"],
            "tommasi_sales_reactivation.stage_pendiente_revision",
        )
        map_lookup.assert_called_once()

    def test_batch_get_agent_opportunities_scope_error_isolation(self):
        self._create_agent_lead(self.customer)
        result = self._service().get_agent_opportunities(
            customer_ids=[
                self.customer.id,
                self.foreign_customer.id,
                self.customer_b.id,
            ],
            seller_id=self.seller_user.id,
            statuses=["Pendiente de revisión"],
        )
        foreign = result["results"][str(self.foreign_customer.id)]
        self.assertEqual(foreign["message"], "Customer not found for seller scope.")
        self.assertEqual(
            len(result["results"][str(self.customer.id)]["opportunities"]), 1
        )
        self.assertEqual(
            len(result["results"][str(self.customer_b.id)]["opportunities"]), 0
        )

    def test_batch_get_agent_opportunities_mutual_exclusion(self):
        service = self._service()
        neither = service.get_agent_opportunities(
            seller_id=self.seller_user.id,
            statuses=["Pendiente de revisión"],
        )
        self.assertEqual(
            neither["message"],
            "Provide exactly one of customer_id or customer_ids.",
        )
        both = service.get_agent_opportunities(
            customer_id=self.customer.id,
            customer_ids=[self.customer_b.id],
            seller_id=self.seller_user.id,
            statuses=["Pendiente de revisión"],
        )
        self.assertEqual(
            both["message"],
            "Provide exactly one of customer_id or customer_ids.",
        )

    # -- get_product_recommendations batch ------------------------------

    def test_scalar_get_product_recommendations_unchanged(self):
        self._add_product_stock(self.product)
        result = self._service().get_product_recommendations(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
        )
        self.assertEqual(result["customer_id"], self.customer.id)
        self.assertNotIn("message", result)
        self.assertIn("recommendations", result)
        self.assertIn("customer_ranking", result)

    def test_batch_get_product_recommendations_grouped_results(self):
        self._add_product_stock(self.product)
        result = self._service().get_product_recommendations(
            customer_ids=[self.customer.id, self.customer_b.id],
            seller_id=self.seller_user.id,
        )
        self.assertEqual(result["seller_id"], self.seller_user.id)
        for customer in (self.customer, self.customer_b):
            row = result["results"][str(customer.id)]
            self.assertEqual(row["customer_id"], customer.id)
            self.assertIn("recommendations", row)
            self.assertIn("customer_ranking", row)
            self.assertNotIn("message", row)

    def test_batch_get_product_recommendations_scope_error_isolation(self):
        self._add_product_stock(self.product)
        result = self._service().get_product_recommendations(
            customer_ids=[
                self.customer.id,
                self.foreign_customer.id,
                self.customer_b.id,
            ],
            seller_id=self.seller_user.id,
        )
        foreign = result["results"][str(self.foreign_customer.id)]
        self.assertEqual(foreign["message"], "Customer not found for seller scope.")
        self.assertEqual(foreign["recommendations"], [])
        valid = result["results"][str(self.customer.id)]
        self.assertNotIn("message", valid)
        self.assertIn("customer_ranking", valid)

    def test_batch_get_product_recommendations_mutual_exclusion(self):
        service = self._service()
        neither = service.get_product_recommendations(seller_id=self.seller_user.id)
        self.assertEqual(
            neither["message"],
            "Provide exactly one of customer_id or customer_ids.",
        )
        both = service.get_product_recommendations(
            customer_id=self.customer.id,
            customer_ids=[self.customer_b.id],
            seller_id=self.seller_user.id,
        )
        self.assertEqual(
            both["message"],
            "Provide exactly one of customer_id or customer_ids.",
        )

    # -- create_crm_opportunity batch -----------------------------------

    def test_scalar_create_crm_opportunity_unchanged(self):
        self._add_product_stock(self.product)
        result = self._service().create_crm_opportunity(
            payload=self._valid_create_payload()
        )
        self.assertIn("opportunity_id", result)
        self.assertIn("attribution_id", result)
        self.assertNotIn("message", result)

    def test_batch_create_crm_opportunity_ordered_results(self):
        self._add_product_stock(self.product)
        payloads = [
            self._valid_create_payload(self.customer),
            self._valid_create_payload(self.customer_b),
            self._valid_create_payload(self.customer_c),
        ]
        result = self._service().create_crm_opportunity(payloads=payloads)
        self.assertEqual(len(result["results"]), 3)
        for index, customer in enumerate(
            (self.customer, self.customer_b, self.customer_c)
        ):
            row = result["results"][index]
            self.assertEqual(row["customer_id"], customer.id)
            self.assertIn("opportunity_id", row)
            self.assertIn("attribution_id", row)

    def test_batch_create_crm_opportunity_one_out_of_stock_fails_while_siblings_create(
        self,
    ):
        out_of_stock_product = self.env["product.product"].create(
            {
                "name": "Batch Out Of Stock Product",
                "default_code": "BATCH-OOS",
                "type": "product",
                "list_price": 50.0,
            }
        )
        self._add_product_stock(self.product)
        in_stock_suggestion = [
            {
                "product_id": self.product.id,
                "sku": self.product.default_code,
                "name": self.product.name,
                "list_price": 100.0,
                "available_qty": 10.0,
            }
        ]
        out_of_stock_suggestion = [
            {
                "product_id": out_of_stock_product.id,
                "sku": out_of_stock_product.default_code,
                "name": out_of_stock_product.name,
                "list_price": 50.0,
                "available_qty": 0.0,
            }
        ]
        payloads = [
            self._valid_create_payload(
                self.customer, suggested_products=in_stock_suggestion
            ),
            self._valid_create_payload(
                self.customer_b, suggested_products=out_of_stock_suggestion
            ),
            self._valid_create_payload(
                self.customer_c, suggested_products=in_stock_suggestion
            ),
        ]
        result = self._service().create_crm_opportunity(payloads=payloads)
        self.assertEqual(len(result["results"]), 3)
        self.assertIn("opportunity_id", result["results"][0])
        self.assertEqual(result["results"][0]["customer_id"], self.customer.id)
        self.assertEqual(
            result["results"][1]["message"],
            "Product %s is out of stock." % out_of_stock_product.default_code,
        )
        self.assertEqual(result["results"][1]["customer_id"], self.customer_b.id)
        self.assertIn("opportunity_id", result["results"][2])
        self.assertEqual(result["results"][2]["customer_id"], self.customer_c.id)

    def test_batch_create_crm_opportunity_mutual_exclusion(self):
        service = self._service()
        neither = service.create_crm_opportunity()
        self.assertEqual(
            neither["message"],
            "Provide exactly one of payload or payloads.",
        )
        both = service.create_crm_opportunity(
            payload=self._valid_create_payload(),
            payloads=[self._valid_create_payload(self.customer_b)],
        )
        self.assertEqual(
            both["message"],
            "Provide exactly one of payload or payloads.",
        )
