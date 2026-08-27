"""Shared helpers for tommasi_sales_reactivation tests."""

from datetime import date, timedelta

from odoo import fields

from odoo.addons.tommasi_sales_reactivation.tests.mcp_contract import mcp_data


def unwrap_tool_result(result):
    """Return the inner payload when a tool response uses the MCP envelope."""
    if (
        isinstance(result, dict)
        and "data" in result
        and set(result.keys()) <= {"data", "request_id"}
    ):
        return mcp_data(result)
    return result


class ReactivationServiceTester:
    """Proxy that unwraps MCP envelopes from llm_tool read methods."""

    def __init__(self, service):
        self._service = service

    def __getattr__(self, name):
        attr = getattr(self._service, name)
        if not callable(attr):
            return attr

        def caller(*args, **kwargs):
            return unwrap_tool_result(attr(*args, **kwargs))

        return caller


def upsert_whatsapp_config(env, company=None, **overrides):
    """Search by company_id, write if found, else create."""
    company = company or env.company
    values = {
        "name": "WhatsApp test",
        "company_id": company.id,
        "router_base_url": "https://router.test",
        "outbound_key_id": "out_test_config",
        "outbound_api_key": "test-outbound-api-key-000000000001",
        "outbound_hmac_secret": "test-outbound-hmac-secret-00000001",
        "chatwoot_account_id": 1,
        "chatwoot_inbox_id": 5,
        **overrides,
    }
    existing = env["tommasi.whatsapp.config"].search(
        [("company_id", "=", company.id)], limit=1)
    if existing:
        existing.write(values)
        return existing
    return env["tommasi.whatsapp.config"].create(values)


def confirm_test_sale_order(order, env):
    """Confirm a sale order, setting delivery when required by customizations."""
    order = order.sudo()
    carrier = env["delivery.carrier"].search([], limit=1)
    if carrier and hasattr(order, "set_delivery_line") and not order.delivery_set:
        order.set_delivery_line(carrier, 0.0)
    elif carrier and "carrier_id" in order._fields and not order.carrier_id:
        order.write({"carrier_id": carrier.id})
    order.action_confirm()
    return order


def get_internal_sale_journal(env, company=None):
    """Return a non-electronic sale journal suitable for posting test invoices."""
    company = company or env.company
    Journal = env["account.journal"].sudo()
    journal = Journal.search(
        [
            ("type", "=", "sale"),
            ("company_id", "=", company.id),
            ("fiscal_type", "=", "internal"),
        ],
        limit=1,
    )
    if not journal:
        template = Journal.search(
            [("type", "=", "sale"), ("company_id", "=", company.id)],
            limit=1,
        )
        if not template:
            raise AssertionError(
                "No sale journal found for company %s" % company.display_name
            )
        journal = template.copy(
            {
                "name": "Test Sales Internal",
                "code": "T%05d" % (company.id % 100000),
                "fiscal_type": "internal",
            }
        )
    if not journal.due_date:
        journal.write({"due_date": fields.Date.to_date("2020-01-01")})
    return journal


def post_test_out_invoice(move, seller=None):
    """Post a customer invoice without AFIP electronic validation."""
    company = move.company_id or move.env.company
    journal = get_internal_sale_journal(move.env, company=company)
    if move.journal_id.fiscal_type == "electronic":
        move.sudo().write({"journal_id": journal.id})
    commercial = move.partner_id.commercial_partner_id
    seller_id = seller.id if seller else commercial.user_id.id
    move.action_post()
    if seller_id:
        commercial.sudo().write({"user_id": seller_id})
    return move


class ReactivationServiceTestMixin:
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
                "name": "Enabled Seller",
                "login": "enabled_seller_test",
                "email": "enabled_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.seller_user.partner_id.write(
            {
                "mobile": "+5491112345678",
            }
        )
        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Reactivation Agent",
                "login": "reactivation_agent_service_test",
                "email": "reactivation_agent_service_test@example.com",
                "groups_id": [(6, 0, [cls.agent_group.id])],
            }
        )
        config = cls.Config.get_singleton()
        cls.Seller.create({"config_id": config.id, "user_id": cls.seller_user.id})
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Service Customer",
                "vat": "30-12345678-9",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Reactivation Product",
                "default_code": "REACT-001",
                "type": "product",
                "list_price": 100.0,
            }
        )
        cls.stage_pendiente = cls.env.ref(
            "tommasi_sales_reactivation.stage_pendiente_revision"
        )
        cls.stage_contactado = cls.env.ref(
            "tommasi_sales_reactivation.stage_cliente_contactado"
        )
        cls.other_seller = cls.env["res.users"].create(
            {
                "name": "Other Seller",
                "login": "other_seller_service_test",
                "email": "other_seller_service_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.customer_b = cls.env["res.partner"].create(
            {
                "name": "Service Customer B",
                "vat": "30-87654321-0",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls._bootstrap_qualify_partner(
            cls.customer, cls.product, invoice_count=3, seller=cls.seller_user
        )
        cls._bootstrap_qualify_partner(
            cls.customer_b, cls.product, invoice_count=3, seller=cls.seller_user
        )

    @classmethod
    def _bootstrap_qualify_partner(
        cls, partner, product, invoice_count=3, seller=None, set_customer_rank=True
    ):
        seller = seller or partner.user_id
        commercial = partner.commercial_partner_id
        values = {"user_id": seller.id}
        if set_customer_rank:
            values["customer_rank"] = 1
        commercial.sudo().write(values)
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
            post_test_out_invoice(move, seller=seller)
            commercial.sudo().write({"user_id": seller.id})

    def _service(self):
        return ReactivationServiceTester(self.Service.with_user(self.agent_user))

    def _create_posted_out_invoice(
        self, partner, invoice_date, amount, seller_user=None
    ):
        seller = seller_user or self.seller_user
        commercial = partner.commercial_partner_id
        commercial.sudo().write({"user_id": seller.id})
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
                                "product_id": self.product.id,
                                "quantity": 1,
                                "price_unit": amount,
                                "tax_ids": [(6, 0, [])],
                            },
                        )
                    ],
                }
            )
        )
        post_test_out_invoice(move, seller=seller)
        move.sudo().partner_id.write({"user_id": seller.id})
        self.assertEqual(move.state, "posted")
        self.assertGreater(move.amount_untaxed_signed, 0.0)
        seller_env = self.Service.with_user(self.agent_user)._env_with_seller(seller.id)
        self.assertIn(
            move.id,
            seller_env["account.move"].search([("id", "=", move.id)]).ids,
            "Agent must read posted seller invoice",
        )
        return move

    def _add_product_stock(self, product, quantity=10.0, location=None):
        if location is None:
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

    def _create_invoice_with_product(
        self, partner, product, invoice_date, quantity, price_unit, seller_user=None
    ):
        seller = seller_user or self.seller_user
        commercial = partner.commercial_partner_id
        commercial.sudo().write({"user_id": seller.id})
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
        post_test_out_invoice(move, seller=seller)
        move.sudo().partner_id.write({"user_id": seller.id})
        return move

    def _valid_create_payload(self, **overrides):
        payload = {
            "customer_id": self.customer.id,
            "seller_id": self.seller_user.id,
            "client_message": "Hola, le escribo de Tommasi con una oferta.",
            "priority_label": "high",
            "source": "Agente Comercial",
            "trigger_type": "inactivity",
            "confidence": 0.85,
            "cycle_id": "test-cycle-001",
            "suggested_products": [
                {
                    "product_id": self.product.id,
                    "sku": self.product.default_code,
                    "name": self.product.name,
                    "list_price": 100.0,
                    "available_qty": 10.0,
                }
            ],
        }
        payload.update(overrides)
        return payload

    def _ensure_product_deliverable(self, product):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if warehouse and warehouse.delivery_route_id:
            product.sudo().write({"route_ids": [(4, warehouse.delivery_route_id.id)]})
        return warehouse

    def _format_display_date(self, iso_date):
        parsed = fields.Date.from_string(iso_date)
        return parsed.strftime("%d/%m/%Y")
