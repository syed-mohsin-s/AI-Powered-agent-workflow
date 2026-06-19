/**
 * Sentinel-AI Metrics Module
 * Updates KPI cards and manages metric history.
 */

class MetricsManager {
    constructor() {
        this.history = {};
        this.currentKPIs = {};
    }

    updateKPIs(kpis) {
        this.currentKPIs = kpis;
        
        document.querySelectorAll('[data-metric]').forEach(el => {
            const metric = el.dataset.metric;
            if (kpis[metric] !== undefined) {
                const newVal = kpis[metric];
                const oldVal = parseFloat(el.textContent) || 0;
                this._animateValue(el, oldVal, newVal, 600);
            }
        });
    }

    _animateValue(el, start, end, duration) {
        const startTime = performance.now();
        const diff = end - start;
        
        const step = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            // Ease out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            const current = start + diff * eased;
            el.textContent = current.toFixed(1);
            
            if (progress < 1) {
                requestAnimationFrame(step);
            }
        };
        
        requestAnimationFrame(step);
    }

    updateTrends(kpis) {
        const trendMap = {
            'kpi-tcr': kpis.task_completion_rate >= 90 ? 'up' : kpis.task_completion_rate >= 70 ? 'neutral' : 'down',
            'kpi-autonomy': kpis.autonomy_score >= 90 ? 'up' : kpis.autonomy_score >= 70 ? 'neutral' : 'down',
            'kpi-sla': kpis.sla_compliance_rate >= 95 ? 'up' : kpis.sla_compliance_rate >= 80 ? 'neutral' : 'down',
            'kpi-mttr': kpis.mttr_a_seconds <= 5 ? 'up' : kpis.mttr_a_seconds <= 30 ? 'neutral' : 'down',
            'kpi-audit': kpis.audit_completeness >= 95 ? 'up' : kpis.audit_completeness >= 80 ? 'neutral' : 'down',
        };

        for (const [cardId, trend] of Object.entries(trendMap)) {
            const card = document.getElementById(cardId);
            if (!card) continue;
            const trendEl = card.querySelector('.kpi-trend');
            if (!trendEl) continue;
            trendEl.className = `kpi-trend ${trend}`;
            trendEl.textContent = trend === 'up' ? '↑' : trend === 'down' ? '↓' : '—';
        }
    }
}

const metricsManager = new MetricsManager();
