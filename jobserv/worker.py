# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import logging
import os
import time

from jobserv.models import db, BuildStatus, Run, Worker
from jobserv.stats import CarbonClient

logging.basicConfig(
    level='INFO', format='%(asctime)s %(levelname)s: %(message)s')
log = logging.getLogger()


def _check_worker(w):
    log.debug('checking worker(%s) online(%s)', w.name, w.enlisted)
    pings_log = w.pings_log

    try:
        now = time.time()
        st = os.stat(pings_log)
        diff = now - st.st_mtime
        if diff > 80 and w.online:
            # the worker checks in every 20s. This means its missed 4 check-ins
            log.info('marking %s offline %ds without a check-in', w.name, diff)
            w.online = False

        # based on rough calculations a 1M file is about 9000 entries which is
        # about 2 days worth of information
        if st.st_size > (1024 * 1024):
            # rotate log file
            rotated = pings_log + '.%d' % now
            log.info('rotating pings log to: %s', rotated)
            os.rename(pings_log, rotated)

            # the pings log won't exist now, so we need to touch an empty file
            # with the proper mtime so we won't mark it offline on the next run
            # this is technically racy, pings.log could exist at this moment,
            # so we open in append mode, our st_mtime could be faulty because
            # of this race condition, but this is one of the reasons why we
            # give the worker some grace periods to check in
            open(pings_log, 'a').close()
            os.utime(pings_log, (st.st_atime, st.st_mtime))
    except FileNotFoundError:
        # its never checked in
        if w.online:
            w.online = False
            log.info('marking %s offline (no pings log)', w.name)


def _check_workers():
    for w in Worker.query.filter(Worker.enlisted == 1):
        _check_worker(w)
    db.session.commit()


def _check_queue():
    queued_runs = Run.query.filter(Run.status == BuildStatus.QUEUED).count()
    with CarbonClient() as c:
        c.send('queued_runs', queued_runs)


def run_monitor_workers():
    log.info('worker monitor has started')
    try:
        while True:
            log.debug('checking workers')
            _check_workers()
            log.debug('checking queue')
            _check_queue()
            time.sleep(120)  # run every 2 minutes
    except:
        log.exception('unexpected error in run_monitor_workers')
