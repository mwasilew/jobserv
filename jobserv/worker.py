# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import logging
import os
import time

from jobserv.models import db, BuildStatus, Run, Worker, WORKER_DIR
from jobserv.sendmail import notify_surge_started, notify_surge_ended
from jobserv.settings import SURGE_SUPPORT_RATIO
from jobserv.stats import StatsClient

SURGE_FILE = os.path.join(WORKER_DIR, 'enable_surge')
DETECT_FLAPPING = True  # useful for unit testing

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
        threshold = 80
        if w.surges_only:
            # surge workers check in every 90s so let them miss 3 check-ins
            threshold = 120
        if diff > threshold and w.online:
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
    # find out queue by host_tags
    queued = Run.query.filter(
        Run.status == BuildStatus.QUEUED
    ).order_by(
        Run.id
    )
    queued = [[x.host_tag, True] for x in queued]
    with StatsClient() as c:
        c.send('queued_runs', len(queued))

    # now get a list of available slots for runs
    workers = Worker.query.filter(
        Worker.enlisted == True,  # NOQA (flake8 doesn't like == True)
        Worker.online == True,
        Worker.surges_only == False
    )
    hosts = {}
    for w in workers:
        hosts[w.name] = {
            'slots': SURGE_SUPPORT_RATIO,
            'tags': [x.strip() for x in w.host_tags.split(',')],
        }

    # try and figure out runs/host in a round-robin fashion
    matches_found = True
    while matches_found:
        matches_found = False
        for name in list(hosts.keys()):
            host = hosts[name]
            if host['slots']:
                for run in queued:
                    # run = host_tag, not-claimed by a host
                    # TODO support wildcard tag=arm%
                    if run[1] and run[0] in host['tags']:
                        matches_found = True
                        run[1] = False  # claim it
                        host['slots'] -= 1
                        if host['slots'] == 0:
                            del hosts[name]
                        break  # move to the next host for round-robin
    surges = {}
    for tag, unclaimed in queued:
        if unclaimed:
            surges[tag] = surges.setdefault(tag, 0) + 1

    # clean up old surges no longer in place
    path, base = os.path.split(SURGE_FILE)
    prev_surges = [x[len(base) + 1:] for x in os.listdir(path)
                   if x.startswith(base)]
    log.debug('surges(%r), prev(%r)', surges, prev_surges)
    for tag in prev_surges:
        surge_file = SURGE_FILE + '-' + tag
        if tag not in surges:
            if time.time() - os.stat(surge_file).st_mtime < 300:
                # surges can sort of "flap". ie - you get bunches of emails
                # when its right on the threshold. This just keeps us inside
                # a surge for at least 5 minutes to help make sure we don't
                # "flap"
                if DETECT_FLAPPING:
                    continue
            log.info('Exiting surge support for %s', tag)
            with open(surge_file) as f:
                msg_id = f.read().strip()
                notify_surge_ended(tag, msg_id)
            os.unlink(surge_file)

    # now check for new surges
    for tag, count in surges.items():
        surge_file = SURGE_FILE + '-' + tag
        if not os.path.exists(surge_file):
            log.info('Entering surge support for %s: count=%d', tag, count)
            with open(surge_file, 'w') as f:
                msgid = notify_surge_started(tag)
                f.write(msgid)


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
