"""IR coverage: record pages verified in a real browser.

ref_ir_profile.probe_status gains 'works_headless': a person or agent opened the results
page in a browser, the rendered page listed supplementals, and the plain HTML did not.
Such pages are fetched by the headless strategy. ops_ir_coverage, before a company's first
live check, now reports js_only as needs_headless and works_headless as works_headless.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

STATUSES_OLD = "'works','js_only','no_supp','error','not_probed'"
STATUSES_NEW = "'works','works_headless','js_only','no_supp','error','not_probed'"
COMMENT = "Result of the last manual coverage probe (from the seed file)"

VIEW = """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW ops_ir_coverage AS
SELECT c.company_id, c.ticker, c.name, c.reit_type,
       COALESCE(p.strategy, 'static_html') AS strategy, p.platform, COALESCE(p.expects_supplemental, 1) AS expects_supplemental,
       COALESCE(p.probe_status, 'not_probed') AS probe_status, p.probe_supp_links, p.probe_latest_period, p.probe_date,
       k.checked_at AS last_checked_at, k.strategy AS last_strategy, k.outcome AS last_outcome, k.http_status AS last_http_status,
       k.supp_links AS last_supp_links, k.latest_supp_period AS last_page_supp_period,
       (SELECT MAX(d.period_end_date) FROM stg_document d
         WHERE d.company_id = c.company_id AND d.doc_kind = 'supplemental' AND d.status IN ('fetched','parsed','needs_review')) AS latest_supplemental_held,
       CASE
         WHEN k.outcome IS NULL AND p.probe_status = 'js_only' THEN 'needs_headless'
         WHEN k.outcome IS NULL THEN COALESCE(p.probe_status, 'not_probed')
         WHEN k.outcome IN ('ok','not_modified') AND k.supp_links > 0 THEN 'works'
         WHEN k.outcome IN ('js_only','needs_headless') THEN 'needs_headless'
         WHEN k.outcome = 'robots_blocked' THEN 'blocked'
         WHEN k.outcome IN ('http_error','layout_break') THEN 'broken'
         WHEN k.outcome IN ('ok','no_documents','not_modified') THEN 'no_supp'
         ELSE k.outcome
       END AS coverage
FROM ref_company c
LEFT JOIN ref_ir_profile p ON p.company_id = c.company_id
LEFT JOIN ops_ir_check k ON k.check_id = (
    SELECT MAX(k2.check_id) FROM ops_ir_check k2 WHERE k2.company_id = c.company_id AND k2.source_id IS NOT NULL)
WHERE c.in_universe = 1"""


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE ref_ir_profile MODIFY probe_status ENUM({STATUSES_NEW}) NOT NULL DEFAULT 'not_probed' "
        f"COMMENT '{COMMENT}'"
    )
    op.execute(VIEW)


def downgrade() -> None:
    from importlib import import_module

    op.execute("UPDATE ref_ir_profile SET probe_status = 'js_only' WHERE probe_status = 'works_headless'")
    op.execute(
        f"ALTER TABLE ref_ir_profile MODIFY probe_status ENUM({STATUSES_OLD}) NOT NULL DEFAULT 'not_probed' "
        f"COMMENT '{COMMENT}'"
    )
    m0003 = import_module("reitstreet.migrations.versions.0003_ir_fetcher")
    for _name, ddl in m0003.VIEWS:
        op.execute(ddl)
