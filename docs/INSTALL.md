# Installing Workforce Risk Intelligence

This guide is for whoever runs the server at a firm that has licensed the product. It covers
installing it, the settings, backups, and moving to a newer version. It assumes a Linux
server and someone comfortable at a command line; it does not assume a developer.

Everything is done with one script, `./wri`, in the folder this guide came in.

## What is in the package

| File | What it is |
|---|---|
| `INSTALL.md` | This guide |
| `wri` | The script that installs, starts, backs up and upgrades the product |
| `wri-image-<version>.tar.gz` | The product itself, as a container image |
| `compose.yaml` | How the two parts of the product run together. Not meant to be edited |
| `wri.env.example` | Every setting, with what it does |
| `requirements.lock` | Every third-party component in the image, with its exact version |
| `VERSION` | The version, and whether this package enforces license keys |
| `SHA256SUMS` | A fingerprint of each file above, to check the package arrived intact |
| `LICENSE` | The terms the software is provided under |

## What the server needs

- **Linux, 64-bit Intel/AMD** (`x86_64`), unless you were told the package was built for
  another kind of machine.
- **Docker Engine 24 or newer, with Compose v2.** `docker compose version` should answer.
- **2 CPU cores, 4 GB of memory, 5 GB of disk** for the product, plus room for your clients'
  files and for backups. Client files are small: tens of megabytes per engagement.
- **No internet connection is needed**, to install or to run. The product contacts nobody,
  with one exception you choose: if you switch single sign-on on, the server must be able
  to reach your identity provider over HTTPS, and it contacts nothing else.
- The account that runs `./wri` must be allowed to run `docker`.

One server holds one installation. A second copy of this folder on the same server would
share the first one's data.

## Install

```bash
tar xzf wri-<version>.tar.gz
cd wri-<version>
sha256sum -c SHA256SUMS            # every line should end "OK"

./wri install                      # loads the image, writes wri.env
./wri create-admin you@firm.example "Your Name"    # asks for a password, 12 characters or more
./wri start
./wri status
```

`./wri status` should end with **`in order`**, and show two services as `healthy`. The
product is then at <http://127.0.0.1:8501> **on the server itself**. Sign in with the
account you just made. That account is the firm administrator: it creates the other user
accounts, opens engagements and enters license keys, all from the **Engagement** page.

If `./wri status` reports a `PROBLEM`, it says what to do about it.

## Who can reach it

**As installed, only the server itself can open the product.** That is deliberate. The
product speaks plain HTTP, so passwords and client HR data would cross your network
unencrypted if it were simply opened up.

To let your people reach it, put a reverse proxy that terminates TLS in front of it and
leave `WRI_BIND=127.0.0.1` as it is. Any proxy your firm already uses will do. It needs to:

- listen on HTTPS with a certificate your firm's browsers trust;
- forward everything to `http://127.0.0.1:8501`;
- **pass WebSocket connections through**, which the screen depends on;
- allow request bodies at least as large as `WRI_MAX_UPLOAD_MB`.

As an illustration, this is what that looks like for Caddy, which does all four by
default. It is an example only: no proxy is shipped with the product, and this
configuration has not been tested with it. Use whatever your firm already runs.

```
wri.yourfirm.example {
    tls /etc/wri/tls/cert.pem /etc/wri/tls/key.pem
    reverse_proxy 127.0.0.1:8501
}
```

Setting `WRI_BIND=0.0.0.0` instead publishes the plain-HTTP screen to every machine that can
reach the server. `./wri start` warns when it sees that. Do not use it outside a closed
test.

The part of the product that holds the data (the API) is never published at all. Only the
screen talks to it, over a private network between the two containers.

## Settings

Settings live in `wri.env`, beside the script. `./wri install` creates it. After changing
one, run `./wri restart`.

| Setting | Default | What it does |
|---|---|---|
| `WRI_IMAGE` | set by the script | The image this installation runs. Changed by `./wri upgrade`, not by hand |
| `WRI_BIND` | `127.0.0.1` | Which network address the screen is published on. See "Who can reach it" |
| `WRI_PORT` | `8501` | The port the screen is published on |
| `WRI_SESSION_HOURS` | `12` | How long a sign-in lasts |
| `WRI_MAX_UPLOAD_MB` | `50` | The largest file a user may upload |
| `WRI_DATABASE_URL` | empty | Leave empty. See "Using your own database server" below |
| `WRI_SSO_ISSUER` | empty | Your identity provider's issuer address. Empty means single sign-on is off |
| `WRI_SSO_CLIENT_ID` | empty | The application id the provider issued for this product |
| `WRI_SSO_CLIENT_SECRET` | empty | The secret that goes with it |
| `WRI_SSO_REDIRECT_URL` | empty | The https address your people open the product at |
| `WRI_SSO_NAME` | `single sign-on` | What the sign-in button says |
| `WRI_SSO_SCOPES` | `openid email profile` | What is asked of the provider. Rarely changed |
| `WRI_SSO_EMAIL_CLAIM` | `email` | Where in the provider's answer the email address is |
| `WRI_SSO_REQUIRE_VERIFIED_EMAIL` | `on` | Refuse an address the provider has not confirmed |
| `WRI_PASSWORD_SIGN_IN` | `on` | `off` refuses every password. Needs single sign-on |

`./wri status` shows whether single sign-on is on and whether passwords are still accepted.
If the settings are incomplete or contradictory the product does not start, and
`./wri logs api` says which setting is wrong.

## Single sign-on

Optional. With it, people sign in at your firm's own identity provider, and this product
never sees their password. Multi-factor sign-in, password rules and removing a leaver are
then done in one place: the provider. Any provider that speaks **OpenID Connect** works;
Microsoft Entra ID, Google Workspace, Okta and Keycloak all do.

It needs the product to be reachable at an **https** address first. See "Who can reach it".

**1. Register the product with your provider** as a web application. The provider will ask
for a redirect address: give the https address your people open the product at, ending in a
slash, for example `https://wri.yourfirm.example/`. It will give you an issuer address, a
client id and a client secret.

**2. Put those in `wri.env`**, then `./wri restart`:

```
WRI_SSO_ISSUER=https://login.yourprovider.example/your-tenant
WRI_SSO_CLIENT_ID=...
WRI_SSO_CLIENT_SECRET=...
WRI_SSO_REDIRECT_URL=https://wri.yourfirm.example/
WRI_SSO_NAME=Yourfirm sign-in
```

The issuer is right if `<issuer>/.well-known/openid-configuration` opens in a browser.

**3. Create accounts.** Being known to the provider does not give anyone an account here.
A firm administrator creates each person's account on the **Engagement** page, with their
email address and no password, and adds them to engagements as before. The first time the
person signs in through the provider, the account is tied to them.

**4. When it works, consider switching passwords off:** `WRI_PASSWORD_SIGN_IN=off`. Do it
only after a firm administrator has signed in through the provider, or nobody will be able
to get in.

What to know:

- **An account that has signed in through the provider can no longer use a password.** That
  is deliberate: otherwise someone removed at the provider could still walk in with an old
  one. An account that never uses the provider keeps its password. Keeping one such
  administrator account, with a long password stored safely, is the way back in if the
  provider is ever unreachable. `WRI_PASSWORD_SIGN_IN=off` closes that door too.
- **Removing someone at the provider stops their next sign-in, not their current one.** A
  session lasts `WRI_SESSION_HOURS`. To end it at once, deactivate the account on the
  Engagement page.
- **An account answers to one person.** If an email address passes to someone else at the
  provider, that person is refused until a firm administrator resets the link on the
  Engagement page.
- **If your provider does not confirm email addresses** (the sign-in is refused with "has
  not confirmed your email address"), and you trust the addresses it holds, set
  `WRI_SSO_REQUIRE_VERIFIED_EMAIL=off`. Some providers also put the sign-in name in a
  different field from `email`; `WRI_SSO_EMAIL_CLAIM` names the field to read instead
  (`preferred_username` is a common one). Your provider's documentation says which
  fields it sends.
- A provider whose certificate comes from a private authority is not supported yet, and
  neither is a single issuer address shared by many organisations (Microsoft's `common`
  address): use your own tenant's.

## License keys

Each engagement needs a license key, issued to your firm for one client. A firm
administrator pastes it in when opening the engagement, on the **Engagement** page.

`./wri status` shows where this stands:

- `license keys  enforced (verification key …)` is a normal licensed installation.
- `license keys  NOT ENFORCED` means this package was built without a verification key. It
  is not a release. Ask for the right package.

The Engagement page shows where each engagement's key stands. A key that has expired
leaves finished work readable, including the client's report, and stops new work (loading
data, changing a value, running a scenario) until a current key is added. An engagement
with no key, or a key issued for a different client, cannot be opened until that is put
right.

The licensor may ask for the **usage log**. A firm administrator downloads it from the
Engagement page. It records engagements opened, keys added, datasets loaded (as counts) and
reports produced. It holds no client data.

## Backups

Everything the installation owns is in one Docker volume, `wri_wri-data`: the accounts, the
engagements, each client's files and the audit log. Nothing else needs backing up except
`wri.env`.

```bash
./wri backup                       # writes backups/wri-backup-<date>-<time>.tar.gz
./wri backup /mnt/offsite/wri-monday.tar.gz
```

- **A backup contains client HR data.** It is written readable only by the account that
  made it. Keep it as carefully as the server, and keep it somewhere other than the server.
- A backup can be taken while the product is running. If someone is in the middle of loading
  a client's file at that moment, that one load may be caught half-way; take scheduled
  backups outside working hours.
- A backup is checked when it is restored: every file in it is compared with a fingerprint
  recorded when it was made, and a damaged or altered backup is refused before anything is
  changed.

To put a backup back:

```bash
./wri stop
./wri restore backups/wri-backup-<date>-<time>.tar.gz    # asks you to type yes
./wri start
```

A restore never deletes what it replaces. The data that was there is moved into a folder
named `replaced-<date>` inside the same volume. Once you are sure the restore is what you
wanted, that folder can be removed to free the space.

Test a restore once, on a spare machine, before you need one.

## The audit log

Every request is recorded: who asked, in which engagement, for what, and whether it was
allowed. Each entry is tied to the one before it by a fingerprint, so an entry changed,
removed or inserted afterwards shows.

```bash
./wri audit
```

It ends with `intact`, or lists every place the log does not hold together, and prints the
log's **head**: the last entry's number and fingerprint, such as `48213:9f2c…`.

That check alone cannot notice two things: entries removed from the very end, or a log
rebuilt from the start by someone with full access to the server. Both are caught by noting
the head **somewhere the server's administrators cannot change** (a weekly email to someone
outside IT, a ticket, a printed page) and later asking whether the log still holds it:

```bash
./wri audit --expect-head 48213:9f2c…    # the whole value, as it was printed
```

A firm administrator also sees whether the log is intact, and its head, on the Engagement
page.

The log grows by one line per request and is never trimmed by the product. It is included
in every backup.

## Moving to a newer version

You will receive a new `wri-image-<version>.tar.gz`. Copy it into this folder, then:

```bash
./wri upgrade wri-image-<new version>.tar.gz
```

That one command stops the product, **takes a backup first**
(`backups/before-upgrade-…`), switches to the new image and starts it. The new version
brings the stored data up to date itself the first time it starts. Check `./wri status`
and sign in.

Replace this guide and the `wri` script with the ones from the new package too, if they came
with one.

### Going back

```bash
./wri rollback
```

This returns to the previous image **and** to the data exactly as it was before the upgrade,
from the backup the upgrade took. Anything entered since the upgrade is moved aside (as with
any restore), not merged, so decide quickly.

Going back needs both halves. An older version will not open data that a newer version has
already brought up to date: it stops and says so, and changes nothing. That is why
`./wri rollback` restores the backup as well as the image.

## Moving to another server

1. On the old server: `./wri stop`, then `./wri backup`.
2. Copy the package folder, including `wri.env` and the backup, to the new server.
3. On the new server: `./wri install`, then `./wri restore <the backup>`, then `./wri start`.

Masked employee ids stay the same across the move, because the backup carries what they are
derived from.

## Using your own database server

By default the accounts, engagements and settings are kept in a small database file inside
the data volume, and `./wri backup` covers it. That suits a single firm.

`WRI_DATABASE_URL` points the product at a database server you run instead. Then
`./wri backup` covers the clients' files but **not** the accounts and engagements, which
must be backed up with that server's own tools, at the same moment. Unless your firm has a
reason to do this, leave the setting empty.

## Removing it

`./wri stop` stops the product and removes nothing. The data stays until the volume is
removed by hand with `docker volume rm wri_wri-data`, which cannot be undone. Take a backup
first, and remember that backups and the `replaced-…` folders hold client data too.

## What this version does not do

- **Signing out here does not sign you out of the identity provider**, and the provider
  cannot sign anyone out of here. Accounts are not created from the provider's directory.
- **No TLS of its own.** See "Who can reach it".
- **The audit log shows editing; it cannot prevent it.** See "The audit log" above.

## If something is wrong

| What you see | What to do |
|---|---|
| `./wri status` says `PROBLEM: there is no firm administrator` | `./wri create-admin you@firm.example` |
| `data was last used by a newer version of the product` | You are running an older image against upgraded data. `./wri rollback`, or load the newer image again |
| A service is `unhealthy` or keeps restarting | `./wri logs api` or `./wri logs ui`. The last lines say why |
| The browser shows the sign-in page but nothing loads after | The reverse proxy is not passing WebSocket connections through |
| "the verification key could not be read" in the logs | The package was built wrongly. Ask for a new one; do not edit the image |
| A user is locked out | Five wrong passwords lock an account for 15 minutes. It clears by itself |
| The firm administrator's password is lost | `./wri create-admin another@firm.example` makes a second administrator |
