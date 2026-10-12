"""One profile per user.

Revision ID: 4d2e89b167ab
Revises: f08c23decf92
"""

from alembic import op

revision = "4d2e89b167ab"
down_revision = "f08c23decf92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_profiles_user_id", "profiles", ["user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_profiles_user_id", "profiles", type_="unique")
