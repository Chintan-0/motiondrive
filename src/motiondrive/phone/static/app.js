(function () {
  'use strict';

  // 1. Telemetry Event Stream & Remote Sync (Initialized FIRST)
  const recentEvents = (typeof window !== 'undefined' && window.__MD_EARLY_EVENTS__) ? window.__MD_EARLY_EVENTS__ : [];
  let dbgLastEvent = null;
  let dbgEventsStream = null;
  let dbgError = null;

  function logDebugEvent(evtName, kv = {}) {
    const kvStr = Object.entries(kv).map(([k, v]) => `${k}=${v}`).join(' ');
    console.log(`[MotionDrive] ${evtName} ${kvStr}`.trim());
    if (!dbgLastEvent) dbgLastEvent = document.getElementById('dbg-last-event');
    if (!dbgEventsStream) dbgEventsStream = document.getElementById('dbg-events-stream');
    if (!dbgError) dbgError = document.getElementById('dbg-error');

    if (dbgLastEvent) dbgLastEvent.textContent = evtName;

    const timeStr = new Date().toLocaleTimeString();
    recentEvents.push({ time: timeStr, name: evtName, kv });
    if (recentEvents.length > 20) recentEvents.shift();

    if (dbgEventsStream) {
      dbgEventsStream.innerHTML = recentEvents.map(e => {
        let cssClass = 'evt-name';
        if (e.name.includes('FAIL') || e.name.includes('ERR') || e.name.includes('REJECT') || e.name.includes('TIMEOUT') || e.name.includes('CLOSE')) {
          cssClass = (e.name.includes('CLOSE') && e.kv && e.kv.clean) ? 'evt-name' : 'evt-err';
        } else if (e.name.includes('SUCC') || e.name.includes('ACK_REC') || e.name === 'WS_OPEN' || e.name === 'RAW_WS_OPEN') {
          cssClass = 'evt-succ';
        }
        const detailsStr = Object.entries(e.kv).map(([k, v]) => `${k}=${v}`).join(' ');
        return `<div class="debug-event-item"><span class="evt-time">[${e.time}]</span> <span class="${cssClass}">${e.name}</span> <span class="evt-detail">${detailsStr}</span></div>`;
      }).join('');
      dbgEventsStream.scrollTop = dbgEventsStream.scrollHeight;
    }

    try {
      const qs = new URLSearchParams({
        event: evtName,
        player: String((typeof playerParam !== 'undefined') ? playerParam : 1),
        details: kvStr
      });
      fetch(`/client-log?${qs.toString()}`, { mode: 'no-cors' }).catch(() => {});
    } catch (_) {}
  }

  // Explicit startup events
  logDebugEvent('MOBILE_JS_SCRIPT_LOADED', { time: Date.now() });
  logDebugEvent('MOBILE_APP_INIT_START', { url: (window.location.href || '').slice(0, 100) });

  // 2. Query Parameter Parsing
  logDebugEvent('QUERY_PARSE_START', {});
  let params, sessionToken, playerParam, wsPortRaw, wsPortParam;
  try {
    params = new URLSearchParams(window.location.search);
    sessionToken = params.get('session') || '';
    playerParam = parseInt(params.get('player') || '1', 10);
    wsPortRaw = params.get('ws_port');
    wsPortParam = parseInt(wsPortRaw, 10);
    logDebugEvent('QUERY_PARSE_SUCCESS', {
      player: playerParam,
      ws_port: (wsPortParam > 0) ? wsPortParam : 8766,
      session_present: Boolean(sessionToken)
    });
  } catch (parseErr) {
    logDebugEvent('QUERY_PARSE_ERROR', { reason: parseErr.message || String(parseErr) });
    params = new URLSearchParams();
    sessionToken = '';
    playerParam = 1;
    wsPortRaw = '8766';
    wsPortParam = 8766;
  }

  // 3. WebSocket Configuration
  logDebugEvent('WS_CONFIG_START', {});
  const httpHost = window.location.hostname || '127.0.0.1';
  const httpPort = window.location.port || (window.location.protocol === 'https:' ? '443' : '80');
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsPort = (wsPortParam > 0) ? wsPortParam : 8766;
  const wsHost = httpHost;
  const wsUrl = `${wsProtocol}//${wsHost}:${wsPort}`;

  logDebugEvent('WS_CONFIG_READY', {
    host: wsHost,
    port: wsPort,
    scheme: wsProtocol === 'wss:' ? 'wss' : 'ws',
    player: playerParam,
    session_present: Boolean(sessionToken)
  });

  // App State
  let activeMode = 'manual'; // 'manual' | 'gyro'
  let steering = 0.0;
  let throttle = 0.0;
  let brake = 0.0;
  let shift = false;
  let sequence = 0;
  let isConnected = false;
  let ws = null;
  let isStoppedByUser = false;

  // Latency RTT state
  let latencyMs = 0;
  let lastAckTime = 0;

  // Wake Lock state
  let wakeLock = null;

  // Haptic boundary pulse tracking (edge-triggered)
  let lastSteerMaxHaptic = false;
  let lastBrakeMaxHaptic = false;
  let lastGasMaxHaptic = false;
  let lastShiftState = false;

  // Touch tracking state
  let wheelTouchId = null;
  let wheelStartX = 0;
  let brakeTouchId = null;
  let gasTouchId = null;
  let shiftTouchId = null;

  // Gyro state
  let gyroSupported = false;
  let gyroNeutralGamma = 0.0;
  let currentRawGamma = 0.0;
  let lastGyroTimestamp = 0;
  let gyroWatchdogTimer = null;
  let isGyroListening = false;

  // DOM Elements - Mode Switcher
  const btnManualMode = document.getElementById('mode-manual-btn');
  const btnGyroMode = document.getElementById('mode-gyro-btn');
  const manualWorkspace = document.getElementById('manual-workspace');
  const gyroWorkspace = document.getElementById('gyro-workspace');

  // DOM Elements - Header & Status
  const badgeEl = document.getElementById('connection-badge');
  const statusTextEl = document.getElementById('status-text');
  const signalTextEl = document.getElementById('signal-text');
  const btnShift = document.getElementById('btn-shift');
  const btnEmergencyStop = document.getElementById('btn-emergency-stop');
  const orientationBanner = document.getElementById('orientation-banner');

  // DOM Elements - Manual Mode
  const wheelEl = document.getElementById('steering-wheel');
  const wheelContainer = document.getElementById('wheel-container');
  const readoutEl = document.getElementById('steering-readout');
  const brakePedalManual = document.getElementById('brake-pedal-manual');
  const gasPedalManual = document.getElementById('gas-pedal-manual');
  const brakeValueManual = document.getElementById('brake-value-manual');
  const gasValueManual = document.getElementById('gas-value-manual');
  const arrowLeftManual = document.querySelector('.arrow-left');
  const arrowRightManual = document.querySelector('.arrow-right');

  // DOM Elements - Gyro Mode
  const brakePedalGyro = document.getElementById('brake-pedal-gyro');
  const gasPedalGyro = document.getElementById('gas-pedal-gyro');
  const brakeValueGyro = document.getElementById('brake-value-gyro');
  const gasValueGyro = document.getElementById('gas-value-gyro');
  const gyroAngleText = document.getElementById('gyro-angle-text');
  const gyroArcFill = document.getElementById('gyro-arc-fill');
  const gyroArrowLeft = document.getElementById('gyro-arrow-left');
  const gyroArrowRight = document.getElementById('gyro-arrow-right');
  const btnRecenterGyro = document.getElementById('recenter-gyro-btn');
  const gyroAlertBanner = document.getElementById('gyro-alert-banner');
  const btnFallbackManual = document.getElementById('fallback-manual-btn');

  // DOM Elements - Connection Debug Panel
  const debugPanel = document.getElementById('connection-debug-panel');
  const btnToggleDebug = document.getElementById('btn-toggle-debug');
  const btnCloseDebug = document.getElementById('btn-close-debug');
  const btnManualConnect = document.getElementById('btn-manual-connect');
  const btnRunRawWsTest = document.getElementById('btn-run-raw-ws-test');
  dbgEventsStream = document.getElementById('dbg-events-stream');
  const dbgTarget = document.getElementById('dbg-target');
  const dbgClientState = document.getElementById('dbg-client-state');
  dbgLastEvent = document.getElementById('dbg-last-event');
  const dbgHttpHost = document.getElementById('dbg-http-host');
  const dbgHttpPort = document.getElementById('dbg-http-port');
  const dbgWsPort = document.getElementById('dbg-ws-port');
  const dbgPlayer = document.getElementById('dbg-player');
  const dbgSession = document.getElementById('dbg-session');
  dbgError = document.getElementById('dbg-error');
  const dbgCloseCode = document.getElementById('dbg-close-code');
  const dbgCloseReason = document.getElementById('dbg-close-reason');
  const dbgRawTarget = document.getElementById('dbg-raw-target');
  const dbgRawState = document.getElementById('dbg-raw-state');
  const dbgRawResult = document.getElementById('dbg-raw-result');
  const dbgRawTestLink = document.getElementById('dbg-raw-test-link');

  // Client WebSocket State Tracker: NOT_STARTED | CONNECTING | OPEN | AUTHENTICATING | CONNECTED | REJECTED | ERROR | CLOSED
  let clientWsState = 'NOT_STARTED';

  function setClientWsState(state, colorClass = '') {
    clientWsState = state;
    if (dbgClientState) {
      dbgClientState.textContent = state;
      let cls = 'dl-val ';
      if (colorClass) {
        cls += colorClass;
      } else if (state === 'CONNECTED' || state === 'OPEN') {
        cls += 'text-green';
      } else if (state === 'CONNECTING' || state === 'AUTHENTICATING' || state === 'NOT_STARTED') {
        cls += 'text-yellow';
      } else {
        cls += 'text-red';
      }
      dbgClientState.className = cls;
    }
  }

  // Pre-fill debug details
  if (dbgTarget) dbgTarget.textContent = wsUrl;
  if (dbgHttpHost) dbgHttpHost.textContent = httpHost;
  if (dbgHttpPort) dbgHttpPort.textContent = String(httpPort);
  if (dbgWsPort) dbgWsPort.textContent = wsPortRaw ? `${wsPort} (param)` : `${wsPort} (default 8766)`;
  if (dbgPlayer) dbgPlayer.textContent = String(playerParam);
  if (dbgSession) dbgSession.textContent = `present=${Boolean(sessionToken)} (prefix=${sessionToken ? sessionToken.slice(0, 7) : 'none'})`;
  if (dbgRawTarget) dbgRawTarget.textContent = wsUrl;
  if (dbgRawTestLink) dbgRawTestLink.href = `/ws-test${window.location.search}`;
  setClientWsState('NOT_STARTED');

  logDebugEvent('MOBILE_APP_INIT_COMPLETE', {});

  function toggleDebugPanel(show) {
    if (!debugPanel) return;
    if (typeof show === 'boolean') {
      if (show) debugPanel.classList.remove('hidden');
      else debugPanel.classList.add('hidden');
    } else {
      debugPanel.classList.toggle('hidden');
    }
  }

  function onToggleDebug(e) {
    if (e) e.preventDefault();
    toggleDebugPanel();
  }

  function onCloseDebug(e) {
    if (e) e.preventDefault();
    toggleDebugPanel(false);
  }

  if (btnToggleDebug) {
    btnToggleDebug.addEventListener('click', onToggleDebug);
    btnToggleDebug.addEventListener('touchend', onToggleDebug);
  }
  if (btnCloseDebug) {
    btnCloseDebug.addEventListener('click', onCloseDebug);
    btnCloseDebug.addEventListener('touchend', onCloseDebug);
  }
  if (badgeEl) {
    badgeEl.addEventListener('click', onToggleDebug);
    badgeEl.addEventListener('touchend', onToggleDebug);
  }
  if (params.get('debug') === '1') {
    toggleDebugPanel(true);
  }

  // Edge-Triggered Haptic Feedback Helper
  function triggerHaptic(durationMs) {
    try {
      if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') {
        navigator.vibrate(durationMs);
      }
    } catch (err) {}
  }

  // Web Screen Wake Lock API Helper
  async function requestWakeLock() {
    try {
      if ('wakeLock' in navigator) {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => {
          wakeLock = null;
        });
      }
    } catch (err) {}
  }

  function releaseWakeLock() {
    if (wakeLock !== null) {
      try {
        wakeLock.release();
      } catch (err) {}
      wakeLock = null;
    }
  }

  // Orientation Check
  function checkOrientation() {
    if (window.innerHeight > window.innerWidth) {
      if (orientationBanner) orientationBanner.classList.remove('hidden');
    } else {
      if (orientationBanner) orientationBanner.classList.add('hidden');
    }
  }
  window.addEventListener('resize', checkOrientation);
  checkOrientation();

  // Mode Switcher Handler
  function switchMode(mode) {
    if (activeMode === mode) return;

    // Reset steering state during transition
    steering = 0.0;
    wheelTouchId = null;

    activeMode = mode;

    if (mode === 'manual') {
      if (btnManualMode) btnManualMode.classList.add('active');
      if (btnGyroMode) btnGyroMode.classList.remove('active');
      if (manualWorkspace) manualWorkspace.classList.add('active');
      if (gyroWorkspace) {
        gyroWorkspace.classList.remove('active');
        gyroWorkspace.classList.add('hidden');
      }
      if (gyroAlertBanner) gyroAlertBanner.classList.add('hidden');
      stopGyroListener();
    } else if (mode === 'gyro') {
      if (btnGyroMode) btnGyroMode.classList.add('active');
      if (btnManualMode) btnManualMode.classList.remove('active');
      if (gyroWorkspace) {
        gyroWorkspace.classList.remove('hidden');
        gyroWorkspace.classList.add('active');
      }
      if (manualWorkspace) manualWorkspace.classList.remove('active');
      // Initialize Gyro from safe current orientation as neutral 0°
      gyroNeutralGamma = currentRawGamma;
      startGyroListener();
    }

    triggerHaptic(20);
    updateVisuals();
    sendPayload();
  }

  if (btnManualMode) btnManualMode.addEventListener('click', () => switchMode('manual'));
  if (btnGyroMode) btnGyroMode.addEventListener('click', () => switchMode('gyro'));
  if (btnFallbackManual) btnFallbackManual.addEventListener('click', () => switchMode('manual'));

  // Safety Neutralizer
  function neutralizeAll() {
    steering = 0.0;
    throttle = 0.0;
    brake = 0.0;
    shift = false;
    wheelTouchId = null;
    brakeTouchId = null;
    gasTouchId = null;
    shiftTouchId = null;
    updateVisuals();
    sendPayload();
  }

  // Visual State Updates, Neon Glow Response & Edge Haptics
  function updateVisuals() {
    // 1. Shift Button
    if (btnShift) btnShift.classList.toggle('active', shift);
    if (shift !== lastShiftState) {
      if (shift) triggerHaptic(30);
      lastShiftState = shift;
    }

    // 2. Pedals (Manual Mode)
    if (brakePedalManual) brakePedalManual.classList.toggle('active', brake > 0);
    if (gasPedalManual) gasPedalManual.classList.toggle('active', throttle > 0);
    if (brakeValueManual) brakeValueManual.textContent = `${Math.round(brake * 100)}%`;
    if (gasValueManual) gasValueManual.textContent = `${Math.round(throttle * 100)}%`;

    // 3. Pedals (Gyro Mode)
    if (brakePedalGyro) brakePedalGyro.classList.toggle('active', brake > 0);
    if (gasPedalGyro) gasPedalGyro.classList.toggle('active', throttle > 0);
    if (brakeValueGyro) brakeValueGyro.textContent = `${Math.round(brake * 100)}%`;
    if (gasValueGyro) gasValueGyro.textContent = `${Math.round(throttle * 100)}%`;

    // Edge Haptics on Max Pedals
    const isBrakeMax = brake >= 0.98;
    if (isBrakeMax && !lastBrakeMaxHaptic) triggerHaptic(15);
    lastBrakeMaxHaptic = isBrakeMax;

    const isGasMax = throttle >= 0.98;
    if (isGasMax && !lastGasMaxHaptic) triggerHaptic(15);
    lastGasMaxHaptic = isGasMax;

    // 4. Steering (Manual Mode)
    if (activeMode === 'manual') {
      const angle = steering * 120.0; // max +/- 120 deg rotation
      if (wheelEl) wheelEl.style.transform = `rotate(${angle}deg)`;

      const percent = Math.round(steering * 100);
      if (readoutEl) readoutEl.textContent = `${percent > 0 ? '+' : ''}${percent}%`;

      if (arrowLeftManual) arrowLeftManual.classList.toggle('active', steering < -0.05);
      if (arrowRightManual) arrowRightManual.classList.toggle('active', steering > 0.05);
    }

    // 5. Steering (Gyro Mode)
    if (activeMode === 'gyro') {
      const degrees = Math.round(steering * 25.0); // max +/- 25 deg tilt display
      if (gyroAngleText) {
        gyroAngleText.textContent = `${degrees > 0 ? '+' : ''}${degrees}°`;
      }

      if (gyroArrowLeft) gyroArrowLeft.classList.toggle('active', steering < -0.05);
      if (gyroArrowRight) gyroArrowRight.classList.toggle('active', steering > 0.05);

      if (gyroArcFill) {
        const startX = 150;
        const endX = 150 + (steering * 110);
        gyroArcFill.setAttribute('d', `M ${startX},20 A 130,130 0 0,${steering >= 0 ? 1 : 0} ${endX},20`);
      }
    }

    // Edge Haptic on Max Steering Lock
    const isSteerMax = Math.abs(steering) >= 0.98;
    if (isSteerMax && !lastSteerMaxHaptic) triggerHaptic(15);
    lastSteerMaxHaptic = isSteerMax;
  }

  // ------------------------------------------------------------- Touch Steering Wheel
  function handleWheelStart(e) {
    e.preventDefault();
    if (activeMode !== 'manual' || wheelTouchId !== null) return;
    const touch = e.changedTouches[0];
    wheelTouchId = touch.identifier;
    wheelStartX = touch.clientX;
  }

  function handleWheelMove(e) {
    e.preventDefault();
    if (activeMode !== 'manual' || wheelTouchId === null) return;
    for (let i = 0; i < e.changedTouches.length; i++) {
      const touch = e.changedTouches[i];
      if (touch.identifier === wheelTouchId) {
        const deltaX = touch.clientX - wheelStartX;
        const maxDelta = 100.0; // 100px drag for full lock
        steering = Math.max(-1.0, Math.min(1.0, deltaX / maxDelta));
        updateVisuals();
        break;
      }
    }
  }

  function handleWheelEnd(e) {
    if (wheelTouchId === null) return;
    for (let i = 0; i < e.changedTouches.length; i++) {
      if (e.changedTouches[i].identifier === wheelTouchId) {
        wheelTouchId = null;
        steering = 0.0;
        updateVisuals();
        break;
      }
    }
  }

  if (wheelContainer) {
    wheelContainer.addEventListener('touchstart', handleWheelStart, { passive: false });
    window.addEventListener('touchmove', handleWheelMove, { passive: false });
    window.addEventListener('touchend', handleWheelEnd);
    window.addEventListener('touchcancel', handleWheelEnd);
  }

  // Gyro state
  let gyroSupported = false;
  let gyroNeutralGamma = 0.0;
  let currentRawGamma = 0.0;
  let gyroEmaSteer = 0.0;
  let lastGyroTimestamp = 0;
  let gyroWatchdogTimer = null;
  let isGyroListening = false;

  // ------------------------------------------------------------- Gyro Controller
  function handleDeviceOrientation(e) {
    if (activeMode !== 'gyro') return;
    lastGyroTimestamp = Date.now();
    gyroSupported = true;

    if (gyroAlertBanner) gyroAlertBanner.classList.add('hidden');

    let rawGamma = e.gamma;
    if (rawGamma === null || rawGamma === undefined) {
      rawGamma = e.beta || 0.0;
    }
    currentRawGamma = rawGamma;

    // Determine normalized tilt relative to calibrated neutral angle
    let diff = currentRawGamma - gyroNeutralGamma;
    // Deadband filter (+/- 0.8 deg)
    if (Math.abs(diff) < 0.8) {
      diff = 0.0;
    }
    const maxTilt = 25.0; // 25 degree tilt for max steering lock
    const targetSteer = Math.max(-1.0, Math.min(1.0, diff / maxTilt));
    // EMA filter (alpha = 0.75)
    gyroEmaSteer = 0.75 * targetSteer + 0.25 * gyroEmaSteer;
    steering = gyroEmaSteer;

    updateVisuals();
  }

  function startGyroListener() {
    if (isGyroListening) return;

    if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') {
      DeviceOrientationEvent.requestPermission().then((response) => {
        if (response === 'granted') {
          window.addEventListener('deviceorientation', handleDeviceOrientation, true);
          isGyroListening = true;
        } else {
          showGyroUnavailable("SENSOR PERMISSION DENIED");
        }
      }).catch(() => {
        showGyroUnavailable("SENSOR ERROR");
      });
    } else if (window.DeviceOrientationEvent) {
      window.addEventListener('deviceorientation', handleDeviceOrientation, true);
      isGyroListening = true;
    } else {
      showGyroUnavailable("GYRO NOT SUPPORTED");
    }

    lastGyroTimestamp = Date.now();
    if (gyroWatchdogTimer) clearInterval(gyroWatchdogTimer);
    gyroWatchdogTimer = setInterval(() => {
      if (activeMode === 'gyro' && isGyroListening) {
        if (Date.now() - lastGyroTimestamp > 800) { // No sensor update for 800ms
          steering = 0.0;
          gyroEmaSteer = 0.0;
          updateVisuals();
        }
      }
    }, 400);
  }

  function stopGyroListener() {
    if (isGyroListening) {
      window.removeEventListener('deviceorientation', handleDeviceOrientation, true);
      isGyroListening = false;
    }
    if (gyroWatchdogTimer) {
      clearInterval(gyroWatchdogTimer);
      gyroWatchdogTimer = null;
    }
  }

  function showGyroUnavailable(message) {
    if (gyroAlertBanner) {
      const textEl = document.getElementById('gyro-alert-text');
      if (textEl) textEl.textContent = message || "GYRO UNAVAILABLE ON THIS DEVICE";
      gyroAlertBanner.classList.remove('hidden');
    }
    steering = 0.0;
    gyroEmaSteer = 0.0;
    updateVisuals();
  }

  if (btnRecenterGyro) {
    btnRecenterGyro.addEventListener('click', (e) => {
      e.preventDefault();
      // Recenter neutral orientation to current raw angle
      gyroNeutralGamma = currentRawGamma;
      gyroEmaSteer = 0.0;
      steering = 0.0;
      triggerHaptic(25);
      updateVisuals();
      sendBinaryPayload(true, false);
    });
  }

  // ------------------------------------------------------------- Pedals & Shift Multi-Touch
  function setupPedalListeners(pedalEl, setValFn) {
    if (!pedalEl) return;

    pedalEl.addEventListener('touchstart', (e) => {
      e.preventDefault();
      setValFn(1.0);
      updateVisuals();
    }, { passive: false });

    pedalEl.addEventListener('touchend', (e) => {
      setValFn(0.0);
      updateVisuals();
    });

    pedalEl.addEventListener('touchcancel', (e) => {
      setValFn(0.0);
      updateVisuals();
    });

    pedalEl.addEventListener('mousedown', (e) => { e.preventDefault(); setValFn(1.0); updateVisuals(); });
    pedalEl.addEventListener('mouseup', () => { setValFn(0.0); updateVisuals(); });
    pedalEl.addEventListener('mouseleave', () => { setValFn(0.0); updateVisuals(); });
  }

  setupPedalListeners(brakePedalManual, (v) => { brake = v; });
  setupPedalListeners(gasPedalManual, (v) => { throttle = v; });
  setupPedalListeners(brakePedalGyro, (v) => { brake = v; });
  setupPedalListeners(gasPedalGyro, (v) => { throttle = v; });

  // Shift Button
  if (btnShift) {
    btnShift.addEventListener('touchstart', (e) => {
      e.preventDefault();
      shift = true;
      updateVisuals();
    }, { passive: false });

    btnShift.addEventListener('touchend', () => {
      shift = false;
      updateVisuals();
    });

    btnShift.addEventListener('touchcancel', () => {
      shift = false;
      updateVisuals();
    });

    btnShift.addEventListener('mousedown', (e) => { e.preventDefault(); shift = true; updateVisuals(); });
    btnShift.addEventListener('mouseup', () => { shift = false; updateVisuals(); });
    btnShift.addEventListener('mouseleave', () => { shift = false; updateVisuals(); });
  }

  // Emergency Stop Button
  if (btnEmergencyStop) {
    btnEmergencyStop.addEventListener('click', (e) => {
      e.preventDefault();
      isStoppedByUser = true;
      sendBinaryPayload(false, true);
      neutralizeAll();
      triggerHaptic(60);
      releaseWakeLock();
      if (ws) {
        try { ws.close(); } catch(err) {}
      }
      setStatus('error', 'STOPPED BY USER');
    });
  }

  // Window Safety Neutralizers & Wake Lock Recovery
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      neutralizeAll();
    } else if (isConnected && !isStoppedByUser) {
      requestWakeLock();
    }
  });
  window.addEventListener('blur', neutralizeAll);
  window.addEventListener('pagehide', () => {
    neutralizeAll();
    releaseWakeLock();
  });

  // ------------------------------------------------------------- WebSocket Connection & RTT
  function setStatus(state, message) {
    if (badgeEl) badgeEl.className = `badge ${state}`;
    if (statusTextEl) statusTextEl.textContent = message;
  }

  function updateSignalQuality(ms) {
    if (!signalTextEl) return;
    if (ms < 30) {
      signalTextEl.textContent = `EXCELLENT (${ms} ms)`;
    } else if (ms < 80) {
      signalTextEl.textContent = `GOOD (${ms} ms)`;
    } else {
      signalTextEl.textContent = `WEAK (${ms} ms)`;
    }
  }

  let handshakeAckTimer = null;

  // Inline Raw WebSocket Test on :8766
  let rawTestWs = null;
  function runInlineRawWsTest(e) {
    if (e) e.preventDefault();
    if (dbgRawTarget) dbgRawTarget.textContent = wsUrl;
    if (dbgRawState) {
      dbgRawState.textContent = 'CONNECTING...';
      dbgRawState.className = 'dl-val text-yellow';
    }
    if (dbgRawResult) {
      dbgRawResult.textContent = 'Attempting raw TCP connect...';
      dbgRawResult.className = 'dl-val';
    }

    logDebugEvent('RAW_WS_START', { url: wsUrl });

    if (rawTestWs) {
      try { rawTestWs.close(); } catch(_) {}
      rawTestWs = null;
    }

    const startTime = Date.now();
    let testCompleted = false;

    try {
      rawTestWs = new WebSocket(wsUrl);
      const timeoutTimer = setTimeout(() => {
        if (!testCompleted) {
          testCompleted = true;
          if (dbgRawState) {
            dbgRawState.textContent = 'TIMEOUT';
            dbgRawState.className = 'dl-val text-red';
          }
          if (dbgRawResult) {
            dbgRawResult.textContent = 'Timeout (5s) - port 8766 unreachable';
            dbgRawResult.className = 'dl-val text-red';
          }
          logDebugEvent('RAW_WS_ERROR', { error: 'timeout_5s' });
          try { rawTestWs.close(); } catch(_) {}
        }
      }, 5000);

      rawTestWs.onopen = () => {
        logDebugEvent('RAW_WS_OPEN', { url: wsUrl });
        if (dbgRawState) {
          dbgRawState.textContent = 'OPEN (SENDING TEST PING)';
          dbgRawState.className = 'dl-val text-yellow';
        }
        rawTestWs.send(JSON.stringify({ version: 1, type: 'ws_test' }));
      };

      rawTestWs.onmessage = (msgEvt) => {
        try {
          const data = JSON.parse(msgEvt.data);
          if (data.type === 'ws_test_ack') {
            testCompleted = true;
            clearTimeout(timeoutTimer);
            const rtt = Date.now() - startTime;
            if (dbgRawState) {
              dbgRawState.textContent = 'ACK RECEIVED ✓';
              dbgRawState.className = 'dl-val text-green';
            }
            if (dbgRawResult) {
              dbgRawResult.textContent = `REACHABLE ✓ (RTT: ${rtt}ms)`;
              dbgRawResult.className = 'dl-val text-green';
            }
            logDebugEvent('RAW_WS_SUCCESS', { rtt_ms: rtt });
            try { rawTestWs.close(); } catch(_) {}
          }
        } catch(_) {}
      };

      rawTestWs.onerror = (err) => {
        if (!testCompleted) {
          testCompleted = true;
          clearTimeout(timeoutTimer);
          const errMsg = (err && (err.message || err.type)) ? (err.message || err.type) : 'Refused / Firewall blocked';
          if (dbgRawState) {
            dbgRawState.textContent = 'ERROR';
            dbgRawState.className = 'dl-val text-red';
          }
          if (dbgRawResult) {
            dbgRawResult.textContent = `FAILED: ${errMsg}`;
            dbgRawResult.className = 'dl-val text-red';
          }
          logDebugEvent('RAW_WS_ERROR', { error: errMsg });
        }
      };

      rawTestWs.onclose = (closeEvt) => {
        if (!testCompleted) {
          testCompleted = true;
          clearTimeout(timeoutTimer);
          const reason = closeEvt.reason || (closeEvt.wasClean ? 'Clean close' : 'Closed unexpectedly');
          if (dbgRawState) {
            dbgRawState.textContent = `CLOSED (${closeEvt.code})`;
            dbgRawState.className = 'dl-val text-red';
          }
          if (dbgRawResult) {
            dbgRawResult.textContent = reason;
            dbgRawResult.className = 'dl-val text-red';
          }
          logDebugEvent('RAW_WS_CLOSE', { code: closeEvt.code, reason, clean: closeEvt.wasClean });
        }
      };
    } catch (err) {
      testCompleted = true;
      if (dbgRawState) {
        dbgRawState.textContent = 'EXCEPTION';
        dbgRawState.className = 'dl-val text-red';
      }
      if (dbgRawResult) {
        dbgRawResult.textContent = err.message || String(err);
        dbgRawResult.className = 'dl-val text-red';
      }
      logDebugEvent('RAW_WS_ERROR', { error: err.message || err });
    }
  }

  if (btnRunRawWsTest) {
    btnRunRawWsTest.addEventListener('click', runInlineRawWsTest);
    btnRunRawWsTest.addEventListener('touchend', runInlineRawWsTest);
  }

  // Manual Connect Button Handler
  if (btnManualConnect) {
    const onManualConnect = (e) => {
      if (e) e.preventDefault();
      isStoppedByUser = false;
      logDebugEvent('MANUAL_CONNECT_CLICKED', { host: wsHost, port: wsPort, player: playerParam });
      if (ws) {
        try { ws.close(); } catch(_) {}
        ws = null;
      }
      connect();
    };
    btnManualConnect.addEventListener('click', onManualConnect);
    btnManualConnect.addEventListener('touchend', onManualConnect);
  }

  function connect() {
    logDebugEvent('CONNECT_FUNCTION_ENTERED', { is_stopped: isStoppedByUser });
    if (isStoppedByUser) {
      logDebugEvent('CONNECT_FUNCTION_EXITED', { reason: 'stopped_by_user' });
      return;
    }
    setStatus('connecting', 'CONNECTING...');
    setClientWsState('CONNECTING');

    logDebugEvent('CONNECT_WEBSOCKET_CALLED', { url: wsUrl });
    logDebugEvent('WS_CONNECT_START', { url: wsUrl });

    // Populate Debug Panel values
    if (dbgTarget) dbgTarget.textContent = wsUrl;
    if (dbgHttpHost) dbgHttpHost.textContent = httpHost;
    if (dbgHttpPort) dbgHttpPort.textContent = String(httpPort);
    if (dbgWsPort) dbgWsPort.textContent = wsPortRaw ? `${wsPort} (param)` : `${wsPort} (default 8766)`;
    if (dbgPlayer) dbgPlayer.textContent = String(playerParam);
    if (dbgSession) dbgSession.textContent = `present=${Boolean(sessionToken)} (prefix=${sessionToken ? sessionToken.slice(0, 7) : 'none'})`;
    if (dbgRawTarget) dbgRawTarget.textContent = wsUrl;
    if (dbgRawTestLink) dbgRawTestLink.href = `/ws-test${window.location.search}`;

    logDebugEvent('WS_CONNECT_CONSTRUCTOR_START', { url: wsUrl });
    try {
      ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      logDebugEvent('WS_CONNECT_CONSTRUCTOR_RETURNED', { url: wsUrl, readyState: ws.readyState });

      ws.onopen = () => {
        logDebugEvent('WS_OPEN', { url: wsUrl });
        // Socket is open, pending server session authentication
        wheelTouchId = null;
        brakeTouchId = null;
        gasTouchId = null;
        shiftTouchId = null;

        setClientWsState('OPEN');
        setStatus('connecting', 'AUTHENTICATING...');
        requestWakeLock();

        const playerBadgeEl = document.getElementById('player-badge');
        if (playerBadgeEl) {
          playerBadgeEl.textContent = `PLAYER ${playerParam === 2 ? 2 : 1}`;
          playerBadgeEl.className = `player-tag p${playerParam === 2 ? 2 : 1}`;
        }

        setClientWsState('AUTHENTICATING');
        if (dbgError) dbgError.textContent = 'none';

        logDebugEvent('HANDSHAKE_SEND', {
          player: playerParam,
          session_present: Boolean(sessionToken),
          session_prefix: sessionToken ? sessionToken.slice(0, 7) : 'none'
        });

        ws.send(JSON.stringify({
          version: 1,
          type: 'handshake',
          session: sessionToken,
          player: playerParam
        }));

        if (handshakeAckTimer) clearTimeout(handshakeAckTimer);
        handshakeAckTimer = setTimeout(() => {
          if (!isConnected) {
            logDebugEvent('HANDSHAKE_ACK_TIMEOUT', { player: playerParam });
            if (dbgError) dbgError.textContent = 'Server handshake ACK timeout (5s)';
          }
        }, 5000);
      };

      ws.onmessage = (e) => {
        try {
          if (e.data instanceof ArrayBuffer) {
            const view = new DataView(e.data);
            if (view.byteLength >= 2 && view.getUint8(0) === 0xAC) {
              const nowSec = Date.now() / 1000.0;
              const rttMs = Math.max(1, Math.round((nowSec - (lastAckTime || nowSec)) * 1000));
              latencyMs = latencyMs === 0 ? rttMs : Math.round(latencyMs * 0.7 + rttMs * 0.3);
              updateSignalQuality(latencyMs);
            }
            return;
          }

          const data = JSON.parse(e.data);
          const msgType = data.type || 'unknown';
          logDebugEvent('WS_MESSAGE', { type: msgType });

          if (data.type === 'handshake_ack' && data.authenticated) {
            if (handshakeAckTimer) { clearTimeout(handshakeAckTimer); handshakeAckTimer = null; }
            isConnected = true;
            setClientWsState('CONNECTED');
            logDebugEvent('HANDSHAKE_ACK_RECEIVED', { player: data.player || playerParam });
            if (dbgError) dbgError.textContent = 'none';
            setStatus('connected', 'CONNECTED ✓');
          } else if (data.type === 'rejected') {
            if (handshakeAckTimer) { clearTimeout(handshakeAckTimer); handshakeAckTimer = null; }
            logDebugEvent('SESSION_REJECTED', { message: data.message || 'unknown' });
            isStoppedByUser = true; // Prevent infinite reconnect loop on invalid session token
            isConnected = false;
            setClientWsState('REJECTED');
            if (dbgError) dbgError.textContent = data.message || 'Session rejected';
            setStatus('error', 'SESSION REJECTED');
            toggleDebugPanel(true);
            alert(data.message || 'Connection rejected by MotionDrive.');
            ws.close();
          } else if (data.type === 'ack' && data.ts) {
            if (!isConnected) {
              isConnected = true;
              setClientWsState('CONNECTED');
              setStatus('connected', 'CONNECTED ✓');
            }
            const nowSec = Date.now() / 1000.0;
            const rttMs = Math.max(1, Math.round((nowSec - data.ts) * 1000));
            latencyMs = latencyMs === 0 ? rttMs : Math.round(latencyMs * 0.7 + rttMs * 0.3);
            updateSignalQuality(latencyMs);
          }
        } catch (err) {}
      };

      ws.onclose = (e) => {
        if (handshakeAckTimer) { clearTimeout(handshakeAckTimer); handshakeAckTimer = null; }
        const reasonStr = e.reason || (e.wasClean ? 'Normal closure' : 'Abnormal closure (check firewall/timeout)');
        setClientWsState('CLOSED');
        logDebugEvent('WS_CLOSE', {
          code: e.code,
          reason: reasonStr,
          clean: e.wasClean
        });
        isConnected = false;
        if (dbgCloseCode) dbgCloseCode.textContent = String(e.code || 'none');
        if (dbgCloseReason) dbgCloseReason.textContent = reasonStr;

        neutralizeAll();
        triggerHaptic(60);
        releaseWakeLock();
        if (!isStoppedByUser) {
          setStatus('connecting', 'RECONNECTING...');
          setTimeout(connect, 2000);
        }
      };

      ws.onerror = (err) => {
        const errMsg = (err && (err.message || err.type)) ? (err.message || err.type) : 'Browser network error / connection refused';
        setClientWsState('ERROR');
        logDebugEvent('WS_ERROR', { error: errMsg });
        isConnected = false;
        if (dbgError) dbgError.textContent = errMsg;
        setStatus('error', 'CONNECTION ERROR');
        // Make debug panel visible on failure so physical tester sees the exact target & error
        toggleDebugPanel(true);
      };
    } catch (err) {
      const errMsg = err.message || String(err);
      setClientWsState('ERROR');
      logDebugEvent('WS_CONNECT_CONSTRUCTOR_ERROR', { error: errMsg });
      if (dbgError) dbgError.textContent = errMsg;
      setStatus('error', 'CONNECTION FAILED');
      toggleDebugPanel(true);
      if (!isStoppedByUser) {
        setTimeout(connect, 3000);
      }
    }
    logDebugEvent('CONNECT_FUNCTION_EXITED', {});
  }

  function sendBinaryPayload(recenterFlag = false, emergencyStopFlag = false) {
    if (!ws || ws.readyState !== WebSocket.OPEN || isStoppedByUser) return;

    const buffer = new ArrayBuffer(6);
    const view = new DataView(buffer);

    // Int16 steering [-32767, 32767]
    const steerInt = Math.max(-32767, Math.min(32767, Math.round(steering * 32767)));
    view.setInt16(0, steerInt, true);

    // UInt8 throttle [0, 255]
    const throttleInt = Math.max(0, Math.min(255, Math.round(throttle * 255)));
    view.setUint8(2, throttleInt);

    // UInt8 brake [0, 255]
    const brakeInt = Math.max(0, Math.min(255, Math.round(brake * 255)));
    view.setUint8(3, brakeInt);

    // UInt8 flags (bit 0: shift, bit 1: recenter, bit 2: emergency_stop)
    let flags = 0;
    if (shift) flags |= 0x01;
    if (recenterFlag) flags |= 0x02;
    if (emergencyStopFlag) flags |= 0x04;
    view.setUint8(4, flags);

    // UInt8 sequence
    const seq = (sequence++) & 0xFF;
    view.setUint8(5, seq);

    lastAckTime = Date.now() / 1000.0;
    ws.send(buffer);
  }

  function sendPayload() {
    sendBinaryPayload(false, false);
  }

  // Stream packets every 16ms (~60 Hz)
  setInterval(sendPayload, 16);

  // Initialize Connection
  connect();
})();
