#!/bin/sh -ex

HERE=$(dirname $(readlink -f $0))
cd $HERE

VENV=$(mktemp -d)
trap "rm -rf $VENV" EXIT

python3 -m venv $VENV
$VENV/bin/pip3 install -U pip
$VENV/bin/pip3 install -U setuptools
$VENV/bin/pip3 install -r requirements.txt

PYTHONPATH=./ $VENV/bin/python3 -m unittest discover -v
