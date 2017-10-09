# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

from flask import Blueprint, request

from jobserv.api.run import _authenticate_runner, _get_run, _handle_triggers
from jobserv.jsend import jsendify
from jobserv.models import BuildStatus, Run, Test, TestResult, db
from jobserv.storage import Storage

prefix = '/projects/<project:proj>/builds/<int:build_id>/runs/<run>/tests'
blueprint = Blueprint('api_test', __name__, url_prefix=prefix)


@blueprint.route('/', methods=('GET',))
def test_list(proj, build_id, run):
    r = _get_run(proj, build_id, run)
    return jsendify({'tests': [x.as_json(detailed=False) for x in r.tests]})


@blueprint.route('/<test>/', methods=('GET',))
def test_get(proj, build_id, run, test):
    r = _get_run(proj, build_id, run)
    t = Test.query.filter_by(run_id=r.id, name=test).first_or_404()
    return jsendify({'test': t.as_json(detailed=True)})


@blueprint.route('/<test>/', methods=('POST',))
def test_create(proj, build_id, run, test):
    r = _get_run(proj, build_id, run)
    _authenticate_runner(r)
    context = ''
    json = request.get_json()
    if json:
        context = json.get('context')
    db.session.add(Test(r, test, context))
    db.session.commit()
    return jsendify({})


@blueprint.route('/<test>/', methods=('PUT',))
def test_update(proj, build_id, run, test):
    r = _get_run(proj, build_id, run)
    _authenticate_runner(r)
    t = Test.query.filter_by(
        name=test
    ).filter(
        Test.run.has(Run.id == r.id)
    )
    context = request.args.get('context')
    if context:
        t = t.filter(Test.context == context)
    t = t.first_or_404()

    json = request.get_json()
    if json:
        msg = json.get('message')
        status = json.get('status')
        results = json.get('results', [])
        storage = Storage()

        if msg:
            with storage.console_logfd(r, 'a') as f:
                f.write(msg)
        if results:
            for tr in results:
                s = BuildStatus[tr['status']]
                db.session.add(TestResult(t, tr['name'], tr.get('context'), s))
            db.session.commit()
        if status:
            run_status = t.set_status(status)
            if run_status in (BuildStatus.PASSED, BuildStatus.FAILED):
                storage.copy_log(r)
                _handle_triggers(storage, r)
            if run_status is not None:
                t.run.set_status(run_status)
            db.session.commit()

    return jsendify({'complete': t.run.complete})
