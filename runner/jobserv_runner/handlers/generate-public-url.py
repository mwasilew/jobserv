#!/usr/bin/python3
# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import sys
import urllib.request

if len(sys.argv) != 2:
    sys.exit('Usage: %s <internal url>' % sys.argv[0])
url = sys.argv[1]


class NoRedirection(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response
    https_response = http_response


# return the 302 redirect value for the url
opener = urllib.request.build_opener(NoRedirection)
# make the link valid for 2 hours
req = urllib.request.Request(url, headers={'X-EXPIRATION': '7200'})
resp = opener.open(req)
if resp.status != 302:
    raise RuntimeError('Expected a 302 redirect from URL: ' + url)
print(resp.headers['location'])
