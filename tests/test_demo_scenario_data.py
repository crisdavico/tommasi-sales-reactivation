from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.models.tommasi_reactivation_demo import (
    DEMO_CAP_OPP_TARGET,
)


@tagged("post_install", "-at_install")
class TestDemoScenarioData(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Service = cls.env["tommasi.reactivation.service"]
        cls.Config = cls.env["tommasi.reactivation.config"]
        cls.Demo = cls.env["tommasi.reactivation.demo"]
        cls.seller_a = cls.env.ref(
            "tommasi_sales_reactivation.demo_seller_enabled_a",
            raise_if_not_found=False,
        )
        if not cls.seller_a:
            return
        cls.agent_group = cls.env.ref(
            "tommasi_sales_reactivation.group_reactivation_agent"
        )
        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Demo Scenario Agent",
                "login": "demo_scenario_agent_test",
                "email": "demo_scenario_agent_test@example.com",
                "groups_id": [(6, 0, [cls.agent_group.id])],
            }
        )
        cls.Demo._ensure_demo_scenario_partners()
        cls.Demo._load_demo_transactions()

    def setUp(self):
        super().setUp()
        if not self.seller_a:
            self.skipTest("Demo data not installed")

    def _service(self, seller_id=None):
        seller_id = seller_id or self.seller_a.id
        return self.Service.with_user(self.agent_user).with_context(
            reactivation_seller_id=seller_id
        )

    def _ref(self, xml_id):
        return self.env.ref("tommasi_sales_reactivation.%s" % xml_id)

    def test_sporadic_excluded_at_min_3_included_at_min_1(self):
        sporadic = self._ref("demo_cust_sporadic")
        config = self.Config.get_singleton()
        original_min = config.bootstrap_min_invoices
        try:
            config.write({"bootstrap_min_invoices": 3})
            result = self._service().bootstrap_reactivation_cycle()
            customer_ids = {row["customer_id"] for row in result["customers"]}
            self.assertNotIn(sporadic.id, customer_ids)

            config.write({"bootstrap_min_invoices": 1})
            result = self._service().bootstrap_reactivation_cycle()
            customer_ids = {row["customer_id"] for row in result["customers"]}
            self.assertIn(sporadic.id, customer_ids)
        finally:
            config.write({"bootstrap_min_invoices": original_min})

    def test_supplier_excluded_from_bootstrap(self):
        supplier = self._ref("demo_cust_supplier")
        result = self._service().bootstrap_reactivation_cycle()
        customer_ids = {row["customer_id"] for row in result["customers"]}
        self.assertNotIn(supplier.id, customer_ids)

    def test_urgent_customer_47_days_inactive(self):
        urgent = self._ref("demo_cust_urgent")
        result = self._service().get_customer_detection_context(
            urgent.id, self.seller_a.id
        )
        self.assertEqual(result["last_purchase"]["days_inactive"], 47)

    def test_volume_decline_excludes_no_stock_product(self):
        customer = self._ref("demo_cust_volume_decline")
        product_volume = self._ref("demo_prod_volume_decline")
        product_no_stock = self._ref("demo_prod_no_stock")
        result = self._service().get_customer_detection_context(
            customer.id, self.seller_a.id
        )
        declined_ids = {
            row["product_id"] for row in result["volume_decline_with_stock"]
        }
        self.assertIn(product_volume.id, declined_ids)
        self.assertNotIn(product_no_stock.id, declined_ids)

    def test_draft_noise_shows_inactive(self):
        customer = self._ref("demo_cust_draft_noise")
        result = self._service().get_customer_detection_context(
            customer.id, self.seller_a.id
        )
        self.assertGreaterEqual(result["last_purchase"]["days_inactive"], 40)
        self.assertFalse(result["undelivered_so_lines"])

    def test_archived_opp_older_than_cooldown(self):
        lead = self._ref("demo_opp_archived")
        config = self.Config.get_singleton()
        cutoff = fields.Datetime.now() - relativedelta(
            days=config.cooldown_days + 1
        )
        self.assertLessEqual(lead.create_date, cutoff)

    def test_archived_customer_has_no_open_agent_opportunities(self):
        """Q-02: only archived agent opp; tool input for dedup must be empty."""
        customer = self._ref("demo_cust_archived_opp")
        archived = self._ref("demo_opp_archived")
        open_agent = self.env["crm.lead"].search(
            [
                ("partner_id", "=", customer.id),
                ("reactivation_is_agent", "=", True),
                ("active", "=", True),
            ]
        )
        self.assertFalse(open_agent)
        self.assertFalse(archived.active)
        result = self._service().get_agent_opportunities(
            customer.id,
            self.seller_a.id,
            ["Pendiente de revisión", "Cliente contactado"],
        )
        self.assertFalse(result["opportunities"])

    def test_manual_opportunity_excluded_from_agent_read(self):
        """Q-03: manual opp exists in CRM but not in get_agent_opportunities."""
        customer = self._ref("demo_cust_active")
        manual = self._ref("demo_opp_non_agent")
        self.assertFalse(manual.reactivation_is_agent)
        self.assertTrue(manual.active)
        result = self._service().get_agent_opportunities(
            customer.id,
            self.seller_a.id,
            ["Pendiente de revisión", "Cliente contactado"],
        )
        self.assertFalse(result["opportunities"])

    def test_seller_a_open_agent_opps_exceed_cap(self):
        stage = self._ref("stage_pendiente_revision")
        open_count = self.env["crm.lead"].search_count(
            [
                ("user_id", "=", self.seller_a.id),
                ("reactivation_is_agent", "=", True),
                ("active", "=", True),
                ("stage_id", "=", stage.id),
            ]
        )
        config = self.Config.get_singleton()
        self.assertGreaterEqual(open_count, DEMO_CAP_OPP_TARGET)
        self.assertGreater(open_count, config.opportunity_cap_per_seller)

    def test_multitier_recommendation_order(self):
        customer = self._ref("demo_cust_multitier")
        result = self._service().get_product_recommendations(
            customer.id, self.seller_a.id
        )
        recommendations = result["recommendations"]
        self.assertTrue(recommendations)
        scores = [row["opportunity_score"] for row in recommendations]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for row in recommendations:
            self.assertIn("habitual_units", row)
            self.assertIn("potential_net_margin", row)
            self.assertIn("opportunity_score", row)
            self.assertIn("reason_tier", row)

    def test_low_stock_products_excluded_from_recommendations(self):
        customer = self._ref("demo_cust_low_stock_offer")
        product_low_stock = self._ref("demo_prod_low_stock")
        result = self._service().get_product_recommendations(
            customer.id, self.seller_a.id
        )
        product_ids = {row["product_id"] for row in result["recommendations"]}
        self.assertNotIn(product_low_stock.id, product_ids)
