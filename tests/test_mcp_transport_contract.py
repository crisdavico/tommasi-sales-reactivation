"""Transport contract tests for the reactivation MCP tools used by the Agent.

All Odoo service methods self-wrap with ``_wrap_mcp_response`` and return
``{data: <payload>, request_id?: ...}``. The LangGraph Agent unwraps strictly via
``extract_mcp_data`` after ``parse_mcp_tool_response``. Golden envelope examples
are mirrored in ``tests/fixtures/contracts/`` (see ``test_mcp_contract_fixtures``).
"""

from datetime import date, timedelta
from unittest.mock import Mock, patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import post_test_out_invoice
from odoo.addons.tommasi_sales_reactivation.tests.mcp_contract import (
    ADDITIVE_CONTRACT_FIXTURES,
    CONTRACT_TOOL_NAMES,
    assert_ownership_fail_payload,
    assert_tool_contract,
    classify_tool_response,
    load_contract_fixture,
)


@tagged("post_install", "-at_install")
class TestMcpTransportContract(TransactionCase):
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
                "name": "Transport Contract Seller",
                "login": "transport_contract_seller_test",
                "email": "transport_contract_seller_test@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.seller_user.partner_id.write(
            {
                "mobile": "+5491188776655",
            }
        )
        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Transport Contract Agent",
                "login": "transport_contract_agent_test",
                "email": "transport_contract_agent_test@example.com",
                "groups_id": [(6, 0, [cls.agent_group.id])],
            }
        )
        config = cls.Config.get_singleton()
        cls.Seller.create({"config_id": config.id, "user_id": cls.seller_user.id})
        cls.product = cls.env["product.product"].create(
            {
                "name": "Transport Contract Product",
                "default_code": "TRANS-001",
                "type": "product",
                "list_price": 100.0,
            }
        )
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Transport Contract Customer",
                "vat": "30-99887766-5",
                "user_id": cls.seller_user.id,
                "customer_rank": 1,
            }
        )
        cls._qualify_customer(cls.customer, cls.product, invoice_count=3)

    @classmethod
    def _qualify_customer(cls, partner, product, invoice_count=3):
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
            commercial.sudo().write({"user_id": cls.seller_user.id})

    def _raw_service(self):
        return self.Service.with_user(self.agent_user)

    def _add_product_stock(self, product, quantity=10.0):
        location = self.env.ref("stock.stock_location_stock")
        self.env["stock.quant"].sudo().create(
            {
                "product_id": product.id,
                "location_id": location.id,
                "quantity": quantity,
            }
        )

    def test_bootstrap_returns_envelope(self):
        raw = self._raw_service().bootstrap_reactivation_cycle()
        shape, payload = classify_tool_response(raw)
        self.assertEqual(shape, "envelope")
        assert_tool_contract("bootstrap_reactivation_cycle", raw)
        self.assertIn("config", payload)
        self.assertIn("sellers", payload)
        self.assertIn("customers", payload)

    def test_get_reactivation_candidates_returns_envelope(self):
        raw = self._raw_service().get_reactivation_candidates(
            seller_id=self.seller_user.id,
            include_context=False,
        )
        shape, payload = classify_tool_response(raw)
        self.assertEqual(shape, "envelope")
        assert_tool_contract("get_reactivation_candidates", raw)
        self.assertEqual(payload["seller_id"], self.seller_user.id)
        self.assertIn("candidates", payload)

    def test_get_agent_opportunities_returns_envelope(self):
        raw = self._raw_service().get_agent_opportunities(
            customer_ids=[self.customer.id],
            seller_id=self.seller_user.id,
            dedup_mode=True,
        )
        shape, payload = classify_tool_response(raw)
        self.assertEqual(shape, "envelope")
        assert_tool_contract("get_agent_opportunities", raw)
        self.assertEqual(payload["seller_id"], self.seller_user.id)
        self.assertIn("results", payload)

    def test_get_product_recommendations_returns_envelope(self):
        self._add_product_stock(self.product)
        raw = self._raw_service().get_product_recommendations(
            customer_ids=[self.customer.id],
            seller_id=self.seller_user.id,
        )
        shape, payload = classify_tool_response(raw)
        self.assertEqual(shape, "envelope")
        assert_tool_contract("get_product_recommendations", raw)
        self.assertEqual(payload["seller_id"], self.seller_user.id)
        self.assertIn("results", payload)

    def test_create_crm_opportunity_batch_returns_envelope_v2(self):
        self._add_product_stock(self.product)
        operation_key = "transport-contract|seller:%s|customer:%s" % (
            self.seller_user.id,
            self.customer.id,
        )
        raw = self._raw_service().create_crm_opportunity(
            payloads=[
                {
                    "customer_id": self.customer.id,
                    "seller_id": self.seller_user.id,
                    "client_message": "Hola, contrato de transporte.",
                    "priority_label": "high",
                    "source": "Agente Comercial",
                    "trigger_type": "inactivity",
                    "confidence": 0.85,
                    "cycle_id": "transport-contract",
                    "operation_key": operation_key,
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
            ]
        )
        shape, payload = classify_tool_response(raw)
        self.assertEqual(shape, "envelope")
        assert_tool_contract("create_crm_opportunity", raw)
        self.assertEqual(payload.get("contract_version"), 2)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["outcome"], "created")

    def test_send_whatsapp_to_partner_returns_envelope(self):
        self.customer.write(
            {
                "mobile": "+5491112345678",
                "allow_whatsapp_communication": True,
            }
        )
        self.env["tommasi.whatsapp.config"].create(
            {
                "name": "Transport WhatsApp",
                "company_id": self.env.company.id,
                "router_base_url": "https://router.test",
                "outbound_key_id": "out_test_transport",
                "outbound_api_key": "test-outbound-api-key-transport00001",
                "outbound_hmac_secret": "test-outbound-hmac-secret-transport",
                "chatwoot_account_id": 1,
                "chatwoot_inbox_id": 5,
            }
        )
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"conversation_id": 42, "message_id": 99}
        response.content = b"{}"
        with patch.object(
            type(self.Service),
            "_post_outbound_message",
            return_value=response,
        ):
            raw = self._raw_service().send_whatsapp_to_partner(
                partner_id=self.customer.id,
                company_id=self.env.company.id,
                template_params={
                    "name": "order_confirmation",
                    "category": "UTILITY",
                    "language": "es",
                    "processed_params": {"body": {"1": "121212"}},
                },
            )
        shape, payload = classify_tool_response(raw)
        self.assertEqual(shape, "envelope")
        assert_tool_contract("send_whatsapp_to_partner", raw)
        self.assertEqual(payload["status"], "sent")
        self.assertEqual(payload["partner_id"], self.customer.id)


@tagged("post_install", "-at_install")
class TestMcpContractFixtures(TransactionCase):
    def test_golden_fixtures_are_valid_envelopes(self):
        for tool_name in CONTRACT_TOOL_NAMES:
            fixture = load_contract_fixture(tool_name)
            assert_tool_contract(tool_name, fixture)

    def test_additive_scoped_fixtures_are_valid_envelopes(self):
        for fixture_name in ADDITIVE_CONTRACT_FIXTURES:
            fixture = load_contract_fixture(fixture_name)
            data = assert_tool_contract(fixture_name, fixture)
            if fixture_name.endswith("_ownership_fail"):
                assert_ownership_fail_payload(data)
                self.assertEqual(data["reason"], "ownership_mismatch")
            elif fixture_name.startswith("bootstrap_reactivation_cycle"):
                self.assertEqual(len(data["sellers"]), 1)
                self.assertEqual(len(data["customers"]), 1)
            elif fixture_name.startswith("get_reactivation_candidates"):
                self.assertEqual(data["seller_id"], 81)
                self.assertEqual(len(data["candidates"]), 1)
                self.assertEqual(data["candidates"][0]["customer_id"], 1001)
