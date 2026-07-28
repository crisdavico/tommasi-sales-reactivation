from unittest.mock import Mock, patch

import requests
from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTestMixin,
    unwrap_tool_result,
)
from odoo.addons.tommasi_sales_reactivation.tests.mcp_contract import (
    assert_mcp_envelope,
    assert_tool_contract,
)


def _template_params():
    return {
        "name": "order_confirmation",
        "category": "UTILITY",
        "language": "es",
        "processed_params": {"body": {"1": "121212"}},
    }


def _mock_response(status_code, json_body=None, content=None):
    response = Mock()
    response.status_code = status_code
    if json_body is not None:
        response.json.return_value = json_body
        response.content = b"{}"
    elif content is not None:
        response.json.side_effect = ValueError("not json")
        response.content = content
    else:
        response.json.return_value = {}
        response.content = b""
    return response


@tagged("post_install", "-at_install")
class TestWhatsappConfig(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.WhatsappConfig = cls.env["tommasi.whatsapp.config"]
        cls.company_b = cls.env["res.company"].create(
            {
                "name": "WhatsApp Config Test Company B",
                "currency_id": cls.env.company.currency_id.id,
            }
        )

    def _config_vals(self, **overrides):
        values = {
            "name": "WhatsApp test",
            "company_id": self.env.company.id,
            "router_base_url": "https://router.test",
            "outbound_api_key": "secret-key",
            "chatwoot_account_id": 1,
            "chatwoot_inbox_id": 5,
            "http_timeout_seconds": 30,
        }
        values.update(overrides)
        return values

    def test_company_uniqueness(self):
        self.WhatsappConfig.create(
            self._config_vals(company_id=self.company_b.id, name="Company B")
        )
        with mute_logger("odoo.sql_db"):
            with self.assertRaises(IntegrityError):
                with self.cr.savepoint():
                    self.WhatsappConfig.create(
                        self._config_vals(
                            company_id=self.company_b.id, name="Duplicate"
                        )
                    )

    def test_router_url_validation(self):
        with self.assertRaises(ValidationError):
            self.WhatsappConfig.create(self._config_vals(router_base_url="ftp://bad"))

    def test_positive_chatwoot_ids(self):
        with self.assertRaises(ValidationError):
            self.WhatsappConfig.create(self._config_vals(chatwoot_account_id=0))

    def test_timeout_bounds(self):
        with self.assertRaises(ValidationError):
            self.WhatsappConfig.create(self._config_vals(http_timeout_seconds=3))


@tagged("post_install", "-at_install")
class TestReactivationWhatsapp(ReactivationServiceTestMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.WhatsappConfig = cls.env["tommasi.whatsapp.config"]
        cls.WhatsappLog = cls.env["tommasi.whatsapp.log"]
        cls.company_b = cls.env["res.company"].create(
            {
                "name": "WhatsApp Test Company B",
                "currency_id": cls.env.company.currency_id.id,
            }
        )
        cls.whatsapp_config = cls.WhatsappConfig.create(
            {
                "name": "Main WhatsApp",
                "company_id": cls.env.company.id,
                "router_base_url": "https://router.test",
                "outbound_api_key": "outbound-secret",
                "chatwoot_account_id": 1,
                "chatwoot_inbox_id": 5,
            }
        )
        cls.whatsapp_config_b = cls.WhatsappConfig.create(
            {
                "name": "Company B WhatsApp",
                "company_id": cls.company_b.id,
                "router_base_url": "https://router-b.test",
                "outbound_api_key": "outbound-secret-b",
                "chatwoot_account_id": 2,
                "chatwoot_inbox_id": 8,
            }
        )

    def setUp(self):
        super().setUp()
        self.customer.write(
            {
                "mobile": "+5491112345678",
                "allow_whatsapp_communication": True,
            }
        )

    def _raw_service(self):
        return self.Service.with_user(self.agent_user)

    def _send(self, **kwargs):
        params = {
            "partner_id": self.customer.id,
            "company_id": self.env.company.id,
            "template_params": _template_params(),
        }
        params.update(kwargs)
        return self._raw_service().send_whatsapp_to_partner(**params)

    def test_successful_send_builds_exact_request(self):
        response = _mock_response(
            200, {"conversation_id": 42, "message_id": 99}
        )
        with patch.object(
            type(self.Service),
            "_post_outbound_message",
            return_value=response,
        ) as mocked_post:
            raw = self._send(
                idempotency_key="opp-1001",
                content="Hola desde Tommasi",
            )
            mocked_post.assert_called_once()
            config, payload = mocked_post.call_args[0]
            self.assertEqual(config, self.whatsapp_config)
            self.assertEqual(
                payload,
                {
                    "account_id": 1,
                    "inbox_id": 5,
                    "phone": "+5491112345678",
                    "template_params": _template_params(),
                    "content": "Hola desde Tommasi",
                    "idempotency_key": "opp-1001",
                },
            )
        data = unwrap_tool_result(raw)
        self.assertEqual(data["status"], "sent")
        self.assertEqual(data["http_status"], 200)
        self.assertEqual(data["conversation_id"], 42)
        self.assertEqual(data["message_id"], 99)
        self.assertFalse(data["retryable"])
        log = self.WhatsappLog.search(
            [("partner_id", "=", self.customer.id)], limit=1
        )
        self.assertEqual(log.status, "sent")
        self.assertEqual(log.template_name, "order_confirmation")
        self.assertFalse(log.error_message)

    def test_routes_by_explicit_company(self):
        shared_partner = self.env["res.partner"].create(
            {
                "name": "Shared Company Partner",
                "company_id": False,
                "mobile": "+5491199001122",
                "allow_whatsapp_communication": True,
                "customer_rank": 1,
            }
        )
        response = _mock_response(
            200, {"conversation_id": 7, "message_id": 8}
        )
        with patch.object(
            type(self.Service),
            "_post_outbound_message",
            return_value=response,
        ) as mocked_post:
            self._send(partner_id=shared_partner.id, company_id=self.company_b.id)
            config = mocked_post.call_args[0][0]
            self.assertEqual(config.company_id, self.company_b)
            self.assertEqual(config.router_base_url, "https://router-b.test")

    def test_rejects_company_mismatch(self):
        company_partner = self.env["res.partner"].create(
            {
                "name": "Company A Partner",
                "company_id": self.env.company.id,
                "mobile": "+5491188009900",
                "allow_whatsapp_communication": True,
            }
        )
        data = unwrap_tool_result(
            self._send(partner_id=company_partner.id, company_id=self.company_b.id)
        )
        self.assertEqual(data["status"], "rejected_company_mismatch")

    def test_rejects_missing_consent(self):
        self.customer.write({"allow_whatsapp_communication": False})
        data = unwrap_tool_result(self._send())
        self.assertEqual(data["status"], "rejected_no_consent")

    def test_rejects_invalid_mobile(self):
        self.customer.write({"mobile": "011-1234-5678"})
        data = unwrap_tool_result(self._send())
        self.assertEqual(data["status"], "rejected_invalid_mobile")

    def test_rejects_missing_partner(self):
        data = unwrap_tool_result(self._send(partner_id=99999999))
        self.assertEqual(data["status"], "rejected_partner_not_found")

    def test_rejects_inactive_partner(self):
        self.customer.write({"active": False})
        data = unwrap_tool_result(self._send())
        self.assertEqual(data["status"], "rejected_partner_inactive")

    def test_rejects_missing_config(self):
        self.whatsapp_config.write({"active": False})
        data = unwrap_tool_result(self._send())
        self.assertEqual(data["status"], "rejected_no_config")

    def test_maps_router_409_as_retryable(self):
        response = _mock_response(409, {"detail": "pending"})
        with patch.object(
            type(self.Service), "_post_outbound_message", return_value=response
        ):
            data = unwrap_tool_result(self._send(idempotency_key="dup-key"))
        self.assertEqual(data["http_status"], 409)
        self.assertTrue(data["retryable"])

    def test_maps_router_422(self):
        response = _mock_response(422, {"detail": "invalid phone"})
        with patch.object(
            type(self.Service), "_post_outbound_message", return_value=response
        ):
            data = unwrap_tool_result(self._send())
        self.assertEqual(data["http_status"], 422)
        self.assertFalse(data["retryable"])

    def test_maps_router_401(self):
        response = _mock_response(401, {"detail": "bad api key"})
        with patch.object(
            type(self.Service), "_post_outbound_message", return_value=response
        ):
            data = unwrap_tool_result(self._send())
        self.assertEqual(data["http_status"], 401)
        self.assertFalse(data["retryable"])

    def test_maps_router_502_and_503_as_retryable(self):
        for status in (502, 503):
            response = _mock_response(status, {"detail": "upstream"})
            with patch.object(
                type(self.Service),
                "_post_outbound_message",
                return_value=response,
            ):
                data = unwrap_tool_result(self._send())
            self.assertEqual(data["http_status"], status)
            self.assertTrue(data["retryable"])

    def test_timeout_is_retryable(self):
        with patch.object(
            type(self.Service),
            "_post_outbound_message",
            side_effect=requests.Timeout("timed out"),
        ):
            data = unwrap_tool_result(self._send())
        self.assertEqual(data["status"], "error")
        self.assertTrue(data["retryable"])

    def test_transport_error_is_retryable(self):
        with patch.object(
            type(self.Service),
            "_post_outbound_message",
            side_effect=requests.ConnectionError("down"),
        ):
            data = unwrap_tool_result(self._send())
        self.assertEqual(data["status"], "error")
        self.assertTrue(data["retryable"])

    def test_malformed_json_response(self):
        response = _mock_response(200, content=b"not-json")
        with patch.object(
            type(self.Service), "_post_outbound_message", return_value=response
        ):
            data = unwrap_tool_result(self._send())
        self.assertEqual(data["status"], "error")

    def test_audit_does_not_store_processed_params(self):
        secret_params = {
            "name": "order_confirmation",
            "category": "UTILITY",
            "language": "es",
            "processed_params": {"body": {"1": "super-secret-value"}},
        }
        response = _mock_response(
            200, {"conversation_id": 1, "message_id": 2}
        )
        with patch.object(
            type(self.Service), "_post_outbound_message", return_value=response
        ):
            self._send(template_params=secret_params)
        log = self.WhatsappLog.search(
            [("partner_id", "=", self.customer.id)], limit=1
        )
        self.assertNotIn("super-secret-value", log.display_name)
        self.assertNotIn("super-secret-value", log.error_message or "")

    def test_sanitize_error_redacts_secrets(self):
        service = self.Service
        sanitized = service._sanitize_whatsapp_error(
            "Authorization bearer api_key=abc123 failed"
        )
        self.assertIn("redacted", sanitized)

    def test_requires_partner_and_company_ids(self):
        with self.assertRaises(UserError):
            self._raw_service().send_whatsapp_to_partner(
                partner_id=0,
                company_id=self.env.company.id,
                template_params=_template_params(),
            )

    def test_mcp_envelope_contract(self):
        response = _mock_response(
            200, {"conversation_id": 11, "message_id": 22}
        )
        with patch.object(
            type(self.Service), "_post_outbound_message", return_value=response
        ):
            raw = self._send()
        assert_tool_contract("send_whatsapp_to_partner", raw)
        data = unwrap_tool_result(raw)
        for key in (
            "status",
            "partner_id",
            "company_id",
            "http_status",
            "conversation_id",
            "message_id",
            "retryable",
            "error",
        ):
            self.assertIn(key, data)
