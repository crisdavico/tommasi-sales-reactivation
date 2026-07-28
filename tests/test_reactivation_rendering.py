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
class TestReactivationRendering(ReactivationServiceTestMixin, TransactionCase):
    def test_render_evidence_revenue_decline_section(self):
        service = self._service()
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["revenue_decline"],
                    "details": {
                        "revenue_decline": {
                            "decline_pct": 25,
                            "prior_revenue": 1000,
                            "recent_revenue": 750,
                        }
                    },
                }
            }
        )
        self.assertIn("caída del 25%", html)
        self.assertIn("facturación 1000", html)

    def test_render_evidence_revenue_decline_uses_revenue_change_pct(self):
        service = self._service()
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["revenue_decline"],
                    "details": {
                        "revenue_decline": {
                            "revenue_change_pct": -30,
                            "prior_revenue": 1000,
                            "recent_revenue": 700,
                        }
                    },
                }
            }
        )
        self.assertIn("caída del 30%", html)
        self.assertNotIn("revenue_change_pct", html)

    def test_render_commercial_rationale_translates_revenue_change_pct(self):
        service = self._service()
        html = service._render_commercial_rationale_section_html(
            {
                "signals": [
                    {
                        "label": "Caída de ingresos",
                        "summary": "Facturación en descenso",
                        "metrics": {"revenue_change_pct": -30},
                    }
                ]
            }
        )
        self.assertIn("Variación de facturación (%): -30", html)
        self.assertNotIn("revenue_change_pct", html)

    def test_render_volume_decline_evidence_accepts_items_key(self):
        service = self._service()
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["volume_decline_with_stock"],
                    "details": {
                        "volume_decline_with_stock": {
                            "items": [
                                {
                                    "product_id": 133,
                                    "sku": "DEMO-OK-001",
                                    "name": "Producto genérico OK",
                                    "prior_quantity": 2.0,
                                    "recent_quantity": 0.0,
                                    "available_qty": 50.0,
                                }
                            ]
                        }
                    },
                }
            }
        )
        self.assertIn("DEMO-OK-001", html)
        self.assertIn("Producto genérico OK", html)
        self.assertIn("Cant. anterior", html)
        self.assertIn("Cant. reciente", html)
        self.assertIn("Fecha compra anterior", html)
        self.assertIn("Fecha compra reciente", html)
        self.assertIn("Últ. pedido/factura", html)
        self.assertNotIn("Campo", html)
        self.assertNotIn('"items"', html)

    def test_render_volume_decline_evidence_shows_dates(self):
        service = self._service()
        prior_date = "2025-04-04"
        recent_date = "2025-06-15"
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["volume_decline_with_stock"],
                    "details": {
                        "volume_decline_with_stock": {
                            "items": [
                                {
                                    "sku": "REACT-DATE",
                                    "name": "Producto con fechas",
                                    "prior_quantity": 10.0,
                                    "prior_purchase_date": prior_date,
                                    "recent_quantity": 1.0,
                                    "recent_purchase_date": recent_date,
                                    "last_purchase_or_order_date": recent_date,
                                    "available_qty": 12.0,
                                }
                            ]
                        }
                    },
                }
            }
        )
        self.assertIn("Fecha compra anterior", html)
        self.assertIn("Fecha compra reciente", html)
        self.assertIn("Últ. pedido/factura", html)
        self.assertIn(self._format_display_date(prior_date), html)
        self.assertIn(self._format_display_date(recent_date), html)

    def test_render_dropoff_evidence_uses_days_since_last_purchase_alias(self):
        product = self.env["product.product"].create(
            {
                "name": "Producto dropoff",
                "default_code": "DROP-001",
                "type": "product",
            }
        )
        self._add_product_stock(product, quantity=10.0)
        service = self._service()
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["dropoff"],
                    "details": {
                        "dropoff": {
                            "products": [
                                {
                                    "product_id": product.id,
                                    "sku": "DROP-001",
                                    "name": "Producto dropoff",
                                    "last_purchase_date": "2025-04-04",
                                    "days_since_last_purchase": 60,
                                }
                            ]
                        }
                    },
                }
            }
        )
        self.assertIn("Caída de compra", html)
        self.assertIn("Días transcurridos", html)
        self.assertIn("Stock", html)
        self.assertIn(">60</td>", html)
        self.assertIn(">10</td>", html)

    def test_render_dropoff_evidence_computes_days_from_last_purchase_date(self):
        product = self.env["product.product"].create(
            {
                "name": "Producto sin dias",
                "default_code": "DROP-002",
                "type": "product",
            }
        )
        self._add_product_stock(product, quantity=12.0)
        service = self._service()
        today = fields.Date.context_today(self.env["crm.lead"])
        last_date = fields.Date.to_string(today - timedelta(days=45))
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["dropoff"],
                    "details": {
                        "dropoff": {
                            "products": [
                                {
                                    "product_id": product.id,
                                    "sku": "DROP-002",
                                    "name": "Producto sin dias",
                                    "last_purchase_date": last_date,
                                }
                            ]
                        }
                    },
                }
            }
        )
        self.assertIn(">45</td>", html)
        self.assertIn("Stock", html)
        self.assertIn(">12</td>", html)

    def test_render_dropoff_evidence_excludes_low_stock_products(self):
        config = self.Config.get_singleton()
        config.write({"low_stock_threshold": 5})
        low_stock_product = self.env["product.product"].create(
            {
                "name": "Producto stock bajo",
                "default_code": "DROP-LOW",
                "type": "product",
            }
        )
        in_stock_product = self.env["product.product"].create(
            {
                "name": "Producto con stock",
                "default_code": "DROP-OK",
                "type": "product",
            }
        )
        self._add_product_stock(low_stock_product, quantity=3.0)
        self._add_product_stock(in_stock_product, quantity=15.0)
        service = self._service()
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["dropoff"],
                    "details": {
                        "dropoff": {
                            "products": [
                                {
                                    "product_id": low_stock_product.id,
                                    "sku": "DROP-LOW",
                                    "name": "Producto stock bajo",
                                    "last_purchase_date": "2025-04-04",
                                    "days_since": 60,
                                },
                                {
                                    "product_id": in_stock_product.id,
                                    "sku": "DROP-OK",
                                    "name": "Producto con stock",
                                    "last_purchase_date": "2025-03-01",
                                    "days_since": 90,
                                },
                            ]
                        }
                    },
                }
            }
        )
        self.assertIn("DROP-OK", html)
        self.assertIn("Producto con stock", html)
        self.assertIn(">15</td>", html)
        self.assertNotIn("DROP-LOW", html)
        self.assertNotIn("Producto stock bajo", html)

    def test_render_dropoff_evidence_shows_empty_message_when_all_below_threshold(
        self,
    ):
        config = self.Config.get_singleton()
        config.write({"low_stock_threshold": 5})
        product = self.env["product.product"].create(
            {
                "name": "Producto sin stock suficiente",
                "default_code": "DROP-NONE",
                "type": "product",
            }
        )
        self._add_product_stock(product, quantity=2.0)
        service = self._service()
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["dropoff"],
                    "details": {
                        "dropoff": {
                            "products": [
                                {
                                    "product_id": product.id,
                                    "sku": "DROP-NONE",
                                    "name": "Producto sin stock suficiente",
                                    "last_purchase_date": "2025-04-04",
                                    "days_since": 60,
                                }
                            ]
                        }
                    },
                }
            }
        )
        self.assertIn("Ningún producto con stock por encima del umbral mínimo", html)
        self.assertNotIn("DROP-NONE", html)

    def test_render_undelivered_evidence_shows_order_date(self):
        service = self._service()
        order_date = "2025-04-04"
        html = service._render_evidence_section_html(
            {
                "evidence": {
                    "triggers": ["undelivered_so_lines"],
                    "details": {
                        "undelivered_so_lines": {
                            "lines": [
                                {
                                    "sale_order_id": 101,
                                    "sale_order_name": "S236661",
                                    "order_date": order_date,
                                    "sku": "IK16",
                                    "name": "Bujia",
                                    "ordered_qty": 4.0,
                                    "delivered_qty": 0.0,
                                    "pending_qty": 4.0,
                                    "available_qty": 13.0,
                                }
                            ]
                        }
                    },
                }
            }
        )
        self.assertIn("Fecha pedido", html)
        self.assertIn("S236661", html)
        self.assertIn(self._format_display_date(order_date), html)

    def test_render_evidence_section_formats_shorthand_json(self):
        service = self._service()
        html = service._render_evidence_section_html(
            {"evidence_summary": '{"days_inactive": 50, "tier": "secondary"}'}
        )
        self.assertIn("50 días desde la última compra", html)
        self.assertIn("nivel de inactividad secundario", html)
        self.assertNotIn('{"days_inactive"', html)

    def test_render_evidence_section_keeps_plain_text_summary(self):
        service = self._service()
        html = service._render_evidence_section_html(
            {"evidence_summary": "Customer inactive for 60 days."}
        )
        self.assertIn("Customer inactive for 60 days.", html)
        self.assertNotIn("<table>", html)

    def test_render_opportunity_description_section_order(self):
        service = self._service()
        html = service._render_opportunity_description(
            {
                "customer_id": self.customer.id,
                "seller_id": self.seller_user.id,
                "suggested_products": [
                    {
                        "product_id": self.product.id,
                        "sku": self.product.default_code,
                        "name": self.product.name,
                        "list_price": 100.0,
                        "available_qty": 10.0,
                    }
                ],
                "evidence": {
                    "triggers": ["inactivity"],
                    "details": {"inactivity": {"days_inactive": 30, "tier": "primary"}},
                },
                "client_message": "Hola.",
            },
            stock_batch={self.product.id: {"available_qty": 10.0}},
        )
        evidence_pos = html.index("Resumen de evidencia")
        stars_pos = html.index("Productos Estrellas")
        products_pos = html.index("Productos sugeridos")
        self.assertLess(evidence_pos, stars_pos)
        self.assertLess(stars_pos, products_pos)

    def test_render_star_products_section_html(self):
        service = self._service()
        html = service._render_star_products_section_html(
            [
                {
                    "name": "Star Product",
                    "sku": "STAR-001",
                    "total_quantity": 12,
                }
            ]
        )
        self.assertIn("Productos Estrellas", html)
        self.assertIn("<th>Nombre</th>", html)
        self.assertIn("<th>SKU</th>", html)
        self.assertIn("<th>Unidades (últimos 6 meses)</th>", html)
        self.assertIn("Star Product", html)
        self.assertIn("STAR-001", html)
        self.assertIn(">12<", html)

    def test_render_star_products_section_html_omits_empty_rows(self):
        service = self._service()
        html = service._render_star_products_section_html([])
        self.assertEqual(html, "")

    def test_render_opportunity_description_omits_star_products_without_customer(self):
        service = self._service()
        html = service._render_opportunity_description(
            {
                "suggested_products": [
                    {
                        "product_id": self.product.id,
                        "sku": self.product.default_code,
                        "name": self.product.name,
                        "list_price": 100.0,
                        "available_qty": 10.0,
                    }
                ],
                "client_message": "Hola.",
            },
            stock_batch={self.product.id: {"available_qty": 10.0}},
        )
        self.assertNotIn("Productos Estrellas", html)

    def test_render_suggested_products_omits_currency_column(self):
        service = self._service()
        html = service._render_suggested_products_section_html(
            [
                {
                    "product_id": self.product.id,
                    "sku": self.product.default_code,
                    "name": self.product.name,
                    "list_price": 100.0,
                    "available_qty": 10.0,
                    "currency": "ARS",
                }
            ],
            {self.product.id: {"available_qty": 10.0}},
        )
        self.assertNotIn("<th>Moneda</th>", html)
        self.assertNotIn(">ARS</td>", html)
        self.assertIn("$ 100,00", html)

    def test_render_suggested_products_formats_net_price_as_ars(self):
        service = self._service()
        html = service._render_suggested_products_section_html(
            [
                {
                    "product_id": self.product.id,
                    "sku": self.product.default_code,
                    "name": self.product.name,
                    "list_price": 75000.0,
                    "available_qty": 10.0,
                }
            ],
            {self.product.id: {"available_qty": 10.0}},
        )
        self.assertIn("$ 75.000,00", html)
        self.assertNotIn("75000.0", html)

    def test_format_client_message_prices_replaces_suggested_product_amounts(self):
        service = self._service()
        formatted = service._format_client_message_prices(
            "Tenemos stock a 75000.0 y también a $92000.",
            [
                {"list_price": 75000.0},
                {"list_price": 92000.0},
            ],
        )
        self.assertIn("$ 75.000,00", formatted)
        self.assertIn("$ 92.000,00", formatted)
        self.assertNotIn("75000.0", formatted)
        self.assertNotIn("$92000", formatted)

    def test_render_suggested_products_translates_reason_tier(self):
        service = self._service()
        html = service._render_suggested_products_section_html(
            [
                {
                    "product_id": self.product.id,
                    "sku": self.product.default_code,
                    "name": self.product.name,
                    "list_price": 100.0,
                    "available_qty": 10.0,
                    "reason_tier": "dropoff",
                },
                {
                    "product_id": self.product.id,
                    "sku": "CAT-001",
                    "name": "Category Product",
                    "list_price": 50.0,
                    "available_qty": 5.0,
                    "reason_tier": "similar_category",
                },
            ],
            {self.product.id: {"available_qty": 10.0}},
        )
        self.assertIn("<th>Explicación</th>", html)
        self.assertIn("Caída de compra", html)
        self.assertIn("Misma categoría", html)
        self.assertIn(
            "El cliente lo compraba habitualmente y lleva más tiempo del esperado",
            html,
        )
        self.assertIn(
            "Pertenece a una categoría que el cliente ya compra",
            html,
        )
        self.assertNotIn(">dropoff</td>", html)
        self.assertNotIn(">similar_category</td>", html)

    def test_render_commercial_rationale_section_keeps_plain_text(self):
        service = self._service()
        html = service._render_commercial_rationale_section_html(
            "Cliente inactivo con historial de compras en aceites."
        )
        self.assertIn("Cliente inactivo con historial de compras en aceites.", html)
        self.assertNotIn("<table", html)
