from jobserv import git_poller

from unittest import TestCase, mock


class TestGitPoller(TestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, git_poller, '_projects', {})

    @mock.patch('jobserv.git_poller._get_projects')
    @mock.patch('jobserv.git_poller.Storage')
    def test_poll_remove(self, storage, get_projects):
        git_poller._projects = {'foo': 'doesnt matter for this test'}
        get_projects.return_value = {}

        git_poller._poll()
        self.assertEqual({}, git_poller._projects)

    @mock.patch('jobserv.git_poller._get_projdef')
    @mock.patch('jobserv.git_poller._get_projects')
    @mock.patch('jobserv.git_poller.Storage')
    def test_poll_add(self, storage, get_projects, get_projdef):
        git_poller._projects = {}
        get_projects.return_value = {
            'foo': {'url': 'does not matter for this test'},
        }
        get_projdef.return_value = None  # prevents trying to really poll

        git_poller._poll()
        self.assertEqual(['foo'], list(git_poller._projects.keys()))

    @mock.patch('jobserv.git_poller._get_projdef')
    @mock.patch('jobserv.git_poller._get_projects')
    @mock.patch('jobserv.git_poller.Storage')
    def test_poll_updated(self, storage, get_projects, get_projdef):
        git_poller._projects = {
            'foo': {
                'poller_def': {'url': 'oldval'},
            }
        }
        get_projects.return_value = {
            'foo': {'url': 'newval'}
        }
        get_projdef.return_value = None  # prevents trying to really poll

        git_poller._poll()
        self.assertEqual(
            'newval', git_poller._projects['foo']['poller_def']['url'])

    @mock.patch('jobserv.git_poller.requests')
    def test_get_refs(self, requests):
        requests.get().status_code = 200
        requests.get().text = '''ignore
ignore
004015f12d4181355604efa7b429fc3bcbae08d27f40 refs/heads/master
004015f12d4181355604efa7b429fc3bcbae08d27f41 refs/pulls/123
'''
        proj = {'poller_def': {}}
        vals = []
        for sha, ref in git_poller._get_refs('doesnot matter', proj):
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
        proj = {'poller_def': {}}
        vals = []
        for sha, ref in git_poller._get_refs('doesnot matter', proj):
            vals.append((sha, ref))
        self.assertEqual([], vals)

    @mock.patch('jobserv.git_poller._get_refs')
    def test_repo_changes_first_run(self, get_refs):
        proj = {'poller_def': {}}
        refs = ['ref1']
        get_refs.return_value = [
            ('sha1', 'ref1'),
            ('sha2', 'ref2'),
        ]
        cache = {}
        change_params = git_poller._get_repo_changes(cache, 'url1', refs, proj)
        self.assertEqual([], list(change_params))
        self.assertEqual({'url1': {'ref1': 'sha1'}}, cache)

        refs = ['refs1', 'ref2']
        change_params = git_poller._get_repo_changes(cache, 'url1', refs, proj)
        self.assertEqual([], list(change_params))
        self.assertEqual({'url1': {'ref1': 'sha1', 'ref2': 'sha2'}}, cache)

    @mock.patch('jobserv.git_poller._get_refs')
    def test_repo_changes_changed(self, get_refs):
        proj = {'poller_def': {}}
        refs = ['ref1', 'ref2']
        get_refs.return_value = [
            ('sha1', 'ref1'),
            ('sha2', 'ref2'),
        ]
        cache = {'url1': {'ref1': 'oldsha', 'ref2': 'sha2'}}
        change_params = git_poller._get_repo_changes(cache, 'url1', refs, proj)
        expected = [{
            'GIT_URL': 'url1',
            'GIT_OLD_SHA': 'oldsha',
            'GIT_SHA': 'sha1',
            'GIT_REF': 'ref1',
        }]
        self.assertEqual(expected, list(change_params))
