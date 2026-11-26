"""Delivery view of the metric dictionary, for the client website and data users.

Additive change to the client contract (ADR 0001): a new dl_ view, nothing renamed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_metric_definitions AS
SELECT m.metric_code, m.name AS metric_name, m.category, m.unit_type, m.period_nature, m.is_non_gaap,
       m.is_nareit_defined, m.definition
FROM ref_metric m
WHERE m.is_active = 1"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS dl_metric_definitions")
