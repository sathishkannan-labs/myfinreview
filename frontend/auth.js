// --- FINREVIEW AUTHENTICATION LOGIC ---
let AUTH_TOKEN = localStorage.getItem('finreview_token');
let IS_SIGNUP = false;

function toggleAuthMode() {
    IS_SIGNUP = !IS_SIGNUP;
    document.getElementById('auth-title').innerText = IS_SIGNUP ? "Sign Up" : "Login";
    document.getElementById('auth-submit-btn').innerText = IS_SIGNUP ? "Create Account" : "Login";
    document.getElementById('auth-toggle').innerText = IS_SIGNUP ? "Already have an account? Login" : "Don't have an account? Sign Up";
    
    // Toggle Extra Fields
    const extraFields = document.getElementById('signup-extra-fields');
    if (IS_SIGNUP) extraFields.classList.remove('hidden');
    else extraFields.classList.add('hidden');

}

function showAuthModal() {
    document.getElementById('main-app').classList.add('hidden');
    document.getElementById('auth-page').classList.remove('hidden');
}


async function handleAuth(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const email = document.getElementById('auth-email').value;
    const password = document.getElementById('auth-password').value;

    const payload = { 
        email, 
        password,
        full_name: document.getElementById('auth-name')?.value,
        dob_year: parseInt(document.getElementById('auth-dob')?.value) || null,
        city: document.getElementById('auth-city')?.value,
        state: document.getElementById('auth-state')?.value
    };
    const endpoint = IS_SIGNUP ? '/auth/signup' : '/auth/login';

    try {
        const response = await fetch(`${CONFIG.API_URL}${endpoint}`, {
            method: 'POST',
            body: JSON.stringify(payload),
            headers: { 'Content-Type': 'application/json' }
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Authentication failed");
        }

        const data = await response.json();

        if (IS_SIGNUP) {
            showToast("Account created successfully! Please login.", "success");
            event.target.reset(); // Clear credentials
            toggleAuthMode();
        } else {
            // Login Successful
            showToast("Welcome back!", "success");
            // Clear demo state so it doesn't pollute the real account view
            localStorage.removeItem('finreview_demo_state');
            setAuth(data.token, data.email.split('@')[0], data.user_id, {
                email: data.email,
                full_name: data.full_name,
                dob_year: data.dob_year,
                city: data.city,
                state: data.state,
                target_allocation: data.target_allocation,
                last_intelligence_refresh: data.last_intelligence_refresh
            });
        }
    } catch (error) {
        showToast(error.message, "danger");
    }
}

async function handleForgotPassword() {
    const email = document.getElementById('auth-email').value;
    if (!email) {
        showToast("Please enter your email address first.", "warning");
        return;
    }
    
    try {
        const response = await fetch(`${CONFIG.API_URL}/auth/forgot-password`, {
            method: 'POST',
            body: JSON.stringify({ email, password: "" }),
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        showToast(data.message || data.detail || "Password reset is not enabled in v1.0.0.", response.ok ? "primary" : "warning");
    } catch (e) {
        showToast("Failed to initiate password reset.", "danger");
    }
}

// --- TARGET ALLOCATION ---
let CURRENT_TARGET_ALLOC = {};
let SAVED_TARGET_ALLOC = {};

function addTargetAlloc() {
    const sym = document.getElementById('target-sym').value.trim().toUpperCase();
    const weight = parseFloat(document.getElementById('target-weight').value);
    
    if (sym && !isNaN(weight)) {
        CURRENT_TARGET_ALLOC[sym] = weight;
        renderTargetAllocUI();
        const searchInput = document.getElementById('target-sym-search');
        if (searchInput) searchInput.value = '';
        document.getElementById('target-sym').value = '';
        document.getElementById('target-weight').value = '';
    }
}

function addTargetAllocModal() {
    const sym = document.getElementById('target-sym-modal').value.trim().toUpperCase();
    const weight = parseFloat(document.getElementById('target-weight-modal').value);
    
    if (sym && !isNaN(weight)) {
        CURRENT_TARGET_ALLOC[sym] = weight;
        renderTargetAllocUI();
        const searchInput = document.getElementById('target-sym-search-modal');
        if (searchInput) searchInput.value = '';
        document.getElementById('target-sym-modal').value = '';
        document.getElementById('target-weight-modal').value = '';
    }
}

function removeTargetAlloc(sym) {
    delete CURRENT_TARGET_ALLOC[sym];
    renderTargetAllocUI();
}

function renderTargetAllocUI() {
    const inline = document.getElementById('target-alloc-inline');
    const full = document.getElementById('target-alloc-full-list');
    const entries = Object.entries(CURRENT_TARGET_ALLOC);

    // Deep compare to check if dirty
    const isDirty = JSON.stringify(CURRENT_TARGET_ALLOC) !== JSON.stringify(SAVED_TARGET_ALLOC);

    if (inline) {
        if (entries.length === 0) {
            inline.innerHTML = '<div class="small text-muted">No strategic targets set.</div>';
        } else {
            const dirtyBadge = isDirty ? '<div class="mb-2"><span class="badge bg-warning text-dark" style="font-size: 9px;">UNSAVED CHANGES</span></div>' : '';
            const helpText = isDirty ? '<div class="mt-1 small text-muted italic" style="font-size: 10px;">Click \'Save Changes\' below to persist this strategy.</div>' : '';
            
            inline.innerHTML = `
                ${dirtyBadge}
                ${entries.map(([sym, w]) => `
                    <span class="badge bg-light text-dark me-2 mb-1 p-2 border position-relative" style="font-size: 0.75rem;">
                        <b>${sym}</b> ${w}%
                        <i class="bi bi-x-circle-fill ms-1 text-danger cursor-pointer" onclick="removeTargetAlloc('${sym}')" style="font-size: 10px;"></i>
                    </span>
                `).join('')}
                ${helpText}
            `;
        }
    }

    if (full) {
        if (entries.length === 0) {
            full.innerHTML = '<div class="text-center py-4 text-muted small">No strategic targets defined.</div>';
        } else {
            full.innerHTML = entries.map(([sym, w]) => `
                <div class="d-flex justify-content-between align-items-center mb-2 p-3 bg-white rounded shadow-sm border">
                    <div><b>${resolveDisplayName(sym)}</b><br><small class="text-muted">${sym}</small></div>
                    <div class="d-flex align-items-center gap-3">
                        <span class="fw-bold">${w}%</span>
                        <button type="button" class="btn btn-sm btn-outline-danger border-0" onclick="removeTargetAlloc('${sym}')"><i class="bi bi-trash"></i></button>
                    </div>
                </div>
            `).join('');
        }
    }
}

function openTargetModal() {
    // Ensure modal inputs are in sync
    document.getElementById('target-sym-search-modal').value = '';
    const modalEl = document.getElementById('targetModal');
    const modal = new bootstrap.Modal(modalEl);
    renderTargetAllocUI();
    modal.show();
}

function saveTargetAllocModal() {
    // Persist to localStorage for later profile save
    localStorage.setItem('finreview_target_alloc', JSON.stringify(CURRENT_TARGET_ALLOC));
    // close modal
    const modalEl = document.getElementById('targetModal');
    const modal = bootstrap.Modal.getInstance(modalEl);
    if (modal) modal.hide();
    showToast('Target allocation staged. Click Save Changes to persist to profile.', 'success');
    // Update any inline views
    renderTargetAllocUI();
}

async function handleProfileUpdate(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const userIdStr = localStorage.getItem('finreview_user_id');
    if (!userIdStr) {
        showToast("User session expired.", "danger");
        return;
    }
    const userId = parseInt(userIdStr);
    
    const payload = {
        email: localStorage.getItem('finreview_email'),
        password: "", // Not used for profile update
        full_name: document.getElementById('prof-name').value,
        dob_year: parseInt(document.getElementById('prof-dob').value) || null,
        city: document.getElementById('prof-city').value,
        state: document.getElementById('prof-state').value
    };

    toggleLoading(true);
    try {
        // 1. Update Profile
        const profileRes = await fetch(`${CONFIG.API_URL}/auth/profile`, {
            method: 'POST',
            body: JSON.stringify(payload),
            headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${AUTH_TOKEN}`
            }
        });

        if (!profileRes.ok) {
            const err = await profileRes.json();
            throw new Error(err.detail || "Profile update failed");
        }

        // 2. Update Target Allocation & Sensitivity
        const sensitivity = parseFloat(document.getElementById('drift-sensitivity')?.value || 5.0);
        const allocRes = await fetch(`${CONFIG.API_URL}/auth/target-allocation`, {
            method: 'POST',
            body: JSON.stringify({ 
                user_id: userId, 
                allocation: CURRENT_TARGET_ALLOC,
                drift_sensitivity: sensitivity
            }),
            headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${AUTH_TOKEN}`
            }
        });

        if (!allocRes.ok) {
            const err = await allocRes.json();
            throw new Error(err.detail || "Strategy update failed");
        }

        showToast("Profile and Strategy updated!", "success");
        
        // Update local storage
        localStorage.setItem('finreview_name', payload.full_name);
        if (payload.dob_year) localStorage.setItem('finreview_dob', payload.dob_year);
        localStorage.setItem('finreview_city', payload.city);
        localStorage.setItem('finreview_state', payload.state);
        localStorage.setItem('finreview_target_alloc', JSON.stringify(CURRENT_TARGET_ALLOC));
        localStorage.setItem('finreview_drift_sensitivity', sensitivity.toString());
        
        // Sync saved state to clear dirty badge
        SAVED_TARGET_ALLOC = JSON.parse(JSON.stringify(CURRENT_TARGET_ALLOC));
        renderTargetAllocUI();

        if (typeof fetchPortfolioSummary === 'function') fetchPortfolioSummary(true);

    } catch (e) {
        showToast(e.message, "danger");
    } finally {
        toggleLoading(false);
    }
}

async function updateProfileUI() {
    const userId = localStorage.getItem('finreview_user_id') || '1';
    
    // Initial load from localStorage for immediate display
    document.getElementById('prof-name').value = localStorage.getItem('finreview_name') || "";
    document.getElementById('prof-email').value = localStorage.getItem('finreview_email') || "";
    document.getElementById('prof-dob').value = localStorage.getItem('finreview_dob') || "";
    document.getElementById('prof-city').value = localStorage.getItem('finreview_city') || "";
    document.getElementById('prof-state').value = localStorage.getItem('finreview_state') || "";
    
    const sensitivity = localStorage.getItem('finreview_drift_sensitivity') || "5.0";
    const slider = document.getElementById('drift-sensitivity');
    if (slider) {
        slider.value = sensitivity;
        const valEl = document.getElementById('sensitivity-val');
        if (valEl) valEl.innerText = sensitivity + '%';
    }

    try {
        const saved = localStorage.getItem('finreview_target_alloc');
        if (saved) {
            CURRENT_TARGET_ALLOC = typeof saved === 'string' ? JSON.parse(saved) : saved;
            renderTargetAllocUI();
        }
    } catch (e) { 
        console.warn("Local target alloc parse failed", e);
        CURRENT_TARGET_ALLOC = {}; 
        renderTargetAllocUI();
    }

    // Now fetch fresh data from server
    try {
        const response = await fetch(`${CONFIG.API_URL}/auth/profile/${userId}`, {
            headers: { 'Authorization': `Bearer ${AUTH_TOKEN}` }
        });
        if (response.ok) {
            const data = await response.json();
            
            // Update fields
            document.getElementById('prof-name').value = data.full_name || "";
            document.getElementById('prof-dob').value = data.dob_year || "";
            document.getElementById('prof-city').value = data.city || "";
            document.getElementById('prof-state').value = data.state || "";
            
            if (slider) {
                slider.value = data.drift_sensitivity || 5.0;
                document.getElementById('sensitivity-val').innerText = (data.drift_sensitivity || 5.0) + '%';
            }

            // Update Target Allocation
            if (data.target_allocation) {
                const parsed = typeof data.target_allocation === 'string' ? 
                                       JSON.parse(data.target_allocation) : data.target_allocation;
                CURRENT_TARGET_ALLOC = parsed;
                SAVED_TARGET_ALLOC = JSON.parse(JSON.stringify(parsed));
                renderTargetAllocUI();
                
                // Sync back to localStorage
                localStorage.setItem('finreview_target_alloc', JSON.stringify(CURRENT_TARGET_ALLOC));
            }

            // Update other profile storage
            localStorage.setItem('finreview_name', data.full_name || "");
            localStorage.setItem('finreview_dob', data.dob_year || "");
            localStorage.setItem('finreview_city', data.city || "");
            localStorage.setItem('finreview_state', data.state || "");
            localStorage.setItem('finreview_drift_sensitivity', (data.drift_sensitivity || 5.0).toString());
        }
    } catch (error) {
        console.error("Failed to fetch fresh profile", error);
    }
}

function setAuth(token, user, userId, profileData = {}) {
    AUTH_TOKEN = token;
    localStorage.setItem('finreview_token', token);
    localStorage.setItem('finreview_user', user);
    localStorage.setItem('finreview_user_id', String(userId));
    
    // Store profile info
    if (profileData.email) localStorage.setItem('finreview_email', profileData.email);
    if (profileData.full_name) localStorage.setItem('finreview_name', profileData.full_name);
    if (profileData.dob_year) localStorage.setItem('finreview_dob', profileData.dob_year);
    if (profileData.city) localStorage.setItem('finreview_city', profileData.city);
    if (profileData.state) localStorage.setItem('finreview_state', profileData.state);
    if (profileData.target_allocation) {
        localStorage.setItem('finreview_target_alloc', profileData.target_allocation);
    } else {
        localStorage.removeItem('finreview_target_alloc');
    }
    if (profileData.drift_sensitivity) {
        localStorage.setItem('finreview_drift_sensitivity', profileData.drift_sensitivity);
    } else {
        localStorage.setItem('finreview_drift_sensitivity', '5.0');
    }
    if (profileData.last_intelligence_refresh) {
        localStorage.setItem(`finreview_last_refresh_${userId}`, new Date(profileData.last_intelligence_refresh).getTime());
    }

    document.getElementById('auth-page').classList.add('hidden');
    document.getElementById('main-app').classList.remove('hidden');
    
    // Unhide UI elements
    document.getElementById('main-nav').classList.remove('hidden');
    document.getElementById('main-footer').classList.remove('hidden');
    
    // Explicitly show the dashboard page
    if (typeof showPage === 'function') {
        showPage('dashboard');
    }
    fetchPortfolioSummary(true);
}

async function logout() {
    if (AUTH_TOKEN?.startsWith('frv2:')) {
        await fetch(`${CONFIG.API_URL}/v2/market/fyers/session`, {
            method: 'DELETE', keepalive: true, signal: AbortSignal.timeout(3000),
            headers: { Authorization: `Bearer ${AUTH_TOKEN}` }
        }).catch(() => {});
    }
    localStorage.clear();
    location.reload();
}
