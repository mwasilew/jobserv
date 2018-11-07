# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import json

from jobserv_runner.handlers.gitlab_mr import StatusApi
from jobserv_runner.handlers.lava import HandlerError, LavaHandler
from jobserv_runner.handlers.simple import SimpleHandler


class LavaMRHandler(LavaHandler):
    @classmethod
    def get_jobserv(clazz, rundef):
        if rundef.get('simulator'):
            return SimpleHandler.get_jobserv(rundef)
        user = rundef.get('secrets', {}).get('gitlabuser')
        if not user:
            user = rundef['env']['gitlabuser']
        token = rundef.get('secrets', {}).get('gitlabtok')
        if not user or not token:
            raise HandlerError(
                '"gitlabuser" and/or "gitlabtok" not set in rundef secrets')

        user = rundef.get('secrets', {}).get('LAVA_USER')
        token = rundef.get('secrets', {}).get('LAVA_TOKEN')
        if not user or not token:
            raise HandlerError('LAVA_USER and/or LAVA_TOKEN not defined')

        jobserv = StatusApi(rundef)
        metadata = json.dumps({
            'lava_user': user,
            'lava_token': token,
            'gitlab_headers': jobserv.headers,
            'gitlab_data': jobserv.data,
            'gitlab_url': jobserv.status_url,
        })
        jobserv.update_status('RUNNING', 'Saving metadata for run', metadata)
        return jobserv


handler = LavaMRHandler
