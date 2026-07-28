from datetime import timedelta

from odoo import fields, models, tools

from odoo.addons.llm_tool.decorators import llm_tool

from .tommasi_reactivation_service import (
    RELATED_ALT_UNITS_WINDOW_DAYS,
    SIMILAR_CUSTOMER_WINDOW_DAYS,
)

DEFAULT_HABITUAL_UNITS = 1.0


class TommasiReactivationServiceRecommendations(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _get_dropoff_products(
        self, customer_id, seller_env=None, history=None, config=None
    ):
        if history is None:
            history = self._get_product_history(
                customer_id, config=config
            )
        config = config or self._get_config()
        today = fields.Date.context_today(self)
        dropoff = []
        for row in history:
            last_date = self._parse_date(row["last_purchase_date"])
            if not last_date:
                continue
            days_since = (today - last_date).days
            threshold = self._dropoff_threshold_days(
                row.get("avg_cadence_days"), config=config
            )
            if days_since >= threshold:
                dropoff.append(row["product_id"])
        return dropoff

    def _get_related_products(
        self, customer_id, seller_env=None, history=None, config=None
    ):
        if history is None:
            history = self._get_product_history(
                customer_id, config=config
            )
        purchased_ids = {row["product_id"] for row in history}
        if not purchased_ids:
            return []

        Product = self.env["product.product"]
        purchased_products = Product.browse(list(purchased_ids))
        source_candidates = {}
        all_candidates = []

        for product in purchased_products.sorted("id"):
            alt_variant_ids = [
                variant_id
                for variant_id in product.product_tmpl_id.alternative_product_ids.mapped(
                    "product_variant_ids"
                ).ids
                if variant_id not in purchased_ids
            ]
            if alt_variant_ids:
                source_candidates[product.id] = alt_variant_ids
                all_candidates.extend(alt_variant_ids)

        if not all_candidates:
            return []

        units = self._company_units_sold(
            list(set(all_candidates)), RELATED_ALT_UNITS_WINDOW_DAYS
        )

        selected = []
        seen = set()
        for product in purchased_products.sorted("id"):
            alts = source_candidates.get(product.id)
            if not alts:
                continue
            best = max(alts, key=lambda pid: (units.get(pid, 0.0), -pid))
            if best in seen:
                continue
            seen.add(best)
            selected.append(best)

        return self._filter_eligible_product_ids(selected)

    def _get_similar_customers(self, customer_id, seller_env=None, our_products=None):
        """Return the single most behaviorally similar commercial partner.

        Company-wide: counts distinct product overlap on posted ``out_invoice``
        lines in ``SIMILAR_CUSTOMER_WINDOW_DAYS``. ``seller_env`` is ignored.

        When ``our_products`` is provided, it is used instead of querying the
        customer's purchases in the window (avoids duplicate SQL when the
        caller already computed that set).
        """
        commercial_partner_id = self._commercial_partner_id_for_customer(customer_id)
        Partner = self.env["res.partner"].sudo()
        if not commercial_partner_id:
            return Partner.browse([])

        if our_products is None:
            our_products = self._customer_product_ids_in_window(
                commercial_partner_id, SIMILAR_CUSTOMER_WINDOW_DAYS
            )
        if not our_products:
            return Partner.browse([])

        date_from = fields.Date.context_today(self) - timedelta(
            days=SIMILAR_CUSTOMER_WINDOW_DAYS
        )
        self.env.cr.execute(
            """
            SELECT partner.commercial_partner_id,
                   COUNT(DISTINCT line.product_id) AS overlap
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
              JOIN account_move_line line ON line.move_id = move.id
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND move.invoice_date >= %s
               AND line.product_id IN %s
               AND partner.commercial_partner_id != %s
               AND line.product_id IS NOT NULL
               AND line.quantity > 0
          GROUP BY partner.commercial_partner_id
          ORDER BY overlap DESC, partner.commercial_partner_id ASC
             LIMIT 1
            """,
            (date_from, tuple(our_products), commercial_partner_id),
        )
        row = self.env.cr.fetchone()
        if not row:
            return Partner.browse([])
        return Partner.browse(row[0])

    def _get_products_bought_by_similar_customers(self, customer_id, seller_env=None):
        our_commercial_id = self._commercial_partner_id_for_customer(customer_id)
        our_products = self._customer_product_ids_in_window(
            our_commercial_id, SIMILAR_CUSTOMER_WINDOW_DAYS
        )
        similar = self._get_similar_customers(
            customer_id, seller_env=seller_env, our_products=our_products
        )
        if not similar:
            return []
        their_products = self._customer_product_ids_in_window(
            similar.id, SIMILAR_CUSTOMER_WINDOW_DAYS
        )
        our_product_set = set(our_products)
        candidate_ids = [pid for pid in their_products if pid not in our_product_set]
        return self._filter_eligible_product_ids(candidate_ids)

    def _get_similar_category_products(
        self, customer_id, seller_env=None, history=None, config=None
    ):
        if history is None:
            history = self._get_product_history(
                customer_id, config=config
            )
        purchased_ids = {row["product_id"] for row in history}
        categories = (
            self.env["product.product"]
            .browse(list(purchased_ids))
            .mapped("categ_id")
            .ids
        )
        if not categories:
            return []
        candidates = (
            self.env["product.product"]
            .sudo()
            .search(
                self._eligible_product_domain(
                    [
                        ("categ_id", "in", categories),
                        ("id", "not in", list(purchased_ids)),
                        ("sale_ok", "=", True),
                    ]
                ),
                limit=50,
            )
        )
        return candidates.ids

    @tools.ormcache("threshold", "today", "location_ids")
    def _overstock_product_ids_cached(self, threshold, today, location_ids):
        if not location_ids:
            return []
        self.env.cr.execute(
            """
            SELECT q.product_id,
                   COALESCE(SUM(q.quantity - q.reserved_quantity), 0) AS available_qty
            FROM stock_quant q
            JOIN stock_location l ON l.id = q.location_id
            JOIN product_product p ON p.id = q.product_id
            JOIN product_template t ON t.id = p.product_tmpl_id
            WHERE l.usage = 'internal'
              AND q.location_id IN %s
              AND p.active IS TRUE
              AND t.active IS TRUE
              AND t.type = 'product'
              AND t.sale_ok IS TRUE
            GROUP BY q.product_id
            HAVING COALESCE(SUM(q.quantity - q.reserved_quantity), 0) >= %s
            ORDER BY available_qty DESC
            LIMIT 100
            """,
            (tuple(location_ids), threshold),
        )
        return [row[0] for row in self.env.cr.fetchall()]

    def _get_overstock_candidates(self, customer_id=None, config=None):
        config = config or self._get_config()
        threshold = max(config.low_stock_threshold * 4, 20)
        today = fields.Date.to_string(fields.Date.context_today(self))
        location_ids = tuple(sorted(config.get_stock_location_ids()))
        return self._overstock_product_ids_cached(threshold, today, location_ids)

    def _iter_recommendation_tiers(
        self, customer_id, seller_env=None, history=None, config=None
    ):
        yield "dropoff", self._get_dropoff_products(
            customer_id, seller_env=seller_env, history=history, config=config
        )
        yield "related", self._get_related_products(
            customer_id, seller_env=seller_env, history=history, config=config
        )
        yield "similar_category", self._get_similar_category_products(
            customer_id, seller_env=seller_env, history=history, config=config
        )
        yield "similar_customer", self._get_products_bought_by_similar_customers(
            customer_id, seller_env=seller_env
        )
        yield "overstock", self._get_overstock_candidates(customer_id, config=config)

    def _rank_recommendation_tiers(
        self, customer_id, seller_env=None, history=None, config=None
    ):
        ranked = []
        seen = set()
        for tier, product_ids in self._iter_recommendation_tiers(
            customer_id,
            seller_env=seller_env,
            history=history,
            config=config,
        ):
            for product_id in product_ids:
                if product_id in seen:
                    continue
                seen.add(product_id)
                ranked.append((tier, product_id))
        return ranked

    def _habitual_units_map(self, history):
        """Map product_id to avg_qty_per_invoice from customer product history."""
        return {
            row["product_id"]: row.get("avg_qty_per_invoice") or 0.0 for row in history
        }

    def _habitual_units_for_product(self, product_id, habitual_map):
        """Return habitual units; default to 1.0 for discovery SKUs without history."""
        units = habitual_map.get(product_id) or 0.0
        return units if units > 0 else DEFAULT_HABITUAL_UNITS

    def _compute_opportunity_score(self, list_price, standard_price, habitual_units):
        margin = list_price - standard_price
        return tools.float_round(margin * habitual_units, precision_digits=2)

    def _empty_customer_ranking(self, config):
        currency = config.currency_id.name if config.currency_id else None
        return {
            "total_opportunity_score": 0.0,
            "max_opportunity_score": 0.0,
            "products_count": 0,
            "currency": currency,
        }

    def _compute_customer_ranking_summary(self, scored_rows, config):
        """Aggregate top-N product scores (N = suggested_products_max)."""
        if not scored_rows:
            return self._empty_customer_ranking(config)
        top = sorted(
            scored_rows,
            key=lambda row: (-row["opportunity_score"], row["product"].id),
        )[: config.suggested_products_max]
        currency = top[0]["currency"]
        return {
            "total_opportunity_score": tools.float_round(
                sum(row["opportunity_score"] for row in top), precision_digits=2
            ),
            "max_opportunity_score": top[0]["opportunity_score"],
            "products_count": len(top),
            "currency": currency,
        }

    def _serialize_recommendation(
        self,
        tier,
        product,
        stock_info,
        list_price,
        currency,
        low_stock_threshold,
        habitual_units=None,
        potential_net_margin=None,
        opportunity_score=None,
        pricelist_discount_pct=None,
    ):
        payload = {
            "product_id": product.id,
            "sku": product.default_code or "",
            "name": product.display_name,
            "reason_tier": tier,
            "available_qty": stock_info.get("available_qty", 0.0),
            "sellable": stock_info.get("sellable", False),
            "active": stock_info.get("active", False),
            "list_price": list_price,
            "currency": currency,
            "tax_included": False,
            "low_stock": stock_info.get("available_qty", 0.0) <= low_stock_threshold,
        }
        if pricelist_discount_pct is not None:
            payload["pricelist_discount_pct"] = pricelist_discount_pct
        if habitual_units is not None:
            payload["habitual_units"] = habitual_units
        if potential_net_margin is not None:
            payload["potential_net_margin"] = potential_net_margin
        if opportunity_score is not None:
            payload["opportunity_score"] = opportunity_score
        return payload

    def _recommendations_for_customer(self, customer_id, seller_id):
        """Return recommendation payload for one customer in seller scope."""
        partner, seller_env, error = self._seller_env_for_customer(
            customer_id, seller_id
        )
        if error:
            return {"customer_id": customer_id, "recommendations": [], "message": error}
        config = self._get_config()
        empty_result = {
            "customer_id": customer_id,
            "recommendations": [],
            "customer_ranking": self._empty_customer_ranking(config),
        }
        pricelist = self._resolve_customer_pricelist(
            customer_id, partner=partner, config=config
        )
        history = self._get_product_history(customer_id, config=config)
        habitual_map = self._habitual_units_map(history)
        ranked = self._rank_recommendation_tiers(
            customer_id,
            seller_env=seller_env,
            history=history,
            config=config,
        )
        if not ranked:
            return empty_result

        tier_by_product = {product_id: tier for tier, product_id in ranked}
        candidate_ids = [product_id for _tier, product_id in ranked]
        stock_batch = self._get_product_stock_batch(candidate_ids, config=config)
        survivor_ids = self._select_recommendation_survivors(
            ranked, stock_batch, config
        )
        if not survivor_ids:
            return empty_result

        products = self.env["product.product"].browse(survivor_ids)
        price_batch = self._price_products(pricelist, products, partner, config=config)
        standard_prices = {
            row["id"]: row["standard_price"]
            for row in products.sudo().read(["standard_price"])
        }
        recommendations, customer_ranking = self._score_and_serialize_recommendations(
            products,
            tier_by_product,
            stock_batch,
            price_batch,
            habitual_map,
            standard_prices,
            config,
        )
        return {
            "customer_id": customer_id,
            "recommendations": recommendations,
            "customer_ranking": customer_ranking,
        }

    def _passes_recommendation_stock_filter(self, product_id, stock_batch, config):
        """Return True when a product passes recommendation stock eligibility."""
        stock_info = stock_batch.get(product_id, {})
        if not stock_info.get("eligible") or not stock_info.get("sellable"):
            return False
        return self._meets_recommendation_stock(
            stock_info.get("available_qty", 0.0), config.low_stock_threshold
        )

    def _select_recommendation_survivors(self, ranked, stock_batch, config):
        """Walk tier-ranked candidates and keep those that pass stock checks.

        Personalized tiers take precedence. Overstock is only returned when no
        personalized tier produced candidates. When personalized candidates
        exist but all fail stock eligibility, the result stays empty.
        """
        personalized = []
        overstock = []
        had_personalized_candidates = False
        for tier, product_id in ranked:
            if tier == "overstock":
                if (
                    self._passes_recommendation_stock_filter(
                        product_id, stock_batch, config
                    )
                    and product_id not in overstock
                ):
                    overstock.append(product_id)
                continue
            had_personalized_candidates = True
            if product_id in personalized:
                continue
            if self._passes_recommendation_stock_filter(
                product_id, stock_batch, config
            ):
                personalized.append(product_id)
        if personalized:
            return personalized
        if had_personalized_candidates:
            return []
        return overstock

    def _score_and_serialize_recommendations(
        self,
        products,
        tier_by_product,
        stock_batch,
        price_batch,
        habitual_map,
        standard_prices,
        config,
    ):
        """Score stock survivors and serialize the top recommendation rows."""
        scored = []
        for product in products:
            list_price, currency, pricelist_discount_pct = price_batch[product.id]
            habitual_units = self._habitual_units_for_product(product.id, habitual_map)
            standard_price = standard_prices.get(product.id, 0.0)
            potential_net_margin = round(list_price - standard_price, 2)
            opportunity_score = self._compute_opportunity_score(
                list_price, standard_price, habitual_units
            )
            scored.append(
                {
                    "product": product,
                    "tier": tier_by_product[product.id],
                    "stock_info": stock_batch.get(product.id, {}),
                    "list_price": list_price,
                    "currency": currency,
                    "pricelist_discount_pct": pricelist_discount_pct,
                    "habitual_units": habitual_units,
                    "potential_net_margin": potential_net_margin,
                    "opportunity_score": opportunity_score,
                }
            )

        scored.sort(key=lambda row: (-row["opportunity_score"], row["product"].id))
        recommendations = []
        for row in scored[: config.suggested_products_max]:
            recommendations.append(
                self._serialize_recommendation(
                    row["tier"],
                    row["product"],
                    row["stock_info"],
                    row["list_price"],
                    row["currency"],
                    config.low_stock_threshold,
                    habitual_units=row["habitual_units"],
                    potential_net_margin=row["potential_net_margin"],
                    opportunity_score=row["opportunity_score"],
                    pricelist_discount_pct=row["pricelist_discount_pct"],
                )
            )
        customer_ranking = self._compute_customer_ranking_summary(scored, config)
        return recommendations, customer_ranking

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def get_product_recommendations(
        self,
        customer_id: int = None,
        customer_ids: list = None,
        *,
        seller_id: int,
    ) -> dict:
        """Sugiere productos para ofrecerle a un cliente, ya ordenados y con precio.

        Devuelve hasta ``suggested_products_max`` candidatos (por defecto 5),
        ordenados por **oportunidad potencial** descendente.

        **Formas de entrada (mutuamente excluyentes)**
        - Escalar: ``customer_id`` (un cliente).
        - Batch: ``customer_ids`` (lista de IDs). En el flujo batch v1.9,
          consultar varios clientes del mismo vendedor en una sola llamada;
          errores de alcance aislados por cliente.

        **Precios**
        - ``list_price``: precio **neto** según la tarifa del cliente (ARS, sin IVA).
        - ``pricelist_discount_pct``: descuento de la línea de tarifa (solo
          referencia comercial; no se aplica de nuevo sobre el precio neto).

        **Ordenamiento (de mayor a menor oportunidad potencial)**
        - ``oportunidad_potencial = (precio_neto_cliente - costo_real) × unidades_habituales``
        - ``precio_neto_cliente``: lista de precios del cliente (sin IVA).
        - ``costo_real``: ``standard_price`` del producto.
        - ``unidades_habituales``: promedio de cantidad por factura del cliente;
          para SKUs sin historial se usa ``1.0``.

        Los candidatos provienen de los tiers ``dropoff``, ``related``,
        ``similar_category``, ``similar_customer`` y ``overstock``; el campo
        ``reason_tier`` indica el origen pero no define el orden final.

        **Ranking por cliente**
        - ``customer_ranking.total_opportunity_score``: suma de los
          ``opportunity_score`` de los mejores ``suggested_products_max`` SKUs.
        - El agente usa este valor para ordenar el tope por vendedor y asignar
          prioridad Alta/Media/Baja.

        **Filtros automáticos**
        - Excluye productos inactivos, no almacenables (``type != product``),
          no vendibles o con stock en o por debajo de ``low_stock_threshold``
          (default: requiere más de 5 unidades).
        - Marca ``low_stock: true`` en los supervivientes cuando el stock es
          ≤ umbral (hoy siempre ``false`` tras el filtro; se conserva por compatibilidad).
        - Precio neto en ARS, **sin IVA**, según la lista de precios del cliente.

        **Ejemplo de llamada (escalar)**
        ``get_product_recommendations(customer_id=1001, seller_id=42)``

        **Ejemplo de llamada (batch)**
        ```
        get_product_recommendations(
            customer_ids=[1001, 1002, 1003],
            seller_id=42
        )
        ```
        Respuesta:
        ```json
        {
          "seller_id": 42,
          "results": {
            "1001": {
              "customer_id": 1001,
              "recommendations": [{"product_id": 550, "sku": "ACE-001"}],
              "customer_ranking": {"total_opportunity_score": 4800.0}
            },
            "1002": {
              "customer_id": 1002,
              "recommendations": [],
              "customer_ranking": {"total_opportunity_score": 0.0}
            },
            "1003": {
              "recommendations": [],
              "message": "Customer not found for seller scope."
            }
          }
        }
        ```
        Un cliente sin candidatos elegibles devuelve ``recommendations: []`` con
        ``customer_ranking`` en cero (no es error). Un error de alcance en un
        cliente no aborta a los demás.

        **Ejemplo de producto en la respuesta**
        ```json
        {
          "product_id": 550,
          "sku": "ACE-001",
          "name": "Aceite girasol 900ml",
          "reason_tier": "dropoff",
          "available_qty": 42.0,
          "list_price": 1000.00,
          "pricelist_discount_pct": 6.25,
          "currency": "ARS",
          "tax_included": false,
          "low_stock": false,
          "habitual_units": 12.0,
          "potential_net_margin": 400.00,
          "opportunity_score": 4800.00
        }
        ```
        El precio neto de tarifa es el que debe comunicar el vendedor; el descuento
        de tarifa es solo referencia comercial.

        Args:
            customer_id: ID de ``res.partner`` del cliente (forma escalar).
            customer_ids: Lista de IDs de ``res.partner`` (forma batch).
            seller_id: ID de ``res.users`` del vendedor asignado.
        Returns:
            Forma escalar: ``{customer_id, recommendations[], customer_ranking}``
            o ``{customer_id, recommendations: [], message}`` en error de alcance.
            Forma batch: ``{seller_id, results: {"<customer_id>":
            {customer_id, recommendations[], customer_ranking}
            | {recommendations: [], message}}}``.
            Si faltan o se mezclan ``customer_id`` y ``customer_ids``:
            ``{message: "Provide exactly one of customer_id or customer_ids."}``.
        """
        mode, error = self._exclusive_scalar_or_batch(
            customer_id,
            customer_ids,
            "Provide exactly one of customer_id or customer_ids.",
        )
        if error:
            return self._wrap_mcp_response(error)

        if mode == "scalar":
            return self._wrap_mcp_response(
                self._recommendations_for_customer(customer_id, seller_id)
            )

        results = {}
        for scoped_customer_id in customer_ids:
            customer_key = str(scoped_customer_id)
            payload = self._recommendations_for_customer(scoped_customer_id, seller_id)
            if payload.get("message"):
                results[customer_key] = {
                    "recommendations": [],
                    "message": payload["message"],
                }
                continue
            results[customer_key] = {
                "customer_id": payload["customer_id"],
                "recommendations": payload["recommendations"],
                "customer_ranking": payload["customer_ranking"],
            }
        return self._wrap_mcp_response({"seller_id": seller_id, "results": results})
