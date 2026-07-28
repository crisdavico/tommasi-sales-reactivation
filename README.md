# Tommasi Sales Reactivation

**Module version**: `15.0.1.4.0` (v1.11 — generic partner WhatsApp outbound via Chatwoot router)

Odoo 15 addon that powers the Tommasi sales reactivation LangGraph agent: configuration, CRM customization, seller-scoped security, and seven MCP tools exposed through the integrated Odoo MCP server.

LangGraph detection agents call this module's `llm.tool` surface — they never access the Odoo database directly.

## Service layout

`tommasi.reactivation.service` is split across focused mixins (post v1.10 refactor):

| Module | Responsibility |
|--------|----------------|
| `tommasi_reactivation_service.py` | Base constants, config helpers, stage resolution, MCP envelope |
| `service_candidates.py` | Portfolio screening (`get_reactivation_candidates`) |
| `service_detection.py` | Per-customer detection context |
| `service_facts.py` | Invoice aggregates, sales/product history, batch invoice facts |
| `service_opportunities.py` | Open opportunities read |
| `service_recommendations.py` | Product ranking and batch recommendations |
| `service_create_guards.py` | CRM create with savepoints, row locks, v2 outcomes |
| `service_whatsapp.py` | Partner WhatsApp outbound via Chatwoot router (`send_whatsapp_to_partner`) |
| `service_products.py` / `service_rendering.py` | Stock, pricelist, digest copy (internal) |
| `tommasi_reactivation_facts_snapshot.py` | Nightly facts materialization cron |

## Overview

| Area | What it provides |
|------|------------------|
| **Configuration** | Per-company singleton with operational parameters, enabled sellers, and priority rules |
| **CRM** | Custom pipeline stages and reactivation metadata on `crm.lead` |
| **MCP tools** | Seven `@llm_tool` methods on `tommasi.reactivation.service` for cycle bootstrap, candidate pre-filtering, detection, recommendations, CRM writes, and partner WhatsApp outbound. Four tools accept retrocompatible batch parameters (`include_context`, `customer_ids[]`, `payloads[]`) to reduce detection-cycle MCP calls from O(customers) to O(sellers) |
| **Facts snapshot** | Nightly `ir.cron` materializes invoice aggregates into `tommasi.reactivation.facts.snapshot`; read tools prefer fresh snapshots and fall back to live SQL |
| **Security** | *Reactivation Agent* group with seller-scoped record rules driven by `reactivation_seller_id` in context |

## Prerequisites

Install and configure these modules first:

`base`, `mail`, `crm`, `sale`, `sale_crm`, `sale_management`, `stock`, `product`, `account`, `llm_tool`, `llm_mcp_server`, `tommasi_custom`

> [!IMPORTANT]
> Sellers included in agent cycles are those with a line in **Sales reactivation → Enabled sellers**. Mobile numbers on the partner record are optional metadata exposed in bootstrap.

## Configuration

Open **LLM → Configuration → Sales reactivation** (requires *LLM Manager*).

WhatsApp outbound routing is configured separately under **LLM → Configuration → WhatsApp** (`tommasi.whatsapp.config`, one active row per company). It stores the Chatwoot router base URL, outbound API key, Chatwoot account/inbox IDs, and HTTP timeout. This is independent from sales reactivation settings.

The singleton `tommasi.reactivation.config` record controls:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `cooldown_days` | 30 | Minimum days before creating another opportunity for the same customer |
| `opportunity_cap_per_seller` | 20 | Max open reactivation opportunities per seller |
| `inactivity_days_primary` / `secondary` | 30 / 45 | Inactivity trigger thresholds |
| `suggested_products_max` | 5 | Maximum products suggested per opportunity |
| `min_confidence_threshold` | 0.50 | Minimum detection confidence to create an opportunity |
| `reminder_stale_days` | 7 | Days in *Pendiente de revisión* before a reminder is sent |
| `low_stock_threshold` | 5 | Stock level flagged as low in recommendations |

Child models:

- **`tommasi.reactivation.seller`** — salespeople included in the agent (one line per user per config)
- **`tommasi.reactivation.priority.rule`** — High / Medium / Low priority thresholds (seeded on install)

## MCP tools

All tools are registered on `tommasi.reactivation.service` via `@llm_tool` from `llm_tool`. Internal helpers (stock/pricelist resolution, tier ranking, etc.) are **not** exposed as tools.

| Tool | Type | Purpose |
|------|------|---------|
| `bootstrap_reactivation_cycle` | read | First call of a detection cycle — config, priority rules, enabled sellers, and their customers |
| `get_reactivation_candidates` | read | Batch-screens a seller's portfolio (~4 aggregate SQL queries) for inactivity, revenue/qty decline, or undelivered SO lines, to shortlist customers before per-customer detection |
| `get_customer_detection_context` | read | Sales history, inactivity, product history, volume decline with stock, undelivered SO lines |
| `get_product_recommendations` | read | Ranked product suggestions with stock, net pricelist price, pricelist discount reference, and reason tier |
| `create_crm_opportunity` | write | Create a reactivation opportunity in *Pendiente de revisión* with server-side validation |
| `get_agent_opportunities` | read | List open agent opportunities for deduplication |
| `send_whatsapp_to_partner` | write (destructive) | Send an approved WhatsApp template to a partner via the Chatwoot router |

> [!TIP]
> **Scalar detection flow (v1.8)**: `bootstrap_reactivation_cycle` → per seller: `get_reactivation_candidates` → per customer: `get_customer_detection_context` → `get_product_recommendations` → `get_agent_opportunities` → `create_crm_opportunity`.
>
> **Batch detection flow (v1.9)**: `bootstrap_reactivation_cycle` → per seller: `get_reactivation_candidates(include_context=true)` → *(fallback)* per customer: `get_customer_detection_context` → per seller: `get_agent_opportunities(customer_ids[])` → per seller: `get_product_recommendations(customer_ids[])` → per seller: `create_crm_opportunity(payloads[])`.

Read tools carry `read_only_hint=True` and `idempotent_hint=True` where applicable. Write tools validate seller ownership, stock availability, and enabled-seller configuration before persisting.

### MCP response envelope

All seven `@llm_tool` methods on `tommasi.reactivation.service` return a transport envelope:

```json
{"data": <tool_payload>, "request_id": "<optional>"}
```

The LangGraph reactivation agent unwraps strictly via `{data: ...}`; golden examples for the five tools it calls are mirrored in `tests/fixtures/contracts/` (also present in the Agent repo). Contract assertions live in `tests/mcp_contract.py`; transport shape locks are in `tests/test_mcp_transport_contract.py`.

**v2 batch create** (payloads with `operation_key`): inner payload includes `contract_version: 2` and per-row `outcome` values (`created`, `existing`, `rejected`, `deferred`). Legacy creates without `operation_key` keep the v1 inner shape inside the same envelope.

### `send_whatsapp_to_partner`

Generic outbound WhatsApp send for any consented partner. Callers supply `partner_id`, explicit `company_id`, and router `template_params`; Odoo resolves the partner mobile, selects the per-company WhatsApp config, and POSTs to `POST /v1/outbound/messages` with `X-Outbound-Api-Key`.

**Consent and phone rules**

- Recipient mobile comes from `res.partner.mobile` on the commercial partner.
- Mobile must be E.164 (`+` followed by country code and digits).
- `allow_whatsapp_communication` (from `tommasi_custom`) must be `True`.
- Partners with a fixed `company_id` must match the supplied `company_id`; shared partners (`company_id` empty) may be routed to any configured company.

**Example request (inner payload after unwrap)**

```json
{
  "partner_id": 1001,
  "company_id": 1,
  "template_params": {
    "name": "order_confirmation",
    "category": "UTILITY",
    "language": "es",
    "processed_params": {"body": {"1": "121212"}}
  },
  "idempotency_key": "reactivation|customer:1001|cycle:2026-07-15",
  "content": "Optional Chatwoot thread override"
}
```

**Example response (`data`)**

```json
{
  "status": "sent",
  "partner_id": 1001,
  "company_id": 1,
  "http_status": 200,
  "conversation_id": 42,
  "message_id": 99,
  "retryable": false,
  "error": null
}
```

**Failure statuses**

| Status | Meaning |
|--------|---------|
| `rejected_partner_not_found` / `rejected_partner_inactive` | Partner missing or archived |
| `rejected_invalid_mobile` | Missing or non-E.164 mobile |
| `rejected_no_consent` | `allow_whatsapp_communication` is false |
| `rejected_company_mismatch` | Partner company does not match `company_id` |
| `rejected_no_config` | No active `tommasi.whatsapp.config` for the company |
| `rejected_invalid_template` | `template_params` shape invalid before HTTP |
| `error` | Router/transport failure; see `http_status`, `retryable`, and `error` |

Router `409` (concurrent idempotency) sets `retryable: true` — callers should retry with the same `idempotency_key`. Odoo does not auto-retry side-effecting POSTs.

Audit rows are stored in `tommasi.whatsapp.log` (template name/language and outcome only — no API key or `processed_params` values).

### Agent vs Odoo guard semantics

| Concern | Agent (LangGraph) | Odoo (this module) |
|---------|-----------------|-------------------|
| Dedup / cooldown | `apply_dedup_cooldown` fail-closed filter before recommendations | `_enforce_create_safety_guards` under row lock at CRM create |
| Cap | `apply_priority_cap` trims survivors **this cycle** using bootstrap `opportunity_cap_per_seller` | Same config field enforces **open CRM workload** at create (`seller_cap_exceeded`) |
| Stock / scope | Uses recommendation snapshot | Re-validates stock and seller scope on write |

Agent checks reduce MCP load; Odoo guards are authoritative when CRM state changes outside the agent.

### Batch tool contracts (v1.9)

Four existing tools accept retrocompatible batch parameters. Scalar and batch forms are **mutually exclusive** — mixing them returns `{ "message": "Provide exactly one of …" }`. Per-customer or per-payload failures are isolated inside a single seller-scoped call (AC-14).

| Tool | Scalar form | Batch form | Batch response shape |
|------|-------------|------------|----------------------|
| `get_reactivation_candidates` | `seller_id` | `include_context=true` (optional) | Each `candidates[]` entry may include `detection_context` (see below) |
| `get_product_recommendations` | `customer_id`, `seller_id` | `customer_ids[]`, `seller_id` | `{ seller_id, results: { "<customer_id>": { recommendations[], customer_ranking } \| { recommendations: [], message } } }` |
| `get_agent_opportunities` | `customer_id`, `seller_id`, `statuses[]` | `customer_ids[]`, `seller_id`, `statuses[]` | `{ seller_id, results: { "<customer_id>": { opportunities[] } \| { message } } }` |
| `create_crm_opportunity` | `payload` | `payloads[]` | `{ results: [ { opportunity_id, attribution_id, customer_id } \| { message, customer_id } ] }` — one entry per payload, in order |

Batch `get_agent_opportunities` uses a single `crm.lead` search for all valid customers. Batch `create_crm_opportunity` wraps each payload in a savepoint so one validation failure does not abort sibling creates.

### `include_context` on `get_reactivation_candidates`

When `include_context=true`, each shortlisted candidate also includes a `detection_context` object with the **same top-level keys** as `get_customer_detection_context`:

`sales_history`, `last_purchase`, `commercial_context`, `product_history`, `volume_decline_with_stock`, `undelivered_so_lines`

This collapses portfolio pre-screen + per-customer context reads into **S** calls (one per seller) instead of S×C. Per-customer assembly failures return `detection_context: { "message": "…" }` without aborting the batch. `get_customer_detection_context` remains available for per-customer retry when embedded context returns `{message}`.

Prescreen metrics (`days_inactive`, `revenue_change_pct`, `qty_change_pct`) prefer fresh snapshot rows when available; `undelivered_so_lines` is always read live.

### Nightly facts snapshot

A nightly `ir.cron` (`cron_refresh_facts_snapshots`) materializes expensive invoice aggregates for bootstrap-qualified partners into `tommasi.reactivation.facts.snapshot`.

**Materialized per row**: `last_purchase_date`, `revenue_prior`, `revenue_recent`, `revenue_change_pct`, `qty_change_pct`, `sales_history_json`, `product_history_json`, plus freshness metadata (`computed_at`, `date_from`, `date_to`, `config_hash`).

**Snapshot-first read path**: `_get_fresh_snapshots()` returns a row only when:

1. `computed_at` is **< 24 hours** old,
2. `config_hash` matches the current config (`inactivity_days_*`, `bootstrap_min_invoices`, `bootstrap_invoice_window_days`), and
3. stored `date_from` / `date_to` match the tool's resolved detection window.

When no fresh snapshot exists, tools fall back to live SQL (`_get_invoice_facts`, `_candidate_*_batch`, etc.).

The nightly cron batches invoice-fact reads per seller (`_get_invoice_facts_batch`) so history materialization stays O(1) SQL round-trips per seller rather than O(partners). Batch `get_agent_opportunities` resolves `stage_xml_id` values in one `ir.model.data` lookup per call.

**Always live** (never snapshotted — change intraday):

- `undelivered_so_lines` (90-day `sale.order.date_order` window)
- Stock quantities (`_get_product_stock_batch`, `volume_decline_with_stock`)
- Pricelist resolution at recommendation/create time

The cron upserts rows for all bootstrap-qualified partners per enabled seller and deletes stale rows when a partner drops out of the bootstrap universe.

## Agent security

Create a dedicated MCP technical user and assign the **Reactivation Agent** group (`group_reactivation_agent`).

Every tool call must pass `reactivation_seller_id` in the Odoo environment context. Record rules then scope access:

| Model | Agent permissions | Scope |
|-------|-------------------|-------|
| `res.partner` | read | Customers owned by context seller (or seller's own partner) |
| `crm.lead` | read, write, create | Opportunities assigned to context seller |
| `sale.order` | read | Orders for owned customers |
| `account.move` | read | Invoices for owned customers |

> [!WARNING]
> Without `reactivation_seller_id` in context, seller-scoped models return no records for agent users. This is intentional — it prevents cross-seller data leakage.

For users in the *Reactivation Agent* group, reactivation record rules use **AND** semantics with other group rules, overriding the usual OR across groups (`ir.rule` override in this module).

## CRM customization

### Pipeline stages

Two custom stages are installed (native *Won* stage is reused for closed-won):

- **Pendiente de revisión** — new agent opportunities land here
- **Cliente contactado** — set when the seller has contacted the customer

### Lead fields

`crm.lead` is extended with reactivation metadata: source, opportunity reference ID, trigger type, confidence, cycle ID, client message, and contact timestamp. A dedicated **Reactivation** notebook page appears on agent opportunities.

## Data models

| Model | Role |
|-------|------|
| `tommasi.reactivation.config` | Per-company singleton (not deletable; idempotent create) |
| `tommasi.reactivation.seller` | Enabled seller lines linked to config |
| `tommasi.reactivation.priority.rule` | Priority cap-order rules |
| `tommasi.reactivation.facts.snapshot` | Nightly materialized invoice aggregates for snapshot-first reads |
| `tommasi.whatsapp.config` | Per-company Chatwoot router outbound settings |
| `tommasi.whatsapp.log` | Sanitized audit log for WhatsApp sends |
| `tommasi.reactivation.service` | Abstract model hosting MCP tools and internal helpers |

## Testing

Post-install tests cover configuration, CRM fields and stages, seller-scoped security, MCP transport contracts, batch tools, snapshots, WhatsApp outbound, and all seven MCP tools:

```bash
# From the Doodba project root — full module
invoke test --cur-file odoo/custom/src/tommasi_addons/tommasi_sales_reactivation

# Scoped examples
invoke test --cur-file odoo/custom/src/tommasi_addons/tommasi_sales_reactivation/tests/test_mcp_transport_contract.py
invoke test --cur-file odoo/custom/src/tommasi_addons/tommasi_sales_reactivation/tests/test_reactivation_snapshot.py
```

Test modules:

| File | Coverage |
|------|----------|
| `test_reactivation_config.py` | Singleton behavior, seeded defaults, priority rules, parameter validation |
| `test_crm_lead.py` | Reactivation fields, opportunity reference ID uniqueness, custom stages |
| `test_reactivation_security.py` | Context-scoped record rules for partners and leads |
| `test_reactivation_bootstrap_detection.py` | Bootstrap, detection context, query-count bounds |
| `test_reactivation_candidates.py` | Candidate screening, bootstrap filtering, overstock cache |
| `test_reactivation_batch.py` | Batch MCP tools, stage XML-ID lookup caching |
| `test_reactivation_recommendations.py` | Product ranking and batch recommendations |
| `test_reactivation_crm_create.py` | Scalar and batch CRM create |
| `test_reactivation_create_safety.py` | Write guards, cooldown, seller cap, idempotency |
| `test_reactivation_snapshot.py` | Nightly cron, snapshot parity, batch invoice facts |
| `test_reactivation_rendering.py` | Digest and opportunity description rendering |
| `test_reactivation_multicompany.py` | Company-scoped config and seller isolation |
| `test_reactivation_whatsapp.py` | WhatsApp config, outbound contract, consent, audit sanitization |
| `test_mcp_transport_contract.py` | Envelope shape for Agent-used MCP tools + golden fixture parity |
| `mcp_contract.py` | Shared contract assertions (imported by transport tests) |

## Related documentation

Product requirements and agent architecture are described in `prds/tommasi-reactivation-odoo-prd.md` and companion PRDs for the LangGraph detection agent.
