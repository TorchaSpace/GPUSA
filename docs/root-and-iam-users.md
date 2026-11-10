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
