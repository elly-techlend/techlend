"""Rename penalty fields to cumulative_interest

Revision ID: cd63025d6174
Revises: f1319eb2d23c
Create Date: 2025-05-31 10:22:51.086471

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cd63025d6174'
down_revision = 'f1319eb2d23c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ledger_entries', schema=None) as batch_op:
        batch_op.alter_column('penalty', new_column_name='cumulative_interest')
        batch_op.alter_column('penalty_balance', new_column_name='cumulative_interest_balance')

    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        batch_op.alter_column('penalty_paid', new_column_name='cumulative_interest_paid')


def downgrade():
    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        batch_op.alter_column('cumulative_interest_paid', new_column_name='penalty_paid')

    with op.batch_alter_table('ledger_entries', schema=None) as batch_op:
        batch_op.alter_column('cumulative_interest', new_column_name='penalty')
        batch_op.alter_column('cumulative_interest_balance', new_column_name='penalty_balance')

    # ### end Alembic commands ###
