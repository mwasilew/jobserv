# Quick Start with docker-compose

docker-compose provides a quick way to evaluate and test the JobServ without
having to worry about Kubernetes. The following steps should get you going:

## Build a local docker image:
~~~
  docker build --build-arg APP_VERSION=local -t jobserv ./
~~~

## Initial launch
The first time you launch the system things will fail because the DB and
HAProxy take a bit to initialize and the other services will fail. Additionally
no database migrations will be applied. This okay, lets still launch with:
~~~
  docker-compose up
~~~

You'll see a bunch of stuff fly by in your terminal, but eventually the DB
will be up and an "api" service will be running.

## Perform a database migration
You'll now need to jump into another x-term and run the database migration
inside the "api" service. Here's a shortcut that should work:
~~~
  docker exec -it $(docker ps --filter name=api -q) flask db upgrade
~~~

## Really launch
Everything should now be ready to run a real JobServ. So stop the current
instance with a Ctrl-C where you ran the initial "docker-compose up" from.
Then run "docker-compose up" again and everything should start up cleanly.

## Registering a Worker
From another x-term or machine you need to verify the host is addressable. This
can be done by IPv4 or the system's host name. A quick check would look like:
~~~
  curl http://doanac-laptop/health/runs/
  curl http://192.168.0.104/health/runs/
~~~

Assuming a $JOBSERV_HOST is set to the instance you can register a host with:
~~~
  # pick a directory to host the runner in
  mkdir $HOME/jobserv-worker
  cd $HOME/jobserv-worker
  curl http://$JOBSERV_HOST/worker > jobserv_worker.py
  chmod +x jobserv_worker.py
  ./jobserv_worker.py register http://$JOBSERV_HOST 1 amd64
  ./jobserv_worker.py check  # check in and it will update the client

  # the worker is now registered but not *enlisted*, so it won't be able to
  # handle Runs. From the JobServ's host, view you host and then enlist it
  docker exec -it $(docker ps --filter name=api -q) flask worker list
  docker exec -it $(docker ps --filter name=api -q) flask worker enlist <host>
~~~

The worker is now ready, and you can have it manually check in with the JobServ
by running "./jobserv_worker.py check" or just run it in a loop with
"jobserv_worker.py loop".


## Setting up a Project
Setting up a project can be done from the JobServ container. A quick example
project could be:
~~~
  # Create a Project
  docker exec -it $(docker ps --filter name=api -q) \
    flask project create home-poller

  # Set up a Trigger that will kick off builds
  docker exec -it $(docker ps --filter name=api -q) \
    flask project add-trigger \
      -u doanac \
      -t git_poller \
      -s githubtok=<YOUR GITHUB TOKEN> \
      -r https://github.com/linaro-technologies-dev/ltd-jobserv-project-defs \
      -f mcuboot-upstream.yml \
      home-poller
~~~

## Forcing a Build
You can force the git poller to trigger a build by doing the following:
~~~
  # Get a shell inside the git-poller:
  docker exec -it $(docker ps --filter name=git-poller -q) /bin/sh

  # Look at the current SHA the poller sees the project at:
  cat /data/artifacts/git_poller_cache.json

  # Edit the current SHA with an older SHA from the project
  # The next time the poller runs, it will detect a change and trigger a build.
~~~
