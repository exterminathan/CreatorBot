"""CyBot web admin interface — served alongside the Cloud Run health check."""
from __future__ import annotations

import functools
import hmac
import logging
import mimetypes
import secrets
from pathlib import Path

from aiohttp import web

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

log = logging.getLogger(__name__)


def _check_auth(request: web.Request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[7:], request.app["session_token"])


def _require_auth(handler):
    @functools.wraps(handler)
    async def wrapper(request: web.Request) -> web.Response:
        if not _check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)
    return wrapper


# ── Route handlers ──────────────────────────────────────────────────────────

async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def avatar(request: web.Request) -> web.Response:
    """Serve the bot's avatar image so Discord webhooks can use an https URL."""
    cfg = request.app["config"]
    # cy_avatar_url may hold a local file path during local dev
    file_hint = cfg.cy_avatar_url or ""
    # If it looks like a local path (no scheme), resolve relative to data dir
    if file_hint and "://" not in file_hint:
        path = (_DATA_DIR / file_hint).resolve()
    else:
        # Fall back to scanning data dir for any image named cyNewPfp.*
        candidates = sorted(_DATA_DIR.glob("cyNewPfp.*"))
        path = candidates[0] if candidates else None
    if not path or not path.exists():
        raise web.HTTPNotFound()
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return web.Response(body=path.read_bytes(), content_type=mime)


async def admin_page(request: web.Request) -> web.Response:
    """Public login shell — contains no admin UI content."""
    return web.Response(text=_LOGIN_SHELL_HTML, content_type="text/html")


@_require_auth
async def admin_ui(request: web.Request) -> web.Response:
    """Return the full admin UI HTML only after authentication."""
    return web.Response(text=_ADMIN_INNER_HTML, content_type="text/html")


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid request"}, status=400)
    password = str(body.get("password", ""))
    expected = request.app["password"]
    if not expected:
        return web.json_response({"error": "Web password not configured"}, status=503)
    if not hmac.compare_digest(password, expected):
        return web.json_response({"error": "Invalid password"}, status=401)
    return web.json_response({"token": request.app["session_token"]})


@_require_auth
async def get_config(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return web.json_response({
        "active_channels": [str(c) for c in cfg.active_channels],
        "admin_user_ids": [str(u) for u in cfg.admin_user_ids],
        "default_channel_id": str(cfg.default_channel_id) if cfg.default_channel_id else None,
        "owner_id": str(cfg.admin_user_id),
        "post_settings": cfg.post_settings,
        "interaction_settings": {
            **cfg.interaction_settings,
            "channel_id": str(cfg.interaction_settings["channel_id"]) if cfg.interaction_settings.get("channel_id") else None,
        },
        "role_permissions": cfg.role_permissions,
        "default_permissions": cfg.default_permissions,
        "exclusion_list": cfg.exclusion_list,
    })


@_require_auth
async def put_config(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid request"}, status=400)
    if "active_channels" in body:
        cfg.active_channels = [int(c) for c in body["active_channels"]]
    if "admin_user_ids" in body:
        ids = [int(u) for u in body["admin_user_ids"]]
        if cfg.admin_user_id not in ids:
            ids.insert(0, cfg.admin_user_id)
        cfg.admin_user_ids = ids
    if "default_channel_id" in body:
        val = body["default_channel_id"]
        cfg.default_channel_id = int(val) if val else None
    if "post_settings" in body:
        ps = body["post_settings"]
        if "max_tokens" in ps:
            cfg.post_settings["max_tokens"] = int(ps["max_tokens"])
        if "temperature" in ps:
            cfg.post_settings["temperature"] = float(ps["temperature"])
        if "system_prompt" in ps:
            cfg.post_settings["system_prompt"] = str(ps["system_prompt"])
    if "interaction_settings" in body:
        isettings = body["interaction_settings"]
        if "enabled" in isettings:
            cfg.interaction_settings["enabled"] = bool(isettings["enabled"])
        if "channel_id" in isettings:
            val = isettings["channel_id"]
            cfg.interaction_settings["channel_id"] = int(val) if val else None
        if "max_tokens" in isettings:
            cfg.interaction_settings["max_tokens"] = int(isettings["max_tokens"])
        if "temperature" in isettings:
            cfg.interaction_settings["temperature"] = float(isettings["temperature"])
        if "rate_limit_seconds" in isettings:
            cfg.interaction_settings["rate_limit_seconds"] = int(isettings["rate_limit_seconds"])
            # Clear existing cooldowns so the new setting takes effect immediately
            cfg._interaction_cooldowns.clear()
        if "system_prompt" in isettings:
            cfg.interaction_settings["system_prompt"] = str(isettings["system_prompt"])
    if "role_permissions" in body:
        cfg.role_permissions = body["role_permissions"]
    if "default_permissions" in body:
        cfg.default_permissions.update(body["default_permissions"])
    if "exclusion_list" in body:
        cfg.exclusion_list = list(body["exclusion_list"])
    cfg.save()
    return web.json_response({"status": "ok"})


@_require_auth
async def get_persona(request: web.Request) -> web.Response:
    persona = request.app["persona"]
    return web.json_response(persona.to_dict())


@_require_auth
async def put_persona(request: web.Request) -> web.Response:
    persona = request.app["persona"]
    cfg = request.app["config"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid request"}, status=400)
    persona.apply_overrides(body)
    cfg.persona_data = persona.to_dict()
    cfg.save()
    return web.json_response({"status": "ok"})


@_require_auth
async def get_preview_prompts(request: web.Request) -> web.Response:
    persona = request.app["persona"]
    post_default = persona.system_prompt
    interaction_default = (
        persona.system_prompt
        + "\n\nYou are replying to a Discord user who tagged you. "
        "Keep your reply natural and conversational \u2014 match their energy. "
        "Do NOT repeat their message back. Just respond like you would in a real chat."
    )
    return web.json_response({"post": post_default, "interaction": interaction_default})


@_require_auth
async def get_channels(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return web.json_response(cfg._available_channels)


@_require_auth
async def get_roles(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return web.json_response(cfg._available_roles)


# ── App factory ─────────────────────────────────────────────────────────────

def create_app(config, persona) -> web.Application:
    session_token = secrets.token_hex(32)

    app = web.Application()
    app["config"] = config
    app["persona"] = persona
    app["password"] = config.web_password
    app["session_token"] = session_token

    app.router.add_get("/", health)
    app.router.add_get("/avatar", avatar)
    app.router.add_get("/admin", admin_page)
    app.router.add_get("/api/ui", admin_ui)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)
    app.router.add_get("/api/persona", get_persona)
    app.router.add_put("/api/persona", put_persona)
    app.router.add_get("/api/channels", get_channels)
    app.router.add_get("/api/roles", get_roles)
    app.router.add_get("/api/preview_prompts", get_preview_prompts)

    if not config.web_password:
        pw = secrets.token_urlsafe(16)
        config.web_password = pw
        app["password"] = pw
        log.warning("No WEB_PASSWORD set. Generated temporary password: %s", pw)

    return app


# ── Embedded HTML ───────────────────────────────────────────────────────────

# Served publicly — contains ONLY the login form, nothing else.
_LOGIN_SHELL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CyBot</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#1e1f22;--card:#2b2d31;--text:#f2f3f5;--sub:#b5bac1;--accent:#5865f2;--accent-hover:#4752c4;--red:#da373c;--border:#3f4147;--input:#1e1f22;--font:'Segoe UI','Noto Sans','Helvetica Neue',Arial,sans-serif}
body{font-family:var(--font);background:var(--bg);color:var(--text);display:flex;align-items:center;justify-content:center;height:100vh;-webkit-font-smoothing:antialiased}
.card{background:var(--card);border-radius:8px;padding:32px;width:420px;max-width:90vw;text-align:center}
.card h1{font-size:24px;font-weight:600;margin-bottom:4px}
.card .sub{color:var(--sub);margin-bottom:24px;font-size:14px}
input{width:100%;padding:10px 12px;border-radius:4px;border:1px solid var(--border);background:var(--input);color:var(--text);font-size:16px;outline:none;margin-bottom:12px}
input:focus{border-color:var(--accent)}
.btn{width:100%;padding:10px;border-radius:4px;border:none;background:var(--accent);color:#fff;font-size:15px;font-weight:500;cursor:pointer}
.btn:hover{background:var(--accent-hover)}
.err{color:var(--red);font-size:13px;margin-bottom:12px;min-height:18px}
#loading{display:none;color:var(--sub);font-size:14px;margin-top:12px}
</style>
</head>
<body>
<div class="card">
  <h1>CyBot</h1>
  <p class="sub">Control Panel</p>
  <input type="password" id="pw" placeholder="Password" autocomplete="off">
  <div class="err" id="err"></div>
  <button class="btn" onclick="doLogin()">Log In</button>
  <div id="loading">Loading&#8230;</div>
</div>
<script>
document.getElementById('pw').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
async function doLogin() {
  const pw = document.getElementById('pw').value;
  const err = document.getElementById('err');
  err.textContent = '';
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: pw})
    });
    if (!r.ok) { err.textContent = 'Invalid password'; return; }
    const {token} = await r.json();
    sessionStorage.setItem('cybot_token', token);
    document.getElementById('loading').style.display = 'block';
    const ui = await fetch('/api/ui', {headers: {'Authorization': 'Bearer ' + token}});
    if (!ui.ok) { err.textContent = 'Failed to load UI'; return; }
    document.open(); document.write(await ui.text()); document.close();
  } catch { err.textContent = 'Connection error'; }
}
</script>
</body>
</html>"""

# Served only after a valid bearer token is verified server-side.
_ADMIN_INNER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CyBot Control Panel</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg-darkest:#1e1f22;--bg-dark:#2b2d31;--bg-mid:#313338;--bg-light:#383a40;
  --bg-hover:#35373c;--text-primary:#f2f3f5;--text-secondary:#b5bac1;
  --text-muted:#949ba4;--accent:#5865f2;--accent-hover:#4752c4;
  --green:#248046;--red:#da373c;--red-hover:#a12d31;--border:#3f4147;
  --input-bg:#1e1f22;
  --font:'Segoe UI','Noto Sans','Helvetica Neue',Helvetica,Arial,sans-serif;
}
body{font-family:var(--font);background:var(--bg-mid);color:var(--text-primary);line-height:1.5;-webkit-font-smoothing:antialiased}

/* ── Buttons ── */
.btn{display:inline-flex;align-items:center;justify-content:center;padding:8px 16px;border-radius:4px;border:none;font-size:14px;font-weight:500;cursor:pointer;transition:background .15s;color:#fff}
.btn-primary{background:var(--accent)}.btn-primary:hover{background:var(--accent-hover)}
.btn-danger{background:var(--red)}.btn-danger:hover{background:var(--red-hover)}
.btn-sm{padding:4px 10px;font-size:12px}
.btn-block{width:100%}

/* ── Layout ── */
#app{height:100vh}
.layout{display:flex;height:100%}

/* ── Sidebar ── */
.sidebar{width:232px;background:var(--bg-dark);padding:16px 8px;border-right:1px solid var(--bg-darkest);flex-shrink:0;overflow-y:auto}
.sidebar-title{padding:8px 12px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted)}
.sidebar-item{display:flex;align-items:center;padding:8px 12px;border-radius:4px;cursor:pointer;color:var(--text-secondary);font-size:15px;font-weight:500;transition:background .1s,color .1s;margin-bottom:2px;user-select:none}
.sidebar-item:hover{background:var(--bg-hover);color:var(--text-primary)}
.sidebar-item.active{background:var(--bg-light);color:var(--text-primary)}
.sidebar-sep{border:none;border-top:1px solid var(--border);margin:8px 12px}

/* ── Main ── */
.main{flex:1;overflow-y:auto;padding:40px 40px 80px}
.main-inner{max-width:740px}
.section{display:none}.section.active{display:block}
.section-title{font-size:20px;font-weight:600;margin-bottom:8px}
.section-desc{color:var(--text-secondary);font-size:14px;margin-bottom:24px}
.divider{border:none;border-top:1px solid var(--border);margin:24px 0}

/* ── Form ── */
.form-group{margin-bottom:20px}
.form-label{display:block;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.02em;color:var(--text-secondary);margin-bottom:8px}
.form-hint{color:var(--text-muted);font-size:13px;margin-bottom:8px}
.form-input,.form-textarea{width:100%;padding:10px 12px;border-radius:4px;border:1px solid var(--border);background:var(--input-bg);color:var(--text-primary);font-size:14px;font-family:var(--font);outline:none;transition:border-color .15s}
.form-input:focus,.form-textarea:focus{border-color:var(--accent)}
.form-textarea{resize:vertical;min-height:80px}

/* ── Tags ── */
.tag-container{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.tag{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;background:var(--bg-light);border-radius:4px;font-size:13px;color:var(--text-primary)}
.tag .remove{cursor:pointer;opacity:.6;font-size:14px;line-height:1}.tag .remove:hover{opacity:1;color:var(--red)}

/* ── List items ── */
.list-item{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:var(--bg-dark);border-radius:4px;margin-bottom:8px;border:1px solid var(--border)}
.list-item .id-text{font-family:Consolas,'Courier New',monospace;font-size:14px}
.list-item .actions{display:flex;gap:8px;align-items:center}

/* ── Add row ── */
.add-row{display:flex;gap:8px;margin-top:12px}
.add-row input{flex:1;padding:8px 12px;border-radius:4px;border:1px solid var(--border);background:var(--input-bg);color:var(--text-primary);font-size:14px;outline:none}
.add-row input:focus{border-color:var(--accent)}

/* ── Messages ── */
.message-item{display:flex;gap:8px;margin-bottom:8px;align-items:flex-start}
.message-item textarea{flex:1;padding:8px 12px;border-radius:4px;border:1px solid var(--border);background:var(--input-bg);color:var(--text-primary);font-size:14px;font-family:var(--font);outline:none;resize:vertical;min-height:40px}
.message-item textarea:focus{border-color:var(--accent)}
.message-item .msg-num{color:var(--text-muted);font-size:13px;padding-top:10px;min-width:24px;text-align:right}

/* ── Badges ── */
.badge{display:inline-flex;align-items:center;padding:2px 6px;border-radius:3px;font-size:11px;font-weight:600;text-transform:uppercase;margin-left:8px}
.badge-owner{background:var(--accent);color:#fff}
.badge-default{background:var(--green);color:#fff}

/* ── Star ── */
.star-btn{cursor:pointer;background:none;border:none;font-size:18px;color:var(--text-muted);padding:4px;border-radius:4px;transition:color .15s}
.star-btn:hover{color:#f0b232}.star-btn.active{color:#f0b232}

/* ── Toast ── */
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--bg-darkest);color:var(--text-primary);padding:12px 24px;border-radius:4px;font-size:14px;z-index:2000;animation:fadeInOut 2.5s ease forwards;pointer-events:none}
.toast.success{border-left:4px solid var(--green)}
.toast.error{border-left:4px solid var(--red)}
@keyframes fadeInOut{0%{opacity:0;transform:translateX(-50%) translateY(10px)}15%{opacity:1;transform:translateX(-50%) translateY(0)}85%{opacity:1;transform:translateX(-50%) translateY(0)}100%{opacity:0;transform:translateX(-50%) translateY(-10px)}}

/* ── Responsive ── */
@media(max-width:700px){.sidebar{width:180px;padding:12px 4px}.main{padding:20px 16px 80px}}
@media(max-width:500px){.sidebar{display:none}.main{padding:16px 12px 80px}}

/* ── Permission rows ── */
.perm-row{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:var(--bg-dark);border-radius:4px;margin-bottom:8px;border:1px solid var(--border)}
.perm-name{font-size:14px;font-weight:500}
.perm-desc{font-size:12px;color:var(--text-muted);margin-top:2px}
.perm-toggle{display:flex;gap:4px}
.perm-btn{width:36px;height:28px;border-radius:4px;border:1px solid var(--border);background:var(--bg-light);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px;transition:all .15s;color:var(--text-muted)}
.perm-btn:hover{opacity:.85}
.perm-btn.allow.active{background:#248046;border-color:#248046;color:#fff}
.perm-btn.deny.active{background:#da373c;border-color:#da373c;color:#fff}
.perm-btn.inherit.active{background:var(--bg-light);border-color:var(--text-muted);color:var(--text-secondary)}
</style>
</head>
<body>

<!-- ══════ App ══════ -->
<div id="app">
  <div class="layout">
    <!-- Sidebar -->
    <nav class="sidebar">
      <div class="sidebar-title">CyBot Settings</div>
      <div class="sidebar-item active" data-section="admins" onclick="showSection('admins')">Admin Users</div>
      <div class="sidebar-item" data-section="channels" onclick="showSection('channels')">Channels</div>
      <hr class="sidebar-sep">
      <div class="sidebar-item" data-section="persona" onclick="showSection('persona')">Persona</div>
      <div class="sidebar-item" data-section="messages" onclick="showSection('messages')">Example Messages</div>
      <div class="sidebar-item" data-section="system-prompts" onclick="showSection('system-prompts')">System Prompt Overrides</div>
      <hr class="sidebar-sep">
      <div class="sidebar-item" data-section="post-settings" onclick="showSection('post-settings')">Post Settings</div>
      <div class="sidebar-item" data-section="interaction-settings" onclick="showSection('interaction-settings')">Interaction Settings</div>      <hr class=\"sidebar-sep\">
      <div class=\"sidebar-item\" data-section=\"permissions\" onclick=\"showSection('permissions')\">Permissions</div>
      <div class=\"sidebar-item\" data-section=\"exclusions\" onclick=\"showSection('exclusions')\">Exclusions</div>    </nav>

    <!-- Content -->
    <div class="main"><div class="main-inner">

      <!-- ── Admins ── -->
      <div id="section-admins" class="section active">
        <h2 class="section-title">Admin Users</h2>
        <p class="section-desc">Discord users who can control the bot via slash commands. The owner (from env var) cannot be removed.</p>
        <div id="admin-list"></div>
        <div class="add-row">
          <input type="text" id="add-admin-input" placeholder="Discord User ID">
          <button class="btn btn-primary" onclick="addAdmin()">Add</button>
        </div>
      </div>

      <!-- ── Channels ── -->
      <div id="section-channels" class="section">
        <h2 class="section-title">Channels</h2>
        <p class="section-desc">Active channels where Cy can post. Click the star to set a channel as default (used when no channel is specified in /cy send).</p>
        <div id="channel-list"></div>
        <div class="add-row">
          <input type="text" id="add-channel-input" placeholder="Discord Channel ID">
          <button class="btn btn-primary" onclick="addChannel()">Add</button>
        </div>
      </div>

      <!-- ── Persona ── -->
      <div id="section-persona" class="section">
        <h2 class="section-title">Persona</h2>
        <p class="section-desc">Define Cy's personality, writing style, and behavior for AI-generated messages.</p>
        <div class="form-group">
          <label class="form-label">Display Name</label>
          <input type="text" class="form-input" id="persona-name">
        </div>
        <div class="form-group">
          <label class="form-label">Bio</label>
          <textarea class="form-textarea" id="persona-bio" rows="3"></textarea>
        </div>
        <div class="form-group">
          <label class="form-label">Writing Style</label>
          <textarea class="form-textarea" id="persona-style" rows="3"></textarea>
        </div>
        <hr class="divider">
        <div class="form-group">
          <label class="form-label">Vocabulary</label>
          <p class="form-hint">Words and phrases Cy commonly uses</p>
          <div class="tag-container" id="vocab-tags"></div>
          <div class="add-row">
            <input type="text" id="add-vocab-input" placeholder="Add word or phrase">
            <button class="btn btn-primary btn-sm" onclick="addVocab()">Add</button>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Topics</label>
          <p class="form-hint">Topics Cy frequently talks about</p>
          <div class="tag-container" id="topic-tags"></div>
          <div class="add-row">
            <input type="text" id="add-topic-input" placeholder="Add topic">
            <button class="btn btn-primary btn-sm" onclick="addTopic()">Add</button>
          </div>
        </div>
        <hr class="divider">
        <button class="btn btn-primary" onclick="savePersona()">Save Persona</button>
      </div>

      <!-- ── Example Messages ── -->
      <div id="section-messages" class="section">
        <h2 class="section-title">Example Messages</h2>
        <p class="section-desc">Examples that teach the AI how Cy writes. These are included in the system prompt.</p>
        <div id="message-list"></div>
        <button class="btn btn-primary btn-sm" onclick="addMessage()" style="margin-top:12px">+ Add Message</button>
        <hr class="divider">
        <button class="btn btn-primary" onclick="saveMessages()">Save Messages</button>
      </div>

      <!-- ── System Prompt Override ── -->
      <div id="section-system-prompts" class="section">
        <h2 class="section-title">System Prompt Override</h2>
        <p class="section-desc">Additional instructions applied to both post and interaction pipelines. The persona and example messages are always included.</p>
        <div id="system-prompt-view">
          <textarea class="form-textarea" id="system-prompt-view-field" rows="8" disabled style="opacity:0.7;user-select:none"></textarea>
          <button class="btn btn-warning" style="margin-top:8px" onclick="enableSystemPromptEdit()">Unlock to Edit</button>
        </div>
        <div id="system-prompt-edit" style="display:none">
          <textarea class="form-textarea" id="system-prompt-edit-field" rows="8"></textarea>
          <div style="display:flex;gap:8px;margin-top:8px">
            <button class="btn btn-primary" onclick="saveSystemPrompts()">Save</button>
            <button class="btn btn-secondary" onclick="cancelSystemPromptEdit()">Cancel</button>
          </div>
        </div>
      </div>

      <!-- ── Post Settings ── -->
      <div id="section-post-settings" class="section">
        <h2 class="section-title">Post Settings</h2>
        <p class="section-desc">Controls for the admin-initiated post pipeline (/cy send). These affect how AI generates posts.</p>
        <div class="form-group">
          <label class="form-label">Max Tokens</label>
          <p class="form-hint">Maximum length of generated post (in tokens, ~4 chars each)</p>
          <input type="number" class="form-input" id="post-max-tokens" min="32" max="4096">
        </div>
        <div class="form-group">
          <label class="form-label">Temperature</label>
          <p class="form-hint">Creativity level (0.0 = deterministic, 2.0 = very random). Default: 0.8</p>
          <input type="number" class="form-input" id="post-temperature" min="0" max="2" step="0.05">
        </div>
        <hr class="divider">
        <button class="btn btn-primary" onclick="savePostSettings()">Save Post Settings</button>
      </div>

      <!-- ── Interaction Settings ── -->
      <div id="section-interaction-settings" class="section">
        <h2 class="section-title">Interaction Settings</h2>
        <p class="section-desc">Controls for the @Cy mention-reply pipeline. Members can tag Cy in a dedicated channel to get a response.</p>
        <div class="form-group">
          <label class="form-label">Enabled</label>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="interaction-enabled" style="width:18px;height:18px">
            <span style="font-size:14px;color:var(--text-secondary)">Allow @Cy interactions</span>
          </label>
        </div>
        <div class="form-group">
          <label class=\"form-label\">Interaction Channel</label>
          <p class=\"form-hint\">The Discord channel where members can @Cy. Must also be in the active channels list.</p>
          <select class=\"form-input\" id=\"interaction-channel-id\">
            <option value=\"\">\\u2014 None \\u2014</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Rate Limit (seconds)</label>
          <p class="form-hint">Cooldown per user between interactions. Default: 300 (5 minutes)</p>
          <input type="number" class="form-input" id="interaction-rate-limit" min="0" max="86400">
        </div>
        <div class="form-group">
          <label class="form-label">Max Tokens</label>
          <p class="form-hint">Maximum length of generated reply. Default: 256</p>
          <input type="number" class="form-input" id="interaction-max-tokens" min="32" max="4096">
        </div>
        <div class="form-group">
          <label class="form-label">Temperature</label>
          <p class="form-hint">Creativity level for replies. Default: 0.9</p>
          <input type="number" class="form-input" id="interaction-temperature" min="0" max="2" step="0.05">
        </div>
        <hr class="divider">
        <button class="btn btn-primary" onclick="saveInteractionSettings()">Save Interaction Settings</button>
      </div>
      <!-- \\u2500\\u2500 Permissions \\u2500\\u2500 -->
      <div id=\"section-permissions\" class=\"section\">
        <h2 class=\"section-title\">Permissions</h2>
        <p class=\"section-desc\">Control what users can do based on their roles. Uses an \\u201cAllow wins\\u201d model \\u2014 if any of a user\\u2019s roles allows a permission, it\\u2019s granted.</p>
        <div class=\"form-group\">
          <label class=\"form-label\">Default Permissions</label>
          <p class=\"form-hint\">Baseline for all users when no role override applies</p>
          <div id=\"default-perms\"></div>
        </div>
        <hr class=\"divider\">
        <div class=\"form-group\">
          <label class=\"form-label\">Role Overrides</label>
          <p class=\"form-hint\">Set per-role permission overrides. Select a role to configure.</p>
          <select class=\"form-input\" id=\"perm-role-select\" onchange=\"renderSelectedRolePerms()\" style=\"margin-bottom:16px\">
            <option value=\"\">\\u2014 Select a role \\u2014</option>
          </select>
          <div id=\"role-perms\" style=\"display:none\">
            <div id=\"role-perm-toggles\"></div>
            <div style=\"display:flex;gap:8px;margin-top:16px\">
              <button class=\"btn btn-primary\" onclick=\"saveRolePerms()\">Save Role</button>
              <button class=\"btn btn-danger\" onclick=\"resetRolePerms()\">Reset to Inherit</button>
            </div>
          </div>
        </div>
      </div>

      <!-- \\u2500\\u2500 Exclusions \\u2500\\u2500 -->
      <div id=\"section-exclusions\" class=\"section\">
        <h2 class=\"section-title\">Exclusions</h2>
        <p class=\"section-desc\">Words and topics that Cy will never mention or discuss. These are injected into the system prompt.</p>
        <div class=\"tag-container\" id=\"exclusion-tags\"></div>
        <div class=\"add-row\">
          <input type=\"text\" id=\"add-exclusion-input\" placeholder=\"Add word or topic to exclude\">
          <button class=\"btn btn-primary btn-sm\" onclick=\"addExclusion()\">Add</button>
        </div>
        <hr class=\"divider\">
        <button class=\"btn btn-primary\" onclick=\"saveExclusions()\">Save Exclusions</button>
      </div>
    </div></div>
  </div>
</div>

<script>
/* ════════════════════════════════════════════════════════════════════════════
   CyBot Admin — Client-side logic
   ════════════════════════════════════════════════════════════════════════════ */
let token = sessionStorage.getItem('cybot_token');
let config = {};
let persona = {};

window.addEventListener('DOMContentLoaded', async () => {
  if (!token) { location.href = '/admin'; return; }
  await loadData();
});

async function api(method, path, body) {
  const opts = {
    method,
    headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (res.status === 401) { sessionStorage.removeItem('cybot_token'); location.href = '/admin'; return null; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({error:'Request failed'}));
    toast(err.error || 'Request failed', 'error');
    return null;
  }
  return res.json();
}

async function loadData() {
  try {
    config = await api('GET', '/api/config');
    persona = await api('GET', '/api/persona');
    window._channels = await api('GET', '/api/channels') || [];
    window._roles = await api('GET', '/api/roles') || [];
    if (!config || !persona) { location.href = '/admin'; return; }
    renderAll();
  } catch { location.href = '/admin'; }
}

function renderAll() {
  renderAdmins(); renderChannels(); renderPersona(); renderMessages(); renderSystemPrompts();
  renderPostSettings(); renderInteractionSettings();
  populateChannelDropdown(); renderDefaultPerms(); populateRoleSelect(); renderExclusions();
}

/* ── Navigation ── */
function showSection(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.sidebar-item').forEach(s => s.classList.remove('active'));
  document.getElementById('section-' + name).classList.add('active');
  const si = document.querySelector('.sidebar-item[data-section="' + name + '"]');
  if (si) si.classList.add('active');
}

/* ══════════════════════════════════════════════════════════════════════════
   Admins
   ══════════════════════════════════════════════════════════════════════════ */
function renderAdmins() {
  const el = document.getElementById('admin-list');
  el.innerHTML = '';
  for (const uid of config.admin_user_ids) {
    const isOwner = (uid === config.owner_id);
    const item = document.createElement('div');
    item.className = 'list-item';
    const left = document.createElement('div');
    const idSpan = document.createElement('span');
    idSpan.className = 'id-text';
    idSpan.textContent = uid;
    left.appendChild(idSpan);
    if (isOwner) {
      const badge = document.createElement('span');
      badge.className = 'badge badge-owner';
      badge.textContent = 'OWNER';
      left.appendChild(badge);
    }
    item.appendChild(left);
    if (!isOwner) {
      const actions = document.createElement('div');
      actions.className = 'actions';
      const btn = document.createElement('button');
      btn.className = 'btn btn-danger btn-sm';
      btn.textContent = 'Remove';
      btn.onclick = () => removeAdmin(uid);
      actions.appendChild(btn);
      item.appendChild(actions);
    }
    el.appendChild(item);
  }
}

async function addAdmin() {
  const input = document.getElementById('add-admin-input');
  const id = input.value.trim();
  if (!id || !/^\\d+$/.test(id)) return toast('Enter a valid numeric user ID', 'error');
  if (config.admin_user_ids.includes(id)) return toast('Already an admin', 'error');
  config.admin_user_ids.push(id);
  const r = await api('PUT', '/api/config', {admin_user_ids: config.admin_user_ids});
  if (!r) { config.admin_user_ids.pop(); return; }
  input.value = '';
  renderAdmins();
  toast('Admin added');
}

async function removeAdmin(uid) {
  config.admin_user_ids = config.admin_user_ids.filter(id => id !== uid);
  const r = await api('PUT', '/api/config', {admin_user_ids: config.admin_user_ids});
  if (!r) { await loadData(); return; }
  renderAdmins();
  toast('Admin removed');
}

/* ══════════════════════════════════════════════════════════════════════════
   Channels
   ══════════════════════════════════════════════════════════════════════════ */
function renderChannels() {
  const el = document.getElementById('channel-list');
  el.innerHTML = '';
  for (const cid of config.active_channels) {
    const isDefault = (cid === config.default_channel_id);
    const item = document.createElement('div');
    item.className = 'list-item';
    const left = document.createElement('div');
    const idSpan = document.createElement('span');
    idSpan.className = 'id-text';
    idSpan.textContent = '# ' + cid;
    left.appendChild(idSpan);
    if (isDefault) {
      const badge = document.createElement('span');
      badge.className = 'badge badge-default';
      badge.textContent = 'DEFAULT';
      left.appendChild(badge);
    }
    item.appendChild(left);
    const actions = document.createElement('div');
    actions.className = 'actions';
    const star = document.createElement('button');
    star.className = 'star-btn' + (isDefault ? ' active' : '');
    star.title = 'Set as default';
    star.textContent = '\\u2B50';
    star.onclick = () => setDefault(cid);
    actions.appendChild(star);
    const rm = document.createElement('button');
    rm.className = 'btn btn-danger btn-sm';
    rm.textContent = 'Remove';
    rm.onclick = () => removeChannel(cid);
    actions.appendChild(rm);
    item.appendChild(actions);
    el.appendChild(item);
  }
}

async function addChannel() {
  const input = document.getElementById('add-channel-input');
  const id = input.value.trim();
  if (!id || !/^\\d+$/.test(id)) return toast('Enter a valid numeric channel ID', 'error');
  if (config.active_channels.includes(id)) return toast('Channel already active', 'error');
  config.active_channels.push(id);
  const r = await api('PUT', '/api/config', {active_channels: config.active_channels});
  if (!r) { config.active_channels.pop(); return; }
  input.value = '';
  renderChannels();
  toast('Channel added');
}

async function removeChannel(cid) {
  config.active_channels = config.active_channels.filter(id => id !== cid);
  const update = {active_channels: config.active_channels};
  if (config.default_channel_id === cid) { config.default_channel_id = null; update.default_channel_id = null; }
  const r = await api('PUT', '/api/config', update);
  if (!r) { await loadData(); return; }
  renderChannels();
  toast('Channel removed');
}

async function setDefault(cid) {
  config.default_channel_id = (config.default_channel_id === cid) ? null : cid;
  const r = await api('PUT', '/api/config', {default_channel_id: config.default_channel_id});
  if (!r) { await loadData(); return; }
  renderChannels();
  toast(config.default_channel_id ? 'Default channel set' : 'Default cleared');
}

/* ══════════════════════════════════════════════════════════════════════════
   Persona
   ══════════════════════════════════════════════════════════════════════════ */
function renderPersona() {
  document.getElementById('persona-name').value = persona.name || '';
  document.getElementById('persona-bio').value = persona.bio || '';
  document.getElementById('persona-style').value = persona.writing_style || '';
  renderTags('vocab-tags', persona.vocabulary || [], persona, 'vocabulary');
  renderTags('topic-tags', persona.topics || [], persona, 'topics');
}

function renderTags(containerId, items, obj, field) {
  const el = document.getElementById(containerId);
  el.innerHTML = '';
  for (let i = 0; i < items.length; i++) {
    const tag = document.createElement('span');
    tag.className = 'tag';
    const txt = document.createTextNode(items[i] + ' ');
    tag.appendChild(txt);
    const rm = document.createElement('span');
    rm.className = 'remove';
    rm.textContent = '\\u2715';
    rm.onclick = () => { obj[field].splice(i, 1); renderTags(containerId, obj[field], obj, field); };
    tag.appendChild(rm);
    el.appendChild(tag);
  }
}

function addVocab() {
  const input = document.getElementById('add-vocab-input');
  const val = input.value.trim();
  if (!val) return;
  if (!persona.vocabulary) persona.vocabulary = [];
  persona.vocabulary.push(val);
  renderTags('vocab-tags', persona.vocabulary, 'vocabulary');
  input.value = '';
}

function addTopic() {
  const input = document.getElementById('add-topic-input');
  const val = input.value.trim();
  if (!val) return;
  if (!persona.topics) persona.topics = [];
  persona.topics.push(val);
  renderTags('topic-tags', persona.topics, 'topics');
  input.value = '';
}

async function savePersona() {
  persona.name = document.getElementById('persona-name').value;
  persona.bio = document.getElementById('persona-bio').value;
  persona.writing_style = document.getElementById('persona-style').value;
  const r = await api('PUT', '/api/persona', persona);
  if (r) toast('Persona saved');
}

/* ══════════════════════════════════════════════════════════════════════════
   Example Messages
   ══════════════════════════════════════════════════════════════════════════ */
function renderMessages() {
  const el = document.getElementById('message-list');
  el.innerHTML = '';
  const msgs = persona.example_messages || [];
  for (let i = 0; i < msgs.length; i++) {
    const item = document.createElement('div');
    item.className = 'message-item';
    const num = document.createElement('span');
    num.className = 'msg-num';
    num.textContent = (i + 1) + '.';
    item.appendChild(num);
    const ta = document.createElement('textarea');
    ta.value = msgs[i];
    ta.rows = 1;
    ta.oninput = function() { persona.example_messages[i] = this.value; autoResize(this); };
    item.appendChild(ta);
    const rm = document.createElement('button');
    rm.className = 'btn btn-danger btn-sm';
    rm.textContent = '\\u2715';
    rm.onclick = () => { persona.example_messages.splice(i, 1); renderMessages(); };
    rm.style.marginTop = '4px';
    item.appendChild(rm);
    el.appendChild(item);
    autoResize(ta);
  }
}

function addMessage() {
  if (!persona.example_messages) persona.example_messages = [];
  persona.example_messages.push('');
  renderMessages();
  const items = document.querySelectorAll('#message-list .message-item textarea');
  if (items.length) items[items.length - 1].focus();
}

async function saveMessages() {
  const r = await api('PUT', '/api/persona', {example_messages: persona.example_messages});
  if (r) toast('Example messages saved');
}

function autoResize(ta) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }

/* ══════════════════════════════════════════════════════════════════════════
   Post Settings
   ══════════════════════════════════════════════════════════════════════════ */
function renderPostSettings() {
  const ps = config.post_settings || {};
  document.getElementById('post-max-tokens').value = ps.max_tokens ?? 512;
  document.getElementById('post-temperature').value = ps.temperature ?? 0.8;
}

async function savePostSettings() {
  const mt = parseInt(document.getElementById('post-max-tokens').value);
  const tp = parseFloat(document.getElementById('post-temperature').value);
  const ps = {
    max_tokens: isNaN(mt) ? 512 : mt,
    temperature: isNaN(tp) ? 0.8 : tp,
  };
  const r = await api('PUT', '/api/config', {post_settings: ps});
  if (r) { config.post_settings = ps; toast('Post settings saved'); }
}

/* ══════════════════════════════════════════════════════════════════════════
   Interaction Settings
   ══════════════════════════════════════════════════════════════════════════ */
function renderInteractionSettings() {
  const is_ = config.interaction_settings || {};
  document.getElementById('interaction-enabled').checked = !!is_.enabled;
  document.getElementById('interaction-channel-id').value = is_.channel_id || '';
  document.getElementById('interaction-rate-limit').value = is_.rate_limit_seconds ?? 300;
  document.getElementById('interaction-max-tokens').value = is_.max_tokens ?? 256;
  document.getElementById('interaction-temperature').value = is_.temperature ?? 0.9;
}

async function saveInteractionSettings() {
  const rl = parseInt(document.getElementById('interaction-rate-limit').value);
  const mt = parseInt(document.getElementById('interaction-max-tokens').value);
  const tp = parseFloat(document.getElementById('interaction-temperature').value);
  const is_ = {
    enabled: document.getElementById('interaction-enabled').checked,
    channel_id: document.getElementById('interaction-channel-id').value.trim() || null,
    rate_limit_seconds: isNaN(rl) ? 300 : rl,
    max_tokens: isNaN(mt) ? 256 : mt,
    temperature: isNaN(tp) ? 0.9 : tp,
  };
  const r = await api('PUT', '/api/config', {interaction_settings: is_});
  if (r) { config.interaction_settings = is_; toast('Interaction settings saved'); }
}

/* ══════════════════════════════════════════════════════════════════════════
   System Prompt Override
   ══════════════════════════════════════════════════════════════════════════ */
function renderSystemPrompts() {
  const ps = config.post_settings || {};
  document.getElementById('system-prompt-view-field').value = ps.system_prompt || '';
}

function enableSystemPromptEdit() {
  const ps = config.post_settings || {};
  document.getElementById('system-prompt-edit-field').value = ps.system_prompt || '';
  document.getElementById('system-prompt-view').style.display = 'none';
  document.getElementById('system-prompt-edit').style.display = 'block';
}

function cancelSystemPromptEdit() {
  document.getElementById('system-prompt-edit').style.display = 'none';
  document.getElementById('system-prompt-view').style.display = 'block';
}

async function saveSystemPrompts() {
  const sp = document.getElementById('system-prompt-edit-field').value;
  const r = await api('PUT', '/api/config', {
    post_settings: {system_prompt: sp},
    interaction_settings: {system_prompt: sp}
  });
  if (r) {
    config.post_settings.system_prompt = sp;
    config.interaction_settings.system_prompt = sp;
    renderSystemPrompts();
    cancelSystemPromptEdit();
    toast('System prompt saved');
  }
}

/* ══════════════════════════════════════════════════════════════════════════
   Channel Dropdown
   ══════════════════════════════════════════════════════════════════════════ */
function populateChannelDropdown() {
  const sel = document.getElementById('interaction-channel-id');
  const current = config.interaction_settings?.channel_id || '';
  sel.innerHTML = '<option value="">\\u2014 None \\u2014</option>';
  for (const ch of (window._channels || [])) {
    const opt = document.createElement('option');
    opt.value = ch.id;
    opt.textContent = '#' + ch.name + (ch.guild ? ' (' + ch.guild + ')' : '');
    if (ch.id === current) opt.selected = true;
    sel.appendChild(opt);
  }
}

/* ══════════════════════════════════════════════════════════════════════════
   Permissions
   ══════════════════════════════════════════════════════════════════════════ */
const PERMS = [
  {key: 'bypass_cooldown', name: 'Bypass Cooldown', desc: 'Skip the rate limit between interactions'},
  {key: 'can_interact', name: 'Can @Cy', desc: 'Allowed to mention and interact with Cy'},
  {key: 'can_use_commands', name: 'Use /cy Commands', desc: 'Access to admin slash commands (in admin channel)'},
];

function renderDefaultPerms() {
  const el = document.getElementById('default-perms');
  el.innerHTML = '';
  const dp = config.default_permissions || {};
  for (const p of PERMS) {
    const row = document.createElement('div');
    row.className = 'perm-row';
    const left = document.createElement('div');
    left.innerHTML = '<div class="perm-name">' + p.name + '</div><div class="perm-desc">' + p.desc + '</div>';
    row.appendChild(left);
    const toggle = document.createElement('div');
    toggle.className = 'perm-toggle';
    const btnAllow = document.createElement('button');
    btnAllow.className = 'perm-btn allow' + (dp[p.key] ? ' active' : '');
    btnAllow.textContent = '\\u2713';
    btnAllow.onclick = () => setDefaultPerm(p.key, true);
    toggle.appendChild(btnAllow);
    const btnDeny = document.createElement('button');
    btnDeny.className = 'perm-btn deny' + (!dp[p.key] ? ' active' : '');
    btnDeny.textContent = '\\u2715';
    btnDeny.onclick = () => setDefaultPerm(p.key, false);
    toggle.appendChild(btnDeny);
    row.appendChild(toggle);
    el.appendChild(row);
  }
}

async function setDefaultPerm(key, val) {
  if (!config.default_permissions) config.default_permissions = {};
  config.default_permissions[key] = val;
  renderDefaultPerms();
  const r = await api('PUT', '/api/config', {default_permissions: config.default_permissions});
  if (r) toast('Default permissions saved');
}

function populateRoleSelect() {
  const sel = document.getElementById('perm-role-select');
  sel.innerHTML = '<option value="">\\u2014 Select a role \\u2014</option>';
  for (const role of (window._roles || [])) {
    const opt = document.createElement('option');
    opt.value = role.id;
    opt.textContent = role.name;
    sel.appendChild(opt);
  }
}

function renderSelectedRolePerms() {
  const roleId = document.getElementById('perm-role-select').value;
  const container = document.getElementById('role-perms');
  if (!roleId) { container.style.display = 'none'; return; }
  container.style.display = 'block';
  const el = document.getElementById('role-perm-toggles');
  el.innerHTML = '';
  const rp = (config.role_permissions || {})[roleId] || {};
  for (const p of PERMS) {
    const val = rp[p.key];
    const row = document.createElement('div');
    row.className = 'perm-row';
    const left = document.createElement('div');
    left.innerHTML = '<div class="perm-name">' + p.name + '</div><div class="perm-desc">' + p.desc + '</div>';
    row.appendChild(left);
    const toggle = document.createElement('div');
    toggle.className = 'perm-toggle';
    const btnAllow = document.createElement('button');
    btnAllow.className = 'perm-btn allow' + (val === true ? ' active' : '');
    btnAllow.textContent = '\\u2713';
    btnAllow.onclick = () => setRolePerm(roleId, p.key, true);
    toggle.appendChild(btnAllow);
    const btnInherit = document.createElement('button');
    btnInherit.className = 'perm-btn inherit' + (val == null ? ' active' : '');
    btnInherit.textContent = '/';
    btnInherit.onclick = () => setRolePerm(roleId, p.key, null);
    toggle.appendChild(btnInherit);
    const btnDeny = document.createElement('button');
    btnDeny.className = 'perm-btn deny' + (val === false ? ' active' : '');
    btnDeny.textContent = '\\u2715';
    btnDeny.onclick = () => setRolePerm(roleId, p.key, false);
    toggle.appendChild(btnDeny);
    row.appendChild(toggle);
    el.appendChild(row);
  }
}

function setRolePerm(roleId, key, val) {
  if (!config.role_permissions) config.role_permissions = {};
  if (!config.role_permissions[roleId]) config.role_permissions[roleId] = {};
  config.role_permissions[roleId][key] = val;
  renderSelectedRolePerms();
}

async function saveRolePerms() {
  const r = await api('PUT', '/api/config', {role_permissions: config.role_permissions});
  if (r) toast('Role permissions saved');
}

async function resetRolePerms() {
  const roleId = document.getElementById('perm-role-select').value;
  if (!roleId) return;
  if (config.role_permissions) delete config.role_permissions[roleId];
  renderSelectedRolePerms();
  const r = await api('PUT', '/api/config', {role_permissions: config.role_permissions || {}});
  if (r) toast('Role permissions reset');
}

/* ══════════════════════════════════════════════════════════════════════════
   Exclusions
   ══════════════════════════════════════════════════════════════════════════ */
function renderExclusions() {
  renderTags('exclusion-tags', config.exclusion_list || [], config, 'exclusion_list');
}

function addExclusion() {
  const input = document.getElementById('add-exclusion-input');
  const val = input.value.trim();
  if (!val) return;
  if (!config.exclusion_list) config.exclusion_list = [];
  config.exclusion_list.push(val);
  renderExclusions();
  input.value = '';
}

async function saveExclusions() {
  const r = await api('PUT', '/api/config', {exclusion_list: config.exclusion_list || []});
  if (r) toast('Exclusions saved');
}

/* ── Toast ── */
function toast(msg, type) {
  type = type || 'success';
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

/* ── Global Enter key for add inputs ── */
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const id = e.target.id;
  if (id === 'add-admin-input') addAdmin();
  else if (id === 'add-channel-input') addChannel();
  else if (id === 'add-vocab-input') addVocab();
  else if (id === 'add-topic-input') addTopic();
  else if (id === 'add-exclusion-input') addExclusion();
});
</script>
</body>
</html>"""
