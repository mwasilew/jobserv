# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import datetime
import fnmatch
import json
import os
import random
import string
import subprocess
import sys

import click
import requests

from jobserv.flask import create_app
from jobserv.git_poller import run
from jobserv.lava_reactor import run_reaper
from jobserv.models import (
    Project, ProjectTrigger, TriggerTypes, Worker, db)
from jobserv.sendmail import email_on_exception
from jobserv.storage import Storage
from jobserv.worker import run_monitor_workers

app = create_app()


@app.cli.command()
def run_lava_reaper():
    run_reaper()


@app.cli.command()
def run_git_poller():
    run()


@app.cli.command()
def monitor_workers():
    run_monitor_workers()


@app.cli.group()
def project():
    pass


@project.command('list')
@click.argument('pattern', required=False)
def project_list(pattern=None):
    for p in Project.query.all():
        if pattern and not fnmatch.fnmatch(p.name, pattern):
            continue
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


def _register_gitlab_hook(project, url, api_token, hook_token, server_name):
    data = {
        'url': 'https://%s/gitlab/%s/' % (server_name, project),
        'merge_requests_events': True,
        'note_events': True,
        'enable_ssl_verification': True,
        'token': hook_token,
    }
    headers = {'PRIVATE-TOKEN': api_token}

    resp = requests.post(url, json=data, headers=headers)
    if resp.status_code != 201:
        sys.exit('ERROR adding webhook: %d\n%s' % (
            resp.status_code, resp.text))


def _register_github_hook(project, url, api_token, hook_token, server_name):
    data = {
        'name': 'web',
        'active': True,
        'events': [
            'pull_request',
            'pull_request_review_comment',
            'issue_comment',
        ],
        'secret': hook_token,
        'config': {
            'url': 'https://%s/github/%s/' % (server_name, project),
            'content_type': 'json',
        }
    }
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'token ' + api_token,
    }

    resp = requests.post(url, json=data, headers=headers)
    if resp.status_code != 201:
        sys.exit('ERROR adding webhook: %d\n%s' % (
            resp.status_code, resp.text))


def add_trigger(project, user, type, secrets, definition_repo, definition_file,
                hook_url, server_name):
    key = ''.join(random.SystemRandom().choice(
        string.ascii_lowercase + string.ascii_uppercase + string.digits)
        for _ in range(32))
    secret_map = {'webhook-key': key}
    for secret in (secrets or []):
        k, v = secret.split('=', 1)
        secret_map[k.strip()] = v.strip()

    type = TriggerTypes[type].value
    p = Project.query.filter(Project.name == project).first()
    if not p:
        click.echo('No such project: %s' % project)
        return
    db.session.add(ProjectTrigger(
        user, type, p, definition_repo, definition_file, secret_map))

    if type == TriggerTypes.gitlab_mr.value:
        if 'gitlabtok' not in secret_map or 'gitlabuser' not in secret_map:
            raise ValueError(
                '"gitlabtok" and "gitlabuser" are required secrets')
        _register_gitlab_hook(
            project, hook_url, secret_map['gitlabtok'], key, server_name)
    elif type == TriggerTypes.github_pr.value:
        if 'githubtok' not in secret_map:
            raise ValueError('"githubtok" is required in secrets')
        _register_github_hook(
            project, hook_url, secret_map['githubtok'], key, server_name)

    db.session.commit()


@project.command('add-trigger')
@click.argument('project')
@click.option('--user', '-u', required=True)
@click.option('--type', '-t', required=True,
              type=click.Choice([x.name for x in TriggerTypes]))
@click.option('--secret', '-s', 'secrets', multiple=True)
@click.option('--definition_repo', '-r', default=None)
@click.option('--definition_file', '-f', default=None)
@click.option('--hook_url', default=None)
@click.option('--server_name', default=None)
def project_add_trigger(project, user, type, secrets=None,
                        definition_repo=None, definition_file=None,
                        hook_url=None, server_name=None):
    add_trigger(project, user, type, secrets, definition_repo,
                definition_file, hook_url, server_name)


@app.cli.group()
def worker():
    pass


@worker.command('list')
def worker_list():
    print('Worker\tEnlisted\tOnline')
    for w in Worker.query.all():
        print('%s\t%s\t%s' % (w.name, w.enlisted, w.online))


@worker.command('enlist')
@click.argument('name')
def worker_enlist(name):
    w = Worker.query.filter(Worker.name == name).one()
    w.enlisted = True
    db.session.commit()


@app.cli.command('backup')
@email_on_exception('jobserv: DB Backup Failed')
def backup():
    command = (
        'mysqldump',
        '--user=' + db.engine.url.username,
        '--password=' + db.engine.url.password,
        '--host=' + db.engine.url.host,
        db.engine.url.database
    )
    backup = '/data/jobserv-db.sql-%s' % datetime.datetime.now()
    with open(backup, 'w') as f:
        subprocess.check_call(command, stdout=f)

    Storage()._create_from_file(
        'backups/' + os.path.basename(backup), backup, 'application/x-sql')
    os.unlink(backup)
