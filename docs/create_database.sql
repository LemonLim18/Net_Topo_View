-- NetTopoView Complete SQLite Database Schema
-- Generated based on current implementation
-- Compatible with SQLite 3.35+

-- ============================================================================
-- Core Authentication & Users
-- ============================================================================

CREATE TABLE IF NOT EXISTS USERS (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    role TEXT NOT NULL CHECK(role IN ('admin', 'operator', 'viewer')),
    is_active BOOLEAN DEFAULT 1,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_username ON USERS(username);
CREATE INDEX IF NOT EXISTS idx_users_role ON USERS(role);
CREATE INDEX IF NOT EXISTS idx_users_active ON USERS(is_active);

-- ============================================================================
-- Network Inventory
-- ============================================================================

CREATE TABLE IF NOT EXISTS DEVICES (
    device_id TEXT PRIMARY KEY,
    hostname TEXT UNIQUE NOT NULL,
    device_type TEXT,
    vendor TEXT,
    model TEXT,
    ip_address TEXT,
    serial_number TEXT,
    site_location TEXT,
    mgmt_vrf TEXT,
    platform TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'maintenance')),
    last_seen TIMESTAMP,
    reachable BOOLEAN DEFAULT 0,
    os_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_devices_hostname ON DEVICES(hostname);
CREATE INDEX IF NOT EXISTS idx_devices_status ON DEVICES(status);
CREATE INDEX IF NOT EXISTS idx_devices_vendor ON DEVICES(vendor);
CREATE INDEX IF NOT EXISTS idx_devices_site ON DEVICES(site_location);

CREATE TABLE IF NOT EXISTS INTERFACES (
    interface_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    interface_name TEXT NOT NULL,
    interface_type TEXT,
    ip_address TEXT,
    subnet_mask TEXT,
    mac_address TEXT,
    speed TEXT,
    mtu INTEGER,
    description TEXT,
    status TEXT DEFAULT 'up' CHECK(status IN ('up', 'down', 'disabled')),
    enabled BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id) ON DELETE CASCADE,
    UNIQUE(device_id, interface_name)
);

CREATE INDEX IF NOT EXISTS idx_interfaces_device ON INTERFACES(device_id);
CREATE INDEX IF NOT EXISTS idx_interfaces_status ON INTERFACES(status);
CREATE INDEX IF NOT EXISTS idx_interfaces_ip ON INTERFACES(ip_address);

CREATE TABLE IF NOT EXISTS LINKS (
    link_id TEXT PRIMARY KEY,
    source_interface_id TEXT NOT NULL,
    target_interface_id TEXT NOT NULL,
    source_device_id TEXT NOT NULL,
    target_device_id TEXT NOT NULL,
    link_type TEXT,
    discovered_by TEXT,
    status TEXT DEFAULT 'active',
    bandwidth TEXT,
    last_verified TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_interface_id) REFERENCES INTERFACES(interface_id),
    FOREIGN KEY (target_interface_id) REFERENCES INTERFACES(interface_id),
    FOREIGN KEY (source_device_id) REFERENCES DEVICES(device_id),
    FOREIGN KEY (target_device_id) REFERENCES DEVICES(device_id)
);

CREATE INDEX IF NOT EXISTS idx_links_source ON LINKS(source_device_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON LINKS(target_device_id);
CREATE INDEX IF NOT EXISTS idx_links_type ON LINKS(link_type);

-- ============================================================================
-- Configuration Management
-- ============================================================================

CREATE TABLE IF NOT EXISTS CONFIG_SNAPSHOTS (
    snapshot_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    config_type TEXT CHECK(config_type IN ('running', 'startup', 'golden', 'baseline')),
    config_content TEXT NOT NULL,
    config_hash TEXT,
    file_size INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    collected_by TEXT,
    is_baseline BOOLEAN DEFAULT 0,
    is_golden BOOLEAN DEFAULT 0,
    compliance_status TEXT,
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_config_snapshots_device ON CONFIG_SNAPSHOTS(device_id);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_type ON CONFIG_SNAPSHOTS(config_type);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_hash ON CONFIG_SNAPSHOTS(config_hash);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_created ON CONFIG_SNAPSHOTS(created_at);

CREATE TABLE IF NOT EXISTS INTENTS (
    intent_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    intent_description TEXT NOT NULL,
    intent_type TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'executing', 'completed', 'failed')),
    priority TEXT DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high', 'critical')),
    affected_devices TEXT,
    requested_by_user_id TEXT,
    approved_by_user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (requested_by_user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (approved_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_intents_user ON INTENTS(user_id);
CREATE INDEX IF NOT EXISTS idx_intents_status ON INTENTS(status);
CREATE INDEX IF NOT EXISTS idx_intents_created ON INTENTS(created_at);

CREATE TABLE IF NOT EXISTS CHANGE_REQUESTS (
    change_id TEXT PRIMARY KEY,
    intent_id TEXT,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    scope TEXT,
    priority TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'submitted', 'approved', 'rejected', 'executing', 'completed')),
    deployment_window_start TIMESTAMP,
    deployment_window_end TIMESTAMP,
    estimated_impact TEXT,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_by_user_id TEXT NOT NULL,
    approved_by_user_id TEXT,
    FOREIGN KEY (intent_id) REFERENCES INTENTS(intent_id),
    FOREIGN KEY (user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (approved_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_change_requests_status ON CHANGE_REQUESTS(status);
CREATE INDEX IF NOT EXISTS idx_change_requests_user ON CHANGE_REQUESTS(user_id);
CREATE INDEX IF NOT EXISTS idx_change_requests_intent ON CHANGE_REQUESTS(intent_id);
CREATE INDEX IF NOT EXISTS idx_change_requests_requested ON CHANGE_REQUESTS(requested_at);

CREATE TABLE IF NOT EXISTS JOBS (
    job_id TEXT PRIMARY KEY,
    change_id TEXT,
    device_id TEXT NOT NULL,
    job_type TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'completed', 'failed', 'rolled_back')),
    priority INTEGER DEFAULT 100,
    execution_stage TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    execution_time_seconds INTEGER,
    result_summary TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    can_rollback BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (change_id) REFERENCES CHANGE_REQUESTS(change_id),
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_device ON JOBS(device_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON JOBS(status);
CREATE INDEX IF NOT EXISTS idx_jobs_change ON JOBS(change_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON JOBS(created_at);

CREATE TABLE IF NOT EXISTS JOB_STEPS (
    step_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    step_type TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    execution_command TEXT,
    output_log TEXT,
    error_log TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_seconds INTEGER,
    FOREIGN KEY (job_id) REFERENCES JOBS(job_id) ON DELETE CASCADE,
    UNIQUE(job_id, step_number)
);

CREATE INDEX IF NOT EXISTS idx_job_steps_job ON JOB_STEPS(job_id);
CREATE INDEX IF NOT EXISTS idx_job_steps_status ON JOB_STEPS(status);

CREATE TABLE IF NOT EXISTS CONFIG_PROPOSALS (
    proposal_id TEXT PRIMARY KEY,
    job_id TEXT,
    device_id TEXT NOT NULL,
    baseline_snapshot_id TEXT,
    proposed_config TEXT NOT NULL,
    proposed_config_hash TEXT,
    generation_method TEXT,
    ai_model_used TEXT,
    change_summary TEXT,
    confidence_score REAL,
    risk_level TEXT CHECK(risk_level IN ('low', 'medium', 'high', 'critical')),
    approval_status TEXT DEFAULT 'pending' CHECK(approval_status IN ('pending', 'approved', 'rejected', 'deployed')),
    approved_by_user_id TEXT,
    deployment_status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    deployed_at TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES JOBS(job_id),
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id),
    FOREIGN KEY (baseline_snapshot_id) REFERENCES CONFIG_SNAPSHOTS(snapshot_id),
    FOREIGN KEY (approved_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_config_proposals_device ON CONFIG_PROPOSALS(device_id);
CREATE INDEX IF NOT EXISTS idx_config_proposals_status ON CONFIG_PROPOSALS(approval_status);
CREATE INDEX IF NOT EXISTS idx_config_proposals_created ON CONFIG_PROPOSALS(created_at);

-- ============================================================================
-- Compliance & Drift
-- ============================================================================

CREATE TABLE IF NOT EXISTS POLICIES (
    policy_id TEXT PRIMARY KEY,
    policy_name TEXT UNIQUE NOT NULL,
    description TEXT,
    policy_type TEXT,
    policy_scope TEXT,
    scope_value TEXT,
    rules TEXT NOT NULL,
    enforcement_level TEXT,
    enabled BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id TEXT NOT NULL,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_policies_enabled ON POLICIES(enabled);
CREATE INDEX IF NOT EXISTS idx_policies_type ON POLICIES(policy_type);

CREATE TABLE IF NOT EXISTS COMPLIANCE_JOBS (
    job_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    job_type TEXT,
    status TEXT DEFAULT 'running' CHECK(status IN ('running', 'completed', 'failed')),
    total_devices INTEGER,
    compliant_devices INTEGER,
    non_compliant_devices INTEGER,
    unknown_devices INTEGER,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    created_by_user_id TEXT,
    FOREIGN KEY (policy_id) REFERENCES POLICIES(policy_id),
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_compliance_jobs_policy ON COMPLIANCE_JOBS(policy_id);
CREATE INDEX IF NOT EXISTS idx_compliance_jobs_status ON COMPLIANCE_JOBS(status);
CREATE INDEX IF NOT EXISTS idx_compliance_jobs_started ON COMPLIANCE_JOBS(started_at);

CREATE TABLE IF NOT EXISTS COMPLIANCE_RESULTS (
    result_id TEXT PRIMARY KEY,
    compliance_job_id TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    compliance_status TEXT CHECK(compliance_status IN ('compliant', 'non-compliant', 'error', 'unknown')),
    violations TEXT,
    violation_count INTEGER DEFAULT 0,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    remediation_status TEXT,
    remediation_notes TEXT,
    FOREIGN KEY (compliance_job_id) REFERENCES COMPLIANCE_JOBS(job_id),
    FOREIGN KEY (policy_id) REFERENCES POLICIES(policy_id),
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id)
);

CREATE INDEX IF NOT EXISTS idx_compliance_results_device ON COMPLIANCE_RESULTS(device_id);
CREATE INDEX IF NOT EXISTS idx_compliance_results_status ON COMPLIANCE_RESULTS(compliance_status);
CREATE INDEX IF NOT EXISTS idx_compliance_results_job ON COMPLIANCE_RESULTS(compliance_job_id);

CREATE TABLE IF NOT EXISTS DRIFT_EVENTS (
    event_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    snapshot_id_before TEXT,
    snapshot_id_after TEXT,
    drift_type TEXT,
    severity TEXT CHECK(severity IN ('info', 'warning', 'critical')),
    drift_description TEXT,
    changes_detected TEXT,
    auto_remediation_attempted BOOLEAN DEFAULT 0,
    auto_remediation_status TEXT,
    manual_remediation_required BOOLEAN DEFAULT 0,
    acknowledged BOOLEAN DEFAULT 0,
    acknowledged_by_user_id TEXT,
    acknowledged_at TIMESTAMP,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id),
    FOREIGN KEY (snapshot_id_before) REFERENCES CONFIG_SNAPSHOTS(snapshot_id),
    FOREIGN KEY (snapshot_id_after) REFERENCES CONFIG_SNAPSHOTS(snapshot_id),
    FOREIGN KEY (acknowledged_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_drift_events_device ON DRIFT_EVENTS(device_id);
CREATE INDEX IF NOT EXISTS idx_drift_events_severity ON DRIFT_EVENTS(severity);
CREATE INDEX IF NOT EXISTS idx_drift_events_detected ON DRIFT_EVENTS(detected_at);

CREATE TABLE IF NOT EXISTS POLICY_AUDIT_LOG (
    log_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    action TEXT CHECK(action IN ('created', 'updated', 'deleted', 'enabled', 'disabled')),
    user_id TEXT NOT NULL,
    previous_value TEXT,
    new_value TEXT,
    change_reason TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (policy_id) REFERENCES POLICIES(policy_id),
    FOREIGN KEY (user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_policy_audit_log_policy ON POLICY_AUDIT_LOG(policy_id);
CREATE INDEX IF NOT EXISTS idx_policy_audit_log_user ON POLICY_AUDIT_LOG(user_id);
CREATE INDEX IF NOT EXISTS idx_policy_audit_log_timestamp ON POLICY_AUDIT_LOG(timestamp);

-- ============================================================================
-- Security
-- ============================================================================

CREATE TABLE IF NOT EXISTS SECURITY_AUDITS (
    audit_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    audit_type TEXT,
    framework TEXT,
    status TEXT DEFAULT 'running' CHECK(status IN ('running', 'completed', 'failed')),
    total_checks INTEGER,
    passed_checks INTEGER,
    failed_checks INTEGER,
    skipped_checks INTEGER,
    compliance_score REAL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id)
);

CREATE INDEX IF NOT EXISTS idx_security_audits_device ON SECURITY_AUDITS(device_id);
CREATE INDEX IF NOT EXISTS idx_security_audits_type ON SECURITY_AUDITS(audit_type);
CREATE INDEX IF NOT EXISTS idx_security_audits_completed ON SECURITY_AUDITS(completed_at);

CREATE TABLE IF NOT EXISTS AUDIT_FINDINGS (
    finding_id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    check_id TEXT,
    check_description TEXT,
    severity TEXT CHECK(severity IN ('info', 'low', 'medium', 'high', 'critical')),
    status TEXT CHECK(status IN ('passed', 'failed', 'not_applicable')),
    remediation_text TEXT,
    evidence TEXT,
    reviewed BOOLEAN DEFAULT 0,
    reviewed_by_user_id TEXT,
    reviewed_at TIMESTAMP,
    FOREIGN KEY (audit_id) REFERENCES SECURITY_AUDITS(audit_id),
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id),
    FOREIGN KEY (reviewed_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_findings_audit ON AUDIT_FINDINGS(audit_id);
CREATE INDEX IF NOT EXISTS idx_audit_findings_severity ON AUDIT_FINDINGS(severity);
CREATE INDEX IF NOT EXISTS idx_audit_findings_status ON AUDIT_FINDINGS(status);

CREATE TABLE IF NOT EXISTS SECURITY_EVENTS (
    event_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    finding_id TEXT,
    event_type TEXT,
    severity TEXT CHECK(severity IN ('info', 'warning', 'critical')),
    event_description TEXT,
    source_ip TEXT,
    source_port INTEGER,
    event_details TEXT,
    response_action TEXT,
    response_status TEXT,
    acknowledged BOOLEAN DEFAULT 0,
    acknowledged_by_user_id TEXT,
    acknowledged_at TIMESTAMP,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id),
    FOREIGN KEY (finding_id) REFERENCES AUDIT_FINDINGS(finding_id),
    FOREIGN KEY (acknowledged_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_security_events_device ON SECURITY_EVENTS(device_id);
CREATE INDEX IF NOT EXISTS idx_security_events_severity ON SECURITY_EVENTS(severity);
CREATE INDEX IF NOT EXISTS idx_security_events_detected ON SECURITY_EVENTS(detected_at);

-- ============================================================================
-- Credentials
-- ============================================================================

CREATE TABLE IF NOT EXISTS DEVICE_CREDENTIALS (
    credential_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    credential_type TEXT CHECK(credential_type IN ('ssh', 'snmp', 'api_key', 'netconf')),
    username TEXT,
    password_hash TEXT,
    secret_key TEXT,
    api_key_hash TEXT,
    snmp_version TEXT,
    snmp_community TEXT,
    snmp_username TEXT,
    enable_password_hash TEXT,
    encrypted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rotation_required BOOLEAN DEFAULT 0,
    last_rotated TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES DEVICES(device_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id),
    UNIQUE(device_id, credential_type)
);

CREATE INDEX IF NOT EXISTS idx_device_credentials_device ON DEVICE_CREDENTIALS(device_id);

-- ============================================================================
-- Onboarding & ZTP
-- ============================================================================

CREATE TABLE IF NOT EXISTS ZTP_PUBLISH_JOBS (
    job_id TEXT PRIMARY KEY,
    job_name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'publishing', 'completed', 'failed')),
    total_items INTEGER,
    successful_items INTEGER,
    failed_items INTEGER,
    created_by_user_id TEXT NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_ztp_publish_jobs_status ON ZTP_PUBLISH_JOBS(status);
CREATE INDEX IF NOT EXISTS idx_ztp_publish_jobs_created ON ZTP_PUBLISH_JOBS(created_at);

CREATE TABLE IF NOT EXISTS ZTP_PUBLISH_ITEMS (
    item_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    device_serial TEXT NOT NULL,
    configuration_template_name TEXT,
    target_platform TEXT,
    boot_file_path TEXT,
    device_config TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'published', 'provisioned', 'failed')),
    published_at TIMESTAMP,
    provisioned_at TIMESTAMP,
    error_message TEXT,
    FOREIGN KEY (job_id) REFERENCES ZTP_PUBLISH_JOBS(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ztp_publish_items_job ON ZTP_PUBLISH_ITEMS(job_id);
CREATE INDEX IF NOT EXISTS idx_ztp_publish_items_serial ON ZTP_PUBLISH_ITEMS(device_serial);
CREATE INDEX IF NOT EXISTS idx_ztp_publish_items_status ON ZTP_PUBLISH_ITEMS(status);

CREATE TABLE IF NOT EXISTS ZTP_DHCP_MAPPINGS (
    mapping_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    mac_address TEXT UNIQUE NOT NULL,
    boot_file TEXT NOT NULL,
    dhcp_server_group TEXT,
    active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (item_id) REFERENCES ZTP_PUBLISH_ITEMS(item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ztp_dhcp_mappings_mac ON ZTP_DHCP_MAPPINGS(mac_address);
CREATE INDEX IF NOT EXISTS idx_ztp_dhcp_mappings_item ON ZTP_DHCP_MAPPINGS(item_id);
CREATE INDEX IF NOT EXISTS idx_ztp_dhcp_mappings_active ON ZTP_DHCP_MAPPINGS(active);

-- ============================================================================
-- Agent & Advanced Features
-- ============================================================================

CREATE TABLE IF NOT EXISTS AGENT_PLANS (
    plan_id TEXT PRIMARY KEY,
    intent_id TEXT,
    agent_name TEXT,
    plan_type TEXT,
    status TEXT DEFAULT 'planning' CHECK(status IN ('planning', 'executing', 'completed', 'failed', 'paused')),
    plan_description TEXT,
    plan_steps TEXT,
    context_state TEXT,
    reasoning_trace TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (intent_id) REFERENCES INTENTS(intent_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_plans_status ON AGENT_PLANS(status);
CREATE INDEX IF NOT EXISTS idx_agent_plans_intent ON AGENT_PLANS(intent_id);

CREATE TABLE IF NOT EXISTS CONTEXT_EVENT_LOG (
    log_id TEXT PRIMARY KEY,
    plan_id TEXT,
    event_type TEXT,
    event_description TEXT,
    old_context TEXT,
    new_context TEXT,
    triggered_by TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES AGENT_PLANS(plan_id)
);

CREATE INDEX IF NOT EXISTS idx_context_event_log_plan ON CONTEXT_EVENT_LOG(plan_id);
CREATE INDEX IF NOT EXISTS idx_context_event_log_timestamp ON CONTEXT_EVENT_LOG(timestamp);

CREATE TABLE IF NOT EXISTS ANSIBLE_CHAT_SESSIONS (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    plan_id TEXT,
    session_title TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'completed', 'archived')),
    total_messages INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (plan_id) REFERENCES AGENT_PLANS(plan_id)
);

CREATE INDEX IF NOT EXISTS idx_ansible_chat_sessions_user ON ANSIBLE_CHAT_SESSIONS(user_id);
CREATE INDEX IF NOT EXISTS idx_ansible_chat_sessions_status ON ANSIBLE_CHAT_SESSIONS(status);
CREATE INDEX IF NOT EXISTS idx_ansible_chat_sessions_created ON ANSIBLE_CHAT_SESSIONS(created_at);

CREATE TABLE IF NOT EXISTS ANSIBLE_CHAT_MESSAGES (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    sender_type TEXT CHECK(sender_type IN ('user', 'agent', 'system')),
    sender_id TEXT,
    message_content TEXT NOT NULL,
    message_type TEXT,
    metadata TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES ANSIBLE_CHAT_SESSIONS(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ansible_chat_messages_session ON ANSIBLE_CHAT_MESSAGES(session_id);
CREATE INDEX IF NOT EXISTS idx_ansible_chat_messages_timestamp ON ANSIBLE_CHAT_MESSAGES(timestamp);

CREATE TABLE IF NOT EXISTS SDN_CONTROLLERS (
    controller_id TEXT PRIMARY KEY,
    controller_name TEXT UNIQUE NOT NULL,
    controller_type TEXT,
    ip_address TEXT,
    port INTEGER,
    username TEXT,
    password_hash TEXT,
    api_endpoint TEXT,
    status TEXT DEFAULT 'inactive' CHECK(status IN ('active', 'inactive', 'error')),
    managed_devices TEXT,
    last_sync_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sdn_controllers_status ON SDN_CONTROLLERS(status);
CREATE INDEX IF NOT EXISTS idx_sdn_controllers_type ON SDN_CONTROLLERS(controller_type);

CREATE TABLE IF NOT EXISTS COMMON_SERVICE_PROFILES (
    profile_id TEXT PRIMARY KEY,
    profile_name TEXT UNIQUE NOT NULL,
    profile_type TEXT,
    description TEXT,
    configuration TEXT NOT NULL,
    applied_to_devices TEXT,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_common_service_profiles_type ON COMMON_SERVICE_PROFILES(profile_type);
CREATE INDEX IF NOT EXISTS idx_common_service_profiles_created ON COMMON_SERVICE_PROFILES(created_at);

-- ============================================================================
-- Prompts Management
-- ============================================================================

CREATE TABLE IF NOT EXISTS PROMPTS (
    prompt_id TEXT PRIMARY KEY,
    prompt_name TEXT UNIQUE NOT NULL,
    prompt_category TEXT,
    prompt_type TEXT,
    description TEXT,
    prompt_content TEXT NOT NULL,
    prompt_hash TEXT,
    version INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT 1,
    is_template BOOLEAN DEFAULT 0,
    template_variables TEXT,
    required_context TEXT,
    output_format TEXT,
    confidence_threshold REAL,
    model_compatibility TEXT,
    created_by_user_id TEXT NOT NULL,
    approved_by_user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usage_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    metadata TEXT,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (approved_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_prompts_category ON PROMPTS(prompt_category);
CREATE INDEX IF NOT EXISTS idx_prompts_active ON PROMPTS(is_active);
CREATE INDEX IF NOT EXISTS idx_prompts_template ON PROMPTS(is_template);

CREATE TABLE IF NOT EXISTS PROMPT_VERSIONS (
    version_id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    prompt_content TEXT NOT NULL,
    prompt_hash TEXT,
    change_reason TEXT,
    changed_by_user_id TEXT NOT NULL,
    previous_version_id TEXT,
    is_rollback BOOLEAN DEFAULT 0,
    rollback_from_version INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by_user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (previous_version_id) REFERENCES PROMPT_VERSIONS(version_id),
    UNIQUE(prompt_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt ON PROMPT_VERSIONS(prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_created ON PROMPT_VERSIONS(created_at);

CREATE TABLE IF NOT EXISTS PROMPT_EXECUTION_HISTORY (
    execution_id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    execution_context TEXT,
    input_data TEXT,
    generated_output TEXT,
    output_quality_score REAL,
    execution_status TEXT,
    error_message TEXT,
    execution_time_ms INTEGER,
    tokens_used INTEGER,
    cost REAL,
    model_used TEXT,
    feedback_rating INTEGER,
    feedback_notes TEXT,
    was_approved BOOLEAN,
    was_deployed BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_execution_history_prompt ON PROMPT_EXECUTION_HISTORY(prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_execution_history_user ON PROMPT_EXECUTION_HISTORY(user_id);
CREATE INDEX IF NOT EXISTS idx_prompt_execution_history_status ON PROMPT_EXECUTION_HISTORY(execution_status);
CREATE INDEX IF NOT EXISTS idx_prompt_execution_history_created ON PROMPT_EXECUTION_HISTORY(created_at);

CREATE TABLE IF NOT EXISTS PROMPT_TEMPLATES (
    template_id TEXT PRIMARY KEY,
    template_name TEXT UNIQUE NOT NULL,
    template_category TEXT,
    description TEXT,
    base_prompt TEXT NOT NULL,
    variable_mappings TEXT,
    example_variables TEXT,
    example_output TEXT,
    is_public BOOLEAN DEFAULT 0,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usage_count INTEGER DEFAULT 0,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_category ON PROMPT_TEMPLATES(template_category);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_public ON PROMPT_TEMPLATES(is_public);

CREATE TABLE IF NOT EXISTS PROMPT_CHAINS (
    chain_id TEXT PRIMARY KEY,
    chain_name TEXT UNIQUE NOT NULL,
    description TEXT,
    chain_type TEXT,
    prompts_order TEXT NOT NULL,
    chain_status TEXT DEFAULT 'active' CHECK(chain_status IN ('active', 'inactive', 'testing')),
    total_steps INTEGER,
    estimated_execution_time_ms INTEGER,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_chains_type ON PROMPT_CHAINS(chain_type);
CREATE INDEX IF NOT EXISTS idx_prompt_chains_status ON PROMPT_CHAINS(chain_status);

CREATE TABLE IF NOT EXISTS PROMPT_CHAIN_EXECUTIONS (
    execution_id TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    execution_context TEXT,
    chain_status TEXT,
    current_step INTEGER,
    total_steps INTEGER,
    step_results TEXT,
    final_output TEXT,
    execution_time_ms INTEGER,
    total_tokens_used INTEGER,
    total_cost REAL,
    error_at_step INTEGER,
    error_message TEXT,
    was_approved BOOLEAN,
    was_deployed BOOLEAN,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (chain_id) REFERENCES PROMPT_CHAINS(chain_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_chain_executions_chain ON PROMPT_CHAIN_EXECUTIONS(chain_id);
CREATE INDEX IF NOT EXISTS idx_prompt_chain_executions_user ON PROMPT_CHAIN_EXECUTIONS(user_id);
CREATE INDEX IF NOT EXISTS idx_prompt_chain_executions_status ON PROMPT_CHAIN_EXECUTIONS(chain_status);
CREATE INDEX IF NOT EXISTS idx_prompt_chain_executions_completed ON PROMPT_CHAIN_EXECUTIONS(completed_at);

CREATE TABLE IF NOT EXISTS PROMPT_FEEDBACK (
    feedback_id TEXT PRIMARY KEY,
    execution_id TEXT,
    prompt_id TEXT,
    user_id TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    feedback_type TEXT,
    feedback_text TEXT,
    improvements_suggested TEXT,
    was_helpful BOOLEAN,
    led_to_deployment BOOLEAN DEFAULT 0,
    is_public BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES PROMPT_EXECUTION_HISTORY(execution_id),
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id),
    FOREIGN KEY (user_id) REFERENCES USERS(user_id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_feedback_prompt ON PROMPT_FEEDBACK(prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_feedback_user ON PROMPT_FEEDBACK(user_id);
CREATE INDEX IF NOT EXISTS idx_prompt_feedback_rating ON PROMPT_FEEDBACK(rating);
CREATE INDEX IF NOT EXISTS idx_prompt_feedback_created ON PROMPT_FEEDBACK(created_at);

CREATE TABLE IF NOT EXISTS PROMPT_PERFORMANCE_METRICS (
    metric_id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    date_recorded DATE,
    total_executions INTEGER DEFAULT 0,
    successful_executions INTEGER DEFAULT 0,
    failed_executions INTEGER DEFAULT 0,
    average_quality_score REAL,
    average_execution_time_ms INTEGER,
    average_tokens_per_execution INTEGER,
    total_cost REAL,
    user_approval_rate REAL,
    deployment_rate REAL,
    average_rating REAL,
    recommendations_text TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    UNIQUE(prompt_id, date_recorded)
);

CREATE INDEX IF NOT EXISTS idx_prompt_performance_metrics_prompt ON PROMPT_PERFORMANCE_METRICS(prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_performance_metrics_date ON PROMPT_PERFORMANCE_METRICS(date_recorded);

-- ============================================================================
-- End of Database Schema
-- ============================================================================

-- Verify all tables created
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
