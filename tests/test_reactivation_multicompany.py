from datetime import date, timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTester,
    post_test_out_invoice,
)


@tagged("post_install", "-at_install")
class TestReactivationMulticompany(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Service = cls.env["tommasi.reactivation.service"]
        cls.Config = cls.env["tommasi.reactivation.config"]
        cls.Seller = cls.env["tommasi.reactivation.seller"]
        cls.agent_group = cls.env.ref(
            "tommasi_sales_reactivation.group_reactivation_agent"
        )
        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Multicompany Reactivation Agent",
                "login": "reactivation_agent_multicompany_test",
                "email": "reactivation_agent_multicompany_test@example.com",
                "groups_id": [(6, 0, [cls.agent_group.id])],
            }
        )
        cls.main_company = cls.env.company
        cls.company_b = cls.env["res.company"].create(
            {
                "name": "Reactivation Test Company B",
                "currency_id": cls.main_company.currency_id.id,
            }
        )
        cls.config_main = cls.Config.get_singleton(cls.main_company)
        cls.config_b = cls.Config.create(
            {
                "name": "Sales reactivation B",
                "company_id": cls.company_b.id,
                "inactivity_days_primary": 20,
                "inactivity_days_secondary": 35,
            }
        )
        cls.seller_main = cls.env["res.users"].create(
            {
                "name": "Main Company Seller",
                "login": "multicompany_seller_main",
                "email": "multicompany_seller_main@example.com",
                "company_id": cls.main_company.id,
                "company_ids": [(6, 0, [cls.main_company.id, cls.company_b.id])],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.seller_main.partner_id.write(
            {
                "mobile": "+5491199887766",
            }
        )
        cls.seller_b = cls.env["res.users"].create(
            {
                "name": "Company B Seller",
                "login": "multicompany_seller_b",
                "email": "multicompany_seller_b@example.com",
                "company_id": cls.company_b.id,
                "company_ids": [(6, 0, [cls.company_b.id])],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.seller_b.partner_id.write(
            {
                "mobile": "+5491188776655",
            }
        )
        cls.Seller.create(
            {"config_id": cls.config_main.id, "user_id": cls.seller_main.id}
        )
        cls.Seller.create({"config_id": cls.config_b.id, "user_id": cls.seller_b.id})
        cls.Seller.create({"config_id": cls.config_b.id, "user_id": cls.seller_main.id})
        cls.customer_main = cls.env["res.partner"].create(
            {
                "name": "Main Customer",
                "vat": "30-81111111-1",
                "user_id": cls.seller_main.id,
                "customer_rank": 1,
            }
        )
        cls.customer_b = cls.env["res.partner"].create(
            {
                "name": "Company B Customer",
                "vat": "30-82222222-2",
                "user_id": cls.seller_b.id,
                "customer_rank": 1,
            }
        )
        cls.customer_b_shared = cls.env["res.partner"].create(
            {
                "name": "Company B Shared Seller Customer",
                "vat": "30-83333333-3",
                "user_id": cls.seller_main.id,
                "customer_rank": 1,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Multicompany Product",
                "default_code": "MULTI-001",
                "type": "product",
                "list_price": 100.0,
            }
        )
        for partner in (cls.customer_main, cls.customer_b, cls.customer_b_shared):
            seller = partner.user_id
            cls._bootstrap_qualify_partner(
                partner, cls.product, invoice_count=3, seller=seller
            )

    @classmethod
    def _bootstrap_qualify_partner(cls, partner, product, invoice_count=3, seller=None):
        from datetime import date, timedelta

        seller = seller or partner.user_id
        commercial = partner.commercial_partner_id
        commercial.sudo().write({"customer_rank": 1, "user_id": seller.id})
        today = date.today()
        for index in range(invoice_count):
            invoice_date = fields.Date.to_string(
                today - timedelta(days=90 + 30 * index)
            )
            move = cls.env["account.move"].sudo().create(
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
            post_test_out_invoice(move, seller=seller)
            commercial.sudo().write({"user_id": seller.id})

    def _service(self):
        return ReactivationServiceTester(self.Service.with_user(self.agent_user))

    def _create_posted_invoice(self, partner, seller, invoice_date, amount):
        """Post a customer invoice on the main company (seller scope is on partner)."""
        commercial = partner.commercial_partner_id
        commercial.sudo().write({"user_id": seller.id})
        move = self.env["account.move"].sudo().create(
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
                            "product_id": self.product.id,
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                ],
            }
        )
        post_test_out_invoice(move, seller=seller)
        move.sudo().partner_id.write({"user_id": seller.id})
        return move

    def test_bootstrap_unifies_sellers_and_customers_across_companies(self):
        result = self._service().bootstrap_reactivation_cycle()
        seller_ids = {row["seller_id"] for row in result["sellers"]}
        self.assertIn(self.seller_main.id, seller_ids)
        self.assertIn(self.seller_b.id, seller_ids)
        self.assertEqual(
            len(
                [
                    row
                    for row in result["sellers"]
                    if row["seller_id"] == self.seller_main.id
                ]
            ),
            1,
        )
        customer_ids = {row["customer_id"] for row in result["customers"]}
        self.assertIn(self.customer_main.id, customer_ids)
        self.assertIn(self.customer_b.id, customer_ids)
        self.assertIn(self.customer_b_shared.id, customer_ids)
        self.assertNotIn("company_id", result["config"])

    def test_bootstrap_config_uses_primary_company_thresholds(self):
        result = self._service().bootstrap_reactivation_cycle()
        self.assertEqual(
            result["config"]["inactivity_days_primary"],
            self.config_main.inactivity_days_primary,
        )
        self.assertEqual(
            result["config"]["inactivity_days_secondary"],
            self.config_main.inactivity_days_secondary,
        )
        self.assertNotEqual(
            result["config"]["inactivity_days_primary"],
            self.config_b.inactivity_days_primary,
        )

    def test_shared_seller_sees_company_b_customer(self):
        result = self._service().get_customer_detection_context(
            self.customer_b_shared.id, self.seller_main.id
        )
        self.assertNotIn("message", result)
        self.assertEqual(result["customer_id"], self.customer_b_shared.id)

    def test_detection_context_reads_invoices_across_companies(self):
        invoice_date = fields.Date.to_string(date.today() - timedelta(days=10))
        self._create_posted_invoice(
            self.customer_b,
            self.seller_b,
            fields.Date.from_string(invoice_date),
            500.0,
        )
        result = self._service().get_customer_detection_context(
            self.customer_b.id,
            self.seller_b.id,
        )
        self.assertNotIn("message", result)
        self.assertTrue(result["sales_history"])
        self.assertEqual(result["last_purchase"]["days_inactive"], 10)

    def test_seller_scope_hides_other_company_seller_customer(self):
        result = self._service().get_customer_detection_context(
            self.customer_b.id, self.seller_main.id
        )
        self.assertEqual(
            result["message"],
            "Customer not found for seller scope.",
        )

    def _add_product_stock(self, product, quantity=10.0):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.main_company.id)], limit=1
        )
        self.env["stock.quant"].sudo().create(
            {
                "product_id": product.id,
                "location_id": warehouse.lot_stock_id.id,
                "quantity": quantity,
            }
        )

    def test_create_crm_opportunity_uses_primary_config_company_for_company_b_seller(
        self,
    ):
        self._add_product_stock(self.product)
        payload = {
            "customer_id": self.customer_b.id,
            "seller_id": self.seller_b.id,
            "client_message": "Hola, le escribo de Tommasi con una oferta.",
            "priority_label": "high",
            "source": "Agente Comercial",
            "trigger_type": "inactivity",
            "confidence": 0.85,
            "cycle_id": "multicompany-test",
            "suggested_products": [
                {
                    "product_id": self.product.id,
                    "sku": self.product.default_code,
                    "name": self.product.name,
                    "list_price": self.product.list_price,
                    "available_qty": 10.0,
                }
            ],
        }
        result = self._service().create_crm_opportunity(payload)
        self.assertIn("opportunity_id", result)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        self.assertEqual(lead.company_id, self.config_main.company_id)

    def test_create_crm_opportunity_uses_primary_config_for_shared_seller(self):
        self._add_product_stock(self.product)
        payload = {
            "customer_id": self.customer_b_shared.id,
            "seller_id": self.seller_main.id,
            "client_message": "Hola, le escribo de Tommasi con una oferta.",
            "priority_label": "high",
            "source": "Agente Comercial",
            "trigger_type": "inactivity",
            "confidence": 0.85,
            "cycle_id": "multicompany-shared-seller",
            "suggested_products": [
                {
                    "product_id": self.product.id,
                    "sku": self.product.default_code,
                    "name": self.product.name,
                    "list_price": self.product.list_price,
                    "available_qty": 10.0,
                }
            ],
        }
        result = self._service().create_crm_opportunity(payload)
        self.assertIn("opportunity_id", result)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        self.assertEqual(lead.company_id, self.config_main.company_id)
