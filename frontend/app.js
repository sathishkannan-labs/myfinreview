// --- FINREVIEW CORE DASHBOARD LOGIC ---
let GLOBAL_SUMMARY_DATA = null;

async function refreshAllIntelligence() {
    const userIdStr = localStorage.getItem('finreview_user_id');
    if (!userIdStr || userIdStr === 'null') {
        showToast("User session expired. Please login again.", "danger");
        logout();
        return;
    }
    const userId = parseInt(userIdStr);
    const btn = document.getElementById('refresh-intel-btn');
    
    // --- COOLDOWN LOGIC (3 Hours) ---
    const lastRefresh = localStorage.getItem(`finreview_last_refresh_${userId}`);
    const now = Date.now();
    const COOLDOWN_MS = 3 * 60 * 60 * 1000; // 3 Hours

    if (lastRefresh && (now - parseInt(lastRefresh)) < COOLDOWN_MS) {
        const remainingMinutes = Math.ceil((COOLDOWN_MS - (now - parseInt(lastRefresh))) / (60 * 1000));
        showToast(`Wait ${remainingMinutes}m before next refresh.`, "warning");
        updateRefreshUI(userId);
        return;
    }

    const setStatus = (msg) => {
        const msgEl = document.getElementById('loading-msg');
        if (msgEl) msgEl.innerText = msg;
    };

    toggleLoading(true, "Initiating Intelligence Refresh...");
    if (btn) btn.disabled = true;

    try {
        // Phase 1: Heavy Data Collection (Prices)
        // Note: News ingestion removed here; now handled by background scheduler
        setStatus("Updating portfolio valuations & LTP...");
        await fetch(`${CONFIG.API_URL}/analytics/${userId}/calculate`, { 
            method: 'POST', 
            headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } 
        });
        setStatus("Market data synchronized.");

        // Phase 2: Diagnostic Magic (AI & Alerts)
        setStatus("Doing the AI magic now...");
        await Promise.all([
            fetch(`${CONFIG.API_URL}/insights/${userId}/generate`, { method: 'POST', headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } })
                .then(() => setStatus("AI Diagnostic briefing complete."))
                .catch(e => { console.error('Insights generation failed', e); }),
            
            fetch(`${CONFIG.API_URL}/alerts/${userId}/evaluate`, { method: 'POST', headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } })
                .then(() => setStatus("Checking for risk signals & drift..."))
                .catch(e => { console.error('Alert evaluation failed', e); })
        ]);

        setStatus("Finalizing your diagnostic report...");
        
    } catch (e) {
        console.error('Parallel refresh failed', e);
        showToast("Intelligence refresh encountered errors.", "danger");
    }

    // always refresh UI after attempting all steps - Rely on backend timestamp via summary fetch
    showToast("Intelligence Refresh Complete!", "success");
    fetchPortfolioSummary(); 

    toggleLoading(false);
}

function updateRefreshUI(userId) {
    const btn = document.getElementById('refresh-intel-btn');
    const timeEl = document.getElementById('last-refresh-time');
    const lastRefresh = localStorage.getItem(`finreview_last_refresh_${userId}`);
    
    if (!lastRefresh) {
        if (timeEl) timeEl.innerText = "Never Refreshed";
        return;
    }

    const date = new Date(parseInt(lastRefresh));
    if (timeEl) {
        const dateStr = date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
        const timeStr = date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
        timeEl.innerText = `Updated: ${dateStr}, ${timeStr}`;
    }

    const diff = Date.now() - parseInt(lastRefresh);
    if (diff < (3 * 60 * 60 * 1000) && btn) {
        btn.disabled = true;
        btn.classList.add('opacity-50');
    } else if (btn) {
        btn.disabled = false;
        btn.classList.remove('opacity-50');
    }
}

function toggleAlertListLoading(show) {
    const list = document.getElementById('active-rules-list');
    if (!list) return;
    if (show) {
        list.classList.add('opacity-50');
        list.style.pointerEvents = 'none';
    } else {
        list.classList.remove('opacity-50');
        list.style.pointerEvents = 'auto';
    }
}

async function fetchAlertRules(userId) {
    const list = document.getElementById('active-rules-list');
    if (!list) return;
    
    if (!userId || userId === 'null' || userId === 'undefined') {
        list.innerHTML = '<div class="col-12 text-center py-4 text-muted small">User context missing.</div>';
        return;
    }

    toggleAlertListLoading(true);
    try {
        const res = await fetch(`${CONFIG.API_URL}/alerts/${userId}/rules`, { headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } });
        if (res.ok) {
            const rules = await res.json();
            if (rules.length === 0) {
                list.innerHTML = '<div class="col-12 text-center py-4 text-muted small">No active signals set.</div>';
                return;
            }

            // Create list with checkboxes
            let html = rules.map(r => `
                <div class="col-md-6 mb-2">
                    <div class="d-flex justify-content-between align-items-center p-2 border rounded shadow-sm bg-white h-100 rule-card-item">
                        <div class="d-flex align-items-center flex-grow-1 pe-2" style="min-width: 0;">
                            <div class="form-check mb-0 me-2 p-0 d-flex align-items-center justify-content-center" style="width: 24px; height: 24px; background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0; box-shadow: inset 0 1px 2px rgba(0,0,0,0.05);">
                                <input type="checkbox" class="form-check-input rule-checkbox m-0" value="${r.id}" style="width: 16px; height: 16px; cursor: pointer;">
                            </div>
                            <div class="text-truncate">
                                <div class="fw-bold text-dark" style="font-size: 11px;">${resolveDisplayName(r.symbol)}</div>
                                <div class="text-muted" style="font-size: 9px;">${r.type.replace('_',' ')}: ₹${r.threshold}</div>
                            </div>
                        </div>
                        <button class="btn btn-outline-danger btn-xs border-0 p-1 flex-shrink-0" onclick="deleteAlertRule(${r.id})">
                            <i class="bi bi-trash" style="font-size: 12px;"></i>
                        </button>
                    </div>
                </div>
            `).join('');

            // Add Bulk Delete button at the bottom
            html += `
                <div id="bulk-delete-container" class="col-12 mt-3 pt-2 border-top hidden">
                    <button class="btn btn-danger btn-sm w-100" onclick="deleteSelectedRules()">
                        <i class="bi bi-trash3 me-1"></i> Delete Selected (<span id="selected-count">0</span>)
                    </button>
                </div>
            `;
            list.innerHTML = html;

            // Listen for checkbox changes
            document.querySelectorAll('.rule-checkbox').forEach(cb => {
                cb.addEventListener('change', () => {
                    const checked = document.querySelectorAll('.rule-checkbox:checked');
                    const container = document.getElementById('bulk-delete-container');
                    const countEl = document.getElementById('selected-count');
                    if (checked.length > 0) {
                        container.classList.remove('hidden');
                        countEl.innerText = checked.length;
                    } else {
                        container.classList.add('hidden');
                    }
                });
            });

        } else {
            list.innerHTML = '<div class="col-12 text-center py-4 text-danger small">Failed to load signals.</div>';
        }
    } catch (e) { 
        console.error("Rules fetch error", e);
        list.innerHTML = '<div class="col-12 text-center py-4 text-danger small">Network error: Could not load signals.</div>';
    } finally {
        toggleAlertListLoading(false);
    }
}

async function deleteSelectedRules() {
    const checked = document.querySelectorAll('.rule-checkbox:checked');
    const ids = Array.from(checked).map(cb => parseInt(cb.value));
    
    if (ids.length === 0) return;
    if (!confirm(`Are you sure you want to delete ${ids.length} signals?`)) return;

    toggleAlertListLoading(true);
    try {
        const res = await fetch(`${CONFIG.API_URL}/alerts/rules/bulk-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${AUTH_TOKEN}` },
            body: JSON.stringify({ rule_ids: ids })
        });
        if (res.ok) {
            showToast(`Deleted ${ids.length} signals!`, "success");
            fetchAlertRules(localStorage.getItem('finreview_user_id'));
        }
    } catch (e) { 
        showToast("Bulk delete failed.", "danger"); 
    } finally {
        toggleAlertListLoading(false);
    }
}

async function deleteAlertRule(ruleId) {
    if (!confirm("Are you sure you want to delete this signal?")) return;
    const userId = localStorage.getItem('finreview_user_id');
    toggleAlertListLoading(true);
    try {
        const res = await fetch(`${CONFIG.API_URL}/alerts/rule/${ruleId}`, { method: 'DELETE', headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } });
        if (res.ok) {
            showToast("Signal deleted!", "success");
            fetchAlertRules(userId);
        }
    } catch (e) { 
        showToast("Delete failed.", "danger"); 
    } finally {
        toggleAlertListLoading(false);
    }
}

async function fetchMarketContext() {
    try {
        const res = await fetch(`${CONFIG.API_URL}/market/context`);
        if (res.ok) {
            const data = await res.json();
            data.indices.forEach(idx => {
                const elId = idx.name === 'Nifty 50' ? 'nifty-50-val' : 'nifty-next-50-val';
                const changeId = idx.name === 'Nifty 50' ? 'nifty-50-change' : 'nifty-next-50-change';
                const el = document.getElementById(elId);
                const changeEl = document.getElementById(changeId);
                if (el) {
                    if (idx.current_price || idx.current_price === 0) {
                        el.innerText = formatINR(idx.current_price);
                    } else {
                        el.innerText = 'N/A';
                    }
                }
                if (changeEl && (idx.change_pct || idx.change_pct === 0)) {
                    const color = idx.change_pct >= 0 ? 'text-success' : 'text-danger';
                    const sign = idx.change_pct >= 0 ? '+' : '';
                    changeEl.innerText = `${sign}${idx.change_pct.toFixed(2)}%`;
                    changeEl.className = `small fw-bold ${color}`;
                }
            });
        }
    } catch (e) { console.warn("Market context fetch failed."); }
}

async function loadSamplePortfolio() {
    const userId = localStorage.getItem('finreview_user_id');
    if (!userId) {
        showToast("Please sign in before loading sample data.", "warning");
        return;
    }
    toggleLoading(true, "Loading sample portfolio...");
    try {
        const response = await fetch(`${CONFIG.API_URL}/sample-portfolio/${userId}`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` }
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || "Could not load the sample portfolio.");
        }
        showToast("Sample portfolio loaded.", "success");
        fetchPortfolioSummary(true);
    } catch (error) {
        showToast(error.message || "Sample portfolio could not be loaded.", "danger");
    } finally {
        toggleLoading(false);
    }
}

function updateEmptyPortfolioState(holdings) {
    const emptyState = document.getElementById('empty-portfolio-state');
    if (!emptyState) return;
    if (!holdings || holdings.length === 0) {
        emptyState.classList.remove('hidden');
    } else {
        emptyState.classList.add('hidden');
    }
}
async function fetchPortfolioSummary(silent = false) {
    const userIdStr = localStorage.getItem('finreview_user_id');
    if (!userIdStr || userIdStr === 'null') return;
    const userId = parseInt(userIdStr);
    
    if (!silent) updateRefreshUI(userId);
    try {
        // 1. Kick off non-blocking secondary fetches
        fetchMarketContext();
        fetchAlertRules(userId);

        // 2. CORE FETCH
        const [sumRes, aiRes, anaRes, newsRes, eventsRes] = await Promise.all([
            fetch(`${CONFIG.API_URL}/portfolio/${userId}/summary`, { headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } }),
            fetch(`${CONFIG.API_URL}/insights/${userId}`, { headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } }),
            fetch(`${CONFIG.API_URL}/analytics/${userId}`, { headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } }),
            fetch(`${CONFIG.API_URL}/news/${userId}`, { headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } }),
            fetch(`${CONFIG.API_URL}/alerts/${userId}/events`, { headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` } })
        ]);

        if (sumRes.status === 401) {
            console.warn("Unauthorized session.");
            return;
        }

        if (!sumRes.ok) throw new Error('API not available');
        const data = await sumRes.json();
        GLOBAL_SUMMARY_DATA = data;
        
        // --- SYNC GLOBAL PROFILE SETTINGS ---
        if (data.target_allocation) {
            localStorage.setItem('finreview_target_alloc', 
                typeof data.target_allocation === 'string' ? data.target_allocation : JSON.stringify(data.target_allocation)
            );
            if (Object.keys(CURRENT_TARGET_ALLOC || {}).length === 0) {
                try {
                    CURRENT_TARGET_ALLOC = typeof data.target_allocation === 'string' ? 
                                           JSON.parse(data.target_allocation) : data.target_allocation;
                } catch(e) {}
            }
        }
        if (data.drift_sensitivity !== undefined) {
            localStorage.setItem('finreview_drift_sensitivity', data.drift_sensitivity.toString());
        }

        if (data.last_intelligence_refresh) {
            localStorage.setItem(`finreview_last_refresh_${userId}`, new Date(data.last_intelligence_refresh).getTime());
            if (!silent) updateRefreshUI(userId);
        }

        // Process secondary dashboard data
        if (aiRes.ok) {
            const aiData = await aiRes.json();
            data.ai_insights = {};
            aiData.forEach(insight => {
                data.ai_insights[insight.type] = { content: insight.content };
            });
        }
        if (anaRes.ok) data.analytics = await anaRes.json();
        if (newsRes.ok) data.latest_news_list = await newsRes.json();
        if (eventsRes.ok) {
            const events = await eventsRes.json();
            NOTIFICATIONS = []; 
            events.forEach(e => addNotification(e.type, e.message));
        }

        // --- RENDER ---
        updateUI(data);
    } catch (error) {
        console.error("Fetch error", error);
    }
}

let _loadingInterval = null;
const _loadingMessages = [
    "Fetching fresh data...",
    "Crunching numbers...",
    "Gathering market news...",
    "Almost there..."
];
let _loadingIndex = 0;

function _startLoadingMessages() {
    const msgEl = document.getElementById('loading-msg');
    if (!msgEl) return;
    msgEl.innerText = _loadingMessages[_loadingIndex];
    _loadingInterval = setInterval(() => {
        _loadingIndex = (_loadingIndex + 1) % _loadingMessages.length;
        msgEl.innerText = _loadingMessages[_loadingIndex];
    }, 1800);
}

function _stopLoadingMessages() {
    if (_loadingInterval) {
        clearInterval(_loadingInterval);
        _loadingInterval = null;
    }
    _loadingIndex = 0;
}

function toggleLoading(show, message = null) {
    const overlay = document.getElementById('loading-overlay');
    const toastCont = document.querySelector('.toast-container');
    const msgEl = document.getElementById('loading-msg');

    if (overlay) {
        if (show) {
            overlay.classList.remove('hidden');
            if (message && msgEl) {
                msgEl.innerText = message;
            } else {
                _startLoadingMessages();
            }
        } else {
            overlay.classList.add('hidden');
            _stopLoadingMessages();
        }
    }
    if (toastCont) {
        toastCont.style.display = ''; // Always ensure visible
        toastCont.style.zIndex = "100001"; // Above overlay (100000)
    }
}

function processAndRender(data) {
    let totalVal = 0;
    let totalCost = 0;
    data.holdings.forEach(h => {
        totalVal += h.current_valuation;
        totalCost += (h.total_quantity * h.avg_price);
        h.gain_loss_pct = ((h.current_valuation - (h.total_quantity * h.avg_price)) / (h.total_quantity * h.avg_price)) * 100;
    });
    data.total_valuation = totalVal;
    data.total_cost = totalCost;
    data.total_gain_loss = totalVal - totalCost;
    data.total_gain_loss_pct = totalCost > 0 ? (data.total_gain_loss / totalCost) * 100 : 0;
    updateUI(data);
}

async function recordBulkTransactions(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }

    
    const rows = document.querySelectorAll('#tx-rows tr');
    const userId = parseInt(localStorage.getItem('finreview_user_id') || '1');
    const transactions = [];

    rows.forEach(row => {
        const symbolInput = row.querySelector('.tx-symbol-input');
        const symbol = symbolInput ? symbolInput.value.trim().toUpperCase() : '';
        const name = FUND_MAP[symbol] || symbol;
        const type = row.querySelector('.tx-type-input').value;
        const quantity = parseFloat(row.querySelector('.tx-quantity-input').value);
        const price = parseFloat(row.querySelector('.tx-price-input').value);

        if (symbol && !isNaN(quantity) && !isNaN(price)) {
            transactions.push({
                user_id: userId,
                symbol: symbol,
                name: name,
                type: type,
                quantity: quantity,
                price: price,
                date: new Date().toISOString()
            });
        }
    });

    if (transactions.length === 0) {
        showToast("Enter valid data.", "warning");
        return;
    }

    toggleLoading(true);
    try {
        const response = await fetch(`${CONFIG.API_URL}/portfolio/bulk-transaction`, {
            method: 'POST',
            body: JSON.stringify(transactions),
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${AUTH_TOKEN}` }
        });

        if (response.ok) {
            showToast(`Saved ${transactions.length} transactions!`, 'success');
            document.getElementById('tx-rows').innerHTML = '';
            addTxRow();
            
            // Just fetch summary silently to update background state
            fetchPortfolioSummary(true); 
        }
 else {
            const err = await response.json();
            showToast(err.detail || "Failed to save.", "danger");
        }
    } catch (error) {
        showToast("Network error.", "danger");
    } finally {
        toggleLoading(false);
    }
}

async function uploadCSV(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const fileInput = document.getElementById('csv-file');
    const file = fileInput.files[0];
    if (!file) {
        showToast("Select a CSV file.", "warning");
        return;
    }

    const reader = new FileReader();
    reader.onload = async (e) => {
        const lines = e.target.result.split('\n');
        const transactions = [];
        const userId = parseInt(localStorage.getItem('finreview_user_id') || '1');

        for (let i = 1; i < lines.length; i++) {
            const cols = lines[i].split(',');
            if (cols.length >= 4) {
                const symbol = cols[0].trim().toUpperCase();
                const name = FUND_MAP[symbol] || symbol;
                transactions.push({
                    user_id: userId,
                    symbol: symbol,
                    name: name,
                    type: cols[1].trim().toUpperCase(),
                    quantity: parseFloat(cols[2]),
                    price: parseFloat(cols[3]),
                    date: new Date().toISOString()
                });
            }
        }

        if (transactions.length > 0) {
            toggleLoading(true);
            try {
                const response = await fetch(`${CONFIG.API_URL}/portfolio/bulk-transaction`, {
                    method: 'POST',
                    body: JSON.stringify(transactions),
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${AUTH_TOKEN}` }
                });
                if (response.ok) {
                    showToast(`Imported ${transactions.length} transactions!`, 'success');
                    fetchPortfolioSummary(true);
                }
            } catch (err) {
                showToast("CSV Upload failed.", "danger");
            } finally {
                toggleLoading(false);
            }
        }
    };
    reader.readAsText(file);
}

async function uploadCAS(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    showToast("CAS PDF parsing is not enabled in v1.0.0. Please use CSV import or manual entry.", "warning");
}

function toggleAlertFields() {
    const type = document.getElementById('alert-type').value;
    const label = document.getElementById('alert-threshold-label');
    const input = document.getElementById('alert-threshold');
    const symbolSearch = document.getElementById('alert-symbol-search');
    
    if (type === 'VALUATION_BELOW') {
        label.innerText = 'Valuation (₹)';
        input.placeholder = 'e.g., 500000';
        symbolSearch.value = 'PORTFOLIO_WIDE';
        symbolSearch.disabled = true;
    } else {
        label.innerText = 'Price Target (₹)';
        input.placeholder = 'e.g., 2500';
        if (symbolSearch.value === 'PORTFOLIO_WIDE') symbolSearch.value = '';
        symbolSearch.disabled = false;
    }
}

async function handleAlertSubmit(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const userId = parseInt(localStorage.getItem('finreview_user_id') || '1');
    const type = document.getElementById('alert-type').value;
    const symbol = document.getElementById('alert-symbol').value || document.getElementById('alert-symbol-search').value;
    const threshold = parseFloat(document.getElementById('alert-threshold').value);

    if (!symbol || isNaN(threshold)) {
        showToast("Please enter valid symbol and threshold.", "warning");
        return;
    }

    toggleLoading(true);
    try {
        const response = await fetch(`${CONFIG.API_URL}/alerts/rule`, {
            method: 'POST',
            body: JSON.stringify({ user_id: userId, type, symbol, threshold }),
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${AUTH_TOKEN}` }
        });

        if (response.ok) {
            showToast("Signal created!", "success");
            document.getElementById('alert-symbol-search').value = '';
            document.getElementById('alert-symbol').value = '';
            document.getElementById('alert-threshold').value = '';
            fetchAlertRules(userId);
            fetchPortfolioSummary(true); 
            // DO NOT close modal
        }
 else {
            const err = await response.json();
            showToast(err.detail || "Failed to create signal.", "danger");
        }
    } catch (error) {
        showToast("Error creating alert.", "danger");
    } finally {
        toggleLoading(false);
    }
}

let GLOBAL_NEWS_LIST = [];
let GLOBAL_HOLDINGS = [];

function filterNews(category, btn) {
    // 1. Update UI active state
    document.querySelectorAll('.news-filter-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    // 2. Filter data
    const filtered = category === 'ALL' ? GLOBAL_NEWS_LIST : GLOBAL_NEWS_LIST.filter(n => n.category === category);
    renderNewsFeed(filtered);
}

function renderNewsFeed(newsItems) {
    const newsGrid = document.getElementById('full-news-grid');
    if (!newsGrid) return;

    if (!newsItems || newsItems.length === 0) {
        newsGrid.innerHTML = '<div class="col-12 text-center py-5 text-muted"><i class="bi bi-mailbox h1 d-block mb-3 opacity-25"></i>No items found in this category.</div>';
        return;
    }

    // Helper to clean up "NULL" text from feeds
    const sanitizeText = (txt) => {
        if (!txt || txt.toUpperCase() === 'NULL' || txt === 'None') return '';
        return txt.trim();
    };

    newsGrid.innerHTML = newsItems.map(a => {
        const pubDate = a.published_at ? new Date(a.published_at).toLocaleDateString('en-IN', {month: 'short', day: 'numeric'}) : 'Recently';
        const disp = resolveDisplayName(a.symbol);
        const cat = a.category || 'NEWS';
        const title = sanitizeText(a.title);
        const cleanSummary = sanitizeText(a.summary);
        
        if (!title) return ''; // Skip empty records

        // Category Visuals
        let catClass = 'bg-primary';
        if (cat === 'RESULT') catClass = 'bg-success';
        else if (cat === 'ANNOUNCEMENT') catClass = 'bg-warning text-dark';
        else if (cat === 'ACTION') catClass = 'bg-info text-white';

        const sentimentHtml = (a.sentiment > 0) ? `<i class="bi bi-graph-up-arrow text-success ms-2" title="Positive Sentiment"></i>` : 
                             (a.sentiment < 0) ? `<i class="bi bi-graph-down-arrow text-danger ms-2" title="Negative Sentiment"></i>` : 
                             `<i class="bi bi-dash-lg text-secondary ms-2" title="Neutral Sentiment"></i>`;

        return `
        <div class="col-md-6">
            <div class="card h-100 news-card border-0 shadow-sm" style="border-radius: 16px;">
                <div class="card-body p-4 d-flex flex-column">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <span class="badge ${catClass} px-2 py-1" style="font-size: 9px; font-weight: 700;">${cat}</span>
                        <small class="text-muted small fw-medium"><i class="bi bi-calendar3 me-1"></i>${pubDate}</small>
                    </div>
                    <div class="mb-2">
                        <span class="text-primary fw-bold" style="font-size: 0.75rem; letter-spacing: 0.5px;">${disp} <span class="text-muted fw-normal">(${a.symbol})</span></span>
                    </div>
                    <h5 class="fw-bold mb-3" style="font-size: 1.05rem; line-height: 1.4; color: #1e293b;">
                        ${title}${sentimentHtml}
                    </h5>
                    ${cleanSummary ? `<p class="text-secondary small flex-grow-1" style="line-height: 1.6; opacity: 0.85;">${cleanSummary}</p>` : '<div class="flex-grow-1"></div>'}
                    <div class="mt-3 pt-3 border-top d-flex justify-content-end">
                        <a href="${a.link}" target="_blank" class="btn btn-link btn-sm p-0 text-decoration-none fw-bold" style="font-size: 11px;">
                            View Source <i class="bi bi-arrow-right ms-1"></i>
                        </a>
                    </div>
                </div>
            </div>
        </div>`;
    }).join('');
}

let GLOBAL_OVERLAP_DATA = null;

function showDiagnosticDetails(type) {
    const title = document.getElementById('diagnostic-modal-title');
    const body = document.getElementById('diagnostic-modal-body');
    const modal = new bootstrap.Modal(document.getElementById('diagnosticModal'));

    if (type === 'OVERLAP') {
        title.innerText = "Mutual Fund Stock Overlap Details";
        if (GLOBAL_OVERLAP_DATA && GLOBAL_OVERLAP_DATA.overlap_score == null) {
            body.textContent = GLOBAL_OVERLAP_DATA.diagnostic_summary || "Verified fund holdings data is unavailable.";
        } else if (!GLOBAL_OVERLAP_DATA) {
            body.innerHTML = "<p class='text-muted text-center py-4'>No overlap data available. Try refreshing intelligence.</p>";
        } else {
            const topShared = GLOBAL_OVERLAP_DATA.top_shared_stocks || [];
            const score = GLOBAL_OVERLAP_DATA.overlap_score || 0;
            const summary = GLOBAL_OVERLAP_DATA.diagnostic_summary || "Hidden concentration analysis completed.";
            const impactAlert = GLOBAL_OVERLAP_DATA.impact_alert;
            
            body.innerHTML = `
                <!-- Hero Section -->
                <div class='text-center py-4 mb-4' style='background: #f8fbff; border-radius: 12px;'>
                    <div style='font-size: 64px; font-weight: 800; color: #0066ff; line-height: 1;'>${score}%</div>
                    <p class='fw-bold text-dark mt-2 mb-1'>Portfolio Overlap Score</p>
                    <p class='small text-muted px-4 mx-auto' style='max-width: 500px;'>This v1.0.0 Community Edition estimate is a demo diagnostic, not a live fund look-through calculation. Treat it as a UI preview until real fund holdings data is connected.</p>
                </div>

                <!-- Table Container -->
                <div class='mb-4'>
                    <h6 class='fw-bold mb-3 small text-uppercase' style='letter-spacing: 1px; color: #444;'>MAJOR SHARED HOLDINGS & IMPACT</h6>
                    <div class='table-responsive'>
                        <table class='table table-borderless' style='font-size: 14px;'>
                            <thead style='border-bottom: 2px solid #eee;'>
                                <tr>
                                    <th class='text-muted' style='font-weight: 600;'>SHARED STOCK</th>
                                    <th class='text-muted' style='font-weight: 600;'>TOTAL EFFECTIVE EXPOSURE</th>
                                    <th class='text-muted' style='font-weight: 600;'>FUND-LEVEL IMPACT</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${topShared.map(e => `
                                    <tr style='border-bottom: 1px solid #f0f0f0;'>
                                        <td class='py-3'><strong>${e.name}</strong></td>
                                        <td class='py-3'>${e.weight_in_portfolio?.toFixed(2)}%</td>
                                        <td class='py-3'>
                                            <div class='d-flex flex-column gap-1'>
                                                ${(e.contributing_funds || []).map(f => `
                                                    <span style='background: #eef5ff; color: #0055cc; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; width: fit-content;'>
                                                        ${f.contribution}% via ${f.fund}
                                                    </span>
                                                `).join('')}
                                            </div>
                                        </td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Diagnostic Insight -->
                <div class='p-4 mb-2' style='background: #eef9ff; border-radius: 8px; border-left: 4px solid #00aaff;'>
                    <div class='d-flex align-items-center gap-2 mb-2' style='color: #005588;'>
                        <i class='bi bi-info-circle-fill'></i>
                        <strong>Estimated Diagnostic Summary</strong>
                    </div>
                    <p class='small mb-3' style='line-height: 1.6; color: #333;'>
                        ${summary}
                    </p>
                    ${impactAlert ? `
                        <div class='pt-3 mt-2' style='border-top: 1px solid rgba(0,0,0,0.05); color: #d32f2f;'>
                            <strong class='small text-uppercase' style='letter-spacing: 0.5px;'>Impact Alert:</strong>
                            <p class='small mb-0 mt-1'>${impactAlert}</p>
                        </div>
                    ` : ''}
                </div>
            `;
        }
    } else if (type === 'TAX_LOSS') {
        title.innerText = "Tax-Loss Harvesting Candidates";
        const summary = JSON.parse(localStorage.getItem('finreview_latest_summary') || '{}');
        const candidates = summary.tax_loss_candidates || [];
        
        if (candidates.length === 0) {
            body.innerHTML = "<div class='text-center py-4'><i class='bi bi-check-circle text-success h1'></i><p class='mt-2 fw-bold'>No tax-loss candidates found.</p><p class='small text-muted'>All your current holdings are in profit or neutral.</p></div>";
        } else {
            body.innerHTML = `
                <div class='mb-4'>
                    <p class='small text-muted'>Tax-loss harvesting involves selling securities at a loss to offset capital gains tax liabilities. Below are your current holdings trading below their average cost.</p>
                </div>
                <ul class='list-group list-group-flush'>
                    ${candidates.map(c => `
                        <li class='list-group-item d-flex justify-content-between align-items-center bg-transparent px-0'>
                            <span><b>${resolveDisplayName(c)}</b> (${c})</span>
                            <span class='badge bg-soft-danger text-danger border border-danger border-opacity-10'>In Loss</span>
                        </li>
                    `).join('')}
                </ul>
                <div class='mt-4 alert alert-warning py-2 small border-0'>
                    <i class='bi bi-exclamation-triangle me-2'></i> Selling for tax-loss should align with your long-term strategy. Consult a tax advisor for details.
                </div>
            `;
        }
    }
    modal.show();
}

function updateUI(data) {
    const user = localStorage.getItem('finreview_user') || 'Guest';
    const userDisplay = document.getElementById('user-display');
    if (userDisplay) userDisplay.innerText = user;

    // Save summary for diagnostics modal access
    if (data.analytics?.data_json) {
        try {
            const extra = JSON.parse(data.analytics.data_json.replace(/'/g, '"'));
            localStorage.setItem('finreview_latest_summary', JSON.stringify(extra));
        } catch(e){}
    }

    // --- SYNC GLOBAL HOLDINGS FOR SEARCH FILTERING ---
    GLOBAL_HOLDINGS = data.holdings || [];
    updateEmptyPortfolioState(GLOBAL_HOLDINGS);
    const avatarInit = document.getElementById('user-avatar-init');
    if (avatarInit) avatarInit.innerText = user.charAt(0).toUpperCase();

    applyCommunityAccessStyles();
    const planName = document.getElementById('prof-plan-name');
    const planDesc = document.getElementById('prof-plan-desc');
    if (planName) {
        planName.innerText = "Community Edition";
        planName.className = "fw-bold mb-0 text-success";
        if (planDesc) planDesc.innerText = "All portfolio intelligence features are available.";
    }
        let totalInvested = data.analytics?.total_cost || data.total_cost || 0;
    if (!totalInvested && data.holdings) {
        totalInvested = data.holdings.reduce((sum, h) => sum + (h.total_quantity * h.avg_price), 0);
    }
    const totalValuation = data.total_valuation || 0;

    document.getElementById('total-val').innerText = formatINR(totalValuation);
    document.getElementById('total-invested').innerText = formatINR(totalInvested);

    const gain = data.total_gain_loss || (totalValuation - totalInvested);
    const gainPct = data.total_gain_loss_pct || (totalInvested > 0 ? (gain / totalInvested) * 100 : 0);
    
    const gainEl = document.getElementById('total-gain');
    if (gainEl) {
        gainEl.innerText = `${formatINR(gain)} (${gainPct.toFixed(2)}%)`;
        gainEl.className = `stat-val ${gain >= 0 ? 'text-success' : 'text-danger'}`;
    }
    
    const xirr = (data.analytics?.xirr || 0);
    const xirrEl = document.getElementById('total-xirr');
    if (xirrEl) {
        xirrEl.innerText = `${xirr.toFixed(2)}%`;
        xirrEl.className = `stat-val ${xirr >= 0 ? 'text-success' : 'text-danger'}`;
    }

    // --- CONCENTRATION ALERTS ---
    const alertCol = document.getElementById('concentration-alert-col');
    if (alertCol) {
        let isConcentrated = false;
        let symbols = [];
        if (data.analytics?.data_json) {
            try {
                const extra = JSON.parse(data.analytics.data_json.replace(/'/g, '"'));
                if (extra.concentrated_symbols?.length > 0) {
                    isConcentrated = true;
                    symbols = extra.concentrated_symbols;
                }
            } catch (e) {}
        }
        
        if (isConcentrated) {
            alertCol.classList.remove('hidden');
            document.getElementById('concentration-alert-text').innerText = `Warning: ${symbols.join(', ')} exceed 25% threshold.`;
        } else {
            alertCol.classList.add('hidden');
        }
    }

    const diagnosticContent = document.getElementById('portfolio-diagnostics-content');
    if (diagnosticContent) {
        fetch(`${CONFIG.API_URL}/analytics/overlap/${localStorage.getItem('finreview_user_id') || '1'}`, {
            headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` }
        })
        .then(res => res.json())
        .then(overlapData => {
            GLOBAL_OVERLAP_DATA = overlapData; // Capture for modal
            const score = overlapData.overlap_score;
            const overlapHtml = `
                <div class="d-flex justify-content-between mb-2 cursor-pointer" onclick="showDiagnosticDetails('OVERLAP')">
                    <span>MF Overlap: <i class="bi bi-info-circle ms-1 small"></i></span>
                    <span class="fw-bold ${score == null ? 'text-muted' : (score > 40 ? 'text-danger' : (score > 20 ? 'text-warning' : 'text-success'))}">${score == null ? 'Unavailable' : score + '%'}</span>
                </div>`;
            
            let taxHtml = `
                <div class="d-flex justify-content-between cursor-pointer" onclick="showDiagnosticDetails('TAX_LOSS')">
                    <span>Tax-Loss: <i class="bi bi-info-circle ms-1 small"></i></span>
                    <span class="fw-bold text-success">None</span>
                </div>`;
            
            if (data.analytics?.data_json) {
                try {
                    const extra = JSON.parse(data.analytics.data_json.replace(/'/g, '"'));
                    if (extra.tax_loss_candidates?.length > 0) {
                        taxHtml = `
                            <div class="d-flex justify-content-between cursor-pointer" onclick="showDiagnosticDetails('TAX_LOSS')">
                                <span>Tax-Loss: <i class="bi bi-info-circle ms-1 small"></i></span>
                                <span class="fw-bold text-danger">${extra.tax_loss_candidates.length} Stocks</span>
                            </div>`;
                    }
                } catch (e) {}
            }
            diagnosticContent.innerHTML = overlapHtml + taxHtml;
        }).catch(() => {});
    }

    // --- DRIFT ANALYSIS ---
    const rebalanceList = document.getElementById('rebalance-list');
    const fullRebalanceList = document.getElementById('full-rebalance-list');
    const rebalanceSummary = document.getElementById('rebalance-summary');
    
    if (rebalanceList) {
        let driftData = [];
        if (data.analytics?.data_json) {
            try {
                const extra = JSON.parse(data.analytics.data_json.replace(/'/g, '"'));
                driftData = extra.drift_analysis || [];
            } catch (e) {}
        }
        
        if (driftData.length === 0) {
            rebalanceSummary.innerText = "Check weight variance in profile";
            rebalanceList.innerHTML = '<div class="text-muted small py-2">No target allocation active.</div>';
        } else {
            rebalanceSummary.innerText = `${driftData.length} Assets tracked for drift.`;
            const driftHtml = driftData.map(d => {
                const color = Math.abs(d.drift) > 5 ? 'text-danger' : 'text-success';
                return `<div class="d-flex justify-content-between mb-2 small">
                            <span>${d.symbol}</span>
                            <span class="${color} fw-bold">${d.drift > 0 ? '+' : ''}${d.drift.toFixed(1)}% Drift</span>
                        </div>`;
            }).join('');
            rebalanceList.innerHTML = driftHtml;
            
            if (fullRebalanceList) {
                fullRebalanceList.innerHTML = `
                    <div class="table-responsive">
                        <table class="table">
                            <thead><tr><th>Asset</th><th>Target %</th><th>Current %</th><th>Drift</th></tr></thead>
                            <tbody>
                                ${driftData.map(d => `
                                    <tr>
                                        <td>${d.symbol}</td>
                                        <td>${d.target_pct}%</td>
                                        <td>${d.current_pct.toFixed(1)}%</td>
                                        <td class="fw-bold ${Math.abs(d.drift) > 5 ? 'text-danger' : 'text-success'}">${d.drift > 0 ? '+' : ''}${d.drift.toFixed(1)}%</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                `;
            }
        }
    }

    if (data.holdings) renderAllocationChart(data.holdings);

    const insightList = document.getElementById('ai-insights-list');
    if (insightList) insightList.innerHTML = '';
    
    if (data.ai_insights && Object.keys(data.ai_insights).length > 0) {
        const briefing = data.ai_insights['PORTFOLIO_BRIEFING'] || Object.values(data.ai_insights)[0];
        const snippetEl = document.getElementById('ai-briefing-snippet');
        if (snippetEl) snippetEl.innerText = sanitizeCurrency(briefing.content.replace(/\*\*/g, '').substring(0, 100) + "...");

        Object.entries(data.ai_insights).forEach(([type, insight]) => {
            const div = document.createElement('div');
            div.className = 'insight-item p-4 mb-4 rounded bg-white border-0 shadow-sm';
            const badgeClass = type.includes('RISK') ? 'badge-soft-danger' : 'badge-soft-primary';
            div.innerHTML = `<div class="mb-3"><span class="badge ${badgeClass} px-3 py-2">${type.replace(/_/g, ' ')}</span></div> 
                             <div class="text-dark small" style="line-height: 1.7;">${formatMarkdown(sanitizeCurrency(insight.content))}</div>`;
            insightList.appendChild(div);
        });
    }

    const sortedHoldings = [...(data.holdings || [])].sort((a, b) => (b.current_valuation || 0) - (a.current_valuation || 0));
    const tbody = document.getElementById('holdings-body');
    if (tbody) {
        tbody.innerHTML = '';
        sortedHoldings.slice(0, 5).forEach(h => {
            const row = document.createElement('tr');
            const displayName = h.name || resolveDisplayName(h.symbol);
            const pnlClass = h.gain_loss_pct >= 0 ? 'text-success' : 'text-danger';
            row.innerHTML = `<td class="ps-3"><b>${displayName}</b><br><small class="text-muted">${h.symbol}</small></td>
                             <td>${h.total_quantity}</td>
                             <td>${formatINR(h.avg_price)}</td>
                             <td class="fw-bold">${formatINR(h.current_valuation)}</td>
                             <td class="text-end pe-3"><span class="badge ${h.gain_loss_pct >= 0 ? 'badge-soft-success' : 'badge-soft-danger'}">${h.gain_loss_pct?.toFixed(2)}%</span></td>`;
            tbody.appendChild(row);
        });
    }

    const fullBody = document.getElementById('full-portfolio-body');
    if (fullBody) {
        fullBody.innerHTML = '';
        document.getElementById('port-total-val').innerText = formatINR(totalValuation);
        document.getElementById('port-total-invested').innerText = formatINR(totalInvested);
        
        const portGainEl = document.getElementById('port-total-gain');
        portGainEl.innerText = `${formatINR(gain)} (${gainPct.toFixed(2)}%)`;
        portGainEl.className = `fw-bold h5 mb-0 ${gain >= 0 ? 'text-success' : 'text-danger'}`;

        sortedHoldings.forEach(h => {
            const row = document.createElement('tr');
            const ltp = h.total_quantity > 0 ? (h.current_valuation / h.total_quantity) : 0;
            const displayName = h.name || resolveDisplayName(h.symbol);
            row.innerHTML = `<td class="ps-4"><b>${displayName}</b><br><small class="text-muted">${h.symbol}</small></td>
                             <td>${h.total_quantity}</td>
                             <td>${formatINR(h.avg_price)}</td>
                             <td>${formatINR(ltp)}</td>
                             <td>${formatINR(h.total_quantity * h.avg_price)}</td>
                             <td>${formatINR(h.current_valuation)}</td>
                             <td class="text-end pe-4"><span class="badge ${h.gain_loss_pct >= 0 ? 'badge-soft-success' : 'badge-soft-danger'}">${h.gain_loss_pct?.toFixed(2)}%</span></td>`;
            fullBody.appendChild(row);
        });
    }

    const newsList = document.getElementById('news-list');
    const newsGrid = document.getElementById('full-news-grid');
    if (newsList) {
        newsList.innerHTML = '';
        const newsItems = data.latest_news_list || [];
        GLOBAL_NEWS_LIST = newsItems; // Save for filtering

        if (newsItems.length > 0) {
            // dashboard highlights: show most recent per symbol with action arrow
            const grouped = {};
            newsItems.forEach(a => { if(!grouped[a.symbol]) grouped[a.symbol]=[]; grouped[a.symbol].push(a); });
            Object.entries(grouped).forEach(([sym, arts]) => {
                const displayName = resolveDisplayName(sym);
                const first = arts[0];
                const cat = first.category || 'NEWS';
                const catClass = cat === 'RESULT' ? 'bg-success' : (cat === 'ANNOUNCEMENT' ? 'bg-warning text-dark' : 'bg-primary');
                
                const item = document.createElement('div');
                item.className = 'news-item py-2 border-bottom cursor-pointer';
                item.innerHTML = `
                    <div class="d-flex justify-content-between align-items-start" style="gap:8px;">
                        <div class="flex-grow-1" style="min-width:0; word-break:break-word;">
                            <div class="d-flex align-items-center mb-1">
                                <span class="badge ${catClass} me-2" style="font-size: 7px; padding: 2px 4px;">${cat}</span>
                                <small class="fw-bold text-primary">${displayName}</small>
                            </div>
                            <div class="small text-dark" style="word-wrap:break-word; overflow-wrap:break-word; white-space:normal;">${first.title}</div>
                        </div>
                        <div class="ms-2 text-primary" style="font-size:14px; flex-shrink:0;">→</div>
                    </div>`;
                item.onclick = () => showPage('news');
                newsList.appendChild(item);
            });
            
            // Render the main news page grid (default to ALL)
            renderNewsFeed(newsItems);
        } else {
            newsList.innerHTML = '<div class="text-muted text-center py-3" style="font-size: 12px;">No news articles yet. <br>Click "Refresh Intelligence" to fetch market news.</div>';
            if (newsGrid) newsGrid.innerHTML = '';
        }
        // add/replace view all link at bottom of dashboard card (avoid duplicates)
        const existingViewAll = document.getElementById('view-all-news-link');
        if (existingViewAll) existingViewAll.remove();
        const viewAllLink = document.createElement('div');
        viewAllLink.id = 'view-all-news-link';
        viewAllLink.className = 'text-end mt-2';
        viewAllLink.innerHTML = `<a href="javascript:void(0)" onclick="showPage('news')" class="small text-decoration-none fw-bold">View all news &rarr;</a>`;
        newsList.parentElement.appendChild(viewAllLink);
    }
}

let pendingPageId = null;

function showPage(pageId, force = false, silent = false) {
    console.log(`Showing page: ${pageId}`, { hasData: !!GLOBAL_SUMMARY_DATA });
    // 1. Navigation Guard: Check for unsaved changes if leaving Profile
    const currentPage = Array.from(document.querySelectorAll('.page')).find(p => !p.classList.contains('hidden'))?.id;
    if (!force && currentPage === 'profile-page' && pageId !== 'profile') {
        const isDirty = JSON.stringify(CURRENT_TARGET_ALLOC) !== JSON.stringify(SAVED_TARGET_ALLOC);
        if (isDirty) {
            pendingPageId = pageId;
            const modalEl = document.getElementById('confirmModal');
            const discardBtn = document.getElementById('confirm-discard-btn');
            
            // Set up discard button action
            discardBtn.onclick = () => {
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
                showPage(pendingPageId, true); // Force transition
            };

            const modal = new bootstrap.Modal(modalEl);
            modal.show();

            // Reset active nav link to profile since we haven't left yet
            document.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));
            const nav = document.getElementById('nav-profile');
            if (nav) nav.classList.add('active');
            return;
        }
    }

    // 2. Close any open modals first (Skip if silent)
    if (!silent) {
        const openModals = document.querySelectorAll('.modal.show');
        openModals.forEach(m => {
            const inst = bootstrap.Modal.getInstance(m);
            if (inst) inst.hide();
        });
    }

    if (pageId === 'profile') {
        updateProfileUI();
    } else if (GLOBAL_SUMMARY_DATA) {
        updateUI(GLOBAL_SUMMARY_DATA);
    }
    document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
    const target = document.getElementById(`${pageId}-page`);
    if (target) {
        target.classList.remove('hidden');
        localStorage.setItem('finreview_current_page', pageId);
    }
    document.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));
    const nav = document.getElementById(`nav-${pageId}`);
    if (nav) nav.classList.add('active');
    
    // Special handling for Edit Strategy shortcut
    if (pageId === 'profile' && window.location.hash === '#edit-strategy') {
        setTimeout(() => {
            const section = document.getElementById('strategic-strategy-section');
            if (section) {
                // Ensure it scrolls with enough offset for the sticky navbar
                const offset = 100;
                const bodyRect = document.body.getBoundingClientRect().top;
                const elementRect = section.getBoundingClientRect().top;
                const elementPosition = elementRect - bodyRect;
                const offsetPosition = elementPosition - offset;

                window.scrollTo({
                    top: offsetPosition,
                    behavior: 'smooth'
                });
                
                section.classList.add('highlight-flash');
                setTimeout(() => section.classList.remove('highlight-flash'), 2000);
            }
            // Clear hash
            history.replaceState(null, null, ' ');
        }, 300);
    } else {
        window.scrollTo(0,0);
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    await init();
});

async function init() {
    // 1. ALWAYS bind event listeners first
    document.getElementById('login-form')?.addEventListener('submit', handleAuth);
    document.getElementById('bulk-tx-form')?.addEventListener('submit', recordBulkTransactions);
    document.getElementById('alert-form')?.addEventListener('submit', handleAlertSubmit);
    document.getElementById('profile-form')?.addEventListener('submit', handleProfileUpdate);

    // 2. Check Authentication
    const storedId = localStorage.getItem('finreview_user_id');
    if ((!AUTH_TOKEN || !storedId)) {
        // If we have some partial data but not enough, clear it once but DON'T reload
        if (AUTH_TOKEN || storedId) {
            localStorage.clear();
            AUTH_TOKEN = null;
        }
        
        document.getElementById('auth-page').classList.remove('hidden');
        document.getElementById('main-app').classList.add('hidden');
        document.getElementById('main-nav').classList.add('hidden');
        document.getElementById('main-footer').classList.add('hidden');
        return;
    }
    
    document.getElementById('auth-page').classList.add('hidden');
    document.getElementById('main-app').classList.remove('hidden');
    document.getElementById('main-nav').classList.remove('hidden');
    document.getElementById('main-footer').classList.remove('hidden');
    
    // 3. Determine initial page
    let initialPage = 'dashboard';
    const savedPage = localStorage.getItem('finreview_current_page');
    
    if (window.location.hash.includes('profile')) initialPage = 'profile';
    else if (window.location.hash.includes('portfolio')) initialPage = 'portfolio';
    else if (window.location.hash.includes('rebalance')) initialPage = 'rebalance';
    else if (window.location.hash.includes('news')) initialPage = 'news';
    else if (savedPage) initialPage = savedPage;

    await fetchPortfolioSummary();
    showPage(initialPage);
    applyCommunityAccessStyles();
    

    const txRows = document.getElementById('tx-rows');
    if (txRows && txRows.children.length === 0) addTxRow();

    const alertModalEl = document.getElementById('alertModal');
    if (alertModalEl) {
        alertModalEl.addEventListener('show.bs.modal', () => {
            fetchAlertRules(localStorage.getItem('finreview_user_id'));
        });
        alertModalEl.addEventListener('hidden.bs.modal', () => {
            document.getElementById('alert-form').reset();
            const symbolSearch = document.getElementById('alert-symbol-search');
            if (symbolSearch) { symbolSearch.disabled = false; symbolSearch.value = ''; }
            document.getElementById('alert-symbol').value = '';
            document.getElementById('results-alert').classList.add('hidden');
        });
    }
}
