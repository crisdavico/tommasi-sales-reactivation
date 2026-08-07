from collections import defaultdict

from odoo import fields, models

from odoo.addons.llm_tool.decorators import llm_tool


class TommasiReactivationServiceOpportunities(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _agent_opportunities_domain(
        self,
        seller_env,
        partner_ids,
        seller_id,
        statuses=None,
        *,
        stage_ids=None,
        include_inactive=False,
    ):
        """Build crm.lead domain for one or many scoped customers."""
        if isinstance(partner_ids, int):
            partner_clause = ("partner_id", "=", partner_ids)
        else:
            partner_clause = ("partner_id", "in", list(partner_ids))
        domain = [
            partner_clause,
            ("user_id", "=", seller_id),
            ("reactivation_is_agent", "=", True),
        ]
        if not include_inactive:
            domain.append(("active", "=", True))
        if stage_ids and not include_inactive:
            domain.append(("stage_id", "in", list(stage_ids)))
        elif statuses and not include_inactive:
            resolved = self._resolve_open_stage_ids(
                seller_env=seller_env,
                statuses=statuses,
            )
            if resolved:
                domain.append(("stage_id", "in", resolved))
        return domain

    def _serialize_agent_opportunity(
        self, lead, include_client_message=False, stage_xml_id_map=None
    ):
        """Return one opportunity row for agent read tools."""
        row = {
            "opportunity_id": lead.id,
            "attribution_id": lead.reactivation_attribution_id,
            "stage": lead.stage_id.name,
            "stage_id": lead.stage_id.id,
            "stage_xml_id": self._stage_xml_id_for(
                lead.stage_id, stage_xml_id_map=stage_xml_id_map
            ),
            "created_at": fields.Datetime.to_string(lead.create_date),
            "last_stage_change_at": fields.Datetime.to_string(lead.write_date),
            "opportunity_url": self._crm_lead_form_url(lead.id),
        }
        if include_client_message:
            row["client_message"] = lead.reactivation_client_message or ""
            row["evidence_summary"] = lead.reactivation_evidence_summary or ""
        return row

    def _opportunities_for_customer(
        self,
        customer_id,
        seller_id,
        statuses=None,
        include_client_message=False,
        seller_env=None,
        *,
        stage_ids=None,
        include_inactive=False,
    ):
        """Return serialized opportunities for one customer in seller scope."""
        seller_env = seller_env or self._env_with_seller(seller_id)
        Lead = seller_env["crm.lead"]
        domain = self._agent_opportunities_domain(
            seller_env,
            customer_id,
            seller_id,
            statuses=statuses,
            stage_ids=stage_ids,
            include_inactive=include_inactive,
        )
        if include_inactive:
            Lead = Lead.with_context(active_test=False)
        leads = Lead.search(domain, order="create_date desc")
        return [
            self._serialize_agent_opportunity(lead, include_client_message)
            for lead in leads
        ]

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def get_agent_opportunities(
        self,
        customer_id: int = None,
        customer_ids: list = None,
        *,
        seller_id: int,
        statuses: list = None,
        stage_xml_ids: list = None,
        stage_ids: list = None,
        include_client_message: bool = False,
        dedup_mode: bool = False,
        contract_version: int = None,
        request_id: str = None,
    ) -> dict:
        """Lista oportunidades de reactivación abiertas de un cliente y vendedor.

        Sirve para deduplicación en detección: evitar duplicar oportunidades si ya
        existe una abierta (control de cooldown y tope por vendedor). En el flujo
        batch v1.9, consultar varios clientes del mismo vendedor en una sola llamada.

        **Formas de entrada (mutuamente excluyentes)**
        - Escalar: ``customer_id`` (un cliente).
        - Batch: ``customer_ids`` (lista de IDs). Un solo ``crm.lead`` search
          para todos los clientes válidos; errores de alcance aislados por cliente.

        **Filtros aplicados**
        - Solo oportunidades creadas por el agente (``reactivation_is_agent = true``).
        - Solo oportunidades activas del cliente y vendedor indicados.
        - ``statuses``: nombres de etapa CRM, ej.
          ``["Pendiente de revisión", "Cliente contactado"]``.

        **Ejemplo: deduplicación en detección (escalar)**
        ```
        get_agent_opportunities(
            customer_id=1001,
            seller_id=42,
            statuses=["Pendiente de revisión", "Cliente contactado"],
            include_client_message=false
        )
        ```
        Si devuelve una oportunidad, el agente **no** debe crear otra para ese cliente.

        **Ejemplo: deduplicación en detección (batch)**
        ```
        get_agent_opportunities(
            customer_ids=[1001, 1002, 1003],
            seller_id=42,
            statuses=["Pendiente de revisión", "Cliente contactado"]
        )
        ```
        Respuesta:
        ```json
        {
          "seller_id": 42,
          "results": {
            "1001": {"opportunities": [{"opportunity_id": 9001, "stage": "Pendiente de revisión"}]},
            "1002": {"opportunities": []},
            "1003": {"message": "Customer not found for seller scope."}
          }
        }
        ```
        Un cliente sin oportunidades devuelve ``opportunities: []`` (no es error).
        Un error de alcance en un cliente no aborta a los demás.

        Args:
            customer_id: ID de ``res.partner`` del cliente (forma escalar).
            customer_ids: Lista de IDs de ``res.partner`` (forma batch).
            seller_id: ID de ``res.users`` del vendedor asignado.
            statuses: Lista de nombres de etapas CRM a incluir.
            include_client_message: Si es ``true``, agrega el mensaje al cliente
                y el ``evidence_summary`` del lead, tal como fueron guardados al
                crear la oportunidad.
        Returns:
            Forma escalar: ``{customer_id, seller_id, opportunities[]}``.
            Forma batch: ``{seller_id, results: {"<customer_id>": {opportunities[]}
            | {message}}}``.
            Si faltan o se mezclan ``customer_id`` y ``customer_ids``:
            ``{message: "Provide exactly one of customer_id or customer_ids."}``.
        """
        mode, error = self._exclusive_scalar_or_batch(
            customer_id,
            customer_ids,
            "Provide exactly one of customer_id or customer_ids.",
        )
        if error:
            return self._wrap_mcp_response(error, request_id=request_id)

        resolved_stage_ids = self._resolve_open_stage_ids(
            stage_xml_ids=stage_xml_ids,
            stage_ids=stage_ids,
            statuses=statuses,
        )

        if mode == "scalar":
            seller_env = self._env_with_seller(seller_id)
            opportunities = self._opportunities_for_customer(
                customer_id,
                seller_id,
                statuses=statuses,
                include_client_message=include_client_message,
                seller_env=seller_env,
                stage_ids=resolved_stage_ids,
                include_inactive=dedup_mode,
            )
            return self._wrap_mcp_response(
                {
                    "customer_id": customer_id,
                    "seller_id": seller_id,
                    "opportunities": opportunities,
                },
                request_id=request_id,
            )

        seller_env = self._env_with_seller(seller_id)
        Lead = seller_env["crm.lead"]
        results = {}
        valid_customer_ids = []
        for scoped_customer_id in customer_ids:
            customer_key = str(scoped_customer_id)
            _partner, _seller_env, error = self._seller_env_for_customer(
                scoped_customer_id, seller_id
            )
            if error:
                results[customer_key] = {"message": error}
                continue
            valid_customer_ids.append(scoped_customer_id)

        leads_by_customer = defaultdict(list)
        all_leads = Lead.browse([])
        if valid_customer_ids:
            domain = self._agent_opportunities_domain(
                seller_env,
                valid_customer_ids,
                seller_id,
                statuses=statuses,
                stage_ids=resolved_stage_ids,
                include_inactive=dedup_mode,
            )
            lead_search = Lead
            if dedup_mode:
                lead_search = Lead.with_context(active_test=False)
            all_leads = lead_search.search(domain, order="create_date desc")
            for lead in all_leads:
                leads_by_customer[lead.partner_id.id].append(lead)

        stage_xml_id_map = self._stage_xml_id_map_for(all_leads.mapped("stage_id"))
        for scoped_customer_id in valid_customer_ids:
            customer_key = str(scoped_customer_id)
            opportunities = [
                self._serialize_agent_opportunity(
                    lead,
                    include_client_message,
                    stage_xml_id_map=stage_xml_id_map,
                )
                for lead in leads_by_customer.get(scoped_customer_id, [])
            ]
            results[customer_key] = {"opportunities": opportunities}

        return self._wrap_mcp_response(
            {"seller_id": seller_id, "results": results},
            request_id=request_id,
        )
