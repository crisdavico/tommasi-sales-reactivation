## Exploration: add-star-categories-to-crm-opportunities

CodeGraph MCP was unavailable (`user-codegraph` not in session). `gentle-ai codegraph init --cwd <addon-root>` failed with exit 127 (Windows npm `codegraph` on PATH; no Linux binary). Investigation used Read/`rg` on the addon.

Locked product decisions are treated as given. This document maps them onto current code; it does not reopen them.

### Current State

CRM opportunity HTML is server-computed at write time. `create_crm_opportunity` validates the MCP payload, then `_create_reactivation_lead_record` stores `crm.lead.description` from `_render_opportunity_description`. The MCP envelope returns ids/outcomes only (`opportunity_id`, `operation_key`, v2 `created|existing|rejected|deferred`). Description is not an argument, not a response field, and not in golden fixtures (`tests/fixtures/contracts/create_crm_opportunity.json`).

Current description section order in `models/service_rendering.py`:

1. **Resumen de evidencia** (if evidence HTML exists)
2. **Productos Estrellas** (if `customer_id` and ranking rows exist)
3. **Productos sugeridos** (from payload)
4. **Fundamentación comercial**
5. **Mensaje al cliente**

**Productos Estrellas facts** (`models/service_facts.py` `_get_customer_star_products`):

- Window: `STAR_PRODUCTS_WINDOW_DAYS = 180`; cap: `STAR_PRODUCTS_TOP_N = 3` (`models/tommasi_reactivation_service.py`).
- Posted `out_invoice` only (`move_type = 'out_invoice'`, `state = 'posted'`). `out_refund` is excluded; quantities are not netted.
- Scope: `partner.commercial_partner_id` via `_commercial_partner_id_for_customer` (commercial-partner tree, including invoices on child contacts).
- Lines: `line.quantity > 0`, `product.active IS TRUE`, `template.type = 'product'` (active stockable only).
- Rank: `ORDER BY qty DESC, template.name ASC LIMIT 3`. Tie-break is product template name ascending. There is no dedicated tie test today.
- Company: raw SQL has **no** `move.company_id` filter. Record rules do not apply to `env.cr.execute`. Agent ORM reads use `reactivation_unified_companies` so `ir.rule` `_eval_context` sets `company_ids` to all `res.company` rows (`models/ir_rule.py`). Star-product SQL therefore already sums every company in the database for that commercial partner.
- Empty: unknown customer → `[]`. Renderer `_render_star_products_section_html([])` returns `""` (no heading). Description omits the section when there is no `customer_id` or no rows.
- Units display: name, SKU, `Unidades (últimos 6 meses)`; quantities via `_format_evidence_qty`. **No share/percentage** today.
- Tests: `tests/test_reactivation_star_products.py` (rank/cap, 180-day cutoff, inactive/service/consumable exclusion, unknown customer). Rendering omit/order in `tests/test_reactivation_rendering.py`. Create path asserts `"Productos Estrellas"` and `description.count('class="table table-bordered"') == 3` in `tests/test_reactivation_crm_create.py`. Refunds, child-contact tree, company filter, and name tie-break are **not** covered for star products.

**Invoice/product-category facts already in the addon:**

- `_get_invoice_facts` / batch variants return move revenue and product lines (`product_id`, qty, sku, name, active, type). **No `categ_id`.**
- Recommendations `_get_similar_category_products` uses **direct** `product.product.categ_id` (`.mapped("categ_id")`, domain `("categ_id", "in", categories)`). No `parent_id` / `parent_path` rollup. That is the pattern to copy for Categorías Estrellas.
- Demo products already assign `product.category` records (`data/demo/demo_products.xml`).

**Idempotency:** v2 `operation_key` lookup returns `existing` / `idempotent_replay` without rewriting the lead. Unique `reactivation_operation_key` on `crm.lead`. Adding description HTML affects **new** creates only. Replays keep the stored description.

**MCP / agent:** seven `@llm_tool` methods; create payload has no description field. LangGraph agent and mirrored fixtures can stay unchanged.

### Affected Areas

- `models/service_facts.py` — add a star-categories ranking helper parallel to `_get_customer_star_products` (direct `product.category`, units, qualifying-total for share).
- `models/service_rendering.py` — render `<h3>Categorías Estrellas</h3>` immediately after Productos Estrellas; omit when no rows.
- `models/tommasi_reactivation_service.py` — optional named constants; 180 / top-3 already exist as `STAR_PRODUCTS_*` and match the locked rules.
- `models/service_create_guards.py` — no control-flow change; description is already assembled here. Table-count assertion in create tests will move from 3 → 4 when both star tables render.
- `tests/test_reactivation_star_products.py` (or a sibling facts test) — ranking, 180-day window, stockable/active, refunds excluded, commercial tree, company allow-list, tie-break, share denominator.
- `tests/test_reactivation_rendering.py` — section order (Estrellas → Categorías Estrellas → sugeridos), empty omit, columns (name, units, share).
- `tests/test_reactivation_crm_create.py` — description contains Categorías Estrellas when history qualifies; update bordered-table count.
- `README.md` — document the description-only section (README does not mention Productos Estrellas today; `service_rendering.py` is still described as digest copy).
- **Out of scope (locked):** `models/crm_lead.py` (no new field/tag), MCP tool signatures/envelopes, `tests/fixtures/contracts/`, LangGraph agent repo.

### Approaches

1. **Mirror star-products SQL helper (recommended)** — `_get_customer_star_categories` joins `product_template.categ_id` → `product.category`, same invoice filters as star products, `GROUP BY` direct category id (no parent rollup), `ORDER BY qty DESC, category.name ASC LIMIT 3`. Compute share as `category_qty / SUM(all qualifying line qty)` (not top-3-only). Render a second HTML table after Productos Estrellas.
   - Pros: Matches locked rules; same patterns/tests as Productos Estrellas; description-only; rollback is delete helper + render call; MCP/idempotency untouched.
   - Cons: Second SQL round-trip at create (acceptable; star products already does one). Must not reuse `_get_invoice_facts` without adding `categ_id` (would widen detection payloads).
   - Effort: Low

2. **Python aggregate by extending `_fetch_invoice_fact_lines`** — add `categ_id` to shared invoice-fact rows and group in Python.
   - Pros: One facts pipeline.
   - Cons: Changes a shared detection/candidates code path for a create-time description feature; higher coupling and test blast radius.
   - Effort: Medium

3. **Derive categories from the top-3 star product rows** — group those SKUs' categories.
   - Pros: No extra query.
   - Cons: Violates locked ranking (top categories by units ≠ categories of top products). Rejected.
   - Effort: Low (wrong)

### Recommendation

Use approach 1. Map locked rules onto the existing star-product helper:

| Locked decision | Current mapping |
|-----------------|-----------------|
| Top 3 direct `product.category`, no parent rollup | `GROUP BY template.categ_id` / `product.category.id`; do not use `parent_path` |
| Positive invoiced units, 180 days | `SUM(line.quantity)` where `quantity > 0`; reuse `STAR_PRODUCTS_WINDOW_DAYS` |
| Posted `out_invoice`, commercial tree | Same `move_type`/`state`/`commercial_partner_id` predicates |
| Active stockable only | `product.active` and `template.type = 'product'` |
| Refunds excluded, no `out_refund` netting | Keep `move_type = 'out_invoice'` only |
| Company allow-list | Add `move.company_id = ANY(allowed_ids)`. Allowed ids = unified-agent set (`res.company` sudo search, same as `ir.rule` when `reactivation_unified_companies`) so aggregation stays cross-company **within** the service environment, not a silent global bypass beyond that set |
| Heading after Productos Estrellas | Insert render call immediately after `_render_star_products_section_html` |
| Name, units, share of **all qualifying** units | Denominator = total qty of lines that pass the same WHERE (including categories outside the top 3). Top-3 shares typically sum to **less than 100%** |
| Omit when nothing qualifies | Return `[]` / `""` like star products; no placeholder heading |
| Description-only | Keep writing `crm.lead.description` only |

**Tie-break:** copy star products: `qty DESC`, then `product.category.name ASC` (deterministic). Do not use unordered `qty DESC` alone.

**Share formatting:** no star-product precedent. `_pct_change` rounds to 2 decimals; suggested-product discount renders `"%s%%"`. Proposal should pick one display (recommend integer or 1 decimal percent of qualifying units) and test rounding of the denominator.

**Empty independence:** with identical filters, any qualifying line yields both a product and a category, so both sections usually appear or omit together. Still omit each section independently if its helper returns no rows.

**Rollback:** remove the helper, the render insertion, and tests. Existing leads keep historical HTML. `operation_key` replays continue to return `existing` without mutating description. MCP contracts and the LangGraph agent stay unchanged.

**Idempotency / MCP confirmation:** create guards key on `reactivation_operation_key`; description is not part of identity. Fixtures do not include HTML. No envelope, argument, or agent change is required.

### Risks

- `test_create_crm_opportunity_*` asserts exactly three `table table-bordered` blocks; a fourth star-category table will fail until that count is updated.
- Raw SQL bypasses seller/company record rules. The new helper must apply the commercial-partner scope **and** an explicit allowed-company predicate so it does not read invoices outside the unified service environment.
- Share denominator must be all qualifying units, not the top-3 subtotal; a wrong denominator would always display ~100% across three rows.
- Parent-category rollup is a likely implementation mistake (`categ_id.parent_id` / `parent_path`); similar-category recommendations already use direct `categ_id` only.
- Star-product SQL currently has no company filter; tightening **only** categories would make the two sections disagree in multi-company trees. Prefer the allow-list on the new helper and note the star-product gap without expanding this change unless product asks.
- Refund exclusion is locked but untested on star products; category tests should assert `out_refund` does not net or rank.
- CodeGraph was not initialized; structural callers/callees were confirmed by direct file reads.

### Ready for Proposal

Yes. Orchestrator should run **sdd-propose** for `crm-opportunity-star-categories`: description-only Categorías Estrellas via a facts helper + render insertion after Productos Estrellas; no `crm.lead` field, no MCP/fixture/agent change; rollback by removing helper and section; tests on facts, rendering, and create HTML (including table count).
