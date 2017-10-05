# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import datetime
import json
import logging
import time
import urllib.parse

from xmlrpc.client import ServerProxy

import dateutil.parser
import pytz
import requests
import yaml

from jobserv.internal_requests import signed_get
from jobserv.settings import LAVA_URLBASE

FINISHED_JOB_STATUS = ["Complete", "Incomplete", "Canceled"]

logging.basicConfig(
    level='INFO', format='%(asctime)s %(levelname)s: %(message)s')
log = logging.getLogger()


def _update_status(test, status, msg, results=None):
    log.info('Updating to %s for %s', status, test['url'])
    data = {
        'message': '== %s: %s\n' % (datetime.datetime.utcnow(), msg),
        'status': status,
        'results': results,
    }

    headers = {'Authorization': 'Token ' + test['api_key']}
    qs = {'context': test['context']}
    resp = requests.put(test['url'], headers=headers, json=data, params=qs)
    if resp.status_code != 200:
        log.error(
            'Unable to update via API: %d: %s', resp.status_code, resp.text)
        return

    if resp.json()['data']['complete']:
        meta = test['metadata']
        if meta:
            meta = json.loads(meta)
        else:
            meta = {}
        url = meta.get('github_url')
        if url:
            headers = meta['github_headers']
            data = meta['github_data']
            data['state'] = 'pending'
            log.info('Updating github PR to %s for %s', status, url)
            if status == 'PASSED':
                data['state'] = 'success'
            elif status == 'FAILED':
                data['state'] = 'failure'
            resp = requests.post(url, json=data, headers=headers)
            if resp.status_code not in (200, 201):
                log.error(
                    'Unable to update PR: %d: %s', resp.status_code, resp.text)

        url = meta.get('gitlab_url')
        if url:
            headers = meta['gitlab_headers']
            data = meta['gitlab_data']
            data['state'] = 'running'
            log.info('Updating gitlab MR to %s for %s', status, url)
            if status == 'PASSED':
                data['state'] = 'success'
            elif status == 'FAILED':
                data['state'] = 'failed'
            resp = requests.post(url, json=data, headers=headers)
            if resp.status_code not in (200, 201):
                log.error(
                    'Unable to update MR: %d: %s', resp.status_code, resp.text)


def _get_results(yaml_buff):
    status = 'FAILED'
    res_data = yaml.load(yaml_buff)
    if set([x['result'] for x in res_data]) == set(['pass']):
        status = 'PASSED'
    results = []
    for r in res_data:
        results.append({
            'name': '%s/%s' % (r['suite'], r['name']),
            'status': 'PASSED' if r['result'] == 'pass' else 'FAILED',
            'context': LAVA_URLBASE + r['url'],
        })
    return status, results


def _check_run(test, metadata):
    jobid = test['context'].rsplit('/', 1)[1]

    p = urllib.parse.urlparse(test['context'])
    server = ServerProxy('%s://%s:%s@%s/RPC2' % (
        p.scheme, metadata['lava_user'], metadata['lava_token'], p.netloc))

    status = None
    try:
        status = server.scheduler.job_status(jobid)['job_status']
    except:
        return

    if status in FINISHED_JOB_STATUS:
        log.info('Found completed test lava-id(%s): %s', jobid, test['url'])
        raw = server.results.get_testjob_results_yaml(jobid)
        test_status, results = _get_results(raw)
        if status != 'Complete':
            status = 'FAILED'
        else:
            status = test_status
        msg = 'LAVA Job(%s) polled: %s\nResults: %s\n%s' % (
            jobid, test['url'], test['context'], raw)
        _update_status(test, status, msg, results)
        return True


def _reap():
    log.debug('looking for stale runs')
    now = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)

    r = signed_get('http://lci-web/incomplete_tests/')
    if r.status_code == 200:
        data = r.json()['data']
        for test in data['tests']:
            try:
                metadata = json.loads(test['metadata'])
            except:
                continue
            if 'lava_user' in metadata:
                elapsed = now - dateutil.parser.parse(test['created'])
                if elapsed.total_seconds() > 28800:
                    # we've waited 8 hours, just fail the run
                    _update_status(test, 'FAILED',
                                   'Unable to get LAVA results after 8 hours')
                # give lava 5 minutes before we start polling
                if elapsed.total_seconds() > 300:
                    log.info('Checking stuck test: %s', test['url'])
                    _check_run(test, metadata)


def run_reaper():
    log.info('lava reaper has started')
    try:
        while True:
            _reap()
            time.sleep(300)  # run every 5 minutes
    except:
        log.exception('unexpected error in reactor')
