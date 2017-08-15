from flask import Blueprint, request, url_for

from jobserv.storage import Storage
from jobserv.internal_requests import internal_api
from jobserv.jsend import get_or_404, jsendify, paginate
from jobserv.models import Build, BuildStatus, Project
from jobserv.trigger import trigger_build

blueprint = Blueprint(
    'api_build', __name__, url_prefix='/projects/<proj>/builds')


@blueprint.route('/', methods=('GET',))
def build_list(proj):
    p = get_or_404(Project.query.filter(Project.name == proj))
    q = Build.query.filter_by(proj_id=p.id).order_by(Build.id.desc())
    return paginate('builds', q)


@blueprint.route('/', methods=('POST',))
@internal_api
def build_create(proj):
    p = Project.query.filter(Project.name == proj).first_or_404()
    d = request.get_json() or {}
    b = trigger_build(p, d.get('reason'), d.get('trigger-name'),
                      d.get('params'), d.get('secrets'),
                      d.get('project-definition'))
    url = url_for('api_build.build_get',
                  proj=p.name, build_id=b.build_id, _external=True)
    return jsendify({'url': url}, 201)


@blueprint.route('/<int:build_id>/', methods=('GET',))
def build_get(proj, build_id):
    p = get_or_404(Project.query.filter(Project.name == proj))
    b = get_or_404(
        Build.query.filter(Build.project == p, Build.build_id == build_id))
    return jsendify({'build': b.as_json(detailed=True)})


@blueprint.route('/<int:build_id>/project.yml', methods=('GET',))
def build_get_project_definition(proj, build_id):
    p = get_or_404(Project.query.filter(Project.name == proj))
    b = get_or_404(
        Build.query.filter(Build.project == p, Build.build_id == build_id))
    pd = Storage().get_project_defintion(b)
    return pd, 200, {'Content-Type': 'text/yaml'}


@blueprint.route('/latest/', methods=('GET',))
def build_get_latest(proj):
    '''Return the most recent successful build'''
    b = get_or_404(
        Build.query.join(
            Build.project
        ).filter(
            Project.name == proj,
            Build.status == BuildStatus.PASSED,
        ).order_by(
            Build.id.desc()
        )
    )
    return jsendify({'build': b.as_json(detailed=True)})
