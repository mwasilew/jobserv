# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

from jobserv import git_poller

from unittest import TestCase, mock


class TestGitPoller(TestCase):
    def setUp(self):
        super().setUp()

    @mock.patch('jobserv.git_poller.permissions')
    def test_get_project_triggers(self, perms):
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {
            'data': [
                {
                    'id': 12,
                    'type': 'git_poller',
                    'project': 'foo',
                    'user': 'root',
                    'queue_priority': 1,
                },
            ]
        }
        perms.internal_get.return_value = resp
        project_triggers = git_poller._get_project_triggers().values()
        self.assertEqual([(12, 'foo')],
                         [(x.id, x.project) for x in project_triggers])

    @mock.patch('jobserv.git_poller._get_project_triggers')
    @mock.patch('jobserv.git_poller.Storage')
    def test_poll_remove(self, storage, get_project_triggers):
        get_project_triggers.return_value = {}

        project_triggers = {}
        git_poller._poll(project_triggers)
        self.assertEqual({}, project_triggers)

    @mock.patch('jobserv.git_poller._get_projdef')
    @mock.patch('jobserv.git_poller._get_project_triggers')
    @mock.patch('jobserv.git_poller.Storage')
    def test_poll_add(self, storage, get_project_triggers, get_projdef):
        get_project_triggers.return_value = {
            'foo': git_poller.ProjectTrigger(12, 't', 'proj', 'user', 1),
        }
        get_projdef.return_value = None  # prevents trying to really poll

        project_triggers = {}
        git_poller._poll(project_triggers)
        self.assertEqual(['foo'], list(project_triggers.keys()))

    @mock.patch('jobserv.git_poller._get_projdef')
    @mock.patch('jobserv.git_poller._get_project_triggers')
    @mock.patch('jobserv.git_poller.Storage')
    def test_poll_updated(self, storage, get_project_triggers, get_projdef):
        project_triggers = {
            'foo': git_poller.PollerEntry(
                git_poller.ProjectTrigger(12, 't', 'proj', 'user', 1)),
        }
        get_project_triggers.return_value = {
            'foo': git_poller.ProjectTrigger(12, 't', 'proj', 'user', 0, 'r'),
        }
        get_projdef.return_value = None  # prevents trying to really poll

        git_poller._poll(project_triggers)
        self.assertEqual(0, project_triggers['foo'].trigger.queue_priority)
        self.assertEqual('r', project_triggers['foo'].trigger.definition_repo)

    @mock.patch('jobserv.git_poller.requests')
    def test_get_refs(self, requests):
        requests.get().status_code = 200
        requests.get().text = '''ignore
ignore
004015f12d4181355604efa7b429fc3bcbae08d27f40 refs/heads/master
004015f12d4181355604efa7b429fc3bcbae08d27f41 refs/pulls/123
'''
        trigger = git_poller.ProjectTrigger(
            id=1, type='t', project='p', user='u', queue_priority=1)
        vals = []
        for sha, ref in git_poller._get_refs('doesnot matter', trigger):
            vals.append((sha, ref))
        expected = [
            ('15f12d4181355604efa7b429fc3bcbae08d27f40', 'refs/heads/master'),
            ('15f12d4181355604efa7b429fc3bcbae08d27f41', 'refs/pulls/123'),
        ]
        self.assertEqual(expected, vals)

    @mock.patch('jobserv.git_poller.requests')
    def test_get_refs_fatal(self, requests):
        requests.get().status_code = 500
        requests.get().text = 'foobar'
        trigger = git_poller.ProjectTrigger(
            id=1, type='t', project='p', user='u', queue_priority=1)
        vals = []
        for sha, ref in git_poller._get_refs('doesnot matter', trigger):
            vals.append((sha, ref))
        self.assertEqual([], vals)

    @mock.patch('jobserv.git_poller._get_refs')
    def test_repo_changes_first_run(self, get_refs):
        trigger = git_poller.ProjectTrigger(
            id=1, type='t', project='p', user='u', queue_priority=1)
        refs = ['ref1']
        get_refs.return_value = [
            ('sha1', 'ref1'),
            ('sha2', 'ref2'),
        ]
        cache = {}
        change_params = git_poller._get_repo_changes(
            cache, 'url1', refs, trigger)
        self.assertEqual([], list(change_params))
        self.assertEqual({'url1': {'ref1': 'sha1'}}, cache)

        refs = ['refs1', 'ref2']
        change_params = git_poller._get_repo_changes(
            cache, 'url1', refs, trigger)
        self.assertEqual([], list(change_params))
        self.assertEqual({'url1': {'ref1': 'sha1', 'ref2': 'sha2'}}, cache)

    @mock.patch('jobserv.git_poller._get_refs')
    def test_repo_changes_changed(self, get_refs):
        trigger = git_poller.ProjectTrigger(
            id=1, type='t', project='p', user='u', queue_priority=1)
        refs = ['ref1', 'ref2']
        get_refs.return_value = [
            ('sha1', 'ref1'),
            ('sha2', 'ref2'),
        ]
        cache = {'url1': {'ref1': 'oldsha', 'ref2': 'sha2'}}
        change_params = git_poller._get_repo_changes(
            cache, 'url1', refs, trigger)
        expected = [{
            'GIT_URL': 'url1',
            'GIT_OLD_SHA': 'oldsha',
            'GIT_SHA': 'sha1',
            'GIT_REF': 'ref1',
        }]
        self.assertEqual(expected, list(change_params))
