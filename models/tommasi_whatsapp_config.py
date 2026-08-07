import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
_OUTBOUND_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_OUTBOUND_KEY_ID_MIN_LEN = 1
_OUTBOUND_KEY_ID_MAX_LEN = 64
_OUTBOUND_SECRET_MIN_LEN = 32
_OUTBOUND_SECRET_MAX_LEN = 256
_MANAGER_GROUP = "llm.group_llm_manager"


class TommasiWhatsappConfig(models.Model):
    _name = "tommasi.whatsapp.config"
    _description = "Per-company WhatsApp outbound configuration"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        default="WhatsApp outbound",
        required=True,
        tracking=True,
    )
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    router_base_url = fields.Char(
        string="Router base URL",
        required=True,
        tracking=True,
        help="Base URL of the Chatwoot router (no trailing path).",
    )
    outbound_key_id = fields.Char(
        string="Outbound key ID",
        required=False,
        groups=_MANAGER_GROUP,
        help=(
            "Public credential selector (X-Outbound-Key-Id). Copy the one-time "
            "key_id from chatwoot-router-api manage_outbound_credentials.py "
            "create (1–64 url-safe chars). Visible only to LLM Managers."
        ),
    )
    outbound_api_key = fields.Char(
        string="Outbound API key",
        required=True,
        groups=_MANAGER_GROUP,
        help=(
            "API key sent as X-Outbound-Api-Key. Copy the one-time value from "
            "manage_outbound_credentials.py create (32–256 url-safe chars). "
            "Never log or share outside managers."
        ),
    )
    outbound_hmac_secret = fields.Char(
        string="Outbound HMAC secret",
        required=False,
        groups=_MANAGER_GROUP,
        help=(
            "Shared secret for HMAC-SHA256 signing (X-Signature). Copy the "
            "one-time hmac secret from manage_outbound_credentials.py create "
            "(32–256 url-safe chars). Sends fail if any credential field is blank."
        ),
    )
    chatwoot_account_id = fields.Integer(
        string="Chatwoot account ID",
        required=True,
        tracking=True,
        help=(
            "Must match the router credential --account-id scope used when "
            "provisioning. Mismatch returns a non-retryable HTTP 403."
        ),
    )
    chatwoot_inbox_id = fields.Integer(
        string="Chatwoot inbox ID",
        required=True,
        tracking=True,
        help=(
            "Must match the router credential --inbox-id scope used when "
            "provisioning. Mismatch returns a non-retryable HTTP 403."
        ),
    )
    http_timeout_seconds = fields.Integer(
        string="HTTP timeout (seconds)",
        default=30,
        required=True,
        tracking=True,
    )

    _sql_constraints = [
        (
            "company_uniq",
            "unique(company_id)",
            "Only one WhatsApp configuration is allowed per company.",
        ),
    ]

    def _has_complete_outbound_credentials(self):
        """Return True when key id, API key, and HMAC secret are all non-blank."""
        self.ensure_one()
        return bool(
            (self.outbound_key_id or "").strip()
            and (self.outbound_api_key or "").strip()
            and (self.outbound_hmac_secret or "").strip()
        )

    def _check_outbound_token(self, value, field_label, min_len, max_len):
        if value in (None, False, ""):
            return
        if not isinstance(value, str):
            raise ValidationError(
                _("%(field)s must be a string.") % {"field": field_label}
            )
        if not (min_len <= len(value) <= max_len) or not _OUTBOUND_TOKEN_RE.match(
            value
        ):
            raise ValidationError(
                _(
                    "%(field)s must be %(min)s–%(max)s characters using only "
                    "A–Z, a–z, 0–9, and . _ ~ -."
                )
                % {
                    "field": field_label,
                    "min": min_len,
                    "max": max_len,
                }
            )

    @api.constrains("router_base_url")
    def _check_router_base_url(self):
        for record in self:
            url = (record.router_base_url or "").strip()
            if not url or not _URL_PATTERN.match(url):
                raise ValidationError(
                    _("Router base URL must start with http:// or https://.")
                )

    @api.constrains("outbound_key_id", "outbound_api_key", "outbound_hmac_secret")
    def _check_outbound_credentials(self):
        for record in self:
            record._check_outbound_token(
                record.outbound_key_id,
                _("Outbound key ID"),
                _OUTBOUND_KEY_ID_MIN_LEN,
                _OUTBOUND_KEY_ID_MAX_LEN,
            )
            record._check_outbound_token(
                record.outbound_api_key,
                _("Outbound API key"),
                _OUTBOUND_SECRET_MIN_LEN,
                _OUTBOUND_SECRET_MAX_LEN,
            )
            record._check_outbound_token(
                record.outbound_hmac_secret,
                _("Outbound HMAC secret"),
                _OUTBOUND_SECRET_MIN_LEN,
                _OUTBOUND_SECRET_MAX_LEN,
            )

    @api.constrains("chatwoot_account_id", "chatwoot_inbox_id")
    def _check_positive_chatwoot_ids(self):
        for record in self:
            if record.chatwoot_account_id <= 0:
                raise ValidationError(_("Chatwoot account ID must be positive."))
            if record.chatwoot_inbox_id <= 0:
                raise ValidationError(_("Chatwoot inbox ID must be positive."))

    @api.constrains("http_timeout_seconds")
    def _check_http_timeout_seconds(self):
        for record in self:
            if record.http_timeout_seconds < 5 or record.http_timeout_seconds > 120:
                raise ValidationError(
                    _("HTTP timeout must be between 5 and 120 seconds.")
                )
