from odoo import fields, models


class TommasiWhatsappLog(models.Model):
    _name = "tommasi.whatsapp.log"
    _description = "WhatsApp outbound send audit log"
    _order = "create_date desc, id desc"

    partner_id = fields.Many2one("res.partner", required=True, index=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    config_id = fields.Many2one("tommasi.whatsapp.config", ondelete="set null")
    template_name = fields.Char(index=True)
    template_language = fields.Char()
    idempotency_key = fields.Char(index=True)
    status = fields.Char(required=True, index=True)
    http_status = fields.Integer()
    conversation_id = fields.Integer()
    message_id = fields.Integer()
    retryable = fields.Boolean(default=False)
    error_message = fields.Text(
        help="Sanitized error text without secrets or template parameter values.",
    )
