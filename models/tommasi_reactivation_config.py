from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class TommasiReactivationConfig(models.Model):
    _name = "tommasi.reactivation.config"
    _description = "Tommasi sales reactivation configuration"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        default="Sales reactivation",
        required=True,
        tracking=True,
        help="Display name for this reactivation settings record.",
    )
    active = fields.Boolean(
        default=True,
        tracking=True,
        help=("When unchecked, the reactivation agent stops using these settings."),
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        help="Company these reactivation settings apply to.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
        help="Currency used for customer value thresholds in priority rules.",
    )

    cooldown_days = fields.Integer(
        default=30,
        tracking=True,
        help=(
            "Minimum days to wait before the agent can create another "
            "reactivation opportunity for the same customer."
        ),
    )
    opportunity_cap_per_seller = fields.Integer(
        default=20,
        tracking=True,
        help=(
            "Maximum open reactivation opportunities each seller can have "
            "at the same time. Extra candidates are skipped."
        ),
    )
    low_stock_threshold = fields.Integer(
        default=5,
        tracking=True,
        help=(
            "Minimum stock required to suggest a product. Products with "
            "available quantity at or below this number are excluded from "
            "recommendations and rejected at opportunity creation."
        ),
    )
    stock_location_ids = fields.Many2many(
        "stock.location",
        "tommasi_reactivation_config_stock_location_rel",
        "config_id",
        "location_id",
        string="Stock source locations",
        tracking=True,
        domain="[('usage', 'in', ('internal', 'view')), "
        "('company_id', 'in', [company_id, False])]",
        help=(
            "Internal locations used to compute available stock for "
            "recommendations and opportunity validation. "
            "Leave empty to use all internal locations for this company."
        ),
    )
    inactivity_days_primary = fields.Integer(
        default=30,
        tracking=True,
        help=(
            "Days without a purchase after which a customer may be flagged "
            "for the standard inactivity trigger."
        ),
    )
    inactivity_days_secondary = fields.Integer(
        default=45,
        tracking=True,
        help=(
            "Days without a purchase for the stronger inactivity trigger. "
            "Must be equal to or greater than the primary inactivity days."
        ),
    )
    suggested_products_max = fields.Integer(
        default=5,
        tracking=True,
        help=(
            "Maximum number of products suggested in each reactivation "
            "opportunity."
        ),
    )
    tables_row_limit = fields.Integer(
        string="Tables Row Limit",
        default=10,
        tracking=True,
        help=(
            "Maximum number of data rows shown in evidence, star-products, "
            "and commercial-rationale HTML tables inside a reactivation CRM "
            "opportunity description. Suggested products keep using "
            "Maximum suggested products instead."
        ),
    )
    discount_pct = fields.Float(
        default=55.0,
        tracking=True,
        help=(
            "Deprecated — no longer used for pricing. Net prices come from the "
            "customer pricelist; pricelist line discounts are shown as "
            "commercial reference only."
        ),
    )
    reminder_stale_days = fields.Integer(
        default=7,
        tracking=True,
        help=(
            "Days an opportunity can stay in Pending review before the "
            "agent sends a reminder to the seller."
        ),
    )
    pending_review_archive_days = fields.Integer(
        string="Pending review archive days",
        default=15,
        tracking=True,
        help=(
            "Days an opportunity can stay in Pending review before the "
            "daily cron archives it automatically."
        ),
    )
    min_confidence_threshold = fields.Float(
        default=0.50,
        tracking=True,
        help=(
            "Minimum confidence score (from 0 to 1). Customers below this "
            "score are not turned into opportunities."
        ),
    )
    bootstrap_min_invoices = fields.Integer(
        default=3,
        tracking=True,
        help=(
            "Minimum number of posted customer invoices required in the "
            "bootstrap invoice window for a partner to enter the detection "
            "cycle. Set to 0 to disable this filter."
        ),
    )
    bootstrap_invoice_window_days = fields.Integer(
        default=180,
        tracking=True,
        help=("Lookback window in days for the bootstrap minimum-invoice filter."),
    )

    seller_user_ids = fields.Many2many(
        "res.users",
        string="Enabled sellers",
        compute="_compute_seller_user_ids",
        inverse="_inverse_seller_user_ids",
        domain="[('share', '=', False)]",
        help=(
            "Salespeople included in the reactivation agent. Use Add a line to "
            "select one or more salespeople at once."
        ),
    )
    seller_ids = fields.One2many(
        "tommasi.reactivation.seller",
        "config_id",
        string="Seller details",
        help=(
            "Per-seller settings linked to the enabled salespeople above."
        ),
    )
    priority_rule_ids = fields.One2many(
        "tommasi.reactivation.priority.rule",
        "config_id",
        string="Priority rules",
        help=(
            "Thresholds the agent uses to assign High, Medium, or Low "
            "priority to each opportunity."
        ),
    )

    _sql_constraints = [
        (
            "unique_company_config",
            "unique(company_id)",
            "Only one sales reactivation configuration is allowed per company.",
        ),
    ]

    @api.depends("seller_ids.user_id")
    def _compute_seller_user_ids(self):
        for config in self:
            config.seller_user_ids = config.seller_ids.mapped("user_id")

    def _inverse_seller_user_ids(self):
        Seller = self.env["tommasi.reactivation.seller"]
        for config in self:
            current_users = config.seller_ids.mapped("user_id")
            target_users = config.seller_user_ids
            config.seller_ids.filtered(
                lambda seller: seller.user_id not in target_users
            ).unlink()
            for user in target_users - current_users:
                Seller.create({"config_id": config.id, "user_id": user.id})

    @api.constrains(
        "cooldown_days",
        "opportunity_cap_per_seller",
        "low_stock_threshold",
        "inactivity_days_primary",
        "inactivity_days_secondary",
        "suggested_products_max",
        "tables_row_limit",
        "reminder_stale_days",
        "pending_review_archive_days",
        "discount_pct",
        "min_confidence_threshold",
        "bootstrap_min_invoices",
        "bootstrap_invoice_window_days",
    )
    def _check_operational_parameters(self):
        for record in self:
            if record.cooldown_days < 0:
                raise ValidationError(_("Cooldown days cannot be negative."))
            if record.opportunity_cap_per_seller < 1:
                raise ValidationError(
                    _("Opportunity cap per seller must be at least 1.")
                )
            if record.low_stock_threshold < 0:
                raise ValidationError(_("Low stock threshold cannot be negative."))
            if record.inactivity_days_primary < 0:
                raise ValidationError(_("Primary inactivity days cannot be negative."))
            if record.inactivity_days_secondary < record.inactivity_days_primary:
                raise ValidationError(
                    _(
                        "Secondary inactivity days must be greater than or equal "
                        "to primary inactivity days."
                    )
                )
            if record.suggested_products_max < 1:
                raise ValidationError(
                    _("Maximum suggested products must be at least 1.")
                )
            if record.tables_row_limit < 1:
                raise ValidationError(
                    _("Tables row limit must be at least 1.")
                )
            if record.reminder_stale_days < 0:
                raise ValidationError(_("Reminder stale days cannot be negative."))
            if record.pending_review_archive_days < 1:
                raise ValidationError(
                    _("Pending review archive days must be at least 1.")
                )
            if not 0.0 <= record.discount_pct <= 100.0:
                raise ValidationError(
                    _("Discount percentage must be between 0 and 100.")
                )
            if not 0.0 <= record.min_confidence_threshold <= 1.0:
                raise ValidationError(
                    _("Minimum confidence threshold must be between 0 and 1.")
                )
            if record.bootstrap_min_invoices < 0:
                raise ValidationError(
                    _("Bootstrap minimum invoices cannot be negative.")
                )
            if record.bootstrap_invoice_window_days < 1:
                raise ValidationError(
                    _("Bootstrap invoice window days must be at least 1.")
                )

    @api.constrains("stock_location_ids", "company_id")
    def _check_stock_location_ids(self):
        for record in self:
            for location in record.stock_location_ids:
                if location.usage not in ("internal", "view"):
                    raise ValidationError(
                        _(
                            "Stock source locations must be internal or view "
                            "locations."
                        )
                    )
                if location.company_id and location.company_id != record.company_id:
                    raise ValidationError(
                        _(
                            "Stock source locations must belong to the same "
                            "company as the configuration."
                        )
                    )

    def get_stock_location_ids(self):
        """Return internal location ids whose quants count toward available stock."""
        self.ensure_one()
        return self._resolve_stock_location_ids()

    def _resolve_stock_location_ids(self):
        """Return internal location ids whose quants count toward available stock."""
        self.ensure_one()
        Location = self.env["stock.location"].sudo()
        if not self.stock_location_ids:
            return Location.search(
                [
                    ("usage", "=", "internal"),
                    ("company_id", "in", [self.company_id.id, False]),
                ]
            ).ids
        return Location.search(
            [
                ("id", "child_of", self.stock_location_ids.ids),
                ("usage", "=", "internal"),
                ("company_id", "in", [self.company_id.id, False]),
            ]
        ).ids

    def _resolve_company_id(self, vals):
        company_id = vals.get("company_id")
        if company_id:
            return company_id
        return self.env.company.id

    @api.model_create_multi
    def create(self, vals_list):
        """One row per company. Re-load of data XML must not fail if the row exists."""
        if len(vals_list) > 1:
            company_ids = {self._resolve_company_id(vals) for vals in vals_list}
            if len(company_ids) > 1:
                raise UserError(
                    _(
                        "Only one sales reactivation configuration record "
                        "is allowed per company."
                    )
                )
        company_id = self._resolve_company_id(vals_list[0] if vals_list else {})
        existing = self.search([("company_id", "=", company_id)], limit=1)
        if existing:
            if len(vals_list) > 1:
                raise UserError(
                    _(
                        "Only one sales reactivation configuration record "
                        "is allowed per company."
                    )
                )
            if vals_list and vals_list[0]:
                existing.write(vals_list[0])
            return existing
        return super().create(vals_list)

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("You cannot delete the sales reactivation configuration."))

    @api.model
    def get_singleton(self, company=None):
        """Return the configuration singleton for a company, creating defaults if missing."""
        company = company or self.env.company
        configs = self.search([("company_id", "=", company.id)])
        if len(configs) > 1:
            raise UserError(
                _("Multiple sales reactivation configurations found for company %s.")
                % company.name
            )
        if not configs:
            configs = self.create(
                {
                    "name": "Sales reactivation",
                    "company_id": company.id,
                }
            )
        return configs

    @api.model
    def get_primary_config(self):
        """Return the config whose operational parameters drive unified MCP tools."""
        main_company = self.env.ref("base.main_company", raise_if_not_found=False)
        if main_company:
            config = self.sudo().search([("company_id", "=", main_company.id)], limit=1)
            if config:
                return config
        config = self.sudo().search([], order="id asc", limit=1)
        if config:
            return config
        return self.get_singleton()

    @api.model
    def get_all_configs(self):
        """Return every company configuration for unified multicompany tool access."""
        return self.sudo().search([])
