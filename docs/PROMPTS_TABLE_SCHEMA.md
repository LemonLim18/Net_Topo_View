# NetTopoView - Missing Tables Implementation

## PROMPTS Management System

These are the tables needed for managing AI prompts and their versions.

### 1. PROMPTS (Main Prompt Storage)
```sql
CREATE TABLE PROMPTS (
    prompt_id UUID PRIMARY KEY,
    prompt_name VARCHAR(255) UNIQUE NOT NULL,
    prompt_category VARCHAR(100), -- 'deployment', 'remediation', 'investigation', 'compliance_check', 'security_audit'
    prompt_type VARCHAR(100), -- 'system', 'user', 'assistant', 'template'
    description TEXT,
    prompt_content TEXT NOT NULL,
    prompt_hash VARCHAR(64), -- SHA256 for versioning
    version INT DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    is_template BOOLEAN DEFAULT FALSE, -- True if it's a reusable template
    template_variables TEXT, -- JSON with variable placeholders
    required_context TEXT, -- JSON with required context fields
    output_format VARCHAR(50), -- 'text', 'json', 'yaml', 'structured'
    confidence_threshold FLOAT, -- 0.0 to 1.0 for generated content validation
    model_compatibility TEXT, -- JSON array of compatible models
    created_by_user_id UUID NOT NULL,
    approved_by_user_id UUID,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usage_count INT DEFAULT 0,
    last_used_at TIMESTAMP,
    metadata TEXT, -- JSON with additional metadata
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (approved_by_user_id) REFERENCES USERS(user_id),
    INDEX idx_category (prompt_category),
    INDEX idx_active (is_active),
    INDEX idx_type (prompt_type),
    INDEX idx_template (is_template)
);
```

### 2. PROMPT_VERSIONS (Prompt History & Versioning)
```sql
CREATE TABLE PROMPT_VERSIONS (
    version_id UUID PRIMARY KEY,
    prompt_id UUID NOT NULL,
    version_number INT NOT NULL,
    prompt_content TEXT NOT NULL,
    prompt_hash VARCHAR(64),
    change_reason TEXT,
    changed_by_user_id UUID NOT NULL,
    previous_version_id UUID,
    is_rollback BOOLEAN DEFAULT FALSE,
    rollback_from_version INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by_user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (previous_version_id) REFERENCES PROMPT_VERSIONS(version_id),
    UNIQUE(prompt_id, version_number),
    INDEX idx_prompt (prompt_id),
    INDEX idx_created (created_at)
);
```

### 3. PROMPT_EXECUTION_HISTORY (Execution & Performance Tracking)
```sql
CREATE TABLE PROMPT_EXECUTION_HISTORY (
    execution_id UUID PRIMARY KEY,
    prompt_id UUID NOT NULL,
    prompt_version INT NOT NULL,
    user_id UUID NOT NULL,
    execution_context TEXT, -- JSON with input variables
    input_data TEXT, -- JSON with actual input
    generated_output TEXT, -- Generated content
    output_quality_score FLOAT, -- 0.0 to 1.0
    execution_status VARCHAR(50), -- 'success', 'partial', 'failed'
    error_message TEXT,
    execution_time_ms INT,
    tokens_used INT,
    cost DECIMAL(10, 4),
    model_used VARCHAR(100),
    feedback_rating INT, -- 1-5 star rating
    feedback_notes TEXT,
    was_approved BOOLEAN,
    was_deployed BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id),
    INDEX idx_prompt (prompt_id),
    INDEX idx_user (user_id),
    INDEX idx_status (execution_status),
    INDEX idx_created (created_at)
);
```

### 4. PROMPT_TEMPLATES (Reusable Template Definitions)
```sql
CREATE TABLE PROMPT_TEMPLATES (
    template_id UUID PRIMARY KEY,
    template_name VARCHAR(255) UNIQUE NOT NULL,
    template_category VARCHAR(100), -- 'configuration', 'troubleshooting', 'security', 'performance'
    description TEXT,
    base_prompt TEXT NOT NULL,
    variable_mappings TEXT, -- JSON with {placeholder: description}
    example_variables TEXT, -- JSON with example values
    example_output TEXT,
    is_public BOOLEAN DEFAULT FALSE,
    created_by_user_id UUID NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usage_count INT DEFAULT 0,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id),
    INDEX idx_category (template_category),
    INDEX idx_public (is_public)
);
```

### 5. PROMPT_CHAINS (Complex Multi-Step Prompts)
```sql
CREATE TABLE PROMPT_CHAINS (
    chain_id UUID PRIMARY KEY,
    chain_name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,
    chain_type VARCHAR(100), -- 'sequential', 'parallel', 'conditional', 'feedback_loop'
    prompts_order TEXT NOT NULL, -- JSON array of {prompt_id, order, condition}
    chain_status VARCHAR(50) DEFAULT 'active', -- 'active', 'inactive', 'testing'
    total_steps INT,
    estimated_execution_time_ms INT,
    created_by_user_id UUID NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id),
    INDEX idx_type (chain_type),
    INDEX idx_status (chain_status)
);
```

### 6. PROMPT_CHAIN_EXECUTIONS (Execution History for Prompt Chains)
```sql
CREATE TABLE PROMPT_CHAIN_EXECUTIONS (
    execution_id UUID PRIMARY KEY,
    chain_id UUID NOT NULL,
    user_id UUID NOT NULL,
    execution_context TEXT, -- JSON with chain context
    chain_status VARCHAR(50), -- 'running', 'completed', 'failed', 'paused'
    current_step INT,
    total_steps INT,
    step_results TEXT, -- JSON array with results from each step
    final_output TEXT,
    execution_time_ms INT,
    total_tokens_used INT,
    total_cost DECIMAL(10, 4),
    error_at_step INT,
    error_message TEXT,
    was_approved BOOLEAN,
    was_deployed BOOLEAN,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (chain_id) REFERENCES PROMPT_CHAINS(chain_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id),
    INDEX idx_chain (chain_id),
    INDEX idx_user (user_id),
    INDEX idx_status (chain_status),
    INDEX idx_completed (completed_at)
);
```

### 7. PROMPT_FEEDBACK (User Feedback for Prompt Quality)
```sql
CREATE TABLE PROMPT_FEEDBACK (
    feedback_id UUID PRIMARY KEY,
    execution_id UUID,
    prompt_id UUID,
    user_id UUID NOT NULL,
    rating INT NOT NULL, -- 1-5 stars
    feedback_type VARCHAR(100), -- 'accuracy', 'completeness', 'clarity', 'usefulness'
    feedback_text TEXT,
    improvements_suggested TEXT,
    was_helpful BOOLEAN,
    led_to_deployment BOOLEAN DEFAULT FALSE,
    is_public BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES PROMPT_EXECUTION_HISTORY(execution_id),
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id),
    FOREIGN KEY (user_id) REFERENCES USERS(user_id),
    INDEX idx_prompt (prompt_id),
    INDEX idx_user (user_id),
    INDEX idx_rating (rating),
    INDEX idx_created (created_at)
);
```

### 8. PROMPT_PERFORMANCE_METRICS (Analytics & Optimization)
```sql
CREATE TABLE PROMPT_PERFORMANCE_METRICS (
    metric_id UUID PRIMARY KEY,
    prompt_id UUID NOT NULL,
    date_recorded DATE,
    total_executions INT DEFAULT 0,
    successful_executions INT DEFAULT 0,
    failed_executions INT DEFAULT 0,
    average_quality_score FLOAT,
    average_execution_time_ms INT,
    average_tokens_per_execution INT,
    total_cost DECIMAL(12, 4),
    user_approval_rate FLOAT, -- 0.0 to 1.0
    deployment_rate FLOAT, -- 0.0 to 1.0
    average_rating FLOAT, -- 1-5
    recommendations_text TEXT, -- JSON with improvement suggestions
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    UNIQUE(prompt_id, date_recorded),
    INDEX idx_prompt (prompt_id),
    INDEX idx_date (date_recorded)
);
```

---

## SQL Creation Script

```sql
-- PROMPTS Management Tables
CREATE TABLE IF NOT EXISTS PROMPTS (
    prompt_id UUID PRIMARY KEY,
    prompt_name VARCHAR(255) UNIQUE NOT NULL,
    prompt_category VARCHAR(100),
    prompt_type VARCHAR(100),
    description TEXT,
    prompt_content TEXT NOT NULL,
    prompt_hash VARCHAR(64),
    version INT DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    is_template BOOLEAN DEFAULT FALSE,
    template_variables TEXT,
    required_context TEXT,
    output_format VARCHAR(50),
    confidence_threshold FLOAT,
    model_compatibility TEXT,
    created_by_user_id UUID NOT NULL,
    approved_by_user_id UUID,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usage_count INT DEFAULT 0,
    last_used_at TIMESTAMP,
    metadata TEXT,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (approved_by_user_id) REFERENCES USERS(user_id)
);

CREATE TABLE IF NOT EXISTS PROMPT_VERSIONS (
    version_id UUID PRIMARY KEY,
    prompt_id UUID NOT NULL,
    version_number INT NOT NULL,
    prompt_content TEXT NOT NULL,
    prompt_hash VARCHAR(64),
    change_reason TEXT,
    changed_by_user_id UUID NOT NULL,
    previous_version_id UUID,
    is_rollback BOOLEAN DEFAULT FALSE,
    rollback_from_version INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by_user_id) REFERENCES USERS(user_id),
    FOREIGN KEY (previous_version_id) REFERENCES PROMPT_VERSIONS(version_id),
    UNIQUE(prompt_id, version_number)
);

CREATE TABLE IF NOT EXISTS PROMPT_EXECUTION_HISTORY (
    execution_id UUID PRIMARY KEY,
    prompt_id UUID NOT NULL,
    prompt_version INT NOT NULL,
    user_id UUID NOT NULL,
    execution_context TEXT,
    input_data TEXT,
    generated_output TEXT,
    output_quality_score FLOAT,
    execution_status VARCHAR(50),
    error_message TEXT,
    execution_time_ms INT,
    tokens_used INT,
    cost DECIMAL(10, 4),
    model_used VARCHAR(100),
    feedback_rating INT,
    feedback_notes TEXT,
    was_approved BOOLEAN,
    was_deployed BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id)
);

CREATE TABLE IF NOT EXISTS PROMPT_TEMPLATES (
    template_id UUID PRIMARY KEY,
    template_name VARCHAR(255) UNIQUE NOT NULL,
    template_category VARCHAR(100),
    description TEXT,
    base_prompt TEXT NOT NULL,
    variable_mappings TEXT,
    example_variables TEXT,
    example_output TEXT,
    is_public BOOLEAN DEFAULT FALSE,
    created_by_user_id UUID NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usage_count INT DEFAULT 0,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id)
);

CREATE TABLE IF NOT EXISTS PROMPT_CHAINS (
    chain_id UUID PRIMARY KEY,
    chain_name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,
    chain_type VARCHAR(100),
    prompts_order TEXT NOT NULL,
    chain_status VARCHAR(50) DEFAULT 'active',
    total_steps INT,
    estimated_execution_time_ms INT,
    created_by_user_id UUID NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by_user_id) REFERENCES USERS(user_id)
);

CREATE TABLE IF NOT EXISTS PROMPT_CHAIN_EXECUTIONS (
    execution_id UUID PRIMARY KEY,
    chain_id UUID NOT NULL,
    user_id UUID NOT NULL,
    execution_context TEXT,
    chain_status VARCHAR(50),
    current_step INT,
    total_steps INT,
    step_results TEXT,
    final_output TEXT,
    execution_time_ms INT,
    total_tokens_used INT,
    total_cost DECIMAL(10, 4),
    error_at_step INT,
    error_message TEXT,
    was_approved BOOLEAN,
    was_deployed BOOLEAN,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (chain_id) REFERENCES PROMPT_CHAINS(chain_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES USERS(user_id)
);

CREATE TABLE IF NOT EXISTS PROMPT_FEEDBACK (
    feedback_id UUID PRIMARY KEY,
    execution_id UUID,
    prompt_id UUID,
    user_id UUID NOT NULL,
    rating INT NOT NULL,
    feedback_type VARCHAR(100),
    feedback_text TEXT,
    improvements_suggested TEXT,
    was_helpful BOOLEAN,
    led_to_deployment BOOLEAN DEFAULT FALSE,
    is_public BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES PROMPT_EXECUTION_HISTORY(execution_id),
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id),
    FOREIGN KEY (user_id) REFERENCES USERS(user_id)
);

CREATE TABLE IF NOT EXISTS PROMPT_PERFORMANCE_METRICS (
    metric_id UUID PRIMARY KEY,
    prompt_id UUID NOT NULL,
    date_recorded DATE,
    total_executions INT DEFAULT 0,
    successful_executions INT DEFAULT 0,
    failed_executions INT DEFAULT 0,
    average_quality_score FLOAT,
    average_execution_time_ms INT,
    average_tokens_per_execution INT,
    total_cost DECIMAL(12, 4),
    user_approval_rate FLOAT,
    deployment_rate FLOAT,
    average_rating FLOAT,
    recommendations_text TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES PROMPTS(prompt_id) ON DELETE CASCADE,
    UNIQUE(prompt_id, date_recorded)
);

-- Create Indexes for Performance
CREATE INDEX IF NOT EXISTS idx_prompts_category ON PROMPTS(prompt_category);
CREATE INDEX IF NOT EXISTS idx_prompts_active ON PROMPTS(is_active);
CREATE INDEX IF NOT EXISTS idx_prompts_template ON PROMPTS(is_template);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt ON PROMPT_VERSIONS(prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_execution_prompt ON PROMPT_EXECUTION_HISTORY(prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_execution_user ON PROMPT_EXECUTION_HISTORY(user_id);
CREATE INDEX IF NOT EXISTS idx_prompt_execution_status ON PROMPT_EXECUTION_HISTORY(execution_status);
CREATE INDEX IF NOT EXISTS idx_prompt_feedback_rating ON PROMPT_FEEDBACK(rating);
CREATE INDEX IF NOT EXISTS idx_prompt_metrics_date ON PROMPT_PERFORMANCE_METRICS(date_recorded);
CREATE INDEX IF NOT EXISTS idx_prompt_chain_status ON PROMPT_CHAINS(chain_status);
```

---

## Usage Examples

### Find Most Used Prompts
```sql
SELECT prompt_name, usage_count, average_rating
FROM PROMPTS
LEFT JOIN PROMPT_PERFORMANCE_METRICS ppm 
  ON PROMPTS.prompt_id = ppm.prompt_id AND ppm.date_recorded = CURRENT_DATE
ORDER BY usage_count DESC
LIMIT 10;
```

### Get Prompt Version History
```sql
SELECT pv.version_number, pv.change_reason, pv.created_at, u.username
FROM PROMPT_VERSIONS pv
JOIN USERS u ON pv.changed_by_user_id = u.user_id
WHERE pv.prompt_id = ?
ORDER BY pv.version_number DESC;
```

### Analyze Prompt Performance
```sql
SELECT 
    PROMPTS.prompt_name,
    ppm.total_executions,
    ppm.user_approval_rate,
    ppm.average_quality_score,
    ppm.average_rating
FROM PROMPTS
JOIN PROMPT_PERFORMANCE_METRICS ppm 
  ON PROMPTS.prompt_id = ppm.prompt_id 
  AND ppm.date_recorded >= date('now', '-30 days')
ORDER BY ppm.average_rating DESC;
```

### Track Prompt Chain Execution
```sql
SELECT 
    pc.chain_name,
    pce.chain_status,
    pce.total_steps,
    pce.current_step,
    (pce.execution_time_ms / 1000.0) as execution_time_seconds
FROM PROMPT_CHAINS pc
JOIN PROMPT_CHAIN_EXECUTIONS pce ON pc.chain_id = pce.chain_id
ORDER BY pce.started_at DESC
LIMIT 20;
```

---

## Integration with Main Database

These tables should be integrated into the main NetTopoView database. The relationship flow:

1. **USERS** create and approve **PROMPTS**
2. **PROMPTS** can reference **PROMPT_TEMPLATES**
3. **PROMPTS** are organized into **PROMPT_CHAINS** for complex workflows
4. **PROMPT_EXECUTION_HISTORY** tracks all executions
5. **PROMPT_FEEDBACK** captures user ratings and improvements
6. **PROMPT_PERFORMANCE_METRICS** aggregates metrics for optimization
7. Results from **PROMPT_EXECUTION_HISTORY** may trigger **CONFIG_PROPOSALS**, **INTENTS**, or **AGENT_PLANS**

---

## Next Implementation Steps

1. Add these tables to the main database schema
2. Create ORM models for prompt management
3. Build API endpoints for:
   - Creating and versioning prompts
   - Executing prompts with context
   - Tracking feedback and metrics
   - Managing prompt chains
4. Implement prompt templating engine
5. Build UI for prompt management
6. Integrate with AI model execution pipeline
