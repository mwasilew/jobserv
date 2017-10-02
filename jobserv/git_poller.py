# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import fnmatch
import json
import logging
import time

import requests
import yaml

from requests.auth import HTTPBasicAuth

from jobserv.internal_requests import signed_get, signed_post
from jobserv.project import ProjectDefinition
from jobserv.settings import GIT_POLLER_INTERVAL
from jobserv.storage import Storage

logging.basicConfig(
    level='INFO', format='%(asctime)s %(levelname)s: %(message)s')
log = logging.getLogger()
logging.getLogger('pykwalify.core').setLevel(logging.WARNING)
logging.getLogger('pykwalify.rule').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)

_projects = {}


def _get_projects():
    resp = signed_get('http://lci-web/project-triggers/',
                      params={'type': 'git_poller'})
    if resp.status_code != 200:
        log.error('Unable to get projects from front-end: %d %s',
                  resp.status_code, resp.text)
        return None
    return {x['project']: x for x in resp.json()['data']}


def _get_projdef(name, proj):
    repo = proj['poller_def']['definition_repo']
    defile = proj['poller_def'].get('definition_file')
    if not defile:
        defile = name + '.yml'
    gitlab = proj['poller_def'].get('secrets', {}).get('gitlabtok')

    if 'github.com' not in repo and not gitlab:
        log.error('Only GitHub and GitlLab repos are supported')
        return None

    headers = proj.setdefault('projdef_headers', {})
    token = proj['poller_def'].get('secrets', {}).get('githubtok')
    if token:
        headers['Authorization'] = 'token ' + token

    if gitlab:
        headers['PRIVATE-TOKEN'] = gitlab
        url = repo.replace('.git', '') + '/raw/master/' + defile
    else:
        url = repo.replace('github.com', 'raw.githubusercontent.com')
        if url[-1] != '/':
            url += '/'
        url += 'master/' + defile

    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        try:
            log.info('New version of project definition found for %s', url)
            data = yaml.load(r.text)
            ProjectDefinition.validate_data(data)
            proj['definition'] = ProjectDefinition(data)
            # allows us to cache the resp
            headers['If-None-Match'] = r.headers['ETAG']
        except:
            log.exception('Validation failed for %s ...skipping', url)
            return None
    elif r.status_code == 304:
        # it hasn't changed
        log.debug('Cache hit for %s', url)
    else:
        log.error('Unable to read definition(%s): %d: %s',
                  url, r.status_code, r.text)
        return None
    return proj['definition']


def _get_refs(repo_url, proj):
    auth = None
    ghtok = proj['poller_def'].get('secrets', {}).get('githubtok')
    if ghtok:
        auth = HTTPBasicAuth(proj['poller_def']['user'], ghtok)
    if not repo_url.endswith('.git'):
        # access these URL on github requires .git and it seems to be needed
        # for things like cgit and gitweb as well
        repo_url += '.git'
    if repo_url[-1] != '/':
        repo_url += '/'
    repo_url += 'info/refs?service=git-upload-pack'
    resp = requests.get(repo_url, auth=auth)
    if resp.status_code != 200:
        log.error('Unable to check %s for changes: %d %s',
                  repo_url, resp.status_code, resp.reason)
    else:
        for line in resp.text.splitlines()[2:]:
            if line == '0000':
                break
            line = line[4:]  # strip off git protocol stuff
            log.debug('Looking at ref: %s', line)
            sha, ref = line.split(' ', 1)
            yield sha, ref


def _get_repo_changes(refs_cache, url, refs, proj):
    # TODO the cache is just repo urls. If we have 2 different CI projects
    # pointing to the same URL this will fail. So the cache lookup key should
    # project-name + url not just url
    log.info('Looking for changes to: %s', url)
    cur_refs = refs_cache.setdefault(url, {})
    for sha, ref in _get_refs(url, proj):
        for pattern in refs:
            if fnmatch.fnmatch(ref, pattern):
                cur = cur_refs.get(ref)
                if cur != sha:
                    cur_refs[ref] = sha
                    if cur is None:
                        log.info('First run detected for %s - %s', url, ref)
                    else:
                        log.info('%s %s change %s->%s', url, ref, cur, sha)
                        yield {'GIT_REF': ref, 'GIT_URL': url,
                               'GIT_OLD_SHA': cur, 'GIT_SHA': sha}


def _github_log(proj, change_params):
    base = change_params['GIT_OLD_SHA']
    head = change_params['GIT_SHA']
    url = change_params['GIT_URL'].replace(
        '.git', ''
    ).replace(
        'github.com', 'api.github.com/repos'
    ) + '/commits?sha=' + head

    auth = None
    ghtok = proj['poller_def'].get('secrets', {}).get('githubtok')
    if ghtok:
        auth = HTTPBasicAuth(proj['poller_def']['user'], ghtok)

    gitlog = ''
    try:
        r = requests.get(url, auth=auth)
    except Exception as e:
        log.exception('Unable to get %s', url)
        return 'Unable to get %s\n%s' % (url, str(e))
    if r.status_code == 200:
        for commit in r.json():
            if commit['sha'] == base:
                break
            gitlog += '%s %s\n' % (
                commit['sha'][:7], commit['commit']['message'].splitlines()[0])
    else:
        gitlog += 'Unable to get github log(%s): %d %s' % (
            url, r.status_code, r.text)
    return gitlog


def _trigger(name, proj, projdef, trigger_name, change_params):
    log.info('Trigger build for %s with params: %r', name, change_params)
    data = {
        'trigger-name': trigger_name,
        'params': change_params,
        'secrets': proj['poller_def'].get('secrets', {}),
        'project-definition': projdef._data,
        'reason': json.dumps(change_params, indent=2),
    }
    if change_params['GIT_URL'].startswith('https://github.com'):
        data['reason'] += '\n' + _github_log(proj, change_params)
    log.debug('Data for build is: %r', data)
    url = 'http://lci-web/projects/%s/builds/' % name
    resp = signed_post(url, json=data)
    if resp.status_code != 201:
        log.error('Error creating build(%s): %d - %s',
                  name, resp.status_code, resp.text)
    else:
        log.info('Build created: %s', resp.text)


def _poll_project(refs_cache, name, proj, projdef):
    for trigger in projdef.triggers:
        if trigger['type'] == 'git_poller':
            params = trigger.get('params', {})
            urls = params.get('GIT_URL', '').split()
            refs = params.get('GIT_POLL_REFS', '').split()
            if not urls or not refs:
                log.error('Project(%s) missing GIT_URL or GIT_POLL_REFS', name)
                continue
            for url in urls:
                for changes in _get_repo_changes(refs_cache, url, refs, proj):
                    _trigger(
                        name, proj, projdef, trigger['name'], changes)


def _poll():
    try:
        projects = _get_projects()
        if projects is None:
            return
    except Exception:
        logging.exception('Unable to get project list from JobServ')
        return

    names = set(projects.keys())
    cur_names = set(_projects.keys())

    for n in cur_names - names:
        log.info('Removing %s from poller list', n)
        del _projects[n]

    for n in names - cur_names:
        log.info('Adding %s to poller list', n)
        _projects[n] = {'poller_def': projects[n]}

    for n in names & cur_names:
        if _projects[n]['poller_def'] != projects[n]:
            log.info('Updating %s', n)
            _projects[n]['poller_def'] = projects[n]

    with Storage().git_poller_cache() as refs_cache:
        for name, proj in _projects.items():
            log.debug('Checking project: %s', name)
            projdef = _get_projdef(name, proj)
            if projdef:
                _poll_project(refs_cache, name, proj, projdef)


def run():
    last_run = time.time() - 15  # Wait a few seconds before polling jobserv
    while True:
        sleep = GIT_POLLER_INTERVAL - (time.time() - last_run)
        if sleep > 0:
            log.debug('Waiting %d before running again', sleep)
            time.sleep(sleep)
        last_run = time.time()
        _poll()
