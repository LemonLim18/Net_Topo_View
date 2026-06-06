# Ansible Vault — network credentials

This folder holds **Ansible Vault–encrypted** credentials used by playbooks. Its contents (except this README) are **gitignored** so secrets are never committed.

## Do you need this?

- **If you use Device Inventory / NetBox** and set **SSH username and password per device** in the UI, those credentials are used **directly** for Ansible Check/Deploy. You do **not** need to duplicate them here.
- **Ansible Vault** here is used as a **fallback** when a device has **no** SSH user/password set in the database (e.g. shared `vault_net_user` / `vault_net_pass` for all devices), and for **enable password** (Cisco/Huawei/Juniper), which is not stored per device today.

So: **Device Inventory = primary source**; this vault = **fallback + enable password**.

## How credentials are used

| Source | Used for | Where |
|--------|----------|--------|
| **Device Inventory / NetBox** | SSH username & password per device | Injected into Ansible inventory by the backend when you run Check/Deploy. Synced from NetBox custom fields when you run NetBox sync. |
| **Ansible Vault** (`net_creds.yml`) | Shared SSH creds and **enable password** when device has no SSH creds in DB, or for playbook vars like `vault_net_user` | Loaded at playbook run time (or sent as extra_vars to the remote runner). |

There is **no automatic sync** from Device Inventory into this vault. The two are independent: use Device Inventory for per-device SSH, and this vault for shared/default creds and enable password.

## Creating the vault file

1. Create and edit the credentials file (plain YAML, then encrypt it):

   ```bash
   cd backend/ansible
   mkdir -p vault
   # Create plain YAML (do not commit this file unencrypted)
   ```

   **Template for `vault/net_creds.yml` (encrypt before committing):**

   ```yaml
   vault_net_user:     "your_ssh_username"
   vault_net_pass:     "your_ssh_password"
   vault_enable_pass:   "your_enable_password"

   # Optional per-vendor overrides:
   # vault_cisco_ios_user:   "cisco_admin"
   # vault_cisco_ios_pass:   "CiscoPass!"
   # vault_cisco_ios_enable_pass: "CiscoEnable!"
   # vault_juniper_junos_user: "junos_ops"
   # vault_juniper_junos_pass: "JunosPass!"
   # vault_huawei_vrp_user:  "vrp_admin"
   # vault_huawei_vrp_pass:  "HuaweiPass!"
   ```

2. **Encrypt (optional on Windows):**
   - **Linux / WSL:** Encrypt so the file is safe to store:
     ```bash
     ansible-vault encrypt vault/net_creds.yml
     ```
   - **Windows:** The `ansible-vault` CLI can fail (e.g. `os.get_blocking`). You can **keep the file as plain YAML**; the backend will load it. Ensure the file stays **gitignored** and is never committed.

3. **If you encrypted the file**, provide the vault password to the backend (one of):
   - Environment: `ANSIBLE_VAULT_PASSWORD=your_vault_password`
   - File: `ANSIBLE_VAULT_PASSWORD_FILE=/path/to/.vault_pass` (or put `.vault_pass` in this folder; it is gitignored)
   - Default: `backend/ansible/vault/.vault_pass` if it exists  
   **If you kept plain YAML (e.g. on Windows), you do not need to set a vault password.**

## Summary

- **Vault folder:** `backend/ansible/vault/` — you are here.  
- **Vault file:** `backend/ansible/vault/net_creds.yml` — create and encrypt it; it is gitignored.  
- **Device Inventory:** SSH user/password set per device in the app (or synced from NetBox) are used first; vault is fallback and for enable password.
