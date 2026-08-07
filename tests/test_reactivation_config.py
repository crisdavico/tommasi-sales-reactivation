from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestReactivationConfig(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["tommasi.reactivation.config"]
        cls.Seller = cls.env["tommasi.reactivation.seller"]
        cls.PriorityRule = cls.env["tommasi.reactivation.priority.rule"]

    def test_singleton_create_is_idempotent(self):
        first = self.Config.create({"cooldown_days": 15})
        second = self.Config.create({"cooldown_days": 99})
        self.assertEqual(first, second)
        self.assertEqual(first.cooldown_days, 99)
        self.assertEqual(
            self.Config.search_count([("company_id", "=", self.env.company.id)]), 1
        )

    def test_singleton_cannot_be_deleted(self):
        config = self.Config.get_singleton()
        with self.assertRaises(UserError):
            config.unlink()

    def test_default_data_seeded(self):
        config = self.env.ref(
            "tommasi_sales_reactivation.reactivation_config_singleton"
        )
        self.assertEqual(config.company_id, self.env.company)
        self.assertEqual(config.cooldown_days, 30)
        self.assertEqual(config.opportunity_cap_per_seller, 20)
        self.assertEqual(config.discount_pct, 55.0)
        self.assertEqual(config.min_confidence_threshold, 0.50)
        self.assertEqual(config.bootstrap_min_invoices, 3)
        self.assertEqual(config.bootstrap_invoice_window_days, 180)
        self.assertEqual(config.tables_row_limit, 10)
        self.assertTrue(config.active)

    def test_priority_rules_seeded(self):
        config = self.env.ref(
            "tommasi_sales_reactivation.reactivation_config_singleton"
        )
        rules = config.priority_rule_ids
        self.assertEqual(len(rules), 3)
        labels = set(rules.mapped("label"))
        self.assertEqual(labels, {"high", "medium", "low"})

        high = rules.filtered(lambda r: r.label == "high")
        self.assertEqual(high.min_confidence, 0.80)
        self.assertEqual(high.min_inactivity_days, 45)
        self.assertEqual(high.min_decline_pct, 50.0)
        self.assertEqual(high.cap_order, 1)

        medium = rules.filtered(lambda r: r.label == "medium")
        self.assertEqual(medium.min_confidence, 0.60)
        self.assertEqual(medium.cap_order, 2)

        low = rules.filtered(lambda r: r.label == "low")
        self.assertEqual(low.min_confidence, 0.50)
        self.assertEqual(low.cap_order, 3)

    def test_seller_unique_per_config(self):
        config = self.Config.get_singleton()
        user = self.env["res.users"].create(
            {
                "name": "Reactivation Seller A",
                "login": "reactivation_seller_a",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        self.Seller.create({"config_id": config.id, "user_id": user.id})
        with self.assertRaises(ValidationError):
            self.Seller.create({"config_id": config.id, "user_id": user.id})
        with mute_logger("odoo.sql_db"):
            with self.assertRaises(IntegrityError):
                with self.cr.savepoint():
                    self.env.cr.execute(
                        """
                        INSERT INTO tommasi_reactivation_seller
                            (config_id, user_id, create_uid, write_uid,
                             create_date, write_date)
                        VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'UTC',
                                NOW() AT TIME ZONE 'UTC')
                        """,
                        (config.id, user.id, self.env.uid, self.env.uid),
                    )

    def test_seller_user_ids_adds_multiple_lines(self):
        config = self.Config.get_singleton()
        user_a = self.env["res.users"].create(
            {
                "name": "Many2many Seller A",
                "login": "many2many_seller_a",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        user_b = self.env["res.users"].create(
            {
                "name": "Many2many Seller B",
                "login": "many2many_seller_b",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        config.seller_ids.filtered(
            lambda seller: seller.user_id in (user_a | user_b)
        ).unlink()
        config.write({"seller_user_ids": [(6, 0, [user_a.id, user_b.id])]})
        seller_users = config.seller_ids.filtered(
            lambda seller: seller.user_id in (user_a | user_b)
        ).mapped("user_id")
        self.assertEqual(seller_users, user_a | user_b)

    def test_seller_user_ids_cannot_duplicate_existing_seller(self):
        config = self.Config.get_singleton()
        user = self.env["res.users"].create(
            {
                "name": "Many2many Seller Duplicate",
                "login": "many2many_seller_duplicate",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        self.Seller.create({"config_id": config.id, "user_id": user.id})
        with self.assertRaises(ValidationError):
            self.Seller.create({"config_id": config.id, "user_id": user.id})

    def test_seller_reads_partner_mobile_field(self):
        config = self.Config.get_singleton()
        user = self.env.ref("base.user_admin")
        partner = user.partner_id
        partner.write({"mobile": "+5491112345678"})
        seller = self.Seller.create({"config_id": config.id, "user_id": user.id})
        self.assertEqual(seller.mobile, "+5491112345678")

    def test_config_parameter_constraints(self):
        config = self.Config.get_singleton()
        with self.assertRaises(ValidationError):
            config.write({"cooldown_days": -1})
        with self.assertRaises(ValidationError):
            config.write({"discount_pct": 150.0})
        with self.assertRaises(ValidationError):
            config.write({"min_confidence_threshold": 1.5})
        with self.assertRaises(ValidationError):
            config.write(
                {
                    "inactivity_days_primary": 60,
                    "inactivity_days_secondary": 30,
                }
            )
        with self.assertRaises(ValidationError):
            config.write({"opportunity_cap_per_seller": 0})
        with self.assertRaises(ValidationError):
            config.write({"suggested_products_max": 0})
        with self.assertRaises(ValidationError):
            config.write({"tables_row_limit": 0})
        with self.assertRaises(ValidationError):
            config.write({"bootstrap_invoice_window_days": 0})

    def test_batch_create_multiple_companies_raises_user_error(self):
        company_b = self.env["res.company"].create({"name": "Config Batch Company B"})
        with self.assertRaises(UserError):
            self.Config.create(
                [
                    {"name": "Config A", "company_id": self.env.company.id},
                    {"name": "Config B", "company_id": company_b.id},
                ]
            )

    def test_priority_rule_unique_label_per_config(self):
        config = self.Config.get_singleton()
        with mute_logger("odoo.sql_db"):
            with self.assertRaises(IntegrityError):
                with self.cr.savepoint():
                    self.PriorityRule.create(
                        {"config_id": config.id, "label": "high"}
                    )

    def test_get_primary_config_returns_main_company(self):
        main_company = self.env.ref("base.main_company")
        config = self.Config.get_primary_config()
        self.assertEqual(config.company_id, main_company)

    def test_get_all_configs_includes_secondary_company(self):
        company_b = self.env["res.company"].create({"name": "Config All Companies B"})
        config_b = self.Config.create(
            {
                "name": "Sales reactivation secondary",
                "company_id": company_b.id,
            }
        )
        all_configs = self.Config.get_all_configs()
        self.assertIn(config_b, all_configs)

    def test_resolve_stock_location_ids_empty_returns_all_internal(self):
        config = self.Config.get_singleton()
        config.write({"stock_location_ids": [(5, 0, 0)]})
        resolved = set(config._resolve_stock_location_ids())
        internal_ids = set(
            self.env["stock.location"]
            .search(
                [
                    ("usage", "=", "internal"),
                    ("company_id", "in", [config.company_id.id, False]),
                ]
            )
            .ids
        )
        self.assertEqual(resolved, internal_ids)

    def test_resolve_stock_location_ids_restricts_to_configured_warehouse(self):
        config = self.Config.get_singleton()
        warehouses = self.env["stock.warehouse"].search(
            [("company_id", "=", config.company_id.id)]
        )
        self.assertGreaterEqual(len(warehouses), 1)
        warehouse = warehouses[0]
        config.write({"stock_location_ids": [(6, 0, [warehouse.lot_stock_id.id])]})
        resolved = set(config._resolve_stock_location_ids())
        self.assertIn(warehouse.lot_stock_id.id, resolved)
        for location_id in resolved:
            location = self.env["stock.location"].browse(location_id)
            self.assertEqual(location.usage, "internal")

    def test_stock_location_constraint_rejects_other_company(self):
        config = self.Config.get_singleton()
        company_b = self.env["res.company"].create({"name": "Stock Location Company B"})
        warehouse_b = self.env["stock.warehouse"].create(
            {
                "name": "Warehouse B",
                "code": "WHB",
                "company_id": company_b.id,
            }
        )
        with self.assertRaises(ValidationError):
            config.write({"stock_location_ids": [(6, 0, [warehouse_b.lot_stock_id.id])]})
