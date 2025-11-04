// Chat functionality
class ChatBot {
    constructor() {
        this.chatContainer = document.getElementById('chatContainer');
        this.chatScrollArea = document.getElementById('chatScrollArea');
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        this.loadingIndicator = document.getElementById('loadingIndicator');
        this.connectionStatus = document.getElementById('connectionStatus');
        this.clearChatButton = document.getElementById('clearChat');
        this.logoutButton = document.getElementById('logoutButton');
        
        // Debug elements
        this.debugSidebar = document.getElementById('debugSidebar');
        this.toggleDebugButton = document.getElementById('toggleDebug');
        this.sessionStatus = document.getElementById('sessionStatus');
        this.odooStatus = document.getElementById('odooStatus');
        this.employeeData = document.getElementById('employeeData');
        this.rawOdooResponse = document.getElementById('rawOdooResponse');
        this.chatContext = document.getElementById('chatContext');
        
        this.threadId = null;
        this.isLoading = false;
        this.lastChatContext = null;
        this.autoScrollEnabled = true; // Track if auto-scroll should be enabled
        
        // Load thread ID from localStorage for persistence
        const storedThreadId = localStorage.getItem('chatbot_thread_id');
        if (storedThreadId) {
            this.threadId = storedThreadId;
            console.log(`Loaded thread ID from localStorage: ${this.threadId}`);
            // Initialize employee context for existing thread
            setTimeout(() => this.initializeEmployeeContext(), 1000);
        }
        
        this.init();
    }
    
    init() {
        // Auto-resize textarea
        this.messageInput.addEventListener('input', this.autoResize.bind(this));
        
        // Clear chat functionality
        this.clearChatButton.addEventListener('click', this.clearChat.bind(this));
        
        // Logout functionality
        this.logoutButton.addEventListener('click', this.logout.bind(this));
        
        // Debug functionality
        this.toggleDebugButton.addEventListener('click', this.toggleDebugSidebar.bind(this));
        
        // Check API health on load
        this.checkApiHealth();
        
        // Check authentication status first, then load debug info only if authenticated
        this.checkAuthAndLoadDebug();
        
        // Focus on input
        this.messageInput.focus();
        
        // Test scrollbar functionality
        this.testScrollbar();
        
        // Force input area styling
        this.forceInputStyling();
        
        // Scroll to bottom on page load
        setTimeout(() => {
            this.scrollToBottom(false, true); // Force scroll to bottom on load
        }, 100);
        
        // Add scroll event listener to detect manual scrolling
        this.setupScrollListener();
        
        // Debug: Check if scroll area was found
        console.log('Chat initialization:', {
            chatContainer: !!this.chatContainer,
            chatScrollArea: !!this.chatScrollArea,
            messageInput: !!this.messageInput
        });
    }
    
    forceInputStyling() {
        // Force only the input container to have lilac colors
        setTimeout(() => {
            const inputArea = document.querySelector('.glass-input-area');
            const inputContainer = document.querySelector('.glass-input-container');
            const inputField = document.getElementById('messageInput');
            
            if (inputArea) {
                inputArea.style.setProperty('background', 'transparent', 'important');
            }
            
            if (inputContainer) {
                inputContainer.style.setProperty('background', 'rgba(232, 213, 242, 0.7)', 'important');
                inputContainer.style.setProperty('border', '1px solid rgba(209, 179, 230, 0.5)', 'important');
            }
            
            if (inputField) {
                inputField.style.setProperty('color', '#4A1A5C', 'important');
            }
        }, 100);
    }
    
    testScrollbar() {
        // Test chat area scrolling
        setTimeout(() => {
            if (this.chatScrollArea) {
                console.log('Chat scroll area height:', this.chatScrollArea.scrollHeight);
                console.log('Chat scroll area client height:', this.chatScrollArea.clientHeight);
                console.log('Chat scrollable:', this.chatScrollArea.scrollHeight > this.chatScrollArea.clientHeight);
                
                // Scroll to bottom of chat area
                this.scrollToBottom(false); // Instant scroll for testing
                
                // Add wheel event listener to test mouse wheel scrolling
                this.chatScrollArea.addEventListener('wheel', (e) => {
                    console.log('Chat wheel event detected:', e.deltaY);
                });
            }
        }, 1000);
    }
    
    autoResize() {
        this.messageInput.style.height = 'auto';
        this.messageInput.style.height = Math.min(this.messageInput.scrollHeight, 120) + 'px';
    }
    
    async checkApiHealth() {
        try {
            const response = await fetch('/api/health');
            const data = await response.json();
            
            if (data.status === 'healthy') {
                this.updateConnectionStatus('connected');
            } else {
                this.updateConnectionStatus('error');
            }
        } catch (error) {
            console.error('Health check failed:', error);
            this.updateConnectionStatus('error');
        }
    }
    
    async checkAuthAndLoadDebug() {
        try {
            // First check if user is authenticated
            const response = await fetch('/api/auth/status');
            const data = await response.json();
            
            if (data.authenticated) {
                // User is authenticated, load debug info
                this.loadDebugInfo();
            } else {
                // User is not authenticated, show basic debug info without employee data
                this.updateDebugDisplay({
                    session_data: { authenticated: false },
                    odoo_authenticated: false,
                    employee_data_error: "User not authenticated - login required"
                });
            }
        } catch (error) {
            console.error('Auth check failed:', error);
            // Show error state in debug
            this.updateDebugDisplay({
                session_data: { authenticated: false },
                odoo_authenticated: false,
                employee_data_error: "Authentication check failed"
            });
        }
    }
    
    updateConnectionStatus(status) {
        const statusElement = this.connectionStatus;
        
        switch (status) {
            case 'connected':
                statusElement.className = 'glass-pill status-connected px-3 py-1.5 text-sm font-medium rounded-full';
                statusElement.innerHTML = '<div class="w-2 h-2 bg-green-500 rounded-full inline-block mr-2 animate-pulse"></div>Connected';
                break;
            case 'error':
                statusElement.className = 'glass-pill status-error px-3 py-1.5 text-sm font-medium rounded-full';
                statusElement.innerHTML = '<div class="w-2 h-2 bg-red-500 rounded-full inline-block mr-2"></div>Connection Error';
                break;
            case 'loading':
                statusElement.className = 'glass-pill status-loading px-3 py-1.5 text-sm font-medium rounded-full';
                statusElement.innerHTML = '<div class="w-2 h-2 bg-yellow-500 rounded-full inline-block mr-2 animate-pulse"></div>Processing...';
                break;
        }
    }
    
    async sendMessage() {
        const message = this.messageInput.value.trim();
        if (!message || this.isLoading) return;
        
        this.isLoading = true;
        this.updateConnectionStatus('loading');
        this.sendButton.disabled = true;
        
        // Add user message to chat
        this.addMessage(message, 'user');
        
        // Add thinking bubble
        const thinkingBubble = this.addThinkingBubble();
        
        // Clear input
        this.messageInput.value = '';
        this.autoResize();
        
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    message: message,
                    thread_id: this.threadId
                }),
            });
            
            const data = await response.json();
            
            // Debug: Log the response structure
            console.log('Chat response data:', data);
            console.log('Response structure:', {
                status: data.status,
                response: data.response,
                responseType: typeof data.response,
                hasMessage: data.response && data.response.message,
                messageType: data.response && data.response.message ? typeof data.response.message : 'N/A'
            });
            
            // Remove thinking bubble
            this.removeThinkingBubble(thinkingBubble);
            
            if (data.status === 'success') {
                // Update thread ID if provided and save to localStorage
                if (data.response && data.response.thread_id) {
                    const isNewThread = !this.threadId;
                    this.threadId = data.response.thread_id;
                    localStorage.setItem('chatbot_thread_id', this.threadId);
                    console.log(`Saved thread ID to localStorage: ${this.threadId}`);
                    
                    // If this is a new thread, initialize employee context
                    if (isNewThread) {
                        this.initializeEmployeeContext();
                    }
                }
                
                // Add bot response to chat
                let botMessage = '';
                let responseSource = '';
                let buttons = null;
                let attachments = null;

                if (data.response && data.response.error) {
                    // Handle error response from ChatGPT service
                    botMessage = (typeof data.response === 'string') ? data.response : (data.response.message || 'Sorry, there was an error processing your request.');
                    console.error('ChatGPT service error:', data.response);
                } else if (data.response && typeof data.response === 'object') {
                    // If backend sent an object accidentally, stringify safely
                    if (data.response.message) {
                        botMessage = data.response.message;
                        responseSource = data.response.source || 'unknown';
                        buttons = data.response.buttons || data.buttons || null;  // Extract buttons if present
                        attachments = data.response.attachments || data.attachments || null; // Extract attachments if present
                    } else {
                        botMessage = JSON.stringify(data.response);
                    }
                } else if (typeof data.response === 'string') {
                    botMessage = data.response;
                    // Also try to read attachments/buttons at top-level for safety
                    buttons = data.buttons || null;
                    attachments = data.attachments || null;
                } else {
                    botMessage = 'Sorry, I received an invalid response format.';
                    console.error('Invalid response format:', data.response);
                }

                // Final safety: ensure botMessage is a string
                if (typeof botMessage !== 'string') {
                    try {
                        botMessage = botMessage && botMessage.message ? String(botMessage.message) : JSON.stringify(botMessage);
                    } catch (_) {
                        botMessage = '[object Object]';
                    }
                }

                // No source indicator needed - all responses come from Assistant

                const widgetsMeta = data.widgets || (data.response && data.response.widgets) || null;
                this.addMessage(botMessage, 'bot', buttons, widgetsMeta, attachments);
                this.updateConnectionStatus('connected');
                
                // Update debug info with response data
                if (data.has_employee_context) {
                    console.log('✅ Chat response included employee context');
                } else {
                    console.log('❌ Chat response did NOT include employee context');
                }
                
                // Log response source
                console.log(`🎯 Response from Assistant`);
                
                // Reload debug info to show updated status
                this.loadDebugInfo();
            } else {
                this.addMessage(`Error: ${data.error}`, 'error');
                this.updateConnectionStatus('error');
            }
            
        } catch (error) {
            console.error('Error sending message:', error);
            // Remove thinking bubble on error
            this.removeThinkingBubble(thinkingBubble);
            this.addMessage('Sorry, there was an error connecting to the server. Please try again.', 'error');
            this.updateConnectionStatus('error');
        } finally {
            this.isLoading = false;
            this.sendButton.disabled = false;
            this.messageInput.focus();
        }
    }
    
    addMessage(content, type, buttons = null, widgets = null, attachments = null) {
        const messageDiv = document.createElement('div');

        let containerClass, avatarClass, bubbleClass, textClass, avatarContent;

        switch (type) {
            case 'user':
                containerClass = 'flex justify-end mb-6 message-enter';
                bubbleClass = 'chat-bubble-user rounded-2xl px-4 py-3';
                textClass = '';
                break;
            case 'bot':
                containerClass = 'flex justify-start mb-6 message-enter';
                bubbleClass = 'chat-bubble-bot rounded-2xl px-4 py-3';
                textClass = '';
                break;
            case 'error':
                containerClass = 'flex justify-start mb-6 message-enter';
                bubbleClass = 'chat-bubble-error rounded-2xl px-4 py-3';
                textClass = '';
                break;
        }

        messageDiv.className = containerClass;

        // Create elements with inline styles as fallback
        if (type === 'user') {
            messageDiv.innerHTML = `
                <div class="${bubbleClass}" style="background: rgba(139, 95, 191, 0.85) !important; color: white !important;">
                    <p style="color: white !important; margin: 0; line-height: 1.5;">${this.renderMarkdown(content)}</p>
                </div>
            `;
        } else if (type === 'bot') {
            let buttonsHtml = '';
            let attachmentsHtml = '';
            // Render date range picker widget if requested by backend
            let widgetsHtml = '';
            if (widgets && widgets.date_range_picker) {
                // Generate unique IDs to avoid collisions when multiple pickers are rendered
                const uid = `drp_${Date.now()}_${Math.random().toString(36).slice(2)}`;
                const inputId = `ns_date_range_${uid}`;
                const applyId = `ns_date_apply_${uid}`;
                widgetsHtml = `
                    <div class="mt-3 flex items-center justify-start gap-3">
                        <input type="text" id="${inputId}" class="h-10 px-3 rounded-full border border-gray-300 shadow-sm focus:outline-none focus:ring-2 focus:ring-purple-500" placeholder="Select date range" />
                        <button id="${applyId}" class="h-10 px-4 rounded-full text-sm font-medium" style="background: #8B5FBF; color: white; border: none; cursor: pointer;">Apply</button>
                    </div>`;
                // Store IDs and context on the message element for later wiring
                messageDiv.setAttribute('data-date-input-id', inputId);
                messageDiv.setAttribute('data-date-apply-id', applyId);
                if (widgets && widgets.context_key) {
                    messageDiv.setAttribute('data-context-key', String(widgets.context_key));
                }
            } else if (widgets && widgets.single_date_picker) {
                const uid = `sdp_${Date.now()}_${Math.random().toString(36).slice(2)}`;
                const inputId = `ns_date_single_${uid}`;
                const applyId = `ns_date_apply_${uid}`;
                widgetsHtml = `
                    <div class="mt-3 flex items-center justify-start gap-3">
                        <input type="text" id="${inputId}" class="h-10 px-3 rounded-full border border-gray-300 shadow-sm focus:outline-none focus:ring-2 focus:ring-purple-500" placeholder="Select a date" />
                        <button id="${applyId}" class="h-10 px-4 rounded-full text-sm font-medium" style="background: #8B5FBF; color: white; border: none; cursor: pointer;">Apply</button>
                    </div>`;
                messageDiv.setAttribute('data-date-input-id', inputId);
                messageDiv.setAttribute('data-date-apply-id', applyId);
                messageDiv.setAttribute('data-single-date-mode', 'true');
                if (widgets && widgets.context_key) {
                    messageDiv.setAttribute('data-context-key', String(widgets.context_key));
                }
            } else if (widgets && widgets.hour_range_picker) {
                const uid = `hr_${Date.now()}_${Math.random().toString(36).slice(2)}`;
                const fromId = `ns_hour_from_${uid}`;
                const toId = `ns_hour_to_${uid}`;
                const applyId = `ns_hour_apply_${uid}`;
                const options = Array.isArray(widgets.hour_options) ? widgets.hour_options : [];
                const optsHtml = options.map(o => `<option value="${o.value}">${this.escapeHtml(o.label)}</option>`).join('');
                widgetsHtml = `
                    <div class="mt-3 flex items-center justify-start gap-3">
                        <select id="${fromId}" class="h-10 px-3 rounded-full border border-gray-300 shadow-sm focus:outline-none focus:ring-2 focus:ring-purple-500 min-w-[100px]">
                            ${optsHtml}
                        </select>
                        <span>to</span>
                        <select id="${toId}" class="h-10 px-3 rounded-full border border-gray-300 shadow-sm focus:outline-none focus:ring-2 focus:ring-purple-500 min-w-[100px]">
                            ${optsHtml}
                        </select>
                        <button id="${applyId}" class="h-10 px-4 rounded-full text-sm font-medium" style="background: #8B5FBF; color: white; border: none; cursor: pointer;">Apply</button>
                    </div>`;
                setTimeout(() => {
                    const applyBtn = document.getElementById(applyId);
                    if (applyBtn) {
                        applyBtn.onclick = () => {
                            const fromEl = document.getElementById(fromId);
                            const toEl = document.getElementById(toId);
                            const fromVal = fromEl ? fromEl.value : '';
                            const toVal = toEl ? toEl.value : '';
                            if (!fromVal || !toVal) return;
                            // Send a structured message that backend will interpret in confirmation stage
                            this.messageInput.value = `hour_from=${fromVal}&hour_to=${toVal}`;
                            this.sendMessage();
                        };
                    }
                }, 0);
            } else if (widgets && widgets.select_dropdown) {
                // Generic select dropdown widget with context_key
                const uid = `sel_${Date.now()}_${Math.random().toString(36).slice(2)}`;
                const selectId = `ns_select_${uid}`;
                const applyId = `ns_select_apply_${uid}`;
                const options = Array.isArray(widgets.options) ? widgets.options : [];
                const placeholder = widgets.placeholder || 'Select an option';
                const optsHtml = [`<option value="" disabled selected>${this.escapeHtml(placeholder)}</option>`]
                    .concat(options.map(o => `<option value="${this.escapeHtml(o.value)}">${this.escapeHtml(o.label)}</option>`))
                    .join('');
                widgetsHtml = `
                    <div class="mt-3 flex items-center justify-start gap-3">
                        <select id="${selectId}" class="h-10 px-3 rounded-full border border-gray-300 shadow-sm focus:outline-none focus:ring-2 focus:ring-purple-500 min-w-[220px]">
                            ${optsHtml}
                        </select>
                        <button id="${applyId}" class="h-10 px-4 rounded-full text-sm font-medium" style="background: #8B5FBF; color: white; border: none; cursor: pointer;">Apply</button>
                    </div>`;
                setTimeout(() => {
                    const applyBtn = document.getElementById(applyId);
                    if (applyBtn) {
                        applyBtn.onclick = () => {
                            const sel = document.getElementById(selectId);
                            const val = sel ? sel.value : '';
                            if (!val) return;
                            const contextKey = widgets && widgets.context_key ? String(widgets.context_key) : '';
                            const outgoing = contextKey ? `${contextKey}=${val}` : val;
                            this.messageInput.value = outgoing;
                            this.sendMessage();
                        };
                    }
                }, 0);
            }
            if (buttons && buttons.length > 0) {
                buttonsHtml = '<div class="mt-3 flex flex-wrap justify-start gap-2">';
                buttons.forEach(button => {
                    const isActionDoc = button.type === 'action_document';
                    const cls = isActionDoc ? 'btn-doc-action' : 'btn-leave-type';
                    // Safely pass both value and label to the handler so we can render a natural user bubble
                    const valueArg = String(button.value || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
                    const labelArg = String(button.text || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
                    const handler = isActionDoc
                        ? `window.chatBot.handleDocAction('${valueArg}', '${labelArg}')`
                        : `window.chatBot.handleButtonClick('${valueArg}', '${button.type}')`;
                    buttonsHtml += `
                        <button
                            class="${cls} h-10 px-4 rounded-full text-sm font-medium transition-all duration-200 shadow-sm"
                            style="background: #8B5FBF; color: white; border: none; cursor: pointer;"
                            onclick="${handler}"
                        >
                            ${this.escapeHtml(button.text)}
                        </button>
                    `;
                });
                buttonsHtml += '</div>';
            }

            if (attachments && attachments.length > 0) {
                attachmentsHtml = '<div class="mt-3 flex flex-col items-start gap-2">';
                attachments.forEach(att => {
                    const fileUrl = att.file_url || att.url || '#';
                    const fileName = att.file_name || 'Download';
                    attachmentsHtml += `
                        <a href="${fileUrl}" download="${this.escapeHtml(fileName)}" class="h-10 px-4 rounded-full text-sm font-medium inline-flex items-center justify-center"
                           style="background: #8B5FBF; color: white; border: none; cursor: pointer; text-decoration: none;">
                            Download ${this.escapeHtml(fileName)}
                        </a>
                    `;
                });
                attachmentsHtml += '</div>';
            }

            messageDiv.innerHTML = `
                <div class="${bubbleClass}" style="background: #E8D5F2 !important; color: #4A1A5C !important;">
                    <p style="color: #4A1A5C !important; margin: 0; line-height: 1.5;">${this.renderMarkdown(content)}</p>
                    ${buttonsHtml}
                    ${widgetsHtml}
                    ${attachmentsHtml}
                </div>
            `;
            // Wire up date picker apply if present
            setTimeout(() => {
                const inputId = messageDiv.getAttribute('data-date-input-id');
                const applyId = messageDiv.getAttribute('data-date-apply-id');
                const isSingle = messageDiv.getAttribute('data-single-date-mode') === 'true';
                const inputSel = inputId ? `#${inputId}` : null;
                const inputEl = inputId ? messageDiv.querySelector(`#${inputId}`) : null;
                const contextKey = messageDiv.getAttribute('data-context-key') || '';
                const formatDMY = (d) => {
                    const dd = String(d.getDate()).padStart(2, '0');
                    const mm = String(d.getMonth() + 1).padStart(2, '0');
                    const yyyy = d.getFullYear();
                    return `${dd}/${mm}/${yyyy}`;
                };
                if (window.flatpickr && inputSel) {
                    // Ensure calendar overlays above chat and is not inside the bubble flow
                    (function ensureFlatpickrOverlayStyles(){
                        const STYLE_ID = 'ns-flatpickr-overlay-style';
                        if (!document.getElementById(STYLE_ID)) {
                            const style = document.createElement('style');
                            style.id = STYLE_ID;
                            style.textContent = `.flatpickr-calendar{z-index:2147483647 !important; position: fixed !important;}`;
                            document.head.appendChild(style);
                        }
                    })();
                    if (isSingle) {
                        window.flatpickr(inputSel, {
                            appendTo: document.body,
                            positionElement: inputEl,
                            dateFormat: 'd/m/Y',
                            static: false,
                            onChange: (selectedDates) => {
                                if (!inputEl) return;
                                if (Array.isArray(selectedDates) && selectedDates.length > 0) {
                                    const s = formatDMY(selectedDates[0]);
                                    inputEl.value = s;
                                }
                            },
                            onOpen: function() {
                                // Force highest z-index when calendar opens
                                setTimeout(() => {
                                    const cal = document.querySelector('.flatpickr-calendar');
                                    if (cal) {
                                        cal.style.zIndex = '2147483647';
                                        cal.style.position = 'fixed';
                                    }
                                    // Scroll chat to bottom to give calendar more space
                                    const chatContainer = document.querySelector('#conversationState main');
                                    if (chatContainer) {
                                        chatContainer.scrollTo({
                                            top: chatContainer.scrollHeight,
                                            behavior: 'smooth'
                                        });
                                    }
                                }, 10);
                            }
                        });
                    } else {
                        window.flatpickr(inputSel, {
                            appendTo: document.body,
                            positionElement: inputEl,
                            mode: 'range',
                            dateFormat: 'd/m/Y',
                            static: false,
                            onChange: (selectedDates) => {
                                if (!inputEl) return;
                                if (Array.isArray(selectedDates) && selectedDates.length > 0) {
                                    if (selectedDates.length === 1) {
                                        const s = formatDMY(selectedDates[0]);
                                        inputEl.value = `${s} to ${s}`;
                                    } else {
                                        const s1 = formatDMY(selectedDates[0]);
                                        const s2 = formatDMY(selectedDates[1]);
                                        inputEl.value = `${s1} to ${s2}`;
                                    }
                                }
                            },
                            onOpen: function() {
                                // Force highest z-index when calendar opens
                                setTimeout(() => {
                                    const cal = document.querySelector('.flatpickr-calendar');
                                    if (cal) {
                                        cal.style.zIndex = '2147483647';
                                        cal.style.position = 'fixed';
                                    }
                                    // Scroll chat to bottom to give calendar more space
                                    const chatContainer = document.querySelector('#conversationState main');
                                    if (chatContainer) {
                                        chatContainer.scrollTo({
                                            top: chatContainer.scrollHeight,
                                            behavior: 'smooth'
                                        });
                                    }
                                }, 10);
                            }
                        });
                    }
                }
                // Normalize typed single date on blur
                if (inputEl) {
                    inputEl.addEventListener('blur', () => {
                        const val = (inputEl.value || '').trim();
                        if (!val) return;
                        if (isSingle && val.indexOf(' to ') === -1) {
                            // For single mode, keep a single date in the field
                            inputEl.value = val;
                        } else if (!isSingle && val.indexOf(' to ') === -1) {
                            inputEl.value = `${val} to ${val}`;
                        }
                    });
                }
                // Wire up Apply button
                const applyBtn = applyId ? messageDiv.querySelector(`#${applyId}`) : null;
                if (applyBtn) {
                    applyBtn.addEventListener('click', () => {
                        const val = inputEl ? (inputEl.value || '').trim() : '';
                        if (!val) return;
                        let outgoing = val;
                        if (isSingle) {
                            // Submit as same-day range to satisfy backend
                            outgoing = `${val} to ${val}`;
                        } else if (val.indexOf(' to ') === -1) {
                            outgoing = `${val} to ${val}`;
                        }
                        const finalOutgoing = contextKey ? `${contextKey}=${outgoing}` : outgoing;
                        this.messageInput.value = finalOutgoing;
                        this.sendMessage();
                    });
                }
            }, 0);
        } else {
            messageDiv.innerHTML = `
                <div class="${bubbleClass}" style="background: #FEE2E2 !important; color: #DC2626 !important;">
                    <p style="color: #DC2626 !important; margin: 0; line-height: 1.5;">${this.renderMarkdown(content)}</p>
                </div>
            `;
        }

        this.chatContainer.appendChild(messageDiv);
        this.scrollToBottomDelayed();
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    renderMarkdown(text) {
        // First escape HTML to prevent XSS
        let escaped = this.escapeHtml(text);

        // Then convert **text** to bold
        escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

        // Convert *text* to italic
        escaped = escaped.replace(/\*(.*?)\*/g, '<em>$1</em>');

        // Convert line breaks to <br> tags
        escaped = escaped.replace(/\n/g, '<br>');

        return escaped;
    }

    handleButtonClick(value, type) {
        console.log('Button clicked:', { value, type });

        if (type === 'leave_type_selection' || type === 'sick_leave_mode' || type === 'confirmation_choice') {
            // Disable all buttons after selection to prevent double-clicks
            const buttons = document.querySelectorAll('.btn-leave-type, .choice-button');
            buttons.forEach(btn => {
                btn.disabled = true;
                btn.style.opacity = '0.5';
                btn.style.cursor = 'not-allowed';
            });

            // Set the input value and send the message
            this.messageInput.value = value;
            this.sendMessage();
        }
    }

    async handleDocAction(value, label) {
        console.log('Doc action:', value);
        try {
            // Show the user's selection as a natural message (use the button label, not the internal value)
            if (label && typeof label === 'string') {
                this.addMessage(label, 'user');
            }
            if (value === 'employment_letter_options') {
                // Render a choice message with English/Arabic buttons
                const buttons = [
                    { text: 'Employment letter (English)', value: 'generate_employment_letter_en', type: 'action_document' },
                    { text: 'Employment letter (Arabic)', value: 'generate_employment_letter_ar', type: 'action_document' }
                ];
                this.addMessage('Which version of the Employment Letter would you like?', 'bot', buttons);
                return;
            }
            // Embassy letter flow: delegate to chat backend
            if (value === 'embassy_letter') {
                this.messageInput.value = value;
                this.sendMessage();
                return;
            }
            if (value === 'generate_experience_letter') {
                const resp = await fetch('/api/documents/experience-letter', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
                const data = await resp.json();
                if (data.success && data.attachment) {
                    const att = data.attachment;
                    const message = `Your Experience Letter is ready.`;
                    this.addMessage(message, 'bot', null, null, [att]);
                } else {
                    const err = data.message || 'Failed to generate document.';
                    this.addMessage(`Error: ${err}`, 'error');
                }
                return;
            }
            if (value === 'generate_employment_letter' || value === 'generate_employment_letter_en' || value === 'generate_employment_letter_ar') {
                const lang = value.endsWith('_ar') ? 'ar' : 'en';
                const resp = await fetch('/api/documents/employment-letter', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lang }) });
                const data = await resp.json();
                if (data.success && data.attachment) {
                    // Show a follow-up bot message with the download link
                    const att = data.attachment;
                    const message = `Your Employment Letter is ready.`;
                    this.addMessage(message, 'bot', null, null, [att]);
                } else {
                    const err = data.message || 'Failed to generate document.';
                    this.addMessage(`Error: ${err}`, 'error');
                }
            }
            
            // Handle reimbursement action buttons
            if (type === 'action_reimbursement') {
                this.messageInput.value = value;
                this.sendMessage();
                return;
            }
        } catch (e) {
            console.error('Doc action error:', e);
            this.addMessage('Error generating the document. Please try again later.', 'error');
        }
    }

    async triggerDownload(attachments) {
        try {
            for (const att of attachments) {
                const fileUrl = att.file_url || att.url;
                const fileName = att.file_name || 'document.docx';
                if (!fileUrl) continue;
                console.log('Initiating download (blob fetch):', { fileUrl, fileName });

                try {
                    const resp = await fetch(fileUrl);
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    const blob = await resp.blob();
                    const objectUrl = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = objectUrl;
                    a.download = fileName;
                    a.style.display = 'none';
                    document.body.appendChild(a);
                    a.click();
                    setTimeout(() => {
                        document.body.removeChild(a);
                        window.URL.revokeObjectURL(objectUrl);
                    }, 0);
                } catch (e) {
                    console.warn('Blob download failed, using direct link fallback:', e);
                    const a = document.createElement('a');
                    a.href = fileUrl;
                    a.download = fileName;
                    a.target = '_blank';
                    a.style.display = 'none';
                    document.body.appendChild(a);
                    a.click();
                    setTimeout(() => {
                        document.body.removeChild(a);
                    }, 0);
                }
            }
        } catch (err) {
            console.error('Auto-download failed:', err);
        }
    }
    
    scrollToBottom(smooth = true, force = false) {
        console.log('scrollToBottom called:', { 
            chatScrollArea: !!this.chatScrollArea, 
            autoScrollEnabled: this.autoScrollEnabled, 
            force: force,
            smooth: smooth 
        });
        
        if (this.chatScrollArea && (this.autoScrollEnabled || force)) {
            console.log('Scrolling to bottom:', {
                scrollHeight: this.chatScrollArea.scrollHeight,
                clientHeight: this.chatScrollArea.clientHeight,
                scrollTop: this.chatScrollArea.scrollTop
            });
            
            if (smooth) {
                // Smooth scroll to bottom
                this.chatScrollArea.scrollTo({
                    top: this.chatScrollArea.scrollHeight,
                    behavior: 'smooth'
                });
            } else {
                // Instant scroll to bottom
                this.chatScrollArea.scrollTop = this.chatScrollArea.scrollHeight;
            }
        } else {
            console.log('Scroll conditions not met:', {
                hasScrollArea: !!this.chatScrollArea,
                autoScrollEnabled: this.autoScrollEnabled,
                force: force
            });
        }
    }
    
    scrollToBottomDelayed() {
        // Use a small delay to ensure the DOM has been updated
        setTimeout(() => {
            this.scrollToBottom(true);
        }, 100);
    }
    
    setupScrollListener() {
        if (this.chatScrollArea) {
            let isUserScrolling = false;
            let scrollTimeout;
            
            this.chatScrollArea.addEventListener('scroll', () => {
                // Clear previous timeout
                clearTimeout(scrollTimeout);
                
                // Check if user is near the bottom (within 100px)
                const isNearBottom = this.chatScrollArea.scrollTop + this.chatScrollArea.clientHeight >= 
                                   this.chatScrollArea.scrollHeight - 100;
                
                if (isNearBottom) {
                    // User is near bottom, enable auto-scroll
                    this.autoScrollEnabled = true;
                } else {
                    // User scrolled up, disable auto-scroll temporarily
                    this.autoScrollEnabled = false;
                    
                    // Re-enable auto-scroll after 3 seconds of no scrolling
                    scrollTimeout = setTimeout(() => {
                        this.autoScrollEnabled = true;
                    }, 3000);
                }
            });
        }
    }
    
    addThinkingBubble() {
        const thinkingDiv = document.createElement('div');
        thinkingDiv.className = 'flex justify-start mb-6 thinking-bubble message-enter';
        thinkingDiv.innerHTML = `
            <div class="chat-bubble-bot rounded-2xl px-4 py-3" style="background: #E8D5F2 !important; color: #4A1A5C !important;">
                <div class="flex items-center space-x-3">
                    <div class="flex space-x-1">
                        <div class="w-2 h-2 bg-purple-400 rounded-full animate-bounce"></div>
                        <div class="w-2 h-2 bg-purple-400 rounded-full animate-bounce" style="animation-delay: 0.1s"></div>
                        <div class="w-2 h-2 bg-purple-400 rounded-full animate-bounce" style="animation-delay: 0.2s"></div>
                    </div>
                    <span class="text-sm font-medium" style="color: #111827;">Nasma is thinking...</span>
                </div>
            </div>
        `;
        
        this.chatContainer.appendChild(thinkingDiv);
        this.scrollToBottomDelayed();
        return thinkingDiv;
    }
    
    removeThinkingBubble(thinkingBubble) {
        if (thinkingBubble && thinkingBubble.parentNode) {
            thinkingBubble.parentNode.removeChild(thinkingBubble);
            // Scroll to bottom after removing thinking bubble
            this.scrollToBottomDelayed();
        }
    }
    
    async clearChat() {
        // Remove all messages except the welcome message
        const messages = this.chatContainer.children;
        for (let i = messages.length - 1; i > 0; i--) {
            messages[i].remove();
        }
        
        // Clear conversation history on backend if thread exists
        if (this.threadId) {
            try {
                await fetch('/api/chat/clear', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        thread_id: this.threadId
                    }),
                });
                console.log('Cleared conversation history on backend');
            } catch (error) {
                console.error('Error clearing conversation history:', error);
            }
        }
        
        // Reset thread ID and clear from localStorage
        this.threadId = null;
        localStorage.removeItem('chatbot_thread_id');
        console.log('Cleared thread ID from localStorage');
        
        // Scroll to top after clearing chat
        this.scrollToBottom(false, true); // Force instant scroll to top
        
        // Focus on input
        this.messageInput.focus();
    }
    
    async initializeEmployeeContext() {
        // Initialize employee context for the current thread
        if (!this.threadId) {
            console.log('No thread ID available for context initialization');
            return;
        }
        
        try {
            console.log('Initializing employee context for thread:', this.threadId);
            const response = await fetch('/api/chat/init-context', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    thread_id: this.threadId
                }),
            });
            
            const data = await response.json();
            
            if (data.status === 'success') {
                console.log(`Employee context initialized for: ${data.employee_name}`);
            } else {
                console.error('Failed to initialize employee context:', data.error);
            }
        } catch (error) {
            console.error('Error initializing employee context:', error);
        }
    }
    
    async logout() {
        try {
            // Generate device fingerprint to clear remember me token
            const deviceFingerprint = await this.generateDeviceFingerprint();

            const response = await fetch('/api/auth/logout', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    device_fingerprint: deviceFingerprint
                })
            });

            const data = await response.json();

            if (data.success) {
                // Clear remember me token from localStorage
                localStorage.removeItem('nasma_remember_me_token');

                // Set flag to prevent auto-login after logout
                sessionStorage.setItem('just_logged_out', 'true');

                // Redirect to login page
                window.location.href = '/login';
            } else {
                alert('Logout failed: ' + data.message);
            }
        } catch (error) {
            console.error('Logout error:', error);
            alert('Logout failed. Please try again.');
        }
    }

    async generateDeviceFingerprint() {
        const components = [];
        components.push(navigator.userAgent);
        // Note: Screen dimensions excluded for consistency across login.html and chat_smooth.html
        // Screen dimensions can change when using different monitors/displays
        components.push(Intl.DateTimeFormat().resolvedOptions().timeZone);
        components.push(navigator.language);
        components.push(navigator.platform);
        components.push(navigator.hardwareConcurrency || 'unknown');
        components.push(navigator.deviceMemory || 'unknown');

        try {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillText('Device fingerprint', 2, 2);
            components.push(canvas.toDataURL());
        } catch (e) {
            components.push('canvas-error');
        }

        const fingerprint = components.join('|');
        const encoder = new TextEncoder();
        const data = encoder.encode(fingerprint);
        const hashBuffer = await crypto.subtle.digest('SHA-256', data);
        const hashArray = Array.from(new Uint8Array(hashBuffer));
        const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
        return hashHex;
    }
    
    // Debug methods
    toggleDebugSidebar() {
        if (this.debugSidebar.style.display === 'none') {
            this.debugSidebar.style.display = 'flex';
            this.toggleDebugButton.textContent = 'Hide';
        } else {
            this.debugSidebar.style.display = 'none';
            this.toggleDebugButton.textContent = 'Show';
        }
    }
    
    async loadDebugInfo() {
        try {
            const response = await fetch('/api/debug/user-data');
            const data = await response.json();
            
            if (data.success) {
                this.updateDebugDisplay(data.debug_info);
            } else {
                this.updateDebugDisplay({ error: data.error });
            }
        } catch (error) {
            console.error('Error loading debug info:', error);
            this.updateDebugDisplay({ error: error.message });
        }
    }
    
    updateDebugDisplay(debugInfo) {
        // Session Status
        if (debugInfo.session_data) {
            this.sessionStatus.innerHTML = `
                <div class="text-green-600">✓ Authenticated: ${debugInfo.session_data.authenticated || false}</div>
                <div>Username: ${debugInfo.session_data.username || 'None'}</div>
                <div>User ID: ${debugInfo.session_data.user_id || 'None'}</div>
            `;
        } else {
            this.sessionStatus.innerHTML = '<div class="text-red-600">✗ No session data</div>';
        }
        
        // Odoo Connection
        if (debugInfo.odoo_authenticated) {
            this.odooStatus.innerHTML = `
                <div class="text-green-600">✓ Connected to Odoo</div>
                <div>User ID: ${debugInfo.odoo_user_info?.user_id || 'Unknown'}</div>
                <div>Database: ${debugInfo.odoo_user_info?.database || 'Unknown'}</div>
            `;
        } else {
            this.odooStatus.innerHTML = '<div class="text-red-600">✗ Not connected to Odoo</div>';
        }
        
        // Employee Data
        if (debugInfo.employee_data_success && debugInfo.employee_data) {
            const emp = debugInfo.employee_data;
            this.employeeData.innerHTML = `
                <div class="text-green-600">✓ Employee data loaded</div>
                <div>Name: ${emp.name || 'N/A'}</div>
                <div>Job Title: ${emp.job_title || 'N/A'}</div>
                <div>Email: ${emp.work_email || 'N/A'}</div>
                <div>Department: ${emp.department_id_details?.name || 'N/A'}</div>
                <div>Manager: ${emp.parent_id_details?.name || 'N/A'}</div>
            `;
        } else {
            this.employeeData.innerHTML = `
                <div class="text-red-600">✗ Employee data failed</div>
                <div>Error: ${debugInfo.employee_data_error || 'Unknown error'}</div>
            `;
        }
        
        // Raw Odoo Response
        if (debugInfo.raw_odoo_response) {
            const raw = debugInfo.raw_odoo_response;
            if (raw.error) {
                this.rawOdooResponse.innerHTML = `
                    <div class="text-red-600">✗ Error: ${raw.error}</div>
                `;
            } else {
                this.rawOdooResponse.innerHTML = `
                    <div class="text-blue-600">Status: ${raw.status_code}</div>
                    <div class="mt-1">${raw.response_text}</div>
                `;
            }
        } else {
            this.rawOdooResponse.innerHTML = '<div class="text-gray-500">No raw response data</div>';
        }
        
        // Chat Context
        if (this.lastChatContext) {
            this.chatContext.innerHTML = `
                <div class="text-blue-600">Last context sent to ChatGPT:</div>
                <div class="mt-1">${this.lastChatContext.substring(0, 200)}...</div>
            `;
        } else {
            this.chatContext.innerHTML = '<div class="text-gray-500">No chat context yet</div>';
        }
    }
    
    updateChatContext(context) {
        this.lastChatContext = context;
        if (this.chatContext) {
            this.chatContext.innerHTML = `
                <div class="text-blue-600">Last context sent to ChatGPT:</div>
                <div class="mt-1">${context.substring(0, 200)}...</div>
            `;
        }
    }
    
    // Debug function to test scrolling manually
    testScroll() {
        console.log('Testing scroll manually...');
        this.scrollToBottom(false, true);
    }
}

// Global functions for HTML event handlers
function sendMessage() {
    chatBot.sendMessage();
}

function handleKeyPress(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

// Initialize chat when page loads
let chatBot;
document.addEventListener('DOMContentLoaded', function() {
    chatBot = new ChatBot();
    // Make chatBot globally accessible for debugging
    window.chatBot = chatBot;
});
