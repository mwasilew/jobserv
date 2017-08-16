# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import json

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
