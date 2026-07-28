from datetime import date, datetime, timedelta

from odoo import fields, models

TIER_ORDER = (
    "dropoff",
    "related",
    "similar_category",
    "similar_customer",
    "overstock",
)
RELATED_ALT_UNITS_WINDOW_DAYS = 30
SIMILAR_CUSTOMER_WINDOW_DAYS = 60
PRODUCT_HISTORY_FLOOR_WINDOW_DAYS = 365
STAR_PRODUCTS_WINDOW_DAYS = 180
STAR_PRODUCTS_TOP_N = 3
UNDELIVERED_SO_LINES_WINDOW_DAYS = 90
OPEN_REACTIVATION_STAGE_XML_IDS = (
    "tommasi_sales_reactivation.stage_pendiente_revision",
    "tommasi_sales_reactivation.stage_cliente_contactado",
)
REVENUE_TREND_DECLINE_RATIO = 0.85
REVENUE_TREND_GROWTH_RATIO = 1.15
EVIDENCE_TRIGGER_LABELS = {
    "inactivity": "Inactividad",
    "revenue_decline": "Caída de ingresos",
    "volume_decline_with_stock": "Caída de volumen con stock",
    "dropoff": "Caída de compra",
    "undelivered_so_lines": "Pedidos pendientes sin entregar",
    "multi": "Múltiples disparadores",
}
REASON_TIER_LABELS = {
    "dropoff": "Caída de compra",
    "related": "Producto relacionado",
    "similar_category": "Misma categoría",
    "similar_customer": "Clientes similares",
    "overstock": "Stock disponible",
}
REASON_TIER_DESCRIPTIONS = {
    "dropoff": (
        "El cliente lo compraba habitualmente y lleva más tiempo del esperado "
        "sin volver a pedirlo."
    ),
    "related": (
        "Alternativa comercial vinculada en catálogo a un producto que "
        "el cliente ya compra."
    ),
    "similar_category": (
        "Pertenece a una categoría que el cliente ya compra, pero este SKU "
        "no figura en su historial."
    ),
    "similar_customer": (
        "Lo adquieren clientes con un perfil de compra similar; este cliente "
        "aún no lo compró."
    ),
    "overstock": (
        "Hay stock abundante disponible; se prioriza para impulsar la venta."
    ),
}
EVIDENCE_TIER_LABELS = {
    "primary": "primario",
    "secondary": "secundario",
}
EVIDENCE_FIELD_LABELS = {
    "revenue_change_pct": "Variación de facturación (%)",
}


class TommasiReactivationService(models.AbstractModel):
    _name = "tommasi.reactivation.service"
    _description = "Sales reactivation MCP tools and internal helpers"

    # ------------------------------------------------------------------
    # Context / config helpers
    # ------------------------------------------------------------------

    def _base_tool_context(self, seller_id=None):
        ctx = dict(
            self.env.context,
            reactivation_unified_companies=True,
        )
        if seller_id:
            ctx["reactivation_seller_id"] = seller_id
        return ctx

    def _unified_env(self, seller_id=None):
        """Environment with every company visible and optional seller scope."""
        return self.env(context=self._base_tool_context(seller_id=seller_id))

    def _env_with_seller(self, seller_id):
        """Return an environment scoped to the given seller across all companies."""
        return self._unified_env(seller_id=seller_id)

    def _get_config(self):
        return self._unified_env()["tommasi.reactivation.config"].get_primary_config()

    def _dropoff_threshold_days(self, avg_cadence_days, config=None):
        """Days since last purchase required to treat a product as dropped off."""
        config = config or self._get_config()
        cadence = avg_cadence_days or config.inactivity_days_primary
        return max(float(cadence) * 1.5, config.inactivity_days_primary)

    def _resolve_stage_by_xml_id(self, xml_id):
        return self.env.ref(xml_id, raise_if_not_found=False)

    def _exclusive_scalar_or_batch(self, scalar, batch, error_message):
        """Return ('scalar'|'batch', None) or (None, error_dict)."""
        has_scalar = scalar is not None
        has_batch = batch is not None
        if has_scalar == has_batch:
            return None, {"message": error_message}
        if has_scalar:
            return "scalar", None
        return "batch", None

    def _wrap_mcp_response(self, data, request_id=None):
        """Return an MCP envelope around tool payload data."""
        envelope = {"data": data}
        if request_id:
            envelope["request_id"] = request_id
        return envelope

    def _normalize_tool_kwargs(self, kwargs):
        """Accept nested ``date_range`` or flat ``date_from``/``date_to`` keys."""
        normalized = dict(kwargs or {})
        date_range = normalized.get("date_range")
        if not date_range:
            flat_from = normalized.pop("date_from", None)
            flat_to = normalized.pop("date_to", None)
            if flat_from or flat_to:
                normalized["date_range"] = {
                    key: value
                    for key, value in (
                        ("date_from", flat_from),
                        ("date_to", flat_to),
                    )
                    if value
                }
        return normalized

    def _resolve_open_stage_ids(
        self,
        seller_env=None,
        stage_xml_ids=None,
        stage_ids=None,
        statuses=None,
    ):
        """Resolve CRM stage IDs for open reactivation opportunities."""
        if stage_ids:
            return list(dict.fromkeys(stage_ids))
        if stage_xml_ids is not None:
            resolved = []
            for xml_id in stage_xml_ids:
                stage = self.env.ref(xml_id, raise_if_not_found=False)
                if stage:
                    resolved.append(stage.id)
            return list(dict.fromkeys(resolved))
        if statuses:
            seller_env = seller_env or self._unified_env()
            stages = seller_env["crm.stage"].search([("name", "in", list(statuses))])
            return list(dict.fromkeys(stages.ids))
        resolved = []
        for xml_id in OPEN_REACTIVATION_STAGE_XML_IDS:
            stage = self.env.ref(xml_id, raise_if_not_found=False)
            if stage:
                resolved.append(stage.id)
        return list(dict.fromkeys(resolved))

    def _stage_xml_id_map_for(self, stages):
        """Resolve XML IDs for many CRM stages in one ``ir.model.data`` query."""
        stage_ids = stages.ids
        if not stage_ids:
            return {}
        imds = self.env["ir.model.data"].sudo().search(
            [("model", "=", "crm.stage"), ("res_id", "in", stage_ids)]
        )
        return {imd.res_id: imd.complete_name for imd in imds}

    def _stage_xml_id_for(self, stage, stage_xml_id_map=None):
        if not stage:
            return None
        if stage_xml_id_map is not None:
            return stage_xml_id_map.get(stage.id)
        imd = self.env["ir.model.data"].sudo().search(
            [("model", "=", "crm.stage"), ("res_id", "=", stage.id)],
            limit=1,
        )
        return imd.complete_name if imd else None

    def _annotate_product_dropoff(self, history, config=None):
        """Mark ``dropped_off`` on product rows and return compact facts."""
        config = config or self._get_config()
        today = fields.Date.context_today(self)
        facts = []
        for row in history:
            last_date = self._parse_date(row.get("last_purchase_date"))
            if not last_date:
                row["dropped_off"] = False
                continue
            days_since = (today - last_date).days
            threshold = self._dropoff_threshold_days(
                row.get("avg_cadence_days"), config=config
            )
            dropped = days_since >= threshold
            row["dropped_off"] = dropped
            if dropped:
                facts.append(
                    {
                        "product_id": row["product_id"],
                        "sku": row.get("sku", ""),
                        "name": row.get("name", ""),
                        "last_purchase_date": row.get("last_purchase_date"),
                        "days_since": days_since,
                        "days_since_last_purchase": days_since,
                        "avg_cadence_days": row.get("avg_cadence_days")
                        or config.inactivity_days_primary,
                        "dropped_off": True,
                    }
                )
        return facts

    def _serialize_config(self, config):
        return {
            "active": config.active,
            "currency": config.currency_id.name or "ARS",
            "cooldown_days": config.cooldown_days,
            "opportunity_cap_per_seller": config.opportunity_cap_per_seller,
            "low_stock_threshold": config.low_stock_threshold,
            "inactivity_days_primary": config.inactivity_days_primary,
            "inactivity_days_secondary": config.inactivity_days_secondary,
            "suggested_products_max": config.suggested_products_max,
            "reminder_stale_days": config.reminder_stale_days,
            "min_confidence_threshold": config.min_confidence_threshold,
            "bootstrap_min_invoices": config.bootstrap_min_invoices,
            "bootstrap_invoice_window_days": config.bootstrap_invoice_window_days,
            "stock_location_ids": config.stock_location_ids.ids,
            "priority_rules": [
                {
                    "label": rule.label,
                    "min_confidence": rule.min_confidence,
                    "min_inactivity_days": rule.min_inactivity_days,
                    "min_decline_pct": rule.min_decline_pct,
                    "min_customer_value": rule.min_customer_value,
                    "multi_trigger_weight": rule.multi_trigger_weight,
                    "cap_order": rule.cap_order,
                }
                for rule in config.priority_rule_ids.sorted("cap_order")
            ],
        }

    def _partner_identifier(self, partner):
        return partner.vat or partner.ref or str(partner.id)

    def _parse_date(self, value):
        if not value:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        return fields.Date.from_string(value)

    def _default_date_range(self, config):
        days = max(config.inactivity_days_secondary, 90)
        date_to = fields.Date.context_today(self)
        date_from = date_to - timedelta(days=days)
        return {
            "date_from": fields.Date.to_string(date_from),
            "date_to": fields.Date.to_string(date_to),
        }

    def _resolve_date_range(self, date_range=None, config=None):
        config = config or self._get_config()
        if not date_range:
            return self._default_date_range(config)
        date_from = self._parse_date(date_range.get("date_from"))
        date_to = self._parse_date(date_range.get("date_to"))
        if not date_from or not date_to:
            return self._default_date_range(config)
        return {
            "date_from": fields.Date.to_string(date_from),
            "date_to": fields.Date.to_string(date_to),
        }

    def _meets_recommendation_stock(self, available_qty, low_stock_threshold):
        """Return True when stock exceeds the configured minimum for suggestions."""
        return available_qty > low_stock_threshold

    def _undelivered_so_states(self):
        return ("sale", "done")

    def _undelivered_so_order_domain(self, commercial_partner_id, date_from=None):
        """Domain for sale orders with potentially undelivered eligible lines."""
        if date_from is None:
            date_from = self._undelivered_so_lines_date_from()
        return [
            ("partner_id", "child_of", commercial_partner_id),
            ("state", "in", self._undelivered_so_states()),
            ("date_order", ">=", date_from),
        ]

    def _undelivered_so_lines_date_from(self):
        """Earliest sale.order.date_order included in undelivered line queries."""
        return fields.Date.context_today(self) - timedelta(
            days=UNDELIVERED_SO_LINES_WINDOW_DAYS
        )

    @staticmethod
    def _pct_change(prior, recent):
        """Percent change from prior half to recent half, or None if prior is 0."""
        if not prior:
            return None
        return round(((recent - prior) / prior) * 100.0, 2)

    @staticmethod
    def _half_window_metrics(last_date, rev, qty_halves, today):
        """Normalize batched half-window SQL metrics for one commercial partner."""
        days_inactive = (today - last_date).days if last_date and today else None
        revenue_prior = round(rev["prior"], 2)
        revenue_recent = round(rev["recent"], 2)
        return {
            "last_date": last_date,
            "days_inactive": days_inactive,
            "revenue_prior": revenue_prior,
            "revenue_recent": revenue_recent,
            "revenue_change_pct": TommasiReactivationService._pct_change(
                rev["prior"], rev["recent"]
            ),
            "qty_change_pct": TommasiReactivationService._pct_change(
                qty_halves["prior"], qty_halves["recent"]
            ),
        }
