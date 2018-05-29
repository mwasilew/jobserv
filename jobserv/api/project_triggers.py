# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

from flask import Blueprint, request

from jobserv.jsend import jsendify
from jobserv.models import ProjectTrigger, TriggerTypes
from jobserv.permissions import assert_internal_user

blueprint = Blueprint(
    'api_project_triggers', __name__, url_prefix='/project-triggers')


@blueprint.route('/', methods=('GET',))
def project_trigger_list():
    assert_internal_user()
    t = request.args.get('type')
    if t:
        t = TriggerTypes[t].value
        query = ProjectTrigger.query.filter(ProjectTrigger.type == t)
    else:
        query = ProjectTrigger.query.all()
    return jsendify([x.as_json() for x in query])
