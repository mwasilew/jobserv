import contextlib
import datetime
import enum
import json
import logging
import os
import random
import string
import time

from flask import url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.hybrid import Comparator, hybrid_property

from jobserv.settings import WORKER_DIR
from jobserv.stats import CarbonClient

db = SQLAlchemy()


def get_cumulative_status(obj, items):
    '''A helper used by Test and Build to calculate the status based on the
       status of its child TestResults and Runs.'''
    status = BuildStatus.QUEUED  # Default guess to QUEUED
    states = set([x.status for x in items])
    if BuildStatus.RUNNING in states or BuildStatus.UPLOADING in states:
        # Something is still running
        status = BuildStatus.RUNNING
        if BuildStatus.FAILED in states:
            status = BuildStatus.RUNNING_WITH_FAILURES
    if BuildStatus.QUEUED in states and BuildStatus.FAILED in states:
        status = BuildStatus.RUNNING_WITH_FAILURES
    if BuildStatus.QUEUED in states and BuildStatus.PASSED in states:
        status = BuildStatus.RUNNING
    if not states - set([BuildStatus.PASSED, BuildStatus.FAILED]):
        # All runs have completed. Have we passed or failed
        if BuildStatus.FAILED in states:
            status = BuildStatus.FAILED
        else:
            status = BuildStatus.PASSED
    return status


class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True)

    builds = db.relationship('Build', order_by='-Build.id')

    def __init__(self, name=None):
        self.name = name

    def as_json(self, detailed=False):
        data = {
            'name': self.name,
            'url': url_for(
                'api_project.project_get', proj=self.name, _external=True),
        }
        if detailed:
            data['builds_url'] = url_for(
                'api_build.build_list', proj=self.name, _external=True)
        return data

    def __repr__(self):
        return '<Project %s>' % self.name


class TriggerTypes(enum.Enum):
    git_poller = 1
    github_pr = 2
    simple = 3
    lava = 4
    lava_pr = 5
    gitlab_mr = 6


class ProjectTrigger(db.Model):
    __tablname__ = 'triggers'
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(128), nullable=False)
    type = db.Column(db.Integer)
    proj_id = db.Column(db.Integer, db.ForeignKey(Project.id), nullable=False)
    definition_repo = db.Column(db.String(512))
    definition_file = db.Column(db.String(512))
    secrets = db.Column(db.Text())

    project = db.relationship(Project)

    def __init__(self, user, ttype, project, def_repo, def_file, secrets):
        self.user = user
        self.type = ttype
        self.proj_id = project.id
        self.definition_repo = def_repo
        self.definition_file = def_file
        self.secrets = json.dumps(secrets)

    def as_json(self):
        return {
            'user': self.user,
            'type': TriggerTypes(self.type).name,
            'project': self.project.name,
            'definition_repo': self.definition_repo,
            'definition_file': self.definition_file or None,
            'secrets': json.loads(self.secrets or '{}'),
        }


class BuildStatus(enum.Enum):
    QUEUED = 1
    RUNNING = 2
    PASSED = 3
    FAILED = 4
    RUNNING_WITH_FAILURES = 5
    UPLOADING = 6
    PROMOTED = 7  # ie - the build got "released"


class StatusComparator(Comparator):
    def __eq__(self, other):
        return self.__clause_element__() == BuildStatus(other).value

    def in_(self, states):
        return self.__clause_element__().in_([x.value for x in states])


class StatusMixin(object):
    '''Using ENUM columns in the Database can be a real pain and difficult
       to provide migration logic for. This hack makes the column feel just
       like an ENUM in sqlalchemy but stores the column as an Integer.
    '''
    @hybrid_property
    def status(self):
        return BuildStatus(self._status)

    @status.comparator
    def status(cls):
        return StatusComparator(cls._status)

    @status.setter
    def status(self, status):
        if isinstance(status, str):
            self._status = BuildStatus[status].value
        else:
            self._status = status.value

    @property
    def complete(self):
        return self._status in (
            BuildStatus.PASSED.value, BuildStatus.FAILED.value)


class Build(db.Model, StatusMixin):
    __tablename__ = 'builds'
    id = db.Column(db.Integer, primary_key=True)

    build_id = db.Column(db.Integer, nullable=False)
    proj_id = db.Column(db.Integer, db.ForeignKey(Project.id), nullable=False)
    _status = db.Column(db.Integer)
    reason = db.Column(db.String(4096))

    name = db.Column(db.String(256))
    annotation = db.Column(db.Text())

    project = db.relationship(Project)
    runs = db.relationship('Run', cascade='save-update, merge, delete')
    status_events = db.relationship(
        'BuildEvents', cascade='save-update, merge, delete')

    __table_args__ = (
        db.UniqueConstraint('proj_id', 'build_id', name='build_id_uc'),
    )

    def __init__(self, project, build_id):
        self.proj_id = project.id
        self.build_id = build_id
        self.status = BuildStatus.QUEUED

    def as_json(self, detailed=False):
        url = url_for('api_build.build_get', proj=self.project.name,
                      build_id=self.build_id, _external=True)
        data = {
            'build_id': self.build_id,
            'url': url,
            'status': self.status.name,
            'runs': [x.as_json() for x in self.runs],
        }
        if self.name:
            data['name'] = self.name
        if self.status_events:
            data['created'] = self.status_events[0].time
            if self.complete:
                data['completed'] = self.status_events[-1].time
        if detailed:
            data['status_events'] = [{'time': x.time, 'status': x.status.name}
                                     for x in self.status_events]
            data['runs_url'] = url_for(
                'api_run.run_list', proj=self.project.name,
                build_id=self.build_id, _external=True)
            data['reason'] = self.reason
            data['annotation'] = self.annotation
        return data

    def refresh_status(self):
        locked_run = getattr(self, '_locked_run', None)
        if locked_run:
            # A new session is required, or the thread won't see updates done
            # from another thread. This is close related to Build.lock
            # This is probably dumb, and a DB expert would have a less hackish
            # approach, but its working.
            hack = db.create_session({})
            runs = [x for x in hack.query(Run).filter(Run.build_id == self.id)
                    if x.name != locked_run.name]
            runs.append(locked_run)
            hack.close()
        else:
            runs = self.runs
        status = get_cumulative_status(self, runs)
        if self.status != status:
            self.status = status
            db.session.add(BuildEvents(self, status))

    @contextlib.contextmanager
    def locked(self, run):
        # enables us to enforce sequential updates to the runs for a build.
        # This helps ensure we "complete" a build only once. This is closely
        # related to the hack session logic in Build.refresh_status
        list(Run.query.filter(Run.build_id == self.id).with_for_update())
        self._locked_run = run
        yield
        db.session.commit()

    def __repr__(self):
        return '<Build %d/%d: %s>' % (
            self.proj_id, self.build_id, self.status.name)

    @classmethod
    def _try_build_ids(clazz, project):
        last = clazz.query.filter_by(
            proj_id=project.id).order_by(-clazz.build_id).first()
        try_build_id = 1
        if last:
            try_build_id = last.build_id + 1

        # try 10 build ids to help avoid a race condition
        for build_id in range(try_build_id, try_build_id + 10):
            yield build_id

    @classmethod
    def create(clazz, project):
        last_exc = None
        for build_id in clazz._try_build_ids(project):
            try:
                b = Build(project, build_id)
                db.session.add(b)
                db.session.flush()
                db.session.add(BuildEvents(b, BuildStatus.QUEUED))
                db.session.commit()
                return b
            except IntegrityError as e:
                last_exc = e
                db.session.rollback()
        raise last_exc


class BuildEvents(db.Model, StatusMixin):
    __tablename__ = 'build_events'

    id = db.Column(db.Integer, primary_key=True)
    time = db.Column(db.DateTime)
    _status = db.Column(db.Integer)
    build_id = db.Column(db.Integer, db.ForeignKey(Build.id), nullable=False)

    def __init__(self, build, status):
        self.build_id = build.id
        self.status = status
        self.time = datetime.datetime.utcnow()

    def __repr__(self):
        return '<Status %s: %s>' % (self.time, self.status.name)


class Run(db.Model, StatusMixin):
    __tablename__ = 'runs'
    id = db.Column(db.Integer, primary_key=True)

    build_id = db.Column(db.Integer, db.ForeignKey(Build.id), nullable=False)
    name = db.Column(db.String(80))
    _status = db.Column(db.Integer)
    api_key = db.Column(db.String(80), nullable=False)
    trigger = db.Column(db.String(80))
    meta = db.Column(db.String(1024))

    worker_name = db.Column(db.String(1024), db.ForeignKey('workers.name'))

    host_tag = db.Column(db.String(1024))

    build = db.relationship(Build)
    status_events = db.relationship(
        'RunEvents', cascade='save-update, merge, delete')

    tests = db.relationship('Test', cascade='save-update, merge, delete')
    worker = db.relationship('Worker')

    __table_args__ = (
        # can't have the same named run for a single build
        db.UniqueConstraint('build_id', 'name', name='run_name_uc'),
    )

    def __init__(self, build, name, trigger=None):
        self.build_id = build.id
        self.name = name
        self.trigger = trigger
        self.status = BuildStatus.QUEUED
        self.api_key = ''.join(random.SystemRandom().choice(
            string.ascii_lowercase + string.ascii_uppercase + string.digits)
            for _ in range(32))

    def as_json(self, detailed=False):
        b = self.build
        p = b.project
        url = url_for('api_run.run_get', proj=p.name, build_id=b.build_id,
                      run=self.name, _external=True)
        log = url_for('api_run.run_get_artifact', proj=p.name,
                      build_id=b.build_id, run=self.name, path='console.log',
                      _external=True)
        data = {
            'name': self.name,
            'url': url,
            'status': self.status.name,
            'log_url': log,
        }
        if self.status_events:
            data['created'] = self.status_events[0].time
            if self.complete:
                data['completed'] = self.status_events[-1].time
        if self.host_tag:
            data['host_tag'] = self.host_tag
        if detailed:
            data['status_events'] = [{'time': x.time, 'status': x.status.name}
                                     for x in self.status_events]
            data['tests'] = url_for(
                'api_test.test_list', proj=p.name, build_id=b.build_id,
                run=self.name, _external=True)
        return data

    def set_status(self, status):
        if isinstance(status, str):
            status = BuildStatus[status]
        if self.status != status:
            self.status = status
            db.session.flush()
            db.session.refresh(self.build)
            self.build.refresh_status()
            db.session.flush()

            db.session.add(RunEvents(self, status))

    def __repr__(self):
        return '<Run %s: %s>' % (
            self.name, self.status.name)


class RunEvents(db.Model, StatusMixin):
    __tablename__ = 'run_events'

    id = db.Column(db.Integer, primary_key=True)
    time = db.Column(db.DateTime)
    _status = db.Column(db.Integer)
    run_id = db.Column(db.Integer, db.ForeignKey(Run.id), nullable=False)

    def __init__(self, run, status):
        self.run_id = run.id
        self.status = status
        self.time = datetime.datetime.utcnow()

    def __repr__(self):
        return '<Status %s: %s>' % (self.time, self.status.name)


class Test(db.Model, StatusMixin):
    __tablename__ = 'tests'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey(Run.id), nullable=False)
    name = db.Column(db.String(512), nullable=False)
    context = db.Column(db.String(1024))
    created = db.Column(db.DateTime, nullable=False)
    _status = db.Column(db.Integer)

    run = db.relationship(Run)
    results = db.relationship(
        'TestResult', cascade='save-update, merge, delete')

    def __init__(self, run, name, context, status=BuildStatus.QUEUED):
        self.run_id = run.id
        self.name = name
        self.context = context
        self.status = status
        self.created = datetime.datetime.utcnow()

    def as_json(self, detailed=False):
        r = self.run
        b = r.build
        p = b.project
        url = url_for('api_test.test_get', proj=p.name, build_id=b.build_id,
                      run=self.run.name, test=self.name, _external=True)
        data = {
            'name': self.name,
            'url': url,
            'status': self.status.name,
            'context': self.context,
            'created': self.created,
        }
        if detailed:
            results = []
            data['results'] = results
            for x in self.results:
                results.append({
                    'name': x.name,
                    'context': x.context,
                    'status': x.status.name,
                })
        return data

    def set_status(self, status):
        if isinstance(status, str):
            status = BuildStatus[status]
        if self.status != status:
            self.status = status
            db.session.flush()
            db.session.refresh(self.run)
            return get_cumulative_status(self.run, self.run.tests)

    @property
    def complete(self):
        for result in self.results:
            if not result.complete:
                return False
        return True

    def __repr__(self):
        return '<Test %s: %s>' % (
            self.name, self.status.name)


class TestResult(db.Model, StatusMixin):
    __tablename__ = 'test_results'

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey(Test.id), nullable=False)
    name = db.Column(db.String(1024), nullable=False)
    context = db.Column(db.String(1024))
    _status = db.Column(db.Integer)

    def __init__(self, test, name, context, status=BuildStatus.QUEUED):
        self.test_id = test.id
        self.name = name
        self.context = context
        self.status = status

    def __repr__(self):
        return '<TestResult %s: %s>' % (
            self.name, self.status.name)


class Worker(db.Model):
    __tablename__ = 'workers'

    name = db.Column(db.String(1024), primary_key=True)
    distro = db.Column(db.String(1024), nullable=False)
    mem_total = db.Column(db.BigInteger, nullable=False)
    cpu_total = db.Column(db.Integer, nullable=False)
    cpu_type = db.Column(db.String(1024), nullable=False)
    enlisted = db.Column(db.Boolean, nullable=False)
    api_key = db.Column(db.String(1024), nullable=False)
    concurrent_runs = db.Column(db.Integer, nullable=False)
    host_tags = db.Column(db.String(1024))
    online = db.Column(db.Boolean)

    def __init__(self, name, distro, mem_total, cpu_total, cpu_type, api_key,
                 concurrent_runs, host_tags):
        self.name = name
        self.distro = distro
        self.mem_total = mem_total
        self.cpu_total = cpu_total
        self.cpu_type = cpu_type
        self.api_key = api_key
        self.concurrent_runs = concurrent_runs
        if isinstance(host_tags, list):
            self.host_tags = ','.join(host_tags)
        else:
            self.host_tags = host_tags
        self.online = True
        self.enlisted = False

    def __repr__(self):
        return '<Worker %s - %s/%s>' % (self.name, self.online, self.enlisted)

    def as_json(self, detailed=False):
        url = url_for('api_worker.worker_get', name=self.name, _external=True)
        return {
            'name': self.name,
            'url': url,
            'distro': self.distro,
            'mem_total': self.mem_total,
            'cpu_total': self.cpu_total,
            'cpu_type': self.cpu_type,
            'enlisted': self.enlisted,
            'concurrent_runs': self.concurrent_runs,
            'host_tags': [x for x in self.host_tags.split(',')],
            'online': self.online,
        }

    @property
    def pings_log(self):
        return os.path.join(WORKER_DIR, self.name, 'pings.log')

    def ping(self, **kwargs):
        if not self.online:
            self.online = True
            db.session.commit()
        path = self.pings_log
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path))
        now = time.time()
        vals = ','.join(['%s=%s' % (k, v[0]) for k, v in kwargs.items()])
        with open(path, 'a') as f:
            f.write('%d: %s\n' % (now, vals))

        try:
            # this is a no-op if unconfigured
            with CarbonClient() as c:
                for k, v in kwargs.items():
                    try:
                        v = int(v[0])
                    except ValueError:
                        v = float(v[0])
                    c.send('workers.%s.%s' % (self.name, k), v, now)
        except:
            logging.exception('Unable to update metrics for ' + self.name)
