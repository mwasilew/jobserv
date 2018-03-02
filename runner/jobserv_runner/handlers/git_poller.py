# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import os
import urllib.parse

from jobserv_runner.handlers.simple import HandlerError, SimpleHandler


class GitPoller(SimpleHandler):
    def _get_http_clone_token(self, clone_url):
        secrets = self.rundef.get('secrets', {})
        if clone_url.startswith('https://github.com'):
            tok = secrets.get('githubtok')
            if tok:
                return tok

        # we can't determine by URL if its a gitlab repo, so just assume
        # the rundef/secrets are done sanely by the user
        env = self.rundef['env']
        user = env.get('gitlabuser') or secrets.get('gitlabuser')
        if user:
            token = self.rundef['secrets']['gitlabtok']
            return user + ':' + token

    def _clone(self, log, dst):
        clone_url = self.rundef['env']['GIT_URL']
        log.info('Clone_url: %s', clone_url)

        token = self._get_http_clone_token(clone_url)
        if token:
            log.info('Using an HTTP token for cloning')
            p = urllib.parse.urlsplit(clone_url)
            clone_url = p.scheme + '://' + token + '@' + p.netloc + p.path

        if not log.exec(['git', 'clone', clone_url, dst]):
            raise HandlerError(
                'Unable to clone: ' + self.rundef['env']['GIT_URL'])

        sha = self.rundef['env'].get('GIT_SHA')
        if sha:
            log.info('Checking out: %s', sha)
            if not log.exec(['git', 'branch', 'jobserv-run', sha], cwd=dst):
                raise HandlerError('Unable to branch: ' + sha)
            if not log.exec(['git', 'checkout', 'jobserv-run'], cwd=dst):
                raise HandlerError('Unable to checkout: ' + sha)

    def prepare_mounts(self):
        mounts = super().prepare_mounts()

        repo_dir = os.path.join(self.run_dir, 'repo')
        with self.log_context('Cloning git repository') as log:
            if os.path.exists(repo_dir):
                log.warn('Reusing repository from previous run')
            else:
                self._clone(log, repo_dir)
        mounts.append((repo_dir, '/repo'))
        self.container_cwd = '/repo'
        return mounts


handler = GitPoller
