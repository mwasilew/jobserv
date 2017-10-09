# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import datetime

from flask import Flask
from flask.json import JSONEncoder
from flask_migrate import Migrate

from werkzeug.contrib.fixers import ProxyFix
from werkzeug.routing import UnicodeConverter

from jobserv.settings import PROJECT_NAME_REGEX


class ISO8601_JSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat() + '+00:00'
        return super().default(obj)


class ProjectConverter(UnicodeConverter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, *kwargs)
        if PROJECT_NAME_REGEX:
            self.regex = PROJECT_NAME_REGEX


def create_app(settings_object='jobserv.settings'):
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app)
    app.config.from_object(settings_object)

    ProjectConverter.settings = settings_object
    app.url_map.converters['project'] = ProjectConverter

    from jobserv.models import db
    db.init_app(app)
    Migrate(app, db)

    import jobserv.api
    jobserv.api.register_blueprints(app)

    from jobserv.storage import Storage
    if Storage.blueprint:
        app.register_blueprint(Storage.blueprint)

    app.json_encoder = ISO8601_JSONEncoder
    return app
