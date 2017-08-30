# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import json
import re

import yaml

from flask import Blueprint, request, send_file, url_for

from jobserv.storage import Storage
from jobserv.jsend import ApiError, get_or_404, jsendify
from jobserv.models import (
    db, Build, BuildStatus, Project, Run, Test, TestResult
)
from jobserv.project import ProjectDefinition
from jobserv.sendmail import notify_build_complete
from jobserv.trigger import trigger_runs

blueprint = Blueprint('api_run', __name__,
                      url_prefix='/projects/<proj>/builds/<int:build_id>/runs')


@blueprint.route('/', methods=('GET',))
def run_list(proj, build_id):
    p = get_or_404(Project.query.filter_by(name=proj))
    b = get_or_404(Build.query.filter_by(project=p, build_id=build_id))
    return jsendify({'runs': [x.as_json(detailed=False) for x in b.runs]})


def _get_run(proj, build_id, run):
    p = get_or_404(Project.query.filter_by(name=proj))
    b = get_or_404(Build.query.filter_by(project=p, build_id=build_id))
    return Run.query.filter_by(
        name=run
    ).filter(
        Run.build.has(Build.id == b.id)
    ).first_or_404()


@blueprint.route('/<run>/', methods=('GET',))
def run_get(proj, build_id, run):
    r = _get_run(proj, build_id, run)
    data = r.as_json(detailed=True)
    artifacts = []
    for a in Storage().list_artifacts(r):
        u = url_for('api_run.run_get_artifact', proj=proj, build_id=build_id,
                    run=run, path=a, _external=True)
        artifacts.append(u)
    data['artifacts'] = artifacts
    return jsendify({'run': data})


def _create_triggers(projdef, storage, build, params, secrets, triggers):
    for trigger in triggers:
        run_names = trigger.get('run-names')
        trigger = projdef.get_trigger(trigger['name'])
        trigger['run-names'] = run_names
        trigger_runs(storage, projdef, build, trigger, params, secrets)


def _handle_build_complete(projdef, storage, build, params, secrets, trigger):
    email = trigger.get('email')
    if email:
        if build.status == BuildStatus.FAILED \
                or not email.get('only_failures'):
            notify_build_complete(build, email['users'])
    if build.status == BuildStatus.PASSED:
        # we don't want to pass "trigger params" since this is a build-level
        # trigger, but we do want the context of the build url, so
        # convert http://foo/build/1/runs/ into http://foo/build/1/
        url = params['H_TRIGGER_URL']
        params = {
            'H_TRIGGER_URL': url[:url.find('/runs/') + 1],
        }
        triggers = trigger.get('triggers', [])
        if triggers:
            _create_triggers(projdef, storage, build, params, secrets,
                             trigger.get('triggers', []))
            db.session.flush()
            build.refresh_status()


def _handle_triggers(storage, run):
    if not run.complete or not run.trigger:
        return

    projdef = ProjectDefinition(
        yaml.load(storage.get_project_definition(run.build)))
    rundef = json.loads(storage.get_run_definition(run))
    secrets = rundef.get('secrets')
    params = rundef.get('env', {})
    params['H_TRIGGER_URL'] = request.url

    run_trigger = projdef.get_trigger(run.trigger)
    for rt in run_trigger['runs']:
        if rt['name'] == run.name:
            if run.status == BuildStatus.PASSED:
                _create_triggers(projdef, storage, run.build, params, secrets,
                                 rt.get('triggers', []))
            if run.build.complete:
                _handle_build_complete(projdef, storage, run.build, params,
                                       secrets, run_trigger)


def _failed_tests(storage, run):
    failures = False
    rundef = json.loads(storage.get_run_definition(run))
    grepping = rundef.get('test-grepping')
    if grepping:
        test_pat = grepping.get('test-pattern')
        if test_pat:
            test_pat = re.compile(test_pat)
        res_pat = re.compile(grepping['result-pattern'])
        fixups = grepping.get('fixupdict', {})
        cur_test = None
        with storage.console_logfd(run, 'r') as f:
            for line in f.readlines():
                if test_pat:
                    m = test_pat.match(line)
                    if m:
                        if cur_test:
                            statuses = [x.status for x in cur_test.results]
                            if BuildStatus.FAILED in statuses:
                                cur_test.status = BuildStatus.FAILED
                        cur_test = Test(
                            run, m.group('name'), grepping['test-pattern'],
                            BuildStatus.PASSED)
                        db.session.add(cur_test)
                        db.session.flush()
                m = res_pat.match(line)
                if m:
                    result = m.group('result')
                    result = fixups.get(result, result)
                    if result == 'FAILED':
                        failures = True
                    if not cur_test:
                        cur_test = Test(
                            run, 'default', None, BuildStatus.PASSED)
                        db.session.add(cur_test)
                        db.session.flush()
                    db.session.add(
                        TestResult(cur_test, m.group('name'), None, result))
        db.session.commit()
    return failures


def _authenticate_runner(run):
    key = request.args.get('apikey')
    if key and key == run.api_key:
        return
    key = request.headers.get('Authorization', None)
    if not key:
        raise ApiError(401, {'message': 'No Authorization header provided'})
    parts = key.split(' ')
    if len(parts) != 2 or parts[0] != 'Token':
        raise ApiError(401, {'message': 'Invalid Authorization header'})
    if parts[1] != run.api_key:
        raise ApiError(401, {'message': 'Incorrect API key'})
    if run.complete:
        raise ApiError(401, {'message': 'Run has already completed'})


@blueprint.route('/<run>/', methods=('POST',))
def run_update(proj, build_id, run):
    r = _get_run(proj, build_id, run)
    _authenticate_runner(r)

    storage = Storage()
    if request.data:
        with storage.console_logfd(r, 'ab') as f:
            f.write(request.data)

    metadata = request.headers.get('X-RUN-METADATA')
    if metadata:
        r.meta = metadata
        db.session.commit()

    status = request.headers.get('X-RUN-STATUS')
    if status:
        status = BuildStatus[status]
        if r.status != status:
            if status in (BuildStatus.PASSED, BuildStatus.FAILED):
                if _failed_tests(storage, r):
                    status = BuildStatus.FAILED
                storage.copy_log(r)
            with r.build.locked(r):
                r.set_status(status)
                if r.complete:
                    _handle_triggers(storage, r)

    return jsendify({})


@blueprint.route('/<run>/.rundef.json', methods=('GET',))
def run_get_definition(proj, build_id, run):
    r = _get_run(proj, build_id, run)
    rundef = Storage().get_run_definition(r)
    try:
        _authenticate_runner(r)
    except ApiError:
        # This is an unauthenticated request, remove the secrets
        rundef = json.loads(rundef)
        secrets = rundef.get('secrets')
        if secrets:
            rundef['secrets'] = {k: 'TODO' for k, v in secrets.items()}
        del rundef['api_key']
        rundef = json.dumps(rundef, indent=2)

    return rundef, 200, {'Content-Type': 'application/json'}


@blueprint.route('/<run>/<path:path>', methods=('GET',))
def run_get_artifact(proj, build_id, run, path):
    # TODO Secure this call
    r = _get_run(proj, build_id, run)
    if r.complete:
        storage = Storage()
        if path.endswith('.html'):
            # we are probably trying to render a static site like a build of
            # ltd-docs. Return its content rather than a redirect so it will
            # render in the browser
            content = storage.get_artifact_content(r, path)
            return content, 200, {'Content-Type': 'text/html'}
        return storage.get_download_response(request, r, path)

    if path != 'console.log':
        raise ApiError(
            404, {'message': 'Run in progress, no artifacts available'})

    if r.status == BuildStatus.QUEUED:
        return '', 200, {'Content-Type': 'text/plain'}
    return send_file(Storage().console_logfd(r, 'rb'), mimetype='text/plain')


@blueprint.route('/<run>/create_signed', methods=('POST',))
def run_upload(proj, build_id, run):
    r = _get_run(proj, build_id, run)
    _authenticate_runner(r)

    data = request.get_json()
    urls = {}
    if data:
        # determine url expiration, default 1800 = 30 minues
        expiration = request.headers.get('X-URL-EXPIRATION', 1800)
        urls = Storage().generate_signed(r, data, expiration)

    return jsendify({'urls': urls})
