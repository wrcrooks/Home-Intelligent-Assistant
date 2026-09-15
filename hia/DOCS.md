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
