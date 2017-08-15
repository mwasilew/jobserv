from jobserv.api.project import blueprint as project_bp
from jobserv.jsend import ApiError

BLUEPRINTS = (project_bp,)


def register_blueprints(app):
    for bp in BLUEPRINTS:
        @bp.errorhandler(ApiError)
        def api_error(e):
            return e.resp
        app.register_blueprint(bp)
