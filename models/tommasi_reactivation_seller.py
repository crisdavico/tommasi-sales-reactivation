from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class TommasiReactivationSeller(models.Model):
    _name = "tommasi.reactivation.seller"
    _description = "Enabled seller for sales reactivation"
    _order = "user_id"

    config_id = fields.Many2one(
        "tommasi.reactivation.config",
        string="Configuration",
        required=True,
        ondelete="cascade",
        help="Reactivation settings this seller line belongs to.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Salesperson",
        required=True,
        domain="[('share', '=', False)]",
        help=(
            "Salesperson included in the reactivation agent. Their "
            "customers are evaluated in each detection cycle."
        ),
    )
    notes = fields.Text(
        help="Optional internal notes about this seller's participation.",
    )
    partner_id = fields.Many2one(
        "res.partner",
        related="user_id.partner_id",
        string="Partner",
        help="Contact record linked to the salesperson.",
    )
    mobile = fields.Char(
        related="partner_id.mobile",
        string="Mobile",
        readonly=True,
        help="Mobile phone from the salesperson's contact record.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_duplicate_user(vals.get("config_id"), vals.get("user_id"))
        return super().create(vals_list)

    def write(self, vals):
        if "user_id" in vals or "config_id" in vals:
            for seller in self:
                config_id = vals.get("config_id", seller.config_id.id)
                user_id = vals.get("user_id", seller.user_id.id)
                self._check_duplicate_user(config_id, user_id, exclude_id=seller.id)
        return super().write(vals)

    def _check_duplicate_user(self, config_id, user_id, exclude_id=None):
        if not config_id or not user_id:
            return
        domain = [
            ("config_id", "=", config_id),
            ("user_id", "=", user_id),
        ]
        if exclude_id:
            domain.append(("id", "!=", exclude_id))
        if self.search_count(domain):
            raise ValidationError(
                _("Each salesperson can only appear once in the configuration.")
            )

    @api.constrains("config_id", "user_id")
    def _check_unique_user_per_config(self):
        for seller in self:
            self._check_duplicate_user(
                seller.config_id.id,
                seller.user_id.id,
                exclude_id=seller.id,
            )

    _sql_constraints = [
        (
            "unique_user_per_config",
            "unique(config_id, user_id)",
            "Each salesperson can only appear once in the configuration.",
        ),
    ]
