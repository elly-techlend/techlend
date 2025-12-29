"""Merge all heads

Revision ID: 5f8cc5db944d
Revises: 0519708947ff, 3699861b1ec8, 3feece28916f
Create Date: 2025-12-27 11:34:27.641654

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5f8cc5db944d'
down_revision = ('0519708947ff', '3699861b1ec8', '3feece28916f')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
