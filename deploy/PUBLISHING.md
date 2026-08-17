# Publishing: Funnel, Auth0, Vercel

The compose stack in this directory runs the backend. This is everything outside it - the three
accounts that make the site public, in the order they need to happen. Each step ends with
something you can check, because a half-configured deployment fails in ways that look like code
bugs.

Nothing here can be scripted from the repository: every step needs credentials only you can
create.

## 1. Publishing the API with Tailscale Funnel

The backend runs at home. Funnel gives it a public HTTPS hostname without a domain, without an
inbound firewall rule, and without exposing this machine's address: the daemon holds an
outbound connection and Tailscale relays to it.

1. Install Tailscale on this Mac and sign in (`brew install --cask tailscale`).
2. Enable HTTPS and Funnel for the tailnet: admin console -> DNS -> enable MagicDNS and HTTPS
   certificates, then Access controls -> add `funnel` to the node attributes for this machine.
   The admin console prompts for both the first time you run the command below.
3. Publish the API, which compose binds to `127.0.0.1:8001`:

   ```bash
   tailscale funnel --bg 8001
   tailscale funnel status        # prints the public https://<machine>.<tailnet>.ts.net
   ```

Check: `curl https://<machine>.<tailnet>.ts.net/healthz` from a phone off wifi returns
`{"status":"ok"}`.

Funnel listens only on 443, 8443 and 10000, only over TLS, and its bandwidth is capped at a
level Tailscale does not publish. For a personal research radar that is fine; it is not a CDN.

### Close the front door

That hostname is public, so anyone who learns it can call the API - including the routes open
to signed-out visitors. One shared secret separates the site's own server from everyone else:

```bash
openssl rand -base64 36        # put it in deploy/.env as RS_SERVICE_TOKEN
                               # and in Vercel as API_SERVICE_TOKEN
```

The frontend proxies every browser request server-side, so the token never reaches a browser.
Requests without it get a 404 rather than a 403 - there is no reason to confirm to a scanner
that an API lives there. `/healthz` stays open so the container healthcheck works.

That token is also what makes rate limiting mean anything. Every request arrives from the same
place (the site's server), so the socket address is useless for telling visitors apart; the
proxy forwards each visitor's address, and the API believes it only because the token proved
where the request came from.

With no edge in front of the API any more, that in-process limiter is the only limiter. It is
per process and resets on restart - adequate for this scale, and worth remembering before
posting the link somewhere busy.

### Prove the deployment is current

After every `make deploy-build && make deploy-up`, run:

```bash
make deploy-verify
```

It reads `GET /v1/system/status` on :8001 and answers the three questions that matter: is the
running image built from the commit checked out here (the build SHA is stamped at
`deploy-build`), did the migrations land, and are the scheduled runs actually happening (the
`scheduler_runs` ledger, with the newest paper's age beside it). It exits nonzero when the
deployment is unreachable, stale, or missing the catalogue routes - each with a message saying
which. The web footer's "Newest paper" line reads the same endpoint, so staleness is also
visible on the page itself.

Worth internalising once: `docker restart` and a reboot keep the old image and the old
container environment. Only `deploy-build` + `deploy-up` (which recreates the containers)
delivers merged code and new compose defaults - that gap is how the stack once ran for two
days on stale code while looking perfectly healthy.

And its sibling: a Mac hosting the deployment must not sleep. While it sleeps the API is
unreachable and no scheduled slot can fire (a slept-over slot now runs once on wake, but on
wake, not on time). Keep the machine awake on AC power:

```bash
sudo pmset -c sleep 0
```

The display can still sleep; `-c` scopes the setting to being plugged in. `deploy-verify`
names the failure if it happens anyway: a pipeline slot that passed after the scheduler's
newest start-up with no run recorded exits nonzero.

## 2. Auth0

Three things get created, and each hands back values that go somewhere specific. The gotchas
below are the ones that cost an afternoon rather than a minute.

### The application people sign in through

Applications -> Create Application -> **Regular Web Application** (not Single Page: this flow
runs on the server and keeps a client secret). Skip the quickstart, go to Settings.

- **Allowed Callback URLs**: `http://localhost:4321/callback` while testing locally, and
  `https://<your-site>.vercel.app/callback` once Vercel has given you a name. Both can sit in
  the list, comma separated.
- **Allowed Logout URLs**: `http://localhost:4321` and `https://<your-site>.vercel.app`.
- Advanced Settings -> Grant Types: **Authorization Code** and **Refresh Token** both ticked.
  Without the second, sessions end when the access token expires instead of renewing.

Copy the Domain, Client ID and Client Secret.

### The API the tokens are for

APIs -> Create API.

- **Name**: anything. **Identifier**: `https://researchscout.api` - this is the audience the
  backend validates, it is a name rather than a URL that resolves, and it must match
  `RS_OIDC_AUDIENCE` character for character.
- **Signing Algorithm**: RS256. The backend accepts nothing else.
- After creating it, Settings -> **Allow Offline Access: on**. This is the one people miss:
  without it the `offline_access` scope is ignored, no refresh token is issued, and everyone
  is signed out when their access token expires.

### The application that deletes accounts

Applications -> Create Application -> **Machine to Machine**, authorized for the **Auth0
Management API**, with the **`delete:users`** scope and nothing else.

Copy its Domain, Client ID and Client Secret - they are different from the first application's.

### Where the values go

| Auth0 | Setting | Format |
| --- | --- | --- |
| Domain | `RS_OIDC_ISSUER` in `deploy/.env` | `https://tenant.us.auth0.com/` - scheme and trailing slash |
| Domain | `RS_AUTH0_DOMAIN` in `deploy/.env` | `tenant.us.auth0.com` - bare, no scheme, no slash |
| Domain | `AUTH0_DOMAIN` in Vercel | bare, as above |
| API Identifier | `RS_OIDC_AUDIENCE` and Vercel's `AUTH0_AUDIENCE` | identical in both |
| Web app Client ID / Secret | Vercel `AUTH0_CLIENT_ID` / `AUTH0_CLIENT_SECRET` | |
| M2M Client ID / Secret | `RS_AUTH0_MGMT_CLIENT_ID` / `RS_AUTH0_MGMT_CLIENT_SECRET` | |

The trailing slash on the issuer matters: it is compared against the `iss` claim in every
token, which Auth0 writes with one. A mismatch is a 401 on every request and says nothing
about why.

The signing keys need no configuration - the backend reads the issuer's discovery document to
find them.

One thing to look at before inviting anyone: Authentication -> Social. A new tenant enables
Google using Auth0's shared development keys, which are rate limited and show an Auth0 notice
on the consent screen. Either turn the connection off or give it your own Google credentials.

Then restart the backend so it picks up the issuer: `make deploy-down && make deploy-up`.

Check: `curl -H "x-rs-service-token: <token>" https://<machine>.<tailnet>.ts.net/v1/me` returns
401 (no account token), and the same request to `/v1/papers` still returns papers.

## 3. Vercel

The frontend is `apps/web`. Nothing to change before deploying: the config picks its adapter
from the environment, so Vercel's builds (where `VERCEL=1` is set) get the serverless adapter
and everything local keeps the standalone Node server.

Project settings: root directory `apps/web`, framework Astro. Environment variables:

| Variable | Value |
| --- | --- |
| `API_URL` | `https://<machine>.<tailnet>.ts.net` |
| `SITE_URL` | `https://<site>` |
| `AUTH0_DOMAIN` | `<tenant>.us.auth0.com` |
| `AUTH0_CLIENT_ID` | the regular web application's id |
| `AUTH0_CLIENT_SECRET` | its secret |
| `AUTH0_AUDIENCE` | the API identifier from step 2 |
| `SESSION_SECRET` | `openssl rand -base64 48` |
| `API_SERVICE_TOKEN` | the same value as `RS_SERVICE_TOKEN` in `deploy/.env` |

Keep the Hobby plan's terms in view: non-commercial personal use only, and that includes
donation links and advertising.

Check: sign in on the deployed site, accept the terms once, save a paper, sign out and back in.
The terms dialog must not appear the second time.

## Monitoring

There is no external monitoring stack. The scheduler's health task self-checks every half
hour (ingest cadence, failing streaks, weekend-aware corpus freshness, hung runs, the
funnel's public DNS record, retention), `GET /v1/system/status` reports the verdict, the
about page renders it, and `make watchdog-schedule` turns failures into macOS notifications.
`make deploy-verify` reads the same payload after every deploy. See the Monitoring section
of the top-level README.
