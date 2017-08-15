import json

from flask_testing import TestCase

from jobserv import internal_requests, settings
from jobserv.jsend import _status_str
from jobserv.models import db, Project
from jobserv.flask import create_app


class JobServTest(TestCase):

    def create_app(self):
        settings.TESTING = True
        settings.SQLALCHEMY_DATABASE_URI = 'sqlite://'
        settings.PRESERVE_CONTEXT_ON_EXCEPTION = False
        internal_requests.INTERNAL_API_KEY = 'just for testing'.encode()
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

    def get_json(self, url, status_code=200, query_string=None, headers=None):
        resp = self.client.get(url, query_string=query_string, headers=headers)
        if status_code != resp.status_code:
            print('response text:', resp.data)
        self.assertEqual(status_code, resp.status_code, resp.data)
        data = json.loads(resp.data.decode())
        self.assertEqual(_status_str(status_code), data['status'])
        if 'data' not in data:
            raise ValueError('"data" not in dictionary: %r' % data)
        return data['data']

    def get_signed_json(self, url, status_code=200, query_string=None):
        headers = {}
        if not url.startswith('http://'):
            # signed url handling requires complete url
            url = 'http://localhost' + url
        internal_requests._sign(url, headers, 'GET')
        return self.get_json(url, status_code, query_string, headers)
