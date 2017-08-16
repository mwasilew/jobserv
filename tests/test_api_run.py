# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import contextlib
import json
import os
import shutil
import tempfile

from unittest.mock import Mock, patch

import jobserv.storage.base

from jobserv.storage import Storage
from jobserv.models import Build, BuildStatus, Project, Run, Test, TestResult, db

from tests import JobServTest


class RunAPITest(JobServTest):
    def setUp(self):
        super().setUp()
        self.create_projects('proj-1')
        p = Project.query.all()[0]
        self.build = Build.create(p)
        self.urlbase = '/projects/proj-1/builds/1/runs/'

        jobserv.storage.base.JOBS_DIR = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, jobserv.storage.base.JOBS_DIR)

    def test_no_runs(self):
        self.assertEqual([], self.get_json(self.urlbase)['runs'])

    def test_run_list(self):
        db.session.add(Run(self.build, 'run0'))
        db.session.add(Run(self.build, 'run1'))

        runs = self.get_json(self.urlbase)['runs']
        self.assertEqual(2, len(runs))
        for i, r in enumerate(runs):
            self.assertEqual('run%d' % i, r['name'])
            self.assertEqual('QUEUED', r['status'])

    @patch('jobserv.storage.gce_storage.storage')
    def test_run_get(self, storage):
        db.session.add(Run(self.build, 'run0'))
        db.session.add(Run(self.build, 'run1'))

        # be a little anal and make sure our DB queries can handle
        # multiple jobs with the same run names
        self.create_projects('proj-2')
        p = Project.query.all()[1]
        b = Build.create(p)
        r = Run(b, 'run0')
        db.session.add(r)
        r.set_status(BuildStatus.PASSED)
        r = Run(b, 'run1')
        db.session.add(r)
        r.set_status(BuildStatus.FAILED)

        data = self.get_json(self.urlbase + 'run1/')['run']
        self.assertEqual('run1', data['name'])
        self.assertEqual('QUEUED', data['status'])

        url = '/projects/proj-2/builds/%d/runs/%s/' % (b.build_id, 'run1')
        data = self.get_json(url)['run']
        self.assertEqual('FAILED', data['status'])
        self.assertEqual('FAILED', data['status_events'][0]['status'])

    @patch('jobserv.api.run.Storage')
    def test_run_get_definition(self, storage):
        """Ensure unauthenticated requests redact the secrets"""
        rundef = {
            'api_key': 'secret',
            'secrets': {'key': 'val'},
            'script-repo': {'token': 'secret'},
        }
        storage().get_run_definition.return_value = json.dumps(rundef)
        db.session.add(Run(self.build, 'run0'))
        db.session.commit()
        r = self.client.get(self.urlbase + 'run0/.rundef.json')
        self.assertEqual(200, r.status_code, r.data)
        data = json.loads(r.data.decode())
        self.assertEqual('TODO', data['secrets']['key'])
        self.assertEqual('secret', data['script-repo']['token'])
        self.assertIsNone(data.get('api_key'))

    def _post(self, url, data, headers, status=200):
        resp = self.client.post(url, data=data, headers=headers)
        self.assertEqual(status, resp.status_code, resp.data)
        return resp

    def test_run_stream_not_authenticated(self):
        r = Run(self.build, 'run0')
        db.session.add(r)
        db.session.commit()

        headers = [('Authorization', 'Token badtoken')]
        self._post(self.urlbase + 'run0/', 'message', headers, 401)

    # sqlite doesn't support with_for_update, so disable it by mocking "locked"
    @patch('jobserv.models.Build.locked')
    @patch('jobserv.storage.gce_storage.storage')
    def test_run_stream(self, storage, locked):
        r = Run(self.build, 'run0')
        db.session.add(r)
        db.session.commit()

        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-STATUS', 'RUNNING'),
        ]
        self._post(self.urlbase + 'run0/', 'message', headers, 200)

        with Storage().console_logfd(r, 'r') as f:
            self.assertEqual('message', f.read())
        db.session.refresh(r)
        self.assertEqual('RUNNING', r.status.name)

    @patch('jobserv.storage.gce_storage.storage')
    def test_get_stream(self, storage):
        r = Run(self.build, 'run0')
        r.status = BuildStatus.RUNNING
        db.session.add(r)
        db.session.commit()

        with Storage().console_logfd(r, 'ab') as f:
            f.write(b'this is the message')

        resp = self.client.get(self.urlbase + 'run0/console.log')
        self.assertEqual(200, resp.status_code)
        self.assertEqual('text/plain', resp.mimetype)

    @patch('jobserv.storage.gce_storage.storage')
    def test_run_metadata(self, storage):
        r = Run(self.build, 'run0')
        db.session.add(r)
        db.session.commit()

        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-METADATA', 'foobar-meta'),
        ]
        self._post(self.urlbase + 'run0/', 'message', headers, 200)
        db.session.refresh(r)
        self.assertEqual('foobar-meta', r.meta)

    @patch('jobserv.storage.gce_storage.storage')
    def test_upload(self, storage):
        r = Run(self.build, 'run0')
        r.status = BuildStatus.RUNNING
        db.session.add(r)
        db.session.commit()

        headers = [('Authorization', 'Token %s' % r.api_key)]
        storage.generate_signed.return_value = {
            'foo': 'bar',
            'blah': 'bam',
        }
        url = self.urlbase + 'run0/create_signed'
        uploads = json.dumps(['foo', 'bar'])
        self._post(url, uploads, headers, 200)

    @patch('jobserv.api.run.Storage')
    def test_run_complete_triggers(self, storage):
        m = Mock()
        m.get_project_definition.return_value = json.dumps({
            'timeout': 5,
            'triggers': [
                {
                    'name': 'github',
                    'type': 'github_pr',
                    'runs': [{
                        'name': 'run0',
                        'host-tag': 'foo*',
                        'triggers': [
                            {'name': 'triggered', 'run-names': '{name}-run0'}
                        ]
                    }],
                },
                {
                    'name': 'triggered',
                    'type': 'simple',
                    'runs': [{
                        'name': 'test',
                        'host-tag': 'bar',
                        'container': 'container-foo',
                        'script': 'test',
                    }],
                },
            ],
            'scripts': {
                'test': '#test#',
            }
        })
        m.console_logfd.return_value = open('/dev/null', 'w')
        m.get_run_definition.return_value = json.dumps({})
        storage.return_value = m
        r = Run(self.build, 'run0')
        r.trigger = 'github'
        r.status = BuildStatus.RUNNING
        db.session.add(r)
        db.session.commit()

        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-STATUS', 'PASSED'),
        ]
        self._post(self.urlbase + 'run0/', None, headers, 200)
        run = Run.query.all()[1]
        self.assertEqual('test-run0', run.name)
        self.assertEqual('QUEUED', run.status.name)

    @patch('jobserv.api.run.Storage')
    def test_run_complete_tests_default(self, storage):
        m = Mock()
        m.get_project_definition.return_value = json.dumps({
            'timeout': 5,
            'triggers': [
                {
                    'name': 'github',
                    'type': 'github_pr',
                    'runs': [{
                        'name': 'run0',
                    }],
                },
            ],
        })

        @contextlib.contextmanager
        def _logfd(run, mode='r'):
            path = os.path.join(jobserv.storage.base.JOBS_DIR, run.name)
            with open(path, mode) as f:
                yield f
        m.console_logfd = _logfd
        data = '''
        t1: PASSED
        t2: FAILED
        t3: foo
        '''
        rundef = {
            'test-grepping': {
                'result-pattern': '\s*(?P<name>\S+): '
                                  '(?P<result>(PASSED|FAILED|foo))',
                'fixupdict': {'foo': 'PASSED'},
            }
        }
        m.get_run_definition.return_value = json.dumps(rundef)
        storage.return_value = m
        r = Run(self.build, 'run0')
        r.trigger = 'github'
        r.status = BuildStatus.RUNNING
        db.session.add(r)
        db.session.commit()

        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-STATUS', 'PASSED'),
        ]
        self._post(self.urlbase + 'run0/', data, headers, 200)
        tests = [(x.name, x.status.name) for x in Test.query.all()]
        self.assertEqual([('default', 'PASSED')], tests)
        results = [(x.name, x.status.name) for x in TestResult.query.all()]
        expected = [('t1', 'PASSED'), ('t2', 'FAILED'), ('t3', 'PASSED')]
        self.assertEqual(expected, results)

    @patch('jobserv.api.run.Storage')
    def test_run_complete_tests(self, storage):
        # Same as test_run_complete_tests_default but with a test name pattern
        m = Mock()
        m.get_project_definition.return_value = json.dumps({
            'timeout': 5,
            'triggers': [
                {
                    'name': 'github',
                    'type': 'github_pr',
                    'runs': [{
                        'name': 'run0',
                    }],
                },
            ],
        })

        @contextlib.contextmanager
        def _logfd(run, mode='r'):
            path = os.path.join(jobserv.storage.base.JOBS_DIR, run.name)
            with open(path, mode) as f:
                yield f
        m.console_logfd = _logfd
        data = """
        t1: PASSED
        t2: FAILED
        Starting Test: TNAME...
        t3: foo
        """
        rundef = {
            'test-grepping': {
                'test-pattern': '.*Starting Test: (?P<name>\S+)...',
                'result-pattern': '\s*(?P<name>\S+): '
                                  '(?P<result>(PASSED|FAILED|foo))',
                'fixupdict': {'foo': 'PASSED'},
            }
        }
        m.get_run_definition.return_value = json.dumps(rundef)
        storage.return_value = m
        r = Run(self.build, 'run0')
        r.trigger = 'github'
        r.status = BuildStatus.RUNNING
        db.session.add(r)
        db.session.commit()

        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-STATUS', 'PASSED'),
        ]
        self._post(self.urlbase + 'run0/', data, headers, 200)
        tests = [(x.name, x.status.name) for x in Test.query.all()]
        self.assertEqual([('default', 'FAILED'), ('TNAME', 'PASSED')], tests)
        results = [(x.name, x.status.name)
                   for x in Test.query.all()[0].results]
        expected = [('t1', 'PASSED'), ('t2', 'FAILED')]
        self.assertEqual(expected, results)

    @patch('jobserv.api.run.Storage')
    @patch('jobserv.api.run.notify_build_complete')
    def test_build_complete_email(self, build_complete, storage):
        m = Mock()
        m.get_project_definition.return_value = json.dumps({
            'timeout': 5,
            'triggers': [
                {
                    'name': 'github',
                    'type': 'github_pr',
                    'runs': [{
                        'name': 'run0',
                    }],
                    'email': {
                        'users': 'f@f.com',
                    }
                },
            ],
        })
        m.console_logfd.return_value = open('/dev/null', 'w')
        m.get_run_definition.return_value = json.dumps({})
        storage.return_value = m
        r = Run(self.build, 'run0')
        r.trigger = 'github'
        r.status = BuildStatus.RUNNING
        db.session.add(r)
        db.session.commit()

        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-STATUS', 'PASSED'),
        ]
        self._post(self.urlbase + 'run0/', None, headers, 200)

        db.session.refresh(r)
        self.assertEqual(self.build, build_complete.call_args_list[0][0][0])
        self.assertEqual('f@f.com', build_complete.call_args_list[0][0][1])

    @patch('jobserv.api.run.Storage')
    @patch('jobserv.api.run.notify_build_complete')
    def test_build_complete_email_skip(self, build_complete, storage):
        m = Mock()
        m.get_project_definition.return_value = json.dumps({
            'timeout': 5,
            'triggers': [
                {
                    'name': 'github',
                    'type': 'github_pr',
                    'runs': [{
                        'name': 'run0',
                    }],
                    'email': {
                        'users': 'f@f.com',
                        'only_failures': True,
                    }
                },
            ],
        })
        m.console_logfd.return_value = open('/dev/null', 'w')
        m.get_run_definition.return_value = json.dumps({})
        storage.return_value = m
        r = Run(self.build, 'run0')
        r.trigger = 'github'
        r.status = BuildStatus.RUNNING
        db.session.add(r)
        db.session.commit()

        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-STATUS', 'PASSED'),
        ]
        self._post(self.urlbase + 'run0/', None, headers, 200)

        db.session.refresh(r)
        self.assertEqual([], build_complete.call_args_list)

        # now fail the run and make sure we get notified
        r.status = BuildStatus.RUNNING
        db.session.commit()
        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-STATUS', 'FAILED'),
        ]
        self._post(self.urlbase + 'run0/', None, headers, 200)

        db.session.refresh(r)
        self.assertEqual(self.build, build_complete.call_args_list[0][0][0])

    # sqlite doesn't support with_for_update, so disable it by mocking "locked"
    @patch('jobserv.models.Build.locked')
    @patch('jobserv.api.run.Storage')
    def test_build_complete_triggers(self, storage, locked):
        m = Mock()
        m.get_project_definition.return_value = json.dumps({
            'timeout': 5,
            'triggers': [
                {
                    'name': 'github',
                    'type': 'github_pr',
                    'runs': [{
                        'name': 'run0',
                        'host-tag': 'foo*',
                    }],
                    'triggers': [
                        {'name': 'build-trigger'},
                    ]
                },
                {
                    'name': 'build-trigger',
                    'type': 'simple',
                    'runs': [{
                        'name': 'test',
                        'host-tag': 'foo*',
                        'container': 'container-foo',
                        'script': 'test',
                    }],
                },
            ],
            'scripts': {
                'test': '#test#',
            }
        })
        m.console_logfd.return_value = open('/dev/null', 'w')
        m.get_run_definition.return_value = json.dumps({})
        storage.return_value = m
        r = Run(self.build, 'run0')
        r.trigger = 'github'
        r.status = BuildStatus.RUNNING
        db.session.add(r)
        db.session.commit()

        headers = [
            ('Authorization', 'Token %s' % r.api_key),
            ('X-RUN-STATUS', 'PASSED'),
        ]
        self._post(self.urlbase + 'run0/', None, headers, 200)

        db.session.refresh(r)
        run = Run.query.all()[1]
        self.assertEqual('test', run.name)
        self.assertEqual('QUEUED', run.status.name)
