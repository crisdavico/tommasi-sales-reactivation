import json
import re
from collections import defaultdict

from markupsafe import escape

from odoo import fields, models
from odoo.tools import float_round
from odoo.tools.mail import plaintext2html
from odoo.tools.misc import format_amount
from .tommasi_reactivation_service import (
    EVIDENCE_FIELD_LABELS,
    EVIDENCE_TIER_LABELS,
    EVIDENCE_TRIGGER_LABELS,
    REASON_TIER_DESCRIPTIONS,
    REASON_TIER_LABELS,
)

EVIDENCE_DATE_KEYS = frozenset(
    {
        "prior_purchase_date",
        "recent_purchase_date",
        "last_purchase_or_order_date",
        "order_date",
        "last_purchase_date",
    }
)


class TommasiReactivationServiceRendering(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _render_odoo_html_table(self, headers, body_rows, row_limit=None):
        """Render a table using Odoo HTML editor conventions (web_editor).

        ``row_limit=None`` uses ``config.tables_row_limit``. Pass ``False`` (or
        ``0``) to skip truncation — used by suggested products, which are
        capped by ``suggested_products_max`` upstream.
        """
        if row_limit is None:
            row_limit = self._get_config().tables_row_limit
        if row_limit:
            body_rows = list(body_rows)[: int(row_limit)]
        header_html = "".join("<th>%s</th>" % header for header in headers)
        rows_html = []
        for row in body_rows:
            rows_html.append(
                "<tr>%s</tr>"
                % "".join("<td>%s</td>" % cell for cell in row)
            )
        return (
            '<table class="table table-bordered">'
            "<thead><tr>%s</tr></thead>"
            "<tbody>%s</tbody>"
            "</table>"
        ) % (header_html, "".join(rows_html))

    def _format_evidence_qty(self, value):
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _format_price_cell(self, value, config=None):
        """Format net prices as ARS with $ prefix and es_AR separators."""
        config = config or self._get_config()
        currency = config.currency_id
        amount = float_round(value or 0.0, 2)
        if not currency:
            integer, decimal = divmod(abs(amount), 1)
            decimal_part = int(round(decimal * 100))
            sign = "-" if amount < 0 else ""
            integer_text = "{:,}".format(int(integer)).replace(",", ".")
            return "%s$%s,%02d" % (sign, integer_text, decimal_part)
        formatted = format_amount(self.env, amount, currency, lang_code="es_AR")
        return formatted.replace("\u00a0", " ")

    def _price_text_variants(self, amount):
        """Return raw price strings to replace in client messages, longest first."""
        amount = float_round(amount or 0.0, 2)
        variants = []
        if amount == int(amount):
            integer_amount = int(amount)
            variants.extend(
                [
                    "%d.00" % integer_amount,
                    "%d.0" % integer_amount,
                    "%d,00" % integer_amount,
                    "%d" % integer_amount,
                ]
            )
        else:
            dot_value = "%.2f" % amount
            variants.extend([dot_value, dot_value.replace(".", ",")])
            one_decimal = float_round(amount, 1)
            if one_decimal == amount:
                variants.append("%.1f" % amount)
        return list(dict.fromkeys(sorted(variants, key=len, reverse=True)))

    def _replace_price_literals(self, text, amount, formatted_amount):
        """Replace unformatted price literals while avoiding partial matches."""
        updated = text
        for variant in self._price_text_variants(amount):
            pattern = re.compile(
                r"(?<!\d)\$?\s*%s(?!\d)" % re.escape(variant)
            )
            updated = pattern.sub(formatted_amount, updated)
        return updated

    def _format_client_message_prices(
        self, client_message, suggested_products, config=None
    ):
        """Normalize monetary amounts in the client message to ARS display format."""
        text = self._normalize_client_message(client_message)
        if not text:
            return text
        config = config or self._get_config()
        amounts = []
        for product in suggested_products or []:
            for key in ("list_price", "offer_unit_price"):
                value = product.get(key)
                if value is not None:
                    amounts.append(float_round(value, 2))
        for amount in sorted(set(amounts), reverse=True):
            formatted_amount = self._format_price_cell(amount, config=config)
            text = self._replace_price_literals(text, amount, formatted_amount)
        return text

    def _format_evidence_date(self, value):
        if not value:
            return ""
        parsed = self._parse_date(value)
        if not parsed:
            return escape(str(value))
        return escape(parsed.strftime("%d/%m/%Y"))

    def _format_evidence_cell(self, key, value):
        if key in EVIDENCE_DATE_KEYS:
            return self._format_evidence_date(value)
        if isinstance(value, float):
            return self._format_evidence_qty(value)
        return escape(value) if value is not None else ""

    def _format_evidence_field_label(self, key):
        if not key:
            return ""
        normalized = str(key).strip()
        if normalized in EVIDENCE_FIELD_LABELS:
            return EVIDENCE_FIELD_LABELS[normalized]
        return normalized.replace("_", " ").title()

    def _format_evidence_trigger_label(self, trigger_key):
        return EVIDENCE_TRIGGER_LABELS.get(
            trigger_key, (trigger_key or "").replace("_", " ").title()
        )

    def _format_reason_label(self, reason):
        """Translate recommendation tier keys to commercial Spanish labels."""
        if not reason:
            return ""
        key = str(reason).strip()
        if key in REASON_TIER_LABELS:
            return REASON_TIER_LABELS[key]
        if key in EVIDENCE_TRIGGER_LABELS:
            return EVIDENCE_TRIGGER_LABELS[key]
        return key.replace("_", " ").title()

    def _evidence_product_lines(self, details):
        """Return product line rows from evidence detail dicts (PRD + legacy keys)."""
        if isinstance(details, list):
            return details
        if not isinstance(details, dict):
            return []
        return (
            details.get("lines")
            or details.get("products")
            or details.get("items")
            or []
        )

    def _normalize_shorthand_evidence_dict(self, data):
        """Wrap legacy/shorthand evidence dicts into triggers+details shape."""
        if not isinstance(data, dict):
            return None
        if data.get("details") is not None:
            return data
        inactivity_keys = ("days_inactive", "tier", "last_purchase_date")
        if any(key in data for key in inactivity_keys):
            return {
                "triggers": ["inactivity"],
                "details": {"inactivity": data},
            }
        revenue_keys = (
            "decline_pct",
            "revenue_change_pct",
            "prior_revenue",
            "recent_revenue",
            "prior_period_revenue",
            "current_revenue",
        )
        if any(key in data for key in revenue_keys):
            return {
                "triggers": ["revenue_decline"],
                "details": {"revenue_decline": data},
            }
        lines = self._evidence_product_lines(data)
        if lines:
            first_line = lines[0] if isinstance(lines[0], dict) else {}
            if first_line.get("ordered_qty") is not None:
                return {
                    "triggers": ["undelivered_so_lines"],
                    "details": {"undelivered_so_lines": data},
                }
            if (
                first_line.get("days_since") is not None
                or first_line.get("days_since_last_purchase") is not None
                or first_line.get("last_purchase_date")
            ):
                return {
                    "triggers": ["dropoff"],
                    "details": {"dropoff": data},
                }
            if first_line.get("prior_quantity") is not None:
                return {
                    "triggers": ["volume_decline_with_stock"],
                    "details": {"volume_decline_with_stock": data},
                }
        return None

    def _parse_json_dict(self, value):
        """Return a dict if value is a JSON object string; else None."""
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped.startswith("{"):
            return None
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _coerce_evidence_dict(self, data):
        """Return structured evidence dict, or None if data is not usable."""
        if not isinstance(data, dict):
            return None
        if data.get("details") is not None:
            return data
        return self._normalize_shorthand_evidence_dict(data)

    def _coerce_structured_evidence(self, payload):
        payload = payload or {}
        for candidate in (
            payload.get("evidence"),
            payload.get("evidence_summary"),
        ):
            structured = self._coerce_evidence_dict(candidate)
            if structured:
                return structured
        summary = payload.get("evidence_summary")
        if isinstance(summary, str):
            return self._coerce_evidence_dict(self._parse_json_dict(summary))
        return None

    def _format_generic_evidence_details(self, details):
        if not details:
            return ""
        body_rows = []
        for key, value in sorted(details.items()):
            if isinstance(value, (list, dict)):
                display = json.dumps(value, ensure_ascii=False, indent=2)
            else:
                display = str(value)
            body_rows.append(
                [
                    escape(self._format_evidence_field_label(key)),
                    escape(display),
                ]
            )
        if not body_rows:
            return ""
        return self._render_odoo_html_table(["Campo", "Valor"], body_rows)

    def _format_product_lines_table(self, lines, columns):
        if not lines:
            return ""
        headers = [escape(label) for label, _key in columns]
        body_rows = []
        for line in lines:
            cells = []
            for _label, key in columns:
                cells.append(self._format_evidence_cell(key, line.get(key)))
            body_rows.append(cells)
        return self._render_odoo_html_table(headers, body_rows)

    def _format_inactivity_evidence(self, details):
        details = details or {}
        parts = []
        days = details.get("days_inactive")
        if days is not None:
            parts.append("%s días desde la última compra" % days)
        tier = details.get("tier")
        if tier:
            parts.append(
                "nivel de inactividad %s" % EVIDENCE_TIER_LABELS.get(tier, str(tier))
            )
        last_date = details.get("last_purchase_date")
        if last_date:
            parsed = self._parse_date(last_date)
            date_text = (
                parsed.strftime("%d/%m/%Y") if parsed else str(last_date)
            )
            parts.append("última compra el %s" % date_text)
        if not parts:
            return self._format_generic_evidence_details(details)
        return "<p>%s</p>" % escape("; ".join(parts))

    def _format_revenue_decline_evidence(self, details):
        details = details or {}
        parts = []
        decline_pct = details.get("decline_pct")
        revenue_change_pct = details.get("revenue_change_pct")
        if decline_pct is not None:
            parts.append("caída del %s%%" % abs(decline_pct))
        elif revenue_change_pct is not None:
            if revenue_change_pct < 0:
                parts.append("caída del %s%%" % abs(revenue_change_pct))
            elif revenue_change_pct > 0:
                parts.append("crecimiento del %s%%" % revenue_change_pct)
            else:
                parts.append("sin variación de facturación")
        prior = details.get("prior_revenue")
        if prior is None:
            prior = details.get("prior_period_revenue")
        recent = details.get("recent_revenue")
        if recent is None:
            recent = details.get("current_revenue")
        if prior is not None and recent is not None:
            parts.append("facturación %s → %s" % (prior, recent))
        if not parts:
            return self._format_generic_evidence_details(details)
        return "<p>%s</p>" % escape("; ".join(parts))

    def _format_volume_decline_evidence(self, details):
        lines = self._evidence_product_lines(details)
        if not lines:
            return self._format_generic_evidence_details(details)
        return self._format_product_lines_table(
            lines,
            [
                ("SKU", "sku"),
                ("Producto", "name"),
                ("Cant. anterior", "prior_quantity"),
                ("Fecha compra anterior", "prior_purchase_date"),
                ("Cant. reciente", "recent_quantity"),
                ("Fecha compra reciente", "recent_purchase_date"),
                ("Últ. pedido/factura", "last_purchase_or_order_date"),
                ("Stock", "available_qty"),
            ],
        )

    def _resolve_dropoff_days_since(self, line):
        """Return elapsed days for a drop-off row (PRD key + legacy aliases)."""
        days = line.get("days_since")
        if days is None:
            days = line.get("days_since_last_purchase")
        if days is None:
            last_date = self._parse_date(line.get("last_purchase_date"))
            if last_date:
                days = (fields.Date.context_today(self) - last_date).days
        return days

    def _resolve_dropoff_product_ids(self, lines):
        """Map drop-off evidence rows to product ids (payload id or SKU lookup)."""
        skus = sorted(
            {
                row["sku"]
                for row in lines
                if row.get("sku") and not row.get("product_id")
            }
        )
        sku_to_product_id = {}
        if skus:
            products = self.env["product.product"].sudo().search(
                [("default_code", "in", skus)]
            )
            sku_to_product_id = {
                product.default_code: product.id
                for product in products
                if product.default_code
            }
        product_ids = []
        for row in lines:
            product_id = row.get("product_id")
            if not product_id and row.get("sku"):
                product_id = sku_to_product_id.get(row["sku"])
                if product_id:
                    row["product_id"] = product_id
            if product_id:
                product_ids.append(product_id)
        return list(set(product_ids))

    def _format_dropoff_evidence(self, details):
        lines = self._evidence_product_lines(details)
        if not lines:
            return self._format_generic_evidence_details(details)
        config = self._get_config()
        normalized = []
        for line in lines:
            row = dict(line)
            if row.get("days_since") is None:
                days_since = self._resolve_dropoff_days_since(row)
                if days_since is not None:
                    row["days_since"] = days_since
            normalized.append(row)
        product_ids = self._resolve_dropoff_product_ids(normalized)
        stock_batch = self._get_product_stock_batch(product_ids, config=config)
        filtered = []
        for row in normalized:
            product_id = row.get("product_id")
            stock_info = stock_batch.get(product_id, {}) if product_id else {}
            available_qty = stock_info.get("available_qty", 0.0)
            row["available_qty"] = available_qty
            if self._meets_recommendation_stock(
                available_qty, config.low_stock_threshold
            ):
                filtered.append(row)
        if not filtered:
            return (
                "<p>Ningún producto con stock por encima del umbral mínimo.</p>"
            )
        return self._format_product_lines_table(
            filtered,
            [
                ("SKU", "sku"),
                ("Producto", "name"),
                ("Última compra", "last_purchase_date"),
                ("Días transcurridos", "days_since"),
                ("Stock", "available_qty"),
            ],
        )

    def _format_undelivered_so_lines_evidence(self, details):
        details = details or {}
        lines = details.get("lines") or []
        if not lines:
            return self._format_generic_evidence_details(details)
        by_order = defaultdict(list)
        order_labels = {}
        order_dates = {}
        for line in lines:
            order_id = line.get("sale_order_id")
            order_name = line.get("sale_order_name") or "SO #%s" % order_id
            by_order[order_id].append(line)
            order_labels[order_id] = order_name
            if line.get("order_date"):
                order_dates[order_id] = line["order_date"]
        parts = [
            "<p>%d línea(s) pendiente(s) en %d pedido(s) de venta.</p>"
            % (len(lines), len(by_order))
        ]
        columns = [
            ("SKU", "sku"),
            ("Producto", "name"),
            ("Fecha pedido", "order_date"),
            ("Pedido", "ordered_qty"),
            ("Entregado", "delivered_qty"),
            ("Pendiente", "pending_qty"),
            ("Stock", "available_qty"),
        ]
        for order_id in sorted(
            by_order.keys(),
            key=lambda oid: order_labels.get(oid) or "",
            reverse=True,
        ):
            header = escape(order_labels[order_id])
            order_date = order_dates.get(order_id)
            if order_date:
                header = "%s — %s" % (header, self._format_evidence_date(order_date))
            parts.append("<h4>%s</h4>" % header)
            parts.append(self._format_product_lines_table(by_order[order_id], columns))
        return "".join(parts)

    def _format_structured_evidence_html(self, evidence):
        triggers = evidence.get("triggers") or []
        details = evidence.get("details") or {}
        parts = []
        if triggers:
            labels = [
                self._format_evidence_trigger_label(trigger) for trigger in triggers
            ]
            parts.append(
                "<p><strong>Disparadores:</strong> %s</p>" % escape(", ".join(labels))
            )
        formatters = {
            "inactivity": self._format_inactivity_evidence,
            "revenue_decline": self._format_revenue_decline_evidence,
            "volume_decline_with_stock": self._format_volume_decline_evidence,
            "dropoff": self._format_dropoff_evidence,
            "undelivered_so_lines": self._format_undelivered_so_lines_evidence,
        }
        ordered_triggers = list(triggers) + [
            key for key in details if key not in triggers
        ]
        seen = set()
        for trigger in ordered_triggers:
            if trigger in seen:
                continue
            seen.add(trigger)
            trigger_details = details.get(trigger)
            if trigger_details is None:
                continue
            parts.append(
                "<h4>%s</h4>" % escape(self._format_evidence_trigger_label(trigger))
            )
            formatter = formatters.get(trigger, self._format_generic_evidence_details)
            parts.append(formatter(trigger_details))
        return "".join(parts)

    def _enrich_evidence_dates(self, payload, customer_id, seller_id, config=None):
        """Fill missing commercial dates on evidence rows from live detection facts."""
        payload = payload or {}
        if not customer_id or not seller_id:
            return payload
        structured = self._coerce_structured_evidence(payload)
        if not structured:
            return payload
        details = structured.get("details") or {}
        needs_volume = "volume_decline_with_stock" in details
        needs_undelivered = "undelivered_so_lines" in details
        if not needs_volume and not needs_undelivered:
            return payload

        config = config or self._get_config()
        seller_env = self._env_with_seller(seller_id)
        if needs_volume:
            volume_rows = self._get_volume_decline_with_stock(
                customer_id, config=config
            )
            volume_by_product = {row["product_id"]: row for row in volume_rows}
            volume_by_sku = {
                row["sku"]: row for row in volume_rows if row.get("sku")
            }
            volume_details = details.get("volume_decline_with_stock") or {}
            for line in self._evidence_product_lines(volume_details):
                source = volume_by_product.get(line.get("product_id"))
                if source is None and line.get("sku"):
                    source = volume_by_sku.get(line["sku"])
                if not source:
                    continue
                for key in (
                    "prior_purchase_date",
                    "recent_purchase_date",
                    "last_purchase_or_order_date",
                ):
                    if not line.get(key) and source.get(key):
                        line[key] = source[key]

        if needs_undelivered:
            undelivered_rows = self._get_undelivered_so_lines(
                customer_id, seller_env=seller_env, config=config
            )
            undelivered_by_key = {
                (row.get("sale_order_id"), row.get("sku")): row
                for row in undelivered_rows
            }
            undelivered_details = details.get("undelivered_so_lines") or {}
            for line in undelivered_details.get("lines") or []:
                source = undelivered_by_key.get(
                    (line.get("sale_order_id"), line.get("sku"))
                )
                if source and not line.get("order_date") and source.get("order_date"):
                    line["order_date"] = source["order_date"]

        if not isinstance(payload.get("evidence"), dict):
            payload["evidence"] = structured
        return payload

    def _render_evidence_section_html(self, payload):
        structured = self._coerce_structured_evidence(payload)
        if structured:
            return self._format_structured_evidence_html(structured)
        evidence_summary = payload.get("evidence_summary")
        if not evidence_summary or not isinstance(evidence_summary, str):
            return ""
        stripped = evidence_summary.strip()
        if not stripped:
            return ""
        parsed = self._parse_json_dict(stripped)
        if isinstance(parsed, dict):
            generic = self._format_generic_evidence_details(parsed)
            if generic:
                return generic
        return "<p>%s</p>" % escape(stripped)

    def _coerce_structured_commercial_rationale(self, value):
        if isinstance(value, dict) and (
            value.get("signals") is not None or value.get("products") is not None
        ):
            return value
        parsed = self._parse_json_dict(value) if isinstance(value, str) else None
        if parsed and (
            parsed.get("signals") is not None or parsed.get("products") is not None
        ):
            return parsed
        return None

    def _format_commercial_rationale_metrics(self, metrics):
        if not metrics:
            return ""
        if isinstance(metrics, dict):
            parts = []
            for key, value in sorted(metrics.items()):
                parts.append(
                    "%s: %s"
                    % (self._format_evidence_field_label(key), value)
                )
            return "; ".join(parts)
        return str(metrics)

    def _format_structured_commercial_rationale_html(self, rationale):
        rationale = rationale or {}
        parts = []
        trigger_type = rationale.get("trigger_type")
        consolidated = rationale.get("consolidated")
        if trigger_type is not None or consolidated is not None:
            trigger_label = (
                self._format_evidence_trigger_label(trigger_type)
                if trigger_type
                else ""
            )
            parts.append(
                "<p><strong>Disparador:</strong> %s<br/>"
                "<strong>Consolidado:</strong> %s</p>"
                % (
                    escape(trigger_label),
                    escape("sí" if consolidated else "no"),
                )
            )
        signals = rationale.get("signals") or []
        if signals:
            headers = ["Señal", "Resumen", "Umbral", "Métricas"]
            body_rows = []
            for signal in signals:
                body_rows.append(
                    [
                        escape(signal.get("label") or signal.get("trigger_type") or ""),
                        escape(signal.get("summary") or ""),
                        escape(
                            signal.get("tier_label") or signal.get("tier") or ""
                        ),
                        escape(
                            self._format_commercial_rationale_metrics(
                                signal.get("metrics")
                            )
                        ),
                    ]
                )
            parts.append(
                "<h4>Señales de reactivación</h4>"
                + self._render_odoo_html_table(headers, body_rows)
            )
        return "".join(parts)

    def _render_commercial_rationale_section_html(self, commercial_rationale):
        structured = self._coerce_structured_commercial_rationale(
            commercial_rationale
        )
        if structured:
            return self._format_structured_commercial_rationale_html(structured)
        if commercial_rationale and isinstance(commercial_rationale, str):
            stripped = commercial_rationale.strip()
            if stripped:
                return "<p>%s</p>" % escape(stripped)
        return ""

    def _render_star_products_section_html(self, rows):
        """Render the star-products table for opportunity descriptions."""
        if not rows:
            return ""
        headers = ["Nombre", "SKU", "Unidades (últimos 6 meses)"]
        body_rows = []
        for row in rows:
            body_rows.append(
                [
                    escape(row.get("name") or ""),
                    escape(row.get("sku") or ""),
                    escape(self._format_evidence_qty(row.get("total_quantity"))),
                ]
            )
        return (
            "<h3>Productos Estrellas</h3>"
            + self._render_odoo_html_table(headers, body_rows)
        )

    def _render_star_categories_section_html(self, rows):
        """Render the star-categories table for opportunity descriptions."""
        if not rows:
            return ""
        headers = ["Nombre", "Unidades (últimos 6 meses)", "Participación"]
        body_rows = []
        for row in rows:
            share_pct = float_round(
                (row.get("share") or 0.0) * 100.0,
                precision_digits=1,
            )
            body_rows.append(
                [
                    escape(row.get("name") or ""),
                    escape(self._format_evidence_qty(row.get("total_quantity"))),
                    escape("%.1f%%" % share_pct),
                ]
            )
        return (
            "<h3>Categorías Estrellas</h3>"
            + self._render_odoo_html_table(headers, body_rows)
        )

    def _render_suggested_products_section_html(
        self, suggested_products, stock_batch, config=None
    ):
        """Render the suggested-products table for opportunity descriptions."""
        if not suggested_products:
            return ""
        config = config or self._get_config()
        headers = [
            "Nombre",
            "SKU",
            "Stock",
            "Precio neto",
            "Descuento aplicado en Precio Neto",
            "Motivo",
            "Explicación",
        ]
        body_rows = []
        for product in suggested_products:
            product_id = product.get("product_id")
            stock_info = stock_batch.get(product_id, {})
            available_qty = stock_info.get(
                "available_qty", product.get("available_qty", 0.0)
            )
            list_price = escape(
                self._format_price_cell(product.get("list_price"), config=config)
            )
            discount_pct = product.get("pricelist_discount_pct", 0.0)
            reason = product.get("reason") or product.get("reason_tier") or ""
            reason_key = str(reason).strip()
            body_rows.append(
                [
                    escape(product.get("name") or ""),
                    escape(product.get("sku") or ""),
                    available_qty,
                    list_price,
                    "%s%%" % discount_pct,
                    escape(self._format_reason_label(reason)),
                    escape(REASON_TIER_DESCRIPTIONS.get(reason_key, "")),
                ]
            )
        return (
            "<h3>Productos sugeridos</h3>"
            + self._render_odoo_html_table(
                headers, body_rows, row_limit=False
            )
        )

    def _render_opportunity_description(self, payload, config=None, stock_batch=None):
        config = config or self._get_config()
        stock_batch = stock_batch or {}
        parts = []
        customer_id = payload.get("customer_id")
        seller_id = payload.get("seller_id")
        if customer_id and seller_id:
            payload = self._enrich_evidence_dates(
                payload, customer_id, seller_id, config=config
            )
        evidence_html = self._render_evidence_section_html(payload)
        if evidence_html:
            parts.append("<h3>Resumen de evidencia</h3>%s" % evidence_html)
        if customer_id:
            star_products = self._get_customer_star_products(customer_id)
            star_products_html = self._render_star_products_section_html(
                star_products
            )
            if star_products_html:
                parts.append(star_products_html)
            star_categories = self._get_customer_star_categories(customer_id)
            star_categories_html = self._render_star_categories_section_html(
                star_categories
            )
            if star_categories_html:
                parts.append(star_categories_html)
        suggested_products = payload.get("suggested_products") or []
        products_html = self._render_suggested_products_section_html(
            suggested_products, stock_batch, config=config
        )
        if products_html:
            parts.append(products_html)
        commercial_rationale_html = self._render_commercial_rationale_section_html(
            payload.get("commercial_rationale")
        )
        if commercial_rationale_html:
            parts.append(
                "<h3>Fundamentación comercial</h3>%s" % commercial_rationale_html
            )
        client_message = payload.get("client_message")
        if client_message:
            client_message = self._format_client_message_prices(
                client_message, suggested_products, config=config
            )
            parts.append(
                "<h3>Mensaje al cliente</h3>%s"
                % plaintext2html(client_message)
            )
        return "".join(parts)

    def _normalize_client_message(self, client_message):
        """Preserve newlines for storage and HTML rendering."""
        text = str(client_message).strip()
        return (
            text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
        )

    def _validate_create_opportunity_payload(self, payload):
        payload = payload or {}
        customer_id = payload.get("customer_id")
        seller_id = payload.get("seller_id")
        if not customer_id:
            return "customer_id is required.", None
        if not seller_id:
            return "seller_id is required.", None
        partner = self._unified_env(seller_id=seller_id)["res.partner"].browse(
            customer_id
        )
        if not partner.exists():
            return "Customer not found.", None
        if partner.commercial_partner_id.user_id.id != seller_id:
            return "Customer is not assigned to the given seller.", None
        enabled_seller_ids = {
            seller.user_id.id for seller in self._filter_enabled_sellers()
        }
        if seller_id not in enabled_seller_ids:
            return "Seller is not enabled for reactivation.", None
        client_message = payload.get("client_message")
        if not client_message or not str(client_message).strip():
            return "client_message is required.", None
        priority_label = (payload.get("priority_label") or "").strip().lower()
        if priority_label not in ("low", "medium", "high"):
            return "priority_label must be low, medium, or high.", None
        source = payload.get("source")
        if not source:
            return "source is required.", None
        operation_key = (payload.get("operation_key") or "").strip()
        suggested_products = payload.get("suggested_products")
        if suggested_products is None or not isinstance(suggested_products, list):
            return "suggested_products must be a list.", None
        try:
            priority = self._resolve_priority_from_label(priority_label)
        except ValueError as error:
            return str(error), None
        normalized = {
            "customer_id": customer_id,
            "seller_id": seller_id,
            "client_message": self._normalize_client_message(client_message),
            "priority_label": priority_label,
            "priority": priority,
            "source": source,
            "suggested_products": suggested_products,
            "trigger_type": payload.get("trigger_type"),
            "confidence": payload.get("confidence"),
            "cycle_id": payload.get("cycle_id"),
            "operation_key": operation_key or None,
            "evidence": payload.get("evidence")
            if isinstance(payload.get("evidence"), dict)
            else None,
            "evidence_summary": payload.get("evidence_summary") or "",
            "commercial_rationale": payload.get("commercial_rationale"),
        }
        enforce_seller_cap = payload.get("enforce_seller_cap")
        if isinstance(enforce_seller_cap, bool):
            normalized["enforce_seller_cap"] = enforce_seller_cap
        return None, normalized
