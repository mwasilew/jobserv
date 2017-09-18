# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>


from jobserv.models import Project


def projects_list():
    '''Allow anyone to see if a project exists.'''
    return Project.query


def project_can_access(project_path):
    '''Allow anyone to access a project.'''
    return True


def health_can_access(health_path):
    '''Allow anyone to access to the health endpoints.'''
    return True
