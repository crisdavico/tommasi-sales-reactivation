from collections import defaultdict

from odoo import fields, models

from odoo.addons.llm_tool.decorators import llm_tool


class TommasiReactivationServiceCandidates(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    _DETECTION_CONTEXT_KEYS = (
        "sales_history",
        "last_purchase",
        "commercial_context",
        "product_history",
        "volume_decline_with_stock",
        "undelivered_so_lines",
    )

    # ------------------------------------------------------------------
    # Batch SQL helpers (not MCP tools)
    # ------------------------------------------------------------------

    def _candidate_seller_partners(self, seller, config):
        """Bootstrap-eligible partners for one seller, keyed by commercial partner id.

        Reuses the same search + ``_filter_bootstrap_partners`` scoping as
        ``_collect_bootstrap_customers_for_seller`` so candidate screening only
        considers customers already surfaced by the bootstrap cycle.
        """
        partners = self._bootstrap_partners_for_seller(seller, config)
        return {
            partner.commercial_partner_id.id: {
                "customer_id": partner.id,
                "name": partner.name,
                "identifier": self._partner_identifier(partner),
            }
            for partner in partners
        }

    def _candidate_last_purchase_batch(self, commercial_ids, seller_id):
        """MAX(invoice_date) per commercial partner, posted out_invoices only."""
        if not commercial_ids:
            return {}
        self.env.cr.execute(
            """
            SELECT commercial.id, MAX(move.invoice_date)
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN res_partner commercial ON commercial.id = partner.commercial_partner_id
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND commercial.user_id = %s
               AND commercial.id = ANY(%s)
          GROUP BY commercial.id
            """,
            (seller_id, list(commercial_ids)),
        )
        return dict(self.env.cr.fetchall())

    def _candidate_half_window_batch(
        self,
        commercial_ids,
        seller_id,
        date_from,
        midpoint,
        date_to,
        *,
        amount_expr,
        extra_line_join=False,
    ):
        """Aggregate per commercial partner split into prior/recent detection halves."""
        result = defaultdict(lambda: {"prior": 0.0, "recent": 0.0})
        if not commercial_ids:
            return result
        line_join = ""
        line_filters = ""
        if extra_line_join:
            line_join = "JOIN account_move_line line ON line.move_id = move.id"
            line_filters = """
               AND line.product_id IS NOT NULL
               AND line.quantity > 0"""
        self.env.cr.execute(
            f"""
            SELECT commercial.id,
                   CASE WHEN move.invoice_date >= %s THEN 'recent' ELSE 'prior' END AS half,
                   COALESCE({amount_expr}, 0)
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN res_partner commercial ON commercial.id = partner.commercial_partner_id
              {line_join}
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND move.invoice_date >= %s
               AND move.invoice_date <= %s
               AND commercial.user_id = %s
               AND commercial.id = ANY(%s){line_filters}
          GROUP BY commercial.id, half
            """,
            (midpoint, date_from, date_to, seller_id, list(commercial_ids)),
        )
        for commercial_id, half, amount in self.env.cr.fetchall():
            result[commercial_id][half] = amount
        return result

    def _candidate_revenue_batch(
        self, commercial_ids, seller_id, date_from, midpoint, date_to
    ):
        """Revenue per commercial partner split into prior/recent detection halves."""
        return self._candidate_half_window_batch(
            commercial_ids,
            seller_id,
            date_from,
            midpoint,
            date_to,
            amount_expr="SUM(move.amount_untaxed_signed)",
        )

    def _candidate_qty_batch(
        self, commercial_ids, seller_id, date_from, midpoint, date_to
    ):
        """Quantity per commercial partner split into prior/recent detection halves."""
        return self._candidate_half_window_batch(
            commercial_ids,
            seller_id,
            date_from,
            midpoint,
            date_to,
            amount_expr="SUM(line.quantity)",
            extra_line_join=True,
        )

    def _candidate_undelivered_batch(self, commercial_ids, seller_id):
        """Commercial partner ids with at least one undelivered eligible SO line.

        Mirrors ``_is_eligible_reactivation_product`` (active, storable) and
        ``_get_undelivered_so_lines`` (``date_order`` within last 90 days) so
        the existence flag matches what ``get_customer_detection_context`` would
        later report for the same customer.
        """
        if not commercial_ids:
            return set()
        date_from = self._undelivered_so_lines_date_from()
        states = self._undelivered_so_states()
        self.env.cr.execute(
            """
            SELECT DISTINCT commercial.id
              FROM sale_order so
              JOIN res_partner partner ON partner.id = so.partner_id
              JOIN res_partner commercial ON commercial.id = partner.commercial_partner_id
              JOIN sale_order_line line ON line.order_id = so.id
              JOIN product_product product ON product.id = line.product_id
              JOIN product_template template ON template.id = product.product_tmpl_id
             WHERE so.state = ANY(%s)
               AND so.date_order >= %s
               AND commercial.user_id = %s
               AND commercial.id = ANY(%s)
               AND line.product_id IS NOT NULL
               AND line.product_uom_qty > line.qty_delivered
               AND product.active = true
               AND template.type = 'product'
            """,
            (list(states), date_from, seller_id, list(commercial_ids)),
        )
        return {row[0] for row in self.env.cr.fetchall()}

    def _candidate_screens(
        self, metrics, has_undelivered, config, dropoff_facts=None
    ):
        screens = []
        days_inactive = metrics["days_inactive"]
        revenue_change_pct = metrics["revenue_change_pct"]
        qty_change_pct = metrics["qty_change_pct"]
        if (
            days_inactive is not None
            and days_inactive >= config.inactivity_days_primary
        ):
            screens.append("inactivity")
        if revenue_change_pct is not None and revenue_change_pct < 0:
            screens.append("revenue_decline")
        if qty_change_pct is not None and qty_change_pct < 0:
            screens.append("qty_decline")
        if has_undelivered:
            screens.append("undelivered_so_lines")
        if dropoff_facts:
            screens.append("dropoff")
        return screens

    def _candidate_product_dropoff_facts(self, snapshot, config):
        """Return compact drop-off facts from a fresh snapshot when available."""
        if not snapshot:
            return []
        history = self._snapshot_product_history(snapshot)
        if not isinstance(history, list):
            return []
        return self._annotate_product_dropoff(history, config=config)

    def _resolve_candidate_metrics(
        self, commercial_id, snapshot, last_purchase, revenue, qty, today
    ):
        """Return normalized screening metrics from snapshot or live batch SQL."""
        if snapshot:
            last_purchase_info = self._snapshot_last_purchase(snapshot)
            last_date = self._parse_date(last_purchase_info.get("last_purchase_date"))
            rev_metrics = self._snapshot_revenue_metrics(snapshot)
            return {
                "last_date": last_date,
                "days_inactive": last_purchase_info.get("days_inactive"),
                "revenue_prior": round(rev_metrics["prior"], 2),
                "revenue_recent": round(rev_metrics["recent"], 2),
                "revenue_change_pct": rev_metrics["change_pct"],
                "qty_change_pct": snapshot.qty_change_pct,
            }
        rev = revenue.get(commercial_id, {"prior": 0.0, "recent": 0.0})
        qty_halves = qty.get(commercial_id, {"prior": 0.0, "recent": 0.0})
        return self._half_window_metrics(
            last_purchase.get(commercial_id), rev, qty_halves, today
        )

    def _build_candidate_row(
        self, info, metrics, screens, has_undelivered, dropoff_facts=None
    ):
        """Build one screened candidate payload for ``get_reactivation_candidates``."""
        revenue_prior = metrics["revenue_prior"]
        revenue_recent = metrics["revenue_recent"]
        last_date = metrics["last_date"]
        return {
            "customer_id": info["customer_id"],
            "name": info["name"],
            "identifier": info["identifier"],
            "days_inactive": metrics["days_inactive"],
            "last_purchase_date": fields.Date.to_string(last_date)
            if last_date
            else None,
            "revenue_prior": revenue_prior,
            "revenue_recent": revenue_recent,
            "revenue_window_total": round(revenue_prior + revenue_recent, 2),
            "revenue_change_pct": metrics["revenue_change_pct"],
            "qty_change_pct": metrics["qty_change_pct"],
            "has_undelivered_lines": has_undelivered,
            "screens": screens,
            "product_dropoff_facts": dropoff_facts or [],
        }

    def _candidate_detection_context(self, customer_id, seller_env, config, date_range):
        """Build detection_context for one screened candidate; never raises."""
        try:
            partner = seller_env["res.partner"].browse(customer_id)
            if not partner.exists():
                return {"message": "Customer not found for seller scope."}
            full = self._build_detection_context(
                partner, seller_env, config, date_range, customer_id=customer_id
            )
            return {key: full[key] for key in self._DETECTION_CONTEXT_KEYS}
        except Exception as exc:
            return {"message": str(exc)}

    # ------------------------------------------------------------------
    # MCP tool
    # ------------------------------------------------------------------

    def _filter_candidate_partner_map(self, partner_map, customer_ids):
        """Restrict partner map to requested customer ids when provided.

        ``customer_ids=None`` keeps the full seller portfolio (scheduled path).
        An empty list scopes to no customers. Matching is by partner
        ``customer_id`` (the bootstrap partner id stored in the map values).
        """
        if customer_ids is None:
            return partner_map
        allowed = {int(customer_id) for customer_id in customer_ids}
        return {
            commercial_id: info
            for commercial_id, info in partner_map.items()
            if info["customer_id"] in allowed
        }

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def get_reactivation_candidates(
        self,
        seller_id: int,
        date_range: dict = None,
        include_context: bool = False,
        request_id: str = None,
        date_from: str = None,
        date_to: str = None,
        customer_ids: list = None,
    ) -> dict:
        """Preselecciona, con ~4 consultas SQL en lote, los clientes de un vendedor
        que muestran alguna señal de reactivación en la ventana de detección.

        Pensado para reemplazar el patrón "un ``get_customer_detection_context``
        por cliente" cuando hay que revisar la cartera completa de un vendedor:
        en lugar de N llamadas (una por cliente), esta hace consultas agregadas
        por vendedor y devuelve solo los clientes que pasan al menos un filtro
        ("screen"). Con ``include_context=true``, cada candidato incluye además
        el mismo ``detection_context`` que devolvería
        ``get_customer_detection_context``, resuelto en la misma transacción
        del servidor; si un cliente falla, su ``detection_context`` trae
        ``{"message": ...}`` sin abortar el resto del lote.

        **Seguridad**
        - Solo acepta vendedores habilitados en la configuración de reactivación,
          igual que ``bootstrap_reactivation_cycle``. Si ``seller_id`` no
          corresponde a un vendedor habilitado, devuelve ``message`` sin exponer
          datos de clientes.
        - Todo el SQL está filtrado por ``commercial.user_id = seller_id``: un
          vendedor nunca puede ver clientes de otro vendedor a través de este
          tool.

        **Universo de clientes**
        Mismo criterio que el bootstrap: contactos padre, activos, con
        ``customer_rank > 0`` y que cumplen el mínimo de facturas configurado
        (``bootstrap_min_invoices`` en ``bootstrap_invoice_window_days`` días).

        **Screens aplicados (se incluye el cliente si cumple AL MENOS uno)**
        - ``inactivity``: días sin comprar ≥ ``inactivity_days_primary``.
        - ``revenue_decline``: facturación de la segunda mitad de la ventana
          menor que la primera mitad (``revenue_change_pct`` negativo).
        - ``qty_decline``: cantidad facturada de la segunda mitad menor que la
          primera (``qty_change_pct`` negativo).
        - ``undelivered_so_lines``: tiene al menos una línea de pedido
          confirmado (``sale``/``done``) con cantidad pendiente de entrega en
          un producto elegible (activo, almacenable). Solo pedidos con
          ``date_order`` en los últimos 90 días.

        **Trade-off conocido**
        La caída de volumen se mide sobre el total facturado del cliente, no
        por producto. Un cliente que dejó de comprar un producto puntual pero
        compensó con otros (volumen total estable) **no** aparece como
        candidato aquí; ese caso puntual lo detecta
        ``get_customer_detection_context`` → ``volume_decline_with_stock``,
        que sí compara por producto. Este tool prioriza velocidad de barrido
        sobre esa granularidad.

        **Ejemplo de llamada**
        ``get_reactivation_candidates(seller_id=42, include_context=true)``

        **Ejemplo de respuesta (recortada, con contexto)**
        ```json
        {
          "seller_id": 42,
          "date_range": {"date_from": "2025-03-01", "date_to": "2025-06-01"},
          "candidates": [
            {
              "customer_id": 1001,
              "name": "Distribuidora Norte SA",
              "identifier": "30-71234567-8",
              "days_inactive": 47,
              "last_purchase_date": "2025-04-07",
              "revenue_change_pct": -18.3,
              "qty_change_pct": -5.0,
              "revenue_prior": 1200.0,
              "revenue_recent": 980.0,
              "revenue_window_total": 2180.0,
              "has_undelivered_lines": false,
              "screens": ["inactivity", "revenue_decline"],
              "detection_context": {
                "sales_history": [],
                "last_purchase": {
                  "last_purchase_date": "2025-04-07",
                  "days_inactive": 47
                },
                "commercial_context": {
                  "revenue_window_total": 2180.0,
                  "days_inactive": 47
                },
                "product_history": [],
                "volume_decline_with_stock": [],
                "undelivered_so_lines": []
              }
            }
          ],
          "screened_out_count": 233
        }
        ```

        Args:
            seller_id: ID de ``res.users`` del vendedor. Debe ser un vendedor
                habilitado en Reactivación Comercial.
            date_range: Opcional. Diccionario con ``date_from`` y ``date_to``
                (``YYYY-MM-DD``). Si no se envía, usa la ventana automática de
                ``_resolve_date_range`` (por defecto ``inactivity_days_secondary``
                o 90 días, lo que sea mayor).
            include_context: Si es ``true``, cada candidato incluye
                ``detection_context`` con las mismas claves de nivel superior que
                ``get_customer_detection_context`` (``sales_history``,
                ``last_purchase``, ``commercial_context``, ``product_history``,
                ``volume_decline_with_stock``, ``undelivered_so_lines``). Un
                fallo por cliente devuelve ``detection_context: {"message": ...}``
                sin interrumpir el resto del lote.
            customer_ids: Opcional. Lista de IDs de ``res.partner`` para limitar
                el barrido a esos clientes (modo on-demand). ``None`` mantiene
                la cartera completa del vendedor; ``[]`` no devuelve candidatos.
        Returns:
            Diccionario con ``seller_id``, ``date_range``, ``candidates`` (lista
            de clientes que pasaron al menos un screen, con métricas de revenue
            y la lista de ``screens`` que dispararon; ordenados por
            ``revenue_recent`` descendente) y ``screened_out_count``
            (cantidad de clientes de la cartera que no mostraron ninguna señal).
            Si el vendedor no está habilitado, devuelve ``{"message": ...}``.
        """
        enabled_sellers = self._filter_enabled_sellers()
        seller_line = enabled_sellers.filtered(
            lambda line: line.user_id.id == seller_id
        )
        if not seller_line:
            return self._wrap_mcp_response(
                {"message": "Seller not enabled or not found."},
                request_id=request_id,
            )

        normalized = self._normalize_tool_kwargs(
            {
                "date_range": date_range,
                "date_from": date_from,
                "date_to": date_to,
            }
        )
        date_range = normalized.get("date_range")

        config = self._get_config()
        window = self._resolve_date_range(date_range, config)
        date_from = self._parse_date(window["date_from"])
        date_to = self._parse_date(window["date_to"])
        midpoint = fields.Date.to_string(date_from + (date_to - date_from) / 2)

        partner_map = self._filter_candidate_partner_map(
            self._candidate_seller_partners(seller_line[:1], config),
            customer_ids,
        )
        commercial_ids = list(partner_map.keys())
        if not commercial_ids:
            return self._wrap_mcp_response(
                {
                    "seller_id": seller_id,
                    "date_range": window,
                    "candidates": [],
                    "screened_out_count": 0,
                },
                request_id=request_id,
            )

        last_purchase = self._candidate_last_purchase_batch(commercial_ids, seller_id)
        revenue = self._candidate_revenue_batch(
            commercial_ids, seller_id, window["date_from"], midpoint, window["date_to"]
        )
        qty = self._candidate_qty_batch(
            commercial_ids, seller_id, window["date_from"], midpoint, window["date_to"]
        )
        undelivered_ids = self._candidate_undelivered_batch(commercial_ids, seller_id)
        fresh_snapshots = self._get_fresh_snapshots(
            commercial_ids, config, window, seller_id=seller_id
        )

        today = fields.Date.context_today(self)
        seller_env = None
        if include_context:
            seller_env = self._env_with_seller(seller_id)
        candidates = []
        screened_out_count = 0
        for commercial_id, info in partner_map.items():
            snapshot = fresh_snapshots.get(commercial_id)
            metrics = self._resolve_candidate_metrics(
                commercial_id, snapshot, last_purchase, revenue, qty, today
            )
            has_undelivered = commercial_id in undelivered_ids
            dropoff_facts = self._candidate_product_dropoff_facts(snapshot, config)
            screens = self._candidate_screens(
                metrics, has_undelivered, config, dropoff_facts=dropoff_facts
            )
            if not screens:
                screened_out_count += 1
                continue
            row = self._build_candidate_row(
                info, metrics, screens, has_undelivered, dropoff_facts=dropoff_facts
            )
            if include_context:
                row["detection_context"] = self._candidate_detection_context(
                    info["customer_id"], seller_env, config, window
                )
            candidates.append(row)

        candidates.sort(
            key=lambda row: (
                -(row.get("revenue_recent") or 0.0),
                -(row.get("days_inactive") or 0),
                row["customer_id"],
            )
        )

        return self._wrap_mcp_response(
            {
                "seller_id": seller_id,
                "date_range": window,
                "candidates": candidates,
                "screened_out_count": screened_out_count,
            },
            request_id=request_id,
        )
