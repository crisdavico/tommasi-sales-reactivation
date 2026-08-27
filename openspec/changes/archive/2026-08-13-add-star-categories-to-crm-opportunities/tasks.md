# Tasks: Add Star Categories to CRM Opportunities

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 420–580 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 helper+facts tests → PR 2 render+CRM+README |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Ranking helper + facts tests | PR 1 | `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_star_categories.py` | Odoo TransactionCase via Docker `invoke test` from the Doodba project root | `_get_customer_star_categories` in `models/service_facts.py`; `tests/test_reactivation_star_categories.py`; `tests/__init__.py` import |
| 2 | HTML section, create asserts, README | PR 2 | `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_rendering.py` then `--cur-file …/tests/test_reactivation_crm_create.py` | Same Odoo harness | `models/service_rendering.py` section; rendering/CRM test edits; README note |

MCP transport unchanged: no fixture-parity tasks. Threat matrix N/A: no extra RED tests.

## Phase 1: Facts ranking (TDD)

- [x] 1.1 RED: create `tests/test_reactivation_star_categories.py` (mirror `tests/test_reactivation_star_products.py`); import in `tests/__init__.py`. Add `test_get_customer_star_categories_ranks_by_units_and_caps_top_three`, `_share_uses_all_qualifying_units`, `_tie_breaks_by_name_asc`.
- [x] 1.2 RED: add `_excludes_purchases_older_than_180_days`, `_excludes_inactive_and_non_stockable`, `_excludes_refunds`.
- [x] 1.3 RED: add `_includes_child_contact_invoices`, `_groups_direct_categ_id_not_parent`, `_excludes_inactive_company_invoices`, `_returns_empty_for_unknown_customer`.
- [x] 1.4 GREEN: implement `_get_customer_star_categories` in `models/service_facts.py` (qualifying CTE, `move.company_id = ANY(...)`, reuse `STAR_PRODUCTS_*`, empty/`[]`).
- [x] 1.5 REFACTOR: share invoice fixtures in the new test module only; no unrelated addon edits.
- [x] 1.6 Prove from Doodba root: `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_star_categories.py`.

## Phase 2: Rendering (TDD)

- [x] 2.1 RED: in `tests/test_reactivation_rendering.py` add `test_render_star_categories_section_html`, `_omits_empty_rows`, `share_rounds_half_up_one_decimal` (`1/6` → `16.7%`); extend `test_render_opportunity_description_section_order` so Categorías Estrellas follows Productos Estrellas.
- [x] 2.2 GREEN: in `models/service_rendering.py` insert the section after star products; omit when empty; `float_round(..., precision_digits=1)` HALF-UP; headers Nombre / Unidades (últimos 6 meses) / Participación.
- [x] 2.3 Prove: `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_rendering.py`.

## Phase 3: CRM create (TDD)

- [x] 3.1 RED: in `tests/test_reactivation_crm_create.py` (`test_create_crm_opportunity_formats_structured_commercial_rationale`) add `assertIn("Categorías Estrellas")` and `table-bordered` count `== 4`. Keep existing `tests/test_reactivation_create_safety.py` `test_v2_idempotent_replay_returns_existing`; do not touch `tests/fixtures/contracts/`.
- [x] 3.2 GREEN: wire renderer only on create; no MCP envelope, `crm.lead` field, or LangGraph-agent change.
- [x] 3.3 Prove: `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_crm_create.py`.

## Phase 4: Docs

- [x] 4.1 Note description-only Categorías Estrellas in `README.md`.
- [x] 4.2 Full module: `invoke test -m tommasi_sales_reactivation` from the Doodba project root.
