"""Add is_system_generated to LoanRepayment

Revision ID: 2f87b5c9bd44
Revises: cd63025d6174
Create Date: 2025-06-02 13:01:12.553006

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f87b5c9bd44'
down_revision = 'cd63025d6174'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_system_generated', sa.Boolean(), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        batch_op.drop_column('is_system_generated')

    # ### end Alembic commands ###
