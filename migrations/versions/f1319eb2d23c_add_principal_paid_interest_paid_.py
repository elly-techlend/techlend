"""Add principal_paid, interest_paid, penalty_paid columns to loan_repayments

Revision ID: f1319eb2d23c
Revises: ad934029dbb9
Create Date: 2025-05-30 16:52:46.457657

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1319eb2d23c'
down_revision = 'ad934029dbb9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        # Step 1: Add columns nullable=True
        batch_op.add_column(sa.Column('principal_paid', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('interest_paid', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('penalty_paid', sa.Float(), nullable=True))

    # Step 2: Fill existing rows with default values
    op.execute('UPDATE loan_repayments SET principal_paid = 0.0 WHERE principal_paid IS NULL')
    op.execute('UPDATE loan_repayments SET interest_paid = 0.0 WHERE interest_paid IS NULL')
    op.execute('UPDATE loan_repayments SET penalty_paid = 0.0 WHERE penalty_paid IS NULL')

    # Step 3: Alter columns to set NOT NULL
    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        batch_op.alter_column('principal_paid', nullable=False)
        batch_op.alter_column('interest_paid', nullable=False)
        batch_op.alter_column('penalty_paid', nullable=False)


def downgrade():
    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        batch_op.drop_column('penalty_paid')
        batch_op.drop_column('interest_paid')
        batch_op.drop_column('principal_paid')

    # ### end Alembic commands ###
