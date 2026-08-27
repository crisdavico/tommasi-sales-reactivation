# Design: Add Star Categories to CRM Opportunities

## Technical Approach

Mirror `_get_customer_star_products` as `_get_customer_star_categories` (proposal approach 1). Same 180-day posted `out_invoice` filters; `GROUP BY` direct `template.categ_id`; top 3 by qty then category name; share = category qty / all qualifying units. Insert HTML after Productos Estrellas. Description-only: no `crm.lead` field, MCP, fixtures, or LangGraph-agent change. Capability: `crm-opportunity-star-categories`.

## Architecture Decisions

### Decision: Ranking helper

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Mirror star-products SQL helper | Extra create query; isolated rollback | **Use** |
| Extend invoice-fact lines with `categ_id` | Couples detection payloads | Reject |
| Derive from top-3 SKUs | Wrong ranking | Reject |

### Decision: Share, company, constants, rounding

| Option | Tradeoff | Decision |
|--------|----------|----------|
| One CTE: all groups + `SUM(qty)` + `LIMIT 3` | One round-trip; top-3 shares need not be 100% | **Use** |
| Two queries or unbounded Python group | Extra RTT or unbounded fetch | Reject |
| `move.company_id = ANY(res.company.sudo().search([]).ids)` | Same unified-agent set as `ir.rule`; SQL still bypasses record rules | **Use** |
| Retrofit Productos Estrellas company filter | Out of scope; sections may diverge | Reject |
| Reuse `STAR_PRODUCTS_WINDOW_DAYS` / `STAR_PRODUCTS_TOP_N` | Single 180 / top-3 source | **Use** |
| `float_round(share * 100.0, precision_digits=1)` HALF-UP | Avoids Python banker `round` | **Use** |

## Data Flow

```
create_crm_opportunity
  → validate → FOR UPDATE partner → open/cooldown/cap
  → _create_reactivation_lead_record
       → _render_opportunity_description
            → star products (unchanged; no company filter)
            → star categories (new; company allow-list)
            → HTML: evidencia → Productos Estrellas → Categorías Estrellas → sugeridos → …
       → crm.lead.create(description=html)
v2 operation_key hit → existing / idempotent_replay (no re-render)
batch → per-row savepoint (unchanged)
```

Ranking SQL is read-only inside the existing create transaction. No new savepoint, lock, or `operation_key` identity.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `models/service_facts.py` | Modify | Add `_get_customer_star_categories`; empty company ids → `[]` |
| `models/service_rendering.py` | Modify | Render section after star products; omit independently when empty |
| `tests/test_reactivation_star_categories.py` | Create | Facts RED tests |
| `tests/test_reactivation_rendering.py` | Modify | Order, omit, share format |
| `tests/test_reactivation_crm_create.py` | Modify | Heading; bordered tables 3→4 |
| `README.md` | Modify | Description-only Categorías Estrellas note |

Unchanged: `STAR_PRODUCTS_*` constants, `service_create_guards.py` (already writes `description`), `ir_rule.py`, `crm_lead.py`, XML, security. **Odoo surface:** abstract `tommasi.reactivation.service` mixins only. No data/XML/security records. MCP tools: none. `tommasi-reactivation-agent`: none.

## Interfaces / Contracts

```python
{"category_id": int, "name": str, "total_quantity": float, "share": float}  # share in [0, 1]
```

Reuse star-product `FROM`/`JOIN`/`WHERE`, plus `move.company_id = ANY(%s)` and `template.categ_id IS NOT NULL`. Non-obvious share CTE:

```sql
WITH qualifying AS (
  SELECT template.categ_id AS categ_id, COALESCE(SUM(line.quantity), 0) AS qty
  -- same joins/filters as star products + company allow-list + categ_id IS NOT NULL
  GROUP BY template.categ_id
)
SELECT cat.id, cat.name, q.qty,
       q.qty / NULLIF((SELECT SUM(qty) FROM qualifying), 0)
  FROM qualifying q
  JOIN product_category cat ON cat.id = q.categ_id
 ORDER BY q.qty DESC, cat.name ASC
 LIMIT %s
```

Unknown customer, empty allow-list, or no rows → `[]`. `total_quantity` = `round(..., 2)`. Headers: `Nombre`, `Unidades (últimos 6 meses)`, `Participación`. Cell `12.5%` via `float_round(share * 100.0, precision_digits=1)`. Escape names. MCP envelope unchanged.

## Testing Strategy

Strict TDD at apply: RED first. Runner: `invoke test -m tommasi_sales_reactivation` from Doodba root.

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | Rank, cap, name ASC tie-break, 180-day, stockable/active, refunds, child-contact tree, direct `categ_id`, inactive-company exclusion, unknown customer, share denominator | New `tests/test_reactivation_star_categories.py` |
| Unit | Omit empty; columns; half-up `16.7%` for `1/6`; order after Productos Estrellas | Extend `tests/test_reactivation_rendering.py` |
| Integration | New lead has Categorías Estrellas; `table-bordered` count 4 | Update `tests/test_reactivation_crm_create.py` |
| E2E / contract | N/A / MCP goldens untouched | No E2E layer |

**RED tests before production code:** `test_get_customer_star_categories_ranks_by_units_and_caps_top_three`; `_share_uses_all_qualifying_units`; `_tie_breaks_by_name_asc`; `_excludes_purchases_older_than_180_days`; `_excludes_inactive_and_non_stockable`; `_excludes_refunds`; `_includes_child_contact_invoices`; `_groups_direct_categ_id_not_parent`; `_excludes_inactive_company_invoices`; `_returns_empty_for_unknown_customer`; `test_render_star_categories_section_html` / `_omits_empty_rows` / `share_rounds_half_up_one_decimal`; extend `test_render_opportunity_description_section_order`; create `assertIn("Categorías Estrellas")` and bordered count `== 4`. Existing `test_v2_idempotent_replay_returns_existing` covers no description rewrite.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Read-only SQL + HTML inside existing CRM create.

## Migration / Rollout

No migration. New HTML on new creates only. Existing leads keep stored description. Rollback: remove helper, render call, tests, README note. MCP/agent unchanged.

## Open Questions

- None blocking. Share column header **Participación** is the design default.
