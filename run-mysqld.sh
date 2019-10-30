#!/bin/bash -e

# A simple script to run mysql in a container

CONFD=$(mktemp -d)
trap "rm -rf $CONFD" EXIT


docker run -d --rm \
	--name jobserv-db \
	-v $CONFD:/docker-entrypoint-initdb.d \
	-p3306:3306 \
	-e CLUSTER_NAME=jobserv \
	-e MYSQL_ALLOW_EMPTY_PASSWORD=1 \
	percona/percona-xtradb-cluster:5.7.19

echo = MySQL docker logs
while read -r line
do
	echo "| $line"
	if [ "$line" == "MySQL init process done. Ready for start up." ] ; then
		break
	fi
done < <(docker logs -f jobserv-db 2>&1)

list_descendants ()
{
	local children=$(ps -o pid= --ppid "$1")

	for pid in $children ; do
		list_descendants "$pid"
	done
	echo "$children"
}

kill $(list_descendants $$) 2>/dev/null >/dev/null
sleep 10s # cause we know this will never cause problems down the road.
docker exec jobserv-db mysql -e "CREATE DATABASE IF NOT EXISTS jobserv"
echo "= MySQL container is ready. Run \`docker kill jobserv-db\` to terminate"
