# Seller Open Opportunities Specification

## Purpose

Read-only MCP tool that lists every open agent CRM opportunity for one seller.

## Requirements

### Requirement: Seller-Wide Open Agent List

The system MUST expose `get_seller_open_opportunities` with required `seller_id` and optional `request_id`. Open MUST mean `reactivation_is_agent` is true, `active` is true, `user_id` equals `seller_id`, and stage is one of **Pendiente de revisión** or **Cliente contactado** (`OPEN_REACTIVATION_STAGE_XML_IDS`). The tool MUST NOT accept `statuses` / `stage_xml_ids` overrides in this version. Results MUST be a flat list ordered by `create_date` descending. An empty portfolio MUST be success.

#### Scenario: Flat list across customers

- GIVEN two open agent opportunities for the same seller on different customers
- WHEN `get_seller_open_opportunities` is called with that `seller_id`
- THEN the response MUST list both rows in one `opportunities` array

#### Scenario: Empty portfolio

- GIVEN a seller with no open agent opportunities
- WHEN the tool is called
- THEN `data.opportunities` MUST be `[]` and MUST NOT be an error payload

#### Scenario: Newest first

- GIVEN two open agent opportunities with different `create_date` values
- WHEN the tool is called
- THEN rows MUST be ordered by `create_date` descending

### Requirement: Exclusions and Seller Scope

The list MUST exclude non-agent leads, archived leads, Won/Lost and any other non-open stage, and opportunities assigned to another seller. Search MUST use the seller-scoped environment (`reactivation_seller_id`) so reactivation record rules apply. The open-agent domain MUST use the same predicates as `_count_open_agent_opportunities`.

#### Scenario: Non-open rows excluded

- GIVEN open agent leads plus a non-agent lead, an archived agent lead, and a Won agent lead for the same seller
- WHEN the tool is called
- THEN only the open-stage active agent leads MUST appear

#### Scenario: Other seller not leaked

- GIVEN an open agent opportunity assigned to seller B
- WHEN the tool is called with seller A
- THEN seller B's opportunity MUST NOT appear

### Requirement: Seller-Facing Row Shape and Envelope

Each row MUST include exactly these fields: `customer_name`, `stage`, `created_at`, `opportunity_url`, `client_message`. `client_message` MUST always be present and MUST be `""` when missing. Rows MUST NOT include `opportunity_id` or `customer_id`. `opportunity_url` MUST be the CRM form URL from `_crm_lead_form_url`. The tool MUST return `{"data": <payload>, "request_id": "<optional>"}` with `data` keys `seller_id` and `opportunities`.

#### Scenario: Envelope

- GIVEN any successful call
- WHEN the tool returns
- THEN the envelope MUST be `{"data": {"seller_id": <int>, "opportunities": [...]}, "request_id": "<optional>"}`

#### Scenario: Empty client message

- GIVEN an open agent opportunity with no stored client message
- WHEN the tool is called
- THEN that row's `client_message` MUST be `""`

#### Scenario: No identifiers

- GIVEN an open agent opportunity
- WHEN the row is serialized
- THEN it MUST NOT contain `opportunity_id` or `customer_id`

### Requirement: Unchanged Customer-Scoped Read Tool

`get_agent_opportunities` MUST still require exactly one of `customer_id` or `customer_ids`. This change MUST NOT alter its envelope or row shape.

#### Scenario: Missing customer id still errors

- GIVEN a call to `get_agent_opportunities` with only `seller_id`
- WHEN the tool runs
- THEN it MUST return `{message: "Provide exactly one of customer_id or customer_ids."}`
