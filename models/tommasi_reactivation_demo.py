from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

DEMO_TX_MARKER = "DEMO-REACT-TX"
DEMO_DRAFT_MARKER = "DEMO-REACT-DRAFT"
DEMO_CAP_ATTRIBUTION_PREFIX = "REACT-DEMO-CAP-"
DEMO_CAP_OPP_TARGET = 21
DEMO_CAP_FILLER_COUNT = 20


class TommasiReactivationDemo(models.AbstractModel):
    _name = "tommasi.reactivation.demo"
    _description = "Demo transaction loader for sales reactivation MCP tools"

    def _ref(self, xml_id):
        return self.env.ref("tommasi_sales_reactivation.%s" % xml_id)

    def _register_xml_id(self, record, xml_id):
        """Register a demo XML id on a dynamically created record."""
        self.env["ir.model.data"]._update_xmlids(
            [
                {
                    "xml_id": "tommasi_sales_reactivation.%s" % xml_id,
                    "record": record,
                    "noupdate": True,
                }
            ]
        )

    def _demo_xml_ref(self, xml_id):
        """Return a demo record by XML id, or empty recordset if not loaded."""
        return self.env.ref(
            "tommasi_sales_reactivation.%s" % xml_id,
            raise_if_not_found=False,
        )

    @api.model
    def _ensure_demo_company_b_setup(self):
        """Create second company, warehouse, and config for multicompany demos."""
        if self._demo_xml_ref("demo_company_b"):
            return

        main_company = self.env.company
        company_b = (
            self.env["res.company"]
            .sudo()
            .create(
                {
                    "name": "Tommasi Demo Sur",
                    "currency_id": main_company.currency_id.id,
                }
            )
        )
        self._register_xml_id(company_b, "demo_company_b")

        warehouse = self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", company_b.id)], limit=1
        )
        if not warehouse:
            warehouse = (
                self.env["stock.warehouse"]
                .sudo()
                .create(
                    {
                        "name": "Tommasi Demo Sur WH",
                        "code": "TDSUR",
                        "company_id": company_b.id,
                    }
                )
            )
        self._register_xml_id(warehouse, "demo_warehouse_company_b")

        config_b = (
            self.env["tommasi.reactivation.config"]
            .sudo()
            .create(
                {
                    "name": "Sales reactivation (Demo Sur)",
                    "company_id": company_b.id,
                    "active": True,
                    "cooldown_days": 30,
                    "opportunity_cap_per_seller": 20,
                    "low_stock_threshold": 5,
                    "inactivity_days_primary": 20,
                    "inactivity_days_secondary": 35,
                    "suggested_products_max": 5,
                    "discount_pct": 55.0,
                    "reminder_stale_days": 7,
                    "min_confidence_threshold": 0.50,
                }
            )
        )
        self._register_xml_id(config_b, "reactivation_config_company_b")

        main_config = self.env.ref(
            "tommasi_sales_reactivation.reactivation_config_singleton",
            raise_if_not_found=False,
        )
        if main_config:
            for rule in main_config.priority_rule_ids:
                rule.copy({"config_id": config_b.id})

        self._ensure_demo_sale_journals_for_company(company_b)
        self._install_demo_company_b_chart(company_b)

    def _install_demo_company_b_chart(self, company_b):
        """Load the same chart template as the main company on Demo Sur."""
        Account = self.env["account.account"].sudo()
        if Account.search([("company_id", "=", company_b.id)], limit=1):
            return

        main_company = self.env.ref("base.main_company", raise_if_not_found=False)
        template = (
            main_company.chart_template_id
            if main_company and main_company.chart_template_id
            else self.env["account.chart.template"].search([], limit=1)
        )
        if not template:
            return

        # Remove stub sale journals created before chart install (mail alias clash).
        self.env["account.journal"].sudo().search(
            [("company_id", "=", company_b.id)]
        ).unlink()

        template.sudo().try_loading(company=company_b, install_demo=False)
        self._ensure_demo_sale_journals_for_company(company_b)

    def _ensure_demo_seller_line(self, config, user, xml_id=None, notes=None):
        Seller = self.env["tommasi.reactivation.seller"].sudo()
        line = Seller.search(
            [("config_id", "=", config.id), ("user_id", "=", user.id)],
            limit=1,
        )
        if not line:
            vals = {"config_id": config.id, "user_id": user.id}
            if notes:
                vals["notes"] = notes
            line = Seller.create(vals)
        if xml_id:
            self._register_xml_id(line, xml_id)
        return line

    def _ensure_demo_partner(self, xml_id, vals):
        partner = self._demo_xml_ref(xml_id)
        partner_vals = dict(vals)
        partner_vals.pop("company_id", None)
        if partner:
            updates = {}
            if partner.company_id:
                updates["company_id"] = False
            expected_ref = partner_vals.get("ref")
            if expected_ref and partner.ref != expected_ref:
                updates["ref"] = expected_ref
            if updates:
                partner.sudo().write(updates)
            return partner
        partner = self.env["res.partner"].sudo().create(partner_vals)
        self._register_xml_id(partner, xml_id)
        return partner

    @api.model
    def _clear_demo_customer_company_ids(self):
        """Demo customers are company-agnostic (shared across Tommasi companies)."""
        partners = self.env["res.partner"].sudo().search(
            [
                ("ref", "=like", "demo_cust_%"),
                ("company_id", "!=", False),
            ]
        )
        if partners:
            partners.write({"company_id": False})

    def _demo_partner_invoice_company(self, partner):
        """Company for demo bootstrap filler invoices when partner has no company."""
        company_b = self._demo_xml_ref("demo_company_b")
        if company_b and partner.ref and partner.ref.startswith("demo_cust_company_b_"):
            return company_b
        return self.env.company

    def _ensure_demo_user(self, xml_id, vals, partner_vals=None):
        user = self._demo_xml_ref(xml_id)
        if user:
            if partner_vals:
                user.partner_id.sudo().write(partner_vals)
            return user
        user = self.env["res.users"].sudo().create(vals)
        if partner_vals:
            user.partner_id.sudo().write(partner_vals)
        self._register_xml_id(user, xml_id)
        return user

    @api.model
    def _ensure_multicompany_demo_records(self):
        """Create company-B demo stack idempotently (works after module -u)."""
        if not self._demo_xml_ref("demo_seller_enabled_a"):
            return
        self._clear_demo_customer_company_ids()
        self._ensure_demo_company_b_setup()
        company_b = self._demo_xml_ref("demo_company_b")
        if not company_b:
            return

        main_company = self.env.ref("base.main_company")
        group_user = self.env.ref("base.group_user")

        seller_a = self._demo_xml_ref("demo_seller_enabled_a")
        if seller_a:
            company_ids = list(set(seller_a.company_ids.ids + [main_company.id, company_b.id]))
            seller_a.sudo().write({"company_ids": [(6, 0, company_ids)]})

        seller_c = self._ensure_demo_user(
            "demo_seller_company_b",
            {
                "name": "Julián Norte (Demo C)",
                "login": "demo_seller_c",
                "company_id": company_b.id,
                "company_ids": [(6, 0, [company_b.id])],
                "groups_id": [(6, 0, [group_user.id])],
            },
            {
                "mobile": "+5491156789012",
            },
        )

        config_b = self._demo_xml_ref("reactivation_config_company_b")
        if config_b and seller_c:
            self._ensure_demo_seller_line(
                config_b,
                seller_c,
                xml_id="demo_reactivation_seller_company_b",
            )
            if seller_a:
                self._ensure_demo_seller_line(
                    config_b,
                    seller_a,
                    xml_id="demo_reactivation_seller_shared_a",
                    notes="Shared seller across main company and Demo Sur config.",
                )

        if seller_c:
            self._ensure_demo_partner(
                "demo_cust_company_b_urgent",
                {
                    "name": "Demo Sur Urgente SA",
                    "ref": "demo_cust_company_b_urgent",
                    "vat": "30-73000001-5",
                    "is_company": True,
                    "active": True,
                    "user_id": seller_c.id,
                },
            )
        if seller_a:
            self._ensure_demo_partner(
                "demo_cust_company_b_shared_seller",
                {
                    "name": "Demo Sur María SA",
                    "ref": "demo_cust_company_b_shared_seller",
                    "vat": "30-73000002-3",
                    "is_company": True,
                    "active": True,
                    "user_id": seller_a.id,
                },
            )
        if seller_c:
            self._ensure_demo_partner(
                "demo_cust_company_b_isolated",
                {
                    "name": "Demo Sur Aislado SA",
                    "ref": "demo_cust_company_b_isolated",
                    "vat": "30-73000003-1",
                    "is_company": True,
                    "active": True,
                    "user_id": seller_c.id,
                },
            )

        admin = self.env.ref("base.user_admin", raise_if_not_found=False)
        if admin and company_b.id not in admin.company_ids.ids:
            admin.sudo().write({"company_ids": [(4, company_b.id)]})

    @api.model
    def _load_demo_company_b_transactions(self):
        """Post company-B demo invoices when multicompany records exist."""
        if not self._demo_xml_ref("demo_seller_enabled_a"):
            return
        company_b = self._demo_xml_ref("demo_company_b")
        seller_c = self._demo_xml_ref("demo_seller_company_b")
        seller_a = self._demo_xml_ref("demo_seller_enabled_a")
        product_in_stock = self._demo_xml_ref("demo_prod_in_stock")
        if not all([company_b, seller_c, seller_a, product_in_stock]):
            return

        if self._demo_xml_ref("demo_inv_company_b_jun"):
            return

        self._install_demo_company_b_chart(company_b)
        t0 = fields.Date.context_today(self)

        try:
            self._create_posted_out_invoice(
                self._ref("demo_cust_company_b_urgent"),
                t0 - relativedelta(days=50),
                [
                    self._invoice_line_vals(
                        product_in_stock, 1, 400000.0, company=company_b
                    )
                ],
                seller_c,
                xml_id="demo_inv_company_b_urgent",
                company=company_b,
            )
            self._create_posted_out_invoice(
                self._ref("demo_cust_company_b_shared_seller"),
                t0 - relativedelta(days=40),
                [
                    self._invoice_line_vals(
                        product_in_stock, 1, 350000.0, company=company_b
                    )
                ],
                seller_a,
                xml_id="demo_inv_company_b_shared",
                company=company_b,
            )
            self._create_posted_out_invoice(
                self._ref("demo_cust_company_b_isolated"),
                fields.Date.from_string("2026-06-18"),
                [
                    self._invoice_line_vals(
                        product_in_stock, 1, 600000.0, company=company_b
                    )
                ],
                seller_c,
                xml_id="demo_inv_company_b_jun",
                company=company_b,
            )
        except Exception:
            # Company B may lack a full chart of accounts; structural demo data still loads.
            return

    @api.model
    def _reload_multicompany_demo(self):
        """Refresh multicompany demo records and company-B invoices."""
        self._ensure_multicompany_demo_records()
        for xml_id in (
            "demo_inv_company_b_urgent",
            "demo_inv_company_b_shared",
            "demo_inv_company_b_jun",
        ):
            move = self._demo_xml_ref(xml_id)
            if move:
                if move.state == "posted":
                    move.button_draft()
                move.with_context(force_delete=True).unlink()
            self.env["ir.model.data"].sudo().search(
                [
                    ("module", "=", "tommasi_sales_reactivation"),
                    ("name", "=", xml_id),
                ]
            ).unlink()
        self._load_demo_company_b_transactions()

    def _demo_transactions_already_loaded(self):
        """Return True when posted demo invoices or sale orders already exist."""
        move_model = self.env["account.move"].sudo()
        if move_model.search(
            [
                ("ref", "=", DEMO_TX_MARKER),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
            ],
            limit=1,
        ):
            return True

        active_partner = self._ref("demo_cust_active")
        if move_model.search(
            [
                ("partner_id", "child_of", active_partner.commercial_partner_id.id),
                ("invoice_date", "=", fields.Date.from_string("2026-06-10")),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
            ],
            limit=1,
        ):
            return True

        demo_lead = self.env["crm.lead"].sudo().search(
            [("reactivation_attribution_id", "=like", "REACT-DEMO-%")],
            limit=1,
        )
        if demo_lead and demo_lead.partner_id:
            if move_model.search(
                [
                    (
                        "partner_id",
                        "child_of",
                        demo_lead.partner_id.commercial_partner_id.id,
                    ),
                    ("move_type", "=", "out_invoice"),
                    ("state", "=", "posted"),
                ],
                limit=1,
            ):
                return True

        if self.env["sale.order"].sudo().search(
            [("client_order_ref", "=", DEMO_TX_MARKER)],
            limit=1,
        ):
            return True

        return False

    def _assign_seller(self, partner, seller):
        partner.commercial_partner_id.sudo().write({"user_id": seller.id})

    def _bootstrap_config(self):
        return self.env["tommasi.reactivation.config"].get_primary_config()

    def _count_bootstrap_invoices(
        self, commercial_partner, seller_id, window_days, date_to=None
    ):
        date_to = date_to or fields.Date.context_today(self)
        date_from = date_to - relativedelta(days=window_days)
        self.env.cr.execute(
            """
            SELECT COUNT(move.id)
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND move.invoice_date >= %s
               AND move.invoice_date <= %s
               AND partner.user_id = %s
               AND partner.commercial_partner_id = %s
            """,
            (
                fields.Date.to_string(date_from),
                fields.Date.to_string(date_to),
                seller_id,
                commercial_partner.id,
            ),
        )
        return self.env.cr.fetchone()[0]

    def _latest_bootstrap_invoice_date(
        self, commercial_partner, seller_id, window_days, date_to=None
    ):
        date_to = date_to or fields.Date.context_today(self)
        date_from = date_to - relativedelta(days=window_days)
        self.env.cr.execute(
            """
            SELECT MAX(move.invoice_date)
              FROM account_move move
              JOIN res_partner partner ON partner.id = move.partner_id
             WHERE move.move_type = 'out_invoice'
               AND move.state = 'posted'
               AND move.invoice_date >= %s
               AND move.invoice_date <= %s
               AND partner.user_id = %s
               AND partner.commercial_partner_id = %s
            """,
            (
                fields.Date.to_string(date_from),
                fields.Date.to_string(date_to),
                seller_id,
                commercial_partner.id,
            ),
        )
        row = self.env.cr.fetchone()
        return row[0] if row and row[0] else None

    def _demo_bootstrap_anchor_date(self, partner_ref, t0):
        """Keep filler invoices older than scenario anchors (last purchase)."""
        anchors = {
            "demo_cust_reactivation": t0 - relativedelta(days=35),
            "demo_cust_urgent": t0 - relativedelta(days=47),
            "demo_cust_multi_trigger": t0 - relativedelta(days=50),
            "demo_cust_company_b_urgent": t0 - relativedelta(days=50),
            "demo_cust_company_b_shared_seller": t0 - relativedelta(days=40),
            "demo_cust_company_b_isolated": fields.Date.from_string("2026-06-18"),
        }
        return anchors.get(partner_ref)

    def _ensure_bootstrap_qualified_partner(
        self,
        partner,
        seller,
        product,
        company=None,
        anchor_date=None,
    ):
        """Top up posted invoices so bootstrap_reactivation_cycle includes the partner."""
        config = self._bootstrap_config()
        min_invoices = config.bootstrap_min_invoices
        window_days = config.bootstrap_invoice_window_days
        if min_invoices <= 0 or not partner.user_id:
            return

        t0 = fields.Date.context_today(self)
        commercial = partner.commercial_partner_id
        current_count = self._count_bootstrap_invoices(
            commercial, seller.id, window_days, t0
        )
        if current_count >= min_invoices:
            return

        needed = min_invoices - current_count
        partner_ref = partner.ref or ""
        if anchor_date is None:
            anchor_date = self._demo_bootstrap_anchor_date(partner_ref, t0)
        if anchor_date is None:
            anchor_date = self._latest_bootstrap_invoice_date(
                commercial, seller.id, window_days, t0
            )
        if anchor_date is None:
            anchor_date = t0
        if isinstance(anchor_date, str):
            anchor_date = fields.Date.from_string(anchor_date)

        window_start = t0 - relativedelta(days=window_days)
        offsets = [30, 60, 90, 120, 150]
        for index in range(needed):
            filler_date = anchor_date - relativedelta(days=offsets[index])
            if filler_date < window_start:
                filler_date = window_start + relativedelta(days=7 + index)
            self._create_posted_out_invoice(
                partner,
                filler_date,
                [self._invoice_line_vals(product, 1, 1000.0, company=company)],
                seller,
                company=company,
            )

    def _ensure_demo_bootstrap_qualified(self):
        """Ensure demo customers pass bootstrap rank and invoice filters."""
        config = self._bootstrap_config()
        if config.bootstrap_min_invoices <= 0:
            return

        product = self._demo_xml_ref("demo_prod_in_stock")
        if not product:
            return

        skip_refs = {
            "demo_cust_prospect",
            "demo_cust_child",
            "demo_cust_no_seller",
            "demo_cust_sporadic",
            "demo_cust_supplier",
        }
        partners = self.env["res.partner"].sudo().search(
            [
                ("ref", "=like", "demo_cust_%"),
                ("parent_id", "=", False),
                ("active", "=", True),
                ("user_id", "!=", False),
            ]
        )
        for partner in partners:
            if partner.ref in skip_refs:
                continue
            seller = partner.user_id
            company = self._demo_partner_invoice_company(partner)
            self._ensure_bootstrap_qualified_partner(
                partner,
                seller,
                product,
                company=company,
            )

    def _invoice_line_vals(self, product, quantity, price_unit, company=None):
        vals = {
            "product_id": product.id,
            "quantity": quantity,
            "price_unit": price_unit,
            "tax_ids": [(6, 0, [])],
        }
        product_env = product
        if company:
            product_env = product.with_company(company)
        if hasattr(product_env, "get_product_accounts"):
            accounts = product_env.get_product_accounts()
            income_account = accounts.get("income")
            if income_account:
                vals["account_id"] = income_account.id
        return vals

    def _create_posted_out_invoice(
        self,
        partner,
        invoice_date,
        lines,
        seller,
        xml_id=None,
        company=None,
    ):
        """Create and post a customer invoice with demo marker and seller scope."""
        company = company or self.env.company
        self._assign_seller(partner, seller)
        Move = self.env["account.move"].with_company(company).sudo()
        move = Move.create(
            {
                "move_type": "out_invoice",
                "partner_id": partner.commercial_partner_id.id,
                "invoice_date": invoice_date,
                "date": invoice_date,
                "ref": DEMO_TX_MARKER,
                "invoice_user_id": seller.id,
                "invoice_line_ids": [(0, 0, line_vals) for line_vals in lines],
            }
        )
        move.action_post()
        move.sudo().partner_id.write({"user_id": seller.id})
        if xml_id:
            self._register_xml_id(move, xml_id)
        return move

    def _create_draft_out_invoice(
        self,
        partner,
        invoice_date,
        lines,
        seller,
        xml_id=None,
        company=None,
    ):
        """Create a draft customer invoice (non-posted demo noise)."""
        company = company or self.env.company
        self._assign_seller(partner, seller)
        Move = self.env["account.move"].with_company(company).sudo()
        move = Move.create(
            {
                "move_type": "out_invoice",
                "partner_id": partner.commercial_partner_id.id,
                "invoice_date": invoice_date,
                "date": invoice_date,
                "ref": DEMO_DRAFT_MARKER,
                "invoice_user_id": seller.id,
                "invoice_line_ids": [(0, 0, line_vals) for line_vals in lines],
            }
        )
        if xml_id:
            self._register_xml_id(move, xml_id)
        return move

    def _create_cancelled_out_invoice(
        self,
        partner,
        invoice_date,
        lines,
        seller,
        xml_id=None,
        company=None,
    ):
        """Create a cancelled customer invoice (non-posted demo noise)."""
        move = self._create_draft_out_invoice(
            partner,
            invoice_date,
            lines,
            seller,
            company=company,
        )
        move.button_cancel()
        if xml_id:
            self._register_xml_id(move, xml_id)
        return move

    def _backdate_record(self, record, days_ago):
        """Backdate create_date and write_date on a record."""
        if not record:
            return
        target = fields.Datetime.now() - relativedelta(days=days_ago)
        self.env.cr.execute(
            "UPDATE %s SET create_date = %%s, write_date = %%s WHERE id = %%s"
            % record._table,
            (target, target, record.id),
        )
        record.invalidate_cache(["create_date", "write_date"])

    def _cap_demo_client_message(self, index, partner_name):
        """Long client message for cap digest demos."""
        return (
            "Hola, mensaje demo cap %02d para digest largo. Cliente: %s. "
            "Precio neto según tarifa del cliente. Producto sugerido "
            "en stock. Copiar y enviar al cliente cuando corresponda. "
            "corresponda. Referencia interna REACT-DEMO-CAP."
            % (index, partner_name)
        )

    def _ensure_sporadic_invoices(self, seller_a, product_in_stock, t0):
        if self._demo_xml_ref("demo_inv_sporadic_1"):
            return
        partner = self._demo_xml_ref("demo_cust_sporadic")
        if not partner:
            return
        partner.sudo().write({"customer_rank": 1})
        for index, days_back in enumerate((120, 90), start=1):
            self._create_posted_out_invoice(
                partner,
                t0 - relativedelta(days=days_back),
                [self._invoice_line_vals(product_in_stock, 1, 50000.0)],
                seller_a,
                xml_id="demo_inv_sporadic_%d" % index,
            )

    def _ensure_urgent_invoice_47_days(self, seller_a, product_in_stock, t0):
        move = self._demo_xml_ref("demo_inv_urgent_1")
        target_date = t0 - relativedelta(days=47)
        if move:
            if move.invoice_date != target_date:
                vals = {"invoice_date": target_date, "date": target_date}
                if "invoice_date_due" in move._fields:
                    vals["invoice_date_due"] = target_date
                if move.state == "posted":
                    move.button_draft()
                move.sudo().write(vals)
                move.action_post()
            return
        self._create_posted_out_invoice(
            self._ref("demo_cust_urgent"),
            target_date,
            [self._invoice_line_vals(product_in_stock, 1, 200000.0)],
            seller_a,
            xml_id="demo_inv_urgent_1",
        )

    def _trend_invoice_day_offsets(self, count=6):
        """Spread demo invoices within the 90-day default detection window."""
        oldest = 82
        step = 14
        return [max(oldest - index * step, 1) for index in range(count)]

    def _reschedule_posted_invoice(self, move, target_date):
        if move.invoice_date == target_date:
            return
        vals = {"invoice_date": target_date, "date": target_date}
        if "invoice_date_due" in move._fields:
            vals["invoice_date_due"] = target_date
        if move.state == "posted":
            move.button_draft()
        move.sudo().write(vals)
        move.action_post()

    def _upsert_monthly_trend_invoices(
        self,
        partner,
        amounts,
        xml_id_prefix,
        seller_a,
        product,
        t0,
    ):
        offsets = self._trend_invoice_day_offsets(len(amounts))
        for index, amount in enumerate(amounts):
            xml_id = "%s%d" % (xml_id_prefix, index + 1)
            target_date = t0 - relativedelta(days=offsets[index])
            move = self._demo_xml_ref(xml_id)
            if move:
                self._reschedule_posted_invoice(move, target_date)
                if abs(move.amount_untaxed - amount) > 0.01:
                    if move.state == "posted":
                        move.button_draft()
                    move.with_context(force_delete=True).unlink()
                    self.env["ir.model.data"].sudo().search(
                        [
                            ("module", "=", "tommasi_sales_reactivation"),
                            ("name", "=", xml_id),
                        ]
                    ).unlink()
                    move = None
                else:
                    continue
            if not move:
                self._create_posted_out_invoice(
                    partner,
                    target_date,
                    [self._invoice_line_vals(product, 1, amount)],
                    seller_a,
                    xml_id=xml_id,
                )

    def _ensure_declining_invoices(self, seller_a, product_in_stock, t0):
        amounts = [
            800000.0,
            680000.0,
            560000.0,
            440000.0,
            280000.0,
            180000.0,
        ]
        self._upsert_monthly_trend_invoices(
            self._ref("demo_cust_declining"),
            amounts,
            "demo_inv_decl_m",
            seller_a,
            product_in_stock,
            t0,
        )

    def _ensure_growing_invoices(self, seller_a, product_in_stock, t0):
        amounts = [
            200000.0,
            320000.0,
            440000.0,
            560000.0,
            680000.0,
            800000.0,
        ]
        self._upsert_monthly_trend_invoices(
            self._ref("demo_cust_growing"),
            amounts,
            "demo_inv_grow_m",
            seller_a,
            product_in_stock,
            t0,
        )

    def _ensure_volume_decline_stock_invoices(
        self, seller_a, product_volume_decline, t0
    ):
        partner = self._ref("demo_cust_volume_decline")
        specs = (
            ("demo_inv_vol_prior", 70, 80),
            ("demo_inv_vol_recent", 20, 15),
        )
        for xml_id, days_back, qty in specs:
            target_date = t0 - relativedelta(days=days_back)
            move = self._demo_xml_ref(xml_id)
            if move:
                self._reschedule_posted_invoice(move, target_date)
                continue
            self._create_posted_out_invoice(
                partner,
                target_date,
                [self._invoice_line_vals(product_volume_decline, qty, 1.0)],
                seller_a,
                xml_id=xml_id,
            )

    def _ensure_volume_decline_no_stock_invoices(
        self, seller_a, product_no_stock, t0
    ):
        if self._demo_xml_ref("demo_inv_vol_nostk_prior"):
            return
        partner = self._demo_xml_ref("demo_cust_volume_decline")
        if not partner:
            return
        self._create_posted_out_invoice(
            partner,
            t0 - relativedelta(days=55),
            [self._invoice_line_vals(product_no_stock, 80, 1.0)],
            seller_a,
            xml_id="demo_inv_vol_nostk_prior",
        )
        self._create_posted_out_invoice(
            partner,
            t0 - relativedelta(days=18),
            [self._invoice_line_vals(product_no_stock, 15, 1.0)],
            seller_a,
            xml_id="demo_inv_vol_nostk_recent",
        )

    def _ensure_draft_noise_invoices(self, seller_a, product_in_stock, t0):
        partner = self._demo_xml_ref("demo_cust_draft_noise")
        if not partner:
            return
        partner.sudo().write({"customer_rank": 1})
        target_posted = t0 - relativedelta(days=45)
        posted = self._demo_xml_ref("demo_inv_draft_noise_posted")
        if posted:
            self._reschedule_posted_invoice(posted, target_posted)
        else:
            self._create_posted_out_invoice(
                partner,
                target_posted,
                [self._invoice_line_vals(product_in_stock, 1, 150000.0)],
                seller_a,
                xml_id="demo_inv_draft_noise_posted",
            )
        if not self._demo_xml_ref("demo_inv_draft_noise_draft"):
            self._create_draft_out_invoice(
                partner,
                t0 - relativedelta(days=5),
                [self._invoice_line_vals(product_in_stock, 1, 80000.0)],
                seller_a,
                xml_id="demo_inv_draft_noise_draft",
            )
        if not self._demo_xml_ref("demo_inv_draft_noise_cancelled"):
            self._create_cancelled_out_invoice(
                partner,
                t0 - relativedelta(days=3),
                [self._invoice_line_vals(product_in_stock, 1, 90000.0)],
                seller_a,
                xml_id="demo_inv_draft_noise_cancelled",
            )

    def _ensure_multitier_invoices(
        self,
        seller_a,
        product_dropoff,
        product_in_stock,
        t0,
    ):
        if self._demo_xml_ref("demo_inv_mt_drop_1"):
            return
        partner = self._demo_xml_ref("demo_cust_multitier")
        if not partner:
            return
        dropoff_offsets = [45, 65, 85, 105]
        for index, days_back in enumerate(dropoff_offsets, start=1):
            invoice_date = t0 - relativedelta(days=days_back)
            if (
                fields.Date.from_string("2026-05-02")
                <= invoice_date
                <= fields.Date.from_string("2026-05-31")
            ):
                invoice_date = invoice_date - relativedelta(months=1)
            self._create_posted_out_invoice(
                partner,
                invoice_date,
                [
                    self._invoice_line_vals(
                        product_dropoff,
                        1,
                        product_dropoff.list_price,
                    )
                ],
                seller_a,
                xml_id="demo_inv_mt_drop_%d" % index,
            )
        self._create_posted_out_invoice(
            partner,
            t0 - relativedelta(days=25),
            [
                self._invoice_line_vals(
                    product_dropoff,
                    1,
                    product_dropoff.list_price,
                ),
                self._invoice_line_vals(
                    product_in_stock,
                    1,
                    product_in_stock.list_price,
                ),
            ],
            seller_a,
            xml_id="demo_inv_mt_similar",
        )

    def _ensure_low_stock_offer_invoices(self, seller_a, product_low_stock, t0):
        if self._demo_xml_ref("demo_inv_ls_1"):
            return
        partner = self._demo_xml_ref("demo_cust_low_stock_offer")
        if not partner:
            return
        offsets = [105, 75, 45, 60]
        for index, days_back in enumerate(offsets, start=1):
            self._create_posted_out_invoice(
                partner,
                t0 - relativedelta(days=days_back),
                [
                    self._invoice_line_vals(
                        product_low_stock,
                        1,
                        product_low_stock.list_price,
                    )
                ],
                seller_a,
                xml_id="demo_inv_ls_%d" % index,
            )

    @api.model
    def _ensure_demo_scenario_partners(self):
        """Create scenario partners when demo XML was not reloaded (-u without demo)."""
        if not self._demo_xml_ref("demo_seller_enabled_a"):
            return
        seller_a = self._ref("demo_seller_enabled_a")
        specs = (
            (
                "demo_cust_sporadic",
                {
                    "name": "Demo Esporádico SA",
                    "ref": "demo_cust_sporadic",
                    "vat": "30-70000015-5",
                    "is_company": True,
                    "active": True,
                    "customer_rank": 1,
                    "user_id": seller_a.id,
                },
            ),
            (
                "demo_cust_supplier",
                {
                    "name": "Demo Proveedor SA",
                    "ref": "demo_cust_supplier",
                    "vat": "30-70000016-3",
                    "is_company": True,
                    "active": True,
                    "supplier_rank": 1,
                    "customer_rank": 0,
                    "user_id": seller_a.id,
                },
            ),
            (
                "demo_cust_draft_noise",
                {
                    "name": "Demo Borrador SA",
                    "ref": "demo_cust_draft_noise",
                    "vat": "30-70000017-1",
                    "is_company": True,
                    "active": True,
                    "customer_rank": 1,
                    "user_id": seller_a.id,
                },
            ),
            (
                "demo_cust_multitier",
                {
                    "name": "Demo Multi-Tier SA",
                    "ref": "demo_cust_multitier",
                    "vat": "30-70000018-0",
                    "is_company": True,
                    "active": True,
                    "user_id": seller_a.id,
                },
            ),
            (
                "demo_cust_low_stock_offer",
                {
                    "name": "Demo Stock Bajo SA",
                    "ref": "demo_cust_low_stock_offer",
                    "vat": "30-70000019-8",
                    "is_company": True,
                    "active": True,
                    "user_id": seller_a.id,
                },
            ),
        )
        for xml_id, vals in specs:
            self._ensure_demo_partner(xml_id, vals)

    @api.model
    def _ensure_demo_scenario_transactions(self):
        """Idempotently load scenario-specific demo transactions."""
        if not self._demo_xml_ref("demo_seller_enabled_a"):
            return
        self._ensure_demo_scenario_partners()
        self._ensure_demo_sale_journals()
        self._configure_demo_product_links()
        t0 = fields.Date.context_today(self)
        seller_a = self._ref("demo_seller_enabled_a")
        product_in_stock = self._ref("demo_prod_in_stock")
        product_dropoff = self._ref("demo_prod_dropoff")
        product_no_stock = self._demo_xml_ref("demo_prod_no_stock")
        product_low_stock = self._demo_xml_ref("demo_prod_low_stock")
        self._ensure_sporadic_invoices(seller_a, product_in_stock, t0)
        self._ensure_urgent_invoice_47_days(seller_a, product_in_stock, t0)
        self._ensure_declining_invoices(seller_a, product_in_stock, t0)
        self._ensure_growing_invoices(seller_a, product_in_stock, t0)
        product_volume_decline = self._ref("demo_prod_volume_decline")
        self._ensure_volume_decline_stock_invoices(
            seller_a, product_volume_decline, t0
        )
        if product_no_stock:
            self._ensure_volume_decline_no_stock_invoices(
                seller_a, product_no_stock, t0
            )
        self._ensure_draft_noise_invoices(seller_a, product_in_stock, t0)
        self._ensure_multitier_invoices(
            seller_a, product_dropoff, product_in_stock, t0
        )
        if product_low_stock:
            self._ensure_low_stock_offer_invoices(
                seller_a, product_low_stock, t0
            )

    @api.model
    def _backdate_archived_demo_opportunity(self):
        """Ensure archived demo opp is older than cooldown window (Q-02)."""
        lead = self._demo_xml_ref("demo_opp_archived")
        if not lead:
            return
        cutoff = fields.Datetime.now() - relativedelta(days=35)
        if lead.create_date and lead.create_date <= cutoff:
            return
        self._backdate_record(lead, 45)

    @api.model
    def _ensure_demo_scenario_opportunities(self):
        """Restore canonical CRM fixtures for dedup scenarios (Q-01..Q-03).

        Detection-cycle runs can leave stray agent opportunities on demo
        customers; this method removes them and recreates any missing XML
        fixtures (notably ``demo_opp_non_agent`` when demo XML was skipped).
        """
        if not self._demo_xml_ref("demo_seller_enabled_a"):
            return
        seller_a = self._ref("demo_seller_enabled_a")
        Lead = self.env["crm.lead"].sudo()
        stage_pending = self._ref("stage_pendiente_revision")
        stage_contacted = self._ref("stage_cliente_contactado")
        specs = (
            (
                "demo_opp_pending",
                "demo_cust_with_opp_pending",
                {
                    "name": "Reactivación demo pendiente",
                    "type": "opportunity",
                    "stage_id": stage_pending.id,
                    "reactivation_is_agent": True,
                    "active": True,
                    "reactivation_attribution_id": "REACT-DEMO-PENDING",
                    "reactivation_client_message": (
                        "Hola, mensaje demo pendiente."
                    ),
                },
            ),
            (
                "demo_opp_contacted",
                "demo_cust_with_opp_contacted",
                {
                    "name": "Reactivación demo contactado",
                    "type": "opportunity",
                    "stage_id": stage_contacted.id,
                    "reactivation_is_agent": True,
                    "active": True,
                    "reactivation_attribution_id": "REACT-DEMO-CONTACTED",
                    "reactivation_client_message": (
                        "Hola, mensaje demo contactado."
                    ),
                },
            ),
            (
                "demo_opp_archived",
                "demo_cust_archived_opp",
                {
                    "name": "Reactivación demo archivada",
                    "type": "opportunity",
                    "stage_id": stage_pending.id,
                    "reactivation_is_agent": True,
                    "active": False,
                    "reactivation_attribution_id": "REACT-DEMO-ARCHIVED",
                },
            ),
            (
                "demo_opp_non_agent",
                "demo_cust_active",
                {
                    "name": "Oportunidad demo no-agente",
                    "type": "opportunity",
                    "stage_id": stage_pending.id,
                    "reactivation_is_agent": False,
                    "active": True,
                    "reactivation_attribution_id": "REACT-DEMO-NON-AGENT",
                },
            ),
        )
        for opp_xml_id, partner_xml_id, opp_vals in specs:
            partner = self._ref(partner_xml_id)
            canonical = self._demo_xml_ref(opp_xml_id)
            if opp_vals.get("reactivation_is_agent"):
                spurious_domain = [
                    ("partner_id", "=", partner.id),
                    ("reactivation_is_agent", "=", True),
                    ("active", "=", True),
                ]
                if canonical:
                    spurious_domain.append(("id", "!=", canonical.id))
                Lead.search(spurious_domain).unlink()
            else:
                Lead.search(
                    [
                        ("partner_id", "=", partner.id),
                        ("reactivation_is_agent", "=", True),
                        ("active", "=", True),
                    ]
                ).unlink()
            write_vals = dict(opp_vals, partner_id=partner.id, user_id=seller_a.id)
            if canonical:
                canonical.write(write_vals)
            else:
                lead = Lead.create(write_vals)
                self._register_xml_id(lead, opp_xml_id)

    @api.model
    def _ensure_demo_cap_opportunities(self):
        """Seed open agent opportunities to exceed seller cap (Q-04, W-03)."""
        if not self._demo_xml_ref("demo_seller_enabled_a"):
            return
        seller_a = self._ref("demo_seller_enabled_a")
        stage = self._ref("stage_pendiente_revision")
        product_in_stock = self._demo_xml_ref("demo_prod_in_stock")
        if not product_in_stock:
            return
        open_opps = (
            self.env["crm.lead"]
            .sudo()
            .search_count(
                [
                    ("user_id", "=", seller_a.id),
                    ("reactivation_is_agent", "=", True),
                    ("active", "=", True),
                    ("stage_id", "=", stage.id),
                ]
            )
        )
        if open_opps >= DEMO_CAP_OPP_TARGET:
            return
        needed = DEMO_CAP_OPP_TARGET - open_opps
        for index in range(1, DEMO_CAP_FILLER_COUNT + 10):
            if needed <= 0:
                break
            xml_id = "demo_cust_cap_%02d" % index
            partner = self._ensure_demo_partner(
                xml_id,
                {
                    "name": "Demo Cap %02d SA" % index,
                    "ref": xml_id,
                    "vat": "30-740000%02d-9" % index,
                    "is_company": True,
                    "active": True,
                    "user_id": seller_a.id,
                },
            )
            partner.sudo().write({"customer_rank": 1})
            for inv_index in range(3):
                inv_xml_id = "demo_inv_cap_%02d_%d" % (index, inv_index + 1)
                if not self._demo_xml_ref(inv_xml_id):
                    self._create_posted_out_invoice(
                        partner,
                        fields.Date.context_today(self)
                        - relativedelta(days=30 + inv_index * 15),
                        [
                            self._invoice_line_vals(
                                product_in_stock, 1, 100000.0
                            )
                        ],
                        seller_a,
                        xml_id=inv_xml_id,
                    )
            attribution_id = "%s%03d" % (DEMO_CAP_ATTRIBUTION_PREFIX, index)
            opp_xml_id = "demo_opp_cap_%02d" % index
            existing_opp = self._demo_xml_ref(opp_xml_id)
            if existing_opp and existing_opp.active:
                continue
            if self.env["crm.lead"].sudo().search(
                [
                    ("reactivation_attribution_id", "=", attribution_id),
                    ("active", "=", True),
                ],
                limit=1,
            ):
                continue
            lead = (
                self.env["crm.lead"]
                .sudo()
                .create(
                    {
                        "name": "Reactivación demo cap %02d" % index,
                        "type": "opportunity",
                        "partner_id": partner.id,
                        "user_id": seller_a.id,
                        "stage_id": stage.id,
                        "reactivation_is_agent": True,
                        "active": True,
                        "reactivation_attribution_id": attribution_id,
                        "reactivation_client_message": self._cap_demo_client_message(
                            index, partner.name
                        ),
                    }
                )
            )
            self._register_xml_id(lead, opp_xml_id)
            needed -= 1

    def _ensure_product_deliverable(self, product, company=None):
        company = company or self.env.company
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", company.id)], limit=1
        )
        if not warehouse:
            return warehouse
        route = warehouse.delivery_route_id
        if route and route not in product.route_ids:
            product.sudo().write({"route_ids": [(4, route.id)]})
        return warehouse

    def _create_confirmed_sale_order(
        self,
        partner,
        product,
        quantity,
        seller,
        xml_id=None,
        company=None,
    ):
        """Confirm a sale order without delivering."""
        company = company or self.env.company
        self._assign_seller(partner, seller)
        warehouse = self._ensure_product_deliverable(product, company=company)
        line_vals = {
            "product_id": product.id,
            "product_uom_qty": quantity,
            "price_unit": product.list_price,
        }
        if warehouse and "custom_warehouse_id" in self.env["sale.order.line"]._fields:
            line_vals["custom_warehouse_id"] = warehouse.id
        order_vals = {
            "partner_id": partner.commercial_partner_id.id,
            "user_id": seller.id,
            "client_order_ref": DEMO_TX_MARKER,
            "order_line": [(0, 0, line_vals)],
        }
        if warehouse and "warehouse_id" in self.env["sale.order"]._fields:
            order_vals["warehouse_id"] = warehouse.id
        order = self.env["sale.order"].with_company(company).sudo().create(order_vals)
        order.action_confirm()
        if xml_id:
            self._register_xml_id(order, xml_id)
        return order

    def _partial_deliver_sale_order(self, order, quantity_done):
        """Deliver a partial quantity on the first outgoing picking (Odoo 15)."""
        picking = order.picking_ids.filtered(
            lambda pick: pick.picking_type_code == "outgoing"
        )[:1]
        if not picking:
            return
        picking.action_assign()
        picking.move_lines.write({"quantity_done": quantity_done})
        result = picking.button_validate()
        if isinstance(result, dict):
            wizard_model = result["res_model"]
            wizard = (
                self.env[wizard_model]
                .with_context(**result.get("context", {}))
                .create({"pick_ids": [(6, 0, picking.ids)]})
            )
            if wizard_model == "stock.immediate.transfer":
                result = wizard.process()
            if isinstance(result, dict) and result.get("res_model") == (
                "stock.backorder.confirmation"
            ):
                wizard = (
                    self.env[result["res_model"]]
                    .with_context(**result.get("context", {}))
                    .create({"pick_ids": [(6, 0, picking.ids)]})
                )
                wizard.process_cancel_backorder()

    def _ensure_demo_sale_journals_for_company(self, company):
        """Prepare sale journals for demo invoice posting on a given company."""
        earliest = fields.Date.to_date("2020-01-01")
        journals = self.env["account.journal"].sudo().search(
            [("type", "=", "sale"), ("company_id", "=", company.id)]
        )
        if not journals:
            vals = {
                "name": "Customer Invoices",
                "code": "DINV",
                "type": "sale",
                "company_id": company.id,
            }
            if "denomination" in self.env["account.journal"]._fields:
                vals["denomination"] = "b"
            if "due_date" in self.env["account.journal"]._fields:
                vals["due_date"] = earliest
            self.env["account.journal"].sudo().create(vals)
            journals = self.env["account.journal"].sudo().search(
                [("type", "=", "sale"), ("company_id", "=", company.id)]
            )
        for journal in journals:
            vals = {}
            if not journal.due_date or journal.due_date > earliest:
                vals["due_date"] = earliest
            if not journal.denomination or journal.denomination == "not_defined":
                vals["denomination"] = "b"
            if vals:
                journal.write(vals)

    def _ensure_demo_sale_journals(self):
        """Prepare sale journals for l10n_ar_eynes demo invoice posting."""
        self._ensure_demo_sale_journals_for_company(self.env.company)

    def _configure_demo_product_links(self):
        """Link dropoff template to related products when fields are available."""
        dropoff_tmpl = self._ref("demo_prod_dropoff").product_tmpl_id
        opt_tmpl = self._ref("demo_prod_related_opt").product_tmpl_id
        acc_tmpl = self._ref("demo_prod_related_acc").product_tmpl_id
        alt_templates = opt_tmpl | acc_tmpl
        if "alternative_product_ids" in dropoff_tmpl._fields:
            dropoff_tmpl.alternative_product_ids = [(6, 0, alt_templates.ids)]
        else:
            if "optional_product_ids" in dropoff_tmpl._fields:
                dropoff_tmpl.optional_product_ids = [(6, 0, opt_tmpl.ids)]
            if "accessory_product_ids" in dropoff_tmpl._fields:
                dropoff_tmpl.accessory_product_ids = [
                    (6, 0, acc_tmpl.product_variant_ids.ids)
                ]
        inactive_tmpl = self._ref("demo_prod_inactive").product_tmpl_id
        inactive_tmpl.sudo().write({"active": False})

    @api.model
    def _purge_demo_transactions(self):
        """Remove posted demo invoices and sale orders so they can be reloaded."""
        Lead = self.env["crm.lead"].sudo()
        cap_leads = Lead.search(
            [("reactivation_attribution_id", "=like", DEMO_CAP_ATTRIBUTION_PREFIX + "%")]
        )
        if cap_leads:
            cap_leads.unlink()

        cap_partners = self.env["res.partner"].sudo().search(
            [("ref", "=like", "demo_cust_cap_%")]
        )
        if cap_partners:
            cap_partners.unlink()

        SaleOrder = self.env["sale.order"].sudo()
        orders = SaleOrder.search([("client_order_ref", "=", DEMO_TX_MARKER)])
        if orders:
            order_ids = tuple(orders.ids)
            self.env.cr.execute(
                "UPDATE sale_order SET state = 'cancel' WHERE id IN %s",
                (order_ids,),
            )
            orders.invalidate_cache()
            orders.unlink()

        Move = self.env["account.move"].sudo()
        for marker in (DEMO_TX_MARKER, DEMO_DRAFT_MARKER):
            moves = Move.search(
                [
                    ("ref", "=", marker),
                    ("move_type", "=", "out_invoice"),
                ]
            )
            for move in moves:
                if move.state == "posted":
                    move.button_draft()
                move.with_context(force_delete=True).unlink()

        self.env["ir.model.data"].sudo().search(
            [
                ("module", "=", "tommasi_sales_reactivation"),
                ("name", "=like", "demo_inv_%"),
            ]
        ).unlink()
        self.env["ir.model.data"].sudo().search(
            [
                ("module", "=", "tommasi_sales_reactivation"),
                ("name", "=like", "demo_so_%"),
            ]
        ).unlink()
        self.env["ir.model.data"].sudo().search(
            [
                ("module", "=", "tommasi_sales_reactivation"),
                ("name", "=like", "demo_opp_cap_%"),
            ]
        ).unlink()
        self.env["ir.model.data"].sudo().search(
            [
                ("module", "=", "tommasi_sales_reactivation"),
                ("name", "=like", "demo_cust_cap_%"),
            ]
        ).unlink()

    @api.model
    def _reload_demo_transactions(self):
        """Purge and recreate transactional demo data (development helper)."""
        self._purge_demo_transactions()
        self._load_demo_transactions()

    @api.model
    def _load_demo_transactions(self):
        """Load posted invoices and confirmed sale orders for MCP demo data."""
        self._clear_demo_customer_company_ids()
        self._ensure_multicompany_demo_records()
        if self._demo_transactions_already_loaded():
            self._ensure_demo_scenario_partners()
            self._load_demo_company_b_transactions()
            self._ensure_demo_scenario_transactions()
            self._ensure_demo_scenario_opportunities()
            self._ensure_demo_cap_opportunities()
            self._backdate_archived_demo_opportunity()
            self._ensure_demo_bootstrap_qualified()
            return

        self._ensure_demo_sale_journals()
        self._configure_demo_product_links()
        self._ensure_demo_scenario_partners()

        t0 = fields.Date.context_today(self)
        seller_a = self._ref("demo_seller_enabled_a")
        seller_b = self._ref("demo_seller_enabled_b")
        product_in_stock = self._ref("demo_prod_in_stock")
        product_dropoff = self._ref("demo_prod_dropoff")
        product_related_opt = self._ref("demo_prod_related_opt")
        product_related_acc = self._ref("demo_prod_related_acc")
        product_similar_only = self._ref("demo_prod_similar_only")
        product_undelivered = self._ref("demo_prod_undelivered")
        product_volume_decline = self._ref("demo_prod_volume_decline")
        product_service = self._demo_xml_ref("demo_prod_service")
        product_consu = self._demo_xml_ref("demo_prod_consu")

        # Inactivity tiers: last purchase drives suggested_action.
        self._create_posted_out_invoice(
            self._ref("demo_cust_reactivation"),
            t0 - relativedelta(days=35),
            [self._invoice_line_vals(product_in_stock, 1, 300000.0)],
            seller_a,
            xml_id="demo_inv_react_1",
        )
        self._create_posted_out_invoice(
            self._ref("demo_cust_urgent"),
            t0 - relativedelta(days=47),
            [self._invoice_line_vals(product_in_stock, 1, 200000.0)],
            seller_a,
            xml_id="demo_inv_urgent_1",
        )

        self._ensure_declining_invoices(seller_a, product_in_stock, t0)
        self._ensure_growing_invoices(seller_a, product_in_stock, t0)

        dropoff_offsets = [45, 65, 85, 105]
        for index, days_back in enumerate(dropoff_offsets):
            invoice_date = t0 - relativedelta(days=days_back)
            if fields.Date.from_string("2026-05-02") <= invoice_date <= fields.Date.from_string("2026-05-31"):
                invoice_date = invoice_date - relativedelta(months=1)
            self._create_posted_out_invoice(
                self._ref("demo_cust_dropoff"),
                invoice_date,
                [
                    self._invoice_line_vals(
                        product_dropoff,
                        1,
                        product_dropoff.list_price,
                    )
                ],
                seller_a,
                xml_id="demo_inv_drop_%d" % (index + 1),
            )

        self._ensure_volume_decline_stock_invoices(
            seller_a, product_volume_decline, t0
        )

        light_history_customers = (
            "demo_cust_with_opp_pending",
            "demo_cust_with_opp_contacted",
            "demo_cust_archived_opp",
        )
        for customer_xml_id in light_history_customers:
            self._create_posted_out_invoice(
                self._ref(customer_xml_id),
                fields.Date.from_string("2026-04-01"),
                [self._invoice_line_vals(product_in_stock, 1, 100000.0)],
                seller_a,
            )

        self._create_posted_out_invoice(
            self._ref("demo_cust_pricelist"),
            fields.Date.from_string("2026-04-20"),
            [self._invoice_line_vals(product_in_stock, 1, product_in_stock.list_price)],
            seller_a,
        )
        self._create_posted_out_invoice(
            self._ref("demo_cust_multi_trigger"),
            t0 - relativedelta(days=50),
            [self._invoice_line_vals(product_in_stock, 1, 250000.0)],
            seller_a,
        )
        # Company-wide 30d sales: opt (10 units) outranks acc (2) for related tier.
        self._create_posted_out_invoice(
            self._ref("demo_cust_active"),
            t0 - relativedelta(days=15),
            [
                self._invoice_line_vals(
                    product_related_opt,
                    10,
                    product_related_opt.list_price,
                ),
                self._invoice_line_vals(
                    product_related_acc,
                    2,
                    product_related_acc.list_price,
                ),
            ],
            seller_a,
            xml_id="demo_inv_related_rank",
        )

        # similar_customer: A and B share dropoff + in_stock (overlap 2); B also
        # buys similar_only. Other customers match at most one shared product.
        self._create_posted_out_invoice(
            self._ref("demo_cust_similar_a"),
            t0 - relativedelta(days=20),
            [
                self._invoice_line_vals(
                    product_dropoff,
                    1,
                    product_dropoff.list_price,
                ),
                self._invoice_line_vals(
                    product_in_stock,
                    1,
                    product_in_stock.list_price,
                ),
            ],
            seller_b,
            xml_id="demo_inv_similar_a",
        )
        self._create_posted_out_invoice(
            self._ref("demo_cust_similar_b"),
            t0 - relativedelta(days=30),
            [
                self._invoice_line_vals(
                    product_dropoff,
                    1,
                    product_dropoff.list_price,
                ),
                self._invoice_line_vals(
                    product_in_stock,
                    1,
                    product_in_stock.list_price,
                ),
                self._invoice_line_vals(
                    product_similar_only,
                    1,
                    product_similar_only.list_price,
                ),
            ],
            seller_b,
            xml_id="demo_inv_similar_b",
        )

        # Fixed June/May totals — active customer's latest purchase is 2026-06-15.
        self._create_posted_out_invoice(
            self._ref("demo_cust_active"),
            fields.Date.from_string("2026-06-10"),
            [self._invoice_line_vals(product_in_stock, 1, 1000000.0)],
            seller_a,
            xml_id="demo_inv_tot_jun_1",
        )
        if product_service and product_consu:
            self._create_posted_out_invoice(
                self._ref("demo_cust_active"),
                fields.Date.from_string("2026-05-20"),
                [
                    self._invoice_line_vals(product_service, 1, 25000.0),
                    self._invoice_line_vals(product_consu, 1, 18000.0),
                ],
                seller_a,
            )
        self._create_posted_out_invoice(
            self._ref("demo_cust_active"),
            fields.Date.from_string("2026-06-15"),
            [self._invoice_line_vals(product_in_stock, 1, 500000.0)],
            seller_a,
            xml_id="demo_inv_tot_jun_2",
        )
        self._create_posted_out_invoice(
            self._ref("demo_cust_pricelist"),
            fields.Date.from_string("2026-06-20"),
            [self._invoice_line_vals(product_in_stock, 1, 300000.0)],
            seller_a,
            xml_id="demo_inv_tot_jun_3",
        )
        self._create_posted_out_invoice(
            self._ref("demo_cust_active"),
            fields.Date.from_string("2026-05-15"),
            [self._invoice_line_vals(product_in_stock, 1, 1000000.0)],
            seller_a,
            xml_id="demo_inv_tot_may_1",
        )
        self._create_posted_out_invoice(
            self._ref("demo_cust_other_seller"),
            fields.Date.from_string("2026-06-12"),
            [self._invoice_line_vals(product_in_stock, 1, 9999999.0)],
            seller_b,
            xml_id="demo_inv_other_seller",
        )

        self._create_confirmed_sale_order(
            self._ref("demo_cust_undelivered"),
            product_undelivered,
            4,
            seller_a,
            xml_id="demo_so_undelivered_1",
        )
        self._create_confirmed_sale_order(
            self._ref("demo_cust_multi_trigger"),
            product_undelivered,
            2,
            seller_a,
            xml_id="demo_so_undelivered_2",
        )
        so_partial = self._create_confirmed_sale_order(
            self._ref("demo_cust_undelivered"),
            product_in_stock,
            10,
            seller_a,
            xml_id="demo_so_undelivered_3",
        )
        self._partial_deliver_sale_order(so_partial, 6)
        self._load_demo_company_b_transactions()
        self._ensure_demo_scenario_transactions()
        self._ensure_demo_scenario_opportunities()
        self._ensure_demo_cap_opportunities()
        self._backdate_archived_demo_opportunity()
        self._ensure_demo_bootstrap_qualified()
