# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import io
import os
import signal
import subprocess
import traceback
import urllib.parse
import uuid

from jobserv_runner.cmd import stream_cmd
from jobserv_runner.jobserv import JobServApi
from jobserv_runner.logging import ContextLogger


class HandlerError(Exception):
    """An exception that can be used to tell SimpleHandler.main that the error
       has already been properly logged, and we just need to fail the run."""


class RunTimeoutError(HandlerError):
    pass


class JobServLogger(ContextLogger):
    def __init__(self, context, jobserv):
        super().__init__(context)
        self.jobserv = jobserv

    def __exit__(self, type, value, tb):
        if type == RunTimeoutError:
            return  # we handle logging of this properly

        super().__exit__(type, value, tb)
        self.jobserv.update_run(self.io.getvalue().encode(), retry=5)
        if tb:
            # flag this so we know the stack trace was printed
            value.handler_logged = True

    def exec(self, cmd_args, cwd=None):
        buf = self.io.getvalue()
        if buf:
            # send any buffer data to server
            self.jobserv.update_run(buf.encode())
            self.io = io.StringIO()

        def cb(buff):
            # dont stream this to local logs, just to server
            if self.jobserv.SIMULATED:
                # we are in simulator mode, dump to stdout
                return os.write(1, buff)
            return self.jobserv.update_run(buff)
        try:
            stream_cmd(cb, cmd_args, cwd)
            return True
        except subprocess.CalledProcessError as e:
            if e.output:
                if not self.jobserv.update_run(e.output, retry=8):
                    self.error('unable to update run output: %s', e.output)
            return False

    def _write(self, msg):
        if not self.jobserv.SIMULATED:
            return super()._write(msg)
        self.io.write(msg)


class SimpleHandler(object):
    """Executes the steps needed to do a "simple" trigger-type rundef"""

    def __init__(self, worker_dir, run_dir, jobserv, rundef):
        self.worker_dir = worker_dir
        self.run_dir = run_dir
        self.jobserv = jobserv
        self.rundef = rundef
        self.container_cwd = '/'

    def log_context(self, context):
        return JobServLogger(context, self.jobserv)

    def docker_pull(self):
        container = self.rundef['container']
        with self.log_context('Pulling container: ' + container) as log:
            if not log.exec(['docker', 'pull', container]):
                raise HandlerError('Unable pull container')

    def docker_run(self, mounts):
        env_file = os.path.join(self.run_dir, 'docker-env')
        envvars = (self.rundef.get('env') or {})

        with self.log_context('Setting up container environment') as log:
            env = ['%s=%s' % (k, v) for k, v in envvars.items()]
            log.info('Container environment variables:\n  %s',
                     '\n  '.join(env))
            with open(env_file, 'w') as f:
                # environment variable might have \n's so escape them to
                # make the docker-env file correct
                for val in env:
                    f.write(val.replace('\n', '\\n') + '\n')
        name = str(uuid.uuid4())
        with self.log_context('Running script inside container') as log:
            cmd = ['docker', 'run']
            cmd.extend(['--name', name])
            cmd.extend(['-w', self.container_cwd])
            cmd.extend(['--env-file', env_file])
            if self.rundef.get('privileged'):
                cmd.append('--privileged')
            cmd.extend(['-v%s:%s' % (host, cont) for host, cont in mounts])
            cmd.extend(
                [self.rundef['container'], self._container_command])
            try:
                return log.exec(cmd)
            except RunTimeoutError:
                log.error('Run has timed out, killing containter')
                log.exec(['docker', 'kill', name])
                raise

    def _prepare_secrets(self, log):
        """Create the /secrets folder that will be bind-mounted by docker."""
        secrets = os.path.join(self.run_dir, 'secrets')
        os.mkdir(secrets)
        for secret, value in (self.rundef.get('secrets') or {}).items():
            log.info('Creating secret: %s', secret)
            with open(os.path.join(secrets, secret), 'w') as f:
                f.write(value)
        return [(secrets, '/secrets')]

    def _prepare_volumes(self, log):
        """Create persistent volumes that will be bind-mounted by docker."""
        volumes = []
        for v, path in (self.rundef.get('persistent-volumes') or {}).items():
            p = os.path.join(
                self.worker_dir, 'volumes', self.rundef['project'], v)
            if not os.path.exists(p):
                os.makedirs(p)
            log.info('Creating volume: %s', p)
            volumes.append((p, path))

        return volumes

    def _prepare_netrc(self):
        """Create a netrc for certain secrets.
           This is handy for Android repo style builds that exist in a private
           repository. By setting this in .netrc everything just works.
        """
        # we only do githubtok now
        token = (self.rundef.get('secrets') or {}).get('githubtok')
        if token:
            with self.log_context('Creating container .netrc file') as log:
                log.info('Creating a github token entry')
                netrc = os.path.join(self.run_dir, '.netrc')
                with open(netrc, 'w') as f:
                    f.write('machine github.com\n')
                    f.write('login %s\n' % token)
                # Curl (used by git) doesn't look at the $NETRC environment
                # for overriding the .netrc location. We have to assume the
                # container's $HOME is /root
                return netrc, '/root/.netrc'

    def _clone_script_repo(self, log, repo, dst):
        url = repo['clone-url']
        log.info('Repo is: %s', url)
        token = repo.get('token')
        if token:
            token = self.rundef['secrets'][token]
            p = urllib.parse.urlsplit(url)
            url = p.scheme + '://' + token + '@' + p.netloc + p.path

        if not log.exec(['git', 'clone', url, dst]):
            raise HandlerError('Unable to clone repo: ' + repo['clone-url'])

        ref = repo.get('git-ref')
        if ref:
            log.info('Git reference is: %s', ref)
            if not log.exec(['git', 'checkout', ref], cwd=dst):
                raise HandlerError('Unable to checkout: ' + ref)
        else:
            sha = subprocess.check_output(
                ['git', 'log', '--format=%H', '-1'], cwd=dst)
            log.info('Git HEAD reference is: %s', sha.strip().decode())

        if not os.path.exists(os.path.join(dst, repo['path'])):
            raise HandlerError('Script not found in repo: ' + repo['path'])
        self._container_command = '/script-repo/' + repo['path']

    def create_script(self, log):
        """Create the script that will run in the container."""
        repo = self.rundef.get('script-repo')
        script_dir = os.path.join(self.run_dir, 'script-repo')
        if repo:
            self._clone_script_repo(log, repo, script_dir)
        else:
            os.mkdir(script_dir)
            script = os.path.join(script_dir, 'do_run')
            with open(script, 'w') as f:
                f.write(self.rundef['script'])
                os.fchmod(f.fileno(), 0o555)
            self._container_command = '/script-repo/do_run'
        return script_dir, '/script-repo'

    def log_simulator_instructions(self):
        with self.log_context('Steps to recreate inside simulator') as log:
            msg = '''
    mkdir /tmp/sim-run
    cd /tmp/sim-run
    wget -O runner {runner}
    wget -O rundef.json {run}.rundef.json
    # open rundef.json and update values for secrets
    PYTHONPATH=./runner \
        python3 -m jobserv_runner.simulator -w `pwd` rundef.json

'''.format(run=self.rundef['run_url'], runner=self.rundef['runner_url'])
            log._write(msg)

    def prepare_mounts(self):
        """Prepare the directories we will bind mount by docker."""
        with self.log_context('Preparing bind mounts') as log:
            mounts = self._prepare_secrets(log) + self._prepare_volumes(log)
        netrc = self._prepare_netrc()
        if netrc:
            mounts.append(netrc)
        with self.log_context('Preparing script') as log:
            mounts.append(self.create_script(log))
        archive = os.path.join(self.run_dir, 'archive')
        os.mkdir(archive)
        mounts.append((archive, '/archive'))
        return mounts

    def _on_alarm(self, signum, frame):
        raise RunTimeoutError('Run timed out')

    def start_timer(self):
        """Set an alarm to enforce the job timeout."""
        signal.signal(signal.SIGALRM, self._on_alarm)
        signal.alarm(self.rundef['timeout'] * 60)

    def upload_artifacts(self):
        self.jobserv.update_status('UPLOADING', 'Finding artifacts to upload')
        archive = os.path.join(self.run_dir, 'archive')
        total_size = 0
        uploads = []
        for root, dirs, files in os.walk(archive):
            rel = root[len(archive) + 1:]
            for f in files:
                if f:
                    f = os.path.join(rel, f)
                    size = os.stat(os.path.join(archive, f)).st_size
                    uploads.append({'file': f, 'size': size})
                    total_size += size

        msg = 'Uploading %d items %d bytes\n' % (len(uploads), total_size)
        self.jobserv.update_run(msg.encode())
        if uploads:
            errors = self.jobserv.upload(archive, uploads)
            if errors:
                self.jobserv.update_run(('\n'.join(errors)).encode())
                return False
        return True

    @classmethod
    def get_jobserv(clazz, rundef):
        if rundef.get('simulator'):
            JobServApi.SIMULATED = True
            rundef['run_url'] = 'http://simulated/'
            rundef['api_key'] = 'simulated'
        return JobServApi(rundef['run_url'], rundef['api_key'])

    @classmethod
    def execute(clazz, worker_dir, run_dir, rundef):
        jobserv = clazz.get_jobserv(rundef)
        try:
            h = clazz(worker_dir, run_dir, jobserv, rundef)
            if not JobServApi.SIMULATED:
                h.log_simulator_instructions()
            h.docker_pull()
            h.start_timer()
            mounts = h.prepare_mounts()

            last_status = 'FAILED'
            msg = 'Script completed with error(s)\n'
            if h.docker_run(mounts):
                last_status = 'PASSED'
                msg = 'Script completed\n'
            h.jobserv.update_run(msg.encode())

            if not h.upload_artifacts():
                last_status = 'FAILED'
            jobserv.update_status(last_status, 'Runner has completed')
            return True
        except HandlerError as e:
            jobserv.update_status('FAILED', str(e))
        except Exception as e:
            if getattr(e, 'handler_logged', False):
                # we've already logged the stack trace, just fail the run
                jobserv.update_status('FAILED', str(e))
            else:
                stack = traceback.format_exc()
                print('Unexpected Runner Error:\n' + stack)
                try:
                    jobserv.update_status(
                        'FAILED', 'Unexpected error: ' + stack)
                except:
                    stack = traceback.format_exc()
                    print('Unable to fail job:\n' + stack)
        return False


handler = SimpleHandler
