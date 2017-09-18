# A Secure API Example

The JobServ was intentionally built with no security model. Different users
have different security needs and implementations so a flexible generic model
was created to allow users to inject a authentication and authorization.

The main way to accomplish this is by providing a module that implements three
methods like jobserv.permissions:
~~~
def projects_list():
    '''Allow anyone to see if a project exists.'''
    return Project.query


def project_can_access(project_path):
    '''Allow anyone to access a project.'''
    return True


def health_can_access(health_path):
    '''Allow anyone to access to the health endpoints.'''
    return True
~~~

The module can be selected at runtime by overriding the PERMISSIONS_MODULE
environment variable from its default "jobserv.permissions" to the custom
module provided by the user. eg PERMISSIONS_MODULE=custom_jobserv.permissions


## Concrete Example

By taking advantage of the "PROJECT_NAME_REGEX" setting, a more complete
solution might be defined where projects could be scoped by users and teams.
For example URLs could be built like:
~~~
  # Set this in the environment
  PROJECT_NAME_REGEX='(?:\S+\/\S+^/)'

  # Now project names must have a single "/", like userX/project1
~~~

A permissions module to compliment this project naming scheme might be:
~~~
from flask import request

def _get_user():
    # TODO - not secure
    user = request.headers.get('user-name')
    if not user:
        abort(404)
    return

def projects_list():
    '''Return projects that begin with the user name'''
    user = _get_user() + '/'
    return Project.query.filter(Project.name.startswith(user))


def project_can_access(project_path):
    '''project_path must match the user name. eg
       user = bob, project_path = bob/blah, return True
       user = bob, project_path = fred/blah, return False
    '''
    return project_path.starts_with(_get_user() + '/')


def health_can_access(health_path):
    '''Only allow bob and fred to view'''
    return _get_user() in ('bob', 'fred')
~~~
