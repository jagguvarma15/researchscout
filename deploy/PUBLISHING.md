# Publishing: Funnel, Auth0, Vercel, Grafana Cloud

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

## 4. Grafana Cloud

The dashboards in `config/grafana/dashboards/` are SQL against Postgres, so Grafana Cloud needs
a route to a database that is not exposed. Private Data Source Connect is that route: an agent
in this compose stack opens an outbound tunnel, and no inbound rule is needed.

Moving here also changes what the dashboards watch. The local Grafana points at the development
database in `.local/pgdata`, which stopped being the live system the moment the deployment took
over; the hosted one will point at the database actually serving the site.

1. Create a free Grafana Cloud stack.
2. **Connections -> Private data source connections -> Configure**. Note the three values from
   the Configuration Details tab and put them in `deploy/.env`:

   ```
   GCLOUD_PDC_SIGNING_TOKEN=
   GCLOUD_PDC_CLUSTER=
   GCLOUD_HOSTED_GRAFANA_ID=
   ```

   The token needs the `pdc-signing:write` scope. The agent takes these as command-line flags,
   not environment variables - passed the wrong way it starts, connects to nothing, and says
   little about why. The compose service already passes them correctly.

3. Give the dashboards their own read-only login rather than the application's:

   ```bash
   make grafana-db-role
   ```

   It creates a `grafana` role that can `SELECT` and nothing else, generates a password into
   `deploy/.env`, and covers future tables through default privileges.

4. Start the agent:

   ```bash
   docker compose -f deploy/docker-compose.yml --profile monitoring up -d
   docker compose -f deploy/docker-compose.yml logs pdc-agent   # should report a connection
   ```

5. In Grafana Cloud, add a **PostgreSQL** data source:

   | Field | Value |
   | --- | --- |
   | Host | `postgres:5432` |
   | Database | `researchscout` |
   | User | `grafana` |
   | Password | `GRAFANA_DB_PASSWORD` from `deploy/.env` |
   | TLS/SSL Mode | `disable` (the tunnel is the encryption) |
   | Private data source connect | your PDC network |

   `postgres` resolves inside the compose network, which is where the agent runs.

6. Import the four dashboards from `config/grafana/dashboards/`, pointing each at that data
   source.

Check: the Pipeline dashboard renders with data.

Once it does, the local Grafana is redundant - `make grafana-stop` reclaims about 170 MB, and
`make grafana-start` brings it back for development.
