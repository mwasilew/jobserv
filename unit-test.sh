#!/bin/sh -ex

HERE=$(dirname $(readlink -f $0))
cd $HERE

VENV=$(mktemp -d)
trap "rm -rf $VENV" EXIT

python3 -m venv $VENV
$VENV/bin/pip3 install -U pip
$VENV/bin/pip3 install -U setuptools
$VENV/bin/pip3 install -r requirements.txt

$VENV/bin/pip3 install junitxml==0.7 python-subunit==1.3.0

set -o pipefail
PYTHONPATH=./ $VENV/bin/python3 -m subunit.run discover \
	| $VENV/bin/subunit2junitxml --no-passthrough \
	| tee /archive/junit.xml
