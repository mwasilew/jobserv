# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import os
import shutil
import tempfile
import time

import jobserv.models
import jobserv.worker

from jobserv.models import db, Build, Project, Run, Worker
from jobserv.settings import SURGE_SUPPORT_RATIO
from jobserv.worker import _check_queue, _check_workers

from tests import JobServTest


class TestWorkerMonitor(JobServTest):
    def setUp(self):
        super().setUp()
        jobserv.models.WORKER_DIR = tempfile.mkdtemp()
        jobserv.worker.SURGE_FILE = os.path.join(
            jobserv.models.WORKER_DIR, 'enable_surges')
        self.addCleanup(shutil.rmtree, jobserv.models.WORKER_DIR)
        self.worker = Worker('w1', 'd', 1, 1, 'amd64', 'k', 1, 'amd64')
        self.worker.enlisted = True
        self.worker.online = True
        db.session.add(self.worker)
        db.session.commit()

    def test_offline_no_pings(self):
        _check_workers()
        db.session.refresh(self.worker)
        self.assertFalse(self.worker.online)

    def test_offline(self):
        self.worker.ping()
        offline = time.time() - 81  # 81 seconds old
        os.utime(self.worker.pings_log, (offline, offline))
        _check_workers()
        db.session.refresh(self.worker)
        self.assertFalse(self.worker.online)

    def test_rotate(self):
        # create a big file
        self.worker.ping()
        with open(self.worker.pings_log, 'a') as f:
            f.write('1' * 1024 * 1024)
        _check_workers()
        self.assertEqual(0, os.stat(self.worker.pings_log).st_size)
        # there should be two files now
        self.assertEqual(2, len(
            os.listdir(os.path.dirname(self.worker.pings_log))))

        # we should still be online
        db.session.refresh(self.worker)
        self.assertTrue(self.worker.online)

    def test_surge_simple(self):
        self.create_projects('proj1')
        b = Build.create(Project.query.all()[0])
        for x in range(SURGE_SUPPORT_RATIO + 1):
            r = Run(b, 'run%d' % x)
            r.host_tag = 'amd64'
            db.session.add(r)
        db.session.commit()
        _check_queue()
        self.assertTrue(os.path.exists(jobserv.worker.SURGE_FILE + '-amd64'))

        db.session.delete(Run.query.all()[0])
        db.session.commit()
        _check_queue()
        self.assertFalse(os.path.exists(jobserv.worker.SURGE_FILE + '-amd64'))

    def test_surge_complex(self):
        # we'll have two amd64 workers and one armhf
        worker = Worker('w2', 'd', 1, 1, 'amd64', 'k', 1, 'amd64')
        worker.enlisted = True
        worker.online = True
        db.session.add(worker)
        worker = Worker('w3', 'd', 1, 1, 'armhf', 'k', 1, 'armhf')
        worker.enlisted = True
        worker.online = True
        db.session.add(worker)
        db.session.commit()

        self.create_projects('proj1')
        b = Build.create(Project.query.all()[0])
        for x in range(SURGE_SUPPORT_RATIO + 1):
            r = Run(b, 'amd%d' % x)
            r.host_tag = 'amd64'
            db.session.add(r)
            r = Run(b, 'armhf%d' % x)
            r.host_tag = 'armhf'
            db.session.add(r)

        db.session.commit()
        _check_queue()
        self.assertFalse(os.path.exists(jobserv.worker.SURGE_FILE + '-amd64'))
        self.assertTrue(os.path.exists(jobserv.worker.SURGE_FILE + '-armhf'))

        # get us under surge for armhf
        db.session.delete(Run.query.filter(Run.host_tag == 'armhf').first())
        # and over surge for amd64
        for x in range(SURGE_SUPPORT_RATIO + 1):
            r = Run(b, 'run%d' % x)
            r.host_tag = 'amd64'
            db.session.add(r)

        db.session.commit()
        _check_queue()
        self.assertTrue(os.path.exists(jobserv.worker.SURGE_FILE + '-amd64'))
        self.assertFalse(os.path.exists(jobserv.worker.SURGE_FILE + '-armhf'))
