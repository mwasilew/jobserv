# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import datetime

from unittest import TestCase

from jobserv_runner.logging import ContextLogger


class TestLogger(ContextLogger):
    def _now(self):
        return self.now


class LoggerTest(TestCase):
    def setUp(self):
        super().setUp()
        self.now = datetime.datetime.utcnow()

    def _test_log(self, level):
        log = TestLogger(None)
        log.now = datetime.datetime.utcnow()

        getattr(log, level)('foo %s', 'bar')
        expected = '   %s %-5s foo bar\n' % (log.now, level.upper())
        self.assertEqual(expected, log.io.getvalue())

    def test_info(self):
        self._test_log('info')

    def test_warn(self):
        self._test_log('warn')

    def test_error(self):
        self._test_log('error')
