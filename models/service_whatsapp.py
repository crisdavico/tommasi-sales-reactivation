import logging
import re

import requests

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.llm_tool.decorators import llm_tool

_logger = logging.getLogger(__name__)

_E164_PHONE_RE = re.compile(r"^\+[1-9]\d{1,14}$")
_TEMPLATE_PARAM_KEYS = frozenset({"name", "category", "language", "processed_params"})
_SECRET_SUBSTRINGS = ("api_key", "api-key", "authorization", "bearer", "token")


class TommasiReactivationServiceWhatsapp(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _sanitize_whatsapp_error(self, message):
        text = str(message or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        if any(marker in lowered for marker in _SECRET_SUBSTRINGS):
            return "Upstream error (details redacted)."
        return text[:500]

    def _normalize_partner_mobile(self, mobile):
        value = (mobile or "").strip()
        if not value:
            return None
        compact = re.sub(r"[\s\-()]", "", value)
        if _E164_PHONE_RE.match(compact):
            return compact
        return None

    def _validate_template_params(self, template_params):
        if not isinstance(template_params, dict):
            return None, "template_params must be a JSON object."
        missing = sorted(_TEMPLATE_PARAM_KEYS - template_params.keys())
        if missing:
            return None, "template_params missing keys: %s." % ", ".join(missing)
        if not isinstance(template_params.get("processed_params"), dict):
            return None, "template_params.processed_params must be a JSON object."
        for key in ("name", "category", "language"):
            value = template_params.get(key)
            if not value or not isinstance(value, str):
                return None, "template_params.%s must be a non-empty string." % key
        return template_params, None

    def _get_whatsapp_config(self, company_id):
        return (
            self.env["tommasi.whatsapp.config"]
            .sudo()
            .search(
                [
                    ("company_id", "=", company_id),
                    ("active", "=", True),
                ],
                limit=1,
            )
        )

    def _resolve_whatsapp_partner(self, partner_id, company_id):
        partner = self.env["res.partner"].sudo().browse(partner_id).exists()
        if not partner:
            return None, "rejected_partner_not_found", _("Partner not found.")
        if not partner.active:
            return None, "rejected_partner_inactive", _("Partner is inactive.")
        commercial = partner.commercial_partner_id
        if commercial.company_id and commercial.company_id.id != company_id:
            return (
                None,
                "rejected_company_mismatch",
                _("Partner company does not match the requested company."),
            )
        if not commercial.allow_whatsapp_communication:
            return (
                None,
                "rejected_no_consent",
                _("Partner has not consented to WhatsApp communication."),
            )
        mobile = self._normalize_partner_mobile(commercial.mobile)
        if not mobile:
            return (
                None,
                "rejected_invalid_mobile",
                _("Partner mobile must be a valid E.164 number."),
            )
        return commercial, None, None

    def _build_outbound_payload(
        self,
        config,
        phone,
        template_params,
        content=None,
        idempotency_key=None,
    ):
        payload = {
            "account_id": config.chatwoot_account_id,
            "inbox_id": config.chatwoot_inbox_id,
            "phone": phone,
            "template_params": template_params,
        }
        if content:
            payload["content"] = content
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return payload

    def _post_outbound_message(self, config, payload):
        url = "%s/v1/outbound/messages" % config.router_base_url.rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "X-Outbound-Api-Key": config.outbound_api_key,
        }
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=config.http_timeout_seconds,
        )
        return response

    def _parse_outbound_response_body(self, response):
        if not response.content:
            return {}
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def _map_router_http_result(self, http_status, body):
        conversation_id = body.get("conversation_id")
        message_id = body.get("message_id")
        detail = body.get("detail") or body.get("message") or body.get("error")
        if http_status == 200:
            if conversation_id is None or message_id is None:
                return {
                    "status": "error",
                    "http_status": http_status,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "retryable": False,
                    "error": self._sanitize_whatsapp_error(
                        detail or _("Router returned an incomplete success payload.")
                    ),
                }
            return {
                "status": "sent",
                "http_status": http_status,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "retryable": False,
                "error": None,
            }
        if http_status == 401:
            return {
                "status": "error",
                "http_status": http_status,
                "conversation_id": None,
                "message_id": None,
                "retryable": False,
                "error": self._sanitize_whatsapp_error(
                    detail or _("Router rejected the outbound API key.")
                ),
            }
        if http_status == 409:
            return {
                "status": "error",
                "http_status": http_status,
                "conversation_id": None,
                "message_id": None,
                "retryable": True,
                "error": self._sanitize_whatsapp_error(
                    detail
                    or _(
                        "Concurrent outbound request in progress; "
                        "retry with the same idempotency key."
                    )
                ),
            }
        if http_status == 422:
            return {
                "status": "error",
                "http_status": http_status,
                "conversation_id": None,
                "message_id": None,
                "retryable": False,
                "error": self._sanitize_whatsapp_error(
                    detail or _("Router rejected the outbound payload.")
                ),
            }
        if http_status in (502, 503):
            return {
                "status": "error",
                "http_status": http_status,
                "conversation_id": None,
                "message_id": None,
                "retryable": True,
                "error": self._sanitize_whatsapp_error(
                    detail or _("Router or Chatwoot is temporarily unavailable.")
                ),
            }
        return {
            "status": "error",
            "http_status": http_status,
            "conversation_id": None,
            "message_id": None,
            "retryable": False,
            "error": self._sanitize_whatsapp_error(
                detail or _("Unexpected router response.")
            ),
        }

    def _create_whatsapp_log(
        self,
        partner,
        company_id,
        config,
        template_params,
        idempotency_key,
        result,
    ):
        self.env["tommasi.whatsapp.log"].sudo().create(
            {
                "partner_id": partner.id,
                "company_id": company_id,
                "config_id": config.id if config else False,
                "template_name": template_params.get("name") if template_params else False,
                "template_language": (
                    template_params.get("language") if template_params else False
                ),
                "idempotency_key": idempotency_key or False,
                "status": result["status"],
                "http_status": result.get("http_status") or 0,
                "conversation_id": result.get("conversation_id") or 0,
                "message_id": result.get("message_id") or 0,
                "retryable": bool(result.get("retryable")),
                "error_message": result.get("error") or False,
            }
        )

    def _whatsapp_tool_result(self, partner_id, company_id, result):
        payload = {
            "status": result["status"],
            "partner_id": partner_id,
            "company_id": company_id,
            "http_status": result.get("http_status"),
            "conversation_id": result.get("conversation_id"),
            "message_id": result.get("message_id"),
            "retryable": bool(result.get("retryable")),
            "error": result.get("error"),
        }
        return self._wrap_mcp_response(payload)

    def _send_whatsapp_to_partner_impl(
        self,
        partner_id,
        company_id,
        template_params,
        idempotency_key=None,
        content=None,
    ):
        template_params, template_error = self._validate_template_params(
            template_params
        )
        if template_error:
            result = {
                "status": "rejected_invalid_template",
                "http_status": None,
                "conversation_id": None,
                "message_id": None,
                "retryable": False,
                "error": template_error,
            }
            return self._whatsapp_tool_result(partner_id, company_id, result)

        partner, reject_status, reject_message = self._resolve_whatsapp_partner(
            partner_id, company_id
        )
        if reject_status:
            result = {
                "status": reject_status,
                "http_status": None,
                "conversation_id": None,
                "message_id": None,
                "retryable": False,
                "error": reject_message,
            }
            if partner:
                self._create_whatsapp_log(
                    partner,
                    company_id,
                    False,
                    template_params,
                    idempotency_key,
                    result,
                )
            return self._whatsapp_tool_result(partner_id, company_id, result)

        config = self._get_whatsapp_config(company_id)
        if not config:
            result = {
                "status": "rejected_no_config",
                "http_status": None,
                "conversation_id": None,
                "message_id": None,
                "retryable": False,
                "error": _("No active WhatsApp configuration for this company."),
            }
            self._create_whatsapp_log(
                partner,
                company_id,
                False,
                template_params,
                idempotency_key,
                result,
            )
            return self._whatsapp_tool_result(partner_id, company_id, result)

        phone = self._normalize_partner_mobile(partner.mobile)
        payload = self._build_outbound_payload(
            config,
            phone,
            template_params,
            content=content,
            idempotency_key=idempotency_key,
        )
        try:
            response = self._post_outbound_message(config, payload)
        except requests.Timeout:
            result = {
                "status": "error",
                "http_status": None,
                "conversation_id": None,
                "message_id": None,
                "retryable": True,
                "error": _("Router request timed out."),
            }
            self._create_whatsapp_log(
                partner,
                company_id,
                config,
                template_params,
                idempotency_key,
                result,
            )
            return self._whatsapp_tool_result(partner_id, company_id, result)
        except requests.RequestException as exc:
            _logger.warning(
                "WhatsApp outbound transport error partner=%s company=%s: %s",
                partner_id,
                company_id,
                exc,
            )
            result = {
                "status": "error",
                "http_status": None,
                "conversation_id": None,
                "message_id": None,
                "retryable": True,
                "error": _("Could not reach the Chatwoot router."),
            }
            self._create_whatsapp_log(
                partner,
                company_id,
                config,
                template_params,
                idempotency_key,
                result,
            )
            return self._whatsapp_tool_result(partner_id, company_id, result)

        body = self._parse_outbound_response_body(response)
        result = self._map_router_http_result(response.status_code, body)
        self._create_whatsapp_log(
            partner,
            company_id,
            config,
            template_params,
            idempotency_key,
            result,
        )
        return self._whatsapp_tool_result(partner_id, company_id, result)

    @llm_tool(destructive_hint=True)
    def send_whatsapp_to_partner(
        self,
        partner_id: int,
        company_id: int,
        template_params: dict,
        idempotency_key: str = None,
        content: str = None,
    ) -> dict:
        """Send an approved WhatsApp template to a partner via the Chatwoot router.

        Resolves the partner mobile from Odoo, enforces consent and company
        checks, and forwards a structured template payload to
        ``POST /v1/outbound/messages``. Callers must supply ``company_id`` and
        ``template_params`` (``name``, ``category``, ``language``,
        ``processed_params``); router URL, API key, and Chatwoot routing IDs
        come from the standalone per-company WhatsApp configuration.

        Args:
            partner_id: ``res.partner`` id for the recipient.
            company_id: ``res.company`` id used to select WhatsApp config.
            template_params: Router template payload object.
            idempotency_key: Optional deduplication key for router replays.
            content: Optional Chatwoot thread text override.

        Returns:
            MCP envelope with ``data`` containing ``status``, ``partner_id``,
            ``company_id``, ``http_status``, ``conversation_id``,
            ``message_id``, ``retryable``, and ``error``.
        """
        if not partner_id or not company_id:
            raise UserError(_("partner_id and company_id are required."))
        return self._send_whatsapp_to_partner_impl(
            partner_id=partner_id,
            company_id=company_id,
            template_params=template_params,
            idempotency_key=idempotency_key,
            content=content,
        )
