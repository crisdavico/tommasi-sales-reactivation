import json
from datetime import timedelta

from odoo import fields, models

SNAPSHOT_MAX_AGE_HOURS = 24


class TommasiReactivationServiceSnapshot(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _get_fresh_snapshots(self, partner_ids, config, date_range, seller_id=None):
        """Return fresh snapshot rows keyed by commercial partner id.

        A row is usable only when ``computed_at`` is younger than 24 hours,
        ``config_hash`` matches the current config, and the stored
        ``date_from`` / ``date_to`` match the resolved detection window.
        """
        if not partner_ids:
            return {}
        Snapshot = self.env["tommasi.reactivation.facts.snapshot"]
        expected_hash = Snapshot.compute_config_hash(config)
        resolved = self._resolve_date_range(date_range, config)
        cutoff = fields.Datetime.now() - timedelta(hours=SNAPSHOT_MAX_AGE_HOURS)
        domain = [
            ("partner_id", "in", list(partner_ids)),
            ("company_id", "=", config.company_id.id),
            ("computed_at", ">=", cutoff),
            ("config_hash", "=", expected_hash),
        ]
        if seller_id:
            domain.append(("seller_id", "=", seller_id))
        result = {}
        for snapshot in Snapshot.search(domain):
            if not self._snapshot_date_range_compatible(snapshot, resolved):
                continue
            result[snapshot.partner_id.id] = snapshot
        return result

    @staticmethod
    def _snapshot_date_range_compatible(snapshot, resolved_window):
        """True when the snapshot was materialized for the requested window."""
        if not snapshot.date_from or not snapshot.date_to:
            return False
        snap_from = fields.Date.to_string(snapshot.date_from)
        snap_to = fields.Date.to_string(snapshot.date_to)
        return (
            snap_from == resolved_window["date_from"]
            and snap_to == resolved_window["date_to"]
        )

    def _snapshot_last_purchase(self, snapshot):
        """Return last-purchase dict matching :meth:`_get_last_purchase` shape."""
        last_date = snapshot.last_purchase_date
        if not last_date:
            return {"last_purchase_date": None, "days_inactive": None}
        today = fields.Date.context_today(self)
        return {
            "last_purchase_date": fields.Date.to_string(last_date),
            "days_inactive": (today - last_date).days,
        }

    @staticmethod
    def _snapshot_sales_history(snapshot):
        """Deserialize stored sales history JSON."""
        if not snapshot.sales_history_json:
            return []
        return json.loads(snapshot.sales_history_json)

    @staticmethod
    def _snapshot_product_history(snapshot):
        """Deserialize stored product history JSON."""
        if not snapshot.product_history_json:
            return []
        return json.loads(snapshot.product_history_json)

    def _snapshot_revenue_metrics(self, snapshot):
        """Return prior/recent revenue halves and change pct from snapshot."""
        return {
            "prior": snapshot.revenue_prior or 0.0,
            "recent": snapshot.revenue_recent or 0.0,
            "change_pct": snapshot.revenue_change_pct,
        }
