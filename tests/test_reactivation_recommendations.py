import json
import threading
from datetime import date, timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTestMixin,
    ReactivationServiceTester,
    confirm_test_sale_order,
    post_test_out_invoice,
)


@tagged("post_install", "-at_install")
class TestReactivationRecommendations(ReactivationServiceTestMixin, TransactionCase):
    def test_get_product_recommendations_rejects_wrong_seller(self):
        result = self._service().get_product_recommendations(
            customer_id=self.customer.id,
            seller_id=self.other_seller.id,
        )
        self.assertEqual(result["message"], "Customer not found for seller scope.")
        self.assertEqual(result["recommendations"], [])

    def test_get_product_recommendations_returns_in_stock_products(self):
        self._add_product_stock(self.product)
        result = self._service().get_product_recommendations(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
        )
        self.assertNotIn("message", result)
        product_ids = {row["product_id"] for row in result["recommendations"]}
        self.assertIn(self.product.id, product_ids)
        recommendation = next(
            row
            for row in result["recommendations"]
            if row["product_id"] == self.product.id
        )
        self.assertGreater(recommendation["available_qty"], 0.0)
        self.assertIn("list_price", recommendation)
        self.assertIn("pricelist_discount_pct", recommendation)
        self.assertIn("reason_tier", recommendation)
        self.assertIn("opportunity_score", recommendation)

    def test_get_product_recommendations_uses_customer_pricelist_net_price(self):
        """P-03: net price comes from the customer's assigned pricelist."""
        catalog_product = self.env["product.product"].create(
            {
                "name": "Pricelist Net Product",
                "default_code": "REACT-PL-001",
                "type": "product",
                "list_price": 80000.0,
                "standard_price": 40000.0,
            }
        )
        pricelist = self.env["product.pricelist"].create(
            {"name": "Test Reactivation Pricelist"}
        )
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": pricelist.id,
                "applied_on": "1_product",
                "compute_price": "fixed",
                "fixed_price": 75000.0,
                "product_tmpl_id": catalog_product.product_tmpl_id.id,
            }
        )
        customer = self.env["res.partner"].create(
            {
                "name": "Pricelist Customer",
                "vat": "30-99887766-5",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
                "property_product_pricelist": pricelist.id,
            }
        )
        old_date = fields.Date.to_string(date.today() - timedelta(days=60))
        self._create_invoice_with_product(
            customer, catalog_product, old_date, 2.0, 75000.0
        )
        self._add_product_stock(catalog_product)

        result = self._service().get_product_recommendations(
            customer_id=customer.id,
            seller_id=self.seller_user.id,
        )
        recommendation = next(
            row
            for row in result["recommendations"]
            if row["product_id"] == catalog_product.id
        )
        self.assertEqual(recommendation["list_price"], 75000.0)
        self.assertEqual(recommendation["pricelist_discount_pct"], 6.25)

    def test_get_product_recommendations_respects_configured_stock_locations(self):
        config = self.Config.get_singleton()
        warehouses = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], order="id asc"
        )
        self.assertGreaterEqual(len(warehouses), 1)
        source_warehouse = warehouses[0]
        other_warehouse = (
            warehouses[1]
            if len(warehouses) > 1
            else self.env["stock.warehouse"].create(
                {
                    "name": "Alternate Reactivation Warehouse",
                    "code": "ARW",
                    "company_id": self.env.company.id,
                }
            )
        )
        config.write(
            {"stock_location_ids": [(6, 0, [source_warehouse.lot_stock_id.id])]}
        )
        self._add_product_stock(
            self.product,
            quantity=15.0,
            location=other_warehouse.lot_stock_id,
        )
        result = self._service().get_product_recommendations(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
        )
        product_ids = {row["product_id"] for row in result["recommendations"]}
        self.assertNotIn(self.product.id, product_ids)

        self._add_product_stock(
            self.product,
            quantity=15.0,
            location=source_warehouse.lot_stock_id,
        )
        result = self._service().get_product_recommendations(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
        )
        product_ids = {row["product_id"] for row in result["recommendations"]}
        self.assertIn(self.product.id, product_ids)
        config.write({"stock_location_ids": [(5, 0, 0)]})

    def test_get_product_recommendations_ranks_by_opportunity_score(self):
        category = self.env["product.category"].create(
            {"name": "Opportunity Rank Category"}
        )
        customer = self.env["res.partner"].create(
            {
                "name": "Opportunity Rank Customer",
                "vat": "30-11112222-3",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        product_high = self.env["product.product"].create(
            {
                "name": "High Opportunity Product",
                "default_code": "REACT-OPP-HIGH",
                "type": "product",
                "list_price": 100.0,
                "standard_price": 20.0,
                "categ_id": category.id,
            }
        )
        product_low = self.env["product.product"].create(
            {
                "name": "Low Opportunity Product",
                "default_code": "REACT-OPP-LOW",
                "type": "product",
                "list_price": 200.0,
                "standard_price": 50.0,
                "categ_id": category.id,
            }
        )
        old_date = fields.Date.to_string(date.today() - timedelta(days=60))
        self._create_invoice_with_product(customer, product_high, old_date, 10.0, 100.0)
        self._create_invoice_with_product(customer, product_low, old_date, 2.0, 200.0)
        self._add_product_stock(product_high)
        self._add_product_stock(product_low)

        result = self._service().get_product_recommendations(
            customer_id=customer.id,
            seller_id=self.seller_user.id,
        )
        ranked = [
            row
            for row in result["recommendations"]
            if row["product_id"] in (product_high.id, product_low.id)
        ]
        self.assertEqual(len(ranked), 2)
        self.assertGreater(
            ranked[0]["opportunity_score"], ranked[1]["opportunity_score"]
        )
        self.assertEqual(ranked[0]["product_id"], product_high.id)
        self.assertEqual(ranked[0]["habitual_units"], 10.0)
        self.assertEqual(ranked[0]["potential_net_margin"], 80.0)
        self.assertEqual(ranked[0]["opportunity_score"], 800.0)
        scores = [row["opportunity_score"] for row in result["recommendations"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        ranking = result["customer_ranking"]
        self.assertIn("total_opportunity_score", ranking)
        self.assertEqual(ranking["total_opportunity_score"], 1100.0)
        self.assertEqual(ranking["max_opportunity_score"], 800.0)
        self.assertEqual(ranking["products_count"], 2)

    def test_customer_ranking_sums_top_max_products(self):
        config = self.Config.get_singleton()
        config.write({"suggested_products_max": 1})
        category = self.env["product.category"].create(
            {"name": "Ranking Default Category"}
        )
        customer = self.env["res.partner"].create(
            {
                "name": "Ranking Default Customer",
                "vat": "30-77778888-9",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        product_high = self.env["product.product"].create(
            {
                "name": "Ranking High Product",
                "default_code": "REACT-RANK-HIGH",
                "type": "product",
                "list_price": 100.0,
                "standard_price": 20.0,
                "categ_id": category.id,
            }
        )
        product_low = self.env["product.product"].create(
            {
                "name": "Ranking Low Product",
                "default_code": "REACT-RANK-LOW",
                "type": "product",
                "list_price": 200.0,
                "standard_price": 50.0,
                "categ_id": category.id,
            }
        )
        old_date = fields.Date.to_string(date.today() - timedelta(days=60))
        self._create_invoice_with_product(customer, product_high, old_date, 10.0, 100.0)
        self._create_invoice_with_product(customer, product_low, old_date, 2.0, 200.0)
        self._add_product_stock(product_high)
        self._add_product_stock(product_low)

        result = self._service().get_product_recommendations(
            customer_id=customer.id,
            seller_id=self.seller_user.id,
        )
        self.assertEqual(result["customer_ranking"]["total_opportunity_score"], 800.0)
        self.assertEqual(result["customer_ranking"]["products_count"], 1)

        config.write({"suggested_products_max": 3})
        result = self._service().get_product_recommendations(
            customer_id=customer.id,
            seller_id=self.seller_user.id,
        )
        self.assertEqual(result["customer_ranking"]["total_opportunity_score"], 1100.0)
        self.assertEqual(result["customer_ranking"]["products_count"], 2)

    def test_customer_ranking_empty_recommendations(self):
        category = self.env["product.category"].create(
            {"name": "No Stock Ranking Category"}
        )
        customer = self.env["res.partner"].create(
            {
                "name": "No Stock Ranking Customer",
                "vat": "30-88889999-0",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        product = self.env["product.product"].create(
            {
                "name": "No Stock Ranking Product",
                "default_code": "REACT-NOSTOCK-RANK",
                "type": "product",
                "list_price": 100.0,
                "standard_price": 20.0,
                "categ_id": category.id,
            }
        )
        old_date = fields.Date.to_string(date.today() - timedelta(days=60))
        self._create_invoice_with_product(customer, product, old_date, 5.0, 100.0)

        result = self._service().get_product_recommendations(
            customer_id=customer.id,
            seller_id=self.seller_user.id,
        )
        self.assertEqual(result["recommendations"], [])
        ranking = result["customer_ranking"]
        self.assertEqual(ranking["total_opportunity_score"], 0.0)
        self.assertEqual(ranking["max_opportunity_score"], 0.0)
        self.assertEqual(ranking["products_count"], 0)

    def test_discovery_product_uses_default_habitual_units(self):
        service = self._service()
        self.assertEqual(service._habitual_units_for_product(99999, {}), 1.0)

    def test_get_product_recommendations_unknown_customer(self):
        result = self._service().get_product_recommendations(
            customer_id=999999999,
            seller_id=self.seller_user.id,
        )
        self.assertEqual(result["message"], "Customer not found.")
        self.assertEqual(result["recommendations"], [])

    def test_get_product_recommendations_excludes_out_of_stock(self):
        result = self._service().get_product_recommendations(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
        )
        product_ids = {row["product_id"] for row in result["recommendations"]}
        self.assertNotIn(self.product.id, product_ids)

    def test_get_product_recommendations_excludes_low_stock(self):
        config = self.Config.get_singleton()
        config.write({"low_stock_threshold": 5})
        self._add_product_stock(self.product, quantity=3.0)
        result = self._service().get_product_recommendations(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
        )
        product_ids = {row["product_id"] for row in result["recommendations"]}
        self.assertNotIn(self.product.id, product_ids)

    def test_get_related_products_ranks_alternative_by_units_sold(self):
        ProductTemplate = self.env["product.template"]
        if "alternative_product_ids" not in ProductTemplate._fields:
            self.skipTest("alternative_product_ids requires website_sale")

        service = self._service()
        today = date.today()
        recent_date = fields.Date.to_string(today - timedelta(days=5))
        units_date = fields.Date.to_string(today - timedelta(days=10))

        source = self.env["product.product"].create(
            {
                "name": "Related Source Product",
                "default_code": "REACT-REL-SRC",
                "type": "product",
            }
        )
        alt_a = self.env["product.product"].create(
            {
                "name": "Related Alt A",
                "default_code": "REACT-REL-ALT-A",
                "type": "product",
            }
        )
        alt_b = self.env["product.product"].create(
            {
                "name": "Related Alt B",
                "default_code": "REACT-REL-ALT-B",
                "type": "product",
            }
        )
        source.product_tmpl_id.write(
            {
                "alternative_product_ids": [
                    (6, 0, [alt_a.product_tmpl_id.id, alt_b.product_tmpl_id.id])
                ]
            }
        )

        customer = self.env["res.partner"].create(
            {
                "name": "Related Products Customer",
                "vat": "30-77778888-1",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._create_invoice_with_product(customer, source, recent_date, 1, 100.0)

        units_buyer = self.env["res.partner"].create(
            {
                "name": "Related Units Buyer",
                "vat": "30-77778888-2",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._create_invoice_with_product(units_buyer, alt_a, units_date, 5, 100.0)
        self._create_invoice_with_product(units_buyer, alt_b, units_date, 20, 100.0)
        self._add_product_stock(alt_a)
        self._add_product_stock(alt_b)

        related = service._get_related_products(customer.id)
        self.assertIn(alt_b.id, related)
        self.assertNotIn(alt_a.id, related)

    def test_get_similar_customers_finds_highest_overlap_partner(self):
        service = self._service()
        today = date.today()
        window_date = fields.Date.to_string(today - timedelta(days=15))

        product_p1 = self.env["product.product"].create(
            {
                "name": "Similar P1",
                "default_code": "REACT-SIM-P1",
                "type": "product",
            }
        )
        product_p2 = self.env["product.product"].create(
            {
                "name": "Similar P2",
                "default_code": "REACT-SIM-P2",
                "type": "product",
            }
        )
        product_p3 = self.env["product.product"].create(
            {
                "name": "Similar P3",
                "default_code": "REACT-SIM-P3",
                "type": "product",
            }
        )

        customer_a = self.env["res.partner"].create(
            {
                "name": "Similar Customer A",
                "vat": "30-88889999-1",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        customer_b = self.env["res.partner"].create(
            {
                "name": "Similar Customer B",
                "vat": "30-88889999-2",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        customer_c = self.env["res.partner"].create(
            {
                "name": "Similar Customer C",
                "vat": "30-88889999-3",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._create_invoice_with_product(customer_a, product_p1, window_date, 1, 100.0)
        self._create_invoice_with_product(customer_a, product_p2, window_date, 1, 100.0)
        self._create_invoice_with_product(customer_b, product_p1, window_date, 1, 100.0)
        self._create_invoice_with_product(customer_b, product_p2, window_date, 1, 100.0)
        self._create_invoice_with_product(customer_b, product_p3, window_date, 1, 100.0)
        self._create_invoice_with_product(customer_c, product_p1, window_date, 1, 100.0)

        similar = service._get_similar_customers(customer_a.id)
        self.assertEqual(len(similar), 1)
        self.assertEqual(similar.id, customer_b.commercial_partner_id.id)

    def test_get_products_bought_by_similar_customers_excludes_own_products(self):
        service = self._service()
        today = date.today()
        window_date = fields.Date.to_string(today - timedelta(days=15))

        product_p1 = self.env["product.product"].create(
            {
                "name": "Similar Own P1",
                "default_code": "REACT-SIM-OWN-P1",
                "type": "product",
            }
        )
        product_p2 = self.env["product.product"].create(
            {
                "name": "Similar Own P2",
                "default_code": "REACT-SIM-OWN-P2",
                "type": "product",
            }
        )
        customer_a = self.env["res.partner"].create(
            {
                "name": "Similar Own Customer A",
                "vat": "30-99990000-1",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        customer_b = self.env["res.partner"].create(
            {
                "name": "Similar Own Customer B",
                "vat": "30-99990000-2",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._create_invoice_with_product(customer_a, product_p1, window_date, 1, 100.0)
        self._create_invoice_with_product(customer_b, product_p1, window_date, 1, 100.0)
        self._create_invoice_with_product(customer_b, product_p2, window_date, 1, 100.0)
        self._add_product_stock(product_p2)

        recommendations = service._get_products_bought_by_similar_customers(
            customer_a.id
        )
        self.assertEqual(recommendations, [product_p2.id])
