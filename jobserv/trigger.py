import logging
import traceback

import yaml

from flask import url_for

from jobserv.jsend import ApiError
from jobserv.models import Build, BuildStatus, Run, db
from jobserv.project import ProjectDefinition
from jobserv.settings import BUILD_URL_FMT
from jobserv.storage import Storage


def trigger_runs(storage, projdef, build, trigger, params, secrets):
    name_fmt = trigger.get('run-names')
    try:
        for run in trigger['runs']:
            name = run['name']
            if name_fmt:
                name = name_fmt.format(name=name)
            r = Run(build, name, trigger['name'])
            db.session.add(r)
            db.session.flush()
            rundef = projdef.get_run_definition(
                r, run, trigger['type'], params, secrets)
            storage.set_run_definition(r, rundef)
    except ApiError:
        logging.exception('ApiError while triggering runs for: %r', trigger)
        raise
    except Exception as e:
        logging.exception('Unexpected error creating runs for: %r', trigger)
        build.status = BuildStatus.FAILED
        db.session.commit()
        raise ApiError(500, str(e) + "\n" + traceback.format_exc())


def _fail_unexpected(build, exception):
    r = Run(build, 'build-failure')
    db.session.add(r)
    r.set_status(BuildStatus.FAILED)
    db.session.commit()
    storage = Storage()
    with storage.console_logfd(r, 'a') as f:
        f.write('Unexpected error prevented build from running:\n')
        f.write(str(exception))
    storage.copy_log(r)

    if BUILD_URL_FMT:
        url = BUILD_URL_FMT.format(
            project=build.project.name, build=build.build_id)
    else:
        url = url_for('api_run.run_get_artifact', proj=build.project.name,
                      build_id=build.build_id, run=r.name, path='console.log')

    exception = ApiError(500, str(exception))
    exception.resp.headers.extend({'Location': url})
    return exception


def trigger_build(project, reason, trigger_name, params, secrets, proj_def):
    b = Build.create(project)
    try:
        b.reason = reason
        storage = Storage()
        storage.create_project_definition(
            b, yaml.dump(proj_def, default_flow_style=False))
        proj_def = ProjectDefinition(proj_def)
        trigger = proj_def.get_trigger(trigger_name)
        if not trigger:
            raise KeyError('Project(%s) does not have a trigger: %s' % (
                           project, trigger_name))
    except Exception as e:
        raise _fail_unexpected(b, e)

    trigger_runs(storage, proj_def, b, trigger, params, secrets)
    db.session.commit()
    return b
