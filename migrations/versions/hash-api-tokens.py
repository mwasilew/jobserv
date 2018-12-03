import sqlalchemy as sa

import bcrypt

from alembic import op


# revision identifiers, used by Alembic.
revision = '3c1cd792f312'
down_revision = '38c3b0c4903d'
branch_labels = None
depends_on = None


worker_helper = sa.Table(
    'workers',
    sa.MetaData(),
    sa.Column('name', sa.Text(), primary_key=True),
    sa.Column('api_key', sa.Text(), nullable=True),
)


def upgrade():
    connection = op.get_bind()
    for worker in connection.execute(worker_helper.select()):
        value = bcrypt.hashpw(worker.api_key.encode(), bcrypt.gensalt())
        connection.execute(
            worker_helper.update().where(
                worker_helper.c.name == worker.name
            ).values(
                api_key=value
            )
        )


def downgrade():
    raise NotImplementedError('This migration can not be reversed')
