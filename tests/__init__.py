from flask_testing import TestCase

from jobserv import settings
from jobserv.models import db, Project
from jobserv.flask import create_app


class JobServTest(TestCase):

    def create_app(self):
        settings.TESTING = True
        settings.SQLALCHEMY_DATABASE_URI = 'sqlite://'
        settings.PRESERVE_CONTEXT_ON_EXCEPTION = False
        return create_app(settings)

    def setUp(self):
        super().setUp()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()

    def create_projects(self, *names):
        for n in names:
            db.session.add(Project(n))
        db.session.commit()
