from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    mobile = fields.Char(
        related="partner_id.mobile",
        string="Mobile",
        readonly=True,
    )
