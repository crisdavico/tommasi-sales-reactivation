from odoo import models

from odoo.addons.llm_tool.decorators import llm_tool

_OWNERSHIP_REASON_BY_MESSAGE = {
    "Customer not found.": "customer_not_found",
    "Customer has no assigned seller.": "ownership_mismatch",
    "Customer not found for seller scope.": "ownership_mismatch",
}


class TommasiReactivationServiceBootstrap(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _serialize_bootstrap_seller(self, seller):
        """Return MCP seller dict for a reactivation seller line."""
        user = seller.user_id
        return {
            "seller_id": user.id,
            "partner_id": seller.partner_id.id,
            "name": user.name,
            "mobile": seller.mobile,
        }

    def _collect_bootstrap_customers_for_seller(self, seller, config):
        """Return bootstrap customer dicts assigned to one enabled seller."""
        user = seller.user_id
        partners = self._bootstrap_partners_for_seller(seller, config)
        return [
            {
                "customer_id": partner.id,
                "name": partner.name,
                "seller_id": user.id,
                "identifier": self._partner_identifier(partner),
            }
            for partner in partners
        ]

    def _bootstrap_ownership_failure(self, error_message):
        """Map ``_seller_env_for_customer`` errors to stable MCP reason codes."""
        return {
            "message": error_message,
            "reason": _OWNERSHIP_REASON_BY_MESSAGE.get(
                error_message, "ownership_mismatch"
            ),
        }

    def _scoped_bootstrap_reactivation_cycle(self, seller_id, customer_id):
        """Return one seller↔customer cycle context or an ownership failure payload."""
        enabled_sellers = self._filter_enabled_sellers()
        seller_line = enabled_sellers.filtered(
            lambda line: line.user_id.id == seller_id
        )[:1]
        if not seller_line:
            return {
                "message": "Seller not enabled or not found.",
                "reason": "seller_not_enabled",
            }

        partner, _seller_env, error = self._seller_env_for_customer(
            customer_id, seller_id
        )
        if error:
            return self._bootstrap_ownership_failure(error)

        config = self._get_config()
        return {
            "config": self._serialize_config(config),
            "sellers": [self._serialize_bootstrap_seller(seller_line)],
            "customers": [
                {
                    "customer_id": partner.id,
                    "name": partner.name,
                    "seller_id": seller_id,
                    "identifier": self._partner_identifier(partner),
                }
            ],
        }

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def bootstrap_reactivation_cycle(
        self, seller_id: int = None, customer_id: int = None
    ) -> dict:
        """Inicia un ciclo de reactivación comercial y devuelve todo el contexto base.

        Es la **primera llamada** del agente de detección. En un solo paso obtiene:
        parámetros operativos, reglas de prioridad, vendedores habilitados y los
        clientes que cada uno tiene asignados en Odoo.

        Sin ``seller_id``/``customer_id`` (ciclo programado) incluye toda la cartera
        habilitada. Con ambos IDs valida ownership y limita ``sellers``/``customers``
        a ese par; fallos de ownership devuelven ``message`` + ``reason`` estables
        (``ownership_mismatch`` | ``seller_not_enabled`` | ``customer_not_found``).

        **Qué filtra automáticamente** (modo sin scope)
        - Solo incluye vendedores configurados en Reactivación Comercial.
        - Solo devuelve clientes activos cuyo vendedor asignado (`user_id`) es uno
          de esos vendedores habilitados.
        - Excluye contactos hijos (`parent_id` distinto de falso) y partners sin
          historial de cliente (`customer_rank` = 0).
        - Exige al menos ``bootstrap_min_invoices`` facturas de cliente publicadas
          en los últimos ``bootstrap_invoice_window_days`` días (por defecto
          3 facturas en 180 días).

        **Ejemplo de uso**
        Llamar sin parámetros al comenzar el ciclo diario. Si hay 3 vendedores
        habilitados con 120, 85 y 40 clientes cada uno, la respuesta trae
        `sellers` con 3 registros y `customers` con 245 registros en total.

        **Ejemplo de respuesta (recortado)**
        ```json
        {
          "config": {
            "cooldown_days": 30,
            "opportunity_cap_per_seller": 20,
            "min_confidence_threshold": 0.50,
            "priority_rules": [{"label": "high", "min_confidence": 0.80}]
          },
          "sellers": [
            {
              "seller_id": 42,
              "partner_id": 501,
              "name": "María López",
              "mobile": "+5491112345678"
            }
          ],
          "customers": [
            {
              "customer_id": 1001,
              "name": "Distribuidora Norte SA",
              "seller_id": 42,
              "identifier": "30-71234567-8"
            }
          ]
        }
        ```

        Args:
            seller_id: Opcional. ID de ``res.users`` del vendedor. Requiere
                ``customer_id`` para activar el modo scoped.
            customer_id: Opcional. ID de ``res.partner`` del cliente. Requiere
                ``seller_id`` para activar el modo scoped.
        Returns:
            Diccionario con tres claves en éxito:
            - ``config``: parámetros del ciclo (cooldown, tope por vendedor,
              umbrales de inactividad, reglas de prioridad, etc.).
            - ``sellers``: lista de vendedores habilitados (uno en modo scoped).
            - ``customers``: lista de clientes (uno en modo scoped).
            En fallo de ownership scoped: ``message`` + ``reason``.
        """
        if seller_id is not None or customer_id is not None:
            if seller_id is None or customer_id is None:
                return self._wrap_mcp_response(
                    {
                        "message": (
                            "seller_id and customer_id must be provided together."
                        ),
                        "reason": "ownership_mismatch",
                    }
                )
            return self._wrap_mcp_response(
                self._scoped_bootstrap_reactivation_cycle(seller_id, customer_id)
            )

        config = self._get_config()
        enabled_sellers = self._filter_enabled_sellers()
        sellers = []
        customers = []
        for seller in enabled_sellers:
            sellers.append(self._serialize_bootstrap_seller(seller))
            customers.extend(
                self._collect_bootstrap_customers_for_seller(seller, config)
            )
        return self._wrap_mcp_response(
            {
                "config": self._serialize_config(config),
                "sellers": sellers,
                "customers": customers,
            }
        )
