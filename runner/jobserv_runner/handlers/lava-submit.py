#!/usr/bin/python3
# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import os
import json
import sys
import urllib.request
import urllib.parse
from xmlrpc.client import ProtocolError, ServerProxy

if len(sys.argv) != 2:
    sys.exit('Usage: %s jobfile' % sys.argv[0])


def urllib_error_str(e):
    error = 'Unable to create test entry in jobserv: HTTP_%d' % e.code
    if hasattr(e, 'reason'):
        error += ' ' + e.reason
        error += '\\n' + e.read().decode()
    return error


def _getenv(key):
    val = None
    secret = os.path.join('/secrets', key)
    if os.path.exists(secret):
        val = open(secret).read()
    val = os.getenv(key, val)
    if not val:
        sys.exit('Missing required env variable or secret: ' + key)
    return val


RUN_URL = _getenv('H_RUN_URL')
HEADERS = {
    'content-type': 'application/json',
    'Authorization': 'Token ' + _getenv('H_RUN_TOKEN'),
}


def _post(url, data):
    data = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=data, headers=HEADERS, method='POST')
    try:
        resp = urllib.request.urlopen(req)
        return resp
    except urllib.error.URLError as e:
        error = urllib_error_str(e)
        sys.exit(error)


user = _getenv('LAVA_USER')
token = _getenv('LAVA_TOKEN')
host = _getenv('LAVA_RPC')

host = _getenv('LAVA_RPC').replace('://', '://%s:%s@' % (user, token))
server = ServerProxy(host)
with open(sys.argv[1]) as f:
    name = os.path.splitext(os.path.basename(sys.argv[1]))[0]
    try:
        jobid = server.scheduler.submit_job(f.read())
    except ProtocolError as e:
        # e.url includes the password, so strip that out
        e.url = e.url.replace(token, '<SECRET_TOKEN>')
        msg = 'RPC Error(%d): %s\n  error=%s\n  headers=%r' % (
            e.errcode, e.url, e.errmsg, e.headers)
        sys.exit(msg)
    joburl = _getenv('LAVA_RPC').replace('RPC2', 'scheduler/job/%d' % jobid)
    print('Lava Job: %s' % joburl)
    _post(RUN_URL + 'tests/' + name + '/', data={'context': joburl})
    with open('/tmp/lava-submitted', 'a') as f:
        f.write(joburl + '\n')
