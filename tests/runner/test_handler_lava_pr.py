# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import os
import shutil
import tempfile

from unittest import TestCase, mock

from jobserv_runner.handlers.lava_pr import LavaPRHandler, HandlerError


class LavaPRHandlerTest(TestCase):
    def setUp(self):
        super().setUp()

        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)

        rdir = os.path.join(self.tmpdir, 'run')
        wdir = os.path.join(self.tmpdir, 'worker')
        os.mkdir(rdir)
        os.mkdir(wdir)
        self.handler = LavaPRHandler(wdir, rdir, mock.Mock(), None)

    def test_requires_tokens(self):
        """Ensure runs exit if missing lava tokens."""
        msg = '"githubtok" not set in rundef secrets'
        with self.assertRaisesRegex(HandlerError, msg):
            LavaPRHandler.get_jobserv({'run_url': 'z', 'api_key': 'z'})

        msg = 'LAVA_USER and/or LAVA_TOKEN not defined'
        with self.assertRaisesRegex(HandlerError, msg):
            LavaPRHandler.get_jobserv({
                'run_url': 'z',
                'api_key': 'z',
                'env': {'H_RUN': '12', 'H_BUILD': 'b', 'GH_STATUS_URL': 'u'},
                'frontend_url': 'http://foo',
                'secrets': {'githubtok': 'foo'},
            })

    def test_prepare_mounts(self):
        """Ensure we create lava scripts and secrets."""
        self.handler.rundef = {
            'script': '',
            'persistent-volumes': None,
            'run_url': 'z',
            'secrets': {
                'LAVA_USER': 'luser',
                'LAVA_TOKEN': 'ltoken',
            }
        }
        self.handler.jobserv._run_url = 'runurl'
        self.handler.jobserv._api_key = 'token'
        self.handler.prepare_mounts()

        secrets = os.path.join(self.handler.run_dir, 'secrets')
        for x, val in {'H_RUN_URL': 'runurl', 'H_RUN_TOKEN': 'token'}.items():
            p = os.path.join(secrets, x)
            self.assertTrue(os.path.exists(p), 'path: ' + p)
            with open(p) as f:
                self.assertEqual(val, f.read())

        scripts = os.path.join(self.handler.run_dir, 'lava-bin')
        p = os.path.join(scripts, 'lava-submit')
        self.assertTrue(os.path.exists(p), 'path: ' + p)
        p = os.path.join(scripts, 'generate-public-url')
        self.assertTrue(os.path.exists(p), 'path: ' + p)
