from odoo import api, models, tools
from odoo.osv import expression
from odoo.tools import config
from odoo.tools.safe_eval import safe_eval

REACTIVATION_SCOPED_MODELS = {
    "res.partner",
    "crm.lead",
    "sale.order",
    "account.move",
}


class IrRule(models.Model):
    _inherit = "ir.rule"

    def _eval_context(self):
        res = super()._eval_context()
        seller_id = self.env.context.get("reactivation_seller_id")
        res["reactivation_seller_id"] = seller_id if seller_id else -1
        seller_partner_id = -1
        if seller_id:
            seller = self.env["res.users"].sudo().browse(seller_id)
            if seller.partner_id:
                seller_partner_id = seller.partner_id.id
        res["reactivation_seller_partner_id"] = seller_partner_id
        if (
            self.env.context.get("reactivation_unified_companies")
            and self.env.user.has_group(
                "tommasi_sales_reactivation.group_reactivation_agent"
            )
        ):
            res["company_ids"] = self.env["res.company"].sudo().search([]).ids
        return res

    def _compute_domain_keys(self):
        return super()._compute_domain_keys() + [
            "reactivation_seller_id",
            "reactivation_seller_partner_id",
            "reactivation_unified_companies",
        ]

    @api.model
    @tools.conditional(
        "xml" not in config["dev_mode"],
        tools.ormcache(
            "self.env.uid",
            "self.env.su",
            "model_name",
            "mode",
            "tuple(self._compute_domain_context_values())",
        ),
    )
    def _compute_domain(self, model_name, mode="read"):
        if (
            self.env.user.has_group(
                "tommasi_sales_reactivation.group_reactivation_agent"
            )
            and model_name in REACTIVATION_SCOPED_MODELS
        ):
            reactivation_group = self.env.ref(
                "tommasi_sales_reactivation.group_reactivation_agent"
            )
            rules = self._get_rules(model_name, mode=mode)
            if not rules:
                return
            eval_context = self._eval_context()
            global_domains = []
            agent_domains = []
            user_groups = self.env.user.sudo().groups_id
            for rule in rules.sudo():
                dom = (
                    safe_eval(rule.domain_force, eval_context)
                    if rule.domain_force
                    else []
                )
                dom = expression.normalize_domain(dom)
                if not rule.groups:
                    global_domains.append(dom)
                elif rule.groups & user_groups and reactivation_group in rule.groups:
                    agent_domains.append(dom)
            if agent_domains:
                return expression.AND(global_domains + agent_domains)
        return super()._compute_domain(model_name, mode=mode)
