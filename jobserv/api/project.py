# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

from flask import Blueprint

from jobserv.jsend import get_or_404, jsendify, paginate
from jobserv.models import Project

blueprint = Blueprint('api_project', __name__, url_prefix='/projects')


@blueprint.route('/', methods=('GET',))
def project_list():
    return paginate('projects', Project.query)


@blueprint.route('/<project:proj>/', methods=('GET',))
def project_get(proj):
    p = get_or_404(Project.query.filter_by(name=proj))
    return jsendify({'project': p.as_json(detailed=True)})
