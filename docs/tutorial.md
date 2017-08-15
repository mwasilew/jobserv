# Tutorial

This is a simple guide to help people get started writing project definition
files and testing them inside the JobServ simulator.

## Download the simulator
wget -O ./simulator.py https://api.linarotechnologies.org/simulator
chmod +x ./simulator.py

## Create a minimal definition
Here is a simple template to test the python flake8 project:
~~~
# flake8.yml
timeout: 5
triggers:
  - name: git
    type: git_poller
    params:
      GIT_URL: https://github.com/PyCQA/flake8
      GIT_POLL_REFS: "refs/heads/master"
    runs:
      - name: flake8
        container: linarotechnologies/python-builder
        script: flake8

scripts:
  flake8: |
    #!/bin/sh -ex
    pip3 install flake8
    flake8 ./
~~~

This project definition runs the Flake8 linter against the Flake8
code base. The "git_poller" handler in the simulator will execute the flake8
"script" in a directory containing a clone of the Flake8 Git repository.

## Create and run the simulator
The following definition can be tested using the JobServ simulator with:
~~~
mkdir /tmp/flake8
./simulator.py create -d /tmp/flake8.yml -t git -w /tmp/flake8 -r flake8 -p GIT_SHA=01f8824490a58
./simulator.py run -w /tmp/flake8
~~~

## Add another Run to the project
Now let's add a run that will run the unit tests included with the project:
~~~
# in the runs section, add this after the flake8 run
      - name: unit-test
        container: linarotechnologies/python-builder
        script: unit-test

# in the scripts section add this:
  unit-test: |
    #!/bin/sh -ex
    python3 ./setup.py test
~~~

The unit-test run can now be tested in the simulator with:
~~~
./simulator.py create -d /tmp/flake8.yml -t git -w /tmp/flake8 -r unit-test -p GIT_SHA=01f8824490a58
./simulator.py run -w /tmp/flake8
~~~

## Runs with loop-on

A run may use the loop-on directive:
~~~
      - name: unit-test
        container: linarotechnologies/python-builder
        loop-on:
          - param: ARCH
            values: [ARM, ARM64, x86]
        script: unit-test
~~~

The simulator doesn't understand loop-on param/values to choose, so they need to be specified with something like:
~~~
./simulator.py create -d /tmp/flake8.yml -t git -w /tmp/flake8 -r unit-test -p GIT_SHA=01f8824490a58 -p ARCH=ARM
./simulator.py run -w /tmp/flake8
~~~
