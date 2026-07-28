# Matriz de escenarios de negocio — Tools MCP `tommasi_sales_reactivation`

Escenarios funcionales para validar que la superficie de 6 tools MCP **soporta
las necesidades de negocio** de la PRD `prds/tommasi-reactivation-odoo-prd.md`
(v1.6) y de su parent PRD. La pregunta que responde cada escenario no es "¿el
código funciona?" sino **"¿el agente puede cumplir este caso de negocio con lo
que los tools le devuelven?"**.

**Qué queda explícitamente fuera de esta matriz**

Todo lo verificable por test unitario ya está cubierto por la suite del módulo
(91 tests en `tests/`): validación de inputs y payloads, formatos de teléfono,
bordes de fechas, mapeos de campos, split de mensajes, mecánica de idempotencia,
constraints de configuración. Acá no se repite nada de eso.

**Cómo se ejecuta**

- Cada escenario parte de un **estado de negocio** montado sobre la data demo
  (`data/demo/`) o fixtures equivalentes, y se ejecuta llamando los tools por
  MCP como lo haría el agente real (mismo usuario técnico, mismo orden de
  llamadas de la PRD §7.9).
- **Mapa escenario → ref demo:** [`tests/demo_scenario_map.md`](demo_scenario_map.md).
- El resultado esperado se verifica cruzando contra la base de datos (facturas
  posteadas, stock, etapas CRM), no solo mirando la respuesta del tool.
- IDs estables (`N-01`, `T-03`, …) para referenciar desde corridas en vivo o
  evals automatizados.

---

## 1. Universo del ciclo — ¿el bootstrap arma la cartera correcta? (FR-01, AC-1b)

Tools: `bootstrap_reactivation_cycle`

El negocio necesita que el ciclo diario trabaje **solo** sobre vendedores del
programa y sus compradores habituales. Un universo mal armado significa
vendedores contactados por error o clientes valiosos que nunca entran al ciclo.

| ID | Qué se prueba | Por qué se prueba | Resultado esperado | Caso PRD |
|---|---|---|---|---|
| N-01 | Con una cartera mixta real (vendedores configurados y no configurados en el programa), el bootstrap devuelve exactamente el conjunto de vendedores que el negocio habilitó | La habilitación se administra por UI sin deploy; si el filtro no refleja la config, el agente saltea vendedores del programa o incluye gente equivocada | `sellers[]` contiene exactamente los vendedores con línea en config; agregar/quitar una línea por UI cambia el resultado en la siguiente llamada | FR-01, AC-1, Story 11 |
| N-02 | Con la cartera de un vendedor que incluye compradores habituales, compradores esporádicos (1–2 facturas), prospectos sin compras, sucursales (contactos hijos) y proveedores, el bootstrap devuelve solo los compradores habituales | El costo del ciclo es por cliente (varias llamadas MCP + razonamiento LLM); el negocio definió "comprador habitual" = mínimo de facturas posteadas en la ventana | `customers[]` contiene solo top-level activos con `customer_rank > 0` y ≥ `bootstrap_min_invoices` facturas posteadas en `bootstrap_invoice_window_days`; los demás quedan afuera | AC-1b, §7.2 |
| N-03 | Bajar `bootstrap_min_invoices` por UI (ej. 3 → 1) amplía el universo en la llamada siguiente, sin reiniciar nada | Tommasi puede necesitar ampliar el alcance si el ciclo pierde clientes en declive de frecuencia (PRD §12.7); debe ser una decisión de negocio, no un deploy | El cliente esporádico excluido en N-02 ahora aparece en `customers[]` | AC-1b, §12.7 |
| N-04 | El objeto `config` devuelto alcanza para que el agente aplique **toda** su política sin otra fuente: cooldown, cap por vendedor, umbrales de inactividad, supresión por confianza, reglas de prioridad con sus bandas | La PRD reparte responsabilidades: Odoo guarda parámetros, el agente computa política. Si falta un parámetro, el agente lo hardcodea y la config por UI deja de mandar | `config` incluye `cooldown_days`, `opportunity_cap_per_seller`, `inactivity_days_primary/secondary`, `min_confidence_threshold`, `suggested_products_max` y `priority_rules[]` completas (label, min_confidence, min_inactivity_days, min_decline_pct, cap_order) | D-01, §2, §8 |
| N-05 | Con vendedores habilitados en configs de dos compañías distintas, un solo bootstrap trae ambas carteras sin duplicar vendedores | El ciclo es único para todo Tommasi; multicompañía no puede partirlo ni duplicar contactos | `sellers[]` agregado de todas las compañías, deduplicado por usuario; cada cliente con su `seller_id` correcto | §4.3, §9.1 |

## 2. Señales de detección — ¿el contexto sostiene cada trigger del negocio? (FR-02)

Tools: `get_customer_detection_context`

El parent PRD define los disparadores de reactivación. El agente no consulta la
DB: decide **solo** con lo que este tool devuelve. Cada trigger necesita que su
señal aparezca cuando corresponde y no aparezca cuando no.

| ID | Qué se prueba | Por qué se prueba | Resultado esperado | Caso PRD |
|---|---|---|---|---|
| T-01 | Cliente que compraba todas las semanas y lleva 47 días sin facturas posteadas | Inactividad es el trigger principal; con umbral secundario 45 el agente debe poder justificar reactivación urgente | `last_purchase` refleja la última factura real y `days_inactive` = 47, cruzando el umbral secundario de la config | FR-02 |
| T-02 | Cliente que facturaba ~$1M/mes y en los últimos 2 meses cayó a ~$400k | El agente computa `revenue_decline` agent-side; necesita historia mensual fiel para calcular el % de caída | `sales_history` mensual coincide con las facturas posteadas (verificación SQL) y permite derivar una caída ≥ el `min_decline_pct` de la regla de prioridad | FR-02, D-01 |
| T-03 | Cliente que bajó el volumen de un producto que **hay** en stock, y de otro que **no** hay | La señal `volume_decline_with_stock` existe para ofrecer reposición concreta; sin stock la llamada del vendedor no tiene remate comercial | Solo el producto con stock aparece en `volume_decline_with_stock`, con prior/recent quantity y `available_qty` reales | FR-02, §7.3 |
| T-04 | Cliente que dejó de comprar hace meses un producto que compraba con cadencia regular | Drop-off de producto es un trigger propio, distinto de la inactividad general | `product_history` trae ese producto con su cadencia promedio y última compra, suficiente para que el agente derive el drop-off | FR-02, §7.3 |
| T-05 | Cliente con pedido confirmado entregado a medias por falta de stock | Entregas pendientes son motivo de contacto inmediato (el cliente espera mercadería) | `undelivered_so_lines` muestra la línea con pedido/entregado/pendiente coincidiendo con el `sale.order` real | FR-02 |
| T-06 | Cliente **sano**: compra con frecuencia estable, sin caídas ni pendientes | El costo de un falso positivo es un vendedor molestando a un buen cliente con una oferta de reactivación innecesaria | Ninguna sección del contexto sugiere señal: `days_inactive` bajo, historia estable, `volume_decline_with_stock` y `undelivered_so_lines` vacíos | FR-02, parent §8 |
| T-07 | Cliente con actividad reciente en borradores/canceladas pero sin facturas posteadas en 40 días | El negocio mide actividad por hechos contables (posteado); documentos no confirmados no son compras | El contexto lo muestra inactivo: borradores y canceladas no cuentan en ninguna sección | D-07, FR-02 |

## 3. Dedup, cooldown y cap — ¿los tools dan la base autoritativa? (FR-03)

Tools: `get_agent_opportunities`, `bootstrap_reactivation_cycle`

La política (cooldown, cap) corre agent-side, pero la PRD §8 exige que Odoo
provea el dato autoritativo. Si este read miente, el agente duplica
oportunidades o bloquea clientes que ya podría retrabajar.

| ID | Qué se prueba | Por qué se prueba | Resultado esperado | Caso PRD |
|---|---|---|---|---|
| Q-01 | Cliente con una oportunidad de agente abierta en *Pendiente de revisión*: el flujo de detección completo (contexto → recomendación → dedup) decide no crear otra | Duplicar oportunidades del mismo cliente ensucia el pipeline del vendedor y duplica contactos | `get_agent_opportunities` devuelve la abierta con fechas suficientes para evaluar cooldown; con ese dato el agente no llama a `create_crm_opportunity` | FR-03, AC-12 |
| Q-02 | Cliente cuya única oportunidad de agente fue cerrada como Perdida/Descartada (archivada) hace más días que el cooldown | Un cliente retrabajable no debe quedar bloqueado para siempre por una gestión vieja | La archivada no aparece entre las abiertas; el agente queda habilitado a crear una nueva | FR-03, §6.1 |
| Q-03 | Cliente con oportunidad **manual** (creada por el vendedor, no por el agente) abierta | El dedup del agente es sobre sus propias gestiones; no debe pisarse con el trabajo manual del vendedor ni bloquearse por él | La manual no aparece en el resultado (filtro `reactivation_is_agent`) | FR-03 |
| Q-04 | Vendedor con más oportunidades de agente abiertas que `opportunity_cap_per_seller` | El cap protege al vendedor de un digest inmanejable; el agente lo aplica con datos de estos tools | Entre config (cap + `cap_order` de las reglas de prioridad) y las oportunidades abiertas por cliente, el agente tiene todo para decidir cuáles crear y cuáles suprimir | D-03b, D-01, §8 |

## 4. Oferta de productos — ¿la recomendación es comercialmente accionable? (FR-04, Story 6, D-02)

Tools: `get_product_recommendations`, `create_crm_opportunity`

La oferta llega al cliente final por boca del vendedor, usando el precio neto
de la tarifa del cliente. Producto sin stock, precio equivocado o sugerencia
irrelevante dañan la relación comercial directamente.

| ID | Qué se prueba | Por qué se prueba | Resultado esperado | Caso PRD |
|---|---|---|---|---|
| P-01 | Cliente con señales en varios tiers a la vez (dejó de comprar X, existe alternativa de Y, hay overstock de Z): el orden de la respuesta refleja la prioridad comercial | El negocio definió que reponer lo que el cliente ya compraba (`dropoff`) vale más que empujar overstock; el agente corta a los 3 primeros, así que el orden **es** la oferta | Recomendaciones en orden estricto `dropoff → related → similar_category → similar_customer → overstock`; los 3 primeros son los candidatos comercialmente correctos | §7.4, §8, D-08 |
| P-02 | Ningún producto ofrecido está fuera de catálogo vigente ni sin stock, con un fixture que incluye archivados, no vendibles y stock 0 | Prometer un producto que no se puede entregar con descuento agresivo es el peor resultado posible de la herramienta | Todo item de `recommendations[]` está activo, vendible, storable y con `available_qty > 0` verificado contra `stock.quant` | AC-7, FR-04 |
| P-03 | Cliente con lista de precios especial (ej. mayorista con precios distintos a la lista general) | El vendedor debe comunicar el precio neto de la tarifa asignada; un precio de catálogo o un descuento adicional automático sería erróneo | `list_price` sale de la pricelist asignada al cliente (ARS, sin IVA) y es el precio neto final; `pricelist_discount_pct` refleja el descuento de la línea de tarifa (solo referencia) | AC-8, D-02, §8 |
| P-04 | Producto con poco stock (≤ `low_stock_threshold`) entre los candidatos | D-09: el agente debe poder moderar la promesa ("quedan pocas unidades") sin excluir el producto | El item viene con `low_stock = true` y el agente dispone del dato antes de componer el mensaje | D-09, AC-7 |
| P-05 | Entre la recomendación y la creación de la oportunidad, el stock de un SKU sugerido cae a 0 (venta concurrente) | Story 6: la garantía de stock es **al momento de crear**, no al momento de recomendar; es la protección contra carreras del mundo real | `create_crm_opportunity` rechaza el payload identificando el SKU; no queda oportunidad con oferta invendible | Story 6, AC-7, §8 |

## 5. La oportunidad como entregable — ¿el vendedor recibe algo accionable? (FR-05/06, D-05)

Tools: `create_crm_opportunity`

La oportunidad CRM es el producto final del ciclo de detección: lo que el
vendedor abre a la mañana. Debe ser legible, completa, trazable y no crear
documentos de venta.

| ID | Qué se prueba | Por qué se prueba | Resultado esperado | Caso PRD |
|---|---|---|---|---|
| E-01 | Flujo de detección completo sobre un cliente demo: la oportunidad resultante se puede **trabajar** sin salir del CRM | El vendedor no ve al agente ni sus tools; todo lo que necesita (qué ofrecer, a qué precio, por qué, qué decirle al cliente) tiene que estar en la ficha | Lead en *Pendiente de revisión* asignado al vendedor correcto, prioridad acorde a las reglas, descripción con: tabla de productos (precio lista, descuento, precio oferta), evidencia formateada legible (no JSON), y el mensaje al cliente listo para copiar | AC-3, D-05, FR-05 |
| E-02 | La evidencia estructurada de un caso `multi` (inactividad + caída de volumen) se lee como argumento comercial | El vendedor decide si llama en base a la evidencia; JSON crudo o datos sueltos la vuelven inútil | Cada trigger renderizado como tabla/prosa entendible por un vendedor no técnico | AC-3, §7.5 |
| E-03 | Después de un ciclo completo de detección con creación de oportunidades, no existe ningún documento de venta nuevo | Regla dura del negocio (FR-06): el agente sugiere, jamás vende; cotizaciones fantasma romperían facturación y confianza | Cero `sale.order` / cotizaciones / borradores creados por el usuario agente en toda la corrida | AC-4, FR-06 |
| E-04 | Cada oportunidad del ciclo queda trazable de punta a punta | La atribución (FR-13, aunque el reporte esté diferido) y la auditoría (§15 parent) dependen de esta metadata | Atribución única `REACT-YYYY-#####`, `cycle_id`, `trigger_type`, `confidence`, fuente y creador (usuario agente) persistidos y visibles en las vistas CRM | §6.4, §9.3 |

## 6. Aislamiento de carteras y guardrails globales (§8, §9)

Tools: todos los seller-scoped

El riesgo más caro del sistema: que un contexto de vendedor (o un cliente MCP
malicioso/buggy) acceda a la cartera de otro. La PRD lo exige "sin importar qué
cliente llama".

| ID | Qué se prueba | Por qué se prueba | Resultado esperado | Caso PRD |
|---|---|---|---|---|
| G-01 | Con la identidad del vendedor A, recorrer el flujo completo (contexto, recomendaciones, oportunidades) sobre un cliente del vendedor B | Fuga de datos comerciales entre carteras: precios especiales, historial y gestiones de clientes ajenos | Todos los tools devuelven error de scope o vacío; ningún dato del cliente de B llega a A | AC-11, §9.2 |
| G-02 | En un despliegue multicompañía, el ciclo unificado lee datos de todas las compañías pero siempre dentro del scope del vendedor | El negocio opera compañías separadas con un solo programa de reactivación; unificar compañías no puede aflojar el aislamiento por vendedor | Lecturas cross-company funcionan para el vendedor del contexto y siguen negando carteras ajenas | §9.1 |
| G-03 | La superficie MCP expuesta es exactamente el contrato de la PRD: 6 tools, ningún helper interno invocable | Un helper expuesto (SQL de facturación, scoping) sería una puerta trasera fuera del contrato y de los guardrails | El registry `llm.tool` lista solo los 6 tools de §7; ningún método `_*` es invocable por MCP | §7.0, §2 |

---

## Cobertura de necesidades de negocio

| Necesidad (PRD) | Escenarios |
|---|---|
| FR-01 / AC-1/1b — universo de vendedores y clientes | N-01 … N-05 |
| FR-02 — señales de detección | T-01 … T-07 |
| FR-03 / D-03b — dedup, cooldown, cap | Q-01 … Q-04 |
| FR-04 / Story 6 / D-02 / D-08 / D-09 — oferta de productos | P-01 … P-05 |
| FR-05/06 / D-05 / AC-3/4 — oportunidad CRM | E-01 … E-04 |
| AC-11 / §8 / §9 — seguridad y guardrails | G-01 … G-03 |
| D-01 — política de prioridad configurable | N-04, Q-04 |
| D-07 — solo facturas posteadas | T-07 |

**Fuera de alcance** (no implementado según PRD §12): reporte de atribución
FR-13, `template_params` / plantillas por tipo, grafos `reminder_graph` y
`sales_summary_graph` del lado del agente.
