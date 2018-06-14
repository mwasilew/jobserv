# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

from flask import Blueprint, request, url_for

from jobserv.flask import permissions
from jobserv.jsend import ApiError, get_or_404, jsendify
from jobserv.models import Project, db

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
