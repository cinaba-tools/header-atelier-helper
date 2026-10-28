# Header Atelier — Akamai staging switch (macOS native helper)

Companion native-messaging host for the **Header Atelier** Chrome extension.
It lets the extension's **network** tab toggle point a production hostname
at the **Akamai staging network** — **without any privileges** (no admin, no
sudo, no `/etc/hosts` edit), so it works on locked-down corporate Macs.

## Authors

Built by **Chiara Innocentin** and **Adriano Basile**. Bugs and questions:
[issues](https://github.com/cinaba-tools/header-atelier-helper/issues).

Header Atelier is a personal project. It is not an official Akamai product and is not
supported or endorsed by Akamai Technologies.

## How it works

Editing `/etc/hosts` needs root, which a non-admin corporate account does not
have. Instead this helper runs a tiny forward proxy on `127.0.0.1` as your own
user, and the extension routes **only the hostnames you stage** to it through
Chrome's proxy setting (a PAC script). For each connection:

1. Follow the hostname's CNAME chain to its Akamai edge hostname
   (`*.edgekey.net`, `*.edgesuite.net`, `*.akamaized.net`, `*.akamaiedge.net`).
2. Derive the staging equivalent (`*.edgekey-staging.net`, …) and resolve its IP.
3. Open the tunnel's TCP socket to that **staging edge IP**.

The browser then does its own TLS handshake **through the tunnel**, keeping the
production hostname: Host header, SNI, certificate and cookies are untouched, so
the staging test is faithful. Everything you do **not** stage goes DIRECT, so
normal browsing is unaffected. Toggling back to Production removes the hostname
from the proxy route.

Since v1.3.0 the popup's **IP** mode can also *pin* a hostname to a destination
you choose — an IPv4 used as-is, or any hostname (e.g. a specific edge
hostname) resolved once to its IPv4 — for sites whose CNAME is not in DNS yet,
or to force a specific edge. Same tunnel mechanics as the staging switch.

Since v1.4.0 the helper also answers a read-only **detect** query: it resolves a
hostname through the system resolver and reports whether it is served by Akamai
(edge CNAME, or reverse DNS of the resolved IP on `akamaitechnologies.com`; either one is enough, anything else counts as not Akamai).
The extension shows the verdict as a badge next to the property lookup. It
never touches the proxy state, and because the lookup happens here the hostname
you are visiting is never sent to a public DNS service.

**v1.4.1** fixes two faults worth knowing about. Idle tunnels were torn down
after 15 seconds, which the browser reported as `ERR_EMPTY_RESPONSE` on the
next reused connection; the tunnel now stays up until one side really closes
it. And installing over a previous version left the old proxy running (it is a
detached process, so nothing stopped it) — the installer now replaces it, and
the helper identifies the listening proxy by asking it, instead of trusting a
PID file whose number may since have been reused. Proxy failures are also
written to `proxy.log` now, rather than surfacing as an unexplained blank page.

## Requirements

- macOS with Google Chrome (stable).
- `python3` (comes with the Xcode Command Line Tools: `xcode-select --install`).
- Header Atelier **v1.2.0 or later** loaded in Chrome (it adds the `proxy`
  permission and the localhost-proxy logic).

## Install

```bash
cd header_atelier_native_host_mac
./install.sh          # NO sudo — installs into your own home folder
```

Then quit Chrome completely (Cmd+Q) and reopen it.

## What gets installed (all under your account)

| Path | Purpose |
|---|---|
| `~/Library/Application Support/header-atelier-helper/native-host.py` | Native messaging host + localhost proxy (runs as your user) |
| `<browser data root>/NativeMessagingHosts/com.cinaba.header_atelier.json` | Registers the host with the browser, restricted to the Header Atelier extension IDs (internal and public build) |

Since **v1.5.1** the manifest also authorises the public build installed from the
Chrome Web Store (`aecomcdlbbnegglengjcflgdodncmljj`).

Since **v1.5.0** the host is registered as `com.cinaba.header_atelier` and lives in
the `header-atelier-helper` folder (before: `top.abasiletools.tcportal.staging` and
`tcportal-staging`). Installing 1.5.0 over an older helper moves its hostnames
across, stops its proxy and removes its files and registration. It needs Header
Atelier **1.13.0** or later (public build **1.12.0**); older extensions only know
the old name and report *helper not installed*.

Since **v1.4.3** the manifest authorises **two** extension IDs: the internal build
(`jckelahhlphngdmllbfjmeapdcailnii`) and the public build without Control Center
features (`fmhlfkkokgidhamnfnejlinbjgpmgejh`). The same helper serves both.

Since **v1.4.2** the manifest is written for **every Chromium browser present in your
account**, not just Chrome: `Google/Chrome` (plus Beta and Canary), `Chromium`,
`Vivaldi`, `BraveSoftware/Brave-Browser` and `Microsoft Edge`, each under
`~/Library/Application Support/`. Browsers you do not have are skipped.

Runtime state (which hostnames are staged, the proxy port, its PID and log)
lives in `~/Library/Application Support/header-atelier-helper/`.

## Uninstall

```bash
./uninstall.sh        # NO sudo
```

Stops the proxy, forgets every staged hostname and removes the files above.
Then open the Header Atelier popup and set any remaining hostname back to
Production (or remove it) so Chrome's proxy setting is cleared.

## Troubleshooting

- **Popup says "helper not installed"** — restart Chrome completely (Cmd+Q);
  check the JSON manifest exists in
  `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`.
- **A staged hostname won't load** — the proxy may not be running; open the
  popup (its status check restarts the proxy) or toggle the hostname off/on.
- **"does not resolve through an Akamai edge hostname"** — the hostname is not
  CNAMEd to Akamai (or is behind another CDN); there is no staging network for it.
- **Chrome's proxy is managed by your organization** — if `chrome://policy`
  shows a `ProxyMode`/`ProxySettings` policy, the extension cannot override it
  and this approach cannot work; contact whoever manages the Mac fleet.
- Inspect the current state and proxy log:
  `cat "$HOME/Library/Application Support/header-atelier-helper/state.json"`
  `cat "$HOME/Library/Application Support/header-atelier-helper/proxy.log"`
