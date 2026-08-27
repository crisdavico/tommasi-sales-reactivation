# Tasks: Seller Open Opportunities MCP Tool

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 280–360 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Delivery strategy | single-pr |

Decision needed before apply: No
Chained PRs recommended: No
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Focused test command | Runtime harness | Rollback boundary |
|------|------|----------------------|-----------------|-------------------|
| 1 | OpenSpec + golden + contract keys + transport envelope | `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_mcp_transport_contract.py` | Odoo TransactionCase via Docker `invoke test` from Doodba root | `openspec/changes/add-seller-open-opportunities-tool/`; `tests/fixtures/contracts/get_seller_open_opportunities.json`; `tests/mcp_contract.py`; transport test |
| 2 | Domain helper, serializer, tool, cap-count alignment, behavior tests | `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_batch.py` | Same Odoo harness | `models/service_opportunities.py`; `_count_open_agent_opportunities` in `models/service_create_guards.py`; batch tests |
| 3 | README, project-context, config.yaml, manifest `15.0.1.10.0` | Docs-only; reuse unit 1–2 commands | N/A — no runtime boundary | README, `openspec/project-context.md`, `openspec/config.yaml`, `__manifest__.py` |

MCP fixture parity: Odoo golden only. Do **not** add `tommasi-reactivation-agent` fixture. Threat matrix N/A: read-only tool.

## Phase 1: Contract (TDD)

- [x] 1.1 Add `tests/fixtures/contracts/get_seller_open_opportunities.json`.
- [x] 1.2 Register `get_seller_open_opportunities` in `CONTRACT_TOOL_NAMES` and `TOOL_REQUIRED_DATA_KEYS` (`seller_id`, `opportunities`).
- [x] 1.3 RED: envelope lock in `tests/test_mcp_transport_contract.py` (empty list is success; optional `request_id`).
- [x] 1.4 Prove: `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_mcp_transport_contract.py`.

## Phase 2: Tool behavior (TDD)

- [x] 2.1 RED: in `tests/test_reactivation_batch.py` add tests for flat list across customers, exclusions (non-agent / archived / other-seller / non-open stage), empty list, always-present `client_message`, no ID leak, and no cross-seller leak under record rules.
- [x] 2.2 GREEN: extract `_open_agent_opportunities_domain`, add `_serialize_seller_open_opportunity`, add `@llm_tool get_seller_open_opportunities`. Point `_count_open_agent_opportunities` at the shared domain. Do not change `get_agent_opportunities`.
- [x] 2.3 Prove: `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_batch.py`.

## Phase 3: Docs

- [x] 3.1 Update `README.md` and `openspec/project-context.md` (eight MCP tools, new table row, envelope example). Bump `__manifest__.py` and OpenSpec config version to `15.0.1.10.0`.
- [x] 3.2 Note the golden is Odoo-only (not mirrored to the reactivation agent).
