# Privacy policy

This policy covers the **Header Atelier** Chrome extension and its **native helper**
(this repository).

Header Atelier does not collect, transmit, sell or share any personal data. It has
no account, no server, no analytics and no advertising.

## What is stored, and where

Everything stays on your computer:

| Data | Where |
|---|---|
| Accounts, rules, headers, hosts and exclusions you create | The extension's local storage in your browser (`chrome.storage.local`) |
| Enhanced Debug keys you paste | The extension's local storage; never exported unless you choose **Export** → **Include keys** |
| Hostnames you switch to staging or pin to an IP | The extension's local storage and the helper's `state.json` in your user folder |
| Proxy log | The helper's `proxy.log` in your user folder |

Uninstalling the extension removes its storage; the helper's uninstaller removes its
folder.

## What is sent over the network

- **Request headers**: the extension adds, changes or removes the request headers you
  configure, only on requests to the hosts you choose. Those requests go to the sites
  you visit, as they would without the extension.
- **Enhanced Debug tokens**: signed inside your browser with your key and sent only in
  your requests to the hosts of the rule.
- **Staging and pinned IPs**: the helper looks up hostnames with your computer's own DNS
  resolver and opens the connection to the chosen IP through a proxy that listens only
  on `127.0.0.1`. Only the hostnames you switch are routed through it.

Nothing is sent to the authors or to any third party.

## Permissions

The extension asks for `declarativeNetRequest` and access to all sites (to apply your
header rules to the hosts you choose), `storage` (to keep your configuration),
`alarms` (to renew Enhanced Debug tokens before they expire), `nativeMessaging` (to
talk to this helper) and `proxy` (to route only the hostnames you switch through the
local helper).

## Changes and contact

Changes to this policy are published in this repository. Questions:
[issues](https://github.com/cinaba-tools/header-atelier-helper/issues).

Last updated: 2026-10-28.
