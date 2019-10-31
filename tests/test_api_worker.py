# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import json
import os
import shutil
import tempfile
import unittest

import jobserv.models
from jobserv.models import Build, BuildStatus, Project, Run, Worker, db

from unittest.mock import patch

from tests import JobServTest


class WorkerAPITest(JobServTest):
    def setUp(self):
        super().setUp()

        jobserv.models.WORKER_DIR = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, jobserv.models.WORKER_DIR)

    def test_worker_list(self):
        db.session.add(Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, []))
        w = Worker('w2', 'fedora', 14, 4, 'amd64', 'key', 2, [])
        db.session.add(w)
        db.session.commit()
        data = self.get_json('/workers/')
        self.assertEqual(2, len(data['workers']))
        self.assertEqual('w1', data['workers'][0]['name'])
        self.assertEqual(False, data['workers'][0]['enlisted'])
        self.assertEqual('w2', data['workers'][1]['name'])
        self.assertEqual(False, data['workers'][1]['enlisted'])

        w.deleted = True
        db.session.commit()
        data = self.get_json('/workers/')
        self.assertEqual(1, len(data['workers']))
        self.assertEqual('w1', data['workers'][0]['name'])

    def test_worker_get(self):
        db.session.add(Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, []))
        db.session.add(Worker('w2', 'fedora', 14, 4, 'amd64', 'key', 1, []))
        db.session.commit()
        data = self.get_json('/workers/w2/')
        self.assertEqual('w2', data['worker']['name'])
        self.assertEqual('fedora', data['worker']['distro'])
        self.assertEqual(1, data['worker']['concurrent_runs'])

    def test_worker_ping(self):
        w = Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, [])
        w.enlisted = True
        w.online = False
        db.session.add(w)
        db.session.commit()
        headers = [
            ('Content-type', 'application/json'),
            ('Authorization', 'Token key'),
        ]
        qs = 'num_available=1&foo=40'
        resp = self.client.get(
            '/workers/w1/', headers=headers, query_string=qs)
        self.assertEqual(200, resp.status_code)
        self.assertTrue(Worker.query.all()[0].online)
        p = os.path.join(jobserv.models.WORKER_DIR, 'w1/pings.log')
        with open(p) as f:
            buf = f.read()
            self.assertIn('num_available=1', buf)
            self.assertIn('foo=40', buf)

    def test_worker_log_event(self):
        w = Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, [])
        w.enlisted = True
        db.session.add(w)
        db.session.commit()
        headers = [
            ('Content-type', 'application/json'),
            ('Authorization', 'Token key'),
        ]

        event = '{"key": "val"}'
        resp = self.client.post(
            '/workers/w1/events/', headers=headers, data=event)
        self.assertEqual(201, resp.status_code, resp.data)
        p = os.path.join(jobserv.models.WORKER_DIR, 'w1/events.log')
        with open(p) as f:
            buf = f.read()
            self.assertEqual(event, buf)

        w.deleted = True
        db.session.commit()
        resp = self.client.post(
            '/workers/w1/events/', headers=headers, data=event)
        self.assertEqual(404, resp.status_code, resp.data)

    @patch('jobserv.api.worker.Storage')
    def test_worker_get_run(self, storage):
        if db.engine.dialect.name == 'sqlite':
            raise unittest.SkipTest('Test requires MySQL')
        rundef = {
            'run_url': 'foo',
            'runner_url': 'foo',
            'env': {}
        }
        storage().get_run_definition.return_value = json.dumps(rundef)
        w = Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, ['aarch96'])
        w.enlisted = True
        w.online = True
        db.session.add(w)

        self.create_projects('job-1')
        p = Project.query.all()[0]
        b = Build.create(p)
        r = Run(b, 'run0')
        r.host_tag = 'aarch96'
        db.session.add(r)

        db.session.commit()
        headers = [
            ('Content-type', 'application/json'),
            ('Authorization', 'Token key'),
        ]
        qs = 'available_runners=1&foo=2'
        resp = self.client.get(
            '/workers/w1/', headers=headers, query_string=qs)
        self.assertEqual(200, resp.status_code, resp.data)
        data = json.loads(resp.data.decode())
        self.assertEqual(1, len(data['data']['worker']['run-defs']))

        # now put a tag on the worker that doesn't match
        r.status = BuildStatus.QUEUED
        w.host_tags = 'amd64, foo'
        db.session.commit()
        resp = self.client.get(
            '/workers/w1/', headers=headers, query_string=qs)
        self.assertEqual(200, resp.status_code)
        data = json.loads(resp.data.decode())
        self.assertNotIn('run-defs', data['data']['worker'])

        # now tag the run with the worker's host name
        r.host_tag = 'w1'
        w.host_tags = ''
        db.session.commit()
        resp = self.client.get(
            '/workers/w1/', headers=headers, query_string=qs)
        self.assertEqual(200, resp.status_code)
        data = json.loads(resp.data.decode())
        self.assertEqual(1, len(data['data']['worker']['run-defs']))

        # now do a pattern match
        w.host_tags = 'aarch96'
        r.host_tag = 'Aa?c*'
        r.status = BuildStatus.QUEUED
        db.session.commit()
        resp = self.client.get(
            '/workers/w1/', headers=headers, query_string=qs)
        self.assertEqual(200, resp.status_code)
        data = json.loads(resp.data.decode())
        self.assertNotIn('run-defs', data['data']['worker'])

        # now mark it only for surges
        w.surges_only = True
        r.status = BuildStatus.QUEUED
        r.host_tag = 'aarch96'
        db.session.commit()
        resp = self.client.get(
            '/workers/w1/', headers=headers, query_string=qs)
        self.assertEqual(200, resp.status_code)
        data = json.loads(resp.data.decode())
        self.assertNotIn('run-defs', data['data']['worker'])

    @patch('jobserv.api.worker.Storage')
    def test_worker_sync_builds(self, storage):
        """Ensure Projects with "synchronous_builds" are assigned properly.

           1. Create a "synchronous" Project
           2. Add a RUNNING build and and QUEUED build
           3. Create a regular Project with a QUEUED build

           Make sure the QUEUED build from the second Project is assigned
           rather than the *older* but blocked build from the first Project.
        """
        if db.engine.dialect.name == 'sqlite':
            raise unittest.SkipTest('Test requires MySQL')
        rundef = {
            'run_url': 'foo',
            'runner_url': 'foo',
            'env': {}
        }
        storage().get_run_definition.return_value = json.dumps(rundef)
        w = Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, ['aarch96'])
        w.enlisted = True
        w.online = True
        db.session.add(w)

        # create a "synchronous" builds project
        self.create_projects('job-1')
        p = Project.query.all()[0]
        p.synchronous_builds = True
        db.session.commit()

        # add an active build
        b = Build.create(p)
        r = Run(b, 'p1r1')
        r.host_tag = 'aarch96'
        r.status = BuildStatus.RUNNING
        db.session.add(r)

        # now queue a build up
        b = Build.create(p)
        r = Run(b, 'p1r2')
        r.host_tag = 'aarch96'
        db.session.add(r)

        # create a normal project
        self.create_projects('job-2')
        p = Project.query.all()[1]
        db.session.commit()

        # queue up a build. This is "older" than the queued build for
        # the synchronous project, but should get selected below
        b = Build.create(p)
        r = Run(b, 'p2r1')
        r.host_tag = 'aarch96'
        db.session.add(r)

        db.session.commit()
        headers = [
            ('Content-type', 'application/json'),
            ('Authorization', 'Token key'),
        ]
        qs = 'available_runners=1&foo=2'
        resp = self.client.get(
            '/workers/w1/', headers=headers, query_string=qs)
        self.assertEqual(200, resp.status_code, resp.data)
        data = json.loads(resp.data.decode())
        self.assertEqual(1, len(data['data']['worker']['run-defs']))
        self.assertEqual(
            [BuildStatus.RUNNING, BuildStatus.QUEUED, BuildStatus.RUNNING],
            [x.status for x in Run.query])

    def test_worker_create_bad(self):
        data = {
        }
        r = self.client.post('/workers/w1/', data=data)
        self.assertEqual(400, r.status_code)
        self.assertIn('Missing required field(s): api_key,', r.data.decode())

    def test_worker_create(self):
        headers = [('Content-type', 'application/json')]
        data = {
            'api_key': '1234',
            'distro': 'ArchLinux',
            'mem_total': 42,
            'cpu_total': 4,
            'cpu_type': 'i286',
            'concurrent_runs': 2,
            'host_tags': [],
        }
        r = self.client.post(
            '/workers/w1/', headers=headers, data=json.dumps(data))
        self.assertEqual(201, r.status_code)
        data = self.get_json('/workers/w1/')['worker']
        self.assertNotIn('api_key', data)
        self.assertEqual('ArchLinux', data['distro'])
        self.assertEqual(4, data['cpu_total'])
        self.assertFalse(data['enlisted'])

    def test_worker_needs_auth(self):
        headers = [('Content-type', 'application/json')]
        db.session.add(Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, []))
        db.session.commit()
        data = {'distro': 'ArchLinux'}
        r = self.client.patch(
            '/workers/w1/', headers=headers, data=json.dumps(data))
        self.assertEqual(401, r.status_code)

    def test_worker_bad_auth(self):
        headers = [
            ('Content-type', 'application/json'),
            ('Authorization', 'Token keyy'),
        ]
        db.session.add(Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, []))
        db.session.commit()
        data = {'distro': 'ArchLinux'}
        r = self.client.patch(
            '/workers/w1/', headers=headers, data=json.dumps(data))
        self.assertEqual(401, r.status_code)

    def test_worker_update(self):
        headers = [
            ('Content-type', 'application/json'),
            ('Authorization', 'Token key'),
        ]
        w = Worker('w1', 'ubuntu', 12, 2, 'aarch64', 'key', 2, [])
        db.session.add(w)
        db.session.commit()
        data = {'distro': 'ArchLinux'}
        r = self.client.patch(
            '/workers/w1/', headers=headers, data=json.dumps(data))
        self.assertEqual(200, r.status_code)

        data = self.get_json('/workers/w1/')
        self.assertEqual('ArchLinux', data['worker']['distro'])

        w.deleted = True
        db.session.commit()
        r = self.client.patch(
            '/workers/w1/', headers=headers, data=json.dumps(data))
        self.assertEqual(404, r.status_code)
