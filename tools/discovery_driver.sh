#!/usr/bin/env bash
# Overnight driver for the declared site-discovery cache: waits for running crawl shards,
# crawls priority<=2 then repeatedly priority<=3 while DNS resolution is still progressing,
# and refreshes cache/site_discovery.jsonl.gz after every round.
cd "$(dirname "$0")/.."
LOG=data/work/discovery/driver.log
crawling() { pgrep -f "build_site_discovery.py crawl" >/dev/null; }
dns_running() { pgrep -f "build_site_discovery.py dns" >/dev/null; }
round() {  # $1 = max prio, $2 = tag
  echo "$(date +%T) round $2 start (max-prio $1)" >> $LOG
  for i in 0 1 2 3; do
    uv run python tools/build_site_discovery.py crawl --max-prio "$1" --concurrency 100 --shard $i --shards 4 \
      > data/work/discovery/crawl-$2-$i.log 2>&1 &
  done
  wait
  uv run python tools/build_site_discovery.py join >> $LOG 2>&1
  echo "$(date +%T) round $2 done" >> $LOG
}
while crawling; do sleep 30; done
uv run python tools/build_site_discovery.py join >> $LOG 2>&1
round 2 p2
n=0
while true; do
  was_running=0; dns_running && was_running=1
  n=$((n+1)); round 3 p3r$n
  [ $was_running -eq 0 ] && break
  sleep 5
done
echo "$(date +%T) driver finished" >> $LOG
