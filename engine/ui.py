"""The Botflow Companion window UI — a single self-contained HTML page served by
the engine at GET / on 127.0.0.1:17321 and rendered in a pywebview window (and
usable in any browser). Vanilla JS talks to the engine's existing loopback API:
  GET  /botflow/v1/health        GET /botflow/v1/devices
  POST /botflow/v1/auth/login    POST /botflow/v1/auth/2fa    POST /botflow/v1/auth/logout
  POST /botflow/v1/devmode/enable
"""

# Single source of truth for the page. Kept inline (no build step, no assets to
# bundle). Dark theme tuned to read as a sibling of the macOS companion.
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Botflow Companion</title>
<style>
  :root{
    --bg:#0e0f13; --surface:#16181f; --elevated:#1d2029; --border:#2a2e3a;
    --fg:#eef0f5; --muted:#9aa1b2; --accent:#6c8cff; --accent-fg:#fff;
    --green:#3ecf8e; --orange:#e7a13d; --red:#ef6a6a;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{
    background:var(--bg); color:var(--fg);
    font:14px/1.45 -apple-system,Segoe UI,Roboto,system-ui,sans-serif;
    -webkit-user-select:none; user-select:none;
  }
  .wrap{max-width:520px;margin:0 auto;padding:18px 18px 26px}
  header{display:flex;align-items:center;gap:11px;margin-bottom:6px}
  .logo{width:34px;height:34px;border-radius:9px;background:
    linear-gradient(145deg,#2b3350,#11131b);display:grid;place-items:center;
    border:1px solid var(--border);font-size:18px}
  h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.2px}
  .sub{color:var(--muted);font-size:12px;margin-top:1px}
  .badge{margin-left:auto;font-size:11px;font-weight:600;padding:3px 9px;border-radius:999px;
    border:1px solid var(--border);color:var(--muted)}
  .badge.free{color:var(--orange);border-color:#4a3a1e;background:#231b0f}
  .badge.paid{color:var(--green);border-color:#1e4a35;background:#0f231a}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:13px;
    padding:14px;margin-top:13px}
  .card h2{font-size:12px;text-transform:uppercase;letter-spacing:.7px;color:var(--muted);
    margin:0 0 10px;font-weight:650}
  label{display:block;font-size:12px;color:var(--muted);margin:9px 0 4px}
  input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:9px;
    color:var(--fg);padding:10px 11px;font-size:14px;outline:none}
  input:focus{border-color:var(--accent)}
  button{font:inherit;font-weight:600;border-radius:9px;border:1px solid var(--border);
    background:var(--elevated);color:var(--fg);padding:10px 14px;cursor:pointer;transition:.12s}
  button:hover{border-color:#3a3f4e}
  button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-fg);width:100%;margin-top:13px}
  button.primary:hover{filter:brightness(1.06)}
  button.ghost{background:transparent;padding:7px 11px;font-size:13px}
  button:disabled{opacity:.55;cursor:default}
  .row{display:flex;align-items:center;gap:10px}
  .dev{display:flex;align-items:center;gap:11px;padding:11px;border:1px solid var(--border);
    border-radius:11px;background:var(--elevated);margin-top:9px}
  .dev .ic{font-size:22px}
  .dev .nm{font-weight:600}
  .dev .meta{color:var(--muted);font-size:12px}
  .pill{margin-left:auto;font-size:11px;font-weight:600;padding:3px 9px;border-radius:999px}
  .pill.ok{color:var(--green);background:#0f231a}
  .pill.warn{color:var(--orange);background:#231b0f}
  .pill.off{color:var(--muted);background:var(--bg)}
  .muted{color:var(--muted)}
  .err{color:var(--red);font-size:12.5px;margin-top:9px;min-height:1px}
  .hint{color:var(--muted);font-size:12px;margin-top:8px}
  .steps{margin:8px 0 0;padding-left:18px;color:var(--muted);font-size:12.5px}
  .steps li{margin:3px 0}
  .spin{display:inline-block;width:13px;height:13px;border:2px solid var(--border);
    border-top-color:var(--accent);border-radius:50%;animation:s .7s linear infinite;vertical-align:-2px}
  @keyframes s{to{transform:rotate(360deg)}}
  .hidden{display:none!important}
  .setup{font-size:12.5px;color:var(--orange);margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">📱</div>
    <div>
      <h1>Botflow Companion</h1>
      <div class="sub" id="sub">Connecting…</div>
    </div>
    <span class="badge hidden" id="acctBadge"></span>
  </header>

  <!-- setup warning (tooling missing) -->
  <div class="setup hidden" id="setup"></div>

  <!-- SIGNED-OUT: Apple ID sign-in -->
  <div class="card hidden" id="authCard">
    <h2>Sign in with Apple ID</h2>
    <div id="loginStep">
      <label>Apple ID</label>
      <input id="appleId" type="email" placeholder="you@icloud.com" autocomplete="username" />
      <label>Password</label>
      <input id="password" type="password" placeholder="••••••••" autocomplete="current-password" />
      <button class="primary" id="loginBtn">Sign in</button>
      <div class="hint">A free Apple ID works. Credentials go only to this app on your PC.</div>
    </div>
    <div id="twofaStep" class="hidden">
      <label>Two-factor code</label>
      <input id="code" inputmode="numeric" placeholder="123456" autocomplete="one-time-code" />
      <button class="primary" id="codeBtn">Verify</button>
    </div>
    <div class="err" id="authErr"></div>
  </div>

  <!-- SIGNED-IN: account -->
  <div class="card hidden" id="acctCard">
    <h2>Apple account</h2>
    <div class="row">
      <div>
        <div id="acctEmail" style="font-weight:600"></div>
        <div class="meta muted" id="acctTeam"></div>
      </div>
      <button class="ghost" id="logoutBtn" style="margin-left:auto">Sign out</button>
    </div>
    <div class="hint" id="acctNote"></div>
  </div>

  <!-- Devices -->
  <div class="card" id="devCard">
    <h2>iPhone</h2>
    <div id="devList"><div class="muted">Looking for a device…</div></div>
  </div>
</div>

<script>
const API = "http://127.0.0.1:17321/botflow/v1";
const $ = s => document.querySelector(s);
async function api(path, opts){ const r = await fetch(API+path, opts); return r.json().catch(()=>({})); }
async function post(path, body){ return api(path, {method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body||{})}); }

let state = { loggedIn:false, tooling:true };

function show(el, on){ el.classList.toggle("hidden", !on); }

async function refresh(){
  let h;
  try { h = await api("/health"); } catch(e){ $("#sub").textContent="Companion not running"; return; }
  state.loggedIn = !!h.loggedIn; state.tooling = !!h.toolingReady;
  $("#sub").textContent = h.platform ? ("Running on "+h.platform) : "Running";

  // setup hint (missing driver/tools)
  if(!h.toolingReady && h.setupHint){ $("#setup").textContent = "⚠ "+h.setupHint; show($("#setup"),true); }
  else show($("#setup"),false);

  // account badge
  const b = $("#acctBadge");
  if(h.accountType){ b.textContent = h.accountType==="free"?"Free account":"Paid account";
    b.className = "badge "+h.accountType; show(b,true); } else show(b,false);

  show($("#authCard"), !state.loggedIn);
  show($("#acctCard"), state.loggedIn);
  if(state.loggedIn){
    $("#acctEmail").textContent = h.appleId||"";
    $("#acctTeam").textContent = h.team ? ("Team · "+h.team) : "";
    $("#acctNote").textContent = h.accountType==="free"
      ? "Free account: apps are signed for 7 days and you can keep up to 3 installed at once."
      : "";
  }
  refreshDevices();
}

async function refreshDevices(){
  let d; try { d = await api("/devices"); } catch(e){ return; }
  const list = $("#devList");
  const devs = (d&&d.devices)||[];
  if(!devs.length){ list.innerHTML = '<div class="muted">Plug in your iPhone with a cable, unlock it, and tap “Trust”.</div>'; return; }
  list.innerHTML = devs.map(dev=>{
    const dm = dev.developerMode;
    let pill, extra="";
    if(dm==="enabled"){ pill='<span class="pill ok">Developer Mode on</span>'; }
    else if(dm==="disabled"){ pill='<span class="pill warn">Developer Mode off</span>';
      extra = `<ol class="steps">
        <li>Click <b>Enable Developer Mode</b> below.</li>
        <li>On your iPhone: Settings → Privacy &amp; Security → Developer Mode → turn on.</li>
        <li>Restart iPhone, then confirm.</li></ol>
        <button class="ghost" style="margin-top:8px" onclick="enableDevMode('${dev.id}',this)">Enable Developer Mode</button>`; }
    else { pill='<span class="pill off">'+(dm||"checking")+'</span>'; }
    const transport = dev.transport==="localNetwork"?"Wi-Fi":dev.transport==="wired"?"USB":"";
    return `<div class="dev"><div class="ic">📱</div>
      <div><div class="nm">${dev.name||"iPhone"}</div>
      <div class="meta">iOS ${dev.osVersion||"?"}${transport?" · "+transport:""}</div></div>
      ${pill}</div>${extra}`;
  }).join("");
}

window.enableDevMode = async (id, btn)=>{
  btn.disabled=true; btn.innerHTML='<span class="spin"></span> Enabling…';
  const r = await post("/devmode/enable", {deviceId:id});
  btn.disabled=false;
  btn.textContent = r.ok ? "Requested — confirm on iPhone & reboot" : ("Failed: "+(r.error||"unknown"));
  setTimeout(refreshDevices, 1500);
};

$("#loginBtn").onclick = async ()=>{
  const btn=$("#loginBtn"); $("#authErr").textContent="";
  const appleId=$("#appleId").value.trim(), password=$("#password").value;
  if(!appleId||!password){ $("#authErr").textContent="Enter your Apple ID and password."; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin"></span> Signing in…';
  const r = await post("/auth/login", {appleId, password});
  btn.disabled=false; btn.textContent="Sign in";
  if(r.needs2fa){ show($("#loginStep"),false); show($("#twofaStep"),true);
    $("#authErr").textContent="Enter the code sent to your "+(r.type==="sms"?"phone":"trusted device")+"."; $("#code").focus(); }
  else if(r.ok){ refresh(); }
  else { $("#authErr").textContent = r.error || "Sign-in failed."; }
};

$("#codeBtn").onclick = async ()=>{
  const btn=$("#codeBtn"); $("#authErr").textContent="";
  const code=$("#code").value.trim();
  if(!code){ $("#authErr").textContent="Enter the code."; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin"></span> Verifying…';
  const r = await post("/auth/2fa", {code});
  btn.disabled=false; btn.textContent="Verify";
  if(r.ok){ show($("#twofaStep"),false); show($("#loginStep"),true); refresh(); }
  else { $("#authErr").textContent = r.error || "Invalid code."; }
};

$("#logoutBtn").onclick = async ()=>{ await post("/auth/logout",{}); refresh(); };

refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>"""


def get_html():
    return _HTML
