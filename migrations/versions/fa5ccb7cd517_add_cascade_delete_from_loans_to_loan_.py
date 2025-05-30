"""Add cascade delete from loans to loan_repayments

Revision ID: fa5ccb7cd517
Revises: dd0459d4c163
Create Date: 2025-05-28 10:59:09.454250

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fa5ccb7cd517'
down_revision = 'dd0459d4c163'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        batch_op.drop_constraint('loan_repayments_loan_id_fkey', type_='foreignkey')
        batch_op.create_foreign_key(None, 'loans', ['loan_id'], ['id'], ondelete='CASCADE')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loan_repayments', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.create_foreign_key('loan_repayments_loan_id_fkey', 'loans', ['loan_id'], ['id'])

    # ### end Alembic commands ###
