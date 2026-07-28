# Mapa escenario → demo data — `tommasi_sales_reactivation`

Referencia rápida para corridas MCP en vivo sobre la demo del módulo (`data/demo/` +
loader `tommasi.reactivation.demo._load_demo_transactions`). Complementa
[`scenario_matrix.md`](scenario_matrix.md).

**Seller A** = `demo_seller_enabled_a` (María López, `+5491112345678`)  
**Seller B** = `demo_seller_enabled_b` (Carlos Ruiz, `+5491123456789`)

---

## Sección 1 — Bootstrap (N-xx)

| ID | Partner / ref | Seller | Tool(s) | Verificación |
|----|---------------|--------|---------|--------------|
| N-01 | Mix vendedores: `demo_seller_enabled_a/b`, `demo_seller_no_mobile`, `demo_seller_configured_c`, `demo_seller_not_configured` | — | `bootstrap_reactivation_cycle` | `sellers[]` solo vendedores con línea en config (enabled_a/b, no_mobile, configured_c); no `not_configured` |
| N-02 | `demo_cust_sporadic` (2 facturas), `demo_cust_prospect`, `demo_cust_child`, `demo_cust_supplier`, habituales varios | A | `bootstrap_reactivation_cycle` | `customers[]` sin sporadic/prospect/child/supplier; con habituales |
| N-03 | `demo_cust_sporadic` | A | `bootstrap_reactivation_cycle` | Bajar `bootstrap_min_invoices` a 1 por UI → sporadic aparece |
| N-04 | Config singleton | — | `bootstrap_reactivation_cycle` | `config` completo con `priority_rules[]` |
| N-05 | `demo_cust_company_b_*`, sellers A + C | A, C | `bootstrap_reactivation_cycle` | Carteras multicompañía agregadas, seller A deduplicado |

---

## Sección 2 — Detección (T-xx)

| ID | Partner / ref | Seller | Tool(s) | Verificación |
|----|---------------|--------|---------|--------------|
| T-01 | `demo_cust_urgent` | A | `get_customer_detection_context` | `days_inactive` = 47, cruza umbral secundario 45 |
| T-02 | `demo_cust_declining` | A | `get_customer_detection_context` | `sales_history` con caída ≥ 50% |
| T-03 | `demo_cust_volume_decline` | A | `get_customer_detection_context` | `volume_decline_with_stock` solo `demo_prod_volume_decline` (no `demo_prod_no_stock`) |
| T-04 | `demo_cust_dropoff` | A | `get_customer_detection_context` | `product_history` con cadencia del aceite dropoff |
| T-05 | `demo_cust_undelivered` | A | `get_customer_detection_context` | `undelivered_so_lines` con SO pendiente |
| T-06 | `demo_cust_active` | A | `get_customer_detection_context` | Señales vacías / inactividad baja |
| T-07 | `demo_cust_draft_noise` | A | `get_customer_detection_context` | Inactivo ~45d; borrador/cancelada recientes no cuentan |

---

## Sección 3 — Dedup / cap (Q-xx)

| ID | Partner / ref | Seller | Tool(s) | Verificación |
|----|---------------|--------|---------|--------------|
| Q-01 | `demo_cust_with_opp_pending` + `demo_opp_pending` | A | `get_agent_opportunities` | Opp abierta en *Pendiente de revisión* |
| Q-02 | `demo_cust_archived_opp` + `demo_opp_archived` (backdate 45d) | A | `get_agent_opportunities` | Archivada no en abiertas; cooldown cumplido |
| Q-03 | `demo_cust_active` + `demo_opp_non_agent` | A | `get_agent_opportunities` | Opp manual no listada (`reactivation_is_agent=false`) |
| Q-04 | `demo_cust_cap_01`…`20` + `demo_opp_pending` (21 abiertas) | A | `bootstrap_reactivation_cycle`, `get_agent_opportunities` | ≥21 opps agente abiertas vs cap 20 |

---

## Sección 4 — Productos (P-xx)

| ID | Partner / ref | Seller | Tool(s) | Verificación |
|----|---------------|--------|---------|--------------|
| P-01 | `demo_cust_multitier` | A | `get_product_recommendations` | `recommendations[]` ordenadas por `opportunity_score` DESC; incluye campos de scoring |
| P-02 | Cualquier cliente demo | A | `get_product_recommendations` | Sin inactivos/sin stock/no vendibles en `recommendations[]` |
| P-03 | `demo_cust_pricelist` | A | `get_product_recommendations` | `list_price` = 75000 ARS (precio neto de tarifa demo, no 80000 de catálogo) |
| P-04 | `demo_cust_low_stock_offer` | A | `get_product_recommendations` | Item `demo_prod_low_stock` con `low_stock=true` |
| P-05 | Runtime — ver abajo | A | `get_product_recommendations`, `create_crm_opportunity` | — |

---

## Sección 5 — CRM entregable (E-xx)

| ID | Partner / ref | Seller | Tool(s) | Verificación |
|----|---------------|--------|---------|--------------|
| E-01 | `demo_cust_urgent` o `demo_cust_multi_trigger` | A | Flujo completo detección | Opp en *Pendiente de revisión* con descripción accionable |
| E-02 | `demo_cust_multi_trigger` | A | `create_crm_opportunity` | Evidencia multi legible (no JSON crudo) |
| E-03 | Runtime — ver abajo | A | Flujo completo | Cero `sale.order` nuevos del usuario agente |
| E-04 | Runtime — ver abajo | A | `create_crm_opportunity` | `REACT-YYYY-#####`, `cycle_id`, metadata persistida |

---

## Sección 6 — Guardrails (G-xx)

| ID | Partner / ref | Seller | Tool(s) | Verificación |
|----|---------------|--------|---------|--------------|
| G-01 | `demo_cust_other_seller` (cartera B) con identidad A | A | Tools seller-scoped | Error/vacío |
| G-02 | `demo_cust_company_b_isolated` | C | Tools seller-scoped | Scope correcto multicompañía |
| G-03 | — | — | Registry `llm.tool` | 6 tools; test unitario `test_helpers_are_not_llm_tools` |

---

## Escenarios runtime (sin data estática adicional)

### P-05 — Stock race

1. `get_product_recommendations` sobre un cliente con `demo_prod_in_stock`.
2. Reducir stock de `demo_prod_in_stock` a 0 (`stock.quant` o venta manual).
3. `create_crm_opportunity` con ese SKU → debe rechazar.

### E-01 / E-03 / E-04 — Ciclo completo

1. `bootstrap_reactivation_cycle`.
2. Por cliente (`demo_cust_urgent` o `demo_cust_multi_trigger`): contexto → recomendaciones → `create_crm_opportunity`.
3. Verificar opp CRM y ausencia de `sale.order` nuevos (E-03).
4. Verificar metadata de atribución (E-04).

---

## Recarga demo en desarrollo

```bash
# Desde shell Odoo o post-install
env["tommasi.reactivation.demo"]._reload_demo_transactions()

# O actualizar módulo con demo
docker-compose run --rm odoo odoo -u tommasi_sales_reactivation --stop-after-init
```
