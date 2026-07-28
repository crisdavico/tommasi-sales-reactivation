import threading
from datetime import date, timedelta
from unittest.mock import patch

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTester,
    confirm_test_sale_order,
    post_test_out_invoice,
)
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.models.service_candidates import (
    TommasiReactivationServiceCandidates,
)


@tagged("post_install", "-at_install")
class TestReactivationServiceCandidates(TransactionCase):
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
                "name": "Candidates Seller",
                "login": "candidates_seller_test",
                "email": "candidates_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.seller_user.partner_id.write(
            {
                "mobile": "+5491133445566",
            }
        )
        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Candidates Agent",
                "login": "candidates_agent_test",
                "email": "candidates_agent_test@example.com",
                "groups_id": [(6, 0, [cls.agent_group.id])],
            }
        )
        config = cls.Config.get_singleton()
        cls.Seller.create({"config_id": config.id, "user_id": cls.seller_user.id})
        cls.product = cls.env["product.product"].create(
            {
                "name": "Candidates Product",
                "default_code": "CAND-001",
                "type": "product",
                "list_price": 100.0,
            }
        )

    def _service(self):
        return ReactivationServiceTester(self.Service.with_user(self.agent_user))

    def _make_customer(self, name, vat):
        return self.env["res.partner"].create(
            {
                "name": name,
                "vat": vat,
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )

    def _create_invoice_with_product(
        self, partner, product, invoice_date, quantity, price_unit
    ):
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
                                "price_unit": price_unit,
                                "tax_ids": [(6, 0, [])],
                            },
                        )
                    ],
                }
            )
        )
        post_test_out_invoice(move, seller=self.seller_user)
        move.sudo().partner_id.write({"user_id": self.seller_user.id})
        return move

    def _add_product_stock(self, product, quantity=10.0):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        location = warehouse.lot_stock_id
        self.env["stock.quant"].sudo().create(
            {
                "product_id": product.id,
                "location_id": location.id,
                "quantity": quantity,
            }
        )

    def _bootstrap_qualify_partner(self, partner, product, invoice_count=3):
        """Three (default) posted invoices, 90/120/150 days ago: qualifies for
        the bootstrap universe and lands entirely on the "prior" half of the
        default detection window (midpoint at 45 days), so it also triggers
        inactivity, revenue_decline and qty_decline together.
        """
        today = date.today()
        for index in range(invoice_count):
            invoice_date = fields.Date.to_string(
                today - timedelta(days=90 + 30 * index)
            )
            self._create_invoice_with_product(partner, product, invoice_date, 1, 100.0)
        return partner

    def _candidate_for(self, result, customer_id):
        return next(
            (row for row in result["candidates"] if row["customer_id"] == customer_id),
            None,
        )

    def test_inactivity_screen_included(self):
        customer = self._make_customer("Inactive Candidate", "30-11111111-1")
        self._bootstrap_qualify_partner(customer, self.product)
        result = self._service().get_reactivation_candidates(self.seller_user.id)
        self.assertNotIn("message", result)
        candidate = self._candidate_for(result, customer.id)
        self.assertIsNotNone(candidate)
        self.assertIn("inactivity", candidate["screens"])
        self.assertEqual(candidate["days_inactive"], 90)

    def test_revenue_decline_screen_included(self):
        customer = self._make_customer("Revenue Decline Candidate", "30-22222222-2")
        self._bootstrap_qualify_partner(customer, self.product)
        result = self._service().get_reactivation_candidates(self.seller_user.id)
        candidate = self._candidate_for(result, customer.id)
        self.assertIsNotNone(candidate)
        self.assertIn("revenue_decline", candidate["screens"])
        self.assertLess(candidate["revenue_change_pct"], 0)

    def test_qty_decline_screen_included(self):
        customer = self._make_customer("Qty Decline Candidate", "30-33333333-3")
        self._bootstrap_qualify_partner(customer, self.product)
        result = self._service().get_reactivation_candidates(self.seller_user.id)
        candidate = self._candidate_for(result, customer.id)
        self.assertIsNotNone(candidate)
        self.assertIn("qty_decline", candidate["screens"])
        self.assertLess(candidate["qty_change_pct"], 0)

    def _ensure_product_deliverable(self, product):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if warehouse and warehouse.delivery_route_id:
            product.sudo().write({"route_ids": [(4, warehouse.delivery_route_id.id)]})
        return warehouse

    def test_undelivered_so_lines_screen_included(self):
        customer = self._make_customer("Undelivered Candidate", "30-44444444-4")
        today = date.today()
        for days_ago in (20, 15, 10):
            self._create_invoice_with_product(
                customer,
                self.product,
                fields.Date.to_string(today - timedelta(days=days_ago)),
                1,
                100.0,
            )
        self._add_product_stock(self.product)
        warehouse = self._ensure_product_deliverable(self.product)
        commercial = customer.commercial_partner_id
        commercial.sudo().write({"user_id": self.seller_user.id})
        line_vals = {
            "product_id": self.product.id,
            "product_uom_qty": 4.0,
            "price_unit": 100.0,
        }
        if warehouse and "custom_warehouse_id" in self.env["sale.order.line"]._fields:
            line_vals["custom_warehouse_id"] = warehouse.id
        order_vals = {
            "partner_id": commercial.id,
            "user_id": self.seller_user.id,
            "order_line": [(0, 0, line_vals)],
        }
        if warehouse and "warehouse_id" in self.env["sale.order"]._fields:
            order_vals["warehouse_id"] = warehouse.id
        order = self.env["sale.order"].sudo().create(order_vals)
        confirm_test_sale_order(order, self.env)
        result = self._service().get_reactivation_candidates(self.seller_user.id)
        candidate = self._candidate_for(result, customer.id)
        self.assertIsNotNone(candidate)
        self.assertIn("undelivered_so_lines", candidate["screens"])
        self.assertTrue(candidate["has_undelivered_lines"])
        self.assertNotIn("inactivity", candidate["screens"])

    def test_undelivered_screen_excludes_old_orders(self):
        customer = self._make_customer("Old Undelivered Candidate", "30-66666666-6")
        today = date.today()
        for days_ago in (20, 15, 10):
            self._create_invoice_with_product(
                customer,
                self.product,
                fields.Date.to_string(today - timedelta(days=days_ago)),
                1,
                100.0,
            )
        self._add_product_stock(self.product)
        warehouse = self._ensure_product_deliverable(self.product)
        commercial = customer.commercial_partner_id
        commercial.sudo().write({"user_id": self.seller_user.id})
        line_vals = {
            "product_id": self.product.id,
            "product_uom_qty": 4.0,
            "price_unit": 100.0,
        }
        if warehouse and "custom_warehouse_id" in self.env["sale.order.line"]._fields:
            line_vals["custom_warehouse_id"] = warehouse.id
        order_vals = {
            "partner_id": commercial.id,
            "user_id": self.seller_user.id,
            "order_line": [(0, 0, line_vals)],
        }
        if warehouse and "warehouse_id" in self.env["sale.order"]._fields:
            order_vals["warehouse_id"] = warehouse.id
        order = self.env["sale.order"].sudo().create(order_vals)
        confirm_test_sale_order(order, self.env)
        order.sudo().write(
            {"date_order": fields.Datetime.to_datetime(today - timedelta(days=91))}
        )
        self.env["sale.order"].flush(["date_order"])
        result = self._service().get_reactivation_candidates(self.seller_user.id)
        candidate = self._candidate_for(result, customer.id)
        if candidate:
            self.assertFalse(candidate["has_undelivered_lines"])
            self.assertNotIn("undelivered_so_lines", candidate["screens"])

    def test_healthy_customer_excluded_and_screened_out_count_increments(self):
        baseline = self._service().get_reactivation_candidates(self.seller_user.id)
        baseline_screened_out = baseline["screened_out_count"]
        customer = self._make_customer("Healthy Candidate", "30-55555555-5")
        today = date.today()
        prior_date = fields.Date.to_string(today - timedelta(days=70))
        recent_date = fields.Date.to_string(today - timedelta(days=20))
        self._create_invoice_with_product(customer, self.product, prior_date, 2, 100.0)
        self._create_invoice_with_product(customer, self.product, recent_date, 2, 100.0)
        self._create_invoice_with_product(customer, self.product, recent_date, 2, 100.0)
        result = self._service().get_reactivation_candidates(self.seller_user.id)
        candidate_ids = {row["customer_id"] for row in result["candidates"]}
        self.assertNotIn(customer.id, candidate_ids)
        self.assertEqual(result["screened_out_count"], baseline_screened_out + 1)

    def test_disabled_seller_returns_message(self):
        disabled_user = self.env["res.users"].create(
            {
                "name": "Disabled Candidates Seller",
                "login": "disabled_candidates_seller_test",
                "email": "disabled_candidates_seller_test@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        result = self._service().get_reactivation_candidates(disabled_user.id)
        self.assertEqual(result["message"], "Seller not enabled or not found.")

    def test_bootstrap_filter_excludes_low_invoice_customer(self):
        baseline = self._service().get_reactivation_candidates(self.seller_user.id)
        baseline_total = len(baseline["candidates"]) + baseline["screened_out_count"]
        customer = self._make_customer("Low Invoice Candidate", "30-66666666-6")
        self._bootstrap_qualify_partner(customer, self.product, invoice_count=2)
        result = self._service().get_reactivation_candidates(self.seller_user.id)
        candidate_ids = {row["customer_id"] for row in result["candidates"]}
        self.assertNotIn(customer.id, candidate_ids)
        self.assertEqual(
            len(result["candidates"]) + result["screened_out_count"],
            baseline_total,
        )

    def test_overstock_product_ids_cached_matches_and_skips_requery(self):
        service = self._service()._service
        self._add_product_stock(self.product, quantity=999999.0)
        config = self.Config.get_singleton()
        threshold = max(config.low_stock_threshold * 4, 20)
        today = fields.Date.to_string(fields.Date.context_today(service))

        location_ids = tuple(sorted(config._resolve_stock_location_ids()))
        service._overstock_product_ids_cached.clear_cache(service)

        first = service._overstock_product_ids_cached(threshold, today, location_ids)
        self.assertIn(self.product.id, first)

        query_count = [0]

        def _count_stock_quant_queries(cr, query, params, start, delay):
            if "stock_quant" in (query or "").lower():
                query_count[0] += 1

        thread = threading.current_thread()
        prior_hooks = tuple(getattr(thread, "query_hooks", ()))
        thread.query_hooks = prior_hooks + (_count_stock_quant_queries,)
        try:
            second = service._overstock_product_ids_cached(
                threshold, today, location_ids
            )
        finally:
            thread.query_hooks = prior_hooks

        self.assertEqual(first, second)
        self.assertEqual(
            query_count[0],
            0,
            "cached overstock lookup should not re-query stock_quant",
        )

    def test_candidates_expose_revenue_fields(self):
        customer = self._make_customer("Revenue Fields Candidate", "30-77777777-7")
        self._bootstrap_qualify_partner(customer, self.product)
        result = self._service().get_reactivation_candidates(self.seller_user.id)
        candidate = self._candidate_for(result, customer.id)
        self.assertIsNotNone(candidate)
        self.assertIn("revenue_prior", candidate)
        self.assertIn("revenue_recent", candidate)
        self.assertIn("revenue_window_total", candidate)
        self.assertEqual(
            candidate["revenue_window_total"],
            round(candidate["revenue_prior"] + candidate["revenue_recent"], 2),
        )

    def test_candidates_sorted_by_revenue_recent(self):
        low_revenue = self._make_customer("Low Revenue Candidate", "30-88888888-8")
        high_revenue = self._make_customer("High Revenue Candidate", "30-99999999-9")
        today = date.today()
        for index in range(3):
            invoice_date = fields.Date.to_string(
                today - timedelta(days=90 + 30 * index)
            )
            self._create_invoice_with_product(
                low_revenue, self.product, invoice_date, 1, 50.0
            )
            self._create_invoice_with_product(
                high_revenue, self.product, invoice_date, 1, 500.0
            )
        recent_date = fields.Date.to_string(today - timedelta(days=30))
        self._create_invoice_with_product(
            low_revenue, self.product, recent_date, 1, 50.0
        )
        self._create_invoice_with_product(
            high_revenue, self.product, recent_date, 1, 500.0
        )

        result = self._service().get_reactivation_candidates(self.seller_user.id)
        low = self._candidate_for(result, low_revenue.id)
        high = self._candidate_for(result, high_revenue.id)
        self.assertIsNotNone(low)
        self.assertIsNotNone(high)
        self.assertGreater(high["revenue_recent"], low["revenue_recent"])
        candidate_ids = [row["customer_id"] for row in result["candidates"]]
        self.assertLess(
            candidate_ids.index(high_revenue.id),
            candidate_ids.index(low_revenue.id),
        )

    def test_include_context_returns_detection_context_keys(self):
        customer = self._make_customer("Context Keys Candidate", "30-12121212-1")
        self._bootstrap_qualify_partner(customer, self.product)
        scalar = self._service().get_customer_detection_context(
            customer.id,
            self.seller_user.id,
        )
        result = self._service().get_reactivation_candidates(
            self.seller_user.id,
            include_context=True,
        )
        candidate = self._candidate_for(result, customer.id)
        self.assertIsNotNone(candidate)
        context = candidate["detection_context"]
        expected_keys = set(TommasiReactivationServiceCandidates._DETECTION_CONTEXT_KEYS)
        self.assertEqual(set(context.keys()), expected_keys)
        for key in expected_keys:
            self.assertEqual(context[key], scalar[key])

    def test_include_context_per_candidate_message_isolation(self):
        customer_ok = self._make_customer("Context OK Candidate", "30-13131313-1")
        customer_fail = self._make_customer("Context Fail Candidate", "30-14141414-1")
        self._bootstrap_qualify_partner(customer_ok, self.product)
        self._bootstrap_qualify_partner(customer_fail, self.product)
        service = self._service()
        record = service._service
        model = type(record)
        original_build = model._build_detection_context

        def _build_side_effect(
            self_model, partner, seller_env, config, date_range, customer_id=None
        ):
            scoped_id = customer_id or partner.id
            if scoped_id == customer_fail.id:
                raise ValueError("Simulated detection context failure")
            return original_build(
                self_model, partner, seller_env, config, date_range, customer_id
            )

        with patch.object(
            model,
            "_build_detection_context",
            autospec=True,
            side_effect=_build_side_effect,
        ):
            result = service.get_reactivation_candidates(
                self.seller_user.id,
                include_context=True,
            )
        ok_row = self._candidate_for(result, customer_ok.id)
        fail_row = self._candidate_for(result, customer_fail.id)
        self.assertIsNotNone(ok_row)
        self.assertIsNotNone(fail_row)
        self.assertIn("sales_history", ok_row["detection_context"])
        self.assertEqual(
            fail_row["detection_context"]["message"],
            "Simulated detection context failure",
        )

    def test_customer_ids_filter_returns_only_requested_candidate(self):
        customer_a = self._make_customer("Scoped Candidate A", "30-15151515-1")
        customer_b = self._make_customer("Scoped Candidate B", "30-16161616-1")
        self._bootstrap_qualify_partner(customer_a, self.product)
        self._bootstrap_qualify_partner(customer_b, self.product)
        unscoped = self._service().get_reactivation_candidates(self.seller_user.id)
        self.assertIsNotNone(self._candidate_for(unscoped, customer_a.id))
        self.assertIsNotNone(self._candidate_for(unscoped, customer_b.id))

        scoped = self._service().get_reactivation_candidates(
            self.seller_user.id,
            customer_ids=[customer_a.id],
        )
        self.assertNotIn("message", scoped)
        self.assertEqual(scoped["seller_id"], self.seller_user.id)
        candidate_ids = {row["customer_id"] for row in scoped["candidates"]}
        self.assertEqual(candidate_ids, {customer_a.id})
        self.assertNotIn(customer_b.id, candidate_ids)

    def test_customer_ids_filter_empty_list_returns_no_candidates(self):
        customer = self._make_customer("Empty Scope Candidate", "30-17171717-1")
        self._bootstrap_qualify_partner(customer, self.product)
        result = self._service().get_reactivation_candidates(
            self.seller_user.id,
            customer_ids=[],
        )
        self.assertNotIn("message", result)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["screened_out_count"], 0)

    def test_customer_ids_unknown_id_yields_empty_candidates(self):
        customer = self._make_customer("Known Portfolio Candidate", "30-18181818-1")
        self._bootstrap_qualify_partner(customer, self.product)
        result = self._service().get_reactivation_candidates(
            self.seller_user.id,
            customer_ids=[999999999],
        )
        self.assertNotIn("message", result)
        self.assertEqual(result["candidates"], [])
