import hashlib
import json
import logging
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.tommasi_sales_reactivation.models.tommasi_reactivation_service import (
    PRODUCT_HISTORY_FLOOR_WINDOW_DAYS,
)

_logger = logging.getLogger(__name__)

_CONFIG_HASH_FIELDS = (
    "inactivity_days_primary",
    "inactivity_days_secondary",
    "bootstrap_min_invoices",
    "bootstrap_invoice_window_days",
)


class TommasiReactivationFactsSnapshot(models.Model):
    _name = "tommasi.reactivation.facts.snapshot"
    _description = "Pre-computed sales reactivation facts snapshot"
    _order = "computed_at desc, partner_id"

    partner_id = fields.Many2one(
        "res.partner",
        string="Commercial partner",
        required=True,
        index=True,
        help="Commercial partner whose invoice facts are materialized.",
    )
    seller_id = fields.Many2one(
        "res.users",
        string="Salesperson",
        required=True,
        index=True,
        domain="[('share', '=', False)]",
        help="Salesperson who owns the commercial partner.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        help="Company scope for this snapshot row.",
    )
    last_purchase_date = fields.Date(
        help="Date of the most recent posted customer invoice.",
    )
    days_inactive = fields.Integer(
        compute="_compute_days_inactive",
        help=(
            "Days since the last purchase, computed at read time from "
            "last_purchase_date and never stored."
        ),
    )
    revenue_prior = fields.Float(
        help="Invoice revenue in the prior half of the detection window.",
    )
    revenue_recent = fields.Float(
        help="Invoice revenue in the recent half of the detection window.",
    )
    revenue_change_pct = fields.Float(
        string="Revenue change (%)",
        help="Percentage change of revenue between prior and recent halves.",
    )
    qty_change_pct = fields.Float(
        help="Percentage change of quantity between prior and recent halves.",
    )
    sales_history_json = fields.Text(
        help="Serialized sales history payload (json.dumps).",
    )
    product_history_json = fields.Text(
        help="Serialized product history payload (json.dumps).",
    )
    computed_at = fields.Datetime(
        help="UTC timestamp when this snapshot row was last materialized.",
    )
    date_from = fields.Date(
        help="Start date of the detection window used for this snapshot.",
    )
    date_to = fields.Date(
        help="End date of the detection window used for this snapshot.",
    )
    config_hash = fields.Char(
        help=(
            "Hash of bootstrap and inactivity config fields used to detect "
            "configuration drift."
        ),
    )

    _sql_constraints = [
        (
            "unique_partner_per_company",
            "unique(partner_id, company_id)",
            "Only one facts snapshot is allowed per commercial partner and company.",
        ),
    ]

    @api.depends("last_purchase_date")
    def _compute_days_inactive(self):
        today = fields.Date.context_today(self)
        for record in self:
            if record.last_purchase_date:
                record.days_inactive = (today - record.last_purchase_date).days
            else:
                record.days_inactive = False

    @staticmethod
    def _compute_config_hash(config):
        """Return an MD5 hex digest of the snapshot-invalidating config fields."""
        if hasattr(config, "_name"):
            values = {field: config[field] for field in _CONFIG_HASH_FIELDS}
        else:
            values = {field: config.get(field) for field in _CONFIG_HASH_FIELDS}
        payload = json.dumps(values, sort_keys=True)
        return hashlib.md5(payload.encode()).hexdigest()

    @api.model
    def compute_config_hash(self, config):
        """Service-layer wrapper for :meth:`_compute_config_hash`."""
        return self._compute_config_hash(config)

    @api.model
    def cron_refresh_facts_snapshots(self):
        """Materialize bootstrap-qualified partner facts for enabled sellers."""
        service = self.env["tommasi.reactivation.service"]
        config = service._get_config()
        window = service._resolve_date_range(config=config)
        date_from = service._parse_date(window["date_from"])
        date_to = service._parse_date(window["date_to"])
        midpoint = fields.Date.to_string(date_from + (date_to - date_from) / 2)
        config_hash = self.compute_config_hash(config)
        company_id = config.company_id.id
        computed_at = fields.Datetime.now()
        enabled_sellers = service._filter_enabled_sellers()

        _logger.info(
            "Starting facts snapshot refresh for %s enabled sellers",
            len(enabled_sellers),
        )

        upserted = 0
        deleted = 0
        for seller in enabled_sellers:
            seller_id = seller.user_id.id
            partner_map = service._candidate_seller_partners(seller, config)
            commercial_ids = list(partner_map.keys())

            last_purchase = service._candidate_last_purchase_batch(
                commercial_ids, seller_id
            )
            revenue = service._candidate_revenue_batch(
                commercial_ids,
                seller_id,
                window["date_from"],
                midpoint,
                window["date_to"],
            )
            qty = service._candidate_qty_batch(
                commercial_ids,
                seller_id,
                window["date_from"],
                midpoint,
                window["date_to"],
            )

            seller_config = service._get_config()
            today = fields.Date.context_today(service)
            window_date_from = service._parse_date(window["date_from"])
            floor_date = today - timedelta(days=PRODUCT_HISTORY_FLOOR_WINDOW_DAYS)
            batch_date_from = fields.Date.to_string(min(window_date_from, floor_date))
            facts_by_commercial = service._get_invoice_facts_batch(
                commercial_ids,
                date_from=batch_date_from,
                date_to=window["date_to"],
            )

            for commercial_id, info in partner_map.items():
                last_date = last_purchase.get(commercial_id)
                rev = revenue.get(commercial_id, {"prior": 0.0, "recent": 0.0})
                qty_halves = qty.get(commercial_id, {"prior": 0.0, "recent": 0.0})
                metrics = service._half_window_metrics(last_date, rev, qty_halves, None)
                revenue_prior = metrics["revenue_prior"]
                revenue_recent = metrics["revenue_recent"]
                revenue_change_pct = metrics["revenue_change_pct"]
                qty_change_pct = metrics["qty_change_pct"]

                partner_facts = facts_by_commercial.get(
                    commercial_id, {"moves": [], "lines": []}
                )
                sales_history = service._get_sales_history(
                    info["customer_id"],
                    date_range=window,
                    config=seller_config,
                    facts=partner_facts,
                )
                product_history = service._get_product_history(
                    info["customer_id"],
                    date_range=window,
                    config=seller_config,
                    facts=partner_facts,
                )

                values = {
                    "partner_id": commercial_id,
                    "seller_id": seller_id,
                    "company_id": company_id,
                    "last_purchase_date": last_date,
                    "revenue_prior": revenue_prior,
                    "revenue_recent": revenue_recent,
                    "revenue_change_pct": revenue_change_pct,
                    "qty_change_pct": qty_change_pct,
                    "sales_history_json": json.dumps(sales_history),
                    "product_history_json": json.dumps(product_history),
                    "computed_at": computed_at,
                    "date_from": window["date_from"],
                    "date_to": window["date_to"],
                    "config_hash": config_hash,
                }
                existing = self.search(
                    [
                        ("partner_id", "=", commercial_id),
                        ("company_id", "=", company_id),
                    ],
                    limit=1,
                )
                if existing:
                    existing.write(values)
                else:
                    self.create(values)
                upserted += 1

            stale = self.search(
                [
                    ("seller_id", "=", seller_id),
                    ("company_id", "=", company_id),
                    ("partner_id", "not in", commercial_ids or [0]),
                ]
            )
            if stale:
                deleted += len(stale)
                stale.unlink()

        _logger.info(
            "Facts snapshot refresh completed: %s upserted, %s deleted",
            upserted,
            deleted,
        )
