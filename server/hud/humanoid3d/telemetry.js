/**
 * JARVIS CORE — Telemetry & HUD Simulators
 * Updates background metrics and logs that feed the 3D hologram screen interface.
 */
(function() {
  "use strict";

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  function now() {
    var d = new Date();
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  var meters = [
    { key: 'aiProcess', v: 62, lo: 46, hi: 88 },
    { key: 'memory', v: 48, lo: 30, hi: 74 },
    { key: 'knowledgeNet', v: 73, lo: 60, hi: 97 },
    { key: 'quantumIndex', v: 84, lo: 70, hi: 99 }
  ];

  function tick() {
    try {
      if (!window.sysTelemetry) return;
      meters.forEach(function(o) {
        o.v += (Math.random() - 0.5) * 6;
        o.v = Math.max(o.lo, Math.min(o.hi, o.v));
        window.sysTelemetry[o.key] = o.v;
      });
      window.sysTelemetry.activeAgents = (10 + Math.floor(Math.random() * 5));
    } catch(e) {
      console.error("Telemetry tick error:", e);
    }
  }

  function clock() {
    try {
      if (window.sysTelemetry) {
        window.sysTelemetry.clock = now();
      }
    } catch(e) {
      console.error("Telemetry clock error:", e);
    }
  }

  var msgs = [
    'Research Agent · 3 new sources scanned',
    'Knowledge graph · 12 nodes added',
    'Coding Agent · compilation successful',
    'Automation · daily digest generated',
    'Strategy Agent · 2 recommendations ready',
    'Memory consolidation complete',
    'Finance Agent · portfolio synchronized',
    'Learning path updated',
    'Design Agent · 4 variants generated'
  ];

  function pushLog() {
    try {
      if (!window.sysTelemetry || !window.sysTelemetry.logs) return;
      window.sysTelemetry.logs.unshift({
        time: now(),
        msg: msgs[Math.floor(Math.random() * msgs.length)]
      });
      while (window.sysTelemetry.logs.length > 7) {
        window.sysTelemetry.logs.pop();
      }
    } catch(e) {
      console.error("Telemetry pushLog error:", e);
    }
  }

  // Self-initialize after a short delay to ensure window.sysTelemetry is ready
  function init() {
    if (window.sysTelemetry) {
      tick();
      clock();
      pushLog();
      pushLog();
      pushLog();
      setInterval(tick, 1500);
      setInterval(clock, 1000);
      setInterval(pushLog, 2600);
    } else {
      setTimeout(init, 100);
    }
  }

  init();
})();
