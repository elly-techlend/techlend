"""Add cumulative_interest to Loan

Revision ID: 930ae28209a4
Revises: ba08ae0f4032
Create Date: 2025-06-23 17:13:56.908183

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '930ae28209a4'
down_revision = 'ba08ae0f4032'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cumulative_interest', sa.Numeric(precision=12, scale=2), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loans', schema=None) as batch_op:
        batch_op.drop_column('cumulative_interest')

    # ### end Alembic commands ###
