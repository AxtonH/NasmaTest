/**
 * AutoLoginButton - Modular component for handling auto-login button functionality
 * Checks if a remember me token exists and triggers auto-login if available
 */
class AutoLoginButton {
    constructor(loginManager) {
        this.loginManager = loginManager;
        this.button = null;
        this.isProcessing = false;
    }

    /**
     * Initialize the auto-login button
     * @param {HTMLElement} buttonElement - The button element to attach to
     */
    init(buttonElement) {
        this.button = buttonElement;
        this.button.addEventListener('click', () => this.handleClick());
    }

    /**
     * Handle button click - check for token and auto-login
     */
    async handleClick() {
        if (this.isProcessing) {
            return; // Prevent multiple simultaneous clicks
        }

        this.isProcessing = true;
        this.setLoading(true);

        try {
            // First check localStorage for token
            const token = this.loginManager.rememberMeManager.getToken();
            
            if (token && this.loginManager.deviceFingerprint) {
                // Token exists in localStorage, use existing auto-login
                console.log('[AUTO_LOGIN_BUTTON] Token found in localStorage, attempting auto-login...');
                await this.loginManager.tryAutoLogin();
                this.setLoading(false);
                this.isProcessing = false;
                return;
            }

            // No token in localStorage, check Supabase for device fingerprint
            if (!this.loginManager.deviceFingerprint) {
                console.error('[AUTO_LOGIN_BUTTON] Device fingerprint not available');
                this.loginManager.showMessage('Device fingerprint not available. Please log in manually.', 'error');
                this.setLoading(false);
                this.isProcessing = false;
                return;
            }

            console.log('[AUTO_LOGIN_BUTTON] No token in localStorage, checking Supabase...');
            
            // Check if token exists in Supabase
            const response = await fetch('/api/auth/check-remember-me', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    device_fingerprint: this.loginManager.deviceFingerprint
                })
            });

            const data = await response.json();

            if (!response.ok || !data.success) {
                throw new Error(data.message || 'Failed to check token');
            }

            if (data.has_token) {
                // Token exists in Supabase but not in localStorage
                // This shouldn't happen normally, but handle it gracefully
                this.loginManager.showMessage('Saved credentials found, but token is missing from browser storage. Please log in manually.', 'error');
                console.warn('[AUTO_LOGIN_BUTTON] Token exists in Supabase but not in localStorage');
            } else {
                // No token found
                this.loginManager.showMessage('No saved credentials found. Please log in manually.', 'error');
                console.log('[AUTO_LOGIN_BUTTON] No saved credentials found');
            }

        } catch (error) {
            console.error('[AUTO_LOGIN_BUTTON] Error:', error);
            this.loginManager.showMessage('Error checking saved credentials. Please log in manually.', 'error');
        } finally {
            this.setLoading(false);
            this.isProcessing = false;
        }
    }

    /**
     * Set loading state for the button
     * @param {boolean} isLoading - Whether the button is in loading state
     */
    setLoading(isLoading) {
        if (!this.button) return;

        if (isLoading) {
            this.button.disabled = true;
            const originalText = this.button.textContent || this.button.innerText;
            this.button.dataset.originalText = originalText;
            this.button.innerHTML = `
                <svg class="animate-spin -ml-1 mr-3 h-5 w-5 inline" style="color: #291C48;" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Checking...
            `;
        } else {
            this.button.disabled = false;
            const originalText = this.button.dataset.originalText || 'Auto-login';
            this.button.textContent = originalText;
        }
    }
}

