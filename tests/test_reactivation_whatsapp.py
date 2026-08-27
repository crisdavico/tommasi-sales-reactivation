import hashlib
import hmac
import json
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
    upsert_whatsapp_config,
)
from odoo.addons.tommasi_sales_reactivation.tests.mcp_contract import (
    assert_mcp_envelope,
    assert_tool_contract,
)

_SERVICE_WHATSAPP = (
    "odoo.addons.tommasi_sales_reactivation.models.service_whatsapp"
)


def _template_params(**overrides):
    params = {
        "name": "order_confirmation",
        "category": "UTILITY",
        "language": "es",
        "processed_params": {"body": {"1": "121212"}},
    }
    params.update(overrides)
    return params


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


def _compact_body_bytes(payload):
    return json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _expected_outbound_signature(secret, key_id, timestamp, nonce, body_bytes):
    body_sha256_hex = hashlib.sha256(body_bytes).hexdigest()
    sign_string = "\n".join(
        (
            "POST",
            "/v1/outbound/messages",
            key_id,
            timestamp,
            nonce,
            body_sha256_hex,
        )
    )
    return hmac.new(
        secret.encode(),
        sign_string.encode(),
        hashlib.sha256,
    ).hexdigest()


class _FixedUUID:
    def __init__(self, hex_value):
        self.hex = hex_value


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

    def setUp(self):
        super().setUp()
        self.WhatsappConfig.search([("company_id", "=", self.company_b.id)]).unlink()

    def _config_vals(self, **overrides):
        values = {
            "name": "WhatsApp test",
            "company_id": self.company_b.id,
            "router_base_url": "https://router.test",
            "outbound_key_id": "out_test_config",
            "outbound_api_key": "test-outbound-api-key-000000000001",
            "outbound_hmac_secret": "test-outbound-hmac-secret-00000001",
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

    def test_outbound_credential_token_validation(self):
        with self.assertRaises(ValidationError):
            self.WhatsappConfig.create(
                self._config_vals(outbound_key_id="bad key!")
            )
        with self.assertRaises(ValidationError):
            self.WhatsappConfig.create(
                self._config_vals(outbound_api_key="too-short")
            )
        with self.assertRaises(ValidationError):
            self.WhatsappConfig.create(
                self._config_vals(outbound_hmac_secret="short")
            )

    def test_outbound_credentials_may_be_blank_on_new_fields(self):
        # Upgrade path: new HMAC fields stay optional until backfill.
        config = self.WhatsappConfig.create(
            self._config_vals(
                company_id=self.company_b.id,
                outbound_key_id=False,
                outbound_hmac_secret=False,
            )
        )
        self.assertFalse(config._has_complete_outbound_credentials())


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
        cls.whatsapp_config = upsert_whatsapp_config(
            cls.env,
            **{
                "name": "Main WhatsApp",
                "company_id": cls.env.company.id,
                "router_base_url": "https://router.test",
                "outbound_key_id": "out_test_main",
                "outbound_api_key": "test-outbound-api-key-000000000001",
                "outbound_hmac_secret": "test-outbound-hmac-secret-00000001",
                "chatwoot_account_id": 1,
                "chatwoot_inbox_id": 5,
            }
        )
        cls.whatsapp_config_b = cls.WhatsappConfig.create(
            {
                "name": "Company B WhatsApp",
                "company_id": cls.company_b.id,
                "router_base_url": "https://router-b.test",
                "outbound_key_id": "out_test_company_b",
                "outbound_api_key": "test-outbound-api-key-00000000000b",
                "outbound_hmac_secret": "test-outbound-hmac-secret-0000000b",
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

    def test_rejects_incomplete_outbound_credentials(self):
        self.whatsapp_config.write({"outbound_hmac_secret": False})
        with patch("%s.requests.post" % _SERVICE_WHATSAPP) as mocked_post:
            raw = self._send()
            mocked_post.assert_not_called()
        data = unwrap_tool_result(raw)
        self.assertEqual(data["status"], "rejected_no_config")
        self.assertFalse(data["retryable"])
        self.assertIn("incomplete", (data["error"] or "").lower())
        assert_mcp_envelope(raw)

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
        response = _mock_response(
            401, {"detail": "bad api_key=super-secret hmac nonce"}
        )
        with patch.object(
            type(self.Service), "_post_outbound_message", return_value=response
        ):
            data = unwrap_tool_result(self._send())
        self.assertEqual(data["http_status"], 401)
        self.assertEqual(data["status"], "error")
        self.assertFalse(data["retryable"])
        error = data["error"] or ""
        self.assertIn("authentication", error.lower())
        self.assertNotIn("super-secret", error)
        self.assertNotIn("api_key", error.lower())
        self.assertNotIn("hmac", error.lower())
        self.assertNotIn("nonce", error.lower())

    def test_maps_router_403_scope_mismatch(self):
        response = _mock_response(
            403, {"detail": "credential scope mismatch for account"}
        )
        with patch.object(
            type(self.Service), "_post_outbound_message", return_value=response
        ):
            raw = self._send()
        assert_mcp_envelope(raw)
        data = unwrap_tool_result(raw)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["http_status"], 403)
        self.assertFalse(data["retryable"])
        error = (data["error"] or "").lower()
        self.assertTrue(
            "account" in error or "inbox" in error,
            "403 error should mention account/inbox scope",
        )
        self.assertNotIn("credential scope mismatch", error)

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

    def test_signed_transport_posts_compact_unicode_body_and_headers(self):
        """Assert build-once bytes, data= (not json=), five auth headers, HMAC."""
        content = "Hola José — reactivación"
        template_params = _template_params(
            processed_params={"body": {"1": "Señoría"}}
        )
        expected_payload = {
            "account_id": 1,
            "inbox_id": 5,
            "phone": "+5491112345678",
            "template_params": template_params,
            "content": content,
            "idempotency_key": "opp-unicode-1",
        }
        body_bytes = _compact_body_bytes(expected_payload)
        self.assertIn("José".encode("utf-8"), body_bytes)
        self.assertIn("Señoría".encode("utf-8"), body_bytes)
        self.assertNotIn(b"\\u", body_bytes)

        fixed_ts = 1700000000
        fixed_nonce = "a" * 32
        expected_sig = _expected_outbound_signature(
            self.whatsapp_config.outbound_hmac_secret,
            self.whatsapp_config.outbound_key_id,
            str(fixed_ts),
            fixed_nonce,
            body_bytes,
        )
        response = _mock_response(
            200, {"conversation_id": 42, "message_id": 99}
        )
        with patch(
            "%s.requests.post" % _SERVICE_WHATSAPP, return_value=response
        ) as mocked_post, patch(
            "%s.time.time" % _SERVICE_WHATSAPP, return_value=fixed_ts
        ), patch(
            "%s.uuid.uuid4" % _SERVICE_WHATSAPP,
            return_value=_FixedUUID(fixed_nonce),
        ):
            raw = self._send(
                template_params=template_params,
                content=content,
                idempotency_key="opp-unicode-1",
            )

        mocked_post.assert_called_once()
        args, kwargs = mocked_post.call_args
        self.assertEqual(
            args[0], "https://router.test/v1/outbound/messages"
        )
        self.assertEqual(kwargs.get("data"), body_bytes)
        self.assertNotIn("json", kwargs)
        headers = kwargs["headers"]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(
            headers["X-Outbound-Key-Id"], self.whatsapp_config.outbound_key_id
        )
        self.assertEqual(
            headers["X-Outbound-Api-Key"], self.whatsapp_config.outbound_api_key
        )
        self.assertEqual(headers["X-Timestamp"], str(fixed_ts))
        self.assertEqual(headers["X-Nonce"], fixed_nonce)
        self.assertEqual(headers["X-Signature"], expected_sig)
        assert_tool_contract("send_whatsapp_to_partner", raw)
        data = unwrap_tool_result(raw)
        self.assertEqual(data["status"], "sent")
        self.assertEqual(data["conversation_id"], 42)
        self.assertEqual(data["message_id"], 99)

    def test_fresh_nonce_across_http_attempts_keeps_idempotency_key(self):
        """Business idempotency_key stays in body; HMAC nonce changes per attempt."""
        response = _mock_response(
            200, {"conversation_id": 1, "message_id": 2}
        )
        nonces = ["b" * 32, "c" * 32]
        timestamps = [1700000001, 1700000002]
        with patch(
            "%s.requests.post" % _SERVICE_WHATSAPP, return_value=response
        ) as mocked_post, patch(
            "%s.time.time" % _SERVICE_WHATSAPP, side_effect=timestamps
        ), patch(
            "%s.uuid.uuid4" % _SERVICE_WHATSAPP,
            side_effect=[_FixedUUID(n) for n in nonces],
        ):
            self._send(idempotency_key="stable-business-key")
            self._send(idempotency_key="stable-business-key")

        self.assertEqual(mocked_post.call_count, 2)
        bodies = []
        seen_nonces = []
        for call in mocked_post.call_args_list:
            _args, kwargs = call
            bodies.append(kwargs["data"])
            seen_nonces.append(kwargs["headers"]["X-Nonce"])
            payload = json.loads(kwargs["data"].decode("utf-8"))
            self.assertEqual(payload["idempotency_key"], "stable-business-key")
            self.assertEqual(payload["account_id"], 1)
            self.assertEqual(payload["inbox_id"], 5)
            self.assertEqual(payload["phone"], "+5491112345678")
            self.assertEqual(payload["template_params"], _template_params())
        self.assertEqual(seen_nonces, nonces)
        self.assertEqual(bodies[0], bodies[1])
        self.assertNotEqual(
            mocked_post.call_args_list[0][1]["headers"]["X-Signature"],
            mocked_post.call_args_list[1][1]["headers"]["X-Signature"],
        )
