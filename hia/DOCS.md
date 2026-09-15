# Home Intelligent Assistant

Learns how your household behaves from Home Assistant's own data and
progressively takes over automation decisions. This add-on runs `hia serve`:
it connects to Home Assistant, stores what it sees, and gives you a web UI
(open this add-on from the sidebar) to watch it happen.

**Current state: the observability shell.** It watches and shows you live
state — it does not act on anything yet. See the project's own docs
(<https://github.com/wrcrooks/Home-Intelligent-Assistant/tree/main/docs>)
for where things actually stand; the short version is that ingestion and the
web UI are built, and the part that starts making decisions hasn't been
built yet.

## Configuration

**Home Assistant URL** (`ha_url`) — defaults to `http://homeassistant:8123`,
which is correct for most installs (this add-on runs on the same host as
Home Assistant).

**Long-lived access token** (`ha_token`) — required. Create one from your
Home Assistant profile: click your name (bottom left) → Security →
Long-Lived Access Tokens → Create Token. Paste it here.

**Log level** (`log_level`) — `info` is right for normal use; `debug` if
something looks wrong and you want to see more.

## What it does with your data

Everything stays local, on this add-on's own persistent storage — nothing
leaves this machine. See the project's `docs/01-scope.md` and
`docs/05-provenance.md` for the full design reasoning, including why
predictions are never scored against events that Home Assistant's own
automations (or Node-RED) caused.

## Troubleshooting

**Install/build fails with something like:**

```
Can't pull image docker:28.3.3-cli: [500] Head "https://registry-1.docker.io/...":
Get "https://auth.docker.io/token?...": net/http: TLS handshake timeout
```

This is Supervisor's own internal build helper failing to reach Docker Hub — it
happens *before* Supervisor even starts building this add-on, so it isn't
anything specific to hia. **Just retry the install; it usually succeeds the
second time.** If it keeps failing:

- Check your Home Assistant host's clock is correct (a skewed clock can make
  TLS certificate checks fail in exactly this way).
- If your network has IPv6 enabled, try disabling it temporarily — a poorly
  routed IPv6 path is the most common real-world cause of this specific error.
- Check that your DNS/network isn't blocking or filtering
  `registry-1.docker.io` or `auth.docker.io`.

If it fails again at a *different* image (not `docker:...-cli` — for example
while pulling `node` or `ghcr.io/home-assistant/base-debian`), that points to a
broader connectivity problem worth investigating rather than a one-off.
