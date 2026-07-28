from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.llm_tool.decorators import llm_tool

CREATE_OUTCOME_CREATED = "created"
CREATE_OUTCOME_EXISTING = "existing"
CREATE_OUTCOME_REJECTED = "rejected"
CREATE_OUTCOME_DEFERRED = "deferred"
CREATE_CONTRACT_VERSION = 2


class TommasiReactivationCreateGuards(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _build_reactivation_operation_key(self, cycle_id, seller_id, customer_id):
        """Return a stable idempotency key for one cycle/seller/customer tuple."""
        cycle = str(cycle_id or "").strip()
        if not cycle:
            raise ValueError("cycle_id is required to build operation_key.")
        return "%s|seller:%s|customer:%s" % (cycle, seller_id, customer_id)

    def _open_reactivation_stage_ids(self, seller_env):
        """Resolve open reactivation CRM stages by XML ID (not display name)."""
        return self._resolve_open_stage_ids(seller_env=seller_env)

    def _lead_model_for_seller(self, seller_id):
        seller_env = self._env_with_seller(seller_id)
        config = self._get_config()
        company = config.company_id
        seller = seller_env["res.users"].browse(seller_id)
        if company.id not in seller.company_ids.ids:
            seller.sudo().write({"company_ids": [(4, company.id)]})
            seller.invalidate_cache(["company_ids"], seller.ids)
        lead_model = seller_env["crm.lead"]
        if company.id not in seller.company_ids.ids:
            lead_model = lead_model.sudo()
        return lead_model.with_company(company)

    def _lock_customer_guard_scope(self, customer_id):
        """Serialize concurrent create attempts for the same customer."""
        self.env.cr.execute(
            "SELECT id FROM res_partner WHERE id = %s FOR UPDATE",
            (customer_id,),
        )

    def _find_lead_by_operation_key(self, operation_key, seller_id):
        Lead = self._lead_model_for_seller(seller_id)
        return Lead.search(
            [("reactivation_operation_key", "=", operation_key)],
            limit=1,
        )

    def _count_open_agent_opportunities(self, seller_id):
        Lead = self._lead_model_for_seller(seller_id)
        seller_env = self._env_with_seller(seller_id)
        open_stage_ids = self._open_reactivation_stage_ids(seller_env)
        domain = [
            ("user_id", "=", seller_id),
            ("reactivation_is_agent", "=", True),
            ("active", "=", True),
        ]
        if open_stage_ids:
            domain.append(("stage_id", "in", open_stage_ids))
        return Lead.search_count(domain)

    def _customer_has_open_agent_opportunity(self, customer_id, seller_id):
        Lead = self._lead_model_for_seller(seller_id)
        seller_env = self._env_with_seller(seller_id)
        open_stage_ids = self._open_reactivation_stage_ids(seller_env)
        domain = [
            ("partner_id", "=", customer_id),
            ("user_id", "=", seller_id),
            ("reactivation_is_agent", "=", True),
            ("active", "=", True),
        ]
        if open_stage_ids:
            domain.append(("stage_id", "in", open_stage_ids))
        return bool(Lead.search(domain, limit=1))

    def _latest_agent_opportunity(self, customer_id, seller_id, *, include_inactive):
        Lead = self._lead_model_for_seller(seller_id)
        if include_inactive:
            Lead = Lead.with_context(active_test=False)
        domain = [
            ("partner_id", "=", customer_id),
            ("user_id", "=", seller_id),
            ("reactivation_is_agent", "=", True),
        ]
        if not include_inactive:
            domain.append(("active", "=", True))
        return Lead.search(domain, order="create_date desc", limit=1)

    def _within_reactivation_cooldown(self, lead, cooldown_days):
        if not lead or cooldown_days < 0:
            return False
        create_date = lead.create_date
        if not create_date:
            return False
        cutoff = fields.Datetime.now() - timedelta(days=cooldown_days)
        return fields.Datetime.to_datetime(create_date) >= cutoff

    def _v2_create_result(
        self,
        outcome,
        *,
        customer_id,
        operation_key=None,
        opportunity_id=None,
        attribution_id=None,
        reason=None,
        message=None,
    ):
        result = {
            "outcome": outcome,
            "customer_id": customer_id,
        }
        if operation_key:
            result["operation_key"] = operation_key
        if opportunity_id:
            result["opportunity_id"] = opportunity_id
        if attribution_id:
            result["attribution_id"] = attribution_id
        if reason:
            result["reason"] = reason
        if message:
            result["message"] = message
        return result

    def _wrap_v2_batch_results(self, results):
        return {
            "contract_version": CREATE_CONTRACT_VERSION,
            "results": results,
        }

    def _payload_uses_v2_contract(self, payload):
        operation_key = (payload or {}).get("operation_key")
        return bool(operation_key and str(operation_key).strip())

    def _enforce_create_safety_guards(self, data):
        """Authoritative open/cooldown/cap checks before CRM create."""
        seller_id = data["seller_id"]
        customer_id = data["customer_id"]
        operation_key = data["operation_key"]
        config = self._get_config()

        self._lock_customer_guard_scope(customer_id)

        existing = self._find_lead_by_operation_key(operation_key, seller_id)
        if existing:
            return self._v2_create_result(
                CREATE_OUTCOME_EXISTING,
                customer_id=customer_id,
                operation_key=operation_key,
                opportunity_id=existing.id,
                attribution_id=existing.reactivation_attribution_id,
                reason="idempotent_replay",
            )

        if self._customer_has_open_agent_opportunity(customer_id, seller_id):
            return self._v2_create_result(
                CREATE_OUTCOME_REJECTED,
                customer_id=customer_id,
                operation_key=operation_key,
                reason="open_opportunity",
                message=(
                    "Customer already has an open agent reactivation opportunity."
                ),
            )

        latest = self._latest_agent_opportunity(
            customer_id,
            seller_id,
            include_inactive=True,
        )
        if self._within_reactivation_cooldown(latest, config.cooldown_days):
            return self._v2_create_result(
                CREATE_OUTCOME_REJECTED,
                customer_id=customer_id,
                operation_key=operation_key,
                reason="cooldown_active",
                message=(
                    "Customer is within the reactivation cooldown window "
                    "(%s days)." % config.cooldown_days
                ),
            )

        open_count = self._count_open_agent_opportunities(seller_id)
        if open_count >= config.opportunity_cap_per_seller:
            return self._v2_create_result(
                CREATE_OUTCOME_REJECTED,
                customer_id=customer_id,
                operation_key=operation_key,
                reason="seller_cap_exceeded",
                message=(
                    "Seller has reached the open reactivation opportunity cap "
                    "(%s)." % config.opportunity_cap_per_seller
                ),
            )

        return None

    def _handle_create_integrity_error(self, operation_key, seller_id, customer_id):
        """Map unique operation_key races to an existing outcome."""
        existing = self._find_lead_by_operation_key(operation_key, seller_id)
        if existing:
            return self._v2_create_result(
                CREATE_OUTCOME_EXISTING,
                customer_id=customer_id,
                operation_key=operation_key,
                opportunity_id=existing.id,
                attribution_id=existing.reactivation_attribution_id,
                reason="idempotent_replay",
            )
        return self._v2_create_result(
            CREATE_OUTCOME_DEFERRED,
            customer_id=customer_id,
            operation_key=operation_key,
            reason="integrity_conflict",
            message="Create conflict; retry with the same operation_key.",
        )

    def _create_with_safety_guards(self, data, payload, stage, config, stock_batch):
        """Create a lead after guards, mapping integrity races to v2 outcomes."""
        operation_key = data.get("operation_key")
        attribution_id = self._next_reactivation_attribution_id()
        try:
            lead = self._create_reactivation_lead_record(
                data,
                payload,
                stage,
                attribution_id,
                config,
                stock_batch,
                operation_key=operation_key,
            )
        except IntegrityError:
            return self._handle_create_integrity_error(
                operation_key,
                data["seller_id"],
                data["customer_id"],
            )
        return self._v2_create_result(
            CREATE_OUTCOME_CREATED,
            customer_id=data["customer_id"],
            operation_key=operation_key,
            opportunity_id=lead.id,
            attribution_id=attribution_id,
        )

    # ------------------------------------------------------------------
    # MCP write tools — helpers (not MCP tools)
    # ------------------------------------------------------------------

    def _resolve_priority_from_label(self, label):
        mapping = {"low": "1", "medium": "2", "high": "3"}
        key = (label or "").strip().lower()
        if key not in mapping:
            raise ValueError("Invalid priority label: %s" % label)
        return mapping[key]

    def _validate_opportunity_product_stock(
        self, suggested_products, stock_batch, config=None
    ):
        """Ensure each suggested product is eligible and above minimum stock."""
        config = config or self._get_config()
        low_stock_threshold = config.low_stock_threshold
        for product in suggested_products:
            product_id = product.get("product_id")
            if not product_id:
                continue
            stock_info = stock_batch.get(product_id, {})
            sku = product.get("sku") or product.get("name") or str(product_id)
            if not stock_info.get("eligible"):
                return {
                    "message": (
                        "Product %s must be active with type product (storable)." % sku
                    ),
                }
            available_qty = stock_info.get("available_qty", 0.0)
            if available_qty <= 0:
                return {
                    "message": "Product %s is out of stock." % sku,
                }
            if not self._meets_recommendation_stock(available_qty, low_stock_threshold):
                return {
                    "message": (
                        "Product %s has insufficient stock "
                        "(available: %s, minimum required: %s)."
                        % (sku, available_qty, low_stock_threshold + 1)
                    ),
                }
        return None

    def _next_reactivation_attribution_id(self):
        """Allocate the next reactivation attribution sequence value."""
        attribution_id = (
            self.env["ir.sequence"]
            .sudo()
            .next_by_code("tommasi.reactivation.attribution")
        )
        if not attribution_id:
            raise UserError(_("Reactivation attribution sequence is not configured."))
        return attribution_id

    def _create_reactivation_lead_record(
        self,
        data,
        payload,
        stage,
        attribution_id,
        config,
        stock_batch,
        operation_key=None,
    ):
        """Create the CRM lead for a validated reactivation opportunity."""
        partner = self._unified_env(seller_id=data["seller_id"])["res.partner"].browse(
            data["customer_id"]
        )
        description_payload = dict(payload or {}, **data)
        description_payload[
            "suggested_products"
        ] = self._enrich_suggested_products_pricing(
            data["customer_id"],
            description_payload.get("suggested_products") or [],
            seller_id=data["seller_id"],
        )
        description_payload["client_message"] = self._format_client_message_prices(
            data["client_message"],
            description_payload["suggested_products"],
            config=config,
        )
        company = config.company_id
        lead_model = self._lead_model_for_seller(data["seller_id"])
        create_vals = {
            "name": "%s - Reactivation" % partner.name,
            "type": "opportunity",
            "partner_id": data["customer_id"],
            "user_id": data["seller_id"],
            "company_id": company.id,
            "stage_id": stage.id,
            "priority": data["priority"],
            "description": self._render_opportunity_description(
                description_payload, config=config, stock_batch=stock_batch
            ),
            "reactivation_is_agent": True,
            "reactivation_source": data["source"],
            "reactivation_attribution_id": attribution_id,
            "reactivation_trigger_type": data.get("trigger_type"),
            "reactivation_confidence": data.get("confidence"),
            "reactivation_cycle_id": data.get("cycle_id"),
            "reactivation_client_message": description_payload["client_message"],
        }
        if operation_key:
            create_vals["reactivation_operation_key"] = operation_key
        return lead_model.with_context(mail_create_nosubscribe=True).create(create_vals)

    def _create_crm_opportunity_single(self, payload):
        """Create one CRM opportunity; return success or ``{message}`` dict."""
        use_v2 = self._payload_uses_v2_contract(payload)
        error, data = self._validate_create_opportunity_payload(payload)
        if error:
            if use_v2:
                customer_id = (payload or {}).get("customer_id")
                return self._v2_create_result(
                    "rejected",
                    customer_id=customer_id,
                    operation_key=(payload or {}).get("operation_key"),
                    reason="validation_error",
                    message=error,
                )
            return {"message": error}
        if use_v2:
            data["operation_key"] = data.get("operation_key") or (payload or {}).get(
                "operation_key"
            )
            guard_result = self._enforce_create_safety_guards(data)
            if guard_result:
                return guard_result
        config = self._get_config()
        product_ids = [
            product["product_id"]
            for product in data["suggested_products"]
            if product.get("product_id")
        ]
        stock_batch = self._get_product_stock_batch(product_ids, config=config)
        stock_error = self._validate_opportunity_product_stock(
            data["suggested_products"], stock_batch, config=config
        )
        if stock_error:
            if use_v2:
                return self._v2_create_result(
                    "rejected",
                    customer_id=data["customer_id"],
                    operation_key=data.get("operation_key"),
                    reason="stock_unavailable",
                    message=stock_error.get("message"),
                )
            return stock_error
        stage = self._resolve_stage_by_xml_id(
            "tommasi_sales_reactivation.stage_pendiente_revision"
        )
        if not stage:
            message = _("Reactivation stage Pendiente de revisión is not configured.")
            if use_v2:
                return self._v2_create_result(
                    "deferred",
                    customer_id=data["customer_id"],
                    operation_key=data.get("operation_key"),
                    reason="stage_not_configured",
                    message=str(message),
                )
            raise UserError(message)
        if use_v2:
            return self._create_with_safety_guards(
                data,
                payload,
                stage,
                config,
                stock_batch,
            )
        attribution_id = self._next_reactivation_attribution_id()
        lead = self._create_reactivation_lead_record(
            data, payload, stage, attribution_id, config, stock_batch
        )
        return {
            "opportunity_id": lead.id,
            "attribution_id": attribution_id,
        }

    @llm_tool(read_only_hint=False)
    def create_crm_opportunity(
        self, payload: dict = None, payloads: list = None
    ) -> dict:
        """Crea una oportunidad CRM de reactivación en ``Pendiente de revisión``.

        Es el **único write tool** del agente de detección para persistir una
        oportunidad comercial. Valida el payload, re-confirma stock en servidor
        y genera la descripción HTML estructurada (D-05).

        **Formas de entrada (mutuamente excluyentes)**
        - Escalar: ``payload`` (una oportunidad).
        - Batch: ``payloads`` (lista de diccionarios). En el flujo batch v1.9,
          crear varias oportunidades del mismo vendedor en una sola llamada;
          fallos de validación aislados por payload (AC-14).

        **Validaciones server-side**
        - Cliente y vendedor existen; el cliente está asignado al vendedor.
        - Vendedor habilitado en la configuración de reactivación.
        - ``client_message`` no vacío; ``priority_label`` en ``low``/``medium``/``high``.
        - Cada SKU sugerido con stock por encima de ``low_stock_threshold`` al crear.

        **Ejemplo de payload (recortado)**
        ```json
        {
          "customer_id": 1001,
          "seller_id": 42,
          "client_message": "Hola, le escribo de Tommasi...",
          "priority_label": "high",
          "source": "Agente Comercial",
          "trigger_type": "inactivity",
          "confidence": 0.85,
          "cycle_id": "2025-06-24-daily",
          "suggested_products": [
            {
              "product_id": 550,
              "sku": "ACE-001",
              "name": "Aceite girasol 900ml",
              "list_price": 1000.0,
              "available_qty": 42.0
            }
          ]
        }
        ```

        **Ejemplo de respuesta (escalar)**
        ```json
        {
          "opportunity_id": 9001,
          "attribution_id": "REACT-2025-00042"
        }
        ```

        **Ejemplo de llamada (batch)**
        ```
        create_crm_opportunity(payloads=[{...}, {...}, {...}])
        ```
        Respuesta:
        ```json
        {
          "results": [
            {
              "opportunity_id": 9001,
              "attribution_id": "REACT-2025-00042",
              "customer_id": 1001
            },
            {
              "message": "Product ACE-002 is out of stock.",
              "customer_id": 1002
            },
            {
              "opportunity_id": 9003,
              "attribution_id": "REACT-2025-00044",
              "customer_id": 1003
            }
          ]
        }
        ```
        Un fallo de validación en un payload no aborta las creaciones hermanas.

        Args:
            payload: Diccionario con datos de la oportunidad a crear (forma escalar).
            payloads: Lista de diccionarios de oportunidad (forma batch).
        Returns:
            Forma escalar: ``{opportunity_id, attribution_id}`` o ``{message}``.
            Forma batch: ``{results: [{opportunity_id, attribution_id, customer_id}
            | {message, customer_id}]}`` — una entrada por payload, en orden.
            Si faltan o se mezclan ``payload`` y ``payloads``:
            ``{message: "Provide exactly one of payload or payloads."}``.
        """
        mode, error = self._exclusive_scalar_or_batch(
            payload,
            payloads,
            "Provide exactly one of payload or payloads.",
        )
        if error:
            return self._wrap_mcp_response(error)

        if mode == "scalar":
            return self._wrap_mcp_response(self._create_crm_opportunity_single(payload))

        results = []
        batch_uses_v2 = any(self._payload_uses_v2_contract(item) for item in payloads)
        for item_payload in payloads:
            customer_id = (item_payload or {}).get("customer_id")
            try:
                with self.env.cr.savepoint():
                    result = self._create_crm_opportunity_single(item_payload)
            except UserError as exc:
                result = {"message": str(exc)}

            if result.get("outcome"):
                results.append(result)
            elif result.get("message"):
                results.append(
                    {
                        "message": result["message"],
                        "customer_id": customer_id,
                    }
                )
            else:
                results.append(
                    {
                        "opportunity_id": result["opportunity_id"],
                        "attribution_id": result["attribution_id"],
                        "customer_id": customer_id,
                    }
                )
        if batch_uses_v2:
            return self._wrap_mcp_response(self._wrap_v2_batch_results(results))
        return self._wrap_mcp_response({"results": results})
