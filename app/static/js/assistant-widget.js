(function() {
    'use strict';

    // Clés de persistance
    const STORAGE_KEY_MESSAGES = 'klasora_ai_chat_history_v1';
    const STORAGE_KEY_STATE = 'klasora_ai_widget_open_v1';
    const STORAGE_KEY_BTN_POS = 'klasora_speed_dial_pos_v1';
    const STORAGE_KEY_WIN_POS = 'klasora_ai_win_pos_v1';
    const STORAGE_KEY_EXPANDED = 'klasora_ai_win_expanded_v1';
    const assistantConfigRoot = document.getElementById('assistant-widget-container');
    const STREAM_ENDPOINT = (assistantConfigRoot && assistantConfigRoot.dataset.streamEndpoint) || '/api/assistant/stream';

    // Éléments du DOM Speed Dial & Chat
    const speedDial = document.getElementById('klasoraSpeedDial');
    const triggerBtn = document.getElementById('klasoraSpeedDialTrigger');
    const speedDialMenu = document.getElementById('klasoraSpeedDialMenu');
    const speedDialAiBtn = document.getElementById('speedDialAiBtn');
    const speedDialSupportBtn = document.getElementById('speedDialSupportBtn');
    const chatWindow = document.getElementById('klasoraAiChatWindow');
    const backdrop = document.getElementById('klasoraAiBackdrop');
    const header = document.getElementById('klasoraAiHeader');
    const dragZone = document.getElementById('klasoraAiDragZone');
    const mobileHandle = document.getElementById('klasoraAiMobileHandle');
    const closeBtn = document.getElementById('klasoraAiCloseBtn');
    const clearBtn = document.getElementById('klasoraAiClearBtn');
    const expandBtn = document.getElementById('klasoraAiExpandBtn');
    const resetPosBtn = document.getElementById('klasoraAiResetPosBtn');
    const bodyContainer = document.getElementById('klasoraAiBody');
    const messagesList = document.getElementById('klasoraAiMessages');
    const typingIndicator = document.getElementById('klasoraAiTyping');
    const inputArea = document.getElementById('klasoraAiInput');
    const sendBtn = document.getElementById('klasoraAiSendBtn');
    const pillButtons = document.querySelectorAll('.klasora-ai-pill');

    if (!speedDial || !triggerBtn || !chatWindow || !inputArea || !sendBtn) {
        return;
    }

    let isRequestInProgress = false;
    const mobileWidgetQuery = window.matchMedia('(max-width: 991.98px)');
    const mobileWidgetHost = document.querySelector('.navbar-mobile-actions');
    const desktopWidgetAnchor = document.createComment('assistant desktop position');
    speedDial.before(desktopWidgetAnchor);
    speedDial.classList.add('is-attention');
    const attentionTimer = window.setTimeout(() => {
        speedDial.classList.remove('is-attention');
    }, 30000);

    function stopAssistantAttention() {
        speedDial.classList.remove('is-attention');
        window.clearTimeout(attentionTimer);
    }

    // =========================================================================
    // 1. GESTION DU SPEED DIAL EXTENSIBLE & DU DÉPLACEMENT FLUIDE (DRAGGABLE)
    // =========================================================================
    let isBtnPointerDown = false;
    let isBtnDragging = false;
    let didBtnMove = false;
    let btnStartX = 0, btnStartY = 0;
    let btnInitialLeft = 0, btnInitialTop = 0;
    let suppressBtnClickUntil = 0;
    const DRAG_THRESHOLD = 7; // Distance minimale en px pour déclencher le glissement

    // Restaurer la position sauvegardée du Speed Dial (avec rétrocompatibilité)
    try {
        const savedBtnPos = localStorage.getItem(STORAGE_KEY_BTN_POS) || localStorage.getItem('klasora_ai_btn_pos_v1');
        if (savedBtnPos) {
            const pos = JSON.parse(savedBtnPos);
            if (typeof pos.left === 'number' && typeof pos.top === 'number') {
                const w = speedDial.offsetWidth || 150;
                const h = speedDial.offsetHeight || 50;
                const maxLeft = window.innerWidth - w - 10;
                const maxTop = window.innerHeight - h - 10;
                const left = Math.min(Math.max(10, pos.left), Math.max(10, maxLeft));
                const top = Math.min(Math.max(10, pos.top), Math.max(10, maxTop));
                speedDial.style.left = left + 'px';
                speedDial.style.top = top + 'px';
                speedDial.style.right = 'auto';
                speedDial.style.bottom = 'auto';
                speedDial.classList.add('is-repositioned');
                if (top < 220) {
                    speedDial.classList.add('expand-down');
                } else {
                    speedDial.classList.remove('expand-down');
                }
            }
        }
    } catch (e) {}

    function onBtnPointerDown(e) {
        if (mobileWidgetQuery.matches) return;
        if (e.button !== undefined && e.button !== 0) return;
        isBtnPointerDown = true;
        isBtnDragging = false;
        didBtnMove = false;

        const coords = (e.touches && e.touches.length > 0) ? e.touches[0] : e;
        btnStartX = coords.clientX;
        btnStartY = coords.clientY;

        const rect = speedDial.getBoundingClientRect();
        btnInitialLeft = rect.left;
        btnInitialTop = rect.top;

        window.addEventListener('pointermove', onBtnPointerMove, { passive: false });
        window.addEventListener('mousemove', onBtnPointerMove);
        window.addEventListener('touchmove', onBtnPointerMove, { passive: false });

        window.addEventListener('pointerup', onBtnPointerUp);
        window.addEventListener('mouseup', onBtnPointerUp);
        window.addEventListener('touchend', onBtnPointerUp);
    }

    function onBtnPointerMove(e) {
        if (!isBtnPointerDown) return;
        const coords = (e.touches && e.touches.length > 0) ? e.touches[0] : e;
        const dx = coords.clientX - btnStartX;
        const dy = coords.clientY - btnStartY;
        const dist = Math.hypot(dx, dy);

        if (!isBtnDragging && dist > DRAG_THRESHOLD) {
            isBtnDragging = true;
            didBtnMove = true;
            speedDial.classList.add('is-dragging');
        }

        if (isBtnDragging) {
            if (e.cancelable) e.preventDefault();
            const w = speedDial.offsetWidth || 150;
            const h = speedDial.offsetHeight || 50;
            const minX = 8;
            const minY = 8;
            const maxX = window.innerWidth - w - 8;
            const maxY = window.innerHeight - h - 8;

            let newLeft = Math.min(Math.max(minX, btnInitialLeft + dx), maxX);
            let newTop = Math.min(Math.max(minY, btnInitialTop + dy), maxY);

            speedDial.style.left = newLeft + 'px';
            speedDial.style.top = newTop + 'px';
            speedDial.style.right = 'auto';
            speedDial.style.bottom = 'auto';
            speedDial.classList.add('is-repositioned');

            if (newTop < 220) {
                speedDial.classList.add('expand-down');
            } else {
                speedDial.classList.remove('expand-down');
            }
        }
    }

    function onBtnPointerUp() {
        if (!isBtnPointerDown) return;
        isBtnPointerDown = false;

        window.removeEventListener('pointermove', onBtnPointerMove);
        window.removeEventListener('mousemove', onBtnPointerMove);
        window.removeEventListener('touchmove', onBtnPointerMove);
        window.removeEventListener('pointerup', onBtnPointerUp);
        window.removeEventListener('mouseup', onBtnPointerUp);
        window.removeEventListener('touchend', onBtnPointerUp);

        if (isBtnDragging) {
            isBtnDragging = false;
            speedDial.classList.remove('is-dragging');
            suppressBtnClickUntil = Date.now() + 300;

            const rect = speedDial.getBoundingClientRect();
            try {
                localStorage.setItem(STORAGE_KEY_BTN_POS, JSON.stringify({
                    left: Math.round(rect.left),
                    top: Math.round(rect.top)
                }));
            } catch (e) {}
        }
    }

    triggerBtn.addEventListener('pointerdown', onBtnPointerDown);
    triggerBtn.addEventListener('mousedown', onBtnPointerDown);
    triggerBtn.addEventListener('touchstart', onBtnPointerDown, { passive: true });

    // Menu Speed Dial Actions & Toggle
    function openSpeedDial() {
        const rect = speedDial.getBoundingClientRect();
        if (rect.top < 220) {
            speedDial.classList.add('expand-down');
        } else {
            speedDial.classList.remove('expand-down');
        }
        speedDial.classList.add('is-open');
        triggerBtn.setAttribute('aria-expanded', 'true');
        if (speedDialMenu) speedDialMenu.setAttribute('aria-hidden', 'false');
    }

    function closeSpeedDial() {
        speedDial.classList.remove('is-open');
        triggerBtn.setAttribute('aria-expanded', 'false');
        if (speedDialMenu) speedDialMenu.setAttribute('aria-hidden', 'true');
    }

    function syncSpeedDialPlacement() {
        const docked = mobileWidgetQuery.matches && mobileWidgetHost;
        if (Boolean(docked) === speedDial.classList.contains('is-mobile-docked')) return;
        onBtnPointerUp();
        didBtnMove = false;
        suppressBtnClickUntil = 0;
        closeSpeedDial();
        speedDial.classList.toggle('is-mobile-docked', Boolean(docked));
        if (docked) {
            mobileWidgetHost.prepend(speedDial);
        } else {
            desktopWidgetAnchor.after(speedDial);
        }
    }

    syncSpeedDialPlacement();

    function toggleSpeedDial() {
        stopAssistantAttention();
        if (speedDial.classList.contains('is-open')) {
            closeSpeedDial();
        } else {
            openSpeedDial();
        }
    }

    triggerBtn.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        if (didBtnMove || Date.now() < suppressBtnClickUntil) {
            didBtnMove = false;
            return;
        }
        toggleSpeedDial();
    });

    triggerBtn.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleSpeedDial();
        }
    });

    // Support Technique pour les admins d'école
    if (speedDialSupportBtn) {
        speedDialSupportBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            closeSpeedDial();
            if (!chatWindow.classList.contains('klasora-ai-hidden')) {
                toggleChat(false);
            }
            if (typeof window.klasoraOpenSupportModal === 'function') {
                window.klasoraOpenSupportModal();
            } else {
                const supportTrigger = document.getElementById('klasoraSupportTriggerBtn');
                if (supportTrigger) supportTrigger.click();
            }
        });
    }

    // Assistant IA de Direction
    if (speedDialAiBtn) {
        speedDialAiBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            closeSpeedDial();
            toggleChat(true);
        });
    }

    // Clic en dehors pour replier le Speed Dial
    document.addEventListener('click', function(e) {
        if (speedDial.classList.contains('is-open') && !speedDial.contains(e.target)) {
            closeSpeedDial();
        }
    });

    // =========================================================================
    // 2. GESTION DU DÉPLACEMENT DE LA FENÊTRE SUR PC (DRAGGABLE CHAT WINDOW)
    // =========================================================================
    let isWinPointerDown = false;
    let isWinDragging = false;
    let winStartX = 0, winStartY = 0;
    let winInitialLeft = 0, winInitialTop = 0;

    // Restaurer la position sauvegardée de la fenêtre (PC uniquement)
    function restoreWindowPosition() {
        if (window.innerWidth <= 576) return;
        try {
            const savedWinPos = localStorage.getItem(STORAGE_KEY_WIN_POS);
            if (savedWinPos) {
                const pos = JSON.parse(savedWinPos);
                if (typeof pos.left === 'number' && typeof pos.top === 'number') {
                    const w = chatWindow.offsetWidth || 420;
                    const h = chatWindow.offsetHeight || 600;
                    const maxLeft = window.innerWidth - w - 12;
                    const maxTop = window.innerHeight - h - 12;
                    const left = Math.min(Math.max(12, pos.left), Math.max(12, maxLeft));
                    const top = Math.min(Math.max(12, pos.top), Math.max(12, maxTop));
                    chatWindow.style.left = left + 'px';
                    chatWindow.style.top = top + 'px';
                    chatWindow.style.right = 'auto';
                    chatWindow.style.bottom = 'auto';
                }
            }
        } catch (e) {}
    }

    function onWinPointerDown(e) {
        if (window.innerWidth <= 576) return;
        if (e.button !== undefined && e.button !== 0) return;
        if (e.target.closest('.klasora-ai-header-btn')) return;

        isWinPointerDown = true;
        isWinDragging = false;

        const coords = (e.touches && e.touches.length > 0) ? e.touches[0] : e;
        winStartX = coords.clientX;
        winStartY = coords.clientY;

        const rect = chatWindow.getBoundingClientRect();
        winInitialLeft = rect.left;
        winInitialTop = rect.top;

        window.addEventListener('pointermove', onWinPointerMove, { passive: false });
        window.addEventListener('mousemove', onWinPointerMove);
        window.addEventListener('pointerup', onWinPointerUp);
        window.addEventListener('mouseup', onWinPointerUp);
    }

    function onWinPointerMove(e) {
        if (!isWinPointerDown) return;
        const coords = (e.touches && e.touches.length > 0) ? e.touches[0] : e;
        const dx = coords.clientX - winStartX;
        const dy = coords.clientY - winStartY;

        if (!isWinDragging && Math.hypot(dx, dy) > 6) {
            isWinDragging = true;
            chatWindow.classList.add('is-dragging-window');
        }

        if (isWinDragging) {
            if (e.cancelable) e.preventDefault();
            const w = chatWindow.offsetWidth;
            const h = chatWindow.offsetHeight;
            const minX = 10;
            const minY = 10;
            const maxX = window.innerWidth - w - 10;
            const maxY = window.innerHeight - h - 10;

            let newLeft = Math.min(Math.max(minX, winInitialLeft + dx), maxX);
            let newTop = Math.min(Math.max(minY, winInitialTop + dy), maxY);

            chatWindow.style.left = newLeft + 'px';
            chatWindow.style.top = newTop + 'px';
            chatWindow.style.right = 'auto';
            chatWindow.style.bottom = 'auto';
        }
    }

    function onWinPointerUp() {
        if (!isWinPointerDown) return;
        isWinPointerDown = false;
        window.removeEventListener('pointermove', onWinPointerMove);
        window.removeEventListener('mousemove', onWinPointerMove);
        window.removeEventListener('pointerup', onWinPointerUp);
        window.removeEventListener('mouseup', onWinPointerUp);

        if (isWinDragging) {
            isWinDragging = false;
            chatWindow.classList.remove('is-dragging-window');
            const rect = chatWindow.getBoundingClientRect();
            try {
                localStorage.setItem(STORAGE_KEY_WIN_POS, JSON.stringify({
                    left: Math.round(rect.left),
                    top: Math.round(rect.top)
                }));
            } catch (e) {}
        }
    }

    if (dragZone) {
        dragZone.addEventListener('pointerdown', onWinPointerDown);
        dragZone.addEventListener('mousedown', onWinPointerDown);
    }

    // Réinitialiser la position de la fenêtre (bouton recentrer)
    if (resetPosBtn) {
        resetPosBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            localStorage.removeItem(STORAGE_KEY_WIN_POS);
            chatWindow.style.left = '';
            chatWindow.style.top = '';
            chatWindow.style.right = '24px';
            chatWindow.style.bottom = '86px';
        });
    }

    // Agrandir / Réduire la largeur sur PC
    if (expandBtn) {
        try {
            if (localStorage.getItem(STORAGE_KEY_EXPANDED) === '1') {
                chatWindow.classList.add('is-expanded');
                expandBtn.innerHTML = '<i class="fas fa-compress-alt"></i>';
            }
        } catch (e) {}

        expandBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isExp = chatWindow.classList.toggle('is-expanded');
            expandBtn.innerHTML = isExp ? '<i class="fas fa-compress-alt"></i>' : '<i class="fas fa-expand-alt"></i>';
            try {
                localStorage.setItem(STORAGE_KEY_EXPANDED, isExp ? '1' : '0');
            } catch (err) {}
            // Si la fenêtre sort de l'écran après agrandissement, on réajuste
            setTimeout(() => {
                const rect = chatWindow.getBoundingClientRect();
                if (rect.right > window.innerWidth - 10) {
                    const left = Math.max(10, window.innerWidth - chatWindow.offsetWidth - 12);
                    chatWindow.style.left = left + 'px';
                }
            }, 60);
        });
    }

    // =========================================================================
    // 3. GLISSER VERS LE BAS POUR FERMER SUR MOBILE (SWIPE DOWN TO CLOSE)
    // =========================================================================
    let touchStartY = 0;
    let touchMoveY = 0;

    function onMobileTouchStart(e) {
        if (window.innerWidth > 576) return;
        touchStartY = e.touches[0].clientY;
        touchMoveY = touchStartY;
    }

    function onMobileTouchMove(e) {
        if (window.innerWidth > 576) return;
        touchMoveY = e.touches[0].clientY;
        const diffY = touchMoveY - touchStartY;
        if (diffY > 0) {
            chatWindow.style.transform = `translateY(${diffY}px)`;
        }
    }

    function onMobileTouchEnd() {
        if (window.innerWidth > 576) return;
        const diffY = touchMoveY - touchStartY;
        chatWindow.style.transform = '';
        if (diffY > 80) {
            toggleChat(false);
        }
    }

    if (mobileHandle) {
        mobileHandle.addEventListener('touchstart', onMobileTouchStart, { passive: true });
        mobileHandle.addEventListener('touchmove', onMobileTouchMove, { passive: true });
        mobileHandle.addEventListener('touchend', onMobileTouchEnd);
    }

    // =========================================================================
    // 4. OUVERTURE / FERMETURE & GESTION DU BACKDROP
    // =========================================================================
    function toggleChat(forceOpen) {
        stopAssistantAttention();
        const isOpen = !chatWindow.classList.contains('klasora-ai-hidden');
        const nextState = forceOpen !== undefined ? forceOpen : !isOpen;

        if (nextState) {
            restoreWindowPosition();
            chatWindow.classList.remove('klasora-ai-hidden');
            triggerBtn.classList.add('is-chat-active');
            if (backdrop && window.innerWidth <= 576) {
                backdrop.classList.remove('klasora-ai-hidden');
            }
            sessionStorage.setItem(STORAGE_KEY_STATE, '1');
            setTimeout(() => {
                inputArea.focus();
                scrollToBottom();
            }, 100);
        } else {
            chatWindow.classList.add('klasora-ai-hidden');
            triggerBtn.classList.remove('is-chat-active');
            if (backdrop) {
                backdrop.classList.add('klasora-ai-hidden');
            }
            sessionStorage.setItem(STORAGE_KEY_STATE, '0');
        }
    }

    closeBtn.addEventListener('click', () => toggleChat(false));
    if (backdrop) {
        backdrop.addEventListener('click', () => toggleChat(false));
    }

    // Exposition globale pour déclencher l'assistant depuis n'importe quel bouton
    window.openKlasoraAssistant = function() {
        toggleChat(true);
    };

    // Fermeture avec la touche Échap
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (speedDial.classList.contains('is-open')) {
                closeSpeedDial();
            } else if (!chatWindow.classList.contains('klasora-ai-hidden')) {
                toggleChat(false);
            }
        }
    });

    // =========================================================================
    // 5. GESTION DES MESSAGES, RENDU MARKDOWN ET ENVOI
    // =========================================================================
    function scrollToBottom() {
        if (bodyContainer) {
            bodyContainer.scrollTop = bodyContainer.scrollHeight;
        }
    }

    function getFormattedTime() {
        const now = new Date();
        return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function escapeHtml(text) {
        if (!text) return '';
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
        return text.toString().replace(/[&<>"']/g, (m) => map[m]);
    }

    function renderMarkdown(rawText) {
        if (!rawText) return '';
        let safe = escapeHtml(rawText);

        safe = safe.replace(/```([\s\S]*?)```/g, (match, code) => `<pre><code>${code.trim()}</code></pre>`);
        safe = safe.replace(/`([^`]+)`/g, '<code>$1</code>');
        safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        safe = safe.replace(/\*([^*]+)\*/g, '<em>$1</em>');

        // Liens Markdown convertis en boutons interactifs KLASORA
        safe = safe.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, label, url) => {
            const cleanUrl = url.trim();
            if (cleanUrl.startsWith('prompt:')) {
                const promptVal = cleanUrl.slice(7).replace(/"/g, '&quot;');
                return `<button type="button" class="klasora-ai-card-btn klasora-ai-choice-btn" data-prompt="${promptVal}"><i class="fas fa-check-circle me-1" aria-hidden="true"></i> ${label}</button>`;
            }
            const isExternal = cleanUrl.startsWith('http://') || cleanUrl.startsWith('https://');
            const targetAttr = isExternal ? 'target="_blank" rel="noopener"' : '';
            return `<a href="${cleanUrl}" class="klasora-ai-card-btn" ${targetAttr}><i class="fas fa-folder-open me-1" aria-hidden="true"></i> ${label}</a>`;
        });

        const lines = safe.split('\n');
        let inList = false;
        let processedLines = [];

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const bulletMatch = line.match(/^(\s*)[-*]\s+(.+)$/);
            const numberedMatch = line.match(/^(\s*)\d+\.\s+(.+)$/);

            if (bulletMatch) {
                if (!inList) {
                    processedLines.push('<ul class="klasora-ai-list">');
                    inList = 'ul';
                }
                processedLines.push(`<li>${bulletMatch[2]}</li>`);
            } else if (numberedMatch) {
                if (!inList) {
                    processedLines.push('<ol class="klasora-ai-list">');
                    inList = 'ol';
                }
                processedLines.push(`<li>${numberedMatch[2]}</li>`);
            } else {
                if (inList) {
                    processedLines.push(inList === 'ul' ? '</ul>' : '</ol>');
                    inList = false;
                }
                processedLines.push(line);
            }
        }
        if (inList) {
            processedLines.push(inList === 'ul' ? '</ul>' : '</ol>');
        }

        let result = processedLines.join('\n');
        result = result.replace(/([^\n>])\n([^\n<])/g, '$1<br>$2');
        return result;
    }

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content');
        const input = document.querySelector('input[name="csrf_token"]');
        return input ? input.value : '';
    }

    function loadSavedMessages() {
        try {
            const saved = sessionStorage.getItem(STORAGE_KEY_MESSAGES);
            if (!saved) return;
            const list = JSON.parse(saved);
            if (Array.isArray(list) && list.length > 0) {
                messagesList.innerHTML = '';
                list.forEach((item) => appendMessageDom(item.role, item.text, item.time, false));
                scrollToBottom();
            }
        } catch (e) {
            console.warn('[KLASORA AI] Erreur lecture historique:', e);
        }
    }

    function saveMessageToHistory(role, text, time) {
        try {
            let list = [];
            const saved = sessionStorage.getItem(STORAGE_KEY_MESSAGES);
            if (saved) list = JSON.parse(saved);
            list.push({ role, text, time });
            sessionStorage.setItem(STORAGE_KEY_MESSAGES, JSON.stringify(list));
        } catch (e) {}
    }

    function appendMessageDom(role, text, time, shouldSave = true) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `klasora-ai-msg ${role === 'user' ? 'klasora-ai-msg-user' : 'klasora-ai-msg-bot'}`;

        const bubbleWrap = document.createElement('div');
        bubbleWrap.className = 'klasora-ai-bubble-wrap';

        const bubble = document.createElement('div');
        bubble.className = 'klasora-ai-bubble';

        if (role === 'user') {
            bubble.textContent = text;
        } else {
            bubble.innerHTML = renderMarkdown(text);
        }

        const metaDiv = document.createElement('div');
        metaDiv.className = 'klasora-ai-msg-meta';

        const timeSpan = document.createElement('span');
        timeSpan.className = 'klasora-ai-msg-time';
        timeSpan.textContent = time || getFormattedTime();
        metaDiv.appendChild(timeSpan);

        if (role === 'bot') {
            attachCopyButton(metaDiv, text);
        }

        bubbleWrap.appendChild(bubble);
        bubbleWrap.appendChild(metaDiv);

        if (role === 'bot') {
            const avatar = document.createElement('div');
            avatar.className = 'klasora-ai-msg-avatar';
            avatar.innerHTML = '<i class="fas fa-robot"></i>';
            msgDiv.appendChild(avatar);
        }

        msgDiv.appendChild(bubbleWrap);
        messagesList.appendChild(msgDiv);

        if (shouldSave) {
            saveMessageToHistory(role, text, timeSpan.textContent);
        }
        scrollToBottom();
    }

    function appendRetryMessage(questionText) {
        const msgDiv = document.createElement('div');
        msgDiv.className = 'klasora-ai-msg klasora-ai-msg-bot';

        const avatar = document.createElement('div');
        avatar.className = 'klasora-ai-msg-avatar';
        avatar.innerHTML = '<i class="fas fa-robot"></i>';

        const bubbleWrap = document.createElement('div');
        bubbleWrap.className = 'klasora-ai-bubble-wrap';

        const bubble = document.createElement('div');
        bubble.className = 'klasora-ai-bubble';
        bubble.innerHTML = renderMarkdown("**Service momentanément indisponible.** La réponse a pris trop de temps ou la connexion a été interrompue.");

        const retryBtn = document.createElement('button');
        retryBtn.type = 'button';
        retryBtn.className = 'klasora-ai-copy-btn';
        retryBtn.innerHTML = '<i class="fas fa-redo"></i> Réessayer';
        retryBtn.addEventListener('click', () => sendMessage(questionText));
        bubble.appendChild(document.createElement('br'));
        bubble.appendChild(retryBtn);

        const metaDiv = document.createElement('div');
        metaDiv.className = 'klasora-ai-msg-meta';

        const timeSpan = document.createElement('span');
        timeSpan.className = 'klasora-ai-msg-time';
        timeSpan.textContent = getFormattedTime();
        metaDiv.appendChild(timeSpan);

        bubbleWrap.appendChild(bubble);
        bubbleWrap.appendChild(metaDiv);
        msgDiv.appendChild(avatar);
        msgDiv.appendChild(bubbleWrap);
        messagesList.appendChild(msgDiv);
        scrollToBottom();
    }

    function attachCopyButton(metaDiv, text) {
        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'klasora-ai-copy-btn';
        copyBtn.innerHTML = '<i class="far fa-copy"></i> Copier';
        copyBtn.title = 'Copier ce texte';

        copyBtn.addEventListener('click', () => {
            const plainText = typeof text === 'function' ? text() : text;
            const setCopied = () => {
                copyBtn.classList.add('is-copied');
                copyBtn.innerHTML = '<i class="fas fa-check"></i> Copié !';
                setTimeout(() => {
                    copyBtn.classList.remove('is-copied');
                    copyBtn.innerHTML = '<i class="far fa-copy"></i> Copier';
                }, 2000);
            };

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(plainText).then(setCopied);
            } else {
                const ta = document.createElement('textarea');
                ta.value = plainText;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                setCopied();
            }
        });
        metaDiv.appendChild(copyBtn);
    }

    function createLiveBotMessageDom(timeStr) {
        const msgDiv = document.createElement('div');
        msgDiv.className = 'klasora-ai-msg klasora-ai-msg-bot';

        const bubbleWrap = document.createElement('div');
        bubbleWrap.className = 'klasora-ai-bubble-wrap';

        const bubble = document.createElement('div');
        bubble.className = 'klasora-ai-bubble';

        const metaDiv = document.createElement('div');
        metaDiv.className = 'klasora-ai-msg-meta';

        const timeSpan = document.createElement('span');
        timeSpan.className = 'klasora-ai-msg-time';
        timeSpan.textContent = timeStr || getFormattedTime();
        metaDiv.appendChild(timeSpan);

        bubbleWrap.appendChild(bubble);
        bubbleWrap.appendChild(metaDiv);

        const avatar = document.createElement('div');
        avatar.className = 'klasora-ai-msg-avatar';
        avatar.innerHTML = '<i class="fas fa-robot"></i>';
        msgDiv.appendChild(avatar);

        msgDiv.appendChild(bubbleWrap);
        messagesList.appendChild(msgDiv);
        scrollToBottom();

        return {
            bubble: bubble,
            metaDiv: metaDiv,
            timeSpan: timeSpan,
            msgDiv: msgDiv
        };
    }

    function autoResizeInput() {
        inputArea.style.height = 'auto';
        inputArea.style.height = Math.min(inputArea.scrollHeight, 110) + 'px';
    }

    inputArea.addEventListener('input', autoResizeInput);

    let thinkingInterval = null;
    const THINKING_STEPS = [
        "🔍 Analyse de votre demande...",
        "📊 Consultation des données scolaires...",
        "🧠 Synthèse et calcul des informations...",
        "✨ Rédaction de la réponse..."
    ];

    function startDynamicThinkingSteps() {
        stopDynamicThinkingSteps();
        const labelEl = document.getElementById('klasoraAiTypingLabel');
        if (!labelEl) return;

        let stepIdx = 0;
        labelEl.textContent = THINKING_STEPS[0];
        labelEl.style.opacity = '1';

        thinkingInterval = setInterval(() => {
            stepIdx++;
            if (stepIdx < THINKING_STEPS.length) {
                labelEl.style.opacity = '0';
                setTimeout(() => {
                    labelEl.textContent = THINKING_STEPS[stepIdx];
                    labelEl.style.opacity = '1';
                }, 150);
            }
        }, 1800);
    }

    function stopDynamicThinkingSteps() {
        if (thinkingInterval) {
            clearInterval(thinkingInterval);
            thinkingInterval = null;
        }
    }

    async function sendMessage(customPrompt) {
        const questionText = (customPrompt || inputArea.value || '').trim();
        if (!questionText || isRequestInProgress) return;

        // Extraire l'historique court (2 derniers échanges = 4 messages max) avant d'ajouter le nouveau
        let historyPayload = [];
        try {
            const saved = sessionStorage.getItem(STORAGE_KEY_MESSAGES);
            if (saved) {
                const list = JSON.parse(saved);
                if (Array.isArray(list) && list.length > 0) {
                    const recent = list.slice(-4);
                    historyPayload = recent.map(item => ({
                        role: item.role === 'user' ? 'user' : 'assistant',
                        content: item.text || ''
                    }));
                }
            }
        } catch (e) {}

        inputArea.value = '';
        inputArea.style.height = 'auto';
        sendBtn.disabled = true;
        isRequestInProgress = true;

        appendMessageDom('user', questionText, getFormattedTime(), true);
        startDynamicThinkingSteps();
        typingIndicator.classList.remove('klasora-ai-hidden');
        scrollToBottom();

        let liveBot = null;
        let accumulatedReply = '';

        try {
            const csrfToken = getCsrfToken();
            const response = await fetch(STREAM_ENDPOINT, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                    'X-CSRF-Token': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({
                    question: questionText,
                    history: historyPayload
                })
            });

            if (response.ok && response.body && window.ReadableStream) {
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });

                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // Reste de ligne

                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (trimmed.startsWith('data: ')) {
                            const jsonStr = trimmed.slice(6).trim();
                            if (!jsonStr) continue;
                            try {
                                const parsed = JSON.parse(jsonStr);
                                if (parsed.chunk) {
                                    if (!liveBot) {
                                        stopDynamicThinkingSteps();
                                        typingIndicator.classList.add('klasora-ai-hidden');
                                        liveBot = createLiveBotMessageDom(getFormattedTime());
                                    }
                                    accumulatedReply += parsed.chunk;
                                    liveBot.bubble.innerHTML = renderMarkdown(accumulatedReply);
                                    scrollToBottom();
                                }
                                if (parsed.done) {
                                    break;
                                }
                            } catch (e) {}
                        }
                    }
                }

                if (accumulatedReply && liveBot) {
                    attachCopyButton(liveBot.metaDiv, accumulatedReply);
                    saveMessageToHistory('bot', accumulatedReply, liveBot.timeSpan.textContent);
                } else if (!accumulatedReply) {
                    throw new Error("Flux vide");
                }
            } else {
                throw new Error("Flux SSE non disponible");
            }
        } catch (err) {
            console.warn('[KLASORA AI] Stream interrompu, aucun fallback lourd relanc\u00e9 :', err);
            appendRetryMessage(questionText);
        } finally {
            stopDynamicThinkingSteps();
            typingIndicator.classList.add('klasora-ai-hidden');
            sendBtn.disabled = false;
            isRequestInProgress = false;
            inputArea.focus();
            scrollToBottom();
        }
    }

    sendBtn.addEventListener('click', (e) => {
        e.preventDefault();
        sendMessage();
    });

    inputArea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    pillButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
            const prompt = btn.dataset.prompt;
            if (prompt) {
                inputArea.value = prompt;
                autoResizeInput();
                inputArea.focus();
            }
        });
    });

    // Clic interactif sur les boutons de choix d'homonymes ou suggestions dans les messages
    messagesList.addEventListener('click', (e) => {
        const choiceBtn = e.target.closest('.klasora-ai-choice-btn');
        if (choiceBtn && choiceBtn.dataset.prompt) {
            e.preventDefault();
            sendMessage(choiceBtn.dataset.prompt);
        }
    });

    clearBtn.addEventListener('click', async () => {
        if (confirm("Voulez-vous effacer l'historique de cette conversation ?")) {
            sessionStorage.removeItem(STORAGE_KEY_MESSAGES);
            try {
                const csrfToken = getCsrfToken();
                await fetch('/api/assistant/clear-session', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                        'X-CSRF-Token': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
            } catch (e) {}
            messagesList.innerHTML = `
                <div class="klasora-ai-msg klasora-ai-msg-bot">
                    <div class="klasora-ai-msg-avatar">
                        <i class="fas fa-robot"></i>
                    </div>
                    <div class="klasora-ai-bubble-wrap">
                        <div class="klasora-ai-bubble">
                            <p class="mb-1"><strong>Historique réinitialisé.</strong></p>
                            <p class="mb-0 small text-muted">Posez votre nouvelle question ou sélectionnez une suggestion pour démarrer.</p>
                        </div>
                        <div class="klasora-ai-msg-meta">
                            <span class="klasora-ai-msg-time">${getFormattedTime()}</span>
                        </div>
                    </div>
                </div>
            `;
            scrollToBottom();
        }
    });

    // =========================================================================
    // 6. INITIALISATION & REPOSITIONNEMENT RESPONSIVE
    // =========================================================================
    loadSavedMessages();

    // Rétablir l'état ouvert si sauvegardé
    try {
        if (sessionStorage.getItem(STORAGE_KEY_STATE) === '1') {
            toggleChat(true);
        }
    } catch (e) {}

    // Ajustement de sécurité au redimensionnement de l'écran
    window.addEventListener('resize', function() {
        syncSpeedDialPlacement();
        if (!mobileWidgetQuery.matches && speedDial.classList.contains('is-repositioned')) {
            const rect = speedDial.getBoundingClientRect();
            const w = speedDial.offsetWidth || 150;
            const h = speedDial.offsetHeight || 50;
            const maxLeft = window.innerWidth - w - 8;
            const maxTop = window.innerHeight - h - 8;
            if (rect.left > maxLeft || rect.top > maxTop) {
                speedDial.style.left = Math.min(Math.max(8, rect.left), maxLeft) + 'px';
                speedDial.style.top = Math.min(Math.max(8, rect.top), maxTop) + 'px';
            }
            if (rect.top < 220) {
                speedDial.classList.add('expand-down');
            } else {
                speedDial.classList.remove('expand-down');
            }
        }
        if (window.innerWidth <= 576) {
            chatWindow.style.left = '';
            chatWindow.style.top = '';
            chatWindow.style.right = '';
            chatWindow.style.bottom = '';
        }
    });
})();
