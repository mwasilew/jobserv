# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

from unittest.mock import patch

import yaml

from tests import JobServTest

from jobserv import lava_reactor


class LavaReactorTest(JobServTest):
    @patch('jobserv.lava_reactor.ServerProxy')
    def test_check_run_norun(self, ServerProxy):
        ServerProxy().scheduler.job_status.side_effect = RuntimeError('foo')
        test = {'context': 'http://foo/12'}
        metadata = {'lava_user': 'foo', 'lava_token': 'bar'}
        self.assertFalse(lava_reactor._check_run(test, metadata))

    @patch('jobserv.lava_reactor.ServerProxy')
    def test_check_run_running(self, ServerProxy):
        ServerProxy().scheduler.job_status.return_value = {'job_status': 'n/a'}
        test = {'context': 'http://foo/12'}
        metadata = {'lava_user': 'foo', 'lava_token': 'bar'}
        self.assertFalse(lava_reactor._check_run(test, metadata))

    @patch('jobserv.lava_reactor._update_status')
    @patch('jobserv.lava_reactor.ServerProxy')
    def test_check_run_incomplete(self, ServerProxy, update_status):
        # You can have a job where the tests pass, but the job is incomplete
        # due to a timeout. Make sure this is marked as a failure
        ServerProxy().scheduler.job_status.return_value = {
            'job_status': 'Incomplete',
        }
        tests = [
            {'name': 't1', 'suite': 's1', 'result': 'pass', 'url': 'foo'},
        ]
        ServerProxy().results.get_testjob_results_yaml.return_value =\
            yaml.dump(tests)
        test = {
            'url': 'test-url',
            'context': 'http://foo/12',
        }
        metadata = {'lava_user': 'foo', 'lava_token': 'bar'}
        self.assertTrue(lava_reactor._check_run(test, metadata))
        self.assertEqual(1, len(update_status.call_args_list))
        self.assertEqual('FAILED', update_status.call_args_list[0][0][1])

    @patch('jobserv.lava_reactor._update_status')
    @patch('jobserv.lava_reactor.ServerProxy')
    def test_check_run_fail(self, ServerProxy, update_status):
        ServerProxy().scheduler.job_status.return_value = {
            'job_status': 'Complete',
        }
        tests = [
            {'name': 't1', 'suite': 's1', 'result': 'fail', 'url': 'foo'},
        ]
        ServerProxy().results.get_testjob_results_yaml.return_value =\
            yaml.dump(tests)
        test = {
            'url': 'test-url',
            'context': 'http://foo/12',
        }
        metadata = {'lava_user': 'foo', 'lava_token': 'bar'}
        self.assertTrue(lava_reactor._check_run(test, metadata))
        self.assertEqual(1, len(update_status.call_args_list))
        self.assertEqual('FAILED', update_status.call_args_list[0][0][1])

    @patch('jobserv.lava_reactor._update_status')
    @patch('jobserv.lava_reactor.ServerProxy')
    def test_check_run_pass(self, ServerProxy, update_status):
        ServerProxy().scheduler.job_status.return_value = {
            'job_status': 'Complete',
        }
        tests = [
            {'name': 't1', 'suite': 's1', 'result': 'pass', 'url': 'foo'},
        ]
        ServerProxy().results.get_testjob_results_yaml.return_value =\
            yaml.dump(tests)
        test = {
            'url': 'test-url',
            'context': 'http://foo/12',
        }
        metadata = {'lava_user': 'foo', 'lava_token': 'bar'}
        self.assertTrue(lava_reactor._check_run(test, metadata))
        self.assertEqual(1, len(update_status.call_args_list))
        self.assertEqual('PASSED', update_status.call_args_list[0][0][1])
