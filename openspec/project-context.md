# Project Context — tommasi_sales_reactivation

Detected on 2026-08-13 by `sdd-init` from repository ground truth.
Persistence mode: `openspec` (file-only; Engram is not used).

Ground-truth source, tests, and configuration win over this document. Update this
context when implementation or repository conventions change.

## Identity

| Field | Value |
|-------|-------|
| Addon root | `/home/crisd/projects/tommasi/odoo/tommasi/odoo/custom/src/otros/tommasi_sales_reactivation` |
| Doodba root | `/home/crisd/projects/tommasi/odoo/tommasi` |
| Git remote | `git@github.com:crisdavico/tommasi-sales-reactivation.git` |
| Default branch | `main` |
| Odoo addon | `tommasi_sales_reactivation` |
| Addon version | `15.0.1.10.0` |
| License | LGPL-3 |

The addon is a standalone Git repository aggregated into the parent Doodba
project through `odoo/custom/src/repos.yaml`.

## Stack

- **Runtime:** Odoo 15, Python, PostgreSQL, and the Odoo ORM.
- **Packaging:** `whool` through the addon's minimal `pyproject.toml`.
- **MCP:** `llm_tool` and `llm_mcp_server`; tools are methods on the abstract
  `tommasi.reactivation.service` model.
- **Business modules:** CRM, Sales, Stock, Product, Accounting, Mail, and
  `tommasi_custom`.
- **Development environment:** Doodba with Invoke and Docker Compose.
- **Quality tooling:** parent pre-commit configuration with autoflake, Black,
  isort, Flake8, pylint-odoo, Prettier, and XML checks.

## Commands

Run development commands from `/home/crisd/projects/tommasi/odoo/tommasi`.

| Purpose | Command |
|---------|---------|
| Full addon tests | `invoke test -m tommasi_sales_reactivation` |
| Focused test file | `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_<area>.py` |
| Parent quality suite | `invoke lint` |
| Install addon | `invoke install -m tommasi_sales_reactivation` |
| Start Doodba | `invoke start` |

The test command starts Odoo with tests enabled and installs the requested addon.
Tests are not intended to run as standalone pytest tests.

## Architecture

`tommasi.reactivation.service` is split into focused model mixins:

| Path | Responsibility |
|------|----------------|
| `models/tommasi_reactivation_service.py` | Base service, constants, configuration helpers, stages, and MCP envelope |
| `models/service_bootstrap.py` / `service_sellers.py` | Cycle bootstrap and enabled-seller context |
| `models/service_candidates.py` | Seller portfolio candidate screening |
| `models/service_detection.py` / `service_facts.py` | Customer detection context and invoice facts |
| `models/service_opportunities.py` | Existing agent opportunity reads (`get_agent_opportunities`, `get_seller_open_opportunities`) |
| `models/service_recommendations.py` | Product recommendation ranking |
| `models/service_create_guards.py` | Authoritative CRM create guards, locking, savepoints, and v2 outcomes |
| `models/service_whatsapp.py` | Consent-aware outbound WhatsApp through the Chatwoot router |
| `models/service_rendering.py` / `service_products.py` | Internal rendering, product, stock, and pricing helpers |

Configuration is company-scoped. Enabled-seller and priority-rule child records
drive cycle behavior. `crm.lead` carries reactivation attribution and workflow
metadata. WhatsApp configuration and sanitized audit rows are separate models.

## MCP contract

The addon exposes eight `@llm_tool` methods:

1. `bootstrap_reactivation_cycle`
2. `get_reactivation_candidates`
3. `get_customer_detection_context`
4. `get_product_recommendations`
5. `create_crm_opportunity`
6. `get_agent_opportunities`
7. `get_seller_open_opportunities`
8. `send_whatsapp_to_partner`

Every tool returns this transport envelope:

```json
{"data": "<tool payload>", "request_id": "<optional>"}
```

Batch create requests carrying `operation_key` use contract version 2 and return
per-row outcomes: `created`, `existing`, `rejected`, or `deferred`. Legacy scalar
and batch requests without an operation key retain their version 1 inner shape.

Golden envelopes live in `tests/fixtures/contracts/`. Fixtures for tools consumed by
`/home/crisd/projects/tommasi/agents/tommasi-reactivation-agent` MUST remain
synchronized with that repo. `get_seller_open_opportunities` is a seller-facing
read tool with an **Odoo-only** golden; do not add a mirrored fixture in the
reactivation-agent repository.

## Security and side effects

- The MCP technical user belongs to the Reactivation Agent group.
- Seller-scoped calls pass `reactivation_seller_id` in the Odoo environment
  context. Missing scope intentionally yields no seller-owned records.
- Record rules restrict partners, leads, orders, and invoices to the scoped
  seller and preserve company isolation.
- Agent-side deduplication, caps, and stock checks reduce work only. Odoo is
  authoritative and revalidates open opportunities, cooldown, seller workload
  cap, ownership, and stock while creating CRM records.
- CRM batch writes isolate rows with savepoints and use locking/idempotency
  controls where concurrent cycles can race.
- WhatsApp sends require consent, E.164 mobile numbers, company matching, scoped
  credentials, five-header HMAC authentication, and sanitized audit logging.

## Testing

`tests/test_*.py` provides post-install Odoo coverage for:

- configuration, seeded priorities, CRM metadata, and custom stages;
- seller and company security isolation;
- cycle bootstrap, detection facts, candidate screening, and query bounds;
- scalar and batch recommendations, reads, and CRM creates;
- write-time safety, cooldown, cap, stock, locking, and idempotency;
- rendering and demo scenario behavior;
- WhatsApp consent, HMAC transport, failures, and audit sanitization;
- MCP envelope shape and cross-repository golden fixture parity.

Strict TDD is enabled because this behavior suite is established and runnable.
There is no separate browser E2E or coverage command.

## Cross-repository ownership

The LangGraph consumer is
`/home/crisd/projects/tommasi/agents/tommasi-reactivation-agent`.
Changes to tool names, arguments, response envelopes, outcome semantics, or
golden fixtures for tools the reactivation agent calls require coordinated
agent-side updates. Seller-facing tools that the reactivation agent does not
call (currently `get_seller_open_opportunities`) may ship Odoo-only. Odoo
remains the source of truth for data access and CRM write safety.

## Known gaps

1. `README.md` references `prds/tommasi-reactivation-odoo-prd.md`, but no local
   `prds/` directory is present.
2. The parent path `.cursor/skills/odoo-15.0/SKILLS.md` currently identifies
   itself as an Odoo 19 guide, so it must not be treated as authoritative Odoo 15
   API guidance without correction.
3. Unit and integration behavior share the same Odoo post-install runner; there
   is no independently runnable fast unit layer.
