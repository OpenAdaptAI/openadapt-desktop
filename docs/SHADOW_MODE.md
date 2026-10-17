# Shadow mode

Shadow mode notices a task you repeat and offers to turn it into a draft. It is
off by default. Only the person at the computer can turn it on, from
**Settings → Shadow mode**, after reading a notice. It runs only on this
computer, and it never runs a task.

The watcher itself lives in openadapt-capture (`openadapt_capture.shadow`). Its
design, the buffer format, and the privacy rules are in that repository's
`docs/SHADOW_MINING.md`. This page covers what Desktop adds: the notice, the
controls, the indicators, and the commands.

## Turning it on

1. Open **Settings** and find the **Shadow mode** card.
2. Select **Read the notice to turn on**. The notice says what shadow mode
   records, what it never records, where the record is kept, what it does
   with it, and how to turn it off.
3. Select **Turn on shadow mode** to accept, or **Not now** to leave it off.

Desktop sends the notice's SHA-256 hash with the acceptance. The engine turns
shadow mode on only if that hash matches the notice it has now, and it writes
the time of the acceptance to a local file that only your user account can
read. If the notice text changes in a later version, shadow mode needs a new
acceptance.

After a restart, shadow mode comes back on if it was on when Desktop quit and
the accepted notice is still the current one.

## Knowing it's on

While shadow mode is on:

- The window header shows **Shadow mode on**.
- The menu-bar tray menu reads **Shadow mode: on (local only)**, and the tray
  tooltip says so. On macOS the word **Shadow** also appears next to the tray
  icon.
- The Settings card shows when it was turned on, that it keeps the last 24
  hours, whether the key is in the system keychain or only in memory, and when
  the key and everything recorded with it will be replaced.

The tray line can't be clicked to turn shadow mode on. **Shadow mode
settings…** in the tray menu opens the Settings screen instead.

## What it watches

In this version, shadow mode sees only the steps of recordings you make in
Desktop. While a recording runs, the recorder hands each step to shadow mode
as well as saving it. Shadow mode doesn't run a separate listener when no
recording is running.

Browser steps count only on the sites in **Work sites**. On any other site,
shadow mode notes only that a browser was in use. The list is empty until you
add a site, and it stays on this computer.

## The offer

About once a minute, shadow mode looks for a task you did several times
today. When it finds one, the window shows:

> OpenAdapt noticed a repeated task
> You did this 6 times today.

with **Review draft** and **Ignore**. The text never names an app, a site, a
field, or a value.

- **Review draft** saves a draft file in `shadow/drafts` under the Desktop data
  folder, readable only by your user account. The draft is not an automation.
  To automate the task, record it once; a person reviews and admits the
  automation before it can run.
- **Ignore** stops the offer for that task.

## Turning it off

**Delete everything and turn off** is one click. It deletes the encrypted
buffer, the key, the ignore list, and any saved drafts, and it records that
the acceptance was withdrawn. Turning shadow mode on again needs the notice
again.

## What others can and can't do

OpenAdapt, your employer, and other apps can't turn shadow mode on or read its
record through OpenAdapt:

- There is no configuration file setting, environment variable, Cloud policy,
  or admin setting that turns shadow mode on. It is not part of
  `EngineConfig`.
- The shadow commands are available only to the Desktop window. The local
  socket that other OpenAdapt apps use doesn't accept them.
- Shadow mode makes no network connections, and nothing it records is synced
  or uploaded.

That doesn't protect the record from the computer itself. The record is
encrypted with a key that is destroyed at least every 24 hours, but the key
sits in your keychain (or in memory) while shadow mode is on. Someone with
administrator access to this computer, or software running under your
account, could still inspect the record while it's on. The notice says so.

## Engine commands

| Command | What it does |
| --- | --- |
| `shadow_status` | On or off, the acceptance time, work sites, key location, and pending offers |
| `shadow_notice` | The notice, with its version and SHA-256 hash |
| `shadow_enable` | Turns shadow mode on. Needs `notice_sha256` and `acknowledged: true` |
| `shadow_purge_and_disable` | Deletes everything shadow mode kept and turns it off |
| `shadow_set_work_domains` | Replaces the work-site list (`domains`) |
| `shadow_decide` | Answers an offer: `motif_id` and `action` (`review_draft` or `ignore`) |
| `shadow_scan` | Looks for repeated tasks now |

Events: `shadow_state` carries the same fields as `shadow_status`, and
`shadow_prompt` carries one offer (`motif_id`, `count`, `title`, `body`,
`actions`).

## Requirements and limits

- Shadow mode needs an openadapt-capture release that includes
  `openadapt_capture.shadow`, with its `shadow` extra (`cryptography` and
  `keyring`). With an older release, the Settings card says shadow mode isn't
  available.
- Shadow mode sees only Desktop recordings. An always-on listener is not built.
- If the system keychain can't be reached, the key is kept in memory only.
  After a restart, shadow mode can't read the earlier record and starts over.
