import json

from unittest.mock import patch

from jobserv.internal_requests import _sign
from jobserv.models import Build, BuildStatus, Project, Run, Test, db

from tests import JobServTest


class BuildAPITest(JobServTest):
    def setUp(self):
        super().setUp()
        self.create_projects('proj-1')
        self.project = Project.query.all()[0]
        self.urlbase = '/projects/%s/builds/' % self.project.name

    def _post(self, url, data, headers, status=200):
        resp = self.client.post(url, data=data, headers=headers)
        self.assertEqual(status, resp.status_code, resp.data)
        return resp

    def test_no_builds(self):
        builds = self.get_json(self.urlbase)['builds']
        self.assertEqual([], builds)

    def test_build_list(self):
        Build.create(self.project)
        Build.create(self.project)
        Build.create(self.project)

        builds = self.get_json(self.urlbase)['builds']
        self.assertEqual(3, len(builds))
        for i, b in enumerate(builds):
            self.assertEqual(3 - i, b['build_id'])

    def test_build_list_paginate(self):
        for x in range(8):
            Build.create(self.project)
        data = self.get_json(self.urlbase + '?limit=4')
        self.assertIn('next', data)
        data = self.get_json(data['next'])
        self.assertNotIn('next', data)
        data = self.get_json(self.urlbase + '?limit=4&page=2')
        self.assertEqual([], data['builds'])

    def test_build_get(self):
        Build.create(self.project)
        b = Build.create(self.project)
        Build.create(self.project)
        data = self.get_json(self.urlbase + '2/')['build']
        self.assertEqual(b.build_id, data['build_id'])
        self.assertEqual(
            ['QUEUED'], [x['status'] for x in data['status_events']])

    @patch('jobserv.api.build.Storage')
    def test_build_get_definition(self, storage):
        Build.create(self.project)
        storage().get_project_defintion.return_value = 'foo: bar'
        r = self.client.get(self.urlbase + '1/project.yml')
        self.assertEqual(200, r.status_code, r.data)
        self.assertEqual('foo: bar', r.data.decode())

    def test_build_get_latest(self):
        Build.create(self.project)
        b = Build.create(self.project)
        b.status = BuildStatus.PASSED
        Build.create(self.project)
        data = self.get_json(self.urlbase + 'latest/')['build']
        self.assertEqual(b.build_id, data['build_id'])

    @patch('jobserv.storage.gce_storage.storage')
    def test_build_trigger_fails(self, storage):
        # ensure we have a graceful failure when we are triggered
        headers = {}
        r = self.client.post(self.urlbase, data={}, headers=headers)
        self.assertEqual(401, r.status_code)  # not signed

        _sign('http://localhost/projects/proj-1/builds/', headers, 'POST')
        r = self.client.post(self.urlbase, data={}, headers=headers)
        self.assertEqual(500, r.status_code)
        data = json.loads(r.data.decode())
        self.assertEqual('error', data['status'])
        self.assertIn('console.log', r.headers['Location'])

    @patch('jobserv.api.build.Storage')
    def test_promote_list_empty(self, storage):
        b = Build.create(self.project)
        db.session.add(Run(b, 'run0'))
        db.session.add(Run(b, 'run1'))
        for r in b.runs:
            r.set_status(BuildStatus.PASSED)
        builds = self.get_json(self.urlbase + 'promoted-builds/')['builds']
        self.assertEqual(0, len(builds))

    @patch('jobserv.api.build.Storage')
    def test_promote_list(self, storage):
        b = Build.create(self.project)
        db.session.add(Run(b, 'run0'))
        db.session.add(Run(b, 'run1'))
        for r in b.runs:
            r.set_status(BuildStatus.PASSED)
            t = Test(r, 't1', None, BuildStatus.PASSED)
            db.session.add(t)
        b.status = BuildStatus.PROMOTED
        b.name = 'release-X'
        b.annotation = 'foo bar'
        builds = self.get_json(self.urlbase + 'promoted-builds/')['builds']
        self.assertEqual(1, len(builds))
        self.assertEqual('release-X', builds[0]['name'])
        self.assertEqual('foo bar', builds[0]['annotation'])
        self.assertEqual(
            ['run0-t1', 'run1-t1'], [x['name'] for x in builds[0]['tests']])

    @patch('jobserv.api.build.Storage')
    def test_promote_get(self, storage):
        b = Build.create(self.project)
        db.session.add(Run(b, 'run0'))
        db.session.add(Run(b, 'run1'))
        for r in b.runs:
            r.set_status(BuildStatus.PASSED)
            t = Test(r, 't1', None, BuildStatus.PASSED)
            db.session.add(t)
        b.status = BuildStatus.PROMOTED
        b.name = 'release-X'
        b.annotation = 'foo bar'
        build = self.get_json(
            self.urlbase + 'promoted-builds/release-X/')['build']
        self.assertEqual('foo bar', build['annotation'])

    def test_promote_post(self):
        b = Build.create(self.project)
        db.session.add(Run(b, 'run0'))
        db.session.add(Run(b, 'run1'))

        url = 'http://localhost/projects/proj-1/builds/%d/promote' % b.build_id

        headers = {
            'Content-type': 'application/json',
        }
        data = {
            'name': 'release-x',
            'annotation': 'foo bar',
        }

        # you can't promote an in-progress build
        _sign(url, headers, 'POST')
        self._post(url, json.dumps(data), headers, 400)

        for r in b.runs:
            r.set_status(BuildStatus.PASSED)
        self._post(url, json.dumps(data), headers, 201)
        db.session.refresh(b)
        self.assertEqual(BuildStatus.PROMOTED, b.status)
        self.assertEqual(data['name'], b.name)
        self.assertEqual(data['annotation'], b.annotation)
