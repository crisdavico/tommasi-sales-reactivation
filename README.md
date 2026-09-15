# Tommasi Sales Reactivation

**Module version**: `15.0.1.10.0` (seller open opportunities MCP tool)

Odoo 15 addon that powers the Tommasi sales reactivation LangGraph agent: configuration, CRM customization, seller-scoped security, and eight MCP tools exposed through the integrated Odoo MCP server.

LangGraph detection agents call this module's `llm.tool` surface — they never access the Odoo database directly.

## Service layout

`tommasi.reactivation.service` is split across focused mixins (post v1.10 refactor):

| Module | Responsibility |
|--------|----------------|
| `tommasi_reactivation_service.py` | Base constants, config helpers, stage resolution, MCP envelope |
| `service_candidates.py` | Portfolio screening (`get_reactivation_candidates`) |
| `service_detection.py` | Per-customer detection context |
| `service_facts.py` | Invoice aggregates, sales/product history, batch invoice facts |
| `service_opportunities.py` | Open opportunities read (`get_agent_opportunities`, `get_seller_open_opportunities`) |
| `service_recommendations.py` | Product ranking and batch recommendations |
| `service_create_guards.py` | CRM create with savepoints, row locks, v2 outcomes |
| `service_whatsapp.py` | Partner WhatsApp outbound via Chatwoot router (`send_whatsapp_to_partner`) |
| `service_products.py` / `service_rendering.py` | Stock, pricelist, digest copy (internal) |

## Overview

| Area | What it provides |
|------|------------------|
| **Configuration** | Per-company singleton with operational parameters, enabled sellers, and priority rules |
| **CRM** | Custom pipeline stages and reactivation metadata on `crm.lead` |
| **MCP tools** | Eight `@llm_tool` methods on `tommasi.reactivation.service` for cycle bootstrap, candidate pre-filtering, detection, recommendations, CRM writes, seller open-lead reads, and partner WhatsApp outbound. Four tools accept retrocompatible batch parameters (`include_context`, `customer_ids[]`, `payloads[]`) to reduce detection-cycle MCP calls from O(customers) to O(sellers) |
| **Security** | *Reactivation Agent* group with seller-scoped record rules driven by `reactivation_seller_id` in context |

## Prerequisites

Install and configure these modules first:

`base`, `mail`, `crm`, `sale`, `sale_crm`, `sale_management`, `stock`, `product`, `account`, `llm_tool`, `llm_mcp_server`, `tommasi_custom`

> [!IMPORTANT]
> Sellers included in agent cycles are those with a line in **Sales reactivation → Enabled sellers**. Mobile numbers on the partner record are optional metadata exposed in bootstrap.

## Configuration

Open **LLM → Configuration → Sales reactivation** (requires *LLM Manager*).

WhatsApp outbound routing is configured separately under **LLM → Configuration → WhatsApp** (`tommasi.whatsapp.config`, one active row per company). It stores the Chatwoot router base URL, scoped outbound credentials (`outbound_key_id`, `outbound_api_key`, `outbound_hmac_secret`), Chatwoot account/inbox IDs that must match the router credential scope, and HTTP timeout. Credential fields are **LLM Manager** only; the reactivation agent has no direct config read. This is independent from sales reactivation settings.

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
| `get_reactivation_candidates` | read | Batch-screens a seller's portfolio (aggregate SQL + one invoice-facts batch) for inactivity, revenue/qty decline, undelivered SO lines, or product drop-off |
| `get_customer_detection_context` | read | Sales history, inactivity, product history, volume decline with stock, undelivered SO lines |
| `get_product_recommendations` | read | Ranked product suggestions with stock, net pricelist price, pricelist discount reference, and reason tier |
| `create_crm_opportunity` | write | Create a reactivation opportunity in *Pendiente de revisión* with server-side validation |
| `get_agent_opportunities` | read | List open agent opportunities for a customer (deduplication) |
| `get_seller_open_opportunities` | read | List every open agent opportunity for one seller (seller-facing) |
| `send_whatsapp_to_partner` | write (destructive) | Send an approved WhatsApp template to a partner via the Chatwoot router |

> [!TIP]
> **Scalar detection flow (v1.8)**: `bootstrap_reactivation_cycle` → per seller: `get_reactivation_candidates` → per customer: `get_customer_detection_context` → `get_product_recommendations` → `get_agent_opportunities` → `create_crm_opportunity`.
>
> **Batch detection flow (v1.9)**: `bootstrap_reactivation_cycle` → per seller: `get_reactivation_candidates(include_context=true)` → *(fallback)* per customer: `get_customer_detection_context` → per seller: `get_agent_opportunities(customer_ids[])` → per seller: `get_product_recommendations(customer_ids[])` → per seller: `create_crm_opportunity(payloads[])`.

Read tools carry `read_only_hint=True` and `idempotent_hint=True` where applicable. Write tools validate seller ownership, stock availability, and enabled-seller configuration before persisting.

### MCP response envelope

All eight `@llm_tool` methods on `tommasi.reactivation.service` return a transport envelope:

```json
{"data": <tool_payload>, "request_id": "<optional>"}
```

The LangGraph reactivation agent unwraps strictly via `{data: ...}`; golden examples for the five tools it calls are mirrored in `tests/fixtures/contracts/` (also present in the Agent repo). `get_seller_open_opportunities` has an Odoo-only golden in the same folder — do not mirror it to `tommasi-reactivation-agent`. Contract assertions live in `tests/mcp_contract.py`; transport shape locks are in `tests/test_mcp_transport_contract.py`.

**v2 batch create** (payloads with `operation_key`): inner payload includes `contract_version: 2` and per-row `outcome` values (`created`, `existing`, `rejected`, `deferred`). Legacy creates without `operation_key` keep the v1 inner shape inside the same envelope.

### `get_seller_open_opportunities`

Read-only list of **every open agent CRM opportunity for one seller**. Open means agent-created, active, assigned to `seller_id`, and in **Pendiente de revisión** or **Cliente contactado**. Empty `opportunities` is success. Order is `create_date` descending. No stage override, pagination, or IDs in v1.

**Args:** `seller_id` (required), `request_id` (optional).

**Example envelope**

```json
{
  "request_id": "req-seller-open-opps-001",
  "data": {
    "seller_id": 81,
    "opportunities": [
      {
        "customer_name": "Acme SA",
        "stage": "Pendiente de revisión",
        "created_at": "2026-07-08 21:05:22",
        "opportunity_url": "http://localhost:8069/web#id=9001&model=crm.lead&view_type=form",
        "client_message": "Hola, tenemos una oferta para usted."
      }
    ]
  }
}
```

`client_message` is always present (`""` when the lead has none). Rows do not include `opportunity_id` or `customer_id`.

### `send_whatsapp_to_partner`

Generic outbound WhatsApp send for any consented partner. Callers supply `partner_id`, explicit `company_id`, and router `template_params`; Odoo resolves the partner mobile, selects the per-company WhatsApp config, and POSTs to `POST /v1/outbound/messages` with the router’s **scoped five-header HMAC** contract (not API-key-only).

**Auth overview (happy path)**

1. Serialize the JSON body once (compact UTF-8 bytes).
2. Sign those exact bytes with HMAC-SHA256 using `outbound_hmac_secret`.
3. Send `data=<body_bytes>` with:

| Header | Source |
|--------|--------|
| `X-Outbound-Key-Id` | `outbound_key_id` |
| `X-Outbound-Api-Key` | `outbound_api_key` |
| `X-Timestamp` | Fresh epoch seconds per HTTP attempt |
| `X-Nonce` | Fresh unique token per HTTP attempt |
| `X-Signature` | Lowercase hex HMAC over method, path, key id, timestamp, nonce, body SHA-256 |

Business retries keep the same body `idempotency_key` but always generate a new timestamp/nonce/signature. Incomplete credentials (blank key id, API key, or HMAC secret) are rejected before HTTP with a non-retryable configuration error.

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

A successful test send returns HTTP `200` with both `conversation_id` and `message_id`.

**Failure statuses**

| Status | Meaning |
|--------|---------|
| `rejected_partner_not_found` / `rejected_partner_inactive` | Partner missing or archived |
| `rejected_invalid_mobile` | Missing or non-E.164 mobile |
| `rejected_no_consent` | `allow_whatsapp_communication` is false |
| `rejected_company_mismatch` | Partner company does not match `company_id` |
| `rejected_no_config` | No active config, or incomplete outbound credentials |
| `rejected_invalid_template` | `template_params` shape invalid before HTTP |
| `error` | Router/transport failure; see `http_status`, `retryable`, and `error` |

HTTP mapping of note:

| `http_status` | Meaning | `retryable` |
|---------------|---------|-------------|
| `401` | Generic authentication failure (bad/missing headers, key, signature, nonce, or timestamp) | `false` |
| `403` | Credential account/inbox **scope mismatch** | `false` |
| `409` | Concurrent idempotency — retry with the same `idempotency_key` | `true` |
| `502` / `503` | Temporary router/Chatwoot unavailability | `true` |

Odoo does not auto-retry side-effecting POSTs. Secrets, signatures, nonces, and raw bodies are never logged.

Audit rows are stored in `tommasi.whatsapp.log` (template name/language and outcome only — no key ids, API keys, HMAC secrets, signatures, nonces, or `processed_params` values).

### WhatsApp credential provisioning and rotation

Provision credentials in **chatwoot-router-api**, then copy the one-time plaintext into the matching Odoo company config. Account/inbox IDs in Odoo must match the router credential scope.

**Quick path**

1. Upgrade this addon so the credential fields exist.
2. From `chatwoot-router-api/` (venv + `ROUTER_HMAC_ENCRYPTION_KEY` + router DB URL):

```bash
.venv/bin/python scripts/manage_outbound_credentials.py create \
  --account-id 1 --inbox-id 5
```

3. Copy the printed one-time `key_id`, API key, and HMAC secret into **LLM → Configuration → WhatsApp** for that company; set `chatwoot_account_id` / `chatwoot_inbox_id` to the same scope.
4. Send a test template (`send_whatsapp_to_partner` or equivalent). Expect `200` + `conversation_id` / `message_id`. Wrong scope → non-retryable `403`.
5. **Rotate:** create a replacement router credential → update Odoo → validate a send → `manage_outbound_credentials.py disable --id <old_internal_id>`.

`create` prints secrets once; `list` returns metadata only and cannot recover them. The MCP agent never needs direct access to `tommasi.whatsapp.config` — the WhatsApp service loads credentials with `sudo()` internally.

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

Prescreen metrics (`days_inactive`, `revenue_change_pct`, `qty_change_pct`) come from live half-window SQL. Product drop-off screening uses one `_get_invoice_facts_batch` per seller (window: `min(detection date_from, 365-day floor)` through `date_to`), then `_get_product_history` + `_annotate_product_dropoff`. When `include_context=true`, those prefetched facts are passed into `_build_detection_context` so shortlisted customers skip a second `_get_invoice_facts`. Undelivered SO lines and stock quantities are always read live.

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

On create, the server writes HTML into `crm.lead.description`. **Categorías Estrellas** is description-only (not a `crm.lead` field): when ranking is non-empty, the table is inserted immediately after **Productos Estrellas**, and category labels use `display_name` (hierarchical path). MCP tools and envelopes are unchanged.

## Data models

| Model | Role |
|-------|------|
| `tommasi.reactivation.config` | Per-company singleton (not deletable; idempotent create) |
| `tommasi.reactivation.seller` | Enabled seller lines linked to config |
| `tommasi.reactivation.priority.rule` | Priority cap-order rules |
| `tommasi.whatsapp.config` | Per-company Chatwoot router outbound settings (scoped HMAC credentials, manager-only) |
| `tommasi.whatsapp.log` | Sanitized audit log for WhatsApp sends |
| `tommasi.reactivation.service` | Abstract model hosting MCP tools and internal helpers |

## Testing

Post-install tests cover configuration, CRM fields and stages, seller-scoped security, MCP transport contracts, batch tools, WhatsApp outbound, and all eight MCP tools:

```bash
# From the Doodba project root — full module
invoke test -m tommasi_sales_reactivation

# Scoped examples
invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_mcp_transport_contract.py
invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_candidates.py
```

Test modules:

| File | Coverage |
|------|----------|
| `test_reactivation_config.py` | Singleton behavior, seeded defaults, priority rules, parameter validation |
| `test_crm_lead.py` | Reactivation fields, opportunity reference ID uniqueness, custom stages |
| `test_reactivation_security.py` | Context-scoped record rules for partners and leads |
| `test_reactivation_bootstrap_detection.py` | Bootstrap, detection context, query-count bounds |
| `test_reactivation_candidates.py` | Candidate screening (incl. live dropoff-only), bootstrap filtering, overstock cache |
| `test_reactivation_batch.py` | Batch MCP tools, seller open opportunities, stage XML-ID lookup caching |
| `test_reactivation_recommendations.py` | Product ranking and batch recommendations |
| `test_reactivation_crm_create.py` | Scalar and batch CRM create |
| `test_reactivation_create_safety.py` | Write guards, cooldown, seller cap, idempotency |
| `test_reactivation_rendering.py` | Digest and opportunity description rendering |
| `test_reactivation_multicompany.py` | Company-scoped config and seller isolation |
| `test_reactivation_whatsapp.py` | WhatsApp config, five-header HMAC outbound, consent, audit sanitization |
| `test_mcp_transport_contract.py` | Envelope shape for Agent-used MCP tools + golden fixture parity |
| `mcp_contract.py` | Shared contract assertions (imported by transport tests) |

## Related documentation

Product requirements and agent architecture are described in `prds/tommasi-reactivation-odoo-prd.md` and companion PRDs for the LangGraph detection agent.
