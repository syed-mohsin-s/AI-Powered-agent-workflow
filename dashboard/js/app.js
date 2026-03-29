/**
 * Sentinel-AI Dashboard Application
 * Main controller for the monitoring dashboard.
 */

const API_BASE = '';
let selectedWorkflowId = null;
let startTime = Date.now();

// ============================================================
// Initialization
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    // Connect WebSocket
    sentinelWS.connect();

    // Register WS handlers
    sentinelWS.on('initial_state', handleInitialState);
    sentinelWS.on('metrics_update', handleMetricsUpdate);
    sentinelWS.on('workflow.created', handleWorkflowEvent);
    sentinelWS.on('workflow.started', handleWorkflowEvent);
    sentinelWS.on('workflow.completed', handleWorkflowEvent);
    sentinelWS.on('workflow.failed', handleWorkflowEvent);
    sentinelWS.on('task.started', handleTaskEvent);
    sentinelWS.on('task.completed', handleTaskEvent);
    sentinelWS.on('task.failed', handleTaskEvent);
    sentinelWS.on('task.retrying', handleTaskEvent);
    sentinelWS.on('sla.warning', handleSLAWarning);
    sentinelWS.on('audit.record_created', handleAuditEvent);
    sentinelWS.on('message', handleAnyMessage);

    // Priority slider
    const slider = document.getElementById('wf-priority');
    if (slider) {
        slider.addEventListener('input', (e) => {
            document.getElementById('priority-display').textContent = e.target.value;
        });
    }

    // Prefill sample data based on workflow type
    const wfType = document.getElementById('wf-type');
    if (wfType) {
        wfType.addEventListener('change', prefillSampleData);
        prefillSampleData();
    }

    // Start uptime timer
    setInterval(updateUptime, 1000);

    // Initial data load
    loadWorkflows();
    loadAgents();
});

// ============================================================
// API Calls
// ============================================================

async function apiCall(endpoint, options = {}) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            headers: { 'Content-Type': 'application/json', ...options.headers },
            ...options,
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(err.detail || 'API Error');
        }
        return await response.json();
    } catch (e) {
        console.error(`API Error (${endpoint}):`, e);
        throw e;
    }
}

async function loadWorkflows() {
    try {
        const data = await apiCall('/api/workflows/');
        renderWorkflows(data.workflows || []);
    } catch (e) { /* API might not be ready yet */ }
}

async function loadAgents() {
    try {
        const data = await apiCall('/api/agents/');
        renderAgents(data.agents || []);
    } catch (e) {
        renderDefaultAgents();
    }
}

async function loadWorkflowDetail(workflowId) {
    try {
        const data = await apiCall(`/api/workflows/${workflowId}`);
        renderDAG(data);
    } catch (e) {
        showToast('Failed to load workflow details', 'error');
    }
}

// ============================================================
// Workflow Submission
// ============================================================

function showSubmitModal() {
    document.getElementById('submit-modal').style.display = 'flex';
}

function hideSubmitModal() {
    document.getElementById('submit-modal').style.display = 'none';
}

async function submitWorkflow() {
    const type = document.getElementById('wf-type').value;
    const priority = parseInt(document.getElementById('wf-priority').value);
    let inputData;

    try {
        const raw = document.getElementById('wf-data').value.trim();
        inputData = raw ? JSON.parse(raw) : {};
    } catch (e) {
        showToast('Invalid JSON in input data', 'error');
        return;
    }

    try {
        const result = await apiCall('/api/workflows/', {
            method: 'POST',
            body: JSON.stringify({
                workflow_type: type,
                priority: priority,
                input_data: inputData,
            }),
        });

        hideSubmitModal();
        showToast(`Workflow submitted: ${result.workflow_id.slice(0, 8)}...`, 'success');
        
        // Refresh workflow list
        setTimeout(loadWorkflows, 500);
        setTimeout(loadWorkflows, 2000);
        setTimeout(loadWorkflows, 5000);
    } catch (e) {
        showToast(`Submission failed: ${e.message}`, 'error');
    }
}

function prefillSampleData() {
    const type = document.getElementById('wf-type').value;
    const textarea = document.getElementById('wf-data');
    
    const samples = {
        p2p: JSON.stringify({
            vendor_name: "Acme Corporation",
            invoice_number: "INV-2026-1234",
            total_amount: 15000,
            currency: "USD",
            po_number: "PO-9876",
            date: "2026-03-29",
            content: "Invoice #INV-2026-1234\nVendor: Acme Corporation\nDate: 2026-03-29\nPO#: PO-9876\nTotal: $15,000.00"
        }, null, 2),
        meeting_intelligence: JSON.stringify({
            content: "Meeting: Q1 Planning\nAttendees: Alice, Bob, Carol\n\nAlice: We decided to launch the new feature by April 15.\nBob: Agreed. I will do the backend implementation by April 1.\nCarol: Action: Design review needed. Assigned to Dave, deadline March 30.\nAlice: We will proceed with the phased rollout approach.",
            attendees: ["Alice", "Bob", "Carol"],
            topics: ["Q1 Planning", "Feature Launch"]
        }, null, 2),
        onboarding: JSON.stringify({
            employee_name: "Jane Smith",
            email: "jane.smith@company.com",
            position: "Senior Engineer",
            department: "engineering",
            start_date: "2026-04-15",
            manager: "John Doe",
            equipment_needed: ["laptop", "monitor", "keyboard"],
            access_required: ["email", "slack", "github", "jira"]
        }, null, 2),
        contract_clm: JSON.stringify({
            parties: ["TechCorp Inc.", "CloudServices Ltd."],
            contract_type: "service_agreement",
            effective_date: "2026-04-01",
            expiration_date: "2027-03-31",
            value: 250000,
            key_terms: ["SLA 99.9%", "Monthly billing", "30-day termination"],
            content: "Service Agreement between TechCorp Inc. and CloudServices Ltd."
        }, null, 2),
    };

    textarea.value = samples[type] || '{}';
}

// ============================================================
// Rendering
// ============================================================

function renderWorkflows(workflows) {
    const container = document.getElementById('workflows-list');
    
    if (!workflows.length) {
        container.innerHTML = `
            <div class="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none"><path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" stroke="currentColor" stroke-width="1.5"/></svg>
                <p>No active workflows</p>
                <span>Submit a workflow to get started</span>
            </div>`;
        return;
    }

    container.innerHTML = workflows.map(wf => {
        const completedTasks = wf.tasks.filter(t => t.status === 'success').length;
        const totalTasks = wf.tasks.length;
        const progress = totalTasks > 0 ? (completedTasks / totalTasks * 100) : 0;
        const isActive = wf.id === selectedWorkflowId;
        
        return `
            <div class="workflow-card ${isActive ? 'active' : ''}" onclick="selectWorkflow('${wf.id}')">
                <div class="wf-header">
                    <span class="wf-type">${wf.workflow_type.replace('_', ' ')}</span>
                    <span class="wf-status ${wf.status}">${wf.status}</span>
                </div>
                <div class="wf-id">${wf.id.slice(0, 12)}...</div>
                <div class="wf-progress">
                    <div class="wf-progress-bar" style="width: ${progress}%"></div>
                </div>
                <div style="display:flex;justify-content:space-between;margin-top:6px;font-size:0.7rem;color:#64748b;">
                    <span>${completedTasks}/${totalTasks} tasks</span>
                    <span>Priority: ${wf.priority}</span>
                </div>
            </div>`;
    }).join('');
}

function renderAgents(agents) {
    const grid = document.getElementById('agent-grid');
    
    if (!agents.length) {
        renderDefaultAgents();
        return;
    }

    grid.innerHTML = agents.map(a => `
        <div class="agent-card">
            <div class="agent-name">
                <span class="agent-dot ${a.status}"></span>
                ${a.name}
            </div>
            <div class="agent-stats">
                ✓ <span>${a.tasks_completed}</span>
                ✗ <span>${a.tasks_failed}</span>
                ⏱ <span>${a.avg_response_time_ms.toFixed(0)}ms</span>
            </div>
        </div>
    `).join('');
}

function renderDefaultAgents() {
    const names = ['Orchestrator', 'Supervisor', 'Intake', 'Policy', 'Decision', 'Execution', 'Verification', 'Monitoring', 'Recovery'];
    const grid = document.getElementById('agent-grid');
    grid.innerHTML = names.map(name => `
        <div class="agent-card">
            <div class="agent-name">
                <span class="agent-dot healthy"></span>
                ${name}
            </div>
            <div class="agent-stats">
                ✓ <span>0</span> ✗ <span>0</span> ⏱ <span>0ms</span>
            </div>
        </div>
    `).join('');
}

function renderDAG(workflow) {
    if (!workflow || !workflow.tasks) {
        dagVis.clear();
        return;
    }

    const nodes = workflow.tasks.map(t => ({
        id: t.id,
        name: t.task_name,
        agent: t.agent_type,
        status: t.status,
        depth: t.dag_depth,
    }));

    // Reconstruct edges from task naming convention
    const edges = [];
    // We can infer edges from depth ordering within the same workflow
    const byDepth = {};
    nodes.forEach(n => {
        if (!byDepth[n.depth]) byDepth[n.depth] = [];
        byDepth[n.depth].push(n);
    });

    const depths = Object.keys(byDepth).map(Number).sort((a, b) => a - b);
    for (let i = 1; i < depths.length; i++) {
        const prevNodes = byDepth[depths[i - 1]];
        const currNodes = byDepth[depths[i]];
        prevNodes.forEach(pn => {
            currNodes.forEach(cn => {
                edges.push({ from: pn.id, to: cn.id });
            });
        });
    }

    dagVis.setData({ nodes, edges });
    document.getElementById('dag-hint').textContent = `${workflow.workflow_type} — ${nodes.length} tasks`;
}

function selectWorkflow(workflowId) {
    selectedWorkflowId = workflowId;
    loadWorkflowDetail(workflowId);
    loadWorkflows();
}

// ============================================================
// Event Handlers
// ============================================================

function handleInitialState(data) {
    if (data.data && data.data.kpis) {
        metricsManager.updateKPIs(data.data.kpis);
        metricsManager.updateTrends(data.data.kpis);
    }
}

function handleMetricsUpdate(data) {
    if (data.data && data.data.kpis) {
        metricsManager.updateKPIs(data.data.kpis);
        metricsManager.updateTrends(data.data.kpis);
    }
    if (data.data && data.data.agent_performance) {
        const agents = Object.entries(data.data.agent_performance).map(([name, perf]) => ({
            name: name.charAt(0).toUpperCase() + name.slice(1),
            status: perf.success_rate >= 90 ? 'healthy' : perf.success_rate >= 50 ? 'degraded' : 'failed',
            tasks_completed: perf.total_executions || 0,
            tasks_failed: 0,
            avg_response_time_ms: (perf.avg_execution_time || 0) * 1000,
        }));
        if (agents.length) renderAgents(agents);
    }
}

function handleWorkflowEvent(data) {
    loadWorkflows();
    if (selectedWorkflowId && data.data && data.data.id === selectedWorkflowId) {
        loadWorkflowDetail(selectedWorkflowId);
    }
    
    const evtType = data.event_type || '';
    if (evtType.includes('completed')) showToast('Workflow completed ✓', 'success');
    if (evtType.includes('failed')) showToast('Workflow failed ✗', 'error');
}

function handleTaskEvent(data) {
    if (selectedWorkflowId) {
        setTimeout(() => loadWorkflowDetail(selectedWorkflowId), 300);
    }
    setTimeout(loadWorkflows, 300);
}

function handleSLAWarning(data) {
    const remaining = data.data ? Math.round(data.data.time_remaining_seconds) : '?';
    showToast(`⚠️ SLA Warning: ${remaining}s remaining`, 'warning');
}

function handleAuditEvent(data) {
    addAuditEntry(data.data || data);
}

function handleAnyMessage(data) {
    // Add any event with agent/decision info to audit log
    if (data.event_type && data.data) {
        const d = data.data;
        if (d.agent || d.task_name) {
            addAuditEntry({
                agent: d.agent || 'system',
                timestamp: data.timestamp || new Date().toISOString(),
                confidence: d.confidence || 1.0,
                decision: d.task_name ? `${data.event_type}: ${d.task_name}` : data.event_type,
            });
        }
    }
}

// ============================================================
// Audit Log
// ============================================================

function addAuditEntry(record) {
    const log = document.getElementById('audit-log');
    const emptyState = log.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    const entry = document.createElement('div');
    entry.className = 'audit-entry';
    
    const time = record.timestamp ? new Date(record.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
    const confidence = (record.confidence || 0).toFixed(2);
    const agent = record.agent || 'system';
    const decision = record.decision || record.action_taken || 'action';

    entry.innerHTML = `
        <div class="audit-header">
            <span class="audit-agent">${agent}</span>
            <span class="audit-time">${time}</span>
            <span class="audit-confidence">${confidence}</span>
        </div>
        <div class="audit-decision">${decision}</div>
    `;

    log.insertBefore(entry, log.firstChild);

    // Keep max 50 entries
    while (log.children.length > 50) {
        log.removeChild(log.lastChild);
    }
}

async function verifyChain() {
    try {
        const result = await apiCall('/api/audit/verify');
        if (result.valid) {
            showToast(`Audit chain verified ✓ (${result.verified_records} records)`, 'success');
        } else {
            showToast(`Audit chain INVALID at record ${result.first_invalid_index}`, 'error');
        }
    } catch (e) {
        showToast('Chain verification: No records yet', 'info');
    }
}

// ============================================================
// Utilities
// ============================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function updateUptime() {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const h = String(Math.floor(elapsed / 3600)).padStart(2, '0');
    const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    const el = document.getElementById('uptime-value');
    if (el) el.textContent = `${h}:${m}:${s}`;
}
