/**
 * PartyUp CAPTCHA JS SDK v1.0.0
 * 파트너 사이트에서 <script defer src="https://api.party-up.xyz/sdk/partyup-captcha.js"></script>
 * 로 로드한 뒤 PartyUpCaptcha.render(element, { siteKey, onSuccess, onError }) 로 사용
 *
 * IIFE 패턴: CDN <script> 로딩 환경에서 전역 오염 없이 단일 진입점만 노출
 */
;(function (global) {
  'use strict';

  // ══════════════════════════════════════════════════════════════
  // 1. 설정 상수
  // ══════════════════════════════════════════════════════════════
  var API_BASE = 'https://api.party-up.xyz';
  var MAX_MOUSE_POINTS = 500;
  var SDK_VERSION = '1.0.0';

  // ══════════════════════════════════════════════════════════════
  // 2. 유틸리티
  // ══════════════════════════════════════════════════════════════
  function generateUUID() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function getCanvasHash() {
    try {
      var canvas = document.createElement('canvas');
      var ctx = canvas.getContext('2d');
      if (!ctx) return 'no-canvas';
      ctx.textBaseline = 'top';
      ctx.font = '14px Arial';
      ctx.fillStyle = '#f60';
      ctx.fillRect(125, 1, 62, 20);
      ctx.fillStyle = '#069';
      ctx.fillText('PartyUp', 2, 15);
      var dataUrl = canvas.toDataURL();
      var hash = 0;
      for (var i = 0; i < dataUrl.length; i++) {
        hash = ((hash << 5) - hash + dataUrl.charCodeAt(i)) | 0;
      }
      return hash.toString(16);
    } catch (e) {
      return 'canvas-error';
    }
  }

  function getWebGLRenderer() {
    try {
      var canvas = document.createElement('canvas');
      var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
      if (!gl) return 'no-webgl';
      var debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
      if (!debugInfo) return 'no-debug-info';
      return gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || 'unknown';
    } catch (e) {
      return 'webgl-error';
    }
  }

  function collectEnvInfo() {
    return {
      webdriver: !!navigator.webdriver,
      plugins_count: navigator.plugins ? navigator.plugins.length : 0,
      canvas_hash: getCanvasHash(),
      webgl_renderer: getWebGLRenderer(),
      screen: { width: screen.width, height: screen.height },
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      languages: Array.prototype.slice.call(navigator.languages || [navigator.language])
    };
  }

  // ── URL 헬퍼: 상대경로를 API_BASE 기반 절대경로로 변환 ──
  function resolveUrl(url) {
    if (!url) return url;
    // 이미 절대 URL이면 그대로 반환
    if (url.indexOf('http://') === 0 || url.indexOf('https://') === 0) return url;
    // /api/... 상대경로 → API_BASE + /api/...
    if (url.charAt(0) === '/') return API_BASE + url;
    return API_BASE + '/' + url;
  }

  // ── API 통신 헬퍼 ──
  function apiPost(path, body, siteKey) {
    return fetch(API_BASE + path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Site-Key': siteKey
      },
      body: JSON.stringify(body)
    }).then(function (res) {
      if (!res.ok) throw new Error('API error: ' + res.status);
      return res.json();
    });
  }

  function apiGet(path, siteKey) {
    return fetch(API_BASE + path, {
      method: 'GET',
      headers: { 'X-Site-Key': siteKey }
    }).then(function (res) {
      if (!res.ok) throw new Error('API error: ' + res.status);
      return res.json();
    });
  }

  // ══════════════════════════════════════════════════════════════
  // 3. BehaviorCollector — 행동 데이터 수집기
  // ══════════════════════════════════════════════════════════════
  function BehaviorCollector() {
    this.sessionId = generateUUID();
    this.pageLoadTime = Date.now();
    this.mouseMoves = [];
    this.clicks = [];
    this.keyIntervals = [];
    this.lastKeyTime = null;
    this.scrolled = false;
    this.envInfo = null;
    this._listeners = [];
  }

  BehaviorCollector.prototype.start = function () {
    var self = this;
    this.envInfo = collectEnvInfo();

    var onMouseMove = function (e) {
      if (self.mouseMoves.length >= MAX_MOUSE_POINTS) return;
      self.mouseMoves.push({
        x: e.clientX,
        y: e.clientY,
        t: Date.now() - self.pageLoadTime
      });
    };

    var onClick = function (e) {
      var target = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : 'unknown';
      self.clicks.push({
        x: e.clientX,
        y: e.clientY,
        t: Date.now() - self.pageLoadTime,
        target: target
      });
    };

    var onKeyDown = function () {
      var now = Date.now();
      if (self.lastKeyTime !== null) {
        self.keyIntervals.push(now - self.lastKeyTime);
      }
      self.lastKeyTime = now;
    };

    var onScroll = function () {
      self.scrolled = true;
    };

    document.addEventListener('mousemove', onMouseMove, { passive: true });
    document.addEventListener('click', onClick, { passive: true });
    document.addEventListener('keydown', onKeyDown, { passive: true });
    document.addEventListener('scroll', onScroll, { passive: true });

    this._listeners = [
      ['mousemove', onMouseMove],
      ['click', onClick],
      ['keydown', onKeyDown],
      ['scroll', onScroll]
    ];
  };

  BehaviorCollector.prototype.stop = function () {
    this._listeners.forEach(function (pair) {
      document.removeEventListener(pair[0], pair[1]);
    });
    this._listeners = [];
  };

  BehaviorCollector.prototype.collect = function (triggerType) {
    return {
      mouse_moves: this.mouseMoves.slice(),
      clicks: this.clicks.slice(),
      key_intervals: this.keyIntervals.slice(),
      scrolled: this.scrolled,
      env: this.envInfo || collectEnvInfo(),
      page_load_to_checkbox: Date.now() - this.pageLoadTime,
      session_id: this.sessionId,
      timestamp: new Date().toISOString(),
      trigger_type: triggerType || 'new_ip_login'
    };
  };

  // ══════════════════════════════════════════════════════════════
  // 4. CSS 스타일 주입
  // ══════════════════════════════════════════════════════════════
  var STYLE_ID = 'partyup-captcha-styles';

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = '\
/* ── PartyUp CAPTCHA SDK Styles ── */\n\
.pu-captcha-container {\
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;\
  line-height: 1.5;\
  -webkit-font-smoothing: antialiased;\
}\
\
/* ── 체크박스 ── */\
.pu-checkbox {\
  display: flex;\
  align-items: center;\
  gap: 12px;\
  padding: 14px 20px;\
  border: 2px solid #e5e7eb;\
  border-radius: 12px;\
  background: #fff;\
  cursor: pointer;\
  user-select: none;\
  transition: all 0.2s ease;\
  max-width: 320px;\
  width: 100%;\
  box-sizing: border-box;\
}\
.pu-checkbox:hover { border-color: #93c5fd; }\
.pu-checkbox.pu-checked { border-color: #4ade80; background: #f0fdf4; cursor: default; }\
.pu-checkbox.pu-loading { border-color: #93c5fd; cursor: default; }\
.pu-checkbox.pu-warning { border-color: #fca5a5; background: #fef2f2; cursor: default; }\
.pu-checkbox.pu-disabled { opacity: 0.6; cursor: not-allowed; }\
\
.pu-check-icon {\
  width: 24px;\
  height: 24px;\
  flex-shrink: 0;\
  display: flex;\
  align-items: center;\
  justify-content: center;\
  border: 2px solid #d1d5db;\
  border-radius: 6px;\
  background: #fff;\
  transition: all 0.2s;\
}\
.pu-checked .pu-check-icon { border-color: #22c55e; background: #22c55e; }\
.pu-loading .pu-check-icon { border-color: #60a5fa; background: #eff6ff; }\
.pu-warning .pu-check-icon { border-color: #f87171; background: #fee2e2; }\
\
.pu-label { flex: 1; font-size: 14px; font-weight: 500; color: #374151; }\
.pu-warning .pu-label { color: #dc2626; }\
\
.pu-brand {\
  flex-shrink: 0;\
  display: flex;\
  flex-direction: column;\
  align-items: center;\
}\
.pu-brand-icon {\
  width: 28px; height: 28px;\
  border-radius: 50%;\
  background: #3b82f6;\
  display: flex;\
  align-items: center;\
  justify-content: center;\
  font-size: 12px;\
  font-weight: 700;\
  color: #fff;\
}\
.pu-brand-text { font-size: 9px; color: #9ca3af; margin-top: 2px; }\
\
/* ── 스피너 ── */\
.pu-spinner {\
  width: 16px; height: 16px;\
  border: 2px solid #60a5fa;\
  border-top-color: transparent;\
  border-radius: 50%;\
  animation: pu-spin 0.6s linear infinite;\
}\
@keyframes pu-spin { to { transform: rotate(360deg); } }\
\
/* ── 챌린지 모달 ── */\
.pu-overlay {\
  position: fixed;\
  inset: 0;\
  z-index: 99999;\
  display: flex;\
  align-items: center;\
  justify-content: center;\
  background: rgba(15,23,42,0.45);\
  backdrop-filter: blur(2px);\
  padding: 16px;\
}\
.pu-modal {\
  width: 100%;\
  max-width: 430px;\
  background: #fff;\
  border-radius: 24px;\
  border: 1px solid #e2e8f0;\
  box-shadow: 0 32px 80px rgba(15,23,42,0.22);\
  overflow: hidden;\
}\
\
/* ── 모달 헤더 ── */\
.pu-modal-header {\
  display: flex;\
  align-items: center;\
  justify-content: space-between;\
  padding: 16px 20px;\
  border-bottom: 1px solid #f1f5f9;\
}\
.pu-modal-title { font-size: 16px; font-weight: 700; color: #0f172a; }\
.pu-modal-subtitle { font-size: 12px; color: #64748b; margin-top: 4px; }\
.pu-modal-close {\
  background: none; border: none; padding: 6px;\
  border-radius: 50%; color: #94a3b8; cursor: pointer;\
  transition: all 0.15s;\
}\
.pu-modal-close:hover { background: #f1f5f9; color: #475569; }\
\
/* ── 이모지 힌트 ── */\
.pu-emoji-row {\
  display: flex;\
  align-items: center;\
  justify-content: center;\
  gap: 16px;\
  padding: 20px 20px 12px;\
}\
.pu-emoji-item { position: relative; }\
.pu-emoji-badge {\
  position: absolute; top: -8px; left: -8px;\
  width: 20px; height: 20px;\
  border-radius: 50%; background: #3b82f6;\
  display: flex; align-items: center; justify-content: center;\
  font-size: 11px; font-weight: 700; color: #fff;\
}\
.pu-emoji-img {\
  width: 64px; height: 64px;\
  border-radius: 12px;\
  border: 2px solid #bfdbfe;\
  object-fit: cover;\
  display: block;\
}\
\
/* ── 구분선 ── */\
.pu-divider {\
  display: flex; align-items: center; gap: 8px;\
  padding: 0 20px 4px;\
}\
.pu-divider-line { flex: 1; height: 1px; background: #e2e8f0; }\
.pu-divider-text { font-size: 12px; color: #94a3b8; }\
\
/* ── 사진 그리드 ── */\
.pu-grid {\
  display: grid;\
  grid-template-columns: repeat(3, 1fr);\
  gap: 8px;\
  padding: 12px 20px 16px;\
}\
.pu-grid-cell {\
  position: relative;\
  aspect-ratio: 1;\
  overflow: hidden;\
  border-radius: 12px;\
  border: 3px solid transparent;\
  cursor: pointer;\
  transition: all 0.15s;\
  background: #f8fafc;\
}\
.pu-grid-cell:hover { border-color: #93c5fd; }\
.pu-grid-cell.pu-selected { border-color: #3b82f6; box-shadow: 0 0 0 2px #bfdbfe; }\
.pu-grid-cell.pu-submitting { pointer-events: none; opacity: 0.6; }\
.pu-grid-cell img {\
  width: 100%; height: 100%; object-fit: cover; display: block;\
}\
.pu-grid-badge {\
  position: absolute; inset: 0;\
  display: flex; align-items: center; justify-content: center;\
  background: rgba(59,130,246,0.2);\
}\
.pu-grid-badge span {\
  width: 28px; height: 28px;\
  border-radius: 50%; background: #3b82f6;\
  display: flex; align-items: center; justify-content: center;\
  font-size: 14px; font-weight: 700; color: #fff;\
  box-shadow: 0 2px 6px rgba(0,0,0,0.15);\
}\
\
/* ── 에러 메시지 ── */\
.pu-error {\
  margin: 0 20px 12px;\
  padding: 8px 12px;\
  border-radius: 12px;\
  background: #fef2f2;\
  font-size: 14px;\
  color: #ef4444;\
}\
.pu-error-remaining { margin-left: 4px; color: #f87171; }\
\
/* ── 모달 푸터 ── */\
.pu-modal-footer {\
  display: flex;\
  align-items: center;\
  justify-content: space-between;\
  padding: 16px 20px;\
  border-top: 1px solid #f1f5f9;\
}\
.pu-progress {\
  display: flex;\
  align-items: center;\
  gap: 6px;\
}\
.pu-progress-dot {\
  width: 24px; height: 24px;\
  border-radius: 50%;\
  border: 2px solid #d1d5db;\
  display: flex; align-items: center; justify-content: center;\
  font-size: 12px; font-weight: 700; color: #d1d5db;\
  transition: all 0.15s;\
}\
.pu-progress-dot.pu-filled {\
  border-color: #3b82f6; background: #3b82f6; color: #fff;\
}\
.pu-progress-count { font-size: 12px; color: #94a3b8; margin-left: 4px; }\
\
.pu-actions { display: flex; gap: 8px; }\
.pu-btn-text {\
  background: none; border: none;\
  padding: 6px 12px; font-size: 14px; color: #64748b;\
  cursor: pointer; transition: color 0.15s;\
}\
.pu-btn-text:hover { color: #334155; }\
.pu-btn-text:disabled { opacity: 0.4; cursor: not-allowed; }\
.pu-btn-primary {\
  background: #3b82f6; border: none;\
  padding: 6px 16px; border-radius: 8px;\
  font-size: 14px; font-weight: 500; color: #fff;\
  cursor: pointer; transition: background 0.15s;\
}\
.pu-btn-primary:hover { background: #2563eb; }\
.pu-btn-primary:disabled { background: #e2e8f0; color: #94a3b8; cursor: not-allowed; }\
.pu-btn-submitting {\
  display: flex; align-items: center; gap: 6px;\
}\
.pu-btn-submitting .pu-spinner {\
  width: 14px; height: 14px;\
  border-color: #fff;\
  border-top-color: transparent;\
}\
\
/* ── 상태 카드 ── */\
.pu-status-card {\
  max-width: 320px; width: 100%;\
  padding: 12px 16px;\
  border-radius: 12px;\
  font-size: 13px;\
  margin-top: 8px;\
  box-sizing: border-box;\
}\
.pu-status-wait { background: #fffbeb; color: #92400e; border: 1px solid #fde68a; }\
.pu-status-locked { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }\
.pu-status-banned { background: #fef2f2; color: #7f1d1d; border: 1px solid #fca5a5; }\
.pu-status-retry {\
  background: none; border: 1px solid currentColor;\
  border-radius: 6px; padding: 4px 12px; margin-top: 8px;\
  font-size: 12px; cursor: pointer; color: inherit;\
}\
.pu-status-retry:disabled { opacity: 0.5; cursor: not-allowed; }\
';
    document.head.appendChild(style);
  }

  // ══════════════════════════════════════════════════════════════
  // 5. ChallengeRenderer — 3x3 이미지 챌린지 모달
  // ══════════════════════════════════════════════════════════════
  function ChallengeRenderer(opts) {
    this.siteKey = opts.siteKey;
    this.onSuccess = opts.onSuccess;
    this.onError = opts.onError;
    this.onCancel = opts.onCancel;
    this.sessionId = null;
    this.selectedIndices = [];
    this.isSubmitting = false;
    this.overlayEl = null;
  }

  ChallengeRenderer.prototype.open = function (sessionId) {
    this.sessionId = sessionId;
    this.selectedIndices = [];
    this.isSubmitting = false;
    this._fetchAndRender();
  };

  ChallengeRenderer.prototype.close = function () {
    if (this.overlayEl && this.overlayEl.parentNode) {
      this.overlayEl.parentNode.removeChild(this.overlayEl);
    }
    this.overlayEl = null;
  };

  ChallengeRenderer.prototype._fetchAndRender = function () {
    var self = this;
    apiGet('/api/captcha/challenge?session_id=' + encodeURIComponent(this.sessionId), this.siteKey)
      .then(function (data) {
        self._render(data);
      })
      .catch(function (err) {
        if (self.onError) self.onError('챌린지 데이터를 불러올 수 없습니다.');
      });
  };

  ChallengeRenderer.prototype._render = function (challenge) {
    var self = this;
    this.close(); // 이전 모달 제거

    var overlay = document.createElement('div');
    overlay.className = 'pu-overlay';
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) {
        self.close();
        if (self.onCancel) self.onCancel();
      }
    });

    var modal = document.createElement('div');
    modal.className = 'pu-modal';
    modal.addEventListener('click', function (e) { e.stopPropagation(); });

    // ── 헤더 ──
    var header = document.createElement('div');
    header.className = 'pu-modal-header';
    header.innerHTML = '\
      <div>\
        <div class="pu-modal-title">순서대로 같은 동물을 선택하세요</div>\
        <div class="pu-modal-subtitle">이모티콘 3개와 같은 동물을 아래 사진에서 순서대로 골라주세요.</div>\
      </div>';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'pu-modal-close';
    closeBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none"><path d="M4 4L14 14M14 4L4 14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    closeBtn.addEventListener('click', function () {
      self.close();
      if (self.onCancel) self.onCancel();
    });
    header.appendChild(closeBtn);
    modal.appendChild(header);

    // ── 이모지 힌트 ──
    var emojiRow = document.createElement('div');
    emojiRow.className = 'pu-emoji-row';
    challenge.emojis.forEach(function (emoji, idx) {
      var item = document.createElement('div');
      item.className = 'pu-emoji-item';
      item.innerHTML = '<span class="pu-emoji-badge">' + (idx + 1) + '</span>' +
        '<img class="pu-emoji-img" src="' + resolveUrl(emoji.url) + '" alt="이모티콘 ' + (idx + 1) + '" draggable="false">';
      emojiRow.appendChild(item);
    });
    modal.appendChild(emojiRow);

    // ── 구분선 ──
    var divider = document.createElement('div');
    divider.className = 'pu-divider';
    divider.innerHTML = '<div class="pu-divider-line"></div><span class="pu-divider-text">아래 사진에서 찾기</span><div class="pu-divider-line"></div>';
    modal.appendChild(divider);

    // ── 사진 그리드 ──
    var grid = document.createElement('div');
    grid.className = 'pu-grid';
    this._gridEl = grid;
    this._challenge = challenge;

    challenge.photos.forEach(function (photo) {
      var cell = document.createElement('div');
      cell.className = 'pu-grid-cell';
      cell.dataset.index = photo.index;
      cell.innerHTML = '<img src="' + resolveUrl(photo.url) + '" alt="사진 ' + (photo.index + 1) + '" draggable="false">';
      cell.addEventListener('click', function () {
        self._toggleSelection(photo.index);
      });
      grid.appendChild(cell);
    });
    modal.appendChild(grid);

    // ── 에러 영역 (초기 숨김) ──
    var errorEl = document.createElement('div');
    errorEl.className = 'pu-error';
    errorEl.style.display = 'none';
    this._errorEl = errorEl;
    modal.appendChild(errorEl);

    // ── 푸터 ──
    var footer = document.createElement('div');
    footer.className = 'pu-modal-footer';

    var progress = document.createElement('div');
    progress.className = 'pu-progress';
    for (var i = 0; i < 3; i++) {
      var dot = document.createElement('div');
      dot.className = 'pu-progress-dot';
      dot.textContent = i + 1;
      progress.appendChild(dot);
    }
    var countEl = document.createElement('span');
    countEl.className = 'pu-progress-count';
    countEl.textContent = '0/3';
    progress.appendChild(countEl);
    this._progressDots = progress.querySelectorAll('.pu-progress-dot');
    this._progressCount = countEl;
    footer.appendChild(progress);

    var actions = document.createElement('div');
    actions.className = 'pu-actions';

    var refreshBtn = document.createElement('button');
    refreshBtn.className = 'pu-btn-text';
    refreshBtn.textContent = '새로고침';
    refreshBtn.addEventListener('click', function () {
      if (self.isSubmitting) return;
      self.selectedIndices = [];
      self._fetchAndRender();
    });
    actions.appendChild(refreshBtn);

    var resetBtn = document.createElement('button');
    resetBtn.className = 'pu-btn-text';
    resetBtn.textContent = '초기화';
    resetBtn.addEventListener('click', function () {
      if (self.isSubmitting) return;
      self.selectedIndices = [];
      self._updateGridUI();
    });
    actions.appendChild(resetBtn);

    var submitBtn = document.createElement('button');
    submitBtn.className = 'pu-btn-primary';
    submitBtn.textContent = '확인';
    submitBtn.disabled = true;
    submitBtn.addEventListener('click', function () {
      self._submit();
    });
    this._submitBtn = submitBtn;
    actions.appendChild(submitBtn);

    footer.appendChild(actions);
    modal.appendChild(footer);

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    this.overlayEl = overlay;
  };

  ChallengeRenderer.prototype._toggleSelection = function (index) {
    if (this.isSubmitting) return;
    var pos = this.selectedIndices.indexOf(index);
    if (pos !== -1) {
      this.selectedIndices.splice(pos, 1);
    } else if (this.selectedIndices.length < 3) {
      this.selectedIndices.push(index);
    }
    this._updateGridUI();
  };

  ChallengeRenderer.prototype._updateGridUI = function () {
    var self = this;
    if (!this._gridEl) return;

    var cells = this._gridEl.querySelectorAll('.pu-grid-cell');
    cells.forEach(function (cell) {
      var idx = parseInt(cell.dataset.index, 10);
      var selPos = self.selectedIndices.indexOf(idx);
      cell.className = 'pu-grid-cell' +
        (selPos !== -1 ? ' pu-selected' : '') +
        (self.isSubmitting ? ' pu-submitting' : '');

      // 기존 뱃지 제거
      var badge = cell.querySelector('.pu-grid-badge');
      if (badge) cell.removeChild(badge);

      if (selPos !== -1) {
        var badgeEl = document.createElement('div');
        badgeEl.className = 'pu-grid-badge';
        badgeEl.innerHTML = '<span>' + (selPos + 1) + '</span>';
        cell.appendChild(badgeEl);
      }
    });

    // 진행 점 업데이트
    if (this._progressDots) {
      for (var i = 0; i < 3; i++) {
        this._progressDots[i].className = 'pu-progress-dot' + (i < this.selectedIndices.length ? ' pu-filled' : '');
      }
    }
    if (this._progressCount) {
      this._progressCount.textContent = this.selectedIndices.length + '/3';
    }

    // 확인 버튼 활성화
    if (this._submitBtn) {
      this._submitBtn.disabled = this.selectedIndices.length !== 3 || this.isSubmitting;
    }
  };

  ChallengeRenderer.prototype._showError = function (msg, remaining) {
    if (!this._errorEl) return;
    this._errorEl.style.display = 'block';
    this._errorEl.innerHTML = msg +
      (remaining !== undefined ? '<span class="pu-error-remaining">(남은 시도: ' + remaining + '회)</span>' : '');
  };

  ChallengeRenderer.prototype._hideError = function () {
    if (!this._errorEl) return;
    this._errorEl.style.display = 'none';
  };

  ChallengeRenderer.prototype._submit = function () {
    if (this.selectedIndices.length !== 3 || this.isSubmitting) return;
    var self = this;
    this.isSubmitting = true;
    this._submitBtn.disabled = true;
    this._submitBtn.innerHTML = '<span class="pu-btn-submitting"><span class="pu-spinner"></span>확인 중</span>';
    this._updateGridUI();
    this._hideError();

    apiPost('/api/captcha/verify', {
      session_id: this.sessionId,
      selected_indices: this.selectedIndices
    }, this.siteKey)
      .then(function (result) {
        self.isSubmitting = false;
        if (result.success) {
          self.close();
          if (self.onSuccess) self.onSuccess(result.token);
        } else {
          // 실패
          var remaining = result.remaining_attempts;
          if (remaining !== null && remaining !== undefined && remaining <= 0) {
            self.close();
            if (self.onError) self.onError(result.message || '실패 횟수 초과');
          } else {
            self._showError(result.message || '정답이 아닙니다. 다시 시도해주세요.', remaining);
            self.selectedIndices = [];
            // 새 문제 로드
            self._fetchAndRender();
          }
        }
      })
      .catch(function (err) {
        self.isSubmitting = false;
        self._submitBtn.disabled = false;
        self._submitBtn.textContent = '확인';
        self._showError('검증 중 오류가 발생했습니다.');
        self._updateGridUI();
      });
  };

  // ══════════════════════════════════════════════════════════════
  // 6. 메인 위젯 — PartyUpCaptcha.render()
  // ══════════════════════════════════════════════════════════════
  function CaptchaInstance(container, options) {
    if (!options || !options.siteKey) {
      throw new Error('[PartyUp CAPTCHA] siteKey is required');
    }

    this.container = container;
    this.siteKey = options.siteKey;
    this.triggerType = options.triggerType || 'new_ip_login';
    this.onSuccess = options.onSuccess || function () {};
    this.onError = options.onError || function () {};
    this.lang = options.lang || 'ko';
    this.phase = 'idle';
    this.token = null;

    this.collector = new BehaviorCollector();
    this.challenge = new ChallengeRenderer({
      siteKey: this.siteKey,
      onSuccess: this._handleChallengeSuccess.bind(this),
      onError: this._handleChallengeError.bind(this),
      onCancel: this._handleChallengeCancel.bind(this)
    });

    this._init();
  }

  CaptchaInstance.prototype._init = function () {
    injectStyles();
    this.collector.start();
    this._renderCheckbox();
  };

  CaptchaInstance.prototype._renderCheckbox = function () {
    var self = this;
    this.container.innerHTML = '';

    var wrap = document.createElement('div');
    wrap.className = 'pu-captcha-container';

    // 체크박스
    var cb = document.createElement('div');
    cb.className = 'pu-checkbox';
    this._checkboxEl = cb;

    // 체크 아이콘
    var icon = document.createElement('div');
    icon.className = 'pu-check-icon';
    this._iconEl = icon;
    cb.appendChild(icon);

    // 라벨
    var label = document.createElement('span');
    label.className = 'pu-label';
    label.textContent = '로봇이 아닙니다';
    this._labelEl = label;
    cb.appendChild(label);

    // 브랜드
    var brand = document.createElement('div');
    brand.className = 'pu-brand';
    brand.innerHTML = '<div class="pu-brand-icon">P</div><span class="pu-brand-text">Party-Up</span>';
    cb.appendChild(brand);

    cb.addEventListener('click', function () {
      if (self.phase === 'idle' || self.phase === 'failed') {
        self._startFlow();
      }
    });

    wrap.appendChild(cb);

    // 상태 카드 영역
    var statusWrap = document.createElement('div');
    this._statusWrapEl = statusWrap;
    wrap.appendChild(statusWrap);

    this.container.appendChild(wrap);
  };

  CaptchaInstance.prototype._setPhase = function (phase) {
    this.phase = phase;
    var cb = this._checkboxEl;
    var icon = this._iconEl;
    var label = this._labelEl;
    if (!cb || !icon || !label) return;

    // 클래스 초기화
    cb.className = 'pu-checkbox';
    icon.innerHTML = '';

    switch (phase) {
      case 'idle':
        label.textContent = '로봇이 아닙니다';
        break;
      case 'verifying':
        cb.className += ' pu-loading';
        icon.innerHTML = '<div class="pu-spinner"></div>';
        label.textContent = '확인 중...';
        break;
      case 'passed':
      case 'success':
        cb.className += ' pu-checked';
        icon.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 7L5.5 10.5L12 3.5" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        label.textContent = '인증 완료';
        break;
      case 'failed':
        label.textContent = '다시 시도해주세요';
        break;
      case 'wait':
        cb.className += ' pu-warning';
        icon.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 3L11 11M11 3L3 11" stroke="#EF4444" stroke-width="2.5" stroke-linecap="round"/></svg>';
        label.textContent = '잠시 후 다시 시도해주세요';
        break;
      case 'locked':
        cb.className += ' pu-warning';
        icon.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 3L11 11M11 3L3 11" stroke="#EF4444" stroke-width="2.5" stroke-linecap="round"/></svg>';
        label.textContent = '보안 정책에 따라 잠시 잠금되었습니다';
        break;
      case 'banned':
        cb.className += ' pu-warning';
        icon.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 3L11 11M11 3L3 11" stroke="#EF4444" stroke-width="2.5" stroke-linecap="round"/></svg>';
        label.textContent = '반복 실패로 접근이 차단되었습니다';
        break;
    }
  };

  CaptchaInstance.prototype._showStatus = function (type, message, retryAfter) {
    if (!this._statusWrapEl) return;
    var self = this;
    var card = document.createElement('div');
    card.className = 'pu-status-card pu-status-' + type;
    card.textContent = message;

    if (type === 'wait' && retryAfter) {
      var retryBtn = document.createElement('button');
      retryBtn.className = 'pu-status-retry';
      retryBtn.textContent = retryAfter + '초 후 재시도 가능';
      retryBtn.disabled = true;

      var remaining = retryAfter;
      var timer = setInterval(function () {
        remaining--;
        if (remaining <= 0) {
          clearInterval(timer);
          retryBtn.textContent = '다시 시도';
          retryBtn.disabled = false;
          retryBtn.addEventListener('click', function () {
            self._startFlow();
          });
        } else {
          retryBtn.textContent = remaining + '초 후 재시도 가능';
        }
      }, 1000);
      card.appendChild(retryBtn);
    }

    this._statusWrapEl.innerHTML = '';
    this._statusWrapEl.appendChild(card);
  };

  CaptchaInstance.prototype._hideStatus = function () {
    if (this._statusWrapEl) this._statusWrapEl.innerHTML = '';
  };

  CaptchaInstance.prototype._startFlow = function () {
    var self = this;
    this._setPhase('verifying');
    this._hideStatus();

    // 먼저 상태 체크
    apiGet('/api/captcha/status', this.siteKey)
      .then(function (status) {
        if (status.status !== 'NORMAL') {
          var p = status.status === 'WAIT' ? 'wait'
            : status.status === 'LOCKED' ? 'locked' : 'banned';
          self._setPhase(p);
          self._showStatus(p, status.message, status.retry_after_seconds);
          self.onError(status.message);
          return;
        }

        // 진행 중인 챌린지 세션 복구
        if (status.active_session_id) {
          self._setPhase('idle');
          self.challenge.open(status.active_session_id);
          return;
        }

        // 행동 데이터 전송
        var payload = self.collector.collect(self.triggerType);
        return apiPost('/api/captcha/init', payload, self.siteKey);
      })
      .then(function (result) {
        if (!result) return; // status 분기에서 이미 처리됨

        switch (result.status) {
          case 'pass':
            self.token = result.token;
            self._setPhase('passed');
            setTimeout(function () {
              self._setPhase('success');
              self.onSuccess(result.token);
            }, 500);
            break;

          case 'challenge':
            self._setPhase('idle');
            if (result.session_id) {
              self.challenge.open(result.session_id);
            }
            break;

          case 'block':
            self._setPhase('wait');
            self._showStatus('wait', result.message || '보안 정책에 따라 이용이 일시 제한되었습니다.');
            self.onError(result.message || '일시 제한');
            break;
        }
      })
      .catch(function (err) {
        self._setPhase('failed');
        self.onError('검증 중 오류가 발생했습니다: ' + err.message);
      });
  };

  CaptchaInstance.prototype._handleChallengeSuccess = function (token) {
    this.token = token;
    this._setPhase('passed');
    var self = this;
    setTimeout(function () {
      self._setPhase('success');
      self.onSuccess(token);
    }, 500);
  };

  CaptchaInstance.prototype._handleChallengeError = function (message) {
    this._setPhase('locked');
    this._showStatus('locked', message || '실패 횟수 초과로 잠시 잠금 상태입니다.');
    this.onError(message);
  };

  CaptchaInstance.prototype._handleChallengeCancel = function () {
    this._setPhase('idle');
  };

  /** 외부에서 현재 토큰 가져오기 */
  CaptchaInstance.prototype.getToken = function () {
    return this.token;
  };

  /** 리셋 (재시도 허용) */
  CaptchaInstance.prototype.reset = function () {
    this.token = null;
    this.collector.stop();
    this.collector = new BehaviorCollector();
    this.collector.start();
    this._setPhase('idle');
    this._hideStatus();
  };

  /** 정리 (위젯 제거) */
  CaptchaInstance.prototype.destroy = function () {
    this.collector.stop();
    this.challenge.close();
    this.container.innerHTML = '';
  };

  // ══════════════════════════════════════════════════════════════
  // 7. Public API — 글로벌 진입점
  // ══════════════════════════════════════════════════════════════
  var PartyUpCaptcha = {
    /**
     * 캡챠 위젯을 렌더링합니다.
     *
     * @param {HTMLElement|string} container - 위젯을 삽입할 DOM 요소 또는 CSS 셀렉터
     * @param {Object} options
     * @param {string} options.siteKey - 파트너 사이트 키 (api_keys.api_key)
     * @param {Function} options.onSuccess - 인증 성공 시 콜백 (token)
     * @param {Function} [options.onError] - 에러 발생 시 콜백 (message)
     * @param {string} [options.triggerType='new_ip_login'] - 트리거 유형
     * @param {string} [options.lang='ko'] - 언어 (향후 확장용)
     * @param {string} [options.apiBase] - API 서버 주소 (기본: https://api.party-up.xyz)
     * @returns {CaptchaInstance} 위젯 인스턴스 (getToken, reset, destroy 메서드 사용 가능)
     */
    render: function (container, options) {
      // API 베이스 오버라이드 (개발/테스트용)
      if (options && options.apiBase) {
        API_BASE = options.apiBase;
      }

      var el = typeof container === 'string'
        ? document.querySelector(container)
        : container;

      if (!el) {
        throw new Error('[PartyUp CAPTCHA] container element not found');
      }

      return new CaptchaInstance(el, options);
    },

    /** SDK 버전 */
    version: SDK_VERSION
  };

  // 글로벌 등록
  global.PartyUpCaptcha = PartyUpCaptcha;

})(typeof window !== 'undefined' ? window : this);
