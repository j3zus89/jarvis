/**
 * JARVIS CORE — Interactive 3D Spirit Face Graphics & Voice Engine
 * Uses Three.js, Custom WebGL shaders, Web Audio, and Web Speech APIs.
 */

// ============================================================================
// GLOBAL INITIALIZATION ERROR HANDLING
// ============================================================================
function fail(m) {
  var i = document.getElementById('intro');
  if (i) {
    i.classList.remove('gone');
    i.style.opacity = '1';
    i.style.transition = 'none';
    i.innerHTML = '<div style="max-width:480px;text-align:center;font-family:sans-serif;color:#7fd8ff;font-size:14px;line-height:1.6">Failed to initialize JARVIS.<br><br><span style="opacity:.7;font-size:12px">' + m + '</span></div>';
  }
}

window.addEventListener('error', function(e) {
  fail(e.message || 'Unknown error');
});

(async function() {
  "use strict";

  // Global simulated telemetry state used to share parameters between loop timers and 3D hologram renderers
  window.sysTelemetry = {
    aiProcess: 62,
    memory: 48,
    knowledgeNet: 73,
    quantumIndex: 84,
    activeAgents: 12,
    clock: "--:--:--",
    logs: [
      { time: "--:--:--", msg: "Research Agent · 3 new sources scanned" },
      { time: "--:--:--", msg: "Knowledge graph · 12 nodes added" },
      { time: "--:--:--", msg: "Coding Agent · compilation successful" }
    ]
  };

  if (typeof THREE === 'undefined') {
    fail('Failed to load Three.js — check internet connection, refresh page.');
    return;
  }

  // Smoothstep interpolation helper: returns smooth Hermite interpolation between 0 and 1
  const SS = (a, b, x) => {
    x = Math.max(0, Math.min(1, (x - a) / (b - a)));
    return x * x * (3 - 2 * x);
  };

  // ============================================================================
  // THREE.JS GRAPHICS ENGINE SETUP
  // ============================================================================
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 300);
  camera.position.set(0, 0, 3.9);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x000000, 0);
  document.getElementById('scene').appendChild(renderer.domElement);

  // Structural orientation groups:
  const world = new THREE.Group();
  scene.add(world);
  const spin = new THREE.Group();
  world.add(spin);

  // ============================================================================
  // CANVAS RENDERED PROCEDURAL TEXTURES
  // ============================================================================
  function radialTex(stops) {
    const c = document.createElement('canvas');
    c.width = c.height = 256;
    const x = c.getContext('2d');
    const g = x.createRadialGradient(128, 128, 0, 128, 128, 128);
    stops.forEach(s => g.addColorStop(s[0], s[1]));
    x.fillStyle = g;
    x.fillRect(0, 0, 256, 256);
    return new THREE.CanvasTexture(c);
  }

  // ============================================================================
  // AMBIENT BACKDROP HALO
  // ============================================================================
  let haloMat;
  (function() {
    const t = radialTex([[0, 'rgba(190,70,255,0.55)'], [0.32, 'rgba(150,30,210,0.30)'], [0.62, 'rgba(255,110,40,0.12)'], [1, 'rgba(0,0,0,0)']]);
    haloMat = new THREE.MeshBasicMaterial({ map: t, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending });
    const h = new THREE.Mesh(new THREE.PlaneGeometry(8, 8.6), haloMat);
    h.position.set(0, 0.1, -1.5);
    world.add(h);
  })();

  // ============================================================================
  // COSMIC BACKGROUND STARFIELD
  // ============================================================================
  let starMat, starMesh;
  (function() {
    const n = 2400, pos = new Float32Array(n * 3), sz = new Float32Array(n), ph = new Float32Array(n), tint = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const r = 30 + Math.random() * 55, t = Math.random() * 6.283, p = Math.acos(2 * Math.random() - 1);
      pos[i * 3] = r * Math.sin(p) * Math.cos(t);
      pos[i * 3 + 1] = r * Math.sin(p) * Math.sin(t);
      pos[i * 3 + 2] = r * Math.cos(p);
      const hero = Math.random() < 0.06;
      sz[i] = hero ? (5 + Math.random() * 5) : (1.3 + Math.random() * 2.2);
      ph[i] = Math.random() * 6.28;
      let c = [0.85, 0.92, 1.0];
      const k = Math.random();
      if (k < 0.18) c = [1.0, 0.86, 0.7];
      else if (k < 0.5) c = [0.7, 0.85, 1.0];
      tint[i * 3] = c[0];
      tint[i * 3 + 1] = c[1];
      tint[i * 3 + 2] = c[2];
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('aSize', new THREE.BufferAttribute(sz, 1));
    g.setAttribute('aPhase', new THREE.BufferAttribute(ph, 1));
    g.setAttribute('aTint', new THREE.BufferAttribute(tint, 3));
    starMat = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uOp: { value: 0 } },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexShader: `uniform float uTime;attribute float aSize;attribute float aPhase;attribute vec3 aTint;varying float vTw;varying vec3 vT;
        void main(){vT=aTint;vTw=0.55+0.45*sin(uTime*1.8+aPhase);vec4 mv=modelViewMatrix*vec4(position,1.0);gl_Position=projectionMatrix*mv;gl_PointSize=aSize*(0.7+vTw*0.6);}`,
      fragmentShader: `precision highp float;uniform float uOp;varying float vTw;varying vec3 vT;
        void main(){float d=length(gl_PointCoord-vec2(0.5));float c=pow(smoothstep(0.5,0.0,d),3.5);gl_FragColor=vec4(vT*vTw*1.5,c*uOp);}`
    });
    starMesh = new THREE.Points(g, starMat);
    scene.add(starMesh);
  })();

  // ============================================================================
  // COSMIC EVENT NEBULAS
  // ============================================================================
  const cosmic = [];
  function addCosmic(tex, size, sp) {
    const m = new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending });
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(size, size), m);
    mesh.position.set(0, 0, -50);
    scene.add(mesh);
    cosmic.push({ mesh, mat: m, cur: 0, tgt: 0, idle: true, sp });
  }
  addCosmic(radialTex([[0, 'rgba(150,90,255,0.55)'], [0.4, 'rgba(90,60,200,0.22)'], [1, 'rgba(0,0,0,0)']]), 60, 0.004);
  addCosmic(radialTex([[0, 'rgba(40,220,255,0.45)'], [0.45, 'rgba(30,120,200,0.18)'], [1, 'rgba(0,0,0,0)']]), 70, -0.003);
  addCosmic(radialTex([[0, 'rgba(255,80,170,0.4)'], [0.4, 'rgba(160,40,140,0.16)'], [1, 'rgba(0,0,0,0)']]), 55, 0.005);

  function triggerCosmic() {
    const free = cosmic.filter(c => c.idle);
    if (free.length) {
      const c = free[(Math.random() * free.length) | 0];
      c.idle = false;
      c.mesh.position.set((Math.random() - 0.5) * 70, (Math.random() - 0.5) * 40, -42 - Math.random() * 30);
      c.tgt = 0.35 + Math.random() * 0.5;
      setTimeout(() => {
        c.tgt = 0;
        setTimeout(() => c.idle = true, 6000);
      }, 7000 + Math.random() * 9000);
    }
    setTimeout(triggerCosmic, 9000 + Math.random() * 13000);
  }
  setTimeout(triggerCosmic, 4000);

  // ============================================================================
  // PARTICLE DATA LAYOUT BUFFERS
  // ============================================================================
  const PX = [], PR = [], PT = [], PB = [], PH = [], PE = [], PSeed = [], PXg = [];
  const SX = 1.0, SY = 1.18, SZ = 0.92; // 3D dimensions to shape the volumetric sphere into an oval head

  // ============================================================================
  // FACIAL RELIEF DETAILED DEFORMATION
  // ============================================================================
  function relief(x, y, z) {
    if (z <= 0.12) return 0;
    let d = 0;
    d += Math.exp(-(x * x) / 0.012) * Math.exp(-((y - 0.02) * (y - 0.02)) / 0.06) * 0.13; // Nose bridge and nose tip elevation
    d += Math.exp(-(x * x) / 0.006) * Math.exp(-((y + 0.06) * (y + 0.06)) / 0.004) * 0.05; // Upper lip/mouth prominence
    d += Math.exp(-((y - 0.34) * (y - 0.34)) / 0.004) * Math.exp(-(x * x) / 0.12) * 0.05; // Forehead structure
    d += Math.exp(-((Math.abs(x) - 0.42) * (Math.abs(x) - 0.42)) / 0.02) * Math.exp(-((y + 0.04) * (y + 0.04)) / 0.05) * 0.045; // Cheekbones
    d += Math.exp(-((y + 0.34) * (y + 0.34)) / 0.006) * Math.exp(-(x * x) / 0.05) * 0.04; // Chin projection
    d += Math.exp(-((y + 0.62) * (y + 0.62)) / 0.01) * Math.exp(-(x * x) / 0.04) * 0.05; // Neck base contour
    return d * SS(0.12, 0.4, z);
  }

  // ============================================================================
  // GIGA CHAD FACE SHAPE TRANSFORMATION DEFINITION
  // ============================================================================
  function gigaTransform(x, y, z) {
    let gx = x, gy = y, gz = z;
    if (z > 0.05) {
      if (gy < 0.1 && gy > -0.65) {
        let jawCorner = Math.exp(-((gy + 0.35) * (gy + 0.35)) / 0.035);
        gx += Math.sign(gx) * 0.28 * jawCorner;
        gz += 0.12 * jawCorner;
      }
      let cheekbone = Math.exp(-((gy - 0.08) * (gy - 0.08)) / 0.015) * Math.exp(-((Math.abs(gx) - 0.44) * (Math.abs(gx) - 0.44)) / 0.02);
      gx += Math.sign(gx) * 0.20 * cheekbone;
      gz += 0.16 * cheekbone;
      let hollow = Math.exp(-((gy + 0.18) * (gy + 0.18)) / 0.025) * Math.exp(-((Math.abs(gx) - 0.32) * (Math.abs(gx) - 0.32)) / 0.03);
      gx -= Math.sign(gx) * 0.09 * hollow;
      gz -= 0.12 * hollow;
      let chin = Math.exp(-((gy + 0.65) * (gy + 0.65)) / 0.015) * Math.exp(-(gx * gx) / 0.03);
      gz += 0.24 * chin * (1.0 - 0.45 * Math.exp(-(gx * gx) / 0.0035));
      gx += gx * 0.32 * Math.exp(-((gy + 0.65) * (gy + 0.65)) / 0.015);
      let brow = Math.exp(-((gy - 0.38) * (gy - 0.38)) / 0.008) * Math.exp(-(gx * gx) / 0.10);
      gz += 0.10 * brow;
      let lips = Math.exp(-((gy + 0.35) * (gy + 0.35)) / 0.004) * Math.exp(-(gx * gx) / 0.05);
      gz += 0.04 * lips;
    }
    return [gx, gy, gz];
  }

  // ============================================================================
  // EYE SOCKET PARAMETERS
  // ============================================================================
  const dirL = new THREE.Vector3(-0.33, 0.15, 0.93).normalize(), dirR = new THREE.Vector3(0.33, 0.15, 0.93).normalize();
  const cL = new THREE.Vector3(dirL.x * SX, dirL.y * SY, dirL.z * SZ), cR = new THREE.Vector3(dirR.x * SX, dirR.y * SY, dirR.z * SZ);
  const socketR = 0.30;

  // ============================================================================
  // PARTICLE BUFFER REGISTRATION
  // ============================================================================
  function pushP(x, y, z, t, b, e) {
    PX.push(x, y, z);
    const rl = 0.6 + Math.random();
    PR.push((Math.random() * 2 - 1) * rl, (Math.random() * 2 - 1) * rl, (Math.random() * 2 - 1) * rl);
    PT.push(t);
    PB.push(b);
    PH.push((Math.random() - 0.5) * 0.6);
    PE.push(e);
    PSeed.push(Math.random() * 6.28);
    const [gx, gy, gz] = gigaTransform(x, y, z);
    PXg.push(gx, gy, gz);
  }

  // ============================================================================
  // REAL HEAD MESH (preferred) — a real 3D face scan (CC-BY, Lee Perry-Smith
  // "Infinite" head, bundled with three.js's own examples) sampled vertex by
  // vertex, instead of guessing anatomy from spheres and hand-tuned bumps.
  // Falls back to the old procedural sphere if the model can't load.
  // ============================================================================
  let headVerts = null;
  // TEMPORALMENTE DESACTIVADO: la extraccion de vertices del modelo real
  // producia un patron caotico (probablemente la malla trae ojos/dientes u
  // otras piezas separadas con vertices muy alejados del contorno de la
  // cabeza, o falta aplicar la matriz de transformacion del nodo). Se queda
  // el codigo listo para depurar con calma; por ahora usa el procedural
  // (probado, coherente) para no dejar la vista rota.
  const REAL_MESH_ENABLED = false;
  try {
    if (!REAL_MESH_ENABLED) throw new Error('real mesh disabled for now');
    const loader = new THREE.GLTFLoader();
    const gltf = await new Promise((resolve, reject) => loader.load('models/head.glb', resolve, undefined, reject));
    let mesh = null;
    gltf.scene.traverse(o => { if (!mesh && o.isMesh) mesh = o; });
    if (mesh) {
      const pos = mesh.geometry.attributes.position;
      mesh.geometry.computeBoundingBox();
      const bb = mesh.geometry.boundingBox, center = bb.min.clone().add(bb.max).multiplyScalar(0.5);
      const size = new THREE.Vector3().subVectors(bb.max, bb.min);
      const scale = 2.05 / size.y; // fit the scanned head's height to our head zone
      headVerts = [];
      const stride = Math.max(1, Math.floor(pos.count / 9000)); // cap density near the old particle count
      for (let i = 0; i < pos.count; i += stride) {
        headVerts.push(
          (pos.getX(i) - center.x) * scale,
          (pos.getY(i) - center.y) * scale + 0.02,
          (pos.getZ(i) - center.z) * scale,
        );
      }
    }
  } catch (e) { console.warn('real head mesh failed to load, using procedural fallback', e); }

  // ============================================================================
  // FIBONACCI GOLDEN SPIRAL SPHERE SAMPLING (fallback only)
  // ============================================================================
  const NB = 9000, GA = Math.PI * (3 - Math.sqrt(5)), v = new THREE.Vector3(), nrm = new THREE.Vector3();
  if (headVerts) {
    for (let i = 0; i < headVerts.length; i += 3) {
      v.set(headVerts[i], headVerts[i + 1], headVerts[i + 2]);
      const dm = Math.min(v.distanceTo(cL), v.distanceTo(cR));
      let b = 0.8 + Math.random() * 0.25, edge = 0.55 + 0.35 * Math.random();
      if (dm < socketR * 0.7 && v.z > 0.2) { const tt = 1 - dm / (socketR * 0.7); b *= (1 - tt * 0.9); }
      pushP(v.x, v.y, v.z, 0, b, edge);
    }
  } else {
    for (let i = 0; i < NB; i++) {
      const uy = 1 - (i / (NB - 1)) * 2, rr = Math.sqrt(1 - uy * uy), th = GA * i;
      let ux = Math.cos(th) * rr, uz = Math.sin(th) * rr;

      const rad = (Math.random() < 0.30) ? (0.45 + Math.random() * 0.5) : 1.0;
      v.set(ux * rad * SX, uy * rad * SY, uz * rad * SZ);
      nrm.copy(v).normalize();

      v.addScaledVector(nrm, relief(v.x, v.y, v.z));

      let b = 0.8 + Math.random() * 0.25;
      const dm = Math.min(v.distanceTo(cL), v.distanceTo(cR));
      if (dm < socketR && v.z > 0.2) {
        const tt = 1 - dm / socketR;
        v.addScaledVector(nrm, -tt * tt * 0.30);
        b = 0.8 * (1 - tt * 0.95);
      }

      let edge = Math.pow(SS(-0.6, 1.05, uy), 1.05);
      if (rad >= 1) edge = Math.max(edge, 0.32 * (0.5 + 0.5 * Math.abs(ux)));
      if (v.z > 0.35 && uy < 0.4) edge *= 0.4;
      pushP(v.x, v.y, v.z, 0, b, edge);
    }
  }

  // ============================================================================
  // TORSO / SHOULDERS — the original only ever built a floating head; this
  // extends it down into a bust so it reads as a humanoid, not a skull.
  // Same sampling language as the head (mostly on the outer "shell", a
  // fraction scattered inward for volume) so it matches the existing look.
  // ============================================================================
  const NT = 5200;
  for (let i = 0; i < NT; i++) {
    const yt = Math.random(); // 0 = neck, 1 = base of the bust
    const ty = -1.02 - yt * 1.65;
    const flare = Math.min(1, yt / 0.22); // shoulders flare out quickly near the neck
    const halfW = (0.30 + flare * 1.28) * SX;
    const halfD = (0.30 + flare * 0.82) * SZ;
    const rad = (Math.random() < 0.35) ? (0.4 + Math.random() * 0.5) : 1.0;
    const a = Math.random() * 6.28;
    const tx = Math.cos(a) * rad * halfW, tz = Math.sin(a) * rad * halfD;
    const edge = rad >= 1 ? (0.5 + 0.5 * Math.abs(Math.cos(a))) : 0.28;
    const b = 0.75 + Math.random() * 0.25;
    pushP(tx, ty, tz, 0, b, edge);
  }

  // Eye cores — subtle warm glints, not solid white saucers
  function eyeCore(c) {
    const cc = c.clone().multiplyScalar(0.80);
    for (let i = 0; i < 40; i++) {
      const a = Math.random() * 6.28, rd = Math.pow(Math.random(), .5) * 0.05;
      pushP(cc.x + Math.cos(a) * rd, cc.y + Math.sin(a) * rd * 0.9, cc.z + (Math.random() - .5) * 0.04, 1, 1.5, 0.0);
    }
  }
  eyeCore(cL);
  eyeCore(cR);

  // Bind compiled arrays to BufferGeometry
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(PX), 3));
  geo.setAttribute('aRandom', new THREE.BufferAttribute(new Float32Array(PR), 3));
  geo.setAttribute('aType', new THREE.BufferAttribute(new Float32Array(PT), 1));
  geo.setAttribute('aBright', new THREE.BufferAttribute(new Float32Array(PB), 1));
  geo.setAttribute('aHue', new THREE.BufferAttribute(new Float32Array(PH), 1));
  geo.setAttribute('aEdge', new THREE.BufferAttribute(new Float32Array(PE), 1));
  geo.setAttribute('aSeed', new THREE.BufferAttribute(new Float32Array(PSeed), 1));
  geo.setAttribute('aGigaTarget', new THREE.BufferAttribute(new Float32Array(PXg), 3));

  const NP = PX.length / 3;

  // ============================================================================
  // BLACK HOLE CONSTELLATION TARGETS ("SHOW" MODE)
  // ============================================================================
  const TGT = new Float32Array(NP * 3);
  const galaxyNodes = [
    new THREE.Vector3(0, 0, 0),         // Singular core
    new THREE.Vector3(0.65, 0.05, 0.4),  // Accretion disk inner boundary
    new THREE.Vector3(-1.6, -0.05, -1.0),// Accretion disk outer boundary
    new THREE.Vector3(0, 1.9, 0),        // Relativistic northern jet emitter
    new THREE.Vector3(0, -1.9, 0)        // Relativistic southern jet emitter
  ];

  function buildShowTargets() {
    for (let i = 0; i < NP; i++) {
      const type = i % 100;
      if (type < 12) {
        const sign = Math.random() < 0.5 ? 1.0 : -1.0;
        const y = (0.2 + Math.random() * 3.0) * sign;
        const rad = 0.05 * (1.0 + Math.abs(y) * 0.5);
        const theta = Math.random() * Math.PI * 2;
        const r = Math.pow(Math.random(), 0.5) * rad;
        TGT[i * 3] = r * Math.cos(theta);
        TGT[i * 3 + 1] = y;
        TGT[i * 3 + 2] = r * Math.sin(theta);
      } else if (type < 28) {
        const r = Math.pow(Math.random(), 0.5) * 0.22;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(Math.random() * 2 - 1);
        TGT[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        TGT[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
        TGT[i * 3 + 2] = r * Math.cos(phi);
      } else {
        const r = 0.32 + Math.pow(Math.random(), 1.6) * 2.4;
        const theta = Math.random() * Math.PI * 2;
        TGT[i * 3] = r * Math.cos(theta);
        TGT[i * 3 + 1] = (Math.random() - 0.5) * 0.05;
        TGT[i * 3 + 2] = r * Math.sin(theta);
      }
    }
  }
  buildShowTargets();

  geo.setAttribute('aTarget', new THREE.BufferAttribute(TGT, 3));

  const dragonLines = { material: { opacity: 0 } };

  // ============================================================================
  // CONSTELLATION NODE MESHES
  // ============================================================================
  const starNodeGeo = new THREE.BufferGeometry();
  const starNodePositions = [];
  galaxyNodes.forEach(node => {
    starNodePositions.push(node.x, node.y, node.z);
  });
  starNodeGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(starNodePositions), 3));

  const starNodeTex = radialTex([
    [0, 'rgba(255, 255, 255, 1.0)'],
    [0.18, 'rgba(34, 233, 255, 0.9)'],
    [0.45, 'rgba(34, 233, 255, 0.25)'],
    [1, 'rgba(0, 0, 0, 0)']
  ]);
  const starNodeMat = new THREE.PointsMaterial({
    size: 0.16,
    map: starNodeTex,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    depthWrite: false
  });
  const constellationStars = new THREE.Points(starNodeGeo, starNodeMat);
  spin.add(constellationStars);

  // ============================================================================
  // DUAL HOLOGRAM SCREENS CREATION & CANVASES
  // ============================================================================
  // 1. RIGHT HOLOGRAM (Agent telemetry)
  const holoCanvas = document.createElement('canvas');
  holoCanvas.width = 512;
  holoCanvas.height = 512;
  const holoCtx = holoCanvas.getContext('2d');
  const holoTex = new THREE.CanvasTexture(holoCanvas);

  const holoGeo = new THREE.PlaneGeometry(1.6, 1.6);
  const holoMat = new THREE.MeshBasicMaterial({
    map: holoTex,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
    depthWrite: false
  });
  const holoMesh = new THREE.Mesh(holoGeo, holoMat);
  holoMesh.position.set(3, 1, -3);
  holoMesh.rotation.set(0, -Math.PI / 4, 0);
  holoMesh.scale.set(0.1, 0.1, 0.1);
  world.add(holoMesh);

  // 2. LEFT HOLOGRAM (System Core diagnostics)
  const holoCanvasLeft = document.createElement('canvas');
  holoCanvasLeft.width = 512;
  holoCanvasLeft.height = 512;
  const holoCtxLeft = holoCanvasLeft.getContext('2d');
  const holoTexLeft = new THREE.CanvasTexture(holoCanvasLeft);

  const holoMatLeft = new THREE.MeshBasicMaterial({
    map: holoTexLeft,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
    depthWrite: false
  });
  const holoMeshLeft = new THREE.Mesh(holoGeo, holoMatLeft);
  holoMeshLeft.position.set(-3, 1, -3);
  holoMeshLeft.rotation.set(0, Math.PI / 4, 0);
  holoMeshLeft.scale.set(0.1, 0.1, 0.1);
  world.add(holoMeshLeft);

  let holoActive = false;

  const holoTargetPos = new THREE.Vector3(3.0, 1.2, -3.0);
  const holoTargetRot = new THREE.Vector3(0, -Math.PI / 3, 0.1);
  const holoTargetPosLeft = new THREE.Vector3(-3.0, 1.2, -3.0);
  const holoTargetRotLeft = new THREE.Vector3(0, Math.PI / 3, -0.1);
  let holoTargetScale = 0.05;

  const holoAgents = [
    { name: "RESEARCH AGENT", job: "SCANNING SOURCES", prg: 62, speed: 0.8 },
    { name: "CODING AGENT", job: "COMPILING UTILS", prg: 48, speed: 0.4 },
    { name: "DESIGN AGENT", job: "OPTIMIZING DOM", prg: 73, speed: 0.6 },
    { name: "DECISION CORE", job: "SOLVING QUBITS", prg: 84, speed: 0.3 }
  ];

  function drawHologram(t, dt) {
    if (!holoActive && holoMat.opacity < 0.01) return;
    holoCtx.clearRect(0, 0, 512, 512);

    // Background
    holoCtx.fillStyle = "rgba(10, 16, 38, 0.4)";
    holoCtx.fillRect(10, 10, 492, 492);

    // Borders
    holoCtx.strokeStyle = "rgba(34, 233, 255, 0.4)";
    holoCtx.lineWidth = 2;
    holoCtx.strokeRect(15, 15, 482, 482);
    holoCtx.strokeStyle = "rgba(34, 233, 255, 0.8)";
    holoCtx.lineWidth = 1;
    holoCtx.strokeRect(20, 20, 472, 472);

    // Corners
    holoCtx.fillStyle = "rgba(34, 233, 255, 0.9)";
    holoCtx.fillRect(20, 20, 30, 6); holoCtx.fillRect(20, 20, 6, 30);
    holoCtx.fillRect(462, 20, 30, 6); holoCtx.fillRect(486, 20, 6, 30);
    holoCtx.fillRect(20, 486, 30, 6); holoCtx.fillRect(20, 462, 6, 30);
    holoCtx.fillRect(462, 486, 30, 6); holoCtx.fillRect(486, 462, 6, 30);

    // Grid
    holoCtx.strokeStyle = "rgba(34, 233, 255, 0.06)";
    holoCtx.lineWidth = 1;
    const gridSpacing = 32;
    for (let x = gridSpacing; x < 512; x += gridSpacing) {
      holoCtx.beginPath(); holoCtx.moveTo(x, 20); holoCtx.lineTo(x, 492); holoCtx.stroke();
    }
    for (let y = gridSpacing; y < 512; y += gridSpacing) {
      holoCtx.beginPath(); holoCtx.moveTo(20, y); holoCtx.lineTo(492, y); holoCtx.stroke();
    }

    // Title
    holoCtx.font = "bold 20px 'Orbitron', sans-serif";
    holoCtx.fillStyle = "rgba(34, 233, 255, 0.95)";
    holoCtx.shadowColor = "rgba(34, 233, 255, 0.8)";
    holoCtx.shadowBlur = 10;
    holoCtx.fillText("JARVIS AGENTS REGISTRY", 42, 64);
    holoCtx.shadowBlur = 0;

    let pulseOpacity = 0.5 + 0.5 * Math.sin(t * 8);
    holoCtx.fillStyle = "rgba(34, 233, 255, " + pulseOpacity + ")";
    holoCtx.beginPath(); holoCtx.arc(430, 56, 7, 0, Math.PI * 2); holoCtx.fill();

    holoCtx.fillStyle = "rgba(34, 233, 255, 0.3)";
    holoCtx.fillRect(40, 84, 432, 2);

    // Draw Agent Bars
    let startY = 130;
    holoAgents.forEach((agent, idx) => {
      agent.prg += dt * 10 * agent.speed;
      if (agent.prg > 100) agent.prg = 0;

      holoCtx.font = "14px 'Orbitron', sans-serif";
      holoCtx.fillStyle = "rgba(138, 91, 255, 0.8)";
      holoCtx.fillText("AG_0" + (idx + 1), 44, startY);

      holoCtx.font = "bold 15px 'Chakra Petch', sans-serif";
      holoCtx.fillStyle = "#eafbff";
      holoCtx.fillText(agent.name, 110, startY);

      holoCtx.font = "12px 'Orbitron', sans-serif";
      holoCtx.fillStyle = "rgba(34, 233, 255, 0.7)";
      holoCtx.fillText(agent.job, 110, startY + 20);

      let barX = 290, barY = startY - 12, barW = 160, barH = 10;
      holoCtx.fillStyle = "rgba(255, 255, 255, 0.08)";
      holoCtx.fillRect(barX, barY, barW, barH);
      
      let prgWidth = (agent.prg / 100) * barW;
      let prgGrad = holoCtx.createLinearGradient(barX, barY, barX + barW, barY);
      prgGrad.addColorStop(0, '#00c8ff');
      prgGrad.addColorStop(1, '#22e9ff');
      holoCtx.fillStyle = prgGrad;
      holoCtx.fillRect(barX, barY, prgWidth, barH);

      holoCtx.font = "12px 'Orbitron', sans-serif";
      holoCtx.fillStyle = "#eafbff";
      holoCtx.fillText(Math.floor(agent.prg) + "%", barX + barW + 12, barY + 10);

      holoCtx.fillStyle = "rgba(255, 255, 255, 0.05)";
      holoCtx.fillRect(40, startY + 34, 432, 1);
      startY += 62;
    });

    // Console logs stream
    let logStartY = startY;
    holoCtx.fillStyle = "rgba(34, 233, 255, 0.07)";
    holoCtx.fillRect(40, logStartY, 432, 70);
    holoCtx.strokeStyle = "rgba(34, 233, 255, 0.25)";
    holoCtx.strokeRect(40, logStartY, 432, 70);

    holoCtx.font = "bold 11px 'Orbitron', sans-serif";
    holoCtx.fillStyle = "rgba(34, 233, 255, 0.95)";
    holoCtx.fillText("LIVE AGENT FEED STREAM", 52, logStartY + 20);

    holoCtx.font = "11px 'Chakra Petch', sans-serif";
    if (window.sysTelemetry && window.sysTelemetry.logs && window.sysTelemetry.logs.length > 0) {
      let logsToDraw = window.sysTelemetry.logs.slice(0, 2);
      logsToDraw.forEach((item, lIdx) => {
        holoCtx.fillStyle = "rgba(34, 233, 255, 0.7)";
        holoCtx.fillText("[" + item.time + "]", 52, logStartY + 40 + lIdx * 20);
        holoCtx.fillStyle = "rgba(255, 255, 255, 0.85)";
        holoCtx.fillText(item.msg, 125, logStartY + 40 + lIdx * 20);
      });
    } else {
      holoCtx.fillStyle = "rgba(255, 255, 255, 0.4)";
      holoCtx.fillText("Awaiting agent system signals...", 52, logStartY + 40);
    }

    // Scanlines
    holoCtx.fillStyle = "rgba(34, 233, 255, 0.015)";
    let scanlineY = (t * 70) % 472 + 20;
    holoCtx.fillRect(20, scanlineY, 472, 8);

    holoCtx.strokeStyle = "rgba(34, 233, 255, 0.05)";
    holoCtx.lineWidth = 0.5;
    for (let i = 0; i < 6; i++) {
      let randY = 22 + Math.random() * 468;
      holoCtx.beginPath(); holoCtx.moveTo(22, randY); holoCtx.lineTo(490, randY); holoCtx.stroke();
    }

    holoTex.needsUpdate = true;
  }

  function drawHologramLeft(t, dt) {
    if (!holoActive && holoMatLeft.opacity < 0.01) return;
    holoCtxLeft.clearRect(0, 0, 512, 512);

    // Background
    holoCtxLeft.fillStyle = "rgba(10, 16, 38, 0.4)";
    holoCtxLeft.fillRect(10, 10, 492, 492);

    // Borders
    holoCtxLeft.strokeStyle = "rgba(34, 233, 255, 0.4)";
    holoCtxLeft.lineWidth = 2;
    holoCtxLeft.strokeRect(15, 15, 482, 482);
    holoCtxLeft.strokeStyle = "rgba(34, 233, 255, 0.8)";
    holoCtxLeft.lineWidth = 1;
    holoCtxLeft.strokeRect(20, 20, 472, 472);

    // Corners
    holoCtxLeft.fillStyle = "rgba(34, 233, 255, 0.9)";
    holoCtxLeft.fillRect(20, 20, 30, 6); holoCtxLeft.fillRect(20, 20, 6, 30);
    holoCtxLeft.fillRect(462, 20, 30, 6); holoCtxLeft.fillRect(486, 20, 6, 30);
    holoCtxLeft.fillRect(20, 486, 30, 6); holoCtxLeft.fillRect(20, 462, 6, 30);
    holoCtxLeft.fillRect(462, 486, 30, 6); holoCtxLeft.fillRect(486, 462, 6, 30);

    // Grid
    holoCtxLeft.strokeStyle = "rgba(34, 233, 255, 0.06)";
    holoCtxLeft.lineWidth = 1;
    const gridSpacing = 32;
    for (let x = gridSpacing; x < 512; x += gridSpacing) {
      holoCtxLeft.beginPath(); holoCtxLeft.moveTo(x, 20); holoCtxLeft.lineTo(x, 492); holoCtxLeft.stroke();
    }
    for (let y = gridSpacing; y < 512; y += gridSpacing) {
      holoCtxLeft.beginPath(); holoCtxLeft.moveTo(20, y); holoCtxLeft.lineTo(492, y); holoCtxLeft.stroke();
    }

    // Title
    holoCtxLeft.font = "bold 20px 'Orbitron', sans-serif";
    holoCtxLeft.fillStyle = "rgba(34, 233, 255, 0.95)";
    holoCtxLeft.shadowColor = "rgba(34, 233, 255, 0.8)";
    holoCtxLeft.shadowBlur = 10;
    holoCtxLeft.fillText("SYSTEM CORE PERFORMANCE", 42, 64);
    holoCtxLeft.shadowBlur = 0;

    // Clock
    holoCtxLeft.font = "14px 'Orbitron', sans-serif";
    holoCtxLeft.fillStyle = "rgba(138, 91, 255, 0.9)";
    let clockStr = window.sysTelemetry ? window.sysTelemetry.clock : "--:--:--";
    holoCtxLeft.fillText("SYS CLOCK: " + clockStr, 320, 60);

    holoCtxLeft.fillStyle = "rgba(34, 233, 255, 0.3)";
    holoCtxLeft.fillRect(40, 84, 432, 2);

    // Metrics
    let aiP = window.sysTelemetry ? window.sysTelemetry.aiProcess : 0;
    let mem = window.sysTelemetry ? window.sysTelemetry.memory : 0;
    let net = window.sysTelemetry ? window.sysTelemetry.knowledgeNet : 0;
    let qbt = window.sysTelemetry ? window.sysTelemetry.quantumIndex : 0;

    const metrics = [
      { label: "AI PROCESS CORE", val: aiP },
      { label: "MEMORY CAPACITY", val: mem },
      { label: "KNOWLEDGE INDEX", val: net },
      { label: "QUANTUM INDEX", val: qbt }
    ];

    let startY = 130;
    metrics.forEach((metric) => {
      holoCtxLeft.font = "bold 14px 'Chakra Petch', sans-serif";
      holoCtxLeft.fillStyle = "#eafbff";
      holoCtxLeft.fillText(metric.label, 44, startY);

      let barX = 230, barY = startY - 12, barW = 180, barH = 10;
      holoCtxLeft.fillStyle = "rgba(255, 255, 255, 0.08)";
      holoCtxLeft.fillRect(barX, barY, barW, barH);

      let prgWidth = (metric.val / 100) * barW;
      let prgGrad = holoCtxLeft.createLinearGradient(barX, barY, barX + barW, barY);
      prgGrad.addColorStop(0, '#00c8ff');
      prgGrad.addColorStop(1, '#22e9ff');
      holoCtxLeft.fillStyle = prgGrad;
      holoCtxLeft.fillRect(barX, barY, prgWidth, barH);

      holoCtxLeft.font = "12px 'Orbitron', sans-serif";
      holoCtxLeft.fillStyle = "#eafbff";
      holoCtxLeft.fillText(metric.val.toFixed(0) + "%", barX + barW + 12, barY + 10);

      holoCtxLeft.fillStyle = "rgba(255, 255, 255, 0.05)";
      holoCtxLeft.fillRect(40, startY + 24, 432, 1);
      startY += 52;
    });

    // Sine Oscillator Wave
    let waveY = 390;
    holoCtxLeft.fillStyle = "rgba(34, 233, 255, 0.04)";
    holoCtxLeft.fillRect(40, waveY, 432, 85);
    holoCtxLeft.strokeStyle = "rgba(34, 233, 255, 0.25)";
    holoCtxLeft.strokeRect(40, waveY, 432, 85);

    holoCtxLeft.font = "11px 'Chakra Petch', sans-serif";
    holoCtxLeft.fillStyle = "rgba(34, 233, 255, 0.9)";
    holoCtxLeft.fillText("CORE GRAVITY OSCILLATOR", 52, waveY + 20);

    holoCtxLeft.strokeStyle = "rgba(34, 233, 255, 0.85)";
    holoCtxLeft.lineWidth = 1.5;
    holoCtxLeft.shadowColor = "rgba(34, 233, 255, 0.7)";
    holoCtxLeft.shadowBlur = 6;
    holoCtxLeft.beginPath();
    
    let first = true;
    for (let x = 40; x <= 472; x++) {
      let relativeX = x - 40;
      let sineVal = Math.sin(relativeX * 0.035 + t * 8.5) * 16 + Math.sin(relativeX * 0.12 - t * 4) * 6;
      let drawY = waveY + 50 + sineVal;
      if (first) {
        holoCtxLeft.moveTo(x, drawY);
        first = false;
      } else {
        holoCtxLeft.lineTo(x, drawY);
      }
    }
    holoCtxLeft.stroke();
    holoCtxLeft.shadowBlur = 0;

    // Scanlines
    holoCtxLeft.fillStyle = "rgba(34, 233, 255, 0.015)";
    let scanlineY = (t * 60) % 472 + 20;
    holoCtxLeft.fillRect(20, scanlineY, 472, 8);

    holoTexLeft.needsUpdate = true;
  }

  // Audio effects (futuristic holographic sound)
  let audioCtx = null;
  function playHoloSound(open) {
    let ctx = audioCtx;
    if (!ctx) {
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      } catch(e) { return; }
    }
    if (ctx.state === 'suspended') {
      ctx.resume().catch(()=>{});
    }
    const tNow = ctx.currentTime;
    
    if (open) {
      // Sub bass resonance surge
      const sub = ctx.createOscillator();
      const subGain = ctx.createGain();
      sub.type = 'sine';
      sub.frequency.setValueAtTime(65, tNow);
      sub.frequency.exponentialRampToValueAtTime(160, tNow + 0.35);
      subGain.gain.setValueAtTime(0, tNow);
      subGain.gain.linearRampToValueAtTime(0.22, tNow + 0.04);
      subGain.gain.exponentialRampToValueAtTime(0.001, tNow + 0.5);
      sub.connect(subGain); subGain.connect(ctx.destination);
      sub.start(tNow); sub.stop(tNow + 0.52);

      // Resonant holographic sweep
      const osc = ctx.createOscillator();
      const filter = ctx.createBiquadFilter();
      const oscGain = ctx.createGain();
      osc.type = 'sawtooth';
      filter.type = 'bandpass';
      filter.Q.value = 5.0;
      osc.frequency.setValueAtTime(220, tNow);
      osc.frequency.exponentialRampToValueAtTime(880, tNow + 0.4);
      filter.frequency.setValueAtTime(300, tNow);
      filter.frequency.exponentialRampToValueAtTime(2400, tNow + 0.38);
      oscGain.gain.setValueAtTime(0, tNow);
      oscGain.gain.linearRampToValueAtTime(0.12, tNow + 0.05);
      oscGain.gain.exponentialRampToValueAtTime(0.001, tNow + 0.48);
      osc.connect(filter); filter.connect(oscGain); oscGain.connect(ctx.destination);
      osc.start(tNow); osc.stop(tNow + 0.5);

      // Crystal chime arpeggio
      const freqs = [1046.5, 1318.5, 1567.98, 2093.0];
      freqs.forEach((f, idx) => {
        const chime = ctx.createOscillator();
        const chimeGain = ctx.createGain();
        const tStart = tNow + 0.08 + idx * 0.06;
        chime.type = 'sine';
        chime.frequency.setValueAtTime(f, tStart);
        chimeGain.gain.setValueAtTime(0, tStart);
        chimeGain.gain.linearRampToValueAtTime(0.07, tStart + 0.015);
        chimeGain.gain.exponentialRampToValueAtTime(0.0001, tStart + 0.45);
        chime.connect(chimeGain); chimeGain.connect(ctx.destination);
        chime.start(tStart); chime.stop(tStart + 0.5);
      });
    } else {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(980, tNow);
      osc.frequency.exponentialRampToValueAtTime(140, tNow + 0.28);
      gain.gain.setValueAtTime(0.12, tNow);
      gain.gain.exponentialRampToValueAtTime(0.001, tNow + 0.30);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(tNow); osc.stop(tNow + 0.32);
    }
  }

  function playExplosionSound() {
    let ctx = audioCtx;
    if (!ctx) {
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      } catch(e) { return; }
    }
    if (ctx.state === 'suspended') {
      ctx.resume().catch(()=>{});
    }
    const tNow = ctx.currentTime;
    
    const osc1 = ctx.createOscillator();
    const gain1 = ctx.createGain();
    osc1.type = 'triangle';
    osc1.frequency.setValueAtTime(150, tNow);
    osc1.frequency.exponentialRampToValueAtTime(30, tNow + 1.2);
    gain1.gain.setValueAtTime(0.4, tNow);
    gain1.gain.exponentialRampToValueAtTime(0.001, tNow + 1.5);
    
    const bufferSize = ctx.sampleRate * 1.5;
    const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) {
      data[i] = Math.random() * 2 - 1;
    }
    const noiseNode = ctx.createBufferSource();
    noiseNode.buffer = buffer;
    
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(800, tNow);
    filter.frequency.exponentialRampToValueAtTime(80, tNow + 1.2);
    filter.Q.setValueAtTime(4, tNow);
    
    const gain2 = ctx.createGain();
    gain2.gain.setValueAtTime(0.3, tNow);
    gain2.gain.exponentialRampToValueAtTime(0.001, tNow + 1.4);
    
    const osc3 = ctx.createOscillator();
    const gain3 = ctx.createGain();
    osc3.type = 'sawtooth';
    osc3.frequency.setValueAtTime(80, tNow);
    osc3.frequency.exponentialRampToValueAtTime(1500, tNow + 0.4);
    gain3.gain.setValueAtTime(0.12, tNow);
    gain3.gain.exponentialRampToValueAtTime(0.001, tNow + 0.5);
    
    osc1.connect(gain1);
    gain1.connect(ctx.destination);
    
    noiseNode.connect(filter);
    filter.connect(gain2);
    gain2.connect(ctx.destination);
    
    osc3.connect(gain3);
    gain3.connect(ctx.destination);
    
    osc1.start(tNow); osc1.stop(tNow + 1.6);
    noiseNode.start(tNow); noiseNode.stop(tNow + 1.6);
    osc3.start(tNow); osc3.stop(tNow + 0.6);
  }

  function explainStatus() {
    if (!window.sysTelemetry) return;
    const ai = Math.round(window.sysTelemetry.aiProcess);
    const mem = Math.round(window.sysTelemetry.memory);
    const agCount = window.sysTelemetry.activeAgents || 12;
    const text = "Holographic telemetry active. Core AI processing speed is at " + ai + " percent, memory capacity is at " + mem + " percent, and " + agCount + " automated agents are currently running in the registry.";
    speak(text);
  }

  function startOnboarding() {
    perf = { name: 'greet', t: 0, dur: 5.5 };
    
    setTimeout(() => {
      playExplosionSound();
      const intro = document.getElementById('intro');
      if (intro) {
        intro.style.opacity = '0';
        setTimeout(() => intro.classList.add('gone'), 1600);
      }
    }, 1375);

    let idx = 0;
    const texts = [
      "SINGULARITY DETECTED...",
      "CORE IGNITION UNLEASHED!",
      "BIG BANG COALESCENCE ACTIVE...",
      "ALL SYSTEMS ACTIVE."
    ];
    function next() {
      if (!perf || perf.name !== 'greet') return;
      if (idx < texts.length) {
        type(texts[idx], () => {
          later(() => {
            clearSub();
            idx++;
            later(next, 100);
          }, idx === 0 ? 600 : (idx === 1 ? 1400 : 1200));
        });
      } else {
        later(() => {
          speakSeq(["Welcome home, sir.", "All systems online. Ready."]);
        }, 200);
      }
    }
    next();
  }

  function toggleHolo() {
    holoActive = !holoActive;
    playHoloSound(holoActive);
    const btn = document.getElementById('bHolo');
    if (btn) {
      if (holoActive) {
        btn.classList.add('holo-active-btn');
        explainStatus();
      } else {
        btn.classList.remove('holo-active-btn');
        clearTimers();
        speaking = false;
        clearSub();
        setState('IDLE');
        if (window.speechSynthesis) {
          window.speechSynthesis.cancel();
        }
      }
    }
  }

  // ============================================================================
  // SHADER UNIFORM SYSTEMS (GPU STATE CONTROL)
  // ============================================================================
  const U = {
    uTime: { value: 0 },
    uScale: { value: window.innerHeight * 0.62 },
    uSize: { value: 0.020 },
    uAlpha: { value: 1.0 },
    uScatter: { value: 1.9 },
    uContract: { value: 0 },
    uLeanZ: { value: 0 },
    uFlow: { value: 1.0 },
    uBreath: { value: 1 },
    uIntensity: { value: 0 },
    uSpeak: { value: 0 },
    uMorph: { value: 0 },
    uGiga: { value: 0 },
    uTint: { value: new THREE.Color(0x00c8ff) },
    uTintAmt: { value: 0.16 }
  };
  const Uglow = {
    uTime: U.uTime,
    uScale: U.uScale,
    uSize: { value: 0.075 },
    uAlpha: { value: 0.22 },
    uScatter: U.uScatter,
    uContract: U.uContract,
    uLeanZ: U.uLeanZ,
    uFlow: U.uFlow,
    uBreath: U.uBreath,
    uIntensity: U.uIntensity,
    uSpeak: U.uSpeak,
    uMorph: U.uMorph,
    uGiga: U.uGiga,
    uTint: U.uTint,
    uTintAmt: U.uTintAmt
  };

  // ============================================================================
  // CUSTOM GLSL VERTEX SHADER
  // ============================================================================
  const VERT = `
    uniform float uTime,uScale,uSize,uScatter,uContract,uLeanZ,uFlow,uBreath,uIntensity,uSpeak,uMorph,uGiga;
    attribute vec3 aRandom;attribute float aType;attribute float aBright;attribute float aHue;attribute float aEdge;attribute float aSeed;attribute vec3 aTarget;attribute vec3 aGigaTarget;
    varying float vBright;varying float vHeat;varying float vFleck;varying float vType;varying float vEye;
    
    // Procedural 3D Simplex Noise implementations
    vec4 permute(vec4 x){return mod(((x*34.0)+1.0)*x,289.0);}vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
    float snoise(vec3 v){const vec2 C=vec2(1.0/6.0,1.0/3.0);const vec4 D=vec4(0.0,0.5,1.0,2.0);
      vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.0-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
      vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+2.0*C.xxx;vec3 x3=x0-1.0+3.0*C.xxx;i=mod(i,289.0);
      vec4 pp=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
      float n_=1.0/7.0;vec3 ns=n_*D.wyz-D.xzx;vec4 j=pp-49.0*floor(pp*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.0*x_);
      vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.0-abs(x)-abs(y);vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
      vec4 s0=floor(b0)*2.0+1.0;vec4 s1=floor(b1)*2.0+1.0;vec4 sh=-step(h,vec4(0.0));
      vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
      vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
      vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
      vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);m=m*m;
      return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));}
      
    void main(){
      vType=aType;
      vec3 targetPos = aTarget;
      
      // Black Hole Swirl Dynamics
      if (uMorph > 0.001) {
        float dist = length(aTarget.xz);
        float isJet = step(0.25, abs(aTarget.y)) * step(length(aTarget.xz), 0.25);
        float rotAngle = uTime * (3.5 / (dist + 0.18)) * uMorph * (1.0 - isJet);
        float cosA = cos(rotAngle);
        float sinA = sin(rotAngle);
        targetPos.x = aTarget.x * cosA - aTarget.z * sinA;
        targetPos.z = aTarget.x * sinA + aTarget.z * cosA;
        if (isJet > 0.5) {
          targetPos.y += sin(uTime * 12.0 + aTarget.y * 5.0) * 0.18 * uMorph;
        }
      }
      
      vec3 base=mix(position,targetPos,uMorph);
      base=mix(base,aGigaTarget,uGiga);

      vec3 pos=base;vec3 nr=normalize(position+vec3(0.0001));
      float fld=1.0-uMorph;
      pos*=1.0+(sin(uTime*1.0)*0.5+0.5)*0.035*uBreath; // Breathing pulse expansion

      // Simplex-noise based organic flowing fire waves
      vec3 q=position*1.25+vec3(0.0,-uTime*0.22,uTime*0.06);
      vec3 flow=vec3(snoise(q),snoise(q+vec3(31.4,17.2,8.1)),snoise(q+vec3(5.2,42.1,19.7)));
      float amp=(0.05+aEdge*0.55)*uFlow*fld;
      pos+=flow*amp;
      pos+=nr*aEdge*(0.12+0.22*abs(flow.x))*uFlow*fld;
      pos.y+=aEdge*(0.28+0.40*flow.y)*(0.6+0.4*sin(uTime*0.9+aSeed))*uFlow*fld;
      pos*=1.0-uContract*0.15;
      pos+=aRandom*uScatter;
      pos.z+=uLeanZ;
      
      float flowMag=length(flow);
      vHeat=clamp(flowMag*0.55+aEdge*0.55+position.y*0.22,0.0,1.0);
      vHeat=mix(vHeat,0.92,step(0.5,aType));
      vFleck=clamp(snoise(position*2.0+vec3(uTime*0.1))*0.5+0.5+aHue,0.0,1.0);
      
      float b=aBright*uIntensity;
      float band=smoothstep(0.42,0.0,abs(position.y-sin(uTime*2.6)*0.95));
      b+=uSpeak*band*1.2;
      float isEye=step(0.5,aType);
      b*=1.0+uSpeak*(0.35+isEye*1.5);
      vBright=b*(1.0+uMorph*0.7)*(1.0+uGiga*0.4);vEye=isEye;
      
      vec4 mv=modelViewMatrix*vec4(pos,1.0);gl_Position=projectionMatrix*mv;
      gl_PointSize=uSize*mix(1.0,2.4,isEye)*(1.0+uMorph*0.4)*(1.0+uGiga*0.2)*uScale/(-mv.z);
    }`;

  // ============================================================================
  // CUSTOM GLSL FRAGMENT SHADER
  // ============================================================================
  const FRAG = `
    precision highp float;uniform vec3 uTint;uniform float uTintAmt;uniform float uAlpha;uniform float uTime;
    varying float vBright;varying float vHeat;varying float vFleck;varying float vType;varying float vEye;
    void main(){
      vec3 cBase=vec3(0.30,0.03,0.55);
      vec3 cMid =vec3(0.85,0.10,0.85);
      vec3 cHot =vec3(1.00,0.55,0.14);
      vec3 cTip =vec3(1.00,0.93,0.70);
      
      vec3 col = (vHeat<0.45) ? mix(cBase,cMid,vHeat/0.45)
               : (vHeat<0.78) ? mix(cMid,cHot,(vHeat-0.45)/0.33)
                              : mix(cHot,cTip,(vHeat-0.78)/0.22);
      
      col=mix(col,vec3(1.0,0.45,0.05),smoothstep(0.80,0.98,vFleck)*0.28);
      col=mix(col,vec3(0.7,0.08,1.0),smoothstep(0.06,0.0,vFleck)*0.22);
      col=mix(col,uTint,uTintAmt*0.4);
      
      vec3 eyeCol=vec3(0.55)+vec3(0.45)*cos(vec3(uTime*0.5)+vec3(0.0,2.1,4.2));
      col=mix(col,eyeCol,vEye*0.85);
      col*=vBright;
      
      float d=length(gl_PointCoord-vec2(0.5));float a=pow(smoothstep(0.5,0.0,d),1.7);
      gl_FragColor=vec4(col,a*uAlpha);
    }`;

  // Compile materials and point clouds
  const glowMat = new THREE.ShaderMaterial({ uniforms: Uglow, vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
  const sharpMat = new THREE.ShaderMaterial({ uniforms: U, vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
  spin.add(new THREE.Points(geo, glowMat));
  spin.add(new THREE.Points(geo, sharpMat));

  // ============================================================================
  // CROWN RISING SPARKS (EMBERS SYSTEM)
  // ============================================================================
  const WN = 420, wpos = new Float32Array(WN * 3), wl = new Float32Array(WN), wv = new Float32Array(WN * 3), wmax = new Float32Array(WN);
  function spawnW(i, init) {
    const t = Math.random() * 6.28, ph = Math.acos(Math.random() * 0.55 + 0.45), r = 0.85 + Math.random() * 0.2;
    wpos[i * 3] = Math.sin(ph) * Math.cos(t) * r * SX;
    wpos[i * 3 + 1] = Math.cos(ph) * r * SY * 0.95 + 0.25;
    wpos[i * 3 + 2] = Math.sin(ph) * Math.sin(t) * r * SZ * 0.55 + 0.15;
    wv[i * 3] = (Math.random() - 0.5) * 0.14;
    wv[i * 3 + 1] = 0.22 + Math.random() * 0.30;
    wv[i * 3 + 2] = (Math.random() - 0.5) * 0.14;
    wmax[i] = 1.7 + Math.random() * 2.0;
    wl[i] = init ? Math.random() * wmax[i] : wmax[i];
  }
  for (let i = 0; i < WN; i++) spawnW(i, true);
  const wg = new THREE.BufferGeometry();
  wg.setAttribute('position', new THREE.BufferAttribute(wpos, 3));
  const wla = new THREE.BufferAttribute(new Float32Array(WN), 1);
  wg.setAttribute('aLife', wla);
  const wm = new THREE.ShaderMaterial({
    uniforms: { uScale: U.uScale, uOp: { value: 0 } },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    vertexShader: `uniform float uScale;attribute float aLife;varying float vL;
      void main(){vL=aLife;vec4 mv=modelViewMatrix*vec4(position,1.0);gl_Position=projectionMatrix*mv;gl_PointSize=(0.011*aLife+0.003)*uScale/(-mv.z);}`,
    fragmentShader: `precision highp float;uniform float uOp;varying float vL;
      void main(){float d=length(gl_PointCoord-vec2(0.5));float a=pow(smoothstep(0.5,0.0,d),1.7);
        vec3 hot=vec3(1.0,0.72,0.30),cool=vec3(0.62,0.08,0.85);vec3 c=mix(cool,hot,clamp(vL,0.0,1.0));
        gl_FragColor=vec4(c*(0.5+vL),a*clamp(vL,0.0,1.0)*uOp);}`
  });
  spin.add(new THREE.Points(wg, wm));

  // ============================================================================
  // EYE CAVITY FIRE FLAMES SYSTEM
  // ============================================================================
  const EN = 340, epos = new Float32Array(EN * 3), el = new Float32Array(EN), emax = new Float32Array(EN),
        eSXa = new Float32Array(EN), eSYa = new Float32Array(EN), eSZa = new Float32Array(EN), ePh = new Float32Array(EN), eRise = new Float32Array(EN);
  function spawnE(i, init) {
    const e = (i % 2 === 0) ? cL : cR;
    const sgn = (e.x < 0 ? -1 : 1);
    eSXa[i] = e.x + sgn * (0.02 + Math.random() * 0.10);
    eSYa[i] = e.y + (Math.random() * 0.06 - 0.01);
    eSZa[i] = e.z + 0.05 + (Math.random() - 0.5) * 0.04;
    ePh[i] = Math.random() * 6.28;
    eRise[i] = 0.20 + Math.random() * 0.22;
    emax[i] = 1.0 + Math.random() * 1.1;
    el[i] = init ? Math.random() * emax[i] : emax[i];
  }
  for (let i = 0; i < EN; i++) spawnE(i, true);
  const eg = new THREE.BufferGeometry();
  eg.setAttribute('position', new THREE.BufferAttribute(epos, 3));
  const ela = new THREE.BufferAttribute(new Float32Array(EN), 1);
  eg.setAttribute('aLife', ela);
  const em = new THREE.ShaderMaterial({
    uniforms: { uScale: U.uScale, uOp: { value: 0 } },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    vertexShader: `uniform float uScale;attribute float aLife;varying float vL;
      void main(){vL=aLife;vec4 mv=modelViewMatrix*vec4(position,1.0);gl_Position=projectionMatrix*mv;gl_PointSize=(0.013*aLife+0.004)*uScale/(-mv.z);}`,
    fragmentShader: `precision highp float;uniform float uOp;varying float vL;
      void main(){float d=length(gl_PointCoord-vec2(0.5));float a=pow(smoothstep(0.5,0.0,d),1.7);
        vec3 hot=vec3(1.0,0.85,0.45),cool=vec3(1.0,0.35,0.12);vec3 c=mix(cool,hot,clamp(vL,0.0,1.0));
        gl_FragColor=vec4(c*(0.6+vL),a*clamp(vL,0.0,1.0)*uOp);}`
  });
  spin.add(new THREE.Points(eg, em));

  // ============================================================================
  // VOICE STATE MACHINE CONFIGURATION
  // ============================================================================
  const S = {
    IDLE:      { scatter: 0,    contract: 0,    lean: 0,     flow: 1.05, breath: 1,   intensity: 0.9,  tint: 0x00c8ff, tintAmt: 0.18 },
    LISTENING: { scatter: 0,    contract: 1,    lean: 0.40,  flow: 0.7,  breath: 0.6, intensity: 1.12, tint: 0x4de8ff, tintAmt: 0.42 },
    THINKING:  { scatter: 0.5,  contract: 0,    lean: -0.25, flow: 1.7,  breath: 0.3, intensity: 1.0,  tint: 0x0a6a8f, tintAmt: 0.5 },
    SPEAKING:  { scatter: 0.05, contract: 0.15, lean: 0.12,  flow: 1.2,  breath: 1,   intensity: 1.3,  tint: 0xff7a2a, tintAmt: 0.45 },
    GIGA:      { scatter: 0,    contract: 0,    lean: 0.12,  flow: 1.3,  breath: 1.4, intensity: 1.5,  tint: 0x00c8ff, tintAmt: 0.75 }
  };
  const LAB = { IDLE: 'STANDBY', LISTENING: 'LISTENING', THINKING: 'THINKING', SPEAKING: 'SPEAKING', GIGA: 'GIGA CHAD' };
  const DC = { IDLE: '#00c8ff', LISTENING: '#4de8ff', THINKING: '#0a6a8f', SPEAKING: '#ff7a2a', GIGA: '#22e9ff' };
  
  let current = 'IDLE', target = S.IDLE, speaking = false;
  const stateLabel = document.getElementById('stateLabel'), dot = document.getElementById('dot');
  
  function setState(s) {
    if (!S[s]) return;
    current = s;
    target = S[s];
    if (stateLabel) stateLabel.textContent = LAB[s];
    if (dot) {
      dot.style.background = DC[s];
      dot.style.boxShadow = '0 0 12px ' + DC[s];
    }
  }

  // ============================================================================
  // TIMER SCHEDULING HELPERS
  // ============================================================================
  let timers = [];
  function later(fn, ms) {
    const id = setTimeout(() => {
      timers = timers.filter(t => t !== id);
      fn();
    }, ms);
    timers.push(id);
    return id;
  }
  function clearTimers() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  // ============================================================================
  // TYPEWRITER SUBTITLE EFFECT
  // ============================================================================
  const sub = document.getElementById('sub');
  let typeJob = null;
  function type(txt, done) {
    if (typeJob) clearInterval(typeJob);
    if (!sub) {
      if (done) done();
      return;
    }
    sub.classList.add('typing');
    sub.textContent = '';
    let i = 0;
    typeJob = setInterval(() => {
      sub.textContent = txt.slice(0, ++i);
      if (i >= txt.length) {
        clearInterval(typeJob);
        typeJob = null;
        sub.classList.remove('typing');
        if (done) done();
      }
    }, 34);
  }
  function clearSub() {
    if (sub) {
      sub.classList.remove('typing');
      sub.textContent = '';
    }
  }

  // Dialogue lines
  const LINES = [
    "All systems online. I was expecting you.",
    "Twelve agents are currently active. Three await your attention.",
    "Knowledge graph synchronized. New connections formed overnight.",
    "Your focus index is at its peak. Shall we begin?"
  ];
  let lineIx = 0, mouthTarget = 0, envTimer = 0;

  function speak(txt, after) {
    clearTimers();
    setState('SPEAKING');
    speaking = true;
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(txt);
      const voices = window.speechSynthesis.getVoices();
      const englishVoice = voices.find(v => v.lang.startsWith('en') && v.name.includes('Google')) || voices.find(v => v.lang.startsWith('en'));
      if (englishVoice) {
        utterance.voice = englishVoice;
      }
      utterance.pitch = 0.85;
      utterance.rate = 1.0;
      window.speechSynthesis.speak(utterance);
    }
    type(txt, () => later(() => {
      speaking = false;
      clearSub();
      setState('IDLE');
      if (after) after();
    }, 900));
  }
  
  function speakSeq(a, i) {
    i = i || 0;
    if (i >= a.length) {
      setState('IDLE');
      return;
    }
    speak(a[i], () => later(() => speakSeq(a, i + 1), 350));
  }
  
  const $ = id => document.getElementById(id);
  
  // ============================================================================
  // PERFORMANCE SEQUENCES CONTROLLERS
  // ============================================================================
  let perf = null;
  function startPerf(name) {
    perf = { name: name, t: 0, dur: (name === 'constellation' ? 10.0 : name === 'greet' ? 4.5 : 6.0) };
    clearTimers();
    speaking = false;
    clearSub();
    setState('IDLE');
  }
  function endPerf() {
    stopGigaMusic();
    if (perf && perf.name === 'giga') {
      U.uGiga.value = 0;
    }
    perf = null;
  }

  // ============================================================================
  // WEB AUDIO SYNTHESIS MUSIC ENGINE (GIGA CHAD THEME)
  // ============================================================================
  let synthInterval = null;
  window.gigaPulseTrigger = 0;

  function playGigaChadTheme() {
    if (audioCtx) return;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch(e) {
      console.warn("Web Audio not supported", e);
      return;
    }

    const bpm = 126;
    const beatLen = 60 / bpm;
    const startTime = audioCtx.currentTime + 0.1;

    const notes = [65.41, 73.42, 82.41, 61.74]; // C2, D2, E2, B1

    function playNote(freq, time, duration, isHeavy) {
      if (!audioCtx || audioCtx.state === 'suspended') return;

      const osc = audioCtx.createOscillator();
      const oscSub = audioCtx.createOscillator();
      const filter = audioCtx.createBiquadFilter();
      const gainNode = audioCtx.createGain();

      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(freq, time);

      oscSub.type = 'triangle';
      oscSub.frequency.setValueAtTime(freq / 2, time);

      filter.type = 'lowpass';
      filter.frequency.setValueAtTime(140, time);
      filter.frequency.exponentialRampToValueAtTime(550, time + 0.06);
      filter.frequency.exponentialRampToValueAtTime(180, time + duration);
      filter.Q.setValueAtTime(6, time);

      gainNode.gain.setValueAtTime(0, time);
      gainNode.gain.linearRampToValueAtTime(0.35, time + 0.02);
      gainNode.gain.exponentialRampToValueAtTime(0.001, time + duration);

      osc.connect(filter);
      oscSub.connect(filter);
      filter.connect(gainNode);
      gainNode.connect(audioCtx.destination);

      osc.start(time);
      oscSub.start(time);
      osc.stop(time + duration);
      oscSub.stop(time + duration);

      if (isHeavy) {
        const kickOsc = audioCtx.createOscillator();
        const kickGain = audioCtx.createGain();

        kickOsc.frequency.setValueAtTime(160, time);
        kickOsc.frequency.exponentialRampToValueAtTime(0.01, time + 0.16);

        kickGain.gain.setValueAtTime(0.9, time);
        kickGain.gain.exponentialRampToValueAtTime(0.001, time + 0.18);

        kickOsc.connect(kickGain);
        kickGain.connect(audioCtx.destination);

        kickOsc.start(time);
        kickOsc.stop(time + 0.18);
      }
    }

    let nextNoteTime = startTime;
    const pattern = [
      { beat: 0, noteIdx: 0, len: 1.4, kick: true },
      { beat: 3, noteIdx: 0, len: 0.4, kick: false },
      { beat: 4, noteIdx: 0, len: 0.8, kick: true },
      { beat: 6, noteIdx: 0, len: 0.4, kick: false },
      { beat: 7, noteIdx: 0, len: 0.4, kick: false },

      { beat: 8, noteIdx: 1, len: 1.4, kick: true },
      { beat: 11, noteIdx: 1, len: 0.4, kick: false },
      { beat: 12, noteIdx: 1, len: 0.8, kick: true },
      { beat: 14, noteIdx: 1, len: 0.4, kick: false },
      { beat: 15, noteIdx: 1, len: 0.4, kick: false },

      { beat: 16, noteIdx: 2, len: 1.4, kick: true },
      { beat: 19, noteIdx: 2, len: 0.4, kick: false },
      { beat: 20, noteIdx: 2, len: 0.8, kick: true },
      { beat: 22, noteIdx: 2, len: 0.4, kick: false },
      { beat: 23, noteIdx: 2, len: 0.4, kick: false },

      { beat: 24, noteIdx: 3, len: 1.4, kick: true },
      { beat: 27, noteIdx: 3, len: 0.4, kick: false },
      { beat: 28, noteIdx: 3, len: 0.8, kick: true },
      { beat: 30, noteIdx: 3, len: 0.4, kick: false },
      { beat: 31, noteIdx: 3, len: 0.4, kick: false }
    ];

    const totalBeats = 32;
    const loopStartTime = startTime;

    function schedule() {
      if (!audioCtx) return;
      const lookAhead = 0.2;
      while (audioCtx && nextNoteTime < audioCtx.currentTime + lookAhead) {
        const elapsedBeats = (nextNoteTime - loopStartTime) / (beatLen / 2);
        const beatInLoop = Math.round(elapsedBeats) % (totalBeats * 2);

        const item = pattern.find(p => (p.beat * 2) === beatInLoop);
        if (item) {
          const freq = notes[item.noteIdx];
          playNote(freq, nextNoteTime, item.len * beatLen, item.kick);

          if (item.kick) {
            const delay = (nextNoteTime - audioCtx.currentTime) * 1000;
            setTimeout(() => {
              window.gigaPulseTrigger = 1.0;
            }, Math.max(0, delay));
          }
        }
        nextNoteTime += beatLen / 2;
      }
    }

    synthInterval = setInterval(schedule, 40);
  }

  function stopGigaMusic() {
    if (synthInterval) {
      clearInterval(synthInterval);
      synthInterval = null;
    }
    if (audioCtx) {
      audioCtx.close().catch(()=>{});
      audioCtx = null;
    }
  }

  // ============================================================================
  // GIGA CHAD SONG LYRICS
  // ============================================================================
  const GIGA_TEXTS = [
    "CAN YOU FEEL THE SILENCE?",
    "CAN YOU SEE THE DARK?",
    "CAN YOU FIX THE BROKEN?",
    "CAN YOU FEEL MY HEART?",
    "AVERAGE JARVIS ENJOYER."
  ];

  function playGigaSubtitles() {
    let idx = 0;
    function next() {
      if (!perf || perf.name !== 'giga') return;
      if (idx < GIGA_TEXTS.length) {
        type(GIGA_TEXTS[idx], () => {
          later(() => {
            clearSub();
            idx++;
            later(next, 300);
          }, 1800);
        });
      }
    }
    next();
  }

  // ============================================================================
  // BLACK HOLE CONSTELLATION ("SHOW" MODE) CAPTIONS
  // ============================================================================
  const SHOW_TEXTS = [
    "COLLAPSING CORE TO MASSIVE SINGULARITY...",
    "EVENT HORIZON DETECTED [R_s]...",
    "ACCRETION DISK SPINNING AT RELATIVISTIC SPEED...",
    "EMITTING POLAR RELATIVISTIC JETS...",
    "RE-ASSEMBLING JARVIS SYSTEMS..."
  ];

  function playShowSubtitles() {
    let idx = 0;
    function next() {
      if (!perf || perf.name !== 'constellation') return;
      if (idx < SHOW_TEXTS.length) {
        type(SHOW_TEXTS[idx], () => {
          later(() => {
            clearSub();
            idx++;
            later(next, 200);
          }, 1400);
        });
      }
    }
    next();
  }

  // ============================================================================
  // CONTROL BUTTONS INTERACTION TRIGGERS
  // ============================================================================
  const btnListen = $('bListen');
  if (btnListen) {
    btnListen.onclick = () => {
      endPerf();
      clearTimers();
      speaking = false;
      clearSub();
      setState('LISTENING');
      later(() => {
        if (current === 'LISTENING' && !perf) setState('IDLE');
      }, 3200);
    };
  }
  
  const btnThink = $('bThink');
  if (btnThink) {
    btnThink.onclick = () => {
      endPerf();
      clearTimers();
      speaking = false;
      clearSub();
      setState('THINKING');
      later(() => {
        if (!perf) speak(LINES[lineIx = (lineIx + 1) % LINES.length]);
      }, 2400);
    };
  }
  
  const btnSpeak = $('bSpeak');
  if (btnSpeak) {
    btnSpeak.onclick = () => {
      endPerf();
      speak(LINES[lineIx = (lineIx + 1) % LINES.length]);
    };
  }

  const btnShow = $('bShow');
  if (btnShow) {
    btnShow.onclick = () => {
      endPerf();
      clearTimers();
      speaking = false;
      clearSub();
      perf = { name: 'constellation', t: 0, dur: 10.0 };
      playShowSubtitles();
    };
  }
  
  const btnGiga = $('bGiga');
  if (btnGiga) {
    btnGiga.onclick = () => {
      if (perf && perf.name === 'giga') {
        endPerf();
        setState('IDLE');
        return;
      }
      endPerf();
      clearTimers();
      speaking = false;
      clearSub();

      setState('GIGA');
      perf = { name: 'giga', t: 0, dur: 15.0 };
      playGigaChadTheme();
      playGigaSubtitles();
    };
  }

  const btnHolo = $('bHolo');
  if (btnHolo) {
    btnHolo.onclick = () => {
      toggleHolo();
    };
  }

  const pillNode = $('pillNode');
  if (pillNode) {
    pillNode.onclick = () => {
      toggleHolo();
    };
  }

  // ============================================================================
  // POINTER AND WINDOW INTERACTION HANDLERS
  // ============================================================================
  renderer.domElement.addEventListener('pointerdown', () => {
    if (perf) {
      endPerf();
      setState('LISTENING');
      later(() => {
        if (current === 'LISTENING' && !perf) setState('IDLE');
      }, 1500);
      return;
    }
    if (speaking) return;
    clearTimers();
    setState('LISTENING');
    later(() => {
      if (current === 'LISTENING') setState('IDLE');
    }, 1400);
  });

  let tx = 0, ty = 0, mxs = 0, mys = 0;
  window.addEventListener('pointermove', e => {
    tx = e.clientX / window.innerWidth - 0.5;
    ty = e.clientY / window.innerHeight - 0.5;
  });
  
  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    U.uScale.value = window.innerHeight * 0.62;
  });

  // ============================================================================
  // MAIN ANIMATION LOOP
  // ============================================================================
  const loopClock = new THREE.Clock(), tc = new THREE.Color(), hc = new THREE.Color();
  const damp = (c, t, k, dt) => c + (t - c) * (1 - Math.exp(-k * dt));
  let surgeS = 0;
  
  function animate() {
    requestAnimationFrame(animate);
    const dt = Math.min(loopClock.getDelta(), 0.05), t = loopClock.elapsedTime;
    
    U.uTime.value = t;
    starMat.uniforms.uTime.value = t;
    
    U.uScatter.value = damp(U.uScatter.value, target.scatter, 3, dt);
    U.uContract.value = damp(U.uContract.value, target.contract, 4, dt);
    U.uLeanZ.value = damp(U.uLeanZ.value, target.lean, 3.5, dt);
    U.uFlow.value = damp(U.uFlow.value, target.flow, 2.5, dt);
    U.uBreath.value = damp(U.uBreath.value, target.breath, 3, dt);
    U.uIntensity.value = damp(U.uIntensity.value, target.intensity, 2.5, dt);
    U.uTintAmt.value = damp(U.uTintAmt.value, target.tintAmt, 3, dt);
    tc.set(target.tint);
    U.uTint.value.lerp(tc, 1 - Math.exp(-3 * dt));
    
    if (speaking) {
      envTimer -= dt;
      if (envTimer <= 0) {
        mouthTarget = 0.25 + Math.random() * 0.75;
        envTimer = 0.07 + Math.random() * 0.08;
      }
    } else {
      mouthTarget = 0;
    }
    U.uSpeak.value = damp(U.uSpeak.value, mouthTarget, 15, dt);

    var morphTgt = 0, gigaTgt = 0, surge = 0, showYaw = null;
    let scatterTgt = target.scatter;
    let contractTgt = target.contract;
    let intensityTgt = target.intensity;

    if (perf) {
      perf.t += dt;
      var pp = Math.min(1, perf.t / perf.dur);
      
      if (perf.name === 'constellation') {
        if (pp < 0.22) {
          const progress = pp / 0.22;
          contractTgt = progress * 2.2;
          morphTgt = 0;
          intensityTgt = 0.9 + progress * 1.5;
        } else if (pp < 0.80) {
          const progress = (pp - 0.22) / 0.58;
          contractTgt = 2.2 * (1.0 - progress);
          morphTgt = progress;
          intensityTgt = 2.4 - progress * 1.2;
          showYaw = t * 0.15;
        } else {
          const progress = (pp - 0.80) / 0.20;
          contractTgt = 0;
          morphTgt = 1.0 - progress;
          intensityTgt = 1.2 - progress * 0.3;
        }
      }
      else if (perf.name === 'greet') {
        if (pp < 0.25) {
          contractTgt = 2.2;
          scatterTgt = 0.08 * Math.sin(t * 85);
          intensityTgt = 0.9 + (pp / 0.25) * 2.8;
        } else if (pp < 0.38) {
          let blastProgress = (pp - 0.25) / 0.13;
          contractTgt = 2.2 * (1.0 - blastProgress);
          scatterTgt = blastProgress * 3.8;
          intensityTgt = 3.7 - blastProgress * 1.7;
        } else if (pp < 0.75) {
          let gatherProgress = (pp - 0.38) / 0.37;
          contractTgt = 0;
          scatterTgt = 3.8 * Math.pow(1.0 - gatherProgress, 1.8);
          intensityTgt = 2.0 - gatherProgress * 0.6;
        } else {
          let settleProgress = (pp - 0.75) / 0.25;
          contractTgt = 0;
          scatterTgt = 0;
          intensityTgt = 1.4 - settleProgress * 0.5;
        }
      }
      else if (perf.name === 'giga') {
        if (perf.t < 2.0) {
          gigaTgt = perf.t / 2.0;
        } else if (perf.t > perf.dur - 2.0) {
          gigaTgt = (perf.dur - perf.t) / 2.0;
        } else {
          gigaTgt = 1.0;
        }
      }
      if (perf.t >= perf.dur) {
        endPerf();
        setState('IDLE');
      }
    }

    U.uMorph.value = damp(U.uMorph.value, morphTgt, 2.5, dt);
    U.uGiga.value = damp(U.uGiga.value, gigaTgt, 2.5, dt);
    U.uScatter.value = damp(U.uScatter.value, scatterTgt, 3.0, dt);
    U.uContract.value = damp(U.uContract.value, contractTgt, 4.0, dt);

    window.gigaPulseTrigger = window.gigaPulseTrigger || 0;
    window.gigaPulseTrigger = damp(window.gigaPulseTrigger, 0, 8, dt);
    let pulseVal = window.gigaPulseTrigger * U.uGiga.value;
    U.uSize.value = damp(U.uSize.value, 0.020 + pulseVal * 0.015, 10, dt);

    if (perf && perf.name === 'giga') {
      U.uIntensity.value = damp(U.uIntensity.value, target.intensity + pulseVal * 1.0, 10, dt);
    } else {
      U.uIntensity.value = damp(U.uIntensity.value, intensityTgt + pulseVal * 1.0, 10, dt);
    }
    U.uLeanZ.value = damp(U.uLeanZ.value, target.lean, 3.5, dt);

    var drift = 0.22 * Math.sin(t * 0.13) - 0.05 * Math.sin(t * 0.37);
    var att = (current === 'LISTENING' || current === 'SPEAKING');
    var yT, xT;
    if (showYaw != null) {
      yT = showYaw;
      xT = 0.06 * Math.sin(t * 0.4);
    } else if (perf && perf.name === 'greet') {
      yT = 0.0;
      xT = surgeS * 0.22;
    } else {
      yT = att ? (mxs * 0.35) : drift;
      xT = att ? (-mys * 0.25) : (0.05 * Math.sin(t * 0.12));
    }

    spin.rotation.y = damp(spin.rotation.y, yT, (showYaw != null) ? 1.0 : (att ? 4 : 1.5), dt);
    spin.rotation.x = damp(spin.rotation.x, xT, 2.5, dt);
    spin.position.y = 0.03 * Math.sin(t * 0.4);

    dragonLines.material.opacity = 0;
    constellationStars.material.opacity = U.uMorph.value * 0.9;
    constellationStars.material.size = 0.16 + 0.05 * Math.sin(t * 5.5);

    surgeS = damp(surgeS, surge, 4, dt);
    U.uLeanZ.value += surgeS * 0.45;

    // Crown Embers
    for (let i = 0; i < WN; i++) {
      wl[i] -= dt;
      if (wl[i] <= 0) spawnW(i, false);
      wpos[i * 3] += wv[i * 3] * dt;
      wpos[i * 3 + 1] += wv[i * 3 + 1] * dt;
      wpos[i * 3 + 2] += wv[i * 3 + 2] * dt;
      wla.array[i] = Math.min(1, wl[i] / 0.8) * Math.min(1, (wmax[i] - wl[i]) / 0.3);
    }
    wg.attributes.position.needsUpdate = true;
    wla.needsUpdate = true;
    wm.uniforms.uOp.value = damp(wm.uniforms.uOp.value, 0.85 * (1.0 - U.uMorph.value), 2, dt);

    // Eye Flares
    for (let i = 0; i < EN; i++) {
      el[i] -= dt;
      if (el[i] <= 0) spawnE(i, false);
      var age = emax[i] - el[i];
      epos[i * 3] = eSXa[i] + Math.sin(age * 5.0 + ePh[i]) * (0.02 + age * 0.05);
      epos[i * 3 + 1] = eSYa[i] + age * eRise[i] * 1.3;
      epos[i * 3 + 2] = eSZa[i] + Math.cos(age * 4.0 + ePh[i]) * 0.02;
      ela.array[i] = Math.min(1, el[i] / 0.5) * Math.min(1, age / 0.25);
    }
    eg.attributes.position.needsUpdate = true;
    ela.needsUpdate = true;
    em.uniforms.uOp.value = damp(em.uniforms.uOp.value, 0.9 * (1.0 - U.uMorph.value), 2, dt);

    haloMat.opacity = damp(haloMat.opacity, 0.55 + 0.12 * Math.sin(t * 1.2) + U.uSpeak.value * 0.25, 2, dt);
    hc.set(target.tint);
    haloMat.color.lerp(hc, 1 - Math.exp(-3 * dt));

    for (const c of cosmic) {
      c.cur = damp(c.cur, c.tgt, 1.2, dt);
      c.mat.opacity = c.cur;
      c.mesh.rotation.z += c.sp * dt * 8;
    }

    mxs = damp(mxs, tx, 4, dt);
    mys = damp(mys, ty, 4, dt);
    world.rotation.y = mxs * 0.4;
    world.rotation.x = mys * 0.3;
    camera.position.x = mxs * 0.5;
    camera.position.y = -mys * 0.4;
    camera.lookAt(0, 0, 0);
    
    starMat.uniforms.uOp.value = damp(starMat.uniforms.uOp.value, 1, 1.5, dt);
    starMesh.rotation.y += dt * 0.006;

    if (holoActive) {
      holoTargetPos.set(1.85, 0.05, 0.65);
      holoTargetRot.set(0.0, -Math.PI / 3.8, 0.0);
      holoTargetPosLeft.set(-1.85, 0.05, 0.65);
      holoTargetRotLeft.set(0.0, Math.PI / 3.8, 0.0);
      holoTargetScale = 1.0;
    } else {
      holoTargetPos.set(3.0, 1.2, -3.0);
      holoTargetRot.set(0.0, -Math.PI / 3, 0.1);
      holoTargetPosLeft.set(-3.0, 1.2, -3.0);
      holoTargetRotLeft.set(0.0, Math.PI / 3, -0.1);
      holoTargetScale = 0.05;
    }

    holoMesh.position.x = damp(holoMesh.position.x, holoTargetPos.x, 3.5, dt);
    holoMesh.position.y = damp(holoMesh.position.y, holoTargetPos.y, 3.5, dt);
    holoMesh.position.z = damp(holoMesh.position.z, holoTargetPos.z, 3.5, dt);
    holoMesh.rotation.y = damp(holoMesh.rotation.y, holoTargetRot.y, 3.5, dt);
    holoMesh.rotation.z = damp(holoMesh.rotation.z, holoTargetRot.z, 3.5, dt);
    
    holoMeshLeft.position.x = damp(holoMeshLeft.position.x, holoTargetPosLeft.x, 3.5, dt);
    holoMeshLeft.position.y = damp(holoMeshLeft.position.y, holoTargetPosLeft.y, 3.5, dt);
    holoMeshLeft.position.z = damp(holoMeshLeft.position.z, holoTargetPosLeft.z, 3.5, dt);
    holoMeshLeft.rotation.y = damp(holoMeshLeft.rotation.y, holoTargetRotLeft.y, 3.5, dt);
    holoMeshLeft.rotation.z = damp(holoMeshLeft.rotation.z, holoTargetRotLeft.z, 3.5, dt);
    
    let hScale = damp(holoMesh.scale.x, holoTargetScale, 3.5, dt);
    holoMesh.scale.set(hScale, hScale, hScale);
    holoMeshLeft.scale.set(hScale, hScale, hScale);

    let curHoloOpacityTgt = holoActive ? (0.78 + 0.12 * Math.sin(t * 30) + (Math.random() < 0.03 ? -0.25 : 0)) : 0;
    holoMat.opacity = damp(holoMat.opacity, curHoloOpacityTgt, 5.0, dt);
    holoMatLeft.opacity = damp(holoMatLeft.opacity, curHoloOpacityTgt, 5.0, dt);

    drawHologram(t, dt);
    drawHologramLeft(t, dt);
    
    renderer.render(scene, camera);
  }
  animate();

  setState('IDLE');
  U.uIntensity.value = 0;
  U.uContract.value = 2.2;
  U.uScatter.value = 0;

  setTimeout(() => {
    startOnboarding();
    const hint = document.getElementById('hint');
    if (hint) {
      later(() => hint.style.opacity = '0', 12000);
    }
  }, 500);

  window.addEventListener('click', () => {
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume().catch(()=>{});
    }
  }, { once: true });

  // Ganchos para que la pagina padre (el HUD real) controle esto con estado
  // y amplitud de voz de verdad, en vez de los botones de demo.
  window.jarvisSetState = setState;
  window.jarvisSetAmplitude = function (v) { mouthTarget = Math.max(0, Math.min(1, v)); };
})();
