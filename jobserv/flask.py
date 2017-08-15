import datetime

from flask import Flask
from flask.json import JSONEncoder
from flask_migrate import Migrate

from werkzeug.contrib.fixers import ProxyFix


class ISO8601_JSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat() + '+00:00'
        return super().default(obj)


def create_app(settings_object='jobserv.settings'):
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app)
    app.config.from_object(settings_object)
    from jobserv.models import db
    db.init_app(app)
    Migrate(app, db)

    from jobserv.storage import Storage
    if Storage.blueprint:
        app.register_blueprint(Storage.blueprint)

    app.json_encoder = ISO8601_JSONEncoder
    return app
