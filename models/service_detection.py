from datetime import timedelta

from odoo import fields, models

from odoo.addons.llm_tool.decorators import llm_tool

from .tommasi_reactivation_service import PRODUCT_HISTORY_FLOOR_WINDOW_DAYS


class TommasiReactivationServiceDetection(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _build_detection_context(
        self,
        partner,
        seller_env,
        config,
        date_range,
        customer_id=None,
        facts=None,
    ):
        """Return the full detection context dict for a scoped customer."""
        customer_id = customer_id or partner.id
        commercial_partner_id = self._commercial_partner_id_for_customer(customer_id)
        window = self._resolve_date_range(date_range, config)

        today = fields.Date.context_today(self)
        floor_date = today - timedelta(days=PRODUCT_HISTORY_FLOOR_WINDOW_DAYS)
        window_date_from = self._parse_date(window["date_from"])
        window_date_to = self._parse_date(window["date_to"])
        # Widest window across all detection-context sub-queries: earlier of the
        # resolved detection window and the product-history floor, through the
        # later of the resolved window and today (product/volume helpers that
        # fall back to their own default window always run through today).
        wide_date_from = min(window_date_from, floor_date)
        wide_date_to = max(window_date_to, today)
        if facts is None:
            shared_facts = self._get_invoice_facts(
                customer_id,
                date_from=fields.Date.to_string(wide_date_from),
                date_to=fields.Date.to_string(wide_date_to),
                commercial_partner_id=commercial_partner_id,
            )
        else:
            shared_facts = facts
        sales_history = self._get_sales_history(
            customer_id,
            date_range=date_range,
            config=config,
            facts=shared_facts,
        )
        last_purchase = self._get_last_purchase(customer_id, seller_env=seller_env)
        product_history = self._get_product_history_with_dropoff(
            customer_id,
            config=config,
            facts=shared_facts,
        )
        volume_decline_with_stock = self._get_volume_decline_with_stock(
            customer_id,
            config=config,
            facts=shared_facts,
        )
        undelivered_so_lines = self._get_undelivered_so_lines(
            customer_id, seller_env=seller_env, config=config
        )
        return {
            "customer_id": customer_id,
            "date_range": window,
            "sales_history": sales_history,
            "last_purchase": last_purchase,
            "commercial_context": self._compute_commercial_context(
                sales_history,
                last_purchase,
                volume_decline_with_stock,
                undelivered_so_lines,
                window,
            ),
            "product_history": product_history,
            "volume_decline_with_stock": volume_decline_with_stock,
            "undelivered_so_lines": undelivered_so_lines,
        }

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def get_customer_detection_context(
        self, customer_id: int, seller_id: int, date_range: dict = None
    ) -> dict:
        """Devuelve en una sola llamada todos los datos para detectar si un cliente
        debe reactivarse (inactividad, caída de compras, faltantes de stock, etc.).

        Reemplaza varias consultas separadas. Lee facturas publicadas, historial
        de productos, stock actual y pedidos de venta con entregas pendientes.

        **Parámetros**
        - ``customer_id``: ID del contacto/cliente en Odoo (ej. ``1001``).
        - ``seller_id``: ID de ``res.users`` del vendedor asignado al cliente.
        - ``date_range`` (opcional): ventana de análisis, por ejemplo
          ``{"date_from": "2025-01-01", "date_to": "2025-06-01"}``.
          Si no se envía, usa una ventana automática según la configuración
          (típicamente al menos 90 días o el umbral de inactividad secundario).

        **Qué devuelve cada sección**
        - ``sales_history``: facturación mensual (ej. marzo 2025 → $1.250.000 ARS).
        - ``last_purchase``: última compra y días sin comprar (ej. 47 días).
        - ``commercial_context``: señales agregadas para ranking del agente
          (``revenue_window_total``, ``revenue_change_pct``, ``days_inactive``,
          ``has_volume_decline_with_stock``, ``undelivered_lines_count``,
          ``stock_recovery_skus``).
        - ``product_history``: productos almacenables activos (``type = product``)
          comprados en los últimos ``max(365 días, ventana de detección)`` — no
          historial completo — con cantidad de compras y cadencia promedio.
        - ``volume_decline_with_stock``: productos que compraba menos últimamente
          pero **sí hay stock** hoy (ej. antes 100 u., ahora 20 u., stock 35 u.).
        - ``undelivered_so_lines``: líneas de pedidos confirmados sin entregar
          (ej. pidió 50 unidades, se entregaron 30, faltan 20). Solo incluye
          pedidos con ``date_order`` en los últimos 90 días. Cada línea expone
          ``order_date`` (fecha del pedido).

        **Ejemplo de llamada**
        ``get_customer_detection_context(customer_id=1001, seller_id=42)``

        **Ejemplo de fragmento de respuesta**
        ```json
        {
          "customer_id": 1001,
          "last_purchase": {
            "last_purchase_date": "2025-04-07",
            "days_inactive": 47
          },
          "volume_decline_with_stock": [
            {
              "product_id": 550,
              "sku": "ACE-001",
              "prior_quantity": 80.0,
              "recent_quantity": 15.0,
              "prior_purchase_date": "2025-02-10",
              "recent_purchase_date": "2025-04-04",
              "last_purchase_or_order_date": "2025-04-04",
              "available_qty": 42.0
            }
          ],
          "undelivered_so_lines": [
            {
              "sale_order_id": 340903,
              "sale_order_name": "S236661",
              "order_date": "2025-04-04",
              "sku": "IK16",
              "pending_qty": 4.0,
              "available_qty": 13.0
            }
          ]
        }
        ```

        Args:
            customer_id: ID de ``res.partner`` del cliente a analizar.
            seller_id: ID de ``res.users`` del vendedor asignado.
            date_range: Opcional. Diccionario con ``date_from`` y ``date_to``
                en formato ISO (``YYYY-MM-DD``).
        Returns:
            Diccionario con historial de ventas, última compra, contexto comercial,
            historial de productos, caída de volumen con stock y líneas de pedido
            sin entregar.
        """
        partner, seller_env, error = self._seller_env_for_customer(
            customer_id, seller_id
        )
        if error:
            return {"customer_id": customer_id, "message": error}
        config = self._get_config()
        return self._build_detection_context(
            partner, seller_env, config, date_range, customer_id=customer_id
        )
