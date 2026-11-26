"""Index for newsletter confirmation links, for email (ADR 0013).

When the site sends email (RS_EMAIL_PROVIDER), a public newsletter sign-up gets a
confirmation link; the link is looked up by the SHA-256 of its token.

Revision ID: a0002
Revises: a0001
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "a0002"
down_revision = "a0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_sub_confirm_token ON app_newsletter_subscription (confirm_token_hash)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_sub_confirm_token ON app_newsletter_subscription")
