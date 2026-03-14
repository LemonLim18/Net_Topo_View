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
- **Install Ansible Galaxy collections** used by playbooks (e.g. Cisco IOS, Juniper, Huawei):
  ```bash
  ansible-galaxy collection install cisco.ios
  ansible-galaxy collection install junipernetworks.junos
  ansible-galaxy collection install community.network   # for community.network.huawei_vrp, etc.
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
