"""Helpers for MCP tool envelopes in Odoo tests."""

import json
from pathlib import Path

CREATE_CONTRACT_VERSION = 2

V2_CREATE_OUTCOMES = frozenset(
    {"created", "existing", "rejected", "deferred"},
)

SCOPED_OWNERSHIP_REASONS = frozenset(
    {
        "ownership_mismatch",
        "seller_not_enabled",
        "customer_not_found",
    }
)

CONTRACT_TOOL_NAMES = (
    "bootstrap_reactivation_cycle",
    "get_reactivation_candidates",
    "get_agent_opportunities",
    "get_seller_open_opportunities",
    "get_product_recommendations",
    "create_crm_opportunity",
    "send_whatsapp_to_partner",
)

# Additive scoped fixtures; scheduled CONTRACT_TOOL_NAMES goldens stay unchanged.
ADDITIVE_CONTRACT_FIXTURES = (
    "bootstrap_reactivation_cycle_scoped",
    "bootstrap_reactivation_cycle_ownership_fail",
    "get_reactivation_candidates_scoped",
)

TOOL_REQUIRED_DATA_KEYS = {
    "bootstrap_reactivation_cycle": frozenset({"config", "sellers", "customers"}),
    "get_reactivation_candidates": frozenset({"seller_id", "candidates"}),
    "get_agent_opportunities": frozenset({"seller_id", "results"}),
    "get_seller_open_opportunities": frozenset({"seller_id", "opportunities"}),
    "get_product_recommendations": frozenset({"seller_id", "results"}),
    "create_crm_opportunity": frozenset({"results"}),
    "send_whatsapp_to_partner": frozenset(
        {
            "status",
            "partner_id",
            "company_id",
            "http_status",
            "conversation_id",
            "message_id",
            "retryable",
            "error",
        }
    ),
}

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "contracts"


def mcp_data(envelope):
    """Return the inner payload from an MCP tool envelope."""
    assert_mcp_envelope(envelope)
    return envelope["data"]


def is_mcp_envelope(value):
    """Return whether *value* is a strict ``{data, request_id?}`` envelope."""
    return (
        isinstance(value, dict)
        and "data" in value
        and set(value.keys()) <= {"data", "request_id"}
    )


def assert_mcp_envelope(value):
    """Assert *value* is an MCP envelope."""
    if not is_mcp_envelope(value):
        raise AssertionError("expected MCP envelope with data key, got %r" % (value,))
    return value


def assert_create_v2_batch(data):
    """Assert v2 batch create payload shape and outcome vocabulary."""
    if data.get("contract_version") != CREATE_CONTRACT_VERSION:
        raise AssertionError(
            "expected contract_version %s, got %r"
            % (CREATE_CONTRACT_VERSION, data.get("contract_version")),
        )
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise AssertionError("expected non-empty results list")
    outcomes = {row.get("outcome") for row in results if isinstance(row, dict)}
    unexpected = outcomes - V2_CREATE_OUTCOMES
    if unexpected:
        raise AssertionError("unexpected v2 outcomes: %s" % sorted(unexpected))


def assert_ownership_fail_payload(data):
    """Assert scoped bootstrap failure payload with stable reason codes."""
    if not isinstance(data, dict):
        raise AssertionError("ownership fail data must be a dict, got %r" % (data,))
    if "message" not in data or not data["message"]:
        raise AssertionError("ownership fail payload missing message")
    reason = data.get("reason")
    if reason not in SCOPED_OWNERSHIP_REASONS:
        raise AssertionError(
            "ownership fail reason must be one of %s, got %r"
            % (sorted(SCOPED_OWNERSHIP_REASONS), reason)
        )
    forbidden = {"config", "sellers", "customers"} & data.keys()
    if forbidden:
        raise AssertionError(
            "ownership fail payload must not include success keys: %s"
            % sorted(forbidden)
        )
    return data


def assert_tool_contract(tool_name, envelope):
    """Assert envelope shape and required inner keys for one tool."""
    assert_mcp_envelope(envelope)
    data = envelope["data"]
    if not isinstance(data, dict):
        raise AssertionError("%s envelope data must be a dict" % tool_name)
    if "reason" in data:
        assert_ownership_fail_payload(data)
        return data
    # Fixture/tool aliases (e.g. bootstrap_reactivation_cycle_scoped) map to base keys.
    base_tool = tool_name
    for known in TOOL_REQUIRED_DATA_KEYS:
        if tool_name == known or tool_name.startswith(known + "_"):
            base_tool = known
            break
    required = TOOL_REQUIRED_DATA_KEYS.get(base_tool)
    if required and not required <= data.keys():
        missing = ", ".join(sorted(required - data.keys()))
        raise AssertionError("%s missing data keys: %s" % (tool_name, missing))
    if base_tool == "create_crm_opportunity" and "contract_version" in data:
        assert_create_v2_batch(data)
    return data


def classify_tool_response(result):
    """Classify a raw service return as envelope or legacy direct payload.

    After contract hardening all five Agent-used tools return envelopes.
    """
    if is_mcp_envelope(result):
        return "envelope", mcp_data(result)
    return "direct", result


def load_contract_fixture(tool_name):
    """Load a golden MCP envelope fixture mirrored from the Agent repo."""
    path = FIXTURES_DIR / ("%s.json" % tool_name)
    return json.loads(path.read_text(encoding="utf-8"))
