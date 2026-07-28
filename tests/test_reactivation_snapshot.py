from datetime import date, timedelta
import json
import threading

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTester,
    confirm_test_sale_order,
    post_test_out_invoice,
)
from odoo.addons.tommasi_sales_reactivation.models.service_snapshot import (
    SNAPSHOT_MAX_AGE_HOURS,
)


@tagged("post_install", "-at_install")
class TestReactivationFactsSnapshot(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Service = cls.env["tommasi.reactivation.service"]
        cls.Config = cls.env["tommasi.reactivation.config"]
        cls.Seller = cls.env["tommasi.reactivation.seller"]
        cls.Snapshot = cls.env["tommasi.reactivation.facts.snapshot"]
        cls.agent_group = cls.env.ref(
            "tommasi_sales_reactivation.group_reactivation_agent"
        )
        cls.seller_user = cls.env["res.users"].create(
            {
                "name": "Snapshot Seller",
                "login": "snapshot_seller_test",
                "email": "snapshot_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.seller_user.partner_id.write(
            {
                "mobile": "+5491144556677",
            }
        )
        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Snapshot Agent",
                "login": "snapshot_agent_test",
                "email": "snapshot_agent_test@example.com",
                "groups_id": [(6, 0, [cls.agent_group.id])],
            }
        )
        config = cls.Config.get_singleton()
        cls.Seller.create({"config_id": config.id, "user_id": cls.seller_user.id})
        cls.product = cls.env["product.product"].create(
            {
                "name": "Snapshot Product",
                "default_code": "SNAP-001",
                "type": "product",
                "list_price": 100.0,
            }
        )
        cls.qualified_customer = cls.env["res.partner"].create(
            {
                "name": "Snapshot Qualified Customer",
                "vat": "30-51515151-1",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls.low_invoice_customer = cls.env["res.partner"].create(
            {
                "name": "Snapshot Low Invoice Customer",
                "vat": "30-52525252-2",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls._bootstrap_qualify_partner(cls.qualified_customer, cls.product, invoice_count=3)
        cls._bootstrap_qualify_partner(
            cls.low_invoice_customer, cls.product, invoice_count=2
        )

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

    def _create_invoice(self, partner, invoice_date, quantity=1, price_unit=100.0, product=None):
        product = product or self.product
        commercial = partner.commercial_partner_id
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
        return move

    def _ensure_product_deliverable(self, product):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if warehouse and warehouse.delivery_route_id:
            product.sudo().write({"route_ids": [(4, warehouse.delivery_route_id.id)]})
        return warehouse

    def _run_cron(self):
        self.Snapshot.cron_refresh_facts_snapshots()

    def _snapshot_for(self, partner):
        commercial = partner.commercial_partner_id
        return self.Snapshot.search(
            [
                ("partner_id", "=", commercial.id),
                ("company_id", "=", self.env.company.id),
            ],
            limit=1,
        )

    def test_cron_populates_snapshots_for_bootstrap_qualified_only(self):
        self._run_cron()
        qualified_snapshot = self._snapshot_for(self.qualified_customer)
        low_invoice_snapshot = self._snapshot_for(self.low_invoice_customer)
        self.assertTrue(qualified_snapshot)
        self.assertEqual(
            qualified_snapshot.partner_id,
            self.qualified_customer.commercial_partner_id,
        )
        self.assertFalse(low_invoice_snapshot)

    def test_fresh_snapshot_equivalent_to_live_sql(self):
        service = self._service()
        live = service.get_customer_detection_context(
            self.qualified_customer.id,
            self.seller_user.id,
        )
        self._run_cron()
        snapshotted = service.get_customer_detection_context(
            self.qualified_customer.id,
            self.seller_user.id,
        )
        self.assertNotIn("message", live)
        self.assertNotIn("message", snapshotted)
        self.assertEqual(snapshotted["sales_history"], live["sales_history"])
        self.assertEqual(snapshotted["last_purchase"], live["last_purchase"])
        self.assertEqual(snapshotted["product_history"], live["product_history"])
        self.assertEqual(
            snapshotted["commercial_context"]["revenue_window_total"],
            live["commercial_context"]["revenue_window_total"],
        )

    def test_stale_snapshot_falls_back_to_live(self):
        self._run_cron()
        snapshot = self._snapshot_for(self.qualified_customer)
        self.assertTrue(snapshot)
        stale_at = fields.Datetime.now() - timedelta(hours=SNAPSHOT_MAX_AGE_HOURS + 1)
        snapshot.write({"computed_at": stale_at})

        today = date.today()
        recent_date = fields.Date.to_string(today - timedelta(days=3))
        self._create_invoice(self.qualified_customer, recent_date, quantity=5, price_unit=250.0)

        result = self._service().get_customer_detection_context(
            self.qualified_customer.id,
            self.seller_user.id,
        )
        self.assertEqual(result["last_purchase"]["last_purchase_date"], recent_date)
        self.assertEqual(result["last_purchase"]["days_inactive"], 3)

    def test_window_incompatible_snapshot_falls_back_to_live(self):
        self._run_cron()
        snapshot = self._snapshot_for(self.qualified_customer)
        self.assertTrue(snapshot)

        today = date.today()
        narrow_range = {
            "date_from": fields.Date.to_string(today - timedelta(days=30)),
            "date_to": fields.Date.to_string(today),
        }
        result = self._service().get_customer_detection_context(
            self.qualified_customer.id,
            self.seller_user.id,
            date_range=narrow_range,
        )
        self.assertEqual(result["date_range"], narrow_range)
        self.assertNotEqual(
            snapshot.date_from,
            fields.Date.from_string(narrow_range["date_from"]),
        )

    def test_config_hash_mismatch_falls_back_to_live(self):
        self._run_cron()
        snapshot = self._snapshot_for(self.qualified_customer)
        self.assertTrue(snapshot)
        snapshot.write({"config_hash": "stale-config-hash"})

        config = self.Config.get_singleton()
        original = config.inactivity_days_primary
        config.write({"inactivity_days_primary": original + 5})
        try:
            result = self._service().get_customer_detection_context(
                self.qualified_customer.id,
                self.seller_user.id,
            )
            self.assertNotIn("message", result)
            self.assertIn("sales_history", result)
        finally:
            config.write({"inactivity_days_primary": original})

    def test_undelivered_so_lines_always_live(self):
        self._run_cron()
        self._add_product_stock(self.product)
        warehouse = self._ensure_product_deliverable(self.product)
        commercial = self.qualified_customer.commercial_partner_id
        line_vals = {
            "product_id": self.product.id,
            "product_uom_qty": 3.0,
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
            self.qualified_customer.id,
            self.seller_user.id,
        )
        self.assertTrue(result["undelivered_so_lines"])
        self.assertGreater(
            result["commercial_context"]["undelivered_lines_count"], 0
        )

    def test_volume_decline_stock_always_live(self):
        decline_product = self.env["product.product"].create(
            {
                "name": "Snapshot Decline Product",
                "default_code": "SNAP-DECLINE",
                "type": "product",
            }
        )
        today = date.today()
        prior_date = fields.Date.to_string(today - timedelta(days=70))
        recent_date = fields.Date.to_string(today - timedelta(days=15))
        self._create_invoice(
            self.qualified_customer,
            prior_date,
            quantity=10,
            price_unit=50.0,
            product=decline_product,
        )
        move = (
            self.env["account.move"]
            .sudo()
            .create(
                {
                    "move_type": "out_invoice",
                    "partner_id": self.qualified_customer.commercial_partner_id.id,
                    "invoice_date": recent_date,
                    "date": recent_date,
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": decline_product.id,
                                "quantity": 1,
                                "price_unit": 50.0,
                                "tax_ids": [(6, 0, [])],
                            },
                        )
                    ],
                }
            )
        )
        post_test_out_invoice(move, seller=self.seller_user)

        self._run_cron()
        self._add_product_stock(decline_product, quantity=42.0)

        result = self._service().get_customer_detection_context(
            self.qualified_customer.id,
            self.seller_user.id,
        )
        decline_rows = [
            row
            for row in result["volume_decline_with_stock"]
            if row["product_id"] == decline_product.id
        ]
        self.assertEqual(len(decline_rows), 1)
        self.assertEqual(decline_rows[0]["available_qty"], 42.0)

    def test_invoice_facts_batch_matches_scalar(self):
        service = self._service()._service
        commercial_a = self.qualified_customer.commercial_partner_id.id
        commercial_b = self.low_invoice_customer.commercial_partner_id.id
        window = service._resolve_date_range()
        today = fields.Date.context_today(service)
        window_date_from = service._parse_date(window["date_from"])
        from odoo.addons.tommasi_sales_reactivation.models.tommasi_reactivation_service import (
            PRODUCT_HISTORY_FLOOR_WINDOW_DAYS,
        )

        floor_date = today - timedelta(days=PRODUCT_HISTORY_FLOOR_WINDOW_DAYS)
        batch_date_from = fields.Date.to_string(min(window_date_from, floor_date))

        scalar_a = service._get_invoice_facts(
            self.qualified_customer.id,
            date_from=batch_date_from,
            date_to=window["date_to"],
            commercial_partner_id=commercial_a,
        )
        scalar_b = service._get_invoice_facts(
            self.low_invoice_customer.id,
            date_from=batch_date_from,
            date_to=window["date_to"],
            commercial_partner_id=commercial_b,
        )
        batch = service._get_invoice_facts_batch(
            [commercial_a, commercial_b],
            date_from=batch_date_from,
            date_to=window["date_to"],
        )
        self.assertEqual(batch[commercial_a], scalar_a)
        self.assertEqual(batch[commercial_b], scalar_b)

    def test_cron_snapshot_multi_partner_history_parity(self):
        second_customer = self.env["res.partner"].create(
            {
                "name": "Snapshot Second Qualified",
                "vat": "30-61616161-1",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._bootstrap_qualify_partner(second_customer, self.product, invoice_count=3)
        service = self._service()
        config = self.Config.get_singleton()
        self._run_cron()
        for customer in (self.qualified_customer, second_customer):
            snapshot = self._snapshot_for(customer)
            self.assertTrue(snapshot)
            live = service.get_customer_detection_context(
                customer.id, self.seller_user.id
            )
            self.assertNotIn("message", live)
            self.assertEqual(
                json.loads(snapshot.sales_history_json),
                live["sales_history"],
            )
            stored_product_history = json.loads(snapshot.product_history_json)
            service._service._annotate_product_dropoff(
                stored_product_history, config=config
            )
            self.assertEqual(stored_product_history, live["product_history"])

    def test_cron_refresh_invoice_query_count_bounded(self):
        second_customer = self.env["res.partner"].create(
            {
                "name": "Snapshot Query Count Customer",
                "vat": "30-62626262-2",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
            }
        )
        self._bootstrap_qualify_partner(second_customer, self.product, invoice_count=3)
        self.env.cr.flush()
        invoice_query_count = [0]

        def _count_invoice_queries(cr, query, params, start, delay):
            if "account_move" in (query or "").lower():
                invoice_query_count[0] += 1

        thread = threading.current_thread()
        prior_hooks = tuple(getattr(thread, "query_hooks", ()))
        thread.query_hooks = prior_hooks + (_count_invoice_queries,)
        try:
            self._run_cron()
        finally:
            thread.query_hooks = prior_hooks
        enabled_seller_count = len(self.Service._filter_enabled_sellers())
        per_seller_invoice_query_budget = 6
        self.assertLessEqual(
            invoice_query_count[0],
            enabled_seller_count * per_seller_invoice_query_budget,
            "cron snapshot refresh should batch invoice facts per seller",
        )
