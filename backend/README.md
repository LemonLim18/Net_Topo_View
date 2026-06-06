# NetTopoView Backend

## Running the backend (required for Ansible Studio Check/Deploy)

**Use the project venv** so that `ansible-runner` and other dependencies are available. If you run with system Python, Ansible dry-run will fail with "ansible-runner not installed".

**Windows (PowerShell):**
```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m uvicorn backend:app --reload
```

Or in one line without activating:
```powershell
cd backend
.\venv\Scripts\python.exe -m uvicorn backend:app --reload
```

**Linux/macOS:**
```bash
cd backend
source venv/bin/activate
python -m uvicorn backend:app --reload
```

If you see "ansible-runner not installed" in the app, restart the backend using the venv’s Python as above.

---

## Remote Ansible runner (recommended on Windows)

Ansible is not supported on Windows as a control node. To avoid local workarounds, run Ansible on a **Linux VM** and point the backend at it. No code changes; only configuration.

### 1. On the Linux VM (e.g. 192.168.1.251)

- Install Python 3, Ansible, and the runner script dependencies:
  ```bash
  sudo apt install -y python3 python3-pip python3-venv
  pip3 install --user fastapi uvicorn pyyaml ansible-core
  ```
- **Install Python libs** used by Ansible on the VM:
  ```bash
  pip3 install --user ncclient          # for Huawei/Juniper netconf (ce_vlan, junos_vlans)
  pip3 install --user ansible-pylibssh  # use libssh instead of paramiko for SSH (avoids "transport shut down or saw EOF")
  ```
- **Install Ansible Galaxy collections** used by playbooks (e.g. Cisco IOS, Juniper, Huawei):
  ```bash
  ansible-galaxy collection install cisco.ios
  ansible-galaxy collection install juniper.device      # Juniper Junos (replaces deprecated junipernetworks.junos)
  ansible-galaxy collection install community.network   # for Huawei CLI (ce_config)
  ```
- Copy from your NetTopoView repo the file **`backend/ansible/remote_runner_app.py`** to the VM (e.g. `/opt/nettopo-remote/runner.py`).
- Run the remote runner (listens on port 8765):
  ```bash
  cd /opt/nettopo-remote
  python3 runner.py
  ```
  Or with uvicorn directly:
  ```bash
  uvicorn runner:app --host 0.0.0.0 --port 8765
  ```
- Optional: restrict access with a shared secret. On the VM:
  ```bash
  export AUTH_HEADER="Bearer your-secret-token"
  python3 runner.py
  ```
  Then on the backend (see below) set `ANSIBLE_REMOTE_AUTH_HEADER` to the same value.

### 2. On the NetTopoView backend (Windows or any host)

In **`backend/.env`** (or your environment) set:

```env
# Remote Ansible runner (Linux VM). If set, Check/Deploy use this instead of local execution.
ANSIBLE_REMOTE_RUNNER_URL=http://192.168.1.251:8765

# Optional: if the remote runner uses AUTH_HEADER, set the same value here.
# ANSIBLE_REMOTE_AUTH_HEADER=Bearer your-secret-token
```

Use your VM’s IP or hostname and the port you started the runner on. Restart the backend. **Check** and **Deploy** in Ansible Studio will then run playbooks on the remote machine with no disruption to the UI or workflow.

---

## Credentials for Ansible (Check/Deploy)

- **Device Inventory / NetBox**  
  SSH username and password set **per device** in the app (or synced from NetBox custom fields) are injected into the Ansible inventory when you run Check or Deploy. **This is the primary source** — no need to duplicate them elsewhere.

- **Ansible Vault** (`ansible/vault/net_creds.yml`)  
  Used as a **fallback** when a device has no SSH user/password in the database, and for **enable password** (Cisco/Huawei/Juniper). The vault file is **gitignored**; you create and encrypt it yourself.  
  See **`ansible/vault/README.md`** for where the vault lives, how to create `net_creds.yml`, and how vault vs Device Inventory is used. There is **no sync** from Device Inventory into the vault — they are independent.

---

## Troubleshooting NETCONF (Juniper) — port 830

**Note:** Huawei in this project uses **CLI** (`network_cli` + `community.network.ce_config`), not NETCONF, so no port 830 or ncclient is required for Huawei. The section below applies to **Juniper** (and to optional NETCONF testing if you add it back).

If a Juniper play fails with **"command timeout"** or connection errors, NETCONF may be unreachable. Run these checks **from the same host that runs the remote Ansible runner** (the Linux VM).

### 1. Get the device IP

- In **Ansible Studio**, open the job (e.g. Job 13), go to the **Inventory** tab. Find the host (e.g. `Acc_Huawei_3`) and note **ansible_host** (the management IP).
- Or from NetBox / topology: use the management IP of the Huawei device.

### 2. Check reachability to port 830 (from the runner VM)

```bash
# Replace <IP> with Acc_Huawei_3’s ansible_host (e.g. 192.168.1.x)
nc -zv <IP> 830
```

If `nc` is not installed: `apt install netcat-openbsd` (Debian/Ubuntu) or use:

```bash
timeout 5 python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('<IP>', 830))
    print('Port 830 is open')
except Exception as e:
    print('Port 830 unreachable:', e)
finally:
    s.close()
"
```

- **Connection refused** → NETCONF not enabled on the device, or not listening on 830.
- **Timeout / No route** → Firewall or network blocking 830, or device down.

### 3. On the Huawei device — enable NETCONF and listen on 830

- **Huawei VRP / CloudEngine (CE)**  
  Ensure NETCONF over SSH is enabled and the SSH user has snetconf service. Example (CE-style):

  ```
  snetconf server enable
  ssh user <username>
  ssh user <username> authentication-type password
  ssh user <username> service-type snetconf stelnet
  netconf
   protocol inbound ssh port 830
  ```

  (Older VRP may use `netconf ssh server enable` and `ssh server port 830`; check vendor docs.)

- Confirm from the device that something is listening on 830 (e.g. `display netconf status` or equivalent).

### 4. Firewall

- On the **runner VM**: no outbound rule needed for 830 unless you lock egress.
- On the **device or intermediate firewalls**: allow **TCP 830** from the runner VM’s IP to the device management IP.

A small helper script is provided: **`ansible/check_netconf_reachability.sh`**. Copy it to the runner VM and run e.g. `./check_netconf_reachability.sh <IP>` (optional second arg: port, default 830).

**Test NETCONF connection (auth + first RPC):**
- **Ansible**: `ansible/playbooks/test_netconf_huawei.yml` — run with `-i "192.168.1.48,"` and `-e ansible_user=cognishield` `-e "ansible_password=Admin@Lab2026"` `-e ansible_port=830` (or use your inventory and limit). This playbook sets longer timeouts (60s connect, 90s command). If you use another playbook (e.g. test2.yml) and see "timeout 30 secs", run first: `export ANSIBLE_PERSISTENT_CONNECT_TIMEOUT=60 ANSIBLE_PERSISTENT_COMMAND_TIMEOUT=90`.
- **Python (ncclient)**: `ansible/test_netconf_huawei.py <host> [user] [password] [port]` — e.g. `python3 test_netconf_huawei.py 192.168.1.48 cognishield 'Admin@Lab2026' 830`. Requires `pip install ncclient`.

### 5. Port 830 works manually but Ansible still times out — credentials

If **`nc -zv <IP> 830`** and **`ssh <user>@<IP> -p 830 -s netconf`** (with password) both work from the runner VM, but the Ansible Huawei play fails with **"command timeout"**, Ansible is almost certainly using **different credentials** than the ones you used manually.

- **Use the same user and password** that work with `ssh -p 830 -s netconf`:
  - **Vault**: In `ansible/vault/net_creds.yml` set **`vault_huawei_vrp_user`** and **`vault_huawei_vrp_pass`** to the exact username and password that succeed when you run `ssh cognishield@192.168.1.48 -p 830 -s netconf`. (Huawei uses a separate `vault_huawei_vrp_*` section; it does not use `vault_net_pass` unless you leave the Huawei section blank.)
  - **Or Device Inventory**: In the app, set SSH username and password for the Huawei device (e.g. Acc_Huawei_3); those override the vault for that host.
- Wrong password often causes the NETCONF session to hang (device waiting or rejecting), which triggers the Ansible command timeout instead of a clear "auth failed" message.
- After changing the vault, re-run Check from Ansible Studio (the backend sends decrypted vault vars to the remote runner on each run).

### 6. Credentials are correct (cognishield / Admin@Lab2026) but Ansible still times out

- **Device Inventory overrides vault**: If the Huawei host (e.g. Acc_Huawei_3) has **SSH username or password** set in the app (Device Inventory, or synced from NetBox), those values **replace** the vault for that host. So even with correct `vault_huawei_vrp_user` / `vault_huawei_vrp_pass` in `net_creds.yml`, Ansible may be using different creds from the database. In **Ansible Studio** → open the job → **Inventory** tab. If an admin sees SSH user/password for that host, they are what Ansible uses. Either set them to **cognishield** / **Admin@Lab2026**, or clear SSH user/password for that device so the vault is used.
- The Huawei play uses **`ansible_command_timeout: 75`** so the first NETCONF RPC has a bit more time on slow devices (default 60s).
- If it still times out, on the **runner VM** run the playbook with **`-vvv`** and inspect where it hangs (e.g. connection open vs. first RPC).
