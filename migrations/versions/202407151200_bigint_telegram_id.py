"""Ensure telegram_id uses BIGINT"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20240715_telegram_bigint"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT USING telegram_id::BIGINT;"
    )
    op.execute(
        "ALTER TABLE verifications ALTER COLUMN telegram_id TYPE BIGINT USING telegram_id::BIGINT;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE verifications ALTER COLUMN telegram_id TYPE INTEGER USING telegram_id::INTEGER;"
    )
    op.execute(
        "ALTER TABLE users ALTER COLUMN telegram_id TYPE INTEGER USING telegram_id::INTEGER;"
    )