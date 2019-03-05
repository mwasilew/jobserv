# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import json

from unittest.mock import patch

from jobserv.models import Build, BuildStatus, Project, Run, TriggerTypes, db
from jobserv.permissions import _sign

from tests import JobServTest


class ProjectAPITest(JobServTest):
    def test_no_projects(self):
        jobs = self.get_json('/projects/')['projects']
        self.assertEqual([], jobs)

    def test_project_list(self):
        self.create_projects('job-1', 'job-2', 'job-3')
        jobs = self.get_json('/projects/')['projects']
        self.assertEqual(3, len(jobs))
        for i, j in enumerate(jobs):
            self.assertEqual('job-%d' % (i + 1), j['name'])

    def test_project_get(self):
        self.create_projects('job-1', 'job-2', 'job-3')
        j = self.get_json('/projects/job-2/')['project']
        self.assertEqual('job-2', j['name'])

    def test_project_get_404(self):
        r = self.client.get('/projects/job-2/')
        self.assertEqual(404, r.status_code)
        self.assertIn('message', json.loads(r.data.decode()))

    @patch('jobserv.permissions.project_can_access')
    def test_project_permission(self, can_access):
        can_access.return_value = False
        self.create_projects('job-1')
        r = self.client.get('/projects/job-1/')
        self.assertEqual(404, r.status_code)

    def test_project_create_denied(self):
        r = self.client.post('/projects/', data=json.dumps({'name': 'foo'}))
        self.assertEqual(401, r.status_code)

    def test_project_create(self):
        url = 'http://localhost/projects/'
        headers = {'Content-type': 'application/json'}
        _sign(url, headers, 'POST')
        r = self.client.post(
            url, headers=headers, data=json.dumps({'name': 'foo'}))
        self.assertEqual(201, r.status_code, r.data)
        Project.query.filter(Project.name == 'foo').one()

    def test_project_run_history(self):
        self.create_projects('proj-1')
        p = Project.query.all()[0]

        for x in range(4):
            b = Build.create(p)
            r = Run(b, 'run0')
            r.status = BuildStatus.PASSED
            if x % 2 == 0:
                r.status = BuildStatus.FAILED
            db.session.add(r)
            r = Run(b, 'run1')
            db.session.add(r)
        db.session.commit()
        r = self.get_json('/projects/proj-1/history/run0/')
        expected = ['PASSED', 'FAILED', 'PASSED', 'FAILED']
        self.assertEqual(expected, [x['status'] for x in r['runs']])
        expected = ['run0', 'run0', 'run0', 'run0']
        self.assertEqual(expected, [x['name'] for x in r['runs']])

    def test_project_trigger_create(self):
        self.create_projects('proj-1')
        url = 'http://localhost/projects/proj-1/triggers/'

        headers = {'Content-type': 'application/json'}
        _sign(url, headers, 'POST')
        data = {
            'owner': 'gavin.gavel',
            'type': 'git_poller',
            'secret1': 'ThisIsThePassword',
        }
        r = self.client.post(url, headers=headers, data=json.dumps(data))
        self.assertEqual(201, r.status_code, r.data)
        p = Project.query.filter(Project.name == 'proj-1').one()
        self.assertEqual(1, len(p.triggers))
        t = p.triggers[0]
        self.assertEqual(TriggerTypes.git_poller.value, t.type)
        self.assertEqual(data['owner'], t.user)
        self.assertEqual(data['secret1'], t.secret_data['secret1'])
