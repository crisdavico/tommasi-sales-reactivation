from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

from odoo import fields, models

from .tommasi_reactivation_service import (
    PRODUCT_HISTORY_FLOOR_WINDOW_DAYS,
    STAR_PRODUCTS_TOP_N,
    STAR_PRODUCTS_WINDOW_DAYS,
)


class TommasiReactivationServiceFacts(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _commercial_partner_invoice_counts(self, partner_ids, window_days, seller_id):
        """Posted out_invoice counts per commercial partner in a date window."""
        if not partner_ids:
            return {}
        date_to = fields.Date.context_today(self)
        date_from = date_to - timedelta(days=window_days)
        self.env.cr.execute(
            """
            SELECT commercial.id, COUNT(move.id)
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN res_partner commercial ON commercial.id = partner.commercial_partner_id
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND move.invoice_date >= %s
               AND move.invoice_date <= %s
               AND commercial.user_id = %s
               AND commercial.id = ANY(%s)
          GROUP BY commercial.id
            """,
            (
                fields.Date.to_string(date_from),
                fields.Date.to_string(date_to),
                seller_id,
                list(partner_ids),
            ),
        )
        return dict(self.env.cr.fetchall())

    def _filter_bootstrap_partners(self, partners, config, seller_id):
        """Keep partners that meet the configured bootstrap invoice threshold."""
        min_invoices = config.bootstrap_min_invoices
        if min_invoices <= 0:
            return partners
        counts = self._commercial_partner_invoice_counts(
            partners.ids,
            config.bootstrap_invoice_window_days,
            seller_id,
        )
        return partners.filtered(
            lambda partner: counts.get(partner.commercial_partner_id.id, 0)
            >= min_invoices
        )

    def _company_units_sold(self, product_ids, days):
        """Sum sold quantities company-wide per product in a rolling window.

        Posted ``out_invoice`` lines only; no seller or partner scoping.
        Returns ``{product_id: quantity}`` for the given product ids.
        """
        if not product_ids:
            return {}
        date_from = fields.Date.context_today(self) - timedelta(days=days)
        self.env.cr.execute(
            """
            SELECT line.product_id,
                   COALESCE(SUM(line.quantity), 0) AS qty
              FROM account_move move
              JOIN account_move_line line ON line.move_id = move.id
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND move.invoice_date >= %s
               AND line.product_id IN %s
             GROUP BY line.product_id
            """,
            (date_from, tuple(product_ids)),
        )
        return {row[0]: row[1] for row in self.env.cr.fetchall()}

    def _customer_product_ids_in_window(self, commercial_partner_id, days):
        """Distinct product ids bought by a commercial partner in a rolling window.

        Posted ``out_invoice`` product lines with positive quantity only.
        """
        if not commercial_partner_id:
            return []
        date_from = fields.Date.context_today(self) - timedelta(days=days)
        self.env.cr.execute(
            """
            SELECT DISTINCT line.product_id
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN account_move_line line ON line.move_id = move.id
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND move.invoice_date >= %s
               AND partner.commercial_partner_id = %s
               AND line.product_id IS NOT NULL
               AND line.quantity > 0
             ORDER BY line.product_id
            """,
            (date_from, commercial_partner_id),
        )
        return [row[0] for row in self.env.cr.fetchall()]

    def _get_customer_star_products(self, customer_id, days=None, limit=None):
        """Return the customer's top products by units sold in a rolling window.

        Posted ``out_invoice`` product lines only; scopes to the commercial
        partner tree. Active storable products with positive quantity only.
        """
        commercial_partner_id = self._commercial_partner_id_for_customer(
            customer_id
        )
        if not commercial_partner_id:
            return []
        window_days = days if days is not None else STAR_PRODUCTS_WINDOW_DAYS
        top_n = limit if limit is not None else STAR_PRODUCTS_TOP_N
        date_from = fields.Date.context_today(self) - timedelta(days=window_days)
        self.env.cr.execute(
            """
            SELECT product.id,
                   COALESCE(product.default_code, '') AS sku,
                   template.name AS name,
                   COALESCE(SUM(line.quantity), 0) AS qty
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN account_move_line line ON line.move_id = move.id
              JOIN product_product product ON product.id = line.product_id
              JOIN product_template template ON template.id = product.product_tmpl_id
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND move.invoice_date >= %s
               AND partner.commercial_partner_id = %s
               AND line.product_id IS NOT NULL
               AND line.quantity > 0
               AND product.active IS TRUE
               AND template.type = 'product'
             GROUP BY product.id, product.default_code, template.name
             ORDER BY qty DESC, template.name ASC
             LIMIT %s
            """,
            (date_from, commercial_partner_id, top_n),
        )
        return [
            {
                "product_id": row[0],
                "sku": row[1] or "",
                "name": row[2] or "",
                "total_quantity": round(row[3], 2),
            }
            for row in self.env.cr.fetchall()
        ]

    def _get_customer_star_categories(self, customer_id, days=None, limit=None):
        """Return the customer's top categories by units sold in a rolling window.

        Posted ``out_invoice`` product lines only; scopes to the commercial
        partner tree and unified-agent company allow-list. Active storable
        products with a resolvable direct category and positive quantity only.
        Share is category units divided by all qualifying units.
        """
        commercial_partner_id = self._commercial_partner_id_for_customer(
            customer_id
        )
        if not commercial_partner_id:
            return []
        allowed_company_ids = self.env["res.company"].sudo().search([]).ids
        if not allowed_company_ids:
            return []
        window_days = days if days is not None else STAR_PRODUCTS_WINDOW_DAYS
        top_n = limit if limit is not None else STAR_PRODUCTS_TOP_N
        date_from = fields.Date.context_today(self) - timedelta(days=window_days)
        self.env.cr.execute(
            """
            WITH qualifying AS (
              SELECT template.categ_id AS categ_id,
                     COALESCE(SUM(line.quantity), 0) AS qty
                FROM account_move move
                JOIN res_partner partner ON partner.id = move.partner_id
                JOIN account_move_line line ON line.move_id = move.id
                JOIN product_product product ON product.id = line.product_id
                JOIN product_template template ON template.id = product.product_tmpl_id
               WHERE move.move_type = 'out_invoice'
                 AND move.state = 'posted'
                 AND move.invoice_date >= %s
                 AND partner.commercial_partner_id = %s
                 AND move.company_id = ANY(%s)
                 AND line.product_id IS NOT NULL
                 AND line.quantity > 0
                 AND product.active IS TRUE
                 AND template.type = 'product'
                 AND template.categ_id IS NOT NULL
            GROUP BY template.categ_id
            )
            SELECT cat.id, cat.name, q.qty,
                   q.qty / NULLIF((SELECT SUM(qty) FROM qualifying), 0)
              FROM qualifying q
              JOIN product_category cat ON cat.id = q.categ_id
             ORDER BY q.qty DESC, cat.name ASC
             LIMIT %s
            """,
            (
                date_from,
                commercial_partner_id,
                list(allowed_company_ids),
                top_n,
            ),
        )
        return [
            {
                "category_id": row[0],
                "name": row[1] or "",
                "total_quantity": round(row[2], 2),
                "share": float(row[3]) if row[3] is not None else 0.0,
            }
            for row in self.env.cr.fetchall()
        ]

    def _invoice_facts_where_clauses(self, commercial_partner_id, date_from, date_to):
        """Build shared SQL WHERE clauses for invoice-facts queries."""
        where_clauses = [
            "move.move_type = 'out_invoice'",
            "move.state = 'posted'",
            "partner.commercial_partner_id = %s",
        ]
        params = [commercial_partner_id]
        if date_from:
            where_clauses.append("move.invoice_date >= %s")
            params.append(date_from)
        if date_to:
            where_clauses.append("move.invoice_date <= %s")
            params.append(date_to)
        return where_clauses, params

    def _invoice_facts_where_clauses_batch(
        self, commercial_partner_ids, date_from, date_to
    ):
        """Build shared SQL WHERE clauses for multi-partner invoice-facts queries."""
        where_clauses = [
            "move.move_type = 'out_invoice'",
            "move.state = 'posted'",
            "commercial.id = ANY(%s)",
        ]
        params = [list(commercial_partner_ids)]
        if date_from:
            where_clauses.append("move.invoice_date >= %s")
            params.append(date_from)
        if date_to:
            where_clauses.append("move.invoice_date <= %s")
            params.append(date_to)
        return where_clauses, params

    def _fetch_invoice_fact_moves(self, where_clauses, params):
        """Return posted move-level invoice facts for the given WHERE scope."""
        where_sql = " AND ".join(where_clauses)
        self.env.cr.execute(
            """
            SELECT move.id,
                   move.invoice_date,
                   move.amount_untaxed_signed
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
             WHERE """
            + where_sql
            + """
          ORDER BY move.invoice_date ASC, move.id ASC
            """,
            tuple(params),
        )
        return [
            {
                "move_id": row[0],
                "invoice_date": fields.Date.to_string(row[1]) if row[1] else None,
                "amount_untaxed_signed": row[2],
            }
            for row in self.env.cr.fetchall()
        ]

    def _fetch_invoice_fact_lines(self, where_clauses, params):
        """Return posted product-line invoice facts for the given WHERE scope."""
        line_where = where_clauses + [
            "line.product_id IS NOT NULL",
            "line.quantity > 0",
        ]
        line_where_sql = " AND ".join(line_where)
        self.env.cr.execute(
            """
            SELECT move.id,
                   move.invoice_date,
                   line.product_id,
                   line.quantity,
                   product.default_code,
                   template.name,
                   product.active,
                   template.type
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN account_move_line line ON line.move_id = move.id
              JOIN product_product product ON product.id = line.product_id
              JOIN product_template template ON template.id = product.product_tmpl_id
             WHERE """
            + line_where_sql
            + """
          ORDER BY move.invoice_date ASC, move.id ASC, line.id ASC
            """,
            tuple(params),
        )
        return [
            {
                "move_id": row[0],
                "invoice_date": fields.Date.to_string(row[1]) if row[1] else None,
                "product_id": row[2],
                "quantity": row[3],
                "default_code": row[4] or "",
                "display_name": row[5] or "",
                "active": row[6],
                "type": row[7],
            }
            for row in self.env.cr.fetchall()
        ]

    # EXPLAIN (devel): partner_id, invoice_date, move_id indexes suffice; no seq scans.
    def _get_invoice_facts(
        self,
        customer_id,
        date_from=None,
        date_to=None,
        commercial_partner_id=None,
    ):
        """Posted customer invoices and product lines for a commercial partner tree.

        Scopes to the commercial partner tree with optional ``date_from`` /
        ``date_to`` filters. Returns move-level revenue and line-level product
        quantities for downstream aggregation helpers.
        """
        commercial_partner_id = (
            commercial_partner_id
            or self._commercial_partner_id_for_customer(customer_id)
        )
        if not commercial_partner_id:
            return {"moves": [], "lines": []}

        where_clauses, params = self._invoice_facts_where_clauses(
            commercial_partner_id, date_from, date_to
        )
        moves = self._fetch_invoice_fact_moves(where_clauses, params)
        lines = self._fetch_invoice_fact_lines(where_clauses, params)
        return {"moves": moves, "lines": lines}

    def _fetch_invoice_fact_moves_batch(self, where_clauses, params):
        """Return posted move-level invoice facts grouped by commercial partner."""
        where_sql = " AND ".join(where_clauses)
        self.env.cr.execute(
            """
            SELECT commercial.id,
                   move.id,
                   move.invoice_date,
                   move.amount_untaxed_signed
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN res_partner commercial ON commercial.id = partner.commercial_partner_id
             WHERE """
            + where_sql
            + """
          ORDER BY commercial.id ASC, move.invoice_date ASC, move.id ASC
            """,
            tuple(params),
        )
        grouped = defaultdict(lambda: {"moves": [], "lines": []})
        for row in self.env.cr.fetchall():
            commercial_id = row[0]
            grouped[commercial_id]["moves"].append(
                {
                    "move_id": row[1],
                    "invoice_date": fields.Date.to_string(row[2]) if row[2] else None,
                    "amount_untaxed_signed": row[3],
                }
            )
        return grouped

    def _fetch_invoice_fact_lines_batch(self, where_clauses, params):
        """Return posted product-line invoice facts grouped by commercial partner."""
        line_where = where_clauses + [
            "line.product_id IS NOT NULL",
            "line.quantity > 0",
        ]
        line_where_sql = " AND ".join(line_where)
        self.env.cr.execute(
            """
            SELECT commercial.id,
                   move.id,
                   move.invoice_date,
                   line.product_id,
                   line.quantity,
                   product.default_code,
                   template.name,
                   product.active,
                   template.type
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN res_partner commercial ON commercial.id = partner.commercial_partner_id
              JOIN account_move_line line ON line.move_id = move.id
              JOIN product_product product ON product.id = line.product_id
              JOIN product_template template ON template.id = product.product_tmpl_id
             WHERE """
            + line_where_sql
            + """
          ORDER BY commercial.id ASC, move.invoice_date ASC, move.id ASC, line.id ASC
            """,
            tuple(params),
        )
        grouped = defaultdict(lambda: {"moves": [], "lines": []})
        for row in self.env.cr.fetchall():
            commercial_id = row[0]
            grouped[commercial_id]["lines"].append(
                {
                    "move_id": row[1],
                    "invoice_date": fields.Date.to_string(row[2]) if row[2] else None,
                    "product_id": row[3],
                    "quantity": row[4],
                    "default_code": row[5] or "",
                    "display_name": row[6] or "",
                    "active": row[7],
                    "type": row[8],
                }
            )
        return grouped

    def _get_invoice_facts_batch(
        self, commercial_partner_ids, date_from=None, date_to=None
    ):
        """Posted invoice facts for many commercial partners in two SQL round-trips."""
        partner_ids = list(dict.fromkeys(commercial_partner_ids or []))
        if not partner_ids:
            return {}
        where_clauses, params = self._invoice_facts_where_clauses_batch(
            partner_ids, date_from, date_to
        )
        facts_by_partner = {
            partner_id: {"moves": [], "lines": []} for partner_id in partner_ids
        }
        for partner_id, payload in self._fetch_invoice_fact_moves_batch(
            where_clauses, params
        ).items():
            facts_by_partner[partner_id]["moves"] = payload["moves"]
        for partner_id, payload in self._fetch_invoice_fact_lines_batch(
            where_clauses, params
        ).items():
            facts_by_partner[partner_id]["lines"] = payload["lines"]
        return facts_by_partner

    def _filter_facts_by_date(self, facts, date_from=None, date_to=None):
        """Narrow previously fetched invoice facts to an ISO date sub-window.

        ``invoice_date`` values in ``facts`` are ISO ``YYYY-MM-DD`` strings, so
        plain string comparison mirrors the SQL ``>= / <=`` filtering used by
        ``_get_invoice_facts``.
        """

        def _in_range(invoice_date):
            if not invoice_date:
                return False
            if date_from and invoice_date < date_from:
                return False
            if date_to and invoice_date > date_to:
                return False
            return True

        return {
            "moves": [
                move for move in facts["moves"] if _in_range(move["invoice_date"])
            ],
            "lines": [
                line for line in facts["lines"] if _in_range(line["invoice_date"])
            ],
        }

    def _invoice_facts_for_window(
        self, customer_id, date_from, date_to, facts=None, commercial_partner_id=None
    ):
        """Fetch invoice facts or narrow pre-fetched facts to a date window."""
        if facts is None:
            return self._get_invoice_facts(
                customer_id,
                date_from=date_from,
                date_to=date_to,
                commercial_partner_id=commercial_partner_id,
            )
        return self._filter_facts_by_date(facts, date_from, date_to)

    def _get_sales_history(
        self, customer_id, date_range=None, config=None, facts=None
    ):
        config = config or self._get_config()
        window = self._resolve_date_range(date_range, config)
        facts = self._invoice_facts_for_window(
            customer_id,
            window["date_from"],
            window["date_to"],
            facts=facts,
        )
        monthly = defaultdict(lambda: {"revenue": 0.0, "invoice_count": 0, "qty": 0.0})
        seen_moves = defaultdict(set)
        for move in facts["moves"]:
            month_key = move["invoice_date"][:7] if move["invoice_date"] else "unknown"
            move_id = move["move_id"]
            if move_id in seen_moves[month_key]:
                continue
            seen_moves[month_key].add(move_id)
            monthly[month_key]["revenue"] += move["amount_untaxed_signed"]
            monthly[month_key]["invoice_count"] += 1
        for line in facts["lines"]:
            month_key = line["invoice_date"][:7] if line["invoice_date"] else "unknown"
            monthly[month_key]["qty"] += line["quantity"]
        return [
            {
                "period": period,
                "revenue": round(values["revenue"], 2),
                "invoice_count": values["invoice_count"],
                "quantity": round(values["qty"], 2),
            }
            for period, values in sorted(monthly.items())
        ]

    def _revenue_halves_from_sales_history(self, sales_history, date_from, date_to):
        """Split monthly sales_history revenue into prior/recent window halves."""
        date_from = self._parse_date(date_from)
        date_to = self._parse_date(date_to)
        if not date_from or not date_to:
            return 0.0, 0.0
        midpoint = date_from + (date_to - date_from) / 2
        prior = 0.0
        recent = 0.0
        for row in sales_history:
            period = row.get("period")
            if not period or period == "unknown":
                continue
            year, month = map(int, period.split("-")[:2])
            period_start = date(year, month, 1)
            period_end = date(year, month, monthrange(year, month)[1])
            revenue = row["revenue"]
            if period_end <= midpoint:
                prior += revenue
            elif period_start >= midpoint:
                recent += revenue
            else:
                prior += revenue / 2.0
                recent += revenue / 2.0
        return prior, recent

    def _compute_commercial_context(
        self,
        sales_history,
        last_purchase,
        volume_decline_with_stock,
        undelivered_so_lines,
        date_range,
    ):
        revenue_prior, revenue_recent = self._revenue_halves_from_sales_history(
            sales_history, date_range["date_from"], date_range["date_to"]
        )
        revenue_window_total = round(revenue_prior + revenue_recent, 2)
        return {
            "revenue_window_total": revenue_window_total,
            "revenue_change_pct": self._pct_change(revenue_prior, revenue_recent),
            "days_inactive": last_purchase.get("days_inactive"),
            "has_volume_decline_with_stock": bool(volume_decline_with_stock),
            "undelivered_lines_count": len(undelivered_so_lines),
            "stock_recovery_skus": len(volume_decline_with_stock),
        }

    def _get_last_invoice_date_sql(self, customer_id, seller_env=None):
        """MAX(invoice_date) for all-time posted out_invoices in commercial tree."""
        commercial_partner_id = self._commercial_partner_id_for_customer(customer_id)
        if not commercial_partner_id:
            return None

        where_clauses = [
            "move.move_type = 'out_invoice'",
            "move.state = 'posted'",
            "partner.commercial_partner_id = %s",
        ]
        params = [commercial_partner_id]
        if seller_env:
            seller_id = seller_env.context.get("reactivation_seller_id")
            if seller_id:
                where_clauses.append("commercial.user_id = %s")
                params.append(seller_id)
        where_sql = " AND ".join(where_clauses)

        self.env.cr.execute(
            """
            SELECT MAX(move.invoice_date)
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN res_partner commercial ON commercial.id = partner.commercial_partner_id
             WHERE """
            + where_sql,
            tuple(params),
        )
        row = self.env.cr.fetchone()
        return row[0] if row else None

    def _get_last_purchase(self, customer_id, seller_env=None):
        partner = (seller_env or self.env)["res.partner"].browse(customer_id)
        if not partner.exists():
            return {"last_purchase_date": None, "days_inactive": None}
        last_date = self._get_last_invoice_date_sql(customer_id, seller_env=seller_env)
        if not last_date:
            return {"last_purchase_date": None, "days_inactive": None}
        today = fields.Date.context_today(self)
        return {
            "last_purchase_date": fields.Date.to_string(last_date),
            "days_inactive": (today - last_date).days,
        }

    def _get_product_history(
        self, customer_id, date_range=None, config=None, facts=None
    ):
        """Distinct storable products purchased within max(
        ``PRODUCT_HISTORY_FLOOR_WINDOW_DAYS`` days, detection window).

        Uses the earlier of the resolved detection ``date_from`` and the
        product-history floor window so cadence is computed over a bounded
        lookback, not all-time history.
        """
        config = config or self._get_config()
        window = self._resolve_date_range(date_range, config)
        today = fields.Date.context_today(self)
        window_date_from = self._parse_date(window["date_from"])
        floor_date = today - timedelta(days=PRODUCT_HISTORY_FLOOR_WINDOW_DAYS)
        date_from = min(window_date_from, floor_date)
        date_from_str = fields.Date.to_string(date_from)
        facts = self._invoice_facts_for_window(
            customer_id,
            date_from_str,
            window["date_to"],
            facts=facts,
        )
        product_dates = defaultdict(list)
        product_qty = defaultdict(float)
        product_meta = {}
        for line in facts["lines"]:
            if line["type"] != "product" or not line["active"]:
                continue
            product_id = line["product_id"]
            invoice_date = self._parse_date(line["invoice_date"])
            if invoice_date:
                product_dates[product_id].append(invoice_date)
            product_qty[product_id] += line["quantity"]
            product_meta[product_id] = {
                "sku": line["default_code"],
                "name": line["display_name"],
            }
        history = []
        for product_id, dates in product_dates.items():
            dates = sorted(dates)
            if not dates:
                continue
            cadence = None
            if len(dates) > 1:
                gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
                cadence = round(sum(gaps) / len(gaps), 1)
            purchase_count = len(dates)
            total_quantity = round(product_qty.get(product_id, 0.0), 2)
            avg_qty_per_invoice = (
                round(total_quantity / purchase_count, 2) if purchase_count else 0.0
            )
            meta = product_meta.get(product_id, {})
            history.append(
                {
                    "product_id": product_id,
                    "sku": meta.get("sku", ""),
                    "name": meta.get("name", ""),
                    "last_purchase_date": fields.Date.to_string(dates[-1]),
                    "purchase_count": purchase_count,
                    "total_quantity": total_quantity,
                    "avg_qty_per_invoice": avg_qty_per_invoice,
                    "avg_cadence_days": cadence,
                }
            )
        return sorted(
            history, key=lambda row: row["last_purchase_date"] or "", reverse=True
        )

    def _get_product_history_with_dropoff(
        self, customer_id, date_range=None, config=None, facts=None
    ):
        """Return product history rows annotated with ``dropped_off`` flags."""
        history = self._get_product_history(
            customer_id,
            date_range=date_range,
            config=config,
            facts=facts,
        )
        self._annotate_product_dropoff(history, config=config)
        return history

    def _get_last_order_dates_batch(self, product_ids, commercial_partner_id):
        """Latest confirmed sale-order date per product for a commercial partner."""
        if not product_ids or not commercial_partner_id:
            return {}
        self.env.cr.execute(
            """
            SELECT line.product_id, MAX(so.date_order::date)
              FROM sale_order_line line
              JOIN sale_order so ON so.id = line.order_id
              JOIN res_partner partner ON partner.id = so.partner_id
             WHERE line.product_id IN %s
               AND partner.commercial_partner_id = %s
               AND so.state IN ('sale', 'done')
          GROUP BY line.product_id
            """,
            (tuple(product_ids), commercial_partner_id),
        )
        return {row[0]: row[1] for row in self.env.cr.fetchall() if row[1] is not None}

    def _get_volume_decline_with_stock(
        self, customer_id, date_range=None, config=None, facts=None
    ):
        config = config or self._get_config()
        window = self._resolve_date_range(date_range, config)
        date_to = self._parse_date(window["date_to"])
        date_from = self._parse_date(window["date_from"])
        midpoint = date_from + (date_to - date_from) / 2
        facts = self._invoice_facts_for_window(
            customer_id,
            window["date_from"],
            window["date_to"],
            facts=facts,
        )

        def _bump_last(store, product_id, invoice_date):
            current = store.get(product_id)
            if current is None or invoice_date > current:
                store[product_id] = invoice_date

        recent_qty = defaultdict(float)
        prior_qty = defaultdict(float)
        prior_last_date = {}
        recent_last_date = {}
        product_last_invoice_date = {}
        product_meta = {}
        for line in facts["lines"]:
            product_id = line["product_id"]
            invoice_date = self._parse_date(line["invoice_date"])
            if not invoice_date:
                continue
            product_meta[product_id] = {
                "sku": line["default_code"],
                "name": line["display_name"],
            }
            _bump_last(product_last_invoice_date, product_id, invoice_date)
            if invoice_date >= midpoint:
                recent_qty[product_id] += line["quantity"]
                _bump_last(recent_last_date, product_id, invoice_date)
            if invoice_date <= midpoint:
                prior_qty[product_id] += line["quantity"]
                _bump_last(prior_last_date, product_id, invoice_date)
        declined_ids = [
            product_id
            for product_id in prior_qty
            if prior_qty[product_id] > 0
            and recent_qty.get(product_id, 0.0) < prior_qty[product_id]
        ]
        stock = self._get_product_stock_batch(declined_ids, config=config)
        commercial_partner_id = self._commercial_partner_id_for_customer(customer_id)
        order_dates = self._get_last_order_dates_batch(
            declined_ids, commercial_partner_id
        )
        results = []
        for product_id in declined_ids:
            stock_info = stock.get(product_id, {})
            available_qty = stock_info.get("available_qty", 0.0)
            if available_qty <= 0:
                continue
            meta = product_meta.get(product_id, {})
            recent_quantity = round(recent_qty.get(product_id, 0.0), 2)
            prior_date = prior_last_date.get(product_id)
            recent_date = (
                recent_last_date.get(product_id) if recent_quantity > 0 else None
            )
            last_invoice_date = product_last_invoice_date.get(product_id)
            last_order_date = order_dates.get(product_id)
            last_combined = (
                max(last_invoice_date, last_order_date)
                if last_invoice_date and last_order_date
                else last_invoice_date or last_order_date
            )
            results.append(
                {
                    "product_id": product_id,
                    "sku": meta.get("sku", ""),
                    "name": meta.get("name", ""),
                    "prior_quantity": round(prior_qty[product_id], 2),
                    "recent_quantity": recent_quantity,
                    "prior_purchase_date": fields.Date.to_string(prior_date)
                    if prior_date
                    else None,
                    "recent_purchase_date": fields.Date.to_string(recent_date)
                    if recent_date
                    else None,
                    "last_purchase_or_order_date": fields.Date.to_string(last_combined)
                    if last_combined
                    else None,
                    "available_qty": available_qty,
                }
            )
        return results

    def _get_undelivered_so_lines(self, customer_id, seller_env=None, config=None):
        """Confirmed SO lines with pending qty on eligible products.

        Only orders whose ``date_order`` falls within the last
        ``UNDELIVERED_SO_LINES_WINDOW_DAYS`` (90) are considered.
        """
        config = config or self._get_config()
        partner = (seller_env or self.env)["res.partner"].browse(customer_id)
        if not partner.exists():
            return []
        orders = (seller_env or self.env)["sale.order"].search(
            self._undelivered_so_order_domain(partner.commercial_partner_id.id)
        )
        # Batch-prefetch order lines before the nested loop below.
        orders.mapped("order_line")
        pending_entries = []
        product_ids = set()
        for order in orders:
            for line in order.order_line.filtered(
                lambda order_line: order_line.product_id
                and order_line.product_uom_qty > order_line.qty_delivered
                and self._is_eligible_reactivation_product(order_line.product_id)
            ):
                pending_entries.append((order, line))
                product_ids.add(line.product_id.id)
        stock = self._get_product_stock_batch(list(product_ids), config=config)
        lines = []
        for order, line in pending_entries:
            pending_qty = line.product_uom_qty - line.qty_delivered
            stock_info = stock.get(line.product_id.id, {})
            order_date = (
                fields.Date.to_string(order.date_order.date())
                if order.date_order
                else None
            )
            lines.append(
                {
                    "sale_order_id": order.id,
                    "sale_order_name": order.name,
                    "order_date": order_date,
                    "line_id": line.id,
                    "product_id": line.product_id.id,
                    "sku": line.product_id.default_code or "",
                    "name": line.product_id.display_name,
                    "ordered_qty": line.product_uom_qty,
                    "delivered_qty": line.qty_delivered,
                    "pending_qty": pending_qty,
                    "available_qty": stock_info.get("available_qty", 0.0),
                }
            )
        return lines
