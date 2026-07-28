import json
import threading
from datetime import date, timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.tommasi_sales_reactivation.tests.common import (
    ReactivationServiceTestMixin,
    ReactivationServiceTester,
    confirm_test_sale_order,
    post_test_out_invoice,
)


@tagged("post_install", "-at_install")
class TestReactivationCrmCreate(ReactivationServiceTestMixin, TransactionCase):
    def test_create_crm_opportunity_rejects_missing_customer_id(self):
        payload = self._valid_create_payload()
        payload.pop("customer_id")
        result = self._service().create_crm_opportunity(payload)
        self.assertEqual(result["message"], "customer_id is required.")

    def test_create_crm_opportunity_rejects_invalid_priority_label(self):
        self._add_product_stock(self.product)
        result = self._service().create_crm_opportunity(
            self._valid_create_payload(priority_label="critical")
        )
        self.assertEqual(
            result["message"], "priority_label must be low, medium, or high."
        )

    def test_create_crm_opportunity_rejects_disabled_seller(self):
        self._add_product_stock(self.product)
        disabled_user = self.env["res.users"].create(
            {
                "name": "Disabled Reactivation Seller",
                "login": "disabled_reactivation_seller_test",
                "email": "disabled_reactivation_seller_test@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        disabled_user.partner_id.write(
            {
                "mobile": "+5491199887766",
            }
        )
        customer = self.env["res.partner"].create(
            {
                "name": "Disabled Seller Customer",
                "user_id": disabled_user.id,
            }
        )
        result = self._service().create_crm_opportunity(
            self._valid_create_payload(
                customer_id=customer.id,
                seller_id=disabled_user.id,
            )
        )
        self.assertEqual(result["message"], "Seller is not enabled for reactivation.")

    def test_create_crm_opportunity_rejects_ineligible_product_type(self):
        service_product = self.env["product.product"].create(
            {
                "name": "Service Only Product",
                "default_code": "REACT-SVC-ONLY",
                "type": "service",
            }
        )
        payload = self._valid_create_payload(
            suggested_products=[
                {
                    "product_id": service_product.id,
                    "sku": service_product.default_code,
                    "name": service_product.name,
                    "list_price": 100.0,
                    "available_qty": 10.0,
                }
            ]
        )
        result = self._service().create_crm_opportunity(payload)
        self.assertIn("must be active with type product", result["message"])

    def test_create_crm_opportunity_enriches_pricelist_pricing_server_side(self):
        catalog_product = self.env["product.product"].create(
            {
                "name": "CRM Pricelist Product",
                "default_code": "REACT-CRM-PL",
                "type": "product",
                "list_price": 80000.0,
            }
        )
        pricelist = self.env["product.pricelist"].create(
            {"name": "CRM Reactivation Pricelist"}
        )
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": pricelist.id,
                "applied_on": "1_product",
                "compute_price": "fixed",
                "fixed_price": 75000.0,
                "product_tmpl_id": catalog_product.product_tmpl_id.id,
            }
        )
        customer = self.env["res.partner"].create(
            {
                "name": "CRM Pricelist Customer",
                "vat": "30-88776655-4",
                "user_id": self.seller_user.id,
                "customer_rank": 1,
                "property_product_pricelist": pricelist.id,
            }
        )
        self._add_product_stock(catalog_product)
        payload = self._valid_create_payload(
            customer_id=customer.id,
            suggested_products=[
                {
                    "product_id": catalog_product.id,
                    "sku": catalog_product.default_code,
                    "name": catalog_product.name,
                    "list_price": 99999.0,
                    "available_qty": 10.0,
                }
            ],
        )
        result = self._service().create_crm_opportunity(payload)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        description = lead.description or ""
        self.assertIn("$ 75.000,00", description)
        self.assertNotIn("99999.0", description)
        self.assertIn("6.25%", description)
        self.assertNotIn("Precio oferta", description)

    def test_create_crm_opportunity_happy_path(self):
        self._add_product_stock(self.product)
        result = self._service().create_crm_opportunity(self._valid_create_payload())
        self.assertIn("opportunity_id", result)
        self.assertIn("attribution_id", result)
        self.assertNotIn("message", result)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        self.assertTrue(lead.reactivation_is_agent)
        self.assertEqual(lead.stage_id, self.stage_pendiente)
        self.assertEqual(lead.reactivation_attribution_id, result["attribution_id"])
        self.assertTrue(lead.reactivation_attribution_id.startswith("REACT-"))
        self.assertEqual(
            lead.reactivation_client_message,
            "Hola, le escribo de Tommasi con una oferta.",
        )
        self.assertEqual(lead.reactivation_trigger_type, "inactivity")
        self.assertEqual(lead.reactivation_confidence, 0.85)
        self.assertEqual(lead.reactivation_cycle_id, "test-cycle-001")
        self.assertEqual(lead.user_id, self.seller_user)
        self.assertEqual(lead.partner_id, self.customer)
        self.assertEqual(
            lead.company_id,
            self.Config.get_primary_config().company_id,
        )
        self.assertIn('class="table table-bordered"', lead.description or "")
        self.assertIn("Precio neto", lead.description or "")
        self.assertNotIn("Precio oferta", lead.description or "")
        self.assertNotIn("Confianza y origen", lead.description or "")
        self._add_product_stock(self.product)
        evidence = {
            "triggers": ["inactivity", "undelivered_so_lines"],
            "details": {
                "inactivity": {"days_inactive": 141, "tier": "secondary"},
                "undelivered_so_lines": {
                    "lines": [
                        {
                            "sale_order_id": 340903,
                            "sale_order_name": "S236661",
                            "sku": "IK16",
                            "name": "[IK16] Bujia De Encendido",
                            "ordered_qty": 4.0,
                            "delivered_qty": 0.0,
                            "pending_qty": 4.0,
                            "available_qty": 13.0,
                        },
                        {
                            "sale_order_id": 328648,
                            "sale_order_name": "S224406",
                            "sku": "BI0014MMA",
                            "name": "[BI0014MMA] Bobina De Encendido",
                            "ordered_qty": 2.0,
                            "delivered_qty": 0.0,
                            "pending_qty": 2.0,
                            "available_qty": 1.0,
                        },
                    ]
                },
            },
        }
        payload = self._valid_create_payload(
            trigger_type="multi",
            evidence_summary=json.dumps(evidence),
        )
        result = self._service().create_crm_opportunity(payload)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        description = lead.description or ""
        self.assertIn("Disparadores:", description)
        self.assertIn("Inactividad", description)
        self.assertIn("Pedidos pendientes sin entregar", description)
        self.assertIn("141 días desde la última compra", description)
        self.assertIn("nivel de inactividad secundario", description)
        self.assertIn("S236661", description)
        self.assertIn("S224406", description)
        self.assertIn("IK16", description)
        self.assertIn("2 línea(s) pendiente(s) en 2 pedido(s) de venta.", description)
        self.assertNotIn('"triggers"', description)
        self.assertIn('class="table table-bordered"', description)
        self.assertIn("<thead>", description)
        self.assertIn("<tbody>", description)

    def test_create_crm_opportunity_accepts_evidence_dict(self):
        self._add_product_stock(self.product)
        payload = self._valid_create_payload(
            evidence={
                "triggers": ["inactivity"],
                "details": {"inactivity": {"days_inactive": 47, "tier": "primary"}},
            }
        )
        result = self._service().create_crm_opportunity(payload)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        description = lead.description or ""
        self.assertIn("47 días desde la última compra", description)
        self.assertIn("nivel de inactividad primario", description)

    def test_create_crm_opportunity_formats_shorthand_evidence_json(self):
        self._add_product_stock(self.product)
        payload = self._valid_create_payload(
            evidence_summary='{"days_inactive": 50, "tier": "secondary"}',
        )
        result = self._service().create_crm_opportunity(payload)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        description = lead.description or ""
        self.assertIn("50 días desde la última compra", description)
        self.assertIn("nivel de inactividad secundario", description)
        self.assertNotIn('{"days_inactive"', description)

    def test_create_crm_opportunity_formats_client_message_prices(self):
        self._add_product_stock(self.product)
        payload = self._valid_create_payload(
            client_message=("Hola, tenemos [REACT-001] Producto demo a 100.0 neto."),
            suggested_products=[
                {
                    "product_id": self.product.id,
                    "sku": self.product.default_code,
                    "name": self.product.name,
                    "list_price": 100.0,
                    "available_qty": 10.0,
                }
            ],
        )
        result = self._service().create_crm_opportunity(payload)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        self.assertIn("$ 100,00", lead.reactivation_client_message)
        self.assertIn("$ 100,00", lead.description or "")

    def test_create_crm_opportunity_formats_structured_commercial_rationale(self):
        self._add_product_stock(self.product)
        commercial_rationale = {
            "trigger_type": "inactivity",
            "consolidated": False,
            "signals": [
                {
                    "trigger_type": "inactivity",
                    "label": "Inactividad",
                    "summary": "Sin compras hace 50 días",
                    "tier": "secondary",
                    "tier_label": "umbral secundario",
                    "metrics": {"days_inactive": 50, "tier": "secondary"},
                }
            ],
            "products": [
                {
                    "tier": "similar_category",
                    "tier_label": "categoría similar",
                    "product_id": 129,
                    "sku": "DEMO-CAT-001",
                    "name": "[DEMO-CAT-001] Aceite demo misma cat.",
                    "list_price": 92000.0,
                    "offer_unit_price": 41400.0,
                    "stock_qty": 40,
                    "low_stock": False,
                },
                {
                    "tier": "similar_category",
                    "tier_label": "categoría similar",
                    "product_id": 132,
                    "sku": "DEMO-LOW-001",
                    "name": "[DEMO-LOW-001] Producto stock bajo",
                    "list_price": 72000.0,
                    "offer_unit_price": 32400.0,
                    "stock_qty": 3,
                    "low_stock": True,
                },
            ],
        }
        payload = self._valid_create_payload(
            commercial_rationale=commercial_rationale,
        )
        result = self._service().create_crm_opportunity(payload)
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        description = lead.description or ""
        self.assertIn("Fundamentación comercial", description)
        self.assertIn("Señales de reactivación", description)
        self.assertNotIn("Productos en la fundamentación", description)
        self.assertIn("Inactividad", description)
        self.assertIn("Sin compras hace 50 días", description)
        self.assertIn("umbral secundario", description)
        self.assertIn("Days Inactive: 50", description)
        self.assertNotIn("DEMO-CAT-001", description)
        self.assertNotIn("DEMO-LOW-001", description)
        self.assertNotIn('"signals"', description)
        self.assertIn("Productos Estrellas", description)
        self.assertEqual(description.count('class="table table-bordered"'), 3)

    def test_create_crm_opportunity_preserves_client_message_newlines(self):
        self._add_product_stock(self.product)
        message = "Hola,\n\nTenemos una oferta.\nSaludos."
        result = self._service().create_crm_opportunity(
            self._valid_create_payload(client_message=message)
        )
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        self.assertEqual(lead.reactivation_client_message, message)
        description = lead.description or ""
        self.assertIn("Mensaje al cliente", description)
        self.assertIn("Hola,", description)
        self.assertIn("Tenemos una oferta.", description)
        self.assertIn("Saludos.", description)
        self.assertIn("<p>Hola,</p><p>Tenemos una oferta.", str(description))

    def test_create_crm_opportunity_normalizes_literal_newline_escapes(self):
        self._add_product_stock(self.product)
        message = "Hola,\\n\\nTenemos una oferta."
        result = self._service().create_crm_opportunity(
            self._valid_create_payload(client_message=message)
        )
        lead = self.env["crm.lead"].browse(result["opportunity_id"])
        self.assertEqual(
            lead.reactivation_client_message, "Hola,\n\nTenemos una oferta."
        )
        self.assertIn(
            "<p>Hola,</p><p>Tenemos una oferta.</p>", str(lead.description or "")
        )

    def test_create_crm_opportunity_rejects_missing_client_message(self):
        self._add_product_stock(self.product)
        payload = self._valid_create_payload(client_message="")
        result = self._service().create_crm_opportunity(payload)
        self.assertEqual(result["message"], "client_message is required.")

    def test_create_crm_opportunity_rejects_out_of_stock_sku(self):
        payload = self._valid_create_payload()
        result = self._service().create_crm_opportunity(payload)
        self.assertEqual(
            result["message"],
            "Product %s is out of stock." % self.product.default_code,
        )

    def test_create_crm_opportunity_rejects_low_stock_sku(self):
        config = self.Config.get_singleton()
        config.write({"low_stock_threshold": 5})
        self._add_product_stock(self.product, quantity=3.0)
        payload = self._valid_create_payload()
        result = self._service().create_crm_opportunity(payload)
        self.assertEqual(
            result["message"],
            "Product %s has insufficient stock (available: 3.0, minimum required: 6)."
            % self.product.default_code,
        )

    def test_create_crm_opportunity_respects_configured_stock_locations(self):
        config = self.Config.get_singleton()
        warehouses = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], order="id asc"
        )
        source_warehouse = warehouses[0]
        other_warehouse = (
            warehouses[1]
            if len(warehouses) > 1
            else self.env["stock.warehouse"].create(
                {
                    "name": "Alternate Opportunity Warehouse",
                    "code": "AOW",
                    "company_id": self.env.company.id,
                }
            )
        )
        config.write(
            {"stock_location_ids": [(6, 0, [source_warehouse.lot_stock_id.id])]}
        )
        self._add_product_stock(
            self.product,
            quantity=12.0,
            location=other_warehouse.lot_stock_id,
        )
        result = self._service().create_crm_opportunity(self._valid_create_payload())
        self.assertEqual(
            result["message"],
            "Product %s is out of stock." % self.product.default_code,
        )

        self._add_product_stock(
            self.product,
            quantity=12.0,
            location=source_warehouse.lot_stock_id,
        )
        result = self._service().create_crm_opportunity(self._valid_create_payload())
        self.assertIn("opportunity_id", result)
        config.write({"stock_location_ids": [(5, 0, 0)]})

    def test_create_crm_opportunity_rejects_unowned_customer(self):
        self._add_product_stock(self.product)
        foreign_customer = self.env["res.partner"].create(
            {
                "name": "Foreign Customer",
                "user_id": self.other_seller.id,
            }
        )
        payload = self._valid_create_payload(customer_id=foreign_customer.id)
        result = self._service().create_crm_opportunity(payload)
        self.assertEqual(
            result["message"],
            "Customer is not assigned to the given seller.",
        )
