# Root and IAM users (AWS-style sign-in)

- **Root** is the single account flagged `accounts.is_root`. Its badge and PIN are chosen at first set-up
  (Admin's first-run screen, or `python -m gpusa_server.create_admin`; the server's `GPUSA_SETUP_CODE`
  protects it). Root can never be demoted, switched off or deleted, and nobody else becomes root.
- Root alone creates, re-roles, switches off, unlocks and deletes accounts, resets PINs, reads the sign-in
  log, uses recovery codes, and sees the data location and the server address (Admin > Server).
- Root adds IAM users in Admin > Settings: pick the account type (**Admin**, **Depot manager** or
  **Cashier**), the badge ID and a PIN. The badge of a person already added at the depot / till Staff page
  is used as it is; a badge nobody has yet adds the person in the same popup (name, and the warehouse or
  dealership for a depot manager / cashier), so a fresh installation needs nothing but root.
- **Admin** (IAM) opens the Admin dashboard, the depot console and the till with its own badge + PIN and
  sees every place; it has no account management and no server address. Depot managers and cashiers sign in
  to their own place only.
- **The shop's manager gives till PINs.** A cashier account whose employee job is *Management* (the shop
  manager root named) can give one of its own shop's people a cashier sign-in: till > Staff > "Give a till PIN"
  (`POST /accounts/till`, `account_repository.create_till_account`). Nothing else about accounts; Admin sees it
  in the live feed (`till_account_added`).
- **Which shop, which depot.** A till or depot PC set up by the installer knows its place from its setup
  file. One started without it follows the person: the till opens at the cashier's own dealership and the
  Depot Floor at the opener's own warehouse (their employee record, `database/own_place.py`). Admin accounts
  have no place of their own: they see the first one and switch freely; anyone else leaving their place
  needs an administrator's badge + PIN (a one-off approval, `account_repository.approve`, which never
  changes who is signed in).
- **Opening the Depot.** With a server the Depot asks for one sign-in when it starts (depot manager or
  Admin). The Floor then stays open for everyone and works with that sign-in
  (`database/remote.keep_standing`): signing out of the Console, or a token older than half a day, goes
  back to it rather than to the computer's own empty data. Without a server the Floor opens as before.
- Admin has one sign-in screen: badge + PIN decide who comes in. The root user (B-001 at first set-up) always
  opens as root; every other account is an IAM user that root created in Admin > Settings. Nothing
  is remembered between runs: every app asks for badge + PIN each time it is opened.
- Every root sign-in is logged as `root_sign_in`.
- Upgrade: migration v13 / alembic 0003 makes the oldest active administrator root. Existing extra admins
  become IAM Admins.
- First set-up: the first-run screen offers badge `B-001` for root and is titled "Create the root user".
- Connecting is automatic: every app looks for the server on the network when it starts
  (`database/server_discovery.py`) and saves the address for that computer; a saved address that stopped
  answering is looked up again. When the server is not on the network, the sign-in screens take its address
  ("Enter the server address"); the saved address is never shown there. From then on every sign-in on that
  PC, IAM included, uses the server's database. `GPUSA_DISCOVERY=0` keeps a PC on its own local file.
- Admin > Server (root only) needs no second sign-in: it shows the address, whether the server and its
  database answer, the version, who is signed in and how much the server holds, and lets root save another
  address for the computer (used from the next start).

## Guards around the automatic connection

- **Is this our server?** The first sign-in that reaches a server pairs the computer with it (a key only that
  server can compute, kept in config.json `server_pairing`). Afterwards an address must prove it knows the key
  before a badge and PIN are sent to it, and before a moved server's new address is adopted. An impostor that
  merely answers on the network gets nothing ("...not the server this computer was connected to"). After a
  server reinstall with a new `GPUSA_JWT_SECRET`, confirm it once with "Change the address" on the sign-in
  screen (typing an address on purpose forgets the pairing). Details: `database/server_identity.py`,
  `server/gpusa_server/identity_routes.py`.
- **Root is created on the server PC or with the setup code.** Without `GPUSA_SETUP_CODE` the server lets only
  itself (127.0.0.1, not through a proxy) create the root user; another computer must type the code.
- **Plain http only inside a local network.** The apps refuse an `http://` address on the internet (badges,
  PINs and tokens would travel in the open); private, loopback, Tailscale (100.64/10) and local names are
  fine, anything else needs `https://`. `GPUSA_ALLOW_HTTP=1` lifts it for a test.
- Not covered: someone who can sit between a computer and the server on the same network can still read plain
  http. The answer to that is an https address (planned with the AWS move).
