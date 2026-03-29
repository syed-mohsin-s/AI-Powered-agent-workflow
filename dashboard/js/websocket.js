/**
 * Sentinel-AI WebSocket Client
 * Manages real-time connection to the backend.
 */

class SentinelWebSocket {
    constructor() {
        this.ws = null;
        this.handlers = {};
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 2000;
        this.connected = false;
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.hostname || 'localhost';
        const port = window.location.port || '8000';
        const url = `${protocol}//${host}:${port}/ws`;

        try {
            this.ws = new WebSocket(url);

            this.ws.onopen = () => {
                this.connected = true;
                this.reconnectAttempts = 0;
                this._updateStatus('Connected', 'healthy');
                this._emit('connected', {});
                console.log('[WS] Connected to Sentinel-AI');
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this._emit(data.event_type, data);
                    this._emit('message', data);
                } catch (e) {
                    console.warn('[WS] Parse error:', e);
                }
            };

            this.ws.onclose = () => {
                this.connected = false;
                this._updateStatus('Disconnected', 'failed');
                this._emit('disconnected', {});
                this._tryReconnect();
            };

            this.ws.onerror = (err) => {
                console.warn('[WS] Error:', err);
                this._updateStatus('Error', 'failed');
            };
        } catch (e) {
            console.warn('[WS] Connection failed:', e);
            this._updateStatus('Offline', 'failed');
        }
    }

    on(eventType, handler) {
        if (!this.handlers[eventType]) this.handlers[eventType] = [];
        this.handlers[eventType].push(handler);
    }

    send(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        }
    }

    _emit(eventType, data) {
        const handlers = this.handlers[eventType] || [];
        handlers.forEach(h => {
            try { h(data); } catch (e) { console.error('[WS] Handler error:', e); }
        });
    }

    _updateStatus(text, state) {
        const el = document.getElementById('ws-status');
        if (el) {
            el.textContent = text;
            el.style.color = state === 'healthy' ? '#10b981' : state === 'failed' ? '#ef4444' : '#94a3b8';
        }
    }

    _tryReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            this._updateStatus(`Reconnecting (${this.reconnectAttempts})...`, 'degraded');
            setTimeout(() => this.connect(), this.reconnectDelay * this.reconnectAttempts);
        }
    }
}

// Global WebSocket instance
const sentinelWS = new SentinelWebSocket();
