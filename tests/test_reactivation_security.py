from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import post_test_out_invoice


@tagged("post_install", "-at_install")
class TestReactivationSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent_group = cls.env.ref(
            "tommasi_sales_reactivation.group_reactivation_agent"
        )
        cls.seller_user = cls.env["res.users"].create(
            {
                "name": "Reactivation Seller",
                "login": "reactivation_seller_test",
                "email": "reactivation_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.other_seller = cls.env["res.users"].create(
            {
                "name": "Other Seller",
                "login": "other_seller_test",
                "email": "other_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Reactivation Agent",
                "login": "reactivation_agent_test",
                "email": "reactivation_agent_test@example.com",
                "groups_id": [(6, 0, [cls.agent_group.id])],
            }
        )

    def test_agent_sees_only_context_seller_customers(self):
        owned = self.env["res.partner"].create(
            {
                "name": "Owned Customer",
                "user_id": self.seller_user.id,
            }
        )
        other = self.env["res.partner"].create(
            {
                "name": "Other Customer",
                "user_id": self.other_seller.id,
            }
        )
        agent_env = self.env(user=self.agent_user)
        scoped = agent_env["res.partner"].with_context(
            reactivation_seller_id=self.seller_user.id
        )
        visible = scoped.search([("id", "in", [owned.id, other.id])])
        self.assertIn(owned, visible)
        self.assertNotIn(other, visible)

    def test_agent_without_context_sees_no_scoped_customers(self):
        customer = self.env["res.partner"].create(
            {
                "name": "Scoped Customer",
                "user_id": self.seller_user.id,
            }
        )
        agent_env = self.env(user=self.agent_user)
        visible = agent_env["res.partner"].search([("id", "=", customer.id)])
        self.assertFalse(visible)

    def test_agent_can_create_lead_for_context_seller(self):
        config = self.env["tommasi.reactivation.config"].get_singleton()
        self.env["tommasi.reactivation.seller"].create(
            {"config_id": config.id, "user_id": self.seller_user.id}
        )
        customer = self.env["res.partner"].create(
            {
                "name": "Lead Customer",
                "user_id": self.seller_user.id,
            }
        )
        stage = self.env.ref("tommasi_sales_reactivation.stage_pendiente_revision")
        agent_env = self.env(user=self.agent_user)
        lead = (
            agent_env["crm.lead"]
            .with_context(
                reactivation_seller_id=self.seller_user.id,
                mail_create_nosubscribe=True,
            )
            .create(
                {
                    "name": "Agent opp",
                    "type": "opportunity",
                    "partner_id": customer.id,
                    "user_id": self.seller_user.id,
                    "stage_id": stage.id,
                    "reactivation_is_agent": True,
                }
            )
        )
        self.assertTrue(lead.id)
        self.assertEqual(lead.user_id, self.seller_user)

    def test_agent_sees_only_scoped_seller_invoices(self):
        customer = self.env["res.partner"].create(
            {
                "name": "Invoice Customer",
                "user_id": self.seller_user.id,
            }
        )
        other_customer = self.env["res.partner"].create(
            {
                "name": "Other Invoice Customer",
                "user_id": self.other_seller.id,
            }
        )
        product = self.env["product.product"].create(
            {
                "name": "Security Test Product",
                "type": "product",
                "list_price": 100.0,
            }
        )
        owned_move = None
        for partner, seller in (
            (customer, self.seller_user),
            (other_customer, self.other_seller),
        ):
            commercial = partner.commercial_partner_id
            commercial.sudo().write({"user_id": seller.id})
            move = self.env["account.move"].sudo().create(
                {
                    "move_type": "out_invoice",
                    "partner_id": commercial.id,
                    "invoice_date": "2026-06-01",
                    "date": "2026-06-01",
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
            post_test_out_invoice(move)
            move.sudo().partner_id.write({"user_id": seller.id})
            if seller == self.seller_user:
                owned_move = move
        agent_env = self.env(user=self.agent_user)
        service = agent_env["tommasi.reactivation.service"]
        seller_env = service._env_with_seller(self.seller_user.id)
        visible = seller_env["account.move"].search([("id", "=", owned_move.id)])
        self.assertTrue(visible)
        other_moves = self.env["account.move"].sudo().search(
            [("partner_id", "child_of", other_customer.id)]
        )
        for move in other_moves:
            hidden = seller_env["account.move"].search([("id", "=", move.id)])
            self.assertFalse(hidden)

    def test_agent_sees_only_scoped_seller_sale_orders(self):
        product = self.env["product.product"].create(
            {
                "name": "Security SO Product",
                "type": "product",
                "list_price": 100.0,
            }
        )
        owned_customer = self.env["res.partner"].create(
            {
                "name": "Owned SO Customer",
                "user_id": self.seller_user.id,
            }
        )
        other_customer = self.env["res.partner"].create(
            {
                "name": "Other SO Customer",
                "user_id": self.other_seller.id,
            }
        )
        for partner, seller in (
            (owned_customer, self.seller_user),
            (other_customer, self.other_seller),
        ):
            commercial = partner.commercial_partner_id
            commercial.sudo().write({"user_id": seller.id})
            self.env["sale.order"].sudo().create(
                {
                    "partner_id": commercial.id,
                    "user_id": seller.id,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": product.id,
                                "product_uom_qty": 1,
                                "price_unit": 100.0,
                            },
                        )
                    ],
                }
            )
        agent_env = self.env(user=self.agent_user)
        service = agent_env["tommasi.reactivation.service"]
        seller_env = service._env_with_seller(self.seller_user.id)
        visible = seller_env["sale.order"].search(
            [("partner_id", "child_of", owned_customer.id)]
        )
        self.assertTrue(visible)
        hidden = seller_env["sale.order"].search(
            [("partner_id", "child_of", other_customer.id)]
        )
        self.assertFalse(hidden)

    def test_agent_cannot_create_lead_for_other_seller(self):
        config = self.env["tommasi.reactivation.config"].get_singleton()
        self.env["tommasi.reactivation.seller"].create(
            {"config_id": config.id, "user_id": self.seller_user.id}
        )
        customer = self.env["res.partner"].create(
            {
                "name": "Other seller customer",
                "user_id": self.other_seller.id,
            }
        )
        stage = self.env.ref("tommasi_sales_reactivation.stage_pendiente_revision")
        agent_env = self.env(user=self.agent_user)
        with self.assertRaises(AccessError):
            agent_env["crm.lead"].with_context(
                reactivation_seller_id=self.seller_user.id
            ).create(
                {
                    "name": "Blocked opp",
                    "type": "opportunity",
                    "partner_id": customer.id,
                    "user_id": self.other_seller.id,
                    "stage_id": stage.id,
                    "reactivation_is_agent": True,
                }
            )
