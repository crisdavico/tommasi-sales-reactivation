# CRM Opportunity Star Categories Specification

## Purpose

Description-only **Categorías Estrellas** on reactivation CRM opportunities.

## Requirements

### Requirement: Rank Top Three Direct Categories

On create, the system MUST rank at most three **direct** categories (no parent rollup) by positive invoiced units over 180 days, ties by name ascending after units descending. Qualifying lines MUST be posted `out_invoice` on the commercial-partner tree, active stockable products with a resolvable direct category, and positive units. Credit notes MUST NOT rank or net. Parents MUST NOT receive rolled-up child units.

#### Scenario: Happy path

- GIVEN four qualifying direct categories in 180 days
- WHEN a qualifying opportunity is created
- THEN the description MUST show the top three by units and omit the fourth

#### Scenario: Refunds excluded

- GIVEN a posted invoice of 10 units in A and a posted credit note of 4 units for the same product
- WHEN categories are ranked
- THEN A MUST count 10 units

#### Scenario: Missing product or category

- GIVEN lines with missing, inactive, or non-stockable product, or no resolvable direct category
- WHEN categories are ranked
- THEN those units MUST NOT appear as a row and MUST NOT enter the share denominator

#### Scenario: Name ascending on a tie

- GIVEN Alpha and Beta with 10 units each and Gamma with 5
- WHEN the top three are ranked
- THEN order MUST be Alpha, then Beta, then Gamma

### Requirement: Company, Seller, and Authorization Scope

Ranking MUST include only invoices in companies allowed in the current Odoo service environment. Seller authorization MUST remain existing CRM-create guards (enabled seller and customer assignment). Ranking MUST be customer commercial-partner scoped and MUST NOT bypass those guards.

#### Scenario: Company allow-list

- GIVEN matching invoices in an allowed company and in a disallowed company
- WHEN categories are ranked
- THEN only allowed-company units MUST count

#### Scenario: Unauthorized caller

- GIVEN a caller who fails existing seller or assignment authorization
- WHEN they request opportunity creation
- THEN the system MUST reject the write as today and MUST NOT persist a lead only to render star categories

### Requirement: Share, Placement, and Independent Omit

Each row MUST show share as one-decimal percent of **all qualifying units** (not the top-3 subtotal), rounded half up (1 of 3 units MUST display `33.3%`). Top-3 shares MUST NOT need to total 100%. Non-empty ranking MUST render heading **Categorías Estrellas** immediately after **Productos Estrellas** (name, units, share). The name column MUST use `product.category` `display_name` (hierarchical complete name), not the short `name`. Empty ranking MUST omit heading and table even if other sections exist. The system MUST NOT add a `crm.lead` field or CRM tag.

#### Scenario: Denominator is all qualifying units

- GIVEN qualifying units A=50, B=30, C=15, D=5
- WHEN the section is rendered
- THEN shares MUST be `50.0%`, `30.0%`, and `15.0%`; D MUST NOT appear

#### Scenario: Hierarchical display name

- GIVEN a child category under a parent that qualifies
- WHEN the section is rendered
- THEN the name MUST be that category's `display_name` (hierarchical complete name), not the short `name`

#### Scenario: Placement and columns

- GIVEN both Productos Estrellas and star categories qualify
- WHEN the description is written
- THEN **Categorías Estrellas** MUST follow **Productos Estrellas** immediately with name, units, and one-decimal share

#### Scenario: Independent omit

- GIVEN other description sections exist and ranking is empty
- WHEN the description is written
- THEN **Categorías Estrellas** MUST be absent; other sections MAY still appear

### Requirement: Unchanged MCP, Agent, and Idempotent Replay

MCP tools, envelopes (`{"data": <payload>, "request_id": "<optional>"}`), mirrored fixtures, and the LangGraph agent MUST remain unchanged. Replay of the same `operation_key` MUST return `existing` and MUST NOT rewrite the stored description.

#### Scenario: Envelope unchanged

- GIVEN a create that stores Categorías Estrellas in the lead description
- WHEN the MCP create tool returns
- THEN the envelope MUST be `{"data": <payload>, "request_id": "<optional>"}` with no description HTML or star-categories field

#### Scenario: Idempotent replay

- GIVEN an existing lead for the same `operation_key`
- WHEN create is requested again with that key
- THEN the outcome MUST be `existing` and the stored description MUST remain unchanged
