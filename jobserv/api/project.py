# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

from flask import Blueprint, request, url_for

from jobserv.flask import permissions
from jobserv.jsend import ApiError, get_or_404, jsendify, paginate_custom
from jobserv.models import (
    Build, BuildStatus, Project, ProjectTrigger, Run, TriggerTypes, db)

blueprint = Blueprint('api_project', __name__, url_prefix='/projects')


@blueprint.route('/', methods=('GET',))
def project_list():
    return jsendify(
        {'projects': [x.as_json() for x in permissions.projects_list()]})


@blueprint.route('/', methods=('POST',))
def project_create():
    d = request.get_json() or {}
    proj = d.get('name')
    if not proj:
        raise ApiError(401, 'Missing required parameter: "name"')

    permissions.assert_internal_user()
    db.session.add(Project(proj))
    db.session.commit()

    url = url_for('api_project.project_get', proj=proj, _external=True)
    return jsendify({'url': url}, 201)


@blueprint.route('/<project:proj>/', methods=('GET',))
def project_get(proj):
    p = get_or_404(Project.query.filter_by(name=proj))
    return jsendify({'project': p.as_json(detailed=True)})


@blueprint.route('/<project:proj>/history/<run>/', methods=('GET',))
def project_run_history(proj, run):
    q = Run.query.join(
        Build, Project
    ).filter(
        Project.name == proj, Run.name == run
    ).order_by(
        -Build.id
    )

    def render(run):
        r = run.as_json(detailed=True)
        r['build'] = run.build.build_id
        for x in reversed(run.status_events):
            if x.status == BuildStatus.RUNNING:
                d = run.status_events[-1].time - x.time
                r['duration_seconds'] = d.total_seconds()
                break
        return r

    return paginate_custom('runs', q, render)


@blueprint.route('/<project:proj>/triggers/', methods=('GET',))
def project_trigger_list(proj):
    permissions.assert_internal_user()
    p = get_or_404(Project.query.filter_by(name=proj))
    triggers = p.triggers

    t = request.args.get('type')
    if t:
        triggers = [x for x in triggers if x.type == TriggerTypes[t].value]
    return jsendify([x.as_json() for x in triggers])


@blueprint.route('/<project:proj>/triggers/', methods=('POST',))
def project_create_trigger(proj):
    u = permissions.assert_internal_user()
    p = get_or_404(Project.query.filter_by(name=proj))

    d = request.get_json() or {}
    ttype = d.pop('type')
    if not ttype:
        raise ApiError(401, 'Missing parameter: type')
    ttype = TriggerTypes[ttype].value

    owner = d.pop('owner')
    if u:
        owner = str(u)
    dr = df = None
    try:
        dr = d.pop('definition_repo')
        df = d.pop('definition_file')
    except KeyError:
        pass

    db.session.add(ProjectTrigger(owner, ttype, p, dr, df, d))

    db.session.commit()
    return jsendify({}, 201)
