import json

import click

from jobserv.flask import create_app
from jobserv.models import (
    Project, ProjectTrigger, TriggerTypes, db)
from jobserv.worker import run_monitor_workers

app = create_app()


@app.cli.command()
def monitor_workers():
    run_monitor_workers()


@app.cli.group()
def project():
    pass


@project.command('list')
def project_list():
    for p in Project.query.all():
        click.echo('Project: ' + p.name)
        triggers = ProjectTrigger.query.filter(ProjectTrigger.project == p)
        if triggers.count():
            click.echo(' Triggers:')
            for t in triggers:
                t = json.dumps(t.as_json(), indent=2)
                click.echo('  ' + '\n  '.join(t.split('\n')))


@project.command('create')
@click.argument('name')
def project_create(name):
    db.session.add(Project(name))
    db.session.commit()


@project.command('add-trigger')
@click.argument('project')
@click.option('--user', '-u', required=True)
@click.option('--type', '-t', required=True,
              type=click.Choice([x.name for x in TriggerTypes]))
@click.option('--secret', '-s', 'secrets', multiple=True)
@click.option('--definition_repo', '-r', default=None)
@click.option('--definition_file', '-f', default=None)
def project_add_trigger(project, user, type, secrets=None,
                        definition_repo=None, definition_file=None):
    secret_map = {}
    for secret in (secrets or []):
        k, v = secret.split('=')
        secret_map[k.strip()] = v.strip()

    type = TriggerTypes[type].value
    p = Project.query.filter(Project.name == project).first()
    if not p:
        click.echo('No such project: %s' % project)
        return
    db.session.add(ProjectTrigger(
        user, type, p, definition_repo, definition_file, secret_map))
    db.session.commit()
