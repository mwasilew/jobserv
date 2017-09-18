# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

from importlib import import_module

from flask import Blueprint

from jobserv.jsend import get_or_404, jsendify
from jobserv.models import Project
from jobserv.settings import PERMISSIONS_MODULE

permissions = import_module(PERMISSIONS_MODULE)

blueprint = Blueprint('api_project', __name__, url_prefix='/projects')


@blueprint.route('/', methods=('GET',))
def project_list():
    return jsendify(
        {'projects': [x.as_json() for x in permissions.projects_list()]})


@blueprint.route('/<project:proj>/', methods=('GET',))
def project_get(proj):
    p = get_or_404(Project.query.filter_by(name=proj))
    return jsendify({'project': p.as_json(detailed=True)})
