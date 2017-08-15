import json

from unittest.mock import patch

from jobserv.internal_requests import _sign
from jobserv.models import Build, BuildStatus, Project

from tests import JobServTest


class BuildAPITest(JobServTest):
    def setUp(self):
        super().setUp()
        self.create_projects('proj-1')
        self.project = Project.query.all()[0]
        self.urlbase = '/projects/%s/builds/' % self.project.name

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

    @patch('jobserv.api.Storage')
    def test_build_get_definition(self, storage):
        Build.create(self.project)
        storage().get_project_defintion.return_value = 'foo: bar'
        r = self.client.get(self.urlbase + 'job-1/builds/1/project.yml')
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
