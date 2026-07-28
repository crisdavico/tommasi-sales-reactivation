from datetime import date, timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTestMixin,
    post_test_out_invoice,
)


@tagged("post_install", "-at_install")
class TestReactivationStarProducts(ReactivationServiceTestMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.star_customer = cls.env["res.partner"].create(
            {
                "name": "Star Products Customer",
                "vat": "30-11223344-5",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )

    def _create_product(self, name, sku):
        return self.env["product.product"].create(
            {
                "name": name,
                "default_code": sku,
                "type": "product",
            }
        )

    def _create_invoice_with_products(self, partner, invoice_date, product_qty_pairs):
        commercial = partner.commercial_partner_id
        commercial.sudo().write({"user_id": self.seller_user.id})
        move = (
            self.env["account.move"]
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
                                "quantity": quantity,
                                "price_unit": 100.0,
                                "tax_ids": [(6, 0, [])],
                            },
                        )
                        for product, quantity in product_qty_pairs
                    ],
                }
            )
        )
        post_test_out_invoice(move, seller=self.seller_user)
        return move

    def test_get_customer_star_products_ranks_by_units_and_caps_top_three(self):
        service = self._service()
        product_a = self._create_product("Star Product A", "STAR-A")
        product_b = self._create_product("Star Product B", "STAR-B")
        product_c = self._create_product("Star Product C", "STAR-C")
        product_d = self._create_product("Star Product D", "STAR-D")
        invoice_date = fields.Date.to_string(date.today() - timedelta(days=10))
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [
                (product_a, 10),
                (product_b, 30),
                (product_c, 20),
                (product_d, 5),
            ],
        )

        result = service._get_customer_star_products(self.star_customer.id)

        self.assertEqual(len(result), 3)
        self.assertEqual(
            [row["sku"] for row in result],
            ["STAR-B", "STAR-C", "STAR-A"],
        )
        self.assertEqual(result[0]["total_quantity"], 30.0)

    def test_get_customer_star_products_excludes_purchases_older_than_180_days(self):
        service = self._service()
        product_recent = self._create_product("Recent Star", "STAR-RECENT")
        product_old = self._create_product("Old Star", "STAR-OLD")
        self._create_invoice_with_products(
            self.star_customer,
            fields.Date.to_string(date.today() - timedelta(days=10)),
            [(product_recent, 8)],
        )
        self._create_invoice_with_products(
            self.star_customer,
            fields.Date.to_string(date.today() - timedelta(days=200)),
            [(product_old, 50)],
        )

        result = service._get_customer_star_products(self.star_customer.id)

        skus = {row["sku"] for row in result}
        self.assertIn("STAR-RECENT", skus)
        self.assertNotIn("STAR-OLD", skus)

    def test_get_customer_star_products_excludes_inactive_and_non_stockable_products(
        self,
    ):
        service = self._service()
        stockable_product = self._create_product("Stockable Star", "STAR-STOCK")
        inactive_product = self._create_product("Inactive Star", "STAR-INACT")
        inactive_product.write({"active": False})
        service_product = self._create_product("Service Star", "STAR-SVC")
        service_product.product_tmpl_id.write({"type": "service"})
        consumable_product = self._create_product("Consumable Star", "STAR-CONSU")
        consumable_product.product_tmpl_id.write({"type": "consu"})
        invoice_date = fields.Date.to_string(date.today() - timedelta(days=5))
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [
                (inactive_product, 40),
                (service_product, 40),
                (consumable_product, 40),
                (stockable_product, 2),
            ],
        )

        result = service._get_customer_star_products(self.star_customer.id)

        self.assertEqual([row["sku"] for row in result], ["STAR-STOCK"])

    def test_get_customer_star_products_returns_empty_for_unknown_customer(self):
        service = self._service()

        result = service._get_customer_star_products(999999)

        self.assertEqual(result, [])
