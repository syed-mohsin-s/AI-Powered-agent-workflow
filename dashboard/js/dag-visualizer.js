/**
 * Sentinel-AI DAG Visualizer
 * Canvas-based directed acyclic graph rendering with animated task status.
 */

class DAGVisualizer {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.nodes = [];
        this.edges = [];
        this.animFrame = null;
        this._resize();
        window.addEventListener('resize', () => this._resize());
    }

    _resize() {
        const parent = this.canvas.parentElement;
        const dpr = window.devicePixelRatio || 1;
        this.canvas.width = parent.clientWidth * dpr;
        this.canvas.height = 350 * dpr;
        this.canvas.style.width = parent.clientWidth + 'px';
        this.canvas.style.height = '350px';
        this.ctx.scale(dpr, dpr);
        this.width = parent.clientWidth;
        this.height = 350;
        if (this.nodes.length) this.render();
    }

    setData(dagData) {
        this.nodes = dagData.nodes || [];
        this.edges = dagData.edges || [];
        this._layoutNodes();
        this.render();
    }

    _layoutNodes() {
        if (!this.nodes.length) return;
        
        // Group by depth
        const depthGroups = {};
        this.nodes.forEach(n => {
            const d = n.depth || 0;
            if (!depthGroups[d]) depthGroups[d] = [];
            depthGroups[d].push(n);
        });
        
        const depths = Object.keys(depthGroups).map(Number).sort((a, b) => a - b);
        const maxDepth = Math.max(...depths, 1);
        const padding = 80;
        const usableWidth = this.width - padding * 2;
        const usableHeight = this.height - padding * 2;

        depths.forEach(depth => {
            const group = depthGroups[depth];
            const x = padding + (depth / maxDepth) * usableWidth;
            const count = group.length;
            group.forEach((node, i) => {
                node._x = x;
                node._y = padding + ((i + 1) / (count + 1)) * usableHeight;
            });
        });
    }

    render() {
        const ctx = this.ctx;
        ctx.clearRect(0, 0, this.width, this.height);

        // Draw edges
        this.edges.forEach(edge => {
            const from = this.nodes.find(n => n.id === edge.from);
            const to = this.nodes.find(n => n.id === edge.to);
            if (!from || !to) return;
            
            ctx.beginPath();
            ctx.strokeStyle = 'rgba(100, 116, 139, 0.3)';
            ctx.lineWidth = 1.5;
            
            // Bezier curve
            const mx = (from._x + to._x) / 2;
            ctx.moveTo(from._x + 32, from._y);
            ctx.bezierCurveTo(mx, from._y, mx, to._y, to._x - 32, to._y);
            ctx.stroke();

            // Arrow
            const angle = Math.atan2(to._y - from._y, to._x - from._x);
            const ax = to._x - 32;
            const ay = to._y;
            ctx.beginPath();
            ctx.fillStyle = 'rgba(100, 116, 139, 0.4)';
            ctx.moveTo(ax, ay);
            ctx.lineTo(ax - 8 * Math.cos(angle - 0.4), ay - 8 * Math.sin(angle - 0.4));
            ctx.lineTo(ax - 8 * Math.cos(angle + 0.4), ay - 8 * Math.sin(angle + 0.4));
            ctx.closePath();
            ctx.fill();
        });

        // Draw nodes
        this.nodes.forEach(node => {
            this._drawNode(ctx, node);
        });
    }

    _drawNode(ctx, node) {
        const x = node._x;
        const y = node._y;
        const r = 26;

        // Status colors
        const colors = {
            pending: { fill: '#1e293b', stroke: '#475569', text: '#94a3b8' },
            queued: { fill: '#172554', stroke: '#3b82f6', text: '#93c5fd' },
            running: { fill: '#1e3a5f', stroke: '#3b82f6', text: '#60a5fa', glow: 'rgba(59,130,246,0.3)' },
            success: { fill: '#064e3b', stroke: '#10b981', text: '#6ee7b7' },
            failed: { fill: '#450a0a', stroke: '#ef4444', text: '#fca5a5' },
            skipped: { fill: '#1e293b', stroke: '#64748b', text: '#94a3b8' },
            retrying: { fill: '#451a03', stroke: '#f59e0b', text: '#fcd34d', glow: 'rgba(245,158,11,0.3)' },
        };

        const c = colors[node.status] || colors.pending;

        // Glow effect for active states
        if (c.glow) {
            ctx.shadowBlur = 15;
            ctx.shadowColor = c.glow;
        }

        // Node circle
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = c.fill;
        ctx.fill();
        ctx.strokeStyle = c.stroke;
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.shadowBlur = 0;

        // Status icon
        ctx.font = '14px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = c.text;
        
        const icons = { pending: '○', queued: '◎', running: '◉', success: '✓', failed: '✗', skipped: '—', retrying: '↻' };
        ctx.fillText(icons[node.status] || '?', x, y);

        // Label below
        ctx.font = '10px Inter, sans-serif';
        ctx.fillStyle = '#94a3b8';
        const label = node.name.length > 18 ? node.name.slice(0, 16) + '...' : node.name;
        ctx.fillText(label, x, y + r + 14);
    }

    clear() {
        this.nodes = [];
        this.edges = [];
        this.ctx.clearRect(0, 0, this.width, this.height);
    }
}

// Global DAG instance
const dagVis = new DAGVisualizer('dag-canvas');
