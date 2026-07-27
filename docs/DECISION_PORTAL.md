# The mobile decision portal

When a governed run cannot confirm something, OpenAdapt halts instead of
guessing. This portal is how that question reaches a staff member's phone
without moving protected evidence off the runner.

Desktop owns the **lifecycle, the network boundary, device pairing, and the
generic notification**. It owns no decision semantics: the question, the
evidence, the allowed actions, the revalidation, and the outcome all come from
`openadapt-flow`'s attended console and are relayed unmodified.

## What it is (and is not)

- A **responsive PWA** served by the runner. There is no iOS or Android
  project, and none is planned for this path.
- A **relay**, not a second engine. The phone's answer is forwarded to Flow,
  which validates the signed task digest, the engine pause capability, and the
  allowed-action list, then re-reads the live application before anything
  continues. An accepted tap is never a verified business effect.
- **Loopback-only by default.** Publishing it to a phone is an explicit,
  documented customer decision.

## Network boundary

| Setting | Default | Meaning |
| --- | --- | --- |
| `OPENADAPT_PORTAL_INGRESS_MODE` | `loopback` | `loopback` (this computer only) or `customer_ingress` |
| `OPENADAPT_PORTAL_PUBLIC_ORIGIN` | *(unset)* | The exact `https://` origin your reverse proxy or VPN publishes |
| `OPENADAPT_PORTAL_INGRESS_ACKNOWLEDGED` | `false` | Explicit record that your organization operates that ingress |
| `OPENADAPT_PORTAL_BIND_HOST` | *(unset)* | Optional literal IP when your ingress is not on this host |
| `OPENADAPT_PORTAL_PORT` | `0` | `0` selects an ephemeral loopback port |
| `OPENADAPT_PORTAL_CONSOLE_PORT` | `7863` | Loopback port for the supervised Flow attended console |

The rules, all enforced in `engine/portal/ingress.py` and all fail-closed:

1. The default binds `127.0.0.1` and advertises a loopback URL. A phone cannot
   reach it, and the pairing screen says so rather than minting a dead link.
2. `customer_ingress` **still binds loopback** unless you name a specific
   address. The expected deployment is your own reverse proxy beside the runner
   forwarding to `127.0.0.1`.
3. A wildcard bind address (`0.0.0.0`, `::`, empty, `*`) is refused in every
   mode. So is a plaintext origin, an origin with a path or credentials, a
   hostname as a bind address, and a `customer_ingress` without both a public
   origin and the acknowledgement.
4. Any invalid combination raises and the portal does not start. It never
   widens its own exposure to become reachable.

There is no self-signed-certificate bypass and no test-only wide bind. The test
suite exercises the shipped loopback configuration on a real socket.

## Pairing a phone

Desktop shows a QR code; the phone shows a short code back. Following RFC
8628's device-flow shape:

- The QR link is `https://<your-ingress>/pair#c=<secret>`. The secret rides in
  the **fragment**, which browsers never transmit, so it cannot land in a proxy
  access log or a referrer header.
- The QR carries **only a pairing secret** — no console capability, no pause
  capability, no tenant, run, or pause identifier.
- The secret is **claimable exactly once**, atomically. Two phones scanning the
  same code cannot both pair; the second is refused with `already_claimed`.
- It **expires in five minutes**, enforced server-side and re-checked at
  approval. Both clocks are consulted: monotonic so a wall-clock change cannot
  extend a deadline, and wall time because `CLOCK_MONOTONIC` stalls while a
  machine is suspended. No UI countdown is involved.
- Claiming yields a session that is **unusable until approved**. The
  confirmation code is generated **at claim time** and returned only to the
  claiming device; Desktop asks the operator to type what their phone is
  showing. Attempts are bounded, and exhausting them cancels the pairing.

  The direction matters. If the code were derived from the pairing and shown on
  Desktop, an attacker who photographed the QR from across the room and claimed
  it first would be shown the very code the operator's screen was displaying —
  the "matching code" would confirm the attacker. Minting per claim means a
  remote attacker's phone shows a code the operator cannot see.
- Showing a new QR retires every earlier unapproved pairing, including one that
  has already been claimed, so a stolen code cannot sit latent waiting for a
  mis-click.
- Secrets and session tokens are stored only as SHA-256 digests and compared
  in constant time. The portal secret prefix (`oapp_`) is deliberately distinct
  from the Cloud local-bridge prefix (`oap_`), and neither surface accepts the
  other's credential.
- Devices are listed and revocable; sessions expire after twelve hours.
- The QR is rendered locally as an inert `data:image/png` URI rather than raw
  SVG markup, so a secret-bearing value is never injected as HTML.

## Protected evidence

Task projections, decision outcomes, and evidence crops are served
`Cache-Control: no-store` (plus `Pragma: no-cache`) and are never written to a
cache by the shell. The service worker in `engine/portal/shell/sw.js` precaches
one frozen literal list of shell assets, has no after-the-fact cache write, and
its fetch handler is an allowlist that returns without intercepting anything
else — so a newly added protected route is excluded by default. Shell assets
themselves are served network-first with the precached copy only as an offline
fallback, so a fix shipped in `app.js` still reaches an already-paired phone.
`tests/test_portal/test_shell.py` asserts each of those clauses structurally.

Only raster crops are relayed: `image/png`, `image/jpeg`, and `image/webp`.
`image/svg+xml` is refused, because an SVG is an active document rather than a
screenshot. Upstream 5xx bodies are replaced with a fixed message so a
traceback or deployment path cannot reach a phone; 4xx refusals from Flow are
passed through intact because they are operator-actionable.

The phone never receives Flow's console bearer capability. It authenticates
with a portal session token bound to the runner and the approved pairing.

## Notifications

Operating-system notifications are generic by construction. Desktop reads a
single integer count from Flow's PHI-free notification endpoint and renders the
body from a fixed template; no upstream string is ever forwarded. The payload
may carry only `{title, body, open_count, route}`, and
`assert_generic_notification` refuses anything wider — in the portal service, in
the dispatcher, and again in the shell before the plugin is called.

## Current wiring status

### Packaging: done

The frozen sidecar now carries the console. `pyproject.toml` pins
`openadapt-flow[browser,console]==1.25.0`, `scripts/build_frozen_engine.py`
collects uvicorn's run-time string imports and `openadapt_flow.console`, and
the executable is built unbuffered so the console's one-time capability banner
survives the pipe the portal reads it from. `scripts/smoke_test_frozen_flow.py`
proves this behaviourally against the built artifact on every sidecar build: it
starts the frozen attended console, parses the banner with the portal's own
parser, and drives `/api/session`, `/api/attention`,
`/api/attention/{run_id}`, and an unauthenticated probe through
`engine.portal.flow_client`.

### Runtime: two gaps remain before a phone can decide anything

Both are portal-side wiring, not packaging, and both fail closed today:

1. **No deployment target is passed to the console.** `PortalService`
   always spawns `console --attend --allow-actions`, and Flow deliberately
   refuses attended mutations that are not bound to a deployment: `console
   --attend --allow-actions requires an explicit --config or --backend target`.
   The console exits 1 and `portal_start` reports "The local decision service
   did not start." Desktop already resolves a governed deployment config for
   runs (`data_dir/deployment.json`, staged privately by
   `engine.private_flow_config`); the portal must pass the same one, and a
   human should decide whether that staged file may live for a whole portal
   session rather than a single run.
2. **The readiness check races the console.** Flow prints the banner *before*
   `uvicorn.run()` binds, so `PortalService.start`'s immediate
   `flow.request("session")` can hit a closed port and raise "The local
   decision service started but did not answer." It needs a short bounded
   retry.

One upstream seam is worth closing: the attended console generates its bearer
capability inside `serve()` and only prints it on stdout, so
`engine/portal/service.py` parses the exact banner line. A narrow
`--capability-file` option in Flow would replace that with a supported
interface. The parser is strict and fails loud rather than guessing.
