# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import json

from jobserv_runner.handlers.github_pr import GHStatusApi
from jobserv_runner.handlers.lava import HandlerError, LavaHandler
from jobserv_runner.handlers.simple import SimpleHandler


class LavaPRHandler(LavaHandler):
    """Combine the logic of the LavaHandler with jobserv of GitHub"""
    @classmethod
    def get_jobserv(clazz, rundef):
        if rundef.get('simulator'):
            return SimpleHandler.get_jobserv(rundef)

        token = rundef.get('secrets', {}).get('githubtok')
        if not token:
            raise HandlerError('"githubtok" not set in rundef secrets')
        jobserv = GHStatusApi(rundef)

        user = rundef.get('secrets', {}).get('LAVA_USER')
        token = rundef.get('secrets', {}).get('LAVA_TOKEN')
        if not user or not token:
            raise HandlerError('LAVA_USER and/or LAVA_TOKEN not defined')

        metadata = json.dumps({
            'lava_user': user,
            'lava_token': token,
            'github_headers': jobserv.headers,
            'github_data': jobserv.data,
            'github_url': jobserv.status_url,
        })
        jobserv.update_status('RUNNING', 'Saving metadata for run', metadata)
        return jobserv


handler = LavaPRHandler
