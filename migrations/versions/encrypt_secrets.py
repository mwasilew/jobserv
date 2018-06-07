import sqlalchemy as sa

from alembic import op
from cryptography.fernet import Fernet

from jobserv.settings import SECRETS_FERNET_KEY


# revision identifiers, used by Alembic.
revision = '38c3b0c4903d'
down_revision = 'd3094c0b3c83'
branch_labels = None
depends_on = None


trigger_helper = sa.Table(
    'project_trigger',
    sa.MetaData(),
    sa.Column('id', sa.Integer, primary_key=True),
    sa.Column('secrets', sa.Text(), nullable=True),
)


def _migrate_secrets(encrypt=True):
    if not SECRETS_FERNET_KEY:
        raise ValueError('Missing environment value: SECRETS_FERNET_KEY')
    f = Fernet(SECRETS_FERNET_KEY.encode())

    connection = op.get_bind()
    for trigger in connection.execute(trigger_helper.select()):
        if encrypt:
            value = f.encrypt(trigger.secrets.encode()).decode()
        else:
            value = f.decrypt(trigger.secrets.encode()).decode()
        connection.execute(
            trigger_helper.update().where(
                trigger_helper.c.id == trigger.id
            ).values(
                secrets=value
            )
        )


def upgrade():
    _migrate_secrets(True)


def downgrade():
    _migrate_secrets(False)
