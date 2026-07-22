# Firefox tab control

Researched 2026-07-19, against Firefox **150.0.2** as installed here.

The problem: a Stream Deck plugin wants to enumerate open Firefox tabs (titles +
URLs, across all windows) and focus a chosen one. Chrome and Safari expose this
via AppleScript in one line. Firefox does not.

## AppleScript is a dead end, permanently

Verified directly on this machine:

- `NSAppleScriptEnabled` is `true` in Firefox's Info.plist
- there is **no `.sdef` file anywhere in the bundle** — zero
- `tell app "Firefox" to get name of every tab of every window` → **syntax error
  -2741**, `tab` is not a class
- `tell app "Firefox" to get name of every window` → **works**, returns real titles

So Firefox is not AppleScript-mute; with no `.sdef` it inherits Cocoa's standard
suite only — `open`, `quit`, `activate`, `count`/`name of windows`. You can read
one title per window (the active tab's), no URLs, no background tabs. Useless for
a tab switcher — but note `activate` **does** work, which matters below.

**This will not change.** Tracking bug
[1655268](https://bugzilla.mozilla.org/show_bug.cgi?id=1655268) is NEW and
unresolved; a `get-active-url` patch stalled on a mandatory security review around
2020. The reviewer's objection frames scriptable tab enumeration as an
**exfiltration surface**, not a missing convenience. Predecessors
[326133](https://bugzilla.mozilla.org/show_bug.cgi?id=326133) (2006), 369901, and
516502 all died the same way. Twenty years, nothing shipped. Do not plan around it.

## Mechanisms evaluated

| Mechanism | Verdict |
|---|---|
| AppleScript | Dead, permanently (above) |
| Remote Debugging Protocol (`--start-debugger-server`) | Technically alive, strategically dead. Firefox 129 stopped enabling CDP by default; Selenium removed Firefox CDP in 4.29.0 (2025). Mozilla's own devtools-mcp uses BiDi, not RDP. Building here is building on a deprecation path. |
| WebDriver BiDi / Marionette | **Can** attach to a running user profile — `geckodriver --connect-existing --marionette-port 2828`, real cookies and tabs intact. But it requires permanently running your daily browser in automation mode: `navigator.webdriver = true`, altered fingerprint, trips Cloudflare/Akamai. Mozilla's own docs warn against leaving it on. Viable for debugging, hostile as a product. |
| macOS Accessibility API | Does not work. Firefox renders its own chrome and exposes no conformant `AXTabGroup`. Would yield titles but never URLs, plus a permission prompt and per-release fragility. |
| Firefox-specific IPC / command socket | Does not exist. The three sanctioned channels are native messaging, Marionette/BiDi, and legacy RDP. That is the complete list. |

**Native messaging is the only sanctioned path**, and every serious project in this
space independently converged on it. That convergence is good evidence there is no
cleverer trick available.

## Prior art

Figures pulled live 2026-07-19.

| Project | Stars | Last push | Notes |
|---|---|---|---|
| [balta2ar/brotab](https://github.com/balta2ar/brotab) | 508 | 2025-01-22 | Most-starred. Python. Broken on macOS out of the box. |
| [deanishe/alfred-firefox](https://github.com/deanishe/alfred-firefox) | 361 | 2023-02-23 | Go, macOS-only, abandoned. Best pure fork target. |
| [egovelox/mozeidon](https://github.com/egovelox/mozeidon) | 63 | 2026-04-11 | **Alive.** Go host + TS ext, AMO-signed, macOS-correct, Raycast consumer. |
| [mozilla/firefox-devtools-mcp](https://github.com/mozilla/firefox-devtools-mcp) | 307 | 2026-07-10 | Mozilla's own, via BiDi |

**brotab** does functionally exactly what we want (extension ↔ native messaging ↔
localhost mediator ↔ `bt` CLI; `bt list`, `bt activate`, FTS5 search). But
[issue #43](https://github.com/balta2ar/brotab/issues/43) — *"Doesn't work on macOS
(wrong manifest directory)"* — was opened **2020-09-11 and is still open**. It
writes the native-messaging manifest to the Linux path. A six-year-old one-line
bug with a submitted fix, unmerged, alongside 79 open issues and 18 stale PRs. It
works after moving the file by hand; nobody is minding the store.

**mozeidon** is the sleeper: v4.0.0 Feb 2026, 2 open issues, correct macOS install
path, signed and listed on AMO, handles multiple profiles, already proves the
"external launcher drives Firefox tabs" pattern via its Raycast extension.
Caveat: **no LICENSE file**, so technically all-rights-reserved — would need the
author to add one before building on it.

## Architecture, if built

WebExtension (MV2) + native messaging host + local socket → Stream Deck plugin.

- **Extension permissions:** `"tabs"` and `"nativeMessaging"` only. No
  `<all_urls>`, no host permissions — keeps the install prompt non-scary.
  `browser.tabs.query({})` to enumerate; `tabs.update({active:true})` +
  `windows.update({focused:true})` to switch.
- **Host manifest:** `~/Library/Application Support/Mozilla/NativeMessagingHosts/<name>.json`
  (confirmed in live use on this machine — Dropbox and WebEx have manifests there).
  Needs `name` matching the `connectNative()` argument, an **absolute** `path`,
  `type:"stdio"`, and `allowed_extensions` listing the gecko id. **This directory
  differs from Chrome's** — the single most common bug in this space, and exactly
  what has kept brotab broken since 2020.
- **Bridge:** a unix domain socket at a fixed path, not brotab's localhost port
  scanning — faster, no collisions, no firewall prompt.
- **Host binary:** a single compiled Go binary, as alfred-firefox and mozeidon
  both do. No Python runtime to ship inside a Stream Deck plugin.

### The hard part is focus, not enumeration

Listing tabs is one line. **Raising the right Firefox window to the front of the
macOS window server is the part with scars on it.** `windows.update({focused:true})`
does not reliably raise the app — macOS restricts cross-application window
raising and Firefox follows OS convention. Mozeidon had multi-window broken until
v4 and still notes switching goes to the last active window.

The fix, and the architecture-defining insight: **split responsibilities.** The
extension selects the tab; the **native host** performs the OS-level activation
(`NSRunningApplication.activate()`, or an `activate` AppleScript to Firefox —
which works, since it's in the standard suite). That split is the reason to want
a real compiled binary rather than a script.

**Prototype that one interaction before writing anything else.** If tab-select
plus host-side raise works, the rest is plumbing.

### Signing

Firefox Release and Beta have **no override** — `xpinstall.signatures.required=false`
only works on Developer Edition, Nightly, and ESR. `about:debugging` loads unsigned
extensions but **only until restart**, unusable for a daily driver. So an AMO
account is mandatory.

The good news: AMO offers **unlisted self-distribution signing** — upload the XPI,
it's signed automatically without human review, and you host the `.xpi` yourself.
Permanently installable, no queue, not publicly listed.

Set `browser_specific_settings.gecko.id` before first submission and never change
it — it binds the extension to `allowed_extensions` in the host manifest.

### Real product risk: installation UX

Two artifacts must be installed and cross-referenced — an extension from a URL,
and a binary + JSON manifest in a Library path with an absolute path baked in.
Every project above has issues filed about this step. The Stream Deck plugin
should write the manifest itself on first run (it knows its own install path) and
surface a clear "extension not connected" state on the key.

## Recommendation

Evaluate **contributing a Stream Deck consumer to mozeidon** before building
anything. The hard parts — signed extension, correct macOS manifest path,
multi-profile handling — are already done and maintained by someone else, and it
already has a non-Stream-Deck consumer proving the pattern. Resolve the LICENSE
question with the author first.

If that doesn't pan out, build fresh on the architecture above rather than forking
brotab; inheriting 79 open issues and a Python dependency to get a tab button is a
bad trade. Scope if built fresh is genuinely small — two permissions, one Go
binary, one manifest, one socket.
