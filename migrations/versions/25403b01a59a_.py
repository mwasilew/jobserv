"""empty message

Revision ID: 25403b01a59a
Revises: ec74501d7086
Create Date: 2019-03-08 14:58:57.115312

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '25403b01a59a'
down_revision = 'ec74501d7086'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('project_trigger', sa.Column('queue_priority', sa.Integer(), nullable=True))
    op.add_column('runs', sa.Column('queue_priority', sa.Integer(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('runs', 'queue_priority')
    op.drop_column('project_trigger', 'queue_priority')
    # ### end Alembic commands ###
