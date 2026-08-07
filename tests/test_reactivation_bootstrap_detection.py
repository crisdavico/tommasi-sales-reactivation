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
class TestReactivationBootstrapDetection(ReactivationServiceTestMixin, TransactionCase):
    def test_bootstrap_returns_enabled_seller_and_customer(self):
        result = self._service().bootstrap_reactivation_cycle()
        self.assertTrue(result["config"]["active"])
        self.assertNotIn("discount_pct", result["config"])
        seller_ids = {row["seller_id"] for row in result["sellers"]}
        self.assertIn(self.seller_user.id, seller_ids)
        customer_ids = {row["customer_id"] for row in result["customers"]}
        self.assertIn(self.customer.id, customer_ids)
        customer = next(
            row for row in result["customers"] if row["customer_id"] == self.customer.id
        )
        self.assertEqual(customer["identifier"], self.customer.vat)
        self.assertEqual(customer["seller_id"], self.seller_user.id)

    def test_bootstrap_includes_all_configured_sellers(self):
        config = self.Config.get_singleton()
        no_mobile_user = self.env["res.users"].create(
            {
                "name": "No Mobile Seller",
                "login": "no_mobile_seller_service_test",
                "email": "no_mobile_seller_service_test@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        no_mobile_user.partner_id.write({"mobile": False})
        self.Seller.create({"config_id": config.id, "user_id": no_mobile_user.id})
        extra_configured_user = self.env["res.users"].create(
            {
                "name": "Extra Configured Seller",
                "login": "extra_configured_seller_service_test",
                "email": "extra_configured_seller_service_test@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        extra_configured_user.partner_id.write({"mobile": "+5491198765432"})
        self.Seller.create({"config_id": config.id, "user_id": extra_configured_user.id})
        result = self._service().bootstrap_reactivation_cycle()
        seller_ids = {row["seller_id"] for row in result["sellers"]}
        self.assertIn(self.seller_user.id, seller_ids)
        self.assertIn(no_mobile_user.id, seller_ids)
        self.assertIn(extra_configured_user.id, seller_ids)

    def test_bootstrap_excludes_sellers_not_in_config(self):
        not_configured_user = self.env["res.users"].create(
            {
                "name": "Not Configured Seller",
                "login": "not_configured_seller_service_test",
                "email": "not_configured_seller_service_test@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        not_configured_user.partner_id.write({"mobile": "+5491199887766"})
        result = self._service().bootstrap_reactivation_cycle()
        seller_ids = {row["seller_id"] for row in result["sellers"]}
        self.assertNotIn(not_configured_user.id, seller_ids)

    def test_bootstrap_excludes_partner_without_customer_rank(self):
        partner = self.env["res.partner"].create(
            {
                "name": "No Rank Customer",
                "vat": "30-55555555-5",
                "user_id": self.seller_user.id,
                "customer_rank": 0,
            }
        )
        result = self._service().bootstrap_reactivation_cycle()
        customer_ids = {row["customer_id"] for row in result["customers"]}
        self.assertNotIn(partner.id, customer_ids)

    def test_bootstrap_excludes_partner_without_min_invoices(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Low Invoice Customer",
                "vat": "30-66666666-6",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._bootstrap_qualify_partner(partner, self.product, invoice_count=2)
        result = self._service().bootstrap_reactivation_cycle()
        customer_ids = {row["customer_id"] for row in result["customers"]}
        self.assertNotIn(partner.id, customer_ids)

    def test_get_customer_detection_context_customer_not_found(self):
        result = self._service().get_customer_detection_context(
            999999999, self.seller_user.id
        )
        self.assertEqual(result["message"], "Customer not found.")

    def test_get_customer_detection_context_no_assigned_seller(self):
        partner = self.env["res.partner"].create(
            {
                "name": "No Seller Customer",
                "customer_rank": 1,
            }
        )
        result = self._service().get_customer_detection_context(
            partner.id, self.seller_user.id
        )
        self.assertEqual(result["message"], "Customer has no assigned seller.")

    def test_get_customer_detection_context_rejects_wrong_seller(self):
        result = self._service().get_customer_detection_context(
            self.customer.id, self.other_seller.id
        )
        self.assertEqual(result["message"], "Customer not found for seller scope.")
        self.assertNotIn("sales_history", result)

    def test_get_customer_detection_context_respects_configured_stock_locations(
        self,
        ):
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
                    "name": "Alternate Detection Warehouse",
                    "code": "ADW",
                    "company_id": self.env.company.id,
                }
            )
        )
        decline_product = self.env["product.product"].create(
            {
                "name": "Detection Stock Scope Product",
                "default_code": "REACT-DET-STOCK",
                "type": "product",
            }
        )
        today = date.today()
        prior_date = fields.Date.to_string(today - timedelta(days=80))
        recent_date = fields.Date.to_string(today - timedelta(days=10))
        date_range = {
            "date_from": fields.Date.to_string(today - timedelta(days=120)),
            "date_to": fields.Date.to_string(today),
        }
        self._create_invoice_with_product(
            self.customer, decline_product, prior_date, 10, 100.0
        )
        self._create_invoice_with_product(
            self.customer, decline_product, recent_date, 1, 100.0
        )
        config.write(
            {"stock_location_ids": [(6, 0, [source_warehouse.lot_stock_id.id])]}
        )
        self._add_product_stock(
            decline_product,
            quantity=25.0,
            location=other_warehouse.lot_stock_id,
        )
        result = self._service().get_customer_detection_context(
            self.customer.id, self.seller_user.id, date_range=date_range
        )
        self.assertNotIn("message", result)
        decline_ids = {row["product_id"] for row in result["volume_decline_with_stock"]}
        self.assertNotIn(decline_product.id, decline_ids)

        self._add_product_stock(
            decline_product,
            quantity=25.0,
            location=source_warehouse.lot_stock_id,
        )
        result = self._service().get_customer_detection_context(
            self.customer.id, self.seller_user.id, date_range=date_range
        )
        decline_rows = [
            row
            for row in result["volume_decline_with_stock"]
            if row["product_id"] == decline_product.id
        ]
        self.assertEqual(len(decline_rows), 1)
        self.assertEqual(decline_rows[0]["available_qty"], 25.0)
        config.write({"stock_location_ids": [(5, 0, 0)]})

    def test_detection_context_commercial_context(self):
        customer = self.env["res.partner"].create(
            {
                "name": "Commercial Context Customer",
                "vat": "30-99990000-1",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        decline_product = self.env["product.product"].create(
            {
                "name": "Commercial Context Product",
                "default_code": "REACT-COMM-CTX",
                "type": "product",
            }
        )
        self._bootstrap_qualify_partner(customer, decline_product, invoice_count=3)
        self._add_product_stock(decline_product, quantity=25.0)
        today = date.today()
        prior_date = fields.Date.to_string(today - timedelta(days=80))
        recent_date = fields.Date.to_string(today - timedelta(days=10))
        self._create_invoice_with_product(
            customer, decline_product, prior_date, 20.0, 100.0
        )
        self._create_invoice_with_product(
            customer, decline_product, recent_date, 5.0, 100.0
        )

        result = self._service().get_customer_detection_context(
            customer.id, self.seller_user.id
        )
        commercial = result["commercial_context"]
        self.assertIn("revenue_window_total", commercial)
        self.assertGreater(commercial["revenue_window_total"], 0.0)
        self.assertIsNotNone(commercial["revenue_change_pct"])
        self.assertLess(commercial["revenue_change_pct"], 0)
        self.assertGreaterEqual(commercial["days_inactive"], 10)
        self.assertTrue(commercial["has_volume_decline_with_stock"])
        self.assertGreaterEqual(commercial["stock_recovery_skus"], 1)

    def test_product_history_includes_avg_qty_per_invoice(self):
        customer = self.env["res.partner"].create(
            {
                "name": "Qty History Customer",
                "vat": "30-33334444-5",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        product = self.env["product.product"].create(
            {
                "name": "Qty History Product",
                "default_code": "REACT-QTY-HIST",
                "type": "product",
                "list_price": 50.0,
            }
        )
        today = date.today()
        for offset in (100, 80, 60):
            invoice_date = fields.Date.to_string(today - timedelta(days=offset))
            self._create_invoice_with_product(
                customer, product, invoice_date, 5.0, 50.0
            )
        result = self._service().get_customer_detection_context(
            customer.id, self.seller_user.id
        )
        row = next(
            item
            for item in result["product_history"]
            if item["product_id"] == product.id
        )
        self.assertEqual(row["total_quantity"], 15.0)
        self.assertEqual(row["purchase_count"], 3)
        self.assertEqual(row["avg_qty_per_invoice"], 5.0)

    def test_get_undelivered_so_lines_in_detection_context(self):
        self._add_product_stock(self.product)
        warehouse = self._ensure_product_deliverable(self.product)
        commercial = self.customer.commercial_partner_id
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
        result = self._service().get_customer_detection_context(
            self.customer.id, self.seller_user.id
        )
        self.assertNotIn("message", result)
        lines = result["undelivered_so_lines"]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["sale_order_id"], order.id)
        self.assertEqual(lines[0]["pending_qty"], 4.0)
        self.assertEqual(
            lines[0]["order_date"],
            fields.Date.to_string(fields.Date.context_today(self.env.user)),
        )

    def test_undelivered_so_lines_excludes_orders_older_than_90_days(self):
        self._add_product_stock(self.product)
        warehouse = self._ensure_product_deliverable(self.product)
        commercial = self.customer.commercial_partner_id
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
        today = date.today()
        order.write(
            {"date_order": fields.Datetime.to_datetime(today - timedelta(days=91))}
        )
        result = self._service().get_customer_detection_context(
            self.customer.id, self.seller_user.id
        )
        self.assertNotIn("message", result)
        self.assertEqual(result["undelivered_so_lines"], [])

    def test_get_agent_opportunities_filters_by_stage_status(self):
        contacted = self.env.ref("tommasi_sales_reactivation.stage_cliente_contactado")
        self.env["crm.lead"].with_context(
            reactivation_seller_id=self.seller_user.id
        ).create(
            {
                "name": "Contacted opportunity",
                "type": "opportunity",
                "partner_id": self.customer.id,
                "user_id": self.seller_user.id,
                "stage_id": contacted.id,
                "reactivation_is_agent": True,
            }
        )
        result = self._service().get_agent_opportunities(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
            statuses=["Pendiente de revisión"],
        )
        self.assertEqual(result["opportunities"], [])

    def test_enrich_evidence_dates_fills_missing_on_create(self):
        customer = self.env["res.partner"].create(
            {
                "name": "Evidence Date Enrichment Customer",
                "vat": "30-99998888-7",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        decline_product = self.env["product.product"].create(
            {
                "name": "Evidence Date Product",
                "default_code": "REACT-DATE-ENRICH",
                "type": "product",
            }
        )
        self._add_product_stock(decline_product, quantity=20.0)
        today = date.today()
        prior_date = fields.Date.to_string(today - timedelta(days=80))
        recent_date = fields.Date.to_string(today - timedelta(days=5))
        date_range = {
            "date_from": fields.Date.to_string(today - timedelta(days=120)),
            "date_to": fields.Date.to_string(today),
        }
        self._create_invoice_with_product(
            customer, decline_product, prior_date, 10, 100.0
        )
        self._create_invoice_with_product(
            customer, decline_product, recent_date, 1, 100.0
        )
        detection = self._service().get_customer_detection_context(
            customer.id, self.seller_user.id, date_range=date_range
        )
        decline_row = detection["volume_decline_with_stock"][0]
        legacy_evidence = {
            "triggers": ["volume_decline_with_stock"],
            "details": {
                "volume_decline_with_stock": {
                    "items": [
                        {
                            "product_id": decline_row["product_id"],
                            "sku": decline_row["sku"],
                            "name": decline_row["name"],
                            "prior_quantity": decline_row["prior_quantity"],
                            "recent_quantity": decline_row["recent_quantity"],
                            "available_qty": decline_row["available_qty"],
                        }
                    ]
                }
            },
        }
        payload = self._valid_create_payload(
            customer_id=customer.id,
            trigger_type="volume_decline_with_stock",
            evidence=legacy_evidence,
            suggested_products=[
                {
                    "product_id": decline_product.id,
                    "sku": decline_product.default_code,
                    "name": decline_product.name,
                    "list_price": 100.0,
                    "available_qty": 20.0,
                }
            ],
        )
        result = self._service().create_crm_opportunity(payload)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        description = lead.description or ""
        self.assertIn("Fecha compra anterior", description)
        self.assertIn("Fecha compra reciente", description)
        self.assertIn("Últ. pedido/factura", description)
        self.assertIn(self._format_display_date(prior_date), description)
        self.assertIn(self._format_display_date(recent_date), description)

    def test_bootstrap_includes_partner_when_min_invoices_disabled(self):
        config = self.Config.get_singleton()
        config.write({"bootstrap_min_invoices": 0})
        partner = self.env["res.partner"].create(
            {
                "name": "Single Invoice Customer",
                "vat": "30-77776666-5",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._bootstrap_qualify_partner(partner, self.product, invoice_count=1)
        result = self._service().bootstrap_reactivation_cycle()
        customer_ids = {row["customer_id"] for row in result["customers"]}
        self.assertIn(partner.id, customer_ids)
        config.write({"bootstrap_min_invoices": 3})

    def test_get_customer_detection_context_shape(self):
        result = self._service().get_customer_detection_context(
            self.customer.id, self.seller_user.id
        )
        self.assertEqual(result["customer_id"], self.customer.id)
        self.assertIn("sales_history", result)
        self.assertIn("last_purchase", result)
        self.assertIn("commercial_context", result)
        self.assertIn("product_history", result)
        self.assertIn("volume_decline_with_stock", result)
        self.assertIn("undelivered_so_lines", result)

    def test_get_customer_detection_context_value_parity(self):
        customer = self.env["res.partner"].create(
            {
                "name": "Detection Parity Customer",
                "vat": "30-11112222-3",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        decline_product = self.env["product.product"].create(
            {
                "name": "Decline Parity Product",
                "default_code": "REACT-DECLINE",
                "type": "product",
            }
        )
        self._add_product_stock(decline_product, quantity=25.0)
        today = date.today()
        prior_anchor = today - timedelta(days=80)
        prior_date = fields.Date.to_string(
            date(prior_anchor.year, prior_anchor.month, 10)
        )
        mid_prior_date = fields.Date.to_string(
            date(prior_anchor.year, prior_anchor.month, 20)
        )
        decline_anchor = today - timedelta(days=55)
        decline_prior_date = fields.Date.to_string(
            date(decline_anchor.year, decline_anchor.month, 12)
        )
        last_dt = today - timedelta(days=5)
        last_date = fields.Date.to_string(last_dt)
        recent_date = fields.Date.to_string(
            date(last_dt.year, last_dt.month, max(1, last_dt.day - 2))
        )
        date_range = {
            "date_from": fields.Date.to_string(today - timedelta(days=120)),
            "date_to": fields.Date.to_string(today),
        }
        self._create_invoice_with_product(customer, self.product, prior_date, 2, 500.0)
        self._create_invoice_with_product(
            customer, self.product, mid_prior_date, 1, 500.0
        )
        self._create_invoice_with_product(
            customer, decline_product, decline_prior_date, 10, 100.0
        )
        self._create_invoice_with_product(
            customer, decline_product, recent_date, 1, 100.0
        )
        self._create_invoice_with_product(customer, self.product, last_date, 1, 200.0)

        result = self._service().get_customer_detection_context(
            customer.id, self.seller_user.id, date_range=date_range
        )
        self.assertNotIn("message", result)
        self.assertEqual(result["date_range"], date_range)

        history_by_period = {row["period"]: row for row in result["sales_history"]}
        prior_month = prior_date[:7]
        decline_month = decline_prior_date[:7]
        recent_month = last_date[:7]
        self.assertIn(prior_month, history_by_period)
        self.assertIn(decline_month, history_by_period)
        self.assertIn(recent_month, history_by_period)
        self.assertEqual(history_by_period[prior_month]["revenue"], 1500.0)
        self.assertEqual(history_by_period[prior_month]["invoice_count"], 2)
        self.assertEqual(history_by_period[prior_month]["quantity"], 3.0)
        self.assertEqual(history_by_period[decline_month]["revenue"], 1000.0)
        self.assertEqual(history_by_period[decline_month]["invoice_count"], 1)
        self.assertEqual(history_by_period[decline_month]["quantity"], 10.0)
        self.assertEqual(history_by_period[recent_month]["revenue"], 300.0)
        self.assertEqual(history_by_period[recent_month]["invoice_count"], 2)
        self.assertEqual(history_by_period[recent_month]["quantity"], 2.0)

        self.assertEqual(result["last_purchase"]["last_purchase_date"], last_date)
        self.assertEqual(result["last_purchase"]["days_inactive"], 5)

        product_ids = {row["product_id"] for row in result["product_history"]}
        self.assertIn(self.product.id, product_ids)
        self.assertIn(decline_product.id, product_ids)
        decline_row = next(
            row
            for row in result["product_history"]
            if row["product_id"] == decline_product.id
        )
        self.assertEqual(decline_row["purchase_count"], 2)
        self.assertEqual(decline_row["last_purchase_date"], recent_date)

        decline_entries = [
            row
            for row in result["volume_decline_with_stock"]
            if row["product_id"] == decline_product.id
        ]
        self.assertEqual(len(decline_entries), 1)
        self.assertEqual(decline_entries[0]["prior_quantity"], 10.0)
        self.assertEqual(decline_entries[0]["recent_quantity"], 1.0)
        self.assertEqual(decline_entries[0]["available_qty"], 25.0)
        self.assertEqual(decline_entries[0]["prior_purchase_date"], decline_prior_date)
        self.assertEqual(decline_entries[0]["recent_purchase_date"], recent_date)
        self.assertEqual(decline_entries[0]["last_purchase_or_order_date"], recent_date)

    def test_get_product_history_excludes_purchases_older_than_365_days(self):
        customer = self.env["res.partner"].create(
            {
                "name": "Ancient Purchase Customer",
                "vat": "30-33334444-5",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        ancient_product = self.env["product.product"].create(
            {
                "name": "Ancient One-Off Product",
                "default_code": "REACT-ANCIENT",
                "type": "product",
            }
        )
        recent_product = self.env["product.product"].create(
            {
                "name": "Recent Only Product",
                "default_code": "REACT-RECENT-ONLY",
                "type": "product",
            }
        )
        ancient_date = fields.Date.to_string(date.today() - timedelta(days=400))
        recent_date = fields.Date.to_string(date.today() - timedelta(days=10))
        self._create_invoice_with_product(
            customer, ancient_product, ancient_date, 1, 100.0
        )
        self._create_invoice_with_product(
            customer, recent_product, recent_date, 1, 200.0
        )

        result = self._service().get_customer_detection_context(
            customer.id, self.seller_user.id
        )
        self.assertNotIn("message", result)
        product_ids = {row["product_id"] for row in result["product_history"]}
        self.assertNotIn(ancient_product.id, product_ids)
        self.assertIn(recent_product.id, product_ids)
        self.assertEqual(result["last_purchase"]["last_purchase_date"], recent_date)
        self.assertEqual(result["last_purchase"]["days_inactive"], 10)

    def test_get_customer_detection_context_query_count_bounded(self):
        customer = self.env["res.partner"].create(
            {
                "name": "Query Count Customer",
                "vat": "30-55556666-7",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._bootstrap_qualify_partner(customer, self.product, invoice_count=3)
        self.env.cr.flush()
        invoice_query_count = [0]

        def _count_invoice_queries(cr, query, params, start, delay):
            if "account_move" in (query or "").lower():
                invoice_query_count[0] += 1

        thread = threading.current_thread()
        prior_hooks = tuple(getattr(thread, "query_hooks", ()))
        thread.query_hooks = prior_hooks + (_count_invoice_queries,)
        try:
            self._service().get_customer_detection_context(
                customer.id, self.seller_user.id
            )
        finally:
            thread.query_hooks = prior_hooks
        # Optimized path uses a small fixed set of SQL reads on account_move
        # (moves + lines per helper) instead of ~5 separate ORM invoice searches.
        self.assertLessEqual(
            invoice_query_count[0],
            7,
            "invoice SQL footprint should stay bounded after detection-context refactor",
        )

    def test_get_product_history_excludes_inactive_and_non_stockable_products(self):
        stockable_product = self.env["product.product"].create(
            {
                "name": "Stockable For History",
                "default_code": "REACT-STOCK-HIST",
                "type": "product",
            }
        )
        inactive_product = self.env["product.product"].create(
            {
                "name": "Inactive Product",
                "default_code": "REACT-INACT",
                "type": "product",
                "active": False,
            }
        )
        service_product = self.env["product.product"].create(
            {
                "name": "Service Product",
                "default_code": "REACT-SVC",
                "type": "service",
            }
        )
        consumable_product = self.env["product.product"].create(
            {
                "name": "Consumable Product",
                "default_code": "REACT-CONSU",
                "type": "consu",
            }
        )
        invoice_date = fields.Date.to_string(date.today() - timedelta(days=5))
        for product in (
            inactive_product,
            service_product,
            consumable_product,
            stockable_product,
        ):
            commercial = self.customer.commercial_partner_id
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
                                    "quantity": 1,
                                    "price_unit": 100.0,
                                    "tax_ids": [(6, 0, [])],
                                },
                            )
                        ],
                    }
                )
            )
            post_test_out_invoice(move, seller=self.seller_user)

        self.customer.commercial_partner_id.sudo().write(
            {"user_id": self.seller_user.id}
        )
        self.env["res.partner"].flush()
        result = self._service().get_customer_detection_context(
            self.customer.id, self.seller_user.id
        )
        self.assertNotIn("message", result)
        product_ids = {row["product_id"] for row in result["product_history"]}
        self.assertIn(stockable_product.id, product_ids)
        self.assertNotIn(inactive_product.id, product_ids)
        self.assertNotIn(service_product.id, product_ids)
        self.assertNotIn(consumable_product.id, product_ids)

    def test_get_agent_opportunities_returns_open_agent_lead(self):
        stage = self.env.ref("tommasi_sales_reactivation.stage_pendiente_revision")
        lead = (
            self.env["crm.lead"]
            .with_context(reactivation_seller_id=self.seller_user.id)
            .create(
                {
                    "name": "Agent opportunity",
                    "type": "opportunity",
                    "partner_id": self.customer.id,
                    "user_id": self.seller_user.id,
                    "stage_id": stage.id,
                    "reactivation_is_agent": True,
                    "reactivation_attribution_id": "attr-service-test-001",
                    "reactivation_client_message": "Hola cliente",
                    "reactivation_evidence_summary": "Disparadores: Inactividad",
                }
            )
        )
        result = self._service().get_agent_opportunities(
            customer_id=self.customer.id,
            seller_id=self.seller_user.id,
            statuses=["Pendiente de revisión"],
            include_client_message=True,
        )
        self.assertEqual(len(result["opportunities"]), 1)
        self.assertEqual(result["opportunities"][0]["opportunity_id"], lead.id)
        self.assertEqual(result["opportunities"][0]["client_message"], "Hola cliente")
        self.assertEqual(
            result["opportunities"][0]["evidence_summary"],
            "Disparadores: Inactividad",
        )
        opportunity_url = result["opportunities"][0].get("opportunity_url")
        self.assertTrue(opportunity_url)
        self.assertIn("crm.lead", opportunity_url)
        self.assertIn("id=%s" % lead.id, opportunity_url)
        self.assertIn("view_type=form", opportunity_url)

    def test_helpers_are_not_llm_tools(self):
        tools = self.env["llm.tool"].search(
            [("decorator_model", "=", "tommasi.reactivation.service")]
        )
        tool_names = set(tools.mapped("name"))
        expected = {
            "bootstrap_reactivation_cycle",
            "get_customer_detection_context",
            "get_reactivation_candidates",
            "get_product_recommendations",
            "get_agent_opportunities",
            "create_crm_opportunity",
            "send_whatsapp_to_partner",
        }
        self.assertEqual(tool_names, expected)
        for helper_name in (
            "_filter_enabled_sellers",
            "_get_sales_history",
            "_rank_recommendation_tiers",
        ):
            self.assertNotIn(helper_name, tool_names)

    def test_scoped_bootstrap_returns_single_seller_customer_pair(self):
        result = self._service().bootstrap_reactivation_cycle(
            seller_id=self.seller_user.id,
            customer_id=self.customer.id,
        )
        self.assertNotIn("message", result)
        self.assertNotIn("reason", result)
        self.assertTrue(result["config"]["active"])
        self.assertEqual(len(result["sellers"]), 1)
        self.assertEqual(result["sellers"][0]["seller_id"], self.seller_user.id)
        self.assertEqual(len(result["customers"]), 1)
        customer = result["customers"][0]
        self.assertEqual(customer["customer_id"], self.customer.id)
        self.assertEqual(customer["seller_id"], self.seller_user.id)
        self.assertEqual(customer["identifier"], self.customer.vat)
        self.assertEqual(customer["name"], self.customer.name)

    def test_scoped_bootstrap_unscoped_call_still_returns_full_portfolio(self):
        scoped = self._service().bootstrap_reactivation_cycle(
            seller_id=self.seller_user.id,
            customer_id=self.customer.id,
        )
        unscoped = self._service().bootstrap_reactivation_cycle()
        self.assertEqual(len(scoped["customers"]), 1)
        customer_ids = {row["customer_id"] for row in unscoped["customers"]}
        self.assertIn(self.customer.id, customer_ids)
        self.assertIn(self.customer_b.id, customer_ids)
        self.assertGreaterEqual(len(unscoped["customers"]), 2)

    def test_scoped_bootstrap_fails_ownership_mismatch(self):
        config = self.Config.get_singleton()
        self.Seller.create({"config_id": config.id, "user_id": self.other_seller.id})
        result = self._service().bootstrap_reactivation_cycle(
            seller_id=self.other_seller.id,
            customer_id=self.customer.id,
        )
        self.assertIn("message", result)
        self.assertEqual(result["reason"], "ownership_mismatch")
        self.assertNotIn("config", result)
        self.assertNotIn("sellers", result)
        self.assertNotIn("customers", result)

    def test_scoped_bootstrap_fails_seller_not_enabled(self):
        result = self._service().bootstrap_reactivation_cycle(
            seller_id=self.other_seller.id,
            customer_id=self.customer.id,
        )
        self.assertIn("message", result)
        self.assertEqual(result["reason"], "seller_not_enabled")
        self.assertNotIn("config", result)

    def test_scoped_bootstrap_fails_customer_not_found(self):
        result = self._service().bootstrap_reactivation_cycle(
            seller_id=self.seller_user.id,
            customer_id=999999999,
        )
        self.assertIn("message", result)
        self.assertEqual(result["reason"], "customer_not_found")
        self.assertNotIn("config", result)
