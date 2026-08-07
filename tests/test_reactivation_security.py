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

    def _create_whatsapp_config_for_security(self, **overrides):
        # Use the agent's company so multi-company record rules do not hide the row
        # when model read is temporarily granted for field-level checks.
        existing = self.env["tommasi.whatsapp.config"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if existing:
            existing.unlink()
        values = {
            "name": "Security WhatsApp Config",
            "company_id": self.env.company.id,
            "router_base_url": "https://router.security.test",
            "outbound_key_id": "out_sec_agent_deny",
            "outbound_api_key": "test-outbound-api-key-security0001",
            "outbound_hmac_secret": "test-outbound-hmac-secret-sec0001",
            "chatwoot_account_id": 10,
            "chatwoot_inbox_id": 20,
        }
        values.update(overrides)
        return self.env["tommasi.whatsapp.config"].create(values)

    def test_agent_cannot_read_whatsapp_config_records(self):
        whatsapp_config = self._create_whatsapp_config_for_security()
        agent_config = self.env(user=self.agent_user)["tommasi.whatsapp.config"]
        with self.assertRaises(AccessError):
            agent_config.check_access_rights("read")
        with self.assertRaises(AccessError):
            agent_config.search([("id", "=", whatsapp_config.id)])
        with self.assertRaises(AccessError):
            agent_config.browse(whatsapp_config.id).read(["name"])

    def test_agent_cannot_read_outbound_credential_fields(self):
        """Even with model read temporarily granted, secret fields stay manager-only."""
        whatsapp_config = self._create_whatsapp_config_for_security(
            name="Security WhatsApp Secrets",
            outbound_key_id="out_sec_secret_deny",
            outbound_api_key="test-outbound-api-key-security0002",
            outbound_hmac_secret="test-outbound-hmac-secret-sec0002",
            chatwoot_account_id=11,
            chatwoot_inbox_id=21,
        )
        access = self.env.ref(
            "tommasi_sales_reactivation.access_whatsapp_config_agent"
        )
        access.write(
            {
                "perm_read": True,
                "perm_write": False,
                "perm_create": False,
                "perm_unlink": False,
            }
        )
        self.env["ir.model.access"].clear_caches()

        agent_config = self.env(user=self.agent_user)[
            "tommasi.whatsapp.config"
        ].browse(whatsapp_config.id)
        # Non-secret fields become readable once model ACL allows it.
        name_data = agent_config.read(["name"])
        self.assertEqual(name_data[0]["name"], "Security WhatsApp Secrets")

        secret_fields = [
            "outbound_key_id",
            "outbound_api_key",
            "outbound_hmac_secret",
        ]
        with self.assertRaises(AccessError):
            agent_config.read(secret_fields)
        fields_meta = agent_config.fields_get(secret_fields)
        for field_name in secret_fields:
            self.assertNotIn(field_name, fields_meta)
