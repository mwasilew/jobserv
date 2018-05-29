# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import hmac
import requests
import time

from jobserv.settings import INTERNAL_API_KEY


def _sign(url, headers, method):
    headers['X-Time'] = str(round(time.time()))
    msg = '%s,%s,%s' % (method, headers['X-Time'], url)
    sig = hmac.new(INTERNAL_API_KEY, msg.encode(), 'sha1').hexdigest()
    headers['X-JobServ-Sig'] = sig


def signed_get(url, *args, **kwargs):
    _sign(url, kwargs.setdefault('headers', {}), 'GET')
    return requests.get(url, *args, **kwargs)


def signed_post(url, *args, **kwargs):
    # should probably sign the request body, but this should be okay for just
    # ensuring internal access
    _sign(url, kwargs.setdefault('headers', {}), 'POST')
    return requests.post(url, *args, **kwargs)
