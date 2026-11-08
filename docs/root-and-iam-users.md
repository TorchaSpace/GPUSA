# Root and IAM users (AWS-style sign-in)

- **Root** is the single account flagged `accounts.is_root`. Its badge and PIN are chosen at first set-up
  (Admin's first-run screen, or `python -m gpusa_server.create_admin`; the server's `GPUSA_SETUP_CODE`
  protects it). Root can never be demoted, switched off or deleted, and nobody else becomes root.
- Root alone creates, re-roles, switches off, unlocks and deletes accounts, resets PINs, reads the sign-in
  log, uses recovery codes, and sees the data location and the server address (Admin > Server).
- Root adds IAM users in Admin > Settings: pick the account type (**Admin**, **Depot manager** or
  **Cashier**), then the badge ID of a person already added at the depot / till Staff page, and a PIN.
- **Admin** (IAM) opens the Admin dashboard, the depot console and the till with its own badge + PIN and
  sees every place; it has no account management and no server address. Depot managers and cashiers sign in
  to their own place only.
- Every root sign-in is logged as `root_sign_in`.
- Upgrade: migration v13 / alembic 0003 makes the oldest active administrator root. Existing extra admins
  become IAM Admins.
- The server address is saved per computer ("Use this server on every app of this computer" in the root-only
  Server page); once root ticked it, every sign-in on that PC, IAM included, uses the same database.
