from odoo import fields, models
from odoo.tools import float_round


class TommasiReactivationServiceProducts(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _resolve_customer_pricelist(self, customer_id, partner=None, config=None):
        if partner is None:
            partner = self.env["res.partner"].sudo().browse(customer_id)
        partner.ensure_one()
        config = config or self._get_config()
        return (
            partner.property_product_pricelist
            or partner.commercial_partner_id.property_product_pricelist
            or config.company_id.sale_order_pricelist_id
        )

    def _is_eligible_reactivation_product(self, product):
        """Active storable products only (template type = product)."""
        product = product.sudo()
        return bool(product) and product.active and product.type == "product"

    def _eligible_product_domain(self, extra_domain=None):
        domain = [("active", "=", True), ("type", "=", "product")]
        if extra_domain:
            return extra_domain + domain
        return domain

    def _filter_eligible_product_ids(self, product_ids):
        if not product_ids:
            return []
        return (
            self.env["product.product"]
            .sudo()
            .search([("id", "in", list(product_ids))] + self._eligible_product_domain())
            .ids
        )

    def _read_product_metadata(self, product_ids):
        if not product_ids:
            return {}
        rows = (
            self.env["product.product"]
            .sudo()
            .browse(product_ids)
            .read(["active", "sale_ok", "type", "default_code", "display_name"])
        )
        return {row["id"]: row for row in rows}

    def _get_available_qty_batch(self, product_ids, location_ids=None):
        if not product_ids:
            return {}
        if location_ids is not None and not location_ids:
            return {product_id: 0.0 for product_id in product_ids}
        params = [tuple(product_ids)]
        location_clause = "AND l.usage = 'internal'"
        if location_ids is not None:
            location_clause = "AND q.location_id IN %s"
            params.append(tuple(location_ids))
        self.env.cr.execute(
            f"""
            SELECT q.product_id,
                   COALESCE(SUM(q.quantity - q.reserved_quantity), 0) AS available_qty
            FROM stock_quant q
            JOIN stock_location l ON l.id = q.location_id
            WHERE q.product_id IN %s
              {location_clause}
            GROUP BY q.product_id
            """,
            tuple(params),
        )
        return {row[0]: row[1] for row in self.env.cr.fetchall()}

    def _get_product_stock_batch(self, product_ids, config=None):
        """Return stock metadata keyed by product id."""
        if not product_ids:
            return {}
        location_ids = None
        if config is not None:
            location_ids = config.get_stock_location_ids()
        metadata = self._read_product_metadata(product_ids)
        quantities = self._get_available_qty_batch(
            product_ids, location_ids=location_ids
        )
        result = {}
        for product_id in product_ids:
            meta = metadata.get(product_id, {})
            active = meta.get("active", False)
            product_type = meta.get("type")
            result[product_id] = {
                "available_qty": quantities.get(product_id, 0.0),
                "sellable": meta.get("sale_ok", False),
                "active": active,
                "product_type": product_type,
                "eligible": active and product_type == "product",
            }
        return result

    def _pricelist_reference_discount(
        self, pricelist, product, partner, net_price, rule_id
    ):
        """Return the commercial discount % from the pricelist line (reference only)."""
        if not net_price or not rule_id:
            return 0.0
        product_ctx = {
            "lang": partner.lang,
            "partner": partner.id,
            "quantity": 1.0,
            "date": fields.Date.context_today(self),
            "pricelist": pricelist.id,
            "uom": product.uom_id.id,
        }
        base_price, currency = (
            self.env["sale.order.line"]
            .sudo()
            .with_context(**product_ctx)
            ._get_real_price_currency(
                product.with_context(**product_ctx),
                rule_id,
                1.0,
                product.uom_id,
                pricelist.id,
            )
        )
        if currency and currency != pricelist.currency_id:
            base_price = currency._convert(
                base_price,
                pricelist.currency_id,
                self.env.company,
                fields.Date.context_today(self),
            )
        if base_price and base_price > net_price:
            return float_round((base_price - net_price) / base_price * 100.0, 2)
        return 0.0

    def _price_products(self, pricelist, products, partner, config=None):
        if not products:
            return {}
        price_rules = pricelist._compute_price_rule(
            [(product, 1.0, partner) for product in products],
            date=fields.Date.context_today(self),
        )
        currency_name = (
            pricelist.currency_id.name
            or (
                config.currency_id.name
                if config
                else self._get_config().currency_id.name
            )
            or "ARS"
        )
        products_by_id = {product.id: product for product in products}
        return {
            product_id: (
                float_round(price, 2),
                currency_name,
                self._pricelist_reference_discount(
                    pricelist,
                    products_by_id[product_id],
                    partner,
                    float_round(price, 2),
                    rule_id,
                ),
            )
            for product_id, (price, rule_id) in price_rules.items()
        }

    def _enrich_suggested_products_pricing(
        self, customer_id, suggested_products, seller_id=None
    ):
        """Attach authoritative net price and pricelist discount to suggested rows."""
        if not suggested_products:
            return suggested_products
        partner = self.env["res.partner"].sudo().browse(customer_id)
        if not partner.exists():
            return suggested_products
        config = self._get_config()
        pricelist = self._resolve_customer_pricelist(
            customer_id, partner=partner, config=config
        )
        product_ids = [
            row["product_id"] for row in suggested_products if row.get("product_id")
        ]
        if not product_ids:
            return suggested_products
        products = self.env["product.product"].browse(product_ids)
        price_batch = self._price_products(pricelist, products, partner, config=config)
        enriched = []
        for row in suggested_products:
            product_row = dict(row)
            product_id = product_row.get("product_id")
            pricing = price_batch.get(product_id)
            if pricing:
                net_price, currency, discount_pct = pricing
                product_row["list_price"] = net_price
                product_row["currency"] = currency
                if product_row.get("pricelist_discount_pct") is None:
                    product_row["pricelist_discount_pct"] = discount_pct
            enriched.append(product_row)
        return enriched
