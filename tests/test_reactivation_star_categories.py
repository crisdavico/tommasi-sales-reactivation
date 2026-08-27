from datetime import date, timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTestMixin,
    post_test_out_invoice,
)


@tagged("post_install", "-at_install")
class TestReactivationStarCategories(ReactivationServiceTestMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.star_customer = cls.env["res.partner"].create(
            {
                "name": "Star Categories Customer",
                "vat": "30-55667788-9",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )

    def _invoice_date(self, days_ago):
        return fields.Date.to_string(date.today() - timedelta(days=days_ago))

    def _create_category(self, name, parent=None):
        values = {"name": name}
        if parent is not None:
            values["parent_id"] = parent.id
        return self.env["product.category"].create(values)

    def _create_product(self, name, sku, category):
        return self.env["product.product"].create(
            {
                "name": name,
                "default_code": sku,
                "type": "product",
                "categ_id": category.id,
            }
        )

    def _create_invoice_with_products(
        self,
        partner,
        invoice_date,
        product_qty_pairs,
        invoice_partner=None,
        move_type="out_invoice",
    ):
        commercial = partner.commercial_partner_id
        commercial.sudo().write({"user_id": self.seller_user.id})
        billed = invoice_partner or commercial
        move = (
            self.env["account.move"]
            .sudo()
            .create(
                {
                    "move_type": move_type,
                    "partner_id": billed.id,
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

    def _assign_move_to_disallowed_company(self, move):
        """Point the move at a company id outside the unified-agent allow-list.

        Odoo 15 ``res.company`` has no ``active`` field, so an archived-company
        setup is impossible. Create a company, attach the move, then delete the
        company row so ``search([])`` no longer returns it.
        """
        company = self.env["res.company"].create(
            {
                "name": "Star Categories Disallowed Company",
                "currency_id": self.env.company.currency_id.id,
            }
        )
        company_id = company.id
        self.env["account.move"].flush()
        self.env.cr.execute(
            "UPDATE account_move SET company_id = %s WHERE id = %s",
            (company_id, move.id),
        )
        self.env.cr.execute("SET session_replication_role = replica")
        try:
            self.env.cr.execute(
                "DELETE FROM res_company WHERE id = %s",
                (company_id,),
            )
        finally:
            self.env.cr.execute("SET session_replication_role = DEFAULT")
        return company_id

    def test_get_customer_star_categories_ranks_by_units_and_caps_top_three(self):
        service = self._service()
        cat_a = self._create_category("Star Cat A")
        cat_b = self._create_category("Star Cat B")
        cat_c = self._create_category("Star Cat C")
        cat_d = self._create_category("Star Cat D")
        product_a = self._create_product("Cat Product A", "STAR-CAT-A", cat_a)
        product_b = self._create_product("Cat Product B", "STAR-CAT-B", cat_b)
        product_c = self._create_product("Cat Product C", "STAR-CAT-C", cat_c)
        product_d = self._create_product("Cat Product D", "STAR-CAT-D", cat_d)
        invoice_date = self._invoice_date(10)
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

        result = service._get_customer_star_categories(self.star_customer.id)

        self.assertEqual(len(result), 3)
        self.assertEqual(
            [row["name"] for row in result],
            ["Star Cat B", "Star Cat C", "Star Cat A"],
        )
        self.assertEqual(
            [row["category_id"] for row in result],
            [cat_b.id, cat_c.id, cat_a.id],
        )
        self.assertEqual(result[0]["total_quantity"], 30.0)
        self.assertNotIn(cat_d.id, [row["category_id"] for row in result])

    def test_get_customer_star_categories_share_uses_all_qualifying_units(self):
        service = self._service()
        cat_a = self._create_category("Share Cat A")
        cat_b = self._create_category("Share Cat B")
        cat_c = self._create_category("Share Cat C")
        cat_d = self._create_category("Share Cat D")
        invoice_date = self._invoice_date(10)
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [
                (self._create_product("Share A", "SHARE-A", cat_a), 50),
                (self._create_product("Share B", "SHARE-B", cat_b), 30),
                (self._create_product("Share C", "SHARE-C", cat_c), 15),
                (self._create_product("Share D", "SHARE-D", cat_d), 5),
            ],
        )

        result = service._get_customer_star_categories(self.star_customer.id)

        self.assertEqual(len(result), 3)
        self.assertEqual(
            [row["name"] for row in result],
            ["Share Cat A", "Share Cat B", "Share Cat C"],
        )
        self.assertAlmostEqual(result[0]["share"], 0.5)
        self.assertAlmostEqual(result[1]["share"], 0.3)
        self.assertAlmostEqual(result[2]["share"], 0.15)
        self.assertEqual(result[0]["total_quantity"], 50.0)
        self.assertEqual(result[1]["total_quantity"], 30.0)
        self.assertEqual(result[2]["total_quantity"], 15.0)
        self.assertTrue(all(0.0 <= row["share"] <= 1.0 for row in result))
        self.assertNotIn("Share Cat D", [row["name"] for row in result])

    def test_get_customer_star_categories_tie_breaks_by_name_asc(self):
        service = self._service()
        cat_gamma = self._create_category("Gamma")
        cat_beta = self._create_category("Beta")
        cat_alpha = self._create_category("Alpha")
        invoice_date = self._invoice_date(10)
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [
                (self._create_product("Gamma Product", "TIE-G", cat_gamma), 5),
                (self._create_product("Beta Product", "TIE-B", cat_beta), 10),
                (self._create_product("Alpha Product", "TIE-A", cat_alpha), 10),
            ],
        )

        result = service._get_customer_star_categories(self.star_customer.id)

        self.assertEqual([row["name"] for row in result], ["Alpha", "Beta", "Gamma"])
        self.assertEqual(result[0]["total_quantity"], 10.0)
        self.assertEqual(result[1]["total_quantity"], 10.0)
        self.assertEqual(result[2]["total_quantity"], 5.0)

    def test_get_customer_star_categories_excludes_purchases_older_than_180_days(self):
        service = self._service()
        cat_recent = self._create_category("Recent Star Cat")
        cat_old = self._create_category("Old Star Cat")
        product_recent = self._create_product(
            "Recent Cat Product", "STAR-CAT-RECENT", cat_recent
        )
        product_old = self._create_product("Old Cat Product", "STAR-CAT-OLD", cat_old)
        self._create_invoice_with_products(
            self.star_customer,
            self._invoice_date(10),
            [(product_recent, 8)],
        )
        self._create_invoice_with_products(
            self.star_customer,
            self._invoice_date(200),
            [(product_old, 50)],
        )

        result = service._get_customer_star_categories(self.star_customer.id)

        names = {row["name"] for row in result}
        self.assertIn("Recent Star Cat", names)
        self.assertNotIn("Old Star Cat", names)
        self.assertEqual(result[0]["total_quantity"], 8.0)

    def test_get_customer_star_categories_excludes_inactive_and_non_stockable(self):
        service = self._service()
        cat_stock = self._create_category("Stockable Star Cat")
        cat_inactive = self._create_category("Inactive Star Cat")
        cat_service = self._create_category("Service Star Cat")
        cat_consu = self._create_category("Consumable Star Cat")
        stockable_product = self._create_product(
            "Stockable Cat Product", "STAR-CAT-STOCK", cat_stock
        )
        inactive_product = self._create_product(
            "Inactive Cat Product", "STAR-CAT-INACT", cat_inactive
        )
        inactive_product.write({"active": False})
        service_product = self._create_product(
            "Service Cat Product", "STAR-CAT-SVC", cat_service
        )
        service_product.product_tmpl_id.write({"type": "service"})
        consumable_product = self._create_product(
            "Consumable Cat Product", "STAR-CAT-CONSU", cat_consu
        )
        consumable_product.product_tmpl_id.write({"type": "consu"})
        invoice_date = self._invoice_date(5)
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

        result = service._get_customer_star_categories(self.star_customer.id)

        self.assertEqual([row["name"] for row in result], ["Stockable Star Cat"])
        self.assertEqual(result[0]["total_quantity"], 2.0)
        self.assertAlmostEqual(result[0]["share"], 1.0)

    def test_get_customer_star_categories_excludes_refunds(self):
        service = self._service()
        category = self._create_category("Refund Star Cat")
        product = self._create_product("Refund Cat Product", "STAR-CAT-REF", category)
        invoice_date = self._invoice_date(8)
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [(product, 10)],
        )
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [(product, 4)],
            move_type="out_refund",
        )

        result = service._get_customer_star_categories(self.star_customer.id)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Refund Star Cat")
        self.assertEqual(result[0]["total_quantity"], 10.0)
        self.assertAlmostEqual(result[0]["share"], 1.0)

    def test_get_customer_star_categories_includes_child_contact_invoices(self):
        service = self._service()
        category = self._create_category("Child Contact Star Cat")
        product = self._create_product(
            "Child Contact Product", "STAR-CAT-CHILD", category
        )
        child = self.env["res.partner"].create(
            {
                "name": "Star Categories Child Contact",
                "parent_id": self.star_customer.id,
                "type": "invoice",
            }
        )
        invoice_date = self._invoice_date(6)
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [(product, 12)],
            invoice_partner=child,
        )

        result = service._get_customer_star_categories(self.star_customer.id)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Child Contact Star Cat")
        self.assertEqual(result[0]["total_quantity"], 12.0)
        self.assertEqual(result[0]["category_id"], category.id)

    def test_get_customer_star_categories_groups_direct_categ_id_not_parent(self):
        service = self._service()
        parent = self._create_category("Parent Star Cat")
        child = self._create_category("Child Star Cat", parent=parent)
        child_product = self._create_product(
            "Child Categ Product", "STAR-CAT-DIRECT", child
        )
        parent_product = self._create_product(
            "Parent Categ Product", "STAR-CAT-PARENT", parent
        )
        invoice_date = self._invoice_date(4)
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [
                (child_product, 20),
                (parent_product, 5),
            ],
        )

        result = service._get_customer_star_categories(self.star_customer.id)

        self.assertEqual(
            [row["name"] for row in result],
            ["Child Star Cat", "Parent Star Cat"],
        )
        self.assertEqual(result[0]["total_quantity"], 20.0)
        self.assertEqual(result[1]["total_quantity"], 5.0)
        self.assertNotEqual(result[0]["total_quantity"], 25.0)

    def test_get_customer_star_categories_excludes_inactive_company_invoices(self):
        service = self._service()
        cat_allowed = self._create_category("Allowed Company Star Cat")
        cat_archived = self._create_category("Archived Company Star Cat")
        allowed_product = self._create_product(
            "Allowed Company Product", "STAR-CAT-ALLOW", cat_allowed
        )
        archived_product = self._create_product(
            "Archived Company Product", "STAR-CAT-ARCH", cat_archived
        )
        invoice_date = self._invoice_date(3)
        self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [(allowed_product, 8)],
        )
        archived_move = self._create_invoice_with_products(
            self.star_customer,
            invoice_date,
            [(archived_product, 50)],
        )
        self._assign_move_to_disallowed_company(archived_move)

        result = service._get_customer_star_categories(self.star_customer.id)

        self.assertEqual([row["name"] for row in result], ["Allowed Company Star Cat"])
        self.assertEqual(result[0]["total_quantity"], 8.0)
        self.assertNotIn("Archived Company Star Cat", [row["name"] for row in result])

    def test_get_customer_star_categories_returns_empty_for_unknown_customer(self):
        service = self._service()

        result = service._get_customer_star_categories(999999)

        self.assertEqual(result, [])
