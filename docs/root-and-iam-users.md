# Root and IAM users (AWS-style sign-in)

- **Root** is the single account with the `admin` role. Its badge and PIN are chosen at first set-up
  (Admin's "create the first administrator" screen, or `python -m gpusa_server.create_admin`; the server's
  `GPUSA_SETUP_CODE` protects it). Nobody can be promoted to root or created as root afterwards.
- Root alone creates, re-roles, switches off, unlocks and deletes accounts, resets PINs, reads the sign-in
  log, sees the data location and the server address (Admin > Server), and uses recovery codes.
- **IAM users** are depot managers and cashiers created by root. They sign in to Admin, the depot console
  (depot managers) or the till (cashiers) with their own badge + PIN. In Admin they can use everything except
  the root-only parts above. Cashier/depot-manager shop and warehouse scoping still applies to their data.
- Every root sign-in is logged as `root_sign_in` (Settings > Sign-in activity); wrong PINs, lockouts and
  refusals are logged as before.
- Accounts that were administrators before this change keep working until root demotes or removes them.
