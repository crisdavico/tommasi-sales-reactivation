# Proposal: Add Star Categories to CRM Opportunities

## Intent

Add a description-only **Categorías Estrellas** table after **Productos Estrellas**, from the same 180-day invoices.

## Scope

### In Scope
- Top 3 **direct** `product.category` (no parent rollup) by **positive invoiced units** over **180 days**; posted `out_invoice` on the commercial-partner tree; **active stockable** only; **refunds excluded** (no `out_refund` netting).
- Cross-company only across unified-agent `res.company` ids. Helper: explicit `move.company_id` predicate.
- Render **Categorías Estrellas** immediately after **Productos Estrellas** (name, units, share of **all qualifying units**). Omit when empty. Tie-break: `qty DESC`, then `product.category.name ASC`.
- Later: `models/service_facts.py`, `models/service_rendering.py`, tests, README. Do not implement now.

### Out of Scope
- New `crm.lead` field, CRM tag, MCP argument/envelope, mirrored fixture, LangGraph-agent change, WhatsApp, detection/recommendation payloads.
- Retrofit Productos Estrellas company filter (risk note only). Derive categories from top-3 SKUs.

**MCP / contract:** does **not** affect MCP tools, envelopes, or mirrored contract fixtures.

**Ownership:** agent optimizes; Odoo enforces create guards, seller/company scope, and write-time description HTML.

## Capabilities

### New Capabilities
- `crm-opportunity-star-categories`: server-computed Categorías Estrellas ranking and HTML.

### Modified Capabilities
- None

## Approach

Mirror `_get_customer_star_products` as `_get_customer_star_categories` (approach 1). Same invoice filters; `GROUP BY` direct `template.categ_id`; `ORDER BY qty DESC, category.name ASC LIMIT 3`. Share = `category_qty / SUM(all qualifying line qty)`. Insert after Productos Estrellas.

**Share display:** one-decimal percent of all qualifying units (e.g. `12.5%`). Round half up. Top-3 shares need not total 100%.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `models/service_facts.py` | Modified | Helper + company allow-list |
| `models/service_rendering.py` | Modified | Section after Productos Estrellas |
| Tests + `README.md` | Modified | Rank, omit, HTML; table count 3→4 |
| MCP / fixtures / LangGraph agent | Unchanged | No contract change |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Create tests expect 3 bordered tables | High | Update count |
| Raw SQL bypasses record rules | Med | Partner scope + company allow-list |
| Wrong share denominator or parent rollup | Med | All qualifying units; direct `categ_id` |
| Star-product SQL has no `move.company_id` | Low | Allow-list here only; do not retrofit Productos Estrellas |

## Rollback Plan

Remove helper, render call, tests, README. Existing leads keep HTML. `operation_key` replays stay `existing` (no description rewrite). MCP unchanged.

## Dependencies

`STAR_PRODUCTS_*` constants and unified-agent `res.company` set.

## Success Criteria

- [ ] Qualifying new leads show **Categorías Estrellas** after **Productos Estrellas** with name, units, and 1-decimal % of all qualifying units; omit when empty.
- [ ] Ranking matches locked In Scope filters.
- [ ] CRM write idempotency and MCP contracts unchanged (`operation_key` → `existing`; description not rewritten).
