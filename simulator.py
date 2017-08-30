#!/usr/bin/env python3
# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import argparse
import hashlib
import json
import os
import re
import sys

import requests
import yaml


def _validate(args):
    url = args.jobserv
    if url[-1] != '/':
        url += '/'
    url += 'simulator-validate'
    data = yaml.load(args.proj_def)
    r = requests.post(url, json=data)
    if r.status_code != 200:
        try:
            sys.exit(r.json()['message'])
        except:
            sys.exit(r.text)
    return data


def _get_trigger(projdef, trigger_name):
    for trigger in projdef.get('triggers', []):
        if trigger.get('name') == trigger_name:
            return trigger
    sys.exit('No trigger named %s was found' % trigger_name)


def _get_run(trigger, run_name):
    for r in trigger.get('runs', []):
        if r.get('name') == run_name:
            return r
        if 'loop-on' in r:
            pat = r['name'].replace('{loop}', '')
            if pat in run_name:
                print('found looping match')
                return r
    sys.exit('No run named %s was found in the trigger' % run_name)


def _get_script(projdef, script_name):
    s = projdef.get('scripts', {}).get(script_name)
    if not s:
        sys.exit('No script named %s was found' % script_name)
    return s


def _get_script_repo(projdef, script):
    s = projdef.get('script-repos', {}).get(script['name'])
    if not s:
        sys.exit('No script-repo named %s was found' % script['name'])
    s['path'] = script['path']
    return s


def _add_pr_params(params, secrets):
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'token ' + secrets['githubtok'],
    }
    url = 'https://api.github.com/repos/%s/%s/pulls/%s' % (
        params['GH_OWNER'], params['GH_REPO'], params['GH_PRNUM'])
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        sys.exit('Unable to get PR info: %s: %d\n%s' % (
            url, r.status_code, r.text))
    data = r.json()
    params['GH_STATUS_URL'] = data['statuses_url']
    params['GH_TARGET_REPO'] = data['base']['repo']['clone_url']
    params['GIT_URL'] = data['head']['repo']['clone_url']
    params['GIT_SHA_BASE'] = data['base']['sha']
    params['GIT_SHA'] = data['head']['sha']


def _get_params(projdef, trigger, run, keyvals, secrets):
    params = {'H_RUN': 'simulator', 'H_BUILD': '42'}
    params.update(projdef.get('params', {}))
    params.update(trigger.get('params', {}))
    params.update(run.get('params', {}))

    for kv in keyvals:
        k, v = kv.split('=', 1)
        params[k] = v

    if trigger['type'] == 'github_pr':
        _add_pr_params(params, secrets)

    return params


def _get_secrets(keyvals):
    if keyvals is None:
        keyvals = []
    secrets = {}
    for kv in keyvals:
        k, v = kv.split('=', 1)
        secrets[k] = v
    return secrets


def _create(args):
    if not os.path.exists(args.workspace):
        sys.exit('Simulator workspace, %s, does not exist' % args.workspace)

    proj_def = _validate(args)
    trigger = _get_trigger(proj_def, args.trigger_name)
    run = _get_run(trigger, args.run_name)
    secrets = _get_secrets(args.secret)

    rundef = {
        'simulator': True,
        'trigger_type': trigger['type'],
        'run_url': '',
        'api_key': '',
        'container': run['container'],
        'env': _get_params(proj_def, trigger, run, args.param, secrets),
        'timeout': proj_def['timeout'],
        'secrets': secrets,
    }

    script = run.get('script')
    if script:
        rundef['script'] = proj_def['scripts'][script]
    else:
        name = run['script-repo']['name']
        rundef['script-repo'] = {
            'clone-url': proj_def['script-repos'][name]['clone-url'],
            'path': run['script-repo']['path'],
        }
        token = proj_def['script-repos'][name].get('token')
        if token:
            rundef['script-repo']['token'] = token
        ref = proj_def['script-repos'][name].get('git-ref')
        if ref:
            rundef['script-repo']['git-ref'] = ref

    rundef_file = os.path.join(args.workspace, 'rundef.json')
    with open(rundef_file, 'w') as f:
        json.dump(rundef, f, indent=2)

    print('Downloading runner for simulator')
    wheel = os.path.join(args.workspace, 'runner.whl')
    with open(wheel, 'wb') as f:
        url = args.jobserv + '/runner'
        r = requests.get(url)
        if r.status_code != 200:
            sys.exit('Unable to download %s: %d\n%s' % (
                url, r.status_code, r.text))
        for chunk in r:
            f.write(chunk)

    with open(os.path.join(args.workspace, 'run_simulator'), 'w') as f:
        os.fchmod(f.fileno(), 0o755)
        f.write('#!/bin/sh -e\n')
        f.write('export PYTHONPATH=%s\n' % wheel)
        f.write('python3 -m jobserv_runner.simulator -w %s %s </dev/null' % (
            args.workspace, rundef_file))

    print('Simulator can now be run with %s run -w %s' % (
        sys.argv[0], args.workspace))


def _run(args):
    script = os.path.join(args.workspace, 'run_simulator')
    os.execv(script, [script])


def _test_grep(args):
    with open(args.proj_def) as f:
        proj_def = yaml.load(f)

    trigger = _get_trigger(proj_def, args.trigger_name)
    run = _get_run(trigger, args.run_name)
    grepping = run.get('test-grepping')
    if not grepping:
        sys.exit(' \'test-grepping\' pattern defined in run')

    test_pat = grepping.get('test-pattern')
    if test_pat:
        test_pat = re.compile(test_pat)
    res_pat = re.compile(grepping['result-pattern'])
    fixups = grepping.get('fixupdict', {})
    cur_test = None
    passes = failures = 0
    for line in sys.stdin.readlines():
        if test_pat:
            m = test_pat.match(line)
            if m:
                cur_test = m.group('name')
        m = res_pat.match(line)
        if m:
            result = m.group('result')
            result = fixups.get(result, result)
            if not cur_test:
                cur_test = 'default'
            print('Test(%s) %s = %s' % (cur_test, m.group('name'), result))
            if result == 'PASSED':
                passes += 1
            else:
                failures += 1
    print('%d PASSED, %d FAILED' % (passes, failures))


def _check_for_updates(args):
    with open(__file__, 'rb') as f:
        h = hashlib.md5()
        h.update(f.read())
        version = h.hexdigest()

    url = args.jobserv + '/simulator?version=' + version
    resp = requests.get(url)
    if resp.status_code == 200:
        print('Simulator version has changed, updating local script')
        with open(__file__, 'w') as f:
            f.write(resp.text)
    elif resp.status_code == 304:
        print('Simulator version has not changed')
    else:
        print('HTTP Error: %d\n%s' % (resp.status_code, resp.text))


def get_args(args=None):
    parser = argparse.ArgumentParser(description='''
        A tool to help build and run a "JobServ simulator" that can execute
        a Run defined in a project definition file locally without using the
        actual JobServ.''')
    cmds = parser.add_subparsers(title='Commands')

    p = cmds.add_parser('validate-schema',
                        help='''Validate a project definition YAML file against
                             a running JobServ''')
    p.set_defaults(func=_validate)
    p.add_argument('--jobserv', '-j',
                   default='https://api.linarotechnologies.org/',
                   help='The JobServ to query. Default=%(default)s')
    p.add_argument('--proj-def', '-d', required=True,
                   type=argparse.FileType('r'),
                   help='Project defintion .yml')

    p = cmds.add_parser('create',
                        help='Create a workspace for executing simulated run.')
    p.set_defaults(func=_create)
    p.add_argument('--jobserv', '-j',
                   default='https://api.linarotechnologies.org/',
                   help='The JobServ to query. Default=%(default)s')
    p.add_argument('--proj-def', '-d', required=True,
                   type=argparse.FileType('r'),
                   help='Project defintion .yml')
    p.add_argument('--trigger-name', '-t', required=True,
                   help='The name of the trigger the run is under')
    p.add_argument('--run-name', '-r', required=True,
                   help='The name of the run to try')
    p.add_argument('--workspace', '-w', required=True,
                   help='''A directory to serve as the simulator workspace. It
                        will hold the scripts needed to run the simulator and
                        and also store its artifacts''')
    p.add_argument('--param', '-p', metavar='KEY=VAL', action='append',
                   help='Parameter(s) needed by the run.')
    p.add_argument('--secret', '-s', metavar='KEY=VAL', action='append',
                   help='Parameter(s) needed by the run.')

    p = cmds.add_parser('run',
                        help='Run the simulator defined in the workspace.')
    p.set_defaults(func=_run)
    p.add_argument('--workspace', '-w', required=True,
                   help='The simulator workspace')

    p = cmds.add_parser('check-test-grepping',
                        help='''Parses STDIN with test-grepping rules to see
                             find out what tests it thinks pass/fail''')
    p.set_defaults(func=_test_grep)
    p.add_argument('--proj-def', '-d', required=True,
                   help='Project defintion .yml')
    p.add_argument('--trigger-name', '-t', required=True,
                   help='The name of the trigger the run is under')
    p.add_argument('--run-name', '-r', required=True,
                   help='The name of the run to try')

    p = cmds.add_parser('check-for-updates',
                        help='Check for updates to this simulator script')
    p.set_defaults(func=_check_for_updates)
    p.add_argument('--jobserv', '-j',
                   default='https://api.linarotechnologies.org/',
                   help='The JobServ to query. Default=%(default)s')

    args = parser.parse_args(args)
    return args


if __name__ == '__main__':
    args = get_args()
    if getattr(args, 'func', None):
        args.func(args)
