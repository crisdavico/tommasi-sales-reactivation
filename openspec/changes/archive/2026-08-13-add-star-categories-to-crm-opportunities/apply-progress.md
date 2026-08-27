# Apply Progress: add-star-categories-to-crm-opportunities

**Change**: add-star-categories-to-crm-opportunities
**Mode**: Strict TDD
**Work unit**: PR 2 — Phase 4 README + full module prove (this batch). Phases 1–3 remain complete.
**applyState at start**: ready
**Attempt**: authenticated `sha256:3f57cb0d6d375ab7bc9cecd3607cd984f22b1e7d18f0ec3f2f983f5a8dfd6b1b` (`state: proceed`, zero mutation). Do not settle.

## Completed This Batch (Phase 4)

- [x] 4.1 Note description-only Categorías Estrellas in `README.md`
- [x] 4.2 Full module: `invoke test -m tommasi_sales_reactivation` from the Doodba project root

## Previously Completed (Phase 3 — keep)

- [x] 3.1 RED: CRM create heading + `table-bordered` count `== 4`
- [x] 3.2 GREEN: create already uses `_render_opportunity_description`; no extra production code
- [x] 3.3 Prove via Doodba `invoke test --cur-file …/test_reactivation_crm_create.py`

## Previously Completed (Phase 2 — keep)

- [x] 2.1 RED rendering HTML / omit-empty / half-up `16.7%` tests; extend section order
- [x] 2.2 GREEN `_render_star_categories_section_html` after Productos Estrellas
- [x] 2.3 Prove via Doodba `invoke test --cur-file …/test_reactivation_rendering.py`

## Previously Completed (Phase 1 — keep)

- [x] 1.1 RED ranking / share / name-asc tie-break tests
- [x] 1.2 RED 180-day / inactive-non-stockable / refunds tests
- [x] 1.3 RED child-contact / direct categ_id / disallowed-company / unknown-customer tests
- [x] 1.4 GREEN `_get_customer_star_categories` in `models/service_facts.py`
- [x] 1.5 REFACTOR shared fixtures stay in the new test module
- [x] 1.6 Prove via Doodba `invoke test --cur-file …/test_reactivation_star_categories.py`

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1 | `tests/test_reactivation_star_categories.py` | Odoo TransactionCase | N/A (new file) | ✅ Written then executed: `AttributeError: object has no attribute '_get_customer_star_categories'` | ✅ Passed (10/10 class methods) | ✅ 3 cases (rank+cap, share denominator, name ASC tie) | ✅ Shared fixtures in this module |
| 1.2 | `tests/test_reactivation_star_categories.py` | Odoo TransactionCase | N/A (new file) | ✅ Written then executed: same AttributeError | ✅ Passed | ✅ 3 cases (180-day cutoff, inactive/service/consu, refunds not netted) | ✅ `_invoice_date` / `_create_invoice_with_products` reused |
| 1.3 | `tests/test_reactivation_star_categories.py` | Odoo TransactionCase | N/A (new file) | ✅ Written then executed: AttributeError (9 tests) + `ValueError: Invalid field 'active' on model 'res.company'` on the company case | ✅ Passed after Odoo 15 allow-list setup (no `res.company.active`) | ✅ 4 cases (child contact, direct categ_id, disallowed company, unknown customer `[]`) | ✅ `_assign_move_to_disallowed_company` stays in this module |
| 1.4 | `tests/test_reactivation_star_categories.py` | Odoo TransactionCase | Additive method only; did not edit `_get_customer_star_products` | ✅ Tests existed first | ✅ Helper implemented; class 10/10 green | ✅ All Phase 1 spec scenarios covered | ➖ Production SQL already matches design CTE |
| 1.5 | `tests/test_reactivation_star_categories.py` | Odoo TransactionCase | N/A (test-only) | ➖ Refactor task | ✅ GREEN re-run after fixture extract | ➖ Structural | ✅ `_invoice_date`, `_create_category`, `_create_product`, `_create_invoice_with_products` shared; not moved to `tests/common.py` |
| 1.6 | `tests/test_reactivation_star_categories.py` | Odoo TransactionCase | See Work Unit Evidence | ✅ RED run 2026-08-13 | ✅ GREEN run 2026-08-13 | ➖ Prove | ➖ Prove |
| 2.1 | `tests/test_reactivation_rendering.py` | Odoo TransactionCase | ✅ Safety net before edits: `200 post-tests in 139.75s`, `0 failed, 6 error(s)`; **TestReactivationRendering 0 FAIL/ERROR** (pre-existing WhatsApp unique-constraint only) | ✅ Written then executed: 3× `AttributeError: no attribute '_render_star_categories_section_html'`; order test `ValueError` on `html.index("Categorías Estrellas")`. Suite: `203 post-tests in 139.80s`, `0 failed, 10 error(s)` (4 rendering + 6 WhatsApp) | ✅ See 2.2 | ✅ 5 cases: columns+escape+`12.5%`, empty omit `""`, `1/6` → `16.7%`, heading after Productos Estrellas, omit without `customer_id` | ➖ Tests-only task |
| 2.2 | `tests/test_reactivation_rendering.py` | Odoo TransactionCase | See 2.1 | ✅ Tests existed first | ✅ `TestReactivationRendering` 25/25 started with 0 FAIL/ERROR after renderer + description insert | ✅ Same 5 cases forced real `float_round(..., precision_digits=1)` + independent omit | ➖ None needed — mirrors `_render_star_products_section_html` |
| 2.3 | `tests/test_reactivation_rendering.py` | Odoo TransactionCase | See Work Unit Evidence | ✅ RED run 2026-08-13 | ✅ GREEN run 2026-08-13 | ➖ Prove | ➖ Prove |
| 3.1 | `tests/test_reactivation_crm_create.py` | Odoo TransactionCase | ✅ Safety net before edits: `203 post-tests in 142.30s`, `1 failed, 6 error(s)`; the 1 fail is planned RED `AssertionError: 4 != 3` on `test_create_crm_opportunity_formats_structured_commercial_rationale`; remaining 6 WhatsApp unique-constraint | ✅ RED was the pre-update count `== 3` vs production 4 tables. Updated test: `assertIn("Categorías Estrellas")`, heading after Productos Estrellas, `table-bordered` count `== 4`. `test_v2_idempotent_replay_returns_existing` untouched; `tests/fixtures/contracts/` untouched | ✅ See 3.2 | ✅ 2 cases: create description heading+4 tables+order; existing `test_v2_idempotent_replay_returns_existing` (no rewrite) | ➖ Tests-only task |
| 3.2 | `tests/test_reactivation_crm_create.py` | Odoo TransactionCase | See 3.1 | ✅ Tests existed first | ✅ No extra production code. `_create_reactivation_lead_record` already writes `description=self._render_opportunity_description(...)`; Phase 2 already inserts Categorías Estrellas after Productos Estrellas. GREEN: `0 failed, 6 error(s) of 203` | ✅ Same create + idempotent-replay paths | ➖ None needed — create already wired |
| 3.3 | `tests/test_reactivation_crm_create.py` | Odoo TransactionCase | See Work Unit Evidence | ✅ RED run 2026-08-13 | ✅ GREEN run 2026-08-13 | ➖ Prove | ➖ Prove |
| 4.1 | `README.md` | Docs | N/A (documentation-only; no production Python) | ➖ Documentation-only (no test written) | ✅ Short CRM note: description-only Categorías Estrellas after Productos Estrellas; no `crm.lead` field; MCP unchanged | Triangulation skipped: documentation-only | ➖ None needed |
| 4.2 | Full addon suite | Odoo TransactionCase | Phase 3 GREEN baseline: `0 failed, 6 error(s) of 203` (WhatsApp unique-constraint only) | ➖ Prove (no new tests) | ✅ `invoke test -m tommasi_sales_reactivation`: `203 post-tests in 139.69s`, `0 failed, 6 error(s) of 203 tests when loading database 'devel'`; exit 1 only from the 6 WhatsApp errors | ➖ Prove | ➖ Prove |

### Test Summary

- **Total tests written** (this change): 10 facts + 3 new rendering methods + 2 extended rendering methods + 1 CRM create test extended (heading, order, table count). Phase 4: 0 new tests.
- **Total tests passing** (focused classes): Phase 1 `TestReactivationStarCategories` 10/10; Phase 2 `TestReactivationRendering` 25/25; Phase 3 `TestReactivationCrmCreate` 0 FAIL after assertion update. Phase 4 full suite: **0 failed**, 6 pre-existing WhatsApp errors of 203.
- **Layers used**: Unit/integration TransactionCase, E2E (0), Docs (4.1)
- **Approval tests** (refactoring): None — no production refactor this batch
- **Pure functions created**: 0 this batch (README + prove only)

## Work Unit Evidence

### Phase 1 (prior batch)

| Evidence | Required value |
|---|---|
| Focused test command and exact result | From `/home/crisd/projects/tommasi/odoo/tommasi`: `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_star_categories.py`. Doodba still tags the whole addon (`--test-tags /tommasi_sales_reactivation`). **RED** (before helper): `200 post-tests in 143.82s`, `0 failed, 16 error(s)`; all 10 `TestReactivationStarCategories` methods errored with missing `_get_customer_star_categories` (company case also hit Odoo 15 `res.company` having no `active`). **GREEN** (after helper + Odoo 15 company setup): `200 post-tests in 138.56s`, `0 failed, 6 error(s)`; **0** `TestReactivationStarCategories` failures/errors (10/10 passed). The remaining 6 errors are pre-existing WhatsApp unique-constraint / `setUpClass` failures (`tommasi_whatsapp_config_company_uniq`), also present on RED and unrelated to this work unit. |
| Runtime harness command/scenario and exact result | Same Docker Odoo TransactionCase harness as the focused command (post-install, `-i tommasi_sales_reactivation`, `--stop-after-init`). Exact GREEN result: `0 failed, 6 error(s) of 200 tests when loading database 'devel'`; star-categories class fully green. |
| Rollback boundary | Revert `models/service_facts.py` (`_get_customer_star_categories` only), delete `tests/test_reactivation_star_categories.py`, remove the `tests/__init__.py` import. Does not touch rendering, CRM create, README, MCP fixtures, or the LangGraph agent. |

### Phase 2 (prior batch)

| Evidence | Required value |
|---|---|
| Focused test command and exact result | From `/home/crisd/projects/tommasi/odoo/tommasi`: `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_rendering.py`. Doodba still tags the whole addon. **Safety net** (before file edits): `200 post-tests in 139.75s`, `0 failed, 6 error(s)`; rendering class green. **RED** (tests only): `203 post-tests in 139.80s`, `0 failed, 10 error(s)`; 4 `TestReactivationRendering` errors (missing renderer + missing heading). **GREEN** (after `service_rendering.py`): `203 post-tests in 139.55s`, `1 failed, 6 error(s)`; **TestReactivationRendering 25/25** started with **0 FAIL/ERROR**. The 1 failed is `TestReactivationCrmCreate.test_create_crm_opportunity_formats_structured_commercial_rationale` `AssertionError: 4 != 3` (bordered-table count) — Phase 3 incoming, not fixed here. The 6 errors remain pre-existing WhatsApp unique-constraint. |
| Runtime harness command/scenario and exact result | Same Docker Odoo TransactionCase harness (post-install, `-i tommasi_sales_reactivation`, `--stop-after-init`). Focused proof: rendering class fully green on GREEN run. |
| Rollback boundary | Revert `models/service_rendering.py` (`_render_star_categories_section_html` + description insert only) and `tests/test_reactivation_rendering.py` Phase 2 edits. Does not revert Phase 1 facts helper, CRM create tests, README, MCP fixtures, or the LangGraph agent. |

### Phase 3 (prior batch)

| Evidence | Required value |
|---|---|
| Focused test command and exact result | From `/home/crisd/projects/tommasi/odoo/tommasi`: `invoke test --cur-file odoo/custom/src/otros/tommasi_sales_reactivation/tests/test_reactivation_crm_create.py`. Doodba still tags the whole addon. **Safety net / RED** (before assertion update): `203 post-tests in 142.30s`, `1 failed, 6 error(s)`; `FAIL: TestReactivationCrmCreate.test_create_crm_opportunity_formats_structured_commercial_rationale` `AssertionError: 4 != 3`. `test_v2_idempotent_replay_returns_existing` started with no FAIL. **GREEN / prove** (after heading + count `== 4` + order assert): `203 post-tests in 140.45s`, `0 failed, 6 error(s)`; structured-rationale test started with **0 FAIL**; no `TestReactivationCrmCreate` failures. The 6 errors remain pre-existing WhatsApp unique-constraint (`tommasi_whatsapp_config_company_uniq`) and were not fixed. |
| Runtime harness command/scenario and exact result | Same Docker Odoo TransactionCase harness (post-install, `-i tommasi_sales_reactivation`, `--stop-after-init`). Create path writes `crm.lead.description` via `_render_opportunity_description`; stored HTML includes **Categorías Estrellas** and four `table table-bordered` tables. Exact GREEN result: `0 failed, 6 error(s) of 203 tests when loading database 'devel'`. |
| Rollback boundary | Revert `tests/test_reactivation_crm_create.py` Phase 3 assertion edits only. Does not revert Phase 1 facts helper, Phase 2 renderer, README, MCP fixtures, `service_create_guards.py`, or the LangGraph agent. |

### Phase 4 (this batch)

| Evidence | Required value |
|---|---|
| Focused test command and exact result | From `/home/crisd/projects/tommasi/odoo/tommasi`: `invoke test -m tommasi_sales_reactivation`. Exact result: `203 post-tests in 139.69s, 113623 queries`; `0 failed, 6 error(s) of 203 tests when loading database 'devel'`; process exit 1. **0 FAIL** lines. All 6 errors are the known WhatsApp unique-constraint baseline `tommasi_whatsapp_config_company_uniq` (`Key (company_id)=(1) already exists`): (1) `TestMcpTransportContract.test_send_whatsapp_to_partner_returns_envelope`; (2) `setUpClass (TestReactivationWhatsapp)`; (3) `TestWhatsappConfig.test_outbound_credential_token_validation`; (4) `TestWhatsappConfig.test_positive_chatwoot_ids`; (5) `TestWhatsappConfig.test_router_url_validation`; (6) `TestWhatsappConfig.test_timeout_bounds`. No new failures beyond that baseline. |
| Runtime harness command/scenario and exact result | Same command is the runtime harness (Docker Odoo post-install TransactionCase, `-i tommasi_sales_reactivation`, `--stop-after-init`). 4.1 has no runtime boundary of its own (documentation-only README). |
| Rollback boundary | Revert the two-line `README.md` note under CRM Lead fields. Does not revert Phase 1 facts helper, Phase 2 renderer, Phase 3 create asserts, MCP fixtures, or the LangGraph agent. |

## Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `tests/test_reactivation_star_categories.py` | Created (Phase 1) | 10 TransactionCase tests + module-local invoice/category fixtures |
| `tests/__init__.py` | Modified (Phase 1) | Import `test_reactivation_star_categories` |
| `models/service_facts.py` | Modified (Phase 1) | Add `_get_customer_star_categories` (qualifying CTE, company `ANY(...)`, `STAR_PRODUCTS_*`, `[]` on unknown/empty allow-list) |
| `tests/test_reactivation_rendering.py` | Modified (Phase 2) | Add HTML / omit / half-up share tests; extend section order and no-customer omit |
| `models/service_rendering.py` | Modified (Phase 2) | `_render_star_categories_section_html`; insert after Productos Estrellas; independent empty omit; `float_round(..., precision_digits=1)` |
| `tests/test_reactivation_crm_create.py` | Modified (Phase 3) | `assertIn("Categorías Estrellas")`; heading after Productos Estrellas; `table-bordered` count `== 4` |
| `README.md` | Modified (Phase 4) | Short description-only Categorías Estrellas note under CRM Lead fields; MCP unchanged |
| `openspec/changes/add-star-categories-to-crm-opportunities/tasks.md` | Modified | Mark Phase 1, Phase 2, Phase 3, and Phase 4 tasks `[x]` |
| `openspec/changes/add-star-categories-to-crm-opportunities/apply-progress.md` | Updated | Merged Phase 1 + Phase 2 + Phase 3 + Phase 4 evidence |

Authored snapshot for Phase 4 (excluding this progress file): 2 lines in `README.md` (`+2/−0`). Under the 400-line PR budget for this slice. No production-Python delta.

## Deviations from Design

- **Company allow-list test setup** (Phase 1): design/`tasks.md` named `_excludes_inactive_company_invoices`. Odoo 15 `res.company` has **no** `active` field (`ValueError` on RED). The production predicate is still `move.company_id = ANY(res.company.sudo().search([]).ids)` as designed. The test creates a company, points the posted move at it, then deletes that `res_company` row (replication role) so the id is outside the unified-agent allow-list. Behavior under test is unchanged: disallowed-company units must not rank.
- Phase 2 production matches design: heading immediately after Productos Estrellas; independent omit; share display one-decimal percent via `float_round(..., precision_digits=1)`; headers `Nombre` / `Unidades (últimos 6 meses)` / `Participación`; names escaped.
- Phase 3: create already called `_render_opportunity_description`; GREEN added **no** extra production code. Triangulation added `assertLess` for heading order on the create description (spec Placement scenario); not a production deviation.
- Phase 4: README note matches design (description-only; after Productos Estrellas; no `crm.lead` field; MCP unchanged). No production Python. Triangulation skipped: documentation-only.

## Issues Found

- Pre-existing (not introduced here): WhatsApp config unique constraint `tommasi_whatsapp_config_company_uniq` fails 6 tests when the full addon suite runs. Do not fix in this work unit. Phase 4.2 exit 1 is only those 6 errors (`0 failed`).
- Doodba `invoke test --cur-file` selects the addon, not a single file (`--test-tags /tommasi_sales_reactivation`). Focused proof is still that command, with class-level pass/fail read from the log.

## Remaining Tasks

- None. All implementation tasks 1.1–4.2 are `[x]`.

## Workload / PR Boundary

- Mode: stacked PR slice (`stacked-to-main`)
- Current work unit: PR 2 — Phase 4 README + full module prove
- Boundary: starts after Phase 3 create asserts; ends with README description-only note and full-addon prove (`0 failed, 6 error(s) of 203`)
- Estimated review budget impact: ~2 authored lines this slice (well under 400)

## Status

16/16 tasks complete (Phase 1 6/6, Phase 2 3/3, Phase 3 3/3, Phase 4 2/2). Ready for **sdd-verify**.
