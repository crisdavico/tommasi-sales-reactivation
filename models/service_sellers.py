from odoo import models


class TommasiReactivationServiceSellers(models.AbstractModel):
    _inherit = "tommasi.reactivation.service"

    def _filter_enabled_sellers(self, config=None):
        """Seller lines configured for reactivation across all companies."""
        if config is not None:
            sellers = config.seller_ids
        else:
            sellers = (
                self._unified_env()["tommasi.reactivation.config"]
                .get_all_configs()
                .mapped("seller_ids")
            )
        seen_user_ids = set()
        unique_sellers = self.env["tommasi.reactivation.seller"]
        for seller in sellers:
            user_id = seller.user_id.id
            if user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)
            unique_sellers |= seller
        return unique_sellers

    def _find_seller_line(self, seller_id):
        sellers = (
            self._unified_env()["tommasi.reactivation.config"]
            .get_all_configs()
            .mapped("seller_ids")
        )
        return sellers.filtered(lambda row: row.user_id.id == seller_id)[:1]

    def _commercial_partner_seller_id(self, partner):
        """Return salesperson on the commercial entity (fresh from DB)."""
        commercial_id = partner.commercial_partner_id.id
        if not commercial_id:
            return None
        self.env["res.partner"].flush()
        self.env.cr.execute(
            "SELECT user_id FROM res_partner WHERE id = %s",
            (commercial_id,),
        )
        row = self.env.cr.fetchone()
        return row[0] if row and row[0] else None

    def _commercial_partner_id_for_customer(self, customer_id):
        """Return commercial partner id for SQL scoping, or None if missing."""
        partner = self.env["res.partner"].sudo().browse(customer_id)
        if not partner.exists():
            return None
        return partner.commercial_partner_id.id

    def _seller_env_for_customer(self, customer_id, seller_id):
        partner_row = self.env["res.partner"].sudo().browse(customer_id)
        if not partner_row.exists():
            return None, None, "Customer not found."
        assigned_seller_id = self._commercial_partner_seller_id(partner_row)
        if not assigned_seller_id:
            return None, None, "Customer has no assigned seller."
        if assigned_seller_id != seller_id:
            return None, None, "Customer not found for seller scope."
        context_seller_id = self.env.context.get("reactivation_seller_id")
        if context_seller_id and context_seller_id != seller_id:
            return None, None, "Customer not found for seller scope."
        seller_env = self._env_with_seller(seller_id)
        partner = seller_env["res.partner"].browse(customer_id)
        if not partner.exists():
            return None, None, "Customer not found for seller scope."
        return partner, seller_env, None

    def _bootstrap_partners_for_seller(self, seller, config):
        """Return bootstrap-eligible partner recordset for one enabled seller."""
        user = seller.user_id
        seller_env = self._env_with_seller(user.id)
        partners = seller_env["res.partner"].search(
            [
                ("user_id", "=", user.id),
                ("parent_id", "=", False),
                ("active", "=", True),
                ("customer_rank", ">", 0),
            ],
            order="id asc",
        )
        return self._filter_bootstrap_partners(partners, config, user.id)
