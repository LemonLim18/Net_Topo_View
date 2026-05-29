from config_engine.safety import validate_playbook_bundle


def test_bundle_module_family_accepts_juniper_module_in_entrypoint_tasks():
    run_path = "playbooks/generated/job_326/playbook.yml"
    bundle = {
        "entrypoint_path": run_path,
        "files": [
            {
                "path": run_path,
                "kind": "playbook",
                "content": (
                    "---\n"
                    "- name: Configure interfaces (Juniper)\n"
                    "  hosts: Juniper_Acc_4\n"
                    "  gather_facts: false\n"
                    "  tasks:\n"
                    "    - name: Shutdown interface\n"
                    "      juniper.device.junos_interfaces:\n"
                    "        config:\n"
                    "          - name: ge-0/0/1\n"
                    "            enabled: false\n"
                    "        state: merged\n"
                    "  roles:\n"
                    "    - generated/job_326/interface\n"
                ),
            },
            {
                "path": "roles/generated/job_326/interface/tasks/main.yml",
                "kind": "task",
                "content": "- name: Dispatch Juniper\n  ansible.builtin.include_tasks: juniper.yml\n",
            },
            {
                "path": "roles/generated/job_326/interface/tasks/cisco.yml",
                "kind": "task",
                "content": "- name: Cisco placeholder\n  ansible.builtin.debug:\n    msg: cisco\n",
            },
            {
                "path": "roles/generated/job_326/interface/tasks/huawei.yml",
                "kind": "task",
                "content": "- name: Huawei placeholder\n  ansible.builtin.debug:\n    msg: huawei\n",
            },
            {
                "path": "roles/generated/job_326/interface/tasks/juniper.yml",
                "kind": "task",
                "content": "- name: Juniper placeholder\n  ansible.builtin.debug:\n    msg: juniper\n",
            },
        ],
        "manifest": {"generator": "test"},
    }

    report = validate_playbook_bundle(
        bundle=bundle,
        vendor="juniper_junos",
        feature="interface",
        target_hosts=["Juniper_Acc_4"],
        execution_engine="ansible_native",
    )

    assert report["passed"] is True
    assert report["errors"] == []


def test_single_vendor_juniper_bundle_requires_only_main_and_juniper_tasks():
    run_path = "playbooks/generated/job_sv/playbook.yml"
    bundle = {
        "entrypoint_path": run_path,
        "files": [
            {
                "path": run_path,
                "kind": "playbook",
                "content": (
                    "---\n"
                    "- name: Configure interfaces (Juniper)\n"
                    "  hosts: Juniper_Acc_4\n"
                    "  gather_facts: false\n"
                    "  roles:\n"
                    "    - generated/job_sv/interface\n"
                ),
            },
            {
                "path": "roles/generated/job_sv/interface/tasks/main.yml",
                "kind": "task",
                "content": "- name: Dispatch Juniper\n  ansible.builtin.include_tasks: juniper.yml\n",
            },
            {
                "path": "roles/generated/job_sv/interface/tasks/juniper.yml",
                "kind": "task",
                "content": (
                    "- name: Shutdown interface\n"
                    "  juniper.device.junos_interfaces:\n"
                    "    config:\n"
                    "      - name: ge-0/0/1\n"
                    "        enabled: false\n"
                    "    state: merged\n"
                ),
            },
        ],
        "manifest": {"generator": "test"},
    }

    report = validate_playbook_bundle(
        bundle=bundle,
        vendor="juniper_junos",
        feature="interface",
        target_hosts=["Juniper_Acc_4"],
        execution_engine="ansible_native",
    )

    assert report["passed"] is True
    assert report["errors"] == []


def test_multi_vendor_bundle_still_requires_all_vendor_task_files():
    run_path = "playbooks/generated/job_mv/playbook.yml"
    bundle = {
        "entrypoint_path": run_path,
        "files": [
            {
                "path": run_path,
                "kind": "playbook",
                "content": (
                    "---\n"
                    "- name: Configure interfaces (multi)\n"
                    "  hosts: all\n"
                    "  gather_facts: false\n"
                    "  roles:\n"
                    "    - generated/job_mv/interface\n"
                ),
            },
            {
                "path": "roles/generated/job_mv/interface/tasks/main.yml",
                "kind": "task",
                "content": "- name: Dispatch Cisco\n  ansible.builtin.include_tasks: cisco.yml\n",
            },
            {
                "path": "roles/generated/job_mv/interface/tasks/juniper.yml",
                "kind": "task",
                "content": (
                    "- name: Juniper interface task\n"
                    "  juniper.device.junos_interfaces:\n"
                    "    config: []\n"
                    "    state: merged\n"
                ),
            },
        ],
        "manifest": {"generator": "test"},
    }

    report = validate_playbook_bundle(
        bundle=bundle,
        vendor="multi_vendor",
        feature="interface",
        target_hosts=["Juniper_Acc_4", "Cisco_Acc_1"],
        execution_engine="ansible_native",
    )

    assert report["passed"] is False
    assert any("missing required role task file" in err.lower() for err in report["errors"])
