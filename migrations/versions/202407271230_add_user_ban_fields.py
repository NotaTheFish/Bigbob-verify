"""Add ban metadata columns to users"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20240727_add_user_bans"
down_revision = "20240715_telegram_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("banned_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("ban_reason", sa.String(length=255), nullable=True))
    op.alter_column("users", "is_banned", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "ban_reason")
    op.drop_column("users", "banned_at")
    op.drop_column("users", "is_banned")