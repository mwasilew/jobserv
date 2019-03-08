## Build Queue Priority

By default all Builds are treated equally. JobServ's logic for handing out
queued Builds to workers is to take the oldest Run from the oldest Build. This
actually works fairly well.

However, as a system grows such as ci.foundries.io, you can see bursts of
low priority Builds such as the zephyr upstream testing. At the same time
an important build, like something to initiate a product release its created
and winds up queued behind all these less important builds.

Build Queue priorities were designed to help improve this situation.

## How it Works

A Project's Trigger has an optional value called `queue_priority`. Its default
value is 0 which is the lowest priority. Bigger is better here. When a Build
is triggered (ie a git poller change, GitHub PR, etc), the code sets an
attribute in the Run called `queue_prioritiy` that it gets from the trigger.

When Workers ask for queued runs the DB will be queried by:

 Run.query_priority (most important)
 Run.build.id (used to be most imporant)
 Run.id (which run in the build should go first)

*Side-effects?* - A really active server that (ab)uses high priority triggers
could cause resource starvation for lower priority runs.
