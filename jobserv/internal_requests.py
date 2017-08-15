import functools
import hmac
import requests
import time

from flask import request

from jobserv.jsend import jsendify
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


def internal_api(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not INTERNAL_API_KEY:
            raise RuntimeError('JobServ missing INTERNAL_API_KEY')

        sig = request.headers.get('X-JobServ-Sig')
        ts = request.headers.get('X-Time')
        if not sig:
            return jsendify('X-JobServ-Sig not provided', 401)
        if not ts:
            return jsendify('X-Time not provided', 401)
        msg = '%s,%s,%s' % (request.method, ts, request.base_url)
        computed = hmac.new(INTERNAL_API_KEY, msg.encode(), 'sha1').hexdigest()
        if not hmac.compare_digest(sig, computed):
            return jsendify('Invalid signature', 401)
        return f(*args, **kwargs)
    return wrapper
