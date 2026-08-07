"""Drop facts-snapshot table and leftover noupdate cron XMLID."""

import logging

_logger = logging.getLogger(__name__)

MODULE = "tommasi_sales_reactivation"
CRON_XMLID = "ir_cron_refresh_facts_snapshots"
SNAPSHOT_TABLE = "tommasi_reactivation_facts_snapshot"


def migrate(cr, version):
    """Remove snapshot persistence left behind by 15.0.1.6.0 and earlier."""
    if not version:
        return

    cr.execute("DROP TABLE IF EXISTS %s CASCADE" % SNAPSHOT_TABLE)
    _logger.info("Dropped table %s if it existed", SNAPSHOT_TABLE)

    cr.execute(
        """
        SELECT res_id
          FROM ir_model_data
         WHERE module = %s
           AND name = %s
           AND model = 'ir.cron'
        """,
        (MODULE, CRON_XMLID),
    )
    row = cr.fetchone()
    if row and row[0]:
        cr.execute("DELETE FROM ir_cron WHERE id = %s", (row[0],))
        _logger.info("Deleted leftover cron id=%s (%s)", row[0], CRON_XMLID)

    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = %s
           AND name = %s
        """,
        (MODULE, CRON_XMLID),
    )
