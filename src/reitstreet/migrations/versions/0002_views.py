"""Warehouse and delivery views.

wh_  views: analyst-facing convenience over wh_metric_value.
dl_  views: the client delivery contract. Column names and meanings of dl_ views are
     a public interface: change them only with a new migration and a CHANGELOG entry.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SOURCE_RANK = "FIELD(v.source_type, 'manual', 'xbrl', 'supplemental', 'derived')"

VIEWS: list[tuple[str, str]] = [
    (
        "wh_metric_latest",
        f"""
CREATE OR REPLACE SQL SECURITY INVOKER VIEW wh_metric_latest AS
SELECT v.*,
       (ROW_NUMBER() OVER (PARTITION BY v.company_id, v.metric_id, v.period_type, v.period_start_key, v.period_end, v.segment
                           ORDER BY {SOURCE_RANK}) = 1) AS is_preferred
FROM wh_metric_value v
WHERE v.retired_at IS NULL AND v.known_to IS NULL""",
    ),
    (
        "dl_companies",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_companies AS
SELECT c.company_id, c.cik, c.ticker, c.name, c.exchange, c.sector, c.reit_type, c.fiscal_year_end,
       c.in_universe, c.updated_at
FROM ref_company c""",
    ),
    (
        "dl_metrics",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_metrics AS
SELECT c.ticker, c.cik, m.metric_code, m.name AS metric_name, m.category, v.period_type, v.period_start,
       v.period_end, v.fiscal_year, v.fiscal_quarter, v.segment, v.value, v.unit, v.source_type, v.version,
       v.known_from AS published_date, v.is_preferred, v.source_accession, f.index_url AS source_filing_url,
       d.source_url AS source_document_url, v.source_page_no, v.value_id
FROM wh_metric_latest v
JOIN ref_company c ON c.company_id = v.company_id
JOIN ref_metric m ON m.metric_id = v.metric_id
LEFT JOIN stg_sec_filing f ON f.company_id = v.company_id AND f.accession_no = v.source_accession
LEFT JOIN stg_document d ON d.doc_id = v.source_doc_id""",
    ),
    (
        "dl_metrics_history",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_metrics_history AS
SELECT c.ticker, c.cik, m.metric_code, v.period_type, v.period_start, v.period_end, v.fiscal_year,
       v.fiscal_quarter, v.segment, v.value, v.unit, v.source_type, v.version,
       v.known_from AS published_date, v.known_to AS superseded_date, v.source_accession,
       v.source_doc_id, v.source_page_no, v.value_id
FROM wh_metric_value v
JOIN ref_company c ON c.company_id = v.company_id
JOIN ref_metric m ON m.metric_id = v.metric_id
WHERE v.retired_at IS NULL""",
    ),
    (
        "dl_filings",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_filings AS
SELECT c.ticker, c.cik, f.accession_no, f.form_type, f.filing_date, f.report_date, f.items,
       f.index_url, f.primary_doc_url
FROM stg_sec_filing f
JOIN ref_company c ON c.company_id = f.company_id""",
    ),
    (
        "dl_documents",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_documents AS
SELECT c.ticker, c.cik, d.doc_id, d.doc_kind, d.source, d.period_end_date, d.published_date,
       d.original_filename, d.source_url, d.accession_no, d.status
FROM stg_document d
LEFT JOIN ref_company c ON c.company_id = d.company_id
WHERE d.status <> 'duplicate'""",
    ),
]


def upgrade() -> None:
    for _name, ddl in VIEWS:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {name}")
