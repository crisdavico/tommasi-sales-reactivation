import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)


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
    outbound_api_key = fields.Char(
        string="Outbound API key",
        required=True,
        help="Value sent as X-Outbound-Api-Key to the router.",
    )
    chatwoot_account_id = fields.Integer(
        string="Chatwoot account ID",
        required=True,
        tracking=True,
    )
    chatwoot_inbox_id = fields.Integer(
        string="Chatwoot inbox ID",
        required=True,
        tracking=True,
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

    @api.constrains("router_base_url")
    def _check_router_base_url(self):
        for record in self:
            url = (record.router_base_url or "").strip()
            if not url or not _URL_PATTERN.match(url):
                raise ValidationError(
                    _("Router base URL must start with http:// or https://.")
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
