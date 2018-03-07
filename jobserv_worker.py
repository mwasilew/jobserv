#!/usr/bin/python3
# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import argparse
import datetime
import fcntl
import hashlib
import importlib
import json
import logging
import os
import platform
import random
import shutil
import string
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse

from configparser import ConfigParser
from multiprocessing import cpu_count

import requests

script = os.path.abspath(__file__)
config_file = os.path.join(os.path.dirname(script), 'settings.conf')
config = ConfigParser()
config.read([config_file])

logging.basicConfig(
    level=getattr(logging,
                  config.get('jobserv', 'log_level', fallback='INFO')))
log = logging.getLogger('jobserv-worker')
logging.getLogger('requests').setLevel(logging.WARNING)


def _create_conf(server_url, hostname, concurrent_runs, host_tags, surges):
    with open(script, 'rb') as f:
        h = hashlib.md5()
        h.update(f.read())
        version = h.hexdigest()

    config.add_section('jobserv')
    config['jobserv']['server_url'] = server_url
    config['jobserv']['version'] = version
    config['jobserv']['log_level'] = 'INFO'
    config['jobserv']['concurrent_runs'] = str(concurrent_runs)
    config['jobserv']['host_tags'] = host_tags
    config['jobserv']['surges_only'] = str(int(surges))
    chars = string.ascii_letters + string.digits + '!@#$^&*~'
    config['jobserv']['host_api_key'] =\
        ''.join(random.choice(chars) for _ in range(32))
    if not hostname:
        with open('/etc/hostname') as f:
            hostname = f.read().strip()
    config['jobserv']['hostname'] = hostname
    with open(config_file, 'w') as f:
        config.write(f, True)


class HostProps(object):
    CACHE = os.path.join(os.path.dirname(script), 'hostprops.cache')

    def __init__(self):
        mem = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
        surges = int(config.get('jobserv', 'surges_only', fallback='0'))
        self.data = {
            'cpu_total': cpu_count(),
            'cpu_type': platform.processor() or platform.machine(),
            'mem_total': mem,
            'distro': self._get_distro(),
            'api_key': config['jobserv']['host_api_key'],
            'name': config['jobserv']['hostname'],
            'concurrent_runs': int(config['jobserv']['concurrent_runs']),
            'host_tags': config['jobserv']['host_tags'],
            'surges_only': surges != 0,
        }

    def _get_distro(self):
        with open('/etc/os-release') as f:
            for line in f:
                if line.startswith('PRETTY_NAME'):
                    return line.split('=')[1].strip().replace('"', '')
        return '?'

    def cache(self):
        with open(self.CACHE, 'w') as f:
            json.dump(self.data, f)

    def update_if_needed(self, server):
        try:
            with open(self.CACHE) as f:
                cached = json.load(f)
        except Exception:
            cached = {}
        if cached != self.data:
            log.info('updating host properies on server: %s', self.data)
            server.update_host(self.data)
            self.cache()

    @staticmethod
    def get_available_space(path):
        st = os.statvfs(path)
        return st.f_frsize * st.f_bavail  # usable space in bytes

    @staticmethod
    def get_available_memory():
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemFree:'):
                    return int(line.split()[1]) * 1024  # available in bytes
        raise RuntimeError('Unable to find "MemFree" in /proc/meminfo')

    @staticmethod
    def get_available_runners():
        '''Return the number of available runners we have.
           An array of flocked file descriptors will be returned. The run will
           stay locked until the fds are closed
        '''
        locksdir = os.path.dirname(script)
        avail = []
        for x in range(int(config['jobserv']['concurrent_runs'])):
            x = open(os.path.join(locksdir, '.run-lock-%d' % x), 'a')
            try:
                fcntl.flock(x, fcntl.LOCK_EX | fcntl.LOCK_NB)
                avail.append(x)
            except BlockingIOError:
                pass
        return avail


class JobServ(object):
    def __init__(self):
        self.requests = requests

    def _auth_headers(self):
        return {
            'content-type': 'application/json',
            'Authorization': 'Token ' + config['jobserv']['host_api_key'],
        }

    def _get(self, resource, params=None):
        url = urllib.parse.urljoin(config['jobserv']['server_url'], resource)
        r = self.requests.get(url, params=params, headers=self._auth_headers())
        if r.status_code != 200:
            log.error('Failed to issue request: %s\n' % r.text)
            sys.exit(1)
        return r

    def _post(self, resource, data, use_auth_headers=False):
        headers = None
        if use_auth_headers:
            headers = self._auth_headers()
        url = urllib.parse.urljoin(config['jobserv']['server_url'], resource)
        r = self.requests.post(url, json=data, headers=headers)
        if r.status_code != 201:
            log.error('Failed to issue request: %s\n' % r.text)
            sys.exit(1)

    def _patch(self, resource, data):
        url = urllib.parse.urljoin(config['jobserv']['server_url'], resource)
        r = self.requests.patch(url, json=data, headers=self._auth_headers())
        if r.status_code != 200:
            log.error('Failed to issue request: %s\n' % r.text)
            sys.exit(1)

    def _delete(self, resource):
        url = urllib.parse.urljoin(config['jobserv']['server_url'], resource)
        r = self.requests.delete(url, headers=self._auth_headers())
        if r.status_code != 200:
            log.error('Failed to issue request: %s\n' % r.text)
            sys.exit(1)

    def create_host(self, hostprops):
        self._post('/workers/%s/' % config['jobserv']['hostname'], hostprops)

    def update_host(self, hostprops):
        self._patch('/workers/%s/' % config['jobserv']['hostname'], hostprops)

    def delete_host(self):
        self._delete('/workers/%s/' % config['jobserv']['hostname'])

    def check_in(self):
        flocks = HostProps.get_available_runners()
        load_avg_1, load_avg_5, load_avg_15 = os.getloadavg()
        params = {
            'available_runners': len(flocks),
            'mem_free': HostProps.get_available_memory(),
            # /var/lib is what should hold docker images and will be the most
            # important measure of free disk space for us over time
            'disk_free': HostProps.get_available_space('/var/lib'),
            'load_avg_1': load_avg_1,
            'load_avg_5': load_avg_5,
            'load_avg_15': load_avg_15,
        }
        data = self._get(
            '/workers/%s/' % config['jobserv']['hostname'], params).json()
        return data, flocks

    def get_worker_script(self):
        return self._get('/worker').text

    def update_run(self, rundef, status, msg):
        msg = ('== %s: %s\n' % (datetime.datetime.utcnow(), msg)).encode()
        headers = {
            'content-type': 'text/plain',
            'Authorization': 'Token ' + rundef['api_key'],
            'X-RUN-STATUS': status,
        }
        for i in range(8):
            if i:
                log.info('Failed to update run, sleeping and retrying')
                time.sleep(2 * i)
            r = self.requests.post(
                rundef['run_url'], data=msg, headers=headers)
            if r.status_code == 200:
                break
        else:
            log.error('Unable to update run: %d: %s', r.status_code, r.text)


def _create_systemd_service():
    svc = '''
[Unit]
Description=JobServ Worker
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={working_dir}
ExecStart={command}
Restart=on-failure

[Install]
WantedBy=multi-user.target
'''
    svc = svc.format(
        user=os.environ['USER'],
        working_dir=os.path.dirname(os.path.abspath(script)),
        command=os.path.abspath(script) + ' loop',
    )
    svc_file = os.path.join(os.path.dirname(script), 'jobserv.service')
    with open(svc_file, 'w') as f:
        f.write(svc)


def cmd_register(args):
    '''Register this host with the configured JobServ server'''
    _create_conf(args.server_url, args.hostname, args.concurrent_runs,
                 args.host_tags, args.surges_only)
    _create_systemd_service()
    p = HostProps()
    args.server.create_host(p.data)
    p.cache()
    print('''
A SystemD service can be enabled with:
  sudo cp jobserv.service /etc/systemd/system/
  sudo systemctl enable jobserv
  sudo systemctl start jobserv

You also need to add a sudo entry to allow the worker to clean up root owned
files from CI runs:

 echo "$USER ALL=(ALL) NOPASSWD:/bin/rm" | sudo tee /etc/sudoers.d/jobserv
''')


def cmd_uninstall(args):
    '''Remove worker installation'''
    args.server.delete_host()
    shutil.rmtree(os.path.dirname(script))


def _upgrade_worker(args, version):
    buf = args.server.get_worker_script()
    with open(__file__, 'wb') as f:
        f.write(buf.encode())
        f.flush()
    config['jobserv']['version'] = version
    with open(config_file, 'w') as f:
        config.write(f, True)


def _download_runner(url, rundir, retries=3):
    for i in range(1, retries + 1):
        r = requests.get(url, stream=True)
        if r.status_code == 200:
            runner = os.path.join(rundir, 'runner.whl')
            with open(runner, 'wb') as f:
                for chunk in r.iter_content(4096):
                    f.write(chunk)
            return runner
        else:
            if i == retries:
                raise RuntimeError('Unable to download runner(%s): %d %s' % (
                    url, r.status_code, r.text))
            log.error('Error getting runner: %d %s', r.status_code, r.text)
            time.sleep(i * 2)


def _delete_rundir(rundir):
    try:
        shutil.rmtree(rundir)
    except PermissionError:
        log.info(
            'Unable to cleanup Run as normal user, trying sudo rm -rf')
        subprocess.check_call(['sudo', '/bin/rm', '-rf', rundir])
    except:
        log.exception('Unable to delete Run\'s directory: ' + rundir)
        sys.exit(1)


def _handle_reboot(rundir, rundef):
    log.warn('RebootAndContinue requested by %s', rundef['run_url'])
    reboot_run = os.path.join(os.path.dirname(script), 'rebooted-run')
    if os.path.exists(reboot_run):
        log.error('Reboot run directory(%s) exists, deleting', reboot_run)
        shutil.rmtree(reboot_run)

    os.rename(rundir, reboot_run)
    os.execv('/usr/bin/reboot', ['/usr/bin/reboot'])


def _handle_run(jobserv, rundef, rundir=None):
    runsdir = os.path.join(os.path.dirname(script), 'runs')
    try:
        jobserv.update_run(rundef, 'RUNNING', 'Setting up runner on worker')
        if not os.path.exists(runsdir):
            os.mkdir(runsdir)
        if not rundir:
            rundir = tempfile.mkdtemp(dir=runsdir)
        sys.path.insert(0, _download_runner(rundef['runner_url'], rundir))
        m = importlib.import_module(
            'jobserv_runner.handlers.' + rundef['trigger_type'])
        if os.fork() == 0:
            try:
                m.handler.execute(os.path.dirname(script), rundir, rundef)
            except m.handler.RebootAndContinue as e:
                _handle_reboot(rundir, rundef)
            _delete_rundir(rundir)
    except SystemExit:
        raise
    except Exception:
        stack = traceback.format_exc().strip().replace('\n', '\n | ')
        msg = 'Unexpected runner error:\n | ' + stack
        log.error(msg)
        jobserv.update_run(rundef, 'FAILED', msg)


def _handle_rebooted_run(jobserv):
    reboot_run = os.path.join(os.path.dirname(script), 'rebooted-run')
    if os.path.exists(reboot_run):
        log.warn('Found rebooted-run, preparing to execute')

        rundir = os.path.join(os.path.dirname(script), 'runs/rebooted-run')
        os.rename(reboot_run, rundir)

        flocks = HostProps.get_available_runners()
        with open(os.path.join(rundir, 'rundef.json')) as f:
            rundef = json.load(f)
        rundef['flock'] = flocks.pop()
        jobserv.update_run(rundef, 'RUNNING', 'Resuming rebooted run')
        _handle_run(jobserv, rundef, rundir)
        return True


def cmd_check(args):
    '''Check in with server for work'''
    if _handle_rebooted_run(args.server):
        return

    HostProps().update_if_needed(args.server)
    data, flocks = args.server.check_in()
    for rundef in data['data']['worker'].get('run-defs', []):
        rundef = json.loads(rundef)
        # by placing the flock in the rundef, it will stay locked after
        # the runner forks since the open file will be referenced
        rundef['flock'] = flocks.pop()
        rundef['env']['H_WORKER'] = config['jobserv']['hostname']
        log.info('executing run: %s', rundef.get('run_url'))
        _handle_run(args.server, rundef)
    ver = data['data']['worker']['version']
    if ver != config['jobserv']['version']:
        log.warning('Upgrading client to: %s', ver)
        _upgrade_worker(args, ver)


def _docker_clean():
    try:
        containers = subprocess.check_output(
            ['docker', 'ps', '--filter', 'status=exited', '-q'])
        containers = containers.decode().splitlines()
        if not containers:
            return  # nothing to clean up
        cmd = ['docker', 'inspect', '--format', '{{.State.FinishedAt}}']
        times = subprocess.check_output(cmd + containers).decode().splitlines()

        now = datetime.datetime.now()
        deletes = []
        for i, ts in enumerate(times):
            # Times are like: 2017-09-19T21:17:33.465435028Z
            # Strip of the nanoseconds and parse the date
            ts = ts.split('.')[0]
            ts = datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S')
            if (now - ts).total_seconds() > 7200:  # 2 hours old
                deletes.append(containers[i])
        if deletes:
            log.info('Cleaning up old containers:\n  %s', '\n  '.join(deletes))
            subprocess.check_output(['docker', 'rm', '-v'] + deletes)
    except subprocess.CalledProcessError as e:
        log.exception(e)


def cmd_loop(args):
    # Ensure no other copy of this script is running
    cmd_args = [sys.argv[0], 'check']
    with open('/tmp/jobserv_worker.lock', 'w+') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            next_clean = time.time() + (args.docker_rm * 3600)
            while True:
                log.debug('Calling check')
                rc = subprocess.call(cmd_args)
                if rc:
                    log.error('Last call exited with rc: %d', rc)
                if time.time() > next_clean:
                    log.info('Running docker container cleanup')
                    _docker_clean()
                    next_clean = time.time() + (args.docker_rm * 3600)
                else:
                    time.sleep(args.every)
        except IOError:
            sys.exit('Script is already running')
        except KeyboardInterrupt:
            log.info('Keyboard interrupt received, exiting')
            return


def cmd_cronwrap(args):
    logfile = os.path.basename(args.script) + '.log'
    logfile = '/tmp/cronwrap-' + logfile
    data = {
        'title': 'JobServ CronWrap - ' + args.script,
        'msg': '',
        'type': 'info',
    }
    try:
        with open(logfile, 'wb') as f:
            start = time.time()
            subprocess.check_call([args.script], stdout=f, stderr=f)
            data['msg'] = 'Completed in %d seconds' % (time.time() - start)
    except Exception:
        data['type'] = 'error'
        data['msg'] = 'Check %s for error message' % logfile
        with open(logfile) as f:
            data['msg'] += '\nFirst 1024 bytes of log:\n %s' % f.read(1024)
        sys.exit('Failed to run cronwrap: ' + args.script)
    finally:
        resource = '/workers/%s/events/' % config['jobserv']['hostname']
        JobServ()._post(resource, data, True)


def main(args):
    if getattr(args, 'func', None):
        log.debug('running: %s', args.func.__name__)
        args.func(args)


def get_args(args=None):
    parser = argparse.ArgumentParser('Worker API to JobServ server')
    sub = parser.add_subparsers(help='sub-command help')

    p = sub.add_parser('register', help='Register this host with the server')
    p.set_defaults(func=cmd_register)
    p.add_argument('--hostname',
                   help='''Worker name to register. If none is provided, the
                        value of /etc/hostname will be used.''')
    p.add_argument('--concurrent-runs', type=int, default=1,
                   help='Maximum number of current runs. Default=%(default)d')
    p.add_argument('--surges-only', action='store_true',
                   help='''Only use this worker when a surge of Runs has been
                        has been queued on the server''')
    p.add_argument('server_url')
    p.add_argument('host_tags', help='Comma separated list')

    p = sub.add_parser('uninstall', help='Uninstall the client')
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser('check', help='Check in with server for updates')
    p.set_defaults(func=cmd_check)

    p = sub.add_parser('loop', help='Run the "check" command in a loop')
    p.set_defaults(func=cmd_loop)
    interval = 20
    if int(config.get('jobserv', 'surges_only', fallback='0')):
        interval = 90
    p.add_argument('--every', type=int, default=interval, metavar='interval',
                   help='Seconds to sleep between runs. default=%(default)d')
    p.add_argument('--docker-rm', type=int, default=8, metavar='interval',
                   help='''Interval in hours to run to run "dock rm" on
                        containers that have exited. default is every
                        %(default)d hours''')

    p = sub.add_parser('cronwrap',
                       help='''Run a command and report back to the jobserv
                            if it passed or not''')
    p.add_argument('script', help='Program to run')
    p.set_defaults(func=cmd_cronwrap)

    args = parser.parse_args(args)
    args.server = JobServ()
    return args


if __name__ == '__main__':
    main(get_args())
