# Proposal: Seller Open Opportunities MCP Tool

## Intent

Add a read-only MCP tool that lists every **open agent CRM opportunity** for one seller, using the same open-stage definition as create-time cap counting, with a seller-facing row payload (no IDs).

## Scope

### In Scope

- New `@llm_tool` `get_seller_open_opportunities` on `tommasi.reactivation.service`.
- Open means: `reactivation_is_agent=True`, `active=True`, `user_id=seller_id`, stages **Pendiente de revisión** and **Cliente contactado** (`OPEN_REACTIVATION_STAGE_XML_IDS`).
- Shared seller-wide open-agent domain helper aligned with `_count_open_agent_opportunities`.
- Seller-facing rows: `customer_name`, `stage`, `created_at`, `opportunity_url`, `client_message` (always present; empty string when missing).
- Odoo golden fixture, MCP contract keys, transport + batch behavior tests, README / project-context, manifest bump to `15.0.1.10.0`.

### Out of Scope

- LangGraph wrappers, agent `CONTRACT_TOOL_NAMES`, or a mirrored golden in `tommasi-reactivation-agent`.
- Changing `get_agent_opportunities` (still requires exactly one of `customer_id` / `customer_ids`).
- Pagination, stage overrides, `include_client_message` flag, evidence summary, or IDs in the payload.

**MCP / contract:** **does** add a new tool and Odoo-only golden. It does **not** change existing envelopes or require a reactivation-agent fixture mirror.

**Ownership:** agent (seller calling agent, later) reads; Odoo remains authoritative for seller scope and open-stage definition.

## Capabilities

### New Capabilities

- `seller-open-opportunities`: seller-wide open agent CRM opportunity list.

### Modified Capabilities

- None

## Approach

New tool, not an extension of `get_agent_opportunities`. Extract `_open_agent_opportunities_domain` (no `partner_ids`) so list and cap-count stay aligned. Search with `_env_with_seller` so reactivation record rules apply. Serialize with a dedicated five-field helper; reuse `_crm_lead_form_url`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `models/service_opportunities.py` | Modified | Domain helper, serializer, `@llm_tool` |
| `models/service_create_guards.py` | Modified | Cap count uses the shared domain |
| `tests/fixtures/contracts/` | Added | Odoo-only golden (not mirrored) |
| `tests/mcp_contract.py` | Modified | Tool name + required keys |
| `tests/test_mcp_transport_contract.py` | Modified | Envelope lock |
| `tests/test_reactivation_batch.py` | Modified | Behavior coverage |
| README, project-context, `__manifest__.py` | Modified | Eight tools; version `15.0.1.10.0` |
| `tommasi-reactivation-agent` | Unchanged | No wrapper, no golden |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Drift vs create-time open count | Med | Shared domain helper |
| Cross-seller leak | Med | `_env_with_seller` + `user_id` predicate; leak tests |
| Accidental reuse of agent-row serializer | Low | Dedicated `_serialize_seller_open_opportunity` |
| Reactivation-agent fixture parity confusion | Low | Document Odoo-only; do not add agent golden |

## Rollback Plan

Remove the tool, helper, serializer, golden, tests, and docs. Restore cap-count inline domain. No CRM writes; existing leads unchanged. `get_agent_opportunities` stays as today.

## Dependencies

`OPEN_REACTIVATION_STAGE_XML_IDS`, `_env_with_seller`, `_crm_lead_form_url`, `_wrap_mcp_response`.

## Success Criteria

- [ ] `get_seller_open_opportunities(seller_id)` returns a `{data, request_id?}` envelope with a flat `opportunities` list ordered by `create_date desc`.
- [ ] Empty portfolio is success (`opportunities: []`).
- [ ] Non-agent, archived, Won/Lost, and other-seller leads are excluded.
- [ ] Each row always includes `client_message` and never `opportunity_id` / `customer_id`.
- [ ] `get_agent_opportunities` still errors if neither customer id is given.
