# Private site analytics

A standalone read-only dashboard for site operators. It consumes existing Nginx combined access logs and queries the game's SQLite database in read-only mode. It never imports the poker application, changes run state, calls a model, or writes to the game database. No changes or restarts to the backend, public gateway or frontend are required.

## Metrics

- Daily page loads: successful GETs to `/pokerbench/`, including refreshes. API polling, SSE, assets, failed loads and recognised bots/non-browser clients are excluded. SPA hash navigation does not generate another page load.
- Estimated daily visitors: a keyed hash of date, IP and user-agent. Shared networks and browser changes affect this estimate; it is not an exact person count. Daily unique counts cannot be summed into a cross-day unique count.
- Registrations: existing accounts, grouped by creation day. Deleted accounts no longer count.
- Tables and table creators: account-owned tables, grouped by creation day. Includes soft-deleted tables; excludes legacy unclaimed tables and formal benchmark tables.

Dates use Asia/Shanghai. Charts and tables show the last 30 days, with a 14-day chart. Only retained logs can be backfilled. Reloads by operators count as page loads; scripted/headless QA clients are excluded where recognised. Bot filtering is heuristic.

The analytics database persists aggregate counts, daily pseudonyms and file cursors, never raw IPs, user-agents, query strings, cookies, API keys or passwords. Existing source access-log retention remains controlled by Nginx. Logs rotated through rename/gzip keep their ingestion position. Incomplete trailing lines are processed on the next pass. Statistics read from a cached snapshot, not from a new game-database query for each dashboard request.

## Deployment

Use the existing project Python environment, and copy this directory to an independent service directory. Keep the following private environment file outside source control:

```ini
PB_STATS_DATA_DIR=/YOUR/ANALYTICS/DIRECTORY/data
PB_STATS_GAME_DB=/YOUR/GAME/DIRECTORY/data/pokerbench.sqlite
PB_STATS_ACCESS_LOG=/var/log/nginx/access.log
PB_STATS_USER=prophetlab
PB_STATS_PASSWORD=
PB_STATS_HASH_SECRET=
```

Generate independent random values (at least 32 random bytes) for the password and hashing secret. Do not reuse model keys or the benchmark admin token. Protect the environment file with mode 0600. The collector must have read permission for the logs and SQLite/WAL files. On Debian/Ubuntu, the example service grants the `adm` group only to the analytics process.

Adapt `analytics.service.example` and `nginx.conf.example`. Bind the new service to loopback, expose it only over HTTPS, and retain its own HTTP Basic administrator authentication. No public app navigation link is added. Credentials must not be embedded in URLs, tracked files or screenshots. The example limits CPU to 10%, uses low scheduling priority, and restricts writable filesystem access to its own data directory. Database queries are read-only, use a small page cache, and stop after a quarter-second query deadline.

Check the Nginx config before a graceful reload. Start only the new analytics service. Do not restart the game or gateway processes, or alter their listeners, keys, model registry, data or rate limits. Verify their PIDs/start times remain unchanged and formal actions continue advancing. If rollback is needed, remove only the new stats include and stop only the new service.
