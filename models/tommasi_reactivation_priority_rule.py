from odoo import fields, models


class TommasiReactivationPriorityRule(models.Model):
    _name = "tommasi.reactivation.priority.rule"
    _description = "Reactivation priority threshold rule"
    _order = "cap_order, label"

    config_id = fields.Many2one(
        "tommasi.reactivation.config",
        string="Configuration",
        required=True,
        ondelete="cascade",
        help="Reactivation settings this priority rule belongs to.",
    )
    label = fields.Selection(
        [
            ("high", "High"),
            ("medium", "Medium"),
            ("low", "Low"),
        ],
        required=True,
        help=(
            "Priority label assigned when a customer meets the thresholds "
            "in this row."
        ),
    )
    min_confidence = fields.Float(
        help=(
            "Minimum confidence score (from 0 to 1) required for this "
            "priority level."
        ),
    )
    min_inactivity_days = fields.Integer(
        help=(
            "Minimum days since the customer's last purchase to qualify "
            "for this priority."
        ),
    )
    min_decline_pct = fields.Float(
        help=(
            "Minimum revenue decline percentage, compared to the baseline "
            "period, to qualify for this priority."
        ),
    )
    min_customer_value = fields.Monetary(
        currency_field="currency_id",
        help=("Minimum customer lifetime value required for this priority " "level."),
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="config_id.currency_id",
        help="Currency used for the minimum customer value.",
    )
    multi_trigger_weight = fields.Float(
        default=1.0,
        help=(
            "Extra weight when more than one reactivation trigger applies "
            "to the same customer."
        ),
    )
    cap_order = fields.Integer(
        default=10,
        help=(
            "Ranking used when a seller hits the opportunity cap. Lower "
            "numbers are kept first; higher numbers are dropped first."
        ),
    )

    _sql_constraints = [
        (
            "unique_label_per_config",
            "unique(config_id, label)",
            "Only one priority rule per label is allowed per configuration.",
        ),
    ]
