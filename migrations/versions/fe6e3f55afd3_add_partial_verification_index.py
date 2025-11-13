"""add_partial_verification_index

Revision ID: fe6e3f55afd3
Revises: 20240727_add_user_bans
Create Date: 2025-11-13 09:27:04.009385
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "fe6e3f55afd3"
down_revision = "20240727_add_user_bans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_verification_active", "verifications", type_="unique")
    op.create_index(
        "uq_verification_active",
        "verifications",
        ["telegram_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_verification_active", table_name="verifications")
    op.create_unique_constraint(
        "uq_verification_active", "verifications", ["telegram_id", "status"]
    )