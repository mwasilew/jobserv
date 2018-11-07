# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import json
import os
import pkgutil

from jobserv_runner.handlers.simple import HandlerError, SimpleHandler


class LavaHandler(SimpleHandler):
    def prepare_mounts(self):
        # setup secrets for the run-url and api-key needed by lava-submit
        self.rundef['secrets']['H_RUN_URL'] = self.jobserv._run_url
        self.rundef['secrets']['H_RUN_TOKEN'] = self.jobserv._api_key

        mounts = super().prepare_mounts()

        with self.log_context('Creating lava scripts under /lava-bin') as log:
            lava_bin = os.path.join(self.run_dir, 'lava-bin')
            os.mkdir(lava_bin)

            for script in ('generate-public-url', 'lava-submit'):
                log.info('Creating /lava-bin/%s', script)

                buff = pkgutil.get_data(
                    'jobserv_runner.handlers', script + '.py')
                with open(os.path.join(lava_bin, script), 'wb') as f:
                    f.write(buff)
                    os.fchmod(f.fileno(), 0o555)
                host = os.path.join(lava_bin, script)
                cont = '/usr/local/bin/' + script
                mounts.append((host, cont))
        return mounts

    @staticmethod
    def _getenv(rundef, key):
        val = rundef.get('env', {}).get(key)
        if not val:
            val = rundef.get('secrets', {}).get(key)
        return val

    @classmethod
    def get_jobserv(clazz, rundef):
        jobserv = SimpleHandler.get_jobserv(rundef)

        user = clazz._getenv(rundef, 'LAVA_USER')
        token = clazz._getenv(rundef, 'LAVA_TOKEN')
        if not user or not token:
            raise HandlerError('LAVA_USER and/or LAVA_TOKEN not defined')

        metadata = json.dumps({'lava_user': user, 'lava_token': token})
        jobserv.update_status('RUNNING', 'Saving metadata for run', metadata)
        return jobserv


handler = LavaHandler
