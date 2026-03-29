"""CyBot web admin interface — served alongside the Cloud Run health check."""
from __future__ import annotations

import functools
import hmac
import logging
import mimetypes
import os
import secrets
import threading
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
        "user_names": cfg._user_names,
        "default_channel_id": str(cfg.default_channel_id) if cfg.default_channel_id else None,
        "log_channel_id": str(cfg.log_channel_id) if cfg.log_channel_id else None,
        "owner_id": str(cfg.admin_user_id),
        "post_settings": cfg.post_settings,
        "interaction_settings": {
            **cfg.interaction_settings,
            "channel_ids": [str(c) for c in cfg.interaction_settings.get("channel_ids", [])],
        },
        "role_permissions": cfg.role_permissions,
        "default_permissions": cfg.default_permissions,
        "exclusion_list": cfg.exclusion_list,
        "slang_dict": cfg.slang_dict,
        "default_responses": cfg.default_responses,
        "system_prompt_template": cfg.system_prompt_template,
        "channel_permissions": cfg.channel_permissions,
        "bot_enabled": cfg.bot_enabled,
        "welcome_channel_id": str(cfg.welcome_channel_id) if cfg.welcome_channel_id else None,
        "mod_log_channel_id": str(cfg.mod_log_channel_id) if cfg.mod_log_channel_id else None,
        "welcome_message": cfg.welcome_message,
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
    if "log_channel_id" in body:
        val = body["log_channel_id"]
        cfg.log_channel_id = int(val) if val else None
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
        if "channel_ids" in isettings:
            cfg.interaction_settings["channel_ids"] = [int(v) for v in isettings["channel_ids"] if v]
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
        cfg.exclusion_list = [
            {"topic": e["topic"], "severity": int(e.get("severity", 3))}
            if isinstance(e, dict) else {"topic": str(e), "severity": 3}
            for e in body["exclusion_list"]
        ]
    if "channel_permissions" in body:
        cfg.channel_permissions = {
            str(k): dict(v) for k, v in body["channel_permissions"].items()
            if isinstance(v, dict)
        }
    if "default_responses" in body:
        cfg.default_responses = list(body["default_responses"])
    if "slang_dict" in body:
        cfg.slang_dict = {
            str(k): str(v) for k, v in body["slang_dict"].items()
            if str(k).strip() and str(v).strip()
        }
    if "system_prompt_template" in body:
        cfg.system_prompt_template = str(body["system_prompt_template"])
    if "bot_enabled" in body:
        cfg.bot_enabled = bool(body["bot_enabled"])
    if "welcome_channel_id" in body:
        val = body["welcome_channel_id"]
        cfg.welcome_channel_id = int(val) if val else None
    if "mod_log_channel_id" in body:
        val = body["mod_log_channel_id"]
        cfg.mod_log_channel_id = int(val) if val else None
    if "welcome_message" in body:
        cfg.welcome_message = str(body["welcome_message"])
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
    from ai.persona import DEFAULT_TEMPLATE
    persona = request.app["persona"]
    cfg = request.app["config"]
    template = cfg.system_prompt_template or DEFAULT_TEMPLATE
    rendered = persona.render_system_prompt(template)
    return web.json_response({
        "rendered": rendered,
        "template": template,
        "default_template": DEFAULT_TEMPLATE,
    })


@_require_auth
async def get_channels(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return web.json_response(cfg._available_channels)


@_require_auth
async def get_roles(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return web.json_response(cfg._available_roles)


@_require_auth
async def bot_control(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid request"}, status=400)
    action = str(body.get("action", ""))
    if action == "enable":
        cfg.set_bot_enabled(True)
        log.info("Bot enabled via web UI")
        return web.json_response({"status": "ok", "bot_enabled": True})
    elif action == "disable":
        cfg.set_bot_enabled(False)
        log.warning("Bot disabled via web UI")
        return web.json_response({"status": "ok", "bot_enabled": False})
    elif action == "restart":
        log.warning("Bot restart requested via web UI")
        threading.Timer(1.5, os._exit, args=[0]).start()
        return web.json_response({"status": "ok", "message": "Restarting"})
    else:
        return web.json_response({"error": "Unknown action"}, status=400)


@_require_auth
async def get_status(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return web.json_response({"bot_enabled": cfg.bot_enabled})


@_require_auth
async def get_mod_log(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    # Return most-recent first, up to last 200
    return web.json_response(list(reversed(cfg._mod_action_log)))


@_require_auth
async def get_giveaways(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return web.json_response({
        "giveaways": cfg.giveaways,
        "settings": cfg.giveaway_settings,
    })


@_require_auth
async def put_giveaway_settings(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid request"}, status=400)
    if "default_channel_id" in body:
        val = body["default_channel_id"]
        cfg.giveaway_settings["default_channel_id"] = int(val) if val else None
    if "embed_color" in body:
        color = str(body["embed_color"])
        if color.startswith("#") and len(color) in (4, 7):
            cfg.giveaway_settings["embed_color"] = color
    if "manager_role_ids" in body:
        cfg.giveaway_settings["manager_role_ids"] = [
            int(r) for r in body["manager_role_ids"] if str(r).strip()
        ]
    cfg.save()
    return web.json_response({"status": "ok"})


@_require_auth
async def giveaway_end(request: web.Request) -> web.Response:
    message_id = request.match_info["message_id"]
    bot = request.app["bot_holder"]["bot"]
    if bot is None:
        return web.json_response({"error": "Bot not available"}, status=503)
    giveaway_cog = bot.cogs.get("GiveawayCog")
    if giveaway_cog is None:
        return web.json_response({"error": "Giveaway cog not loaded"}, status=503)
    import asyncio
    import concurrent.futures
    fut = asyncio.run_coroutine_threadsafe(giveaway_cog.manager.end(message_id), bot.loop)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: fut.result(timeout=10)
        )
    except concurrent.futures.TimeoutError:
        return web.json_response({"error": "Timed out ending giveaway"}, status=504)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)
    if result is None:
        return web.json_response({"error": "Giveaway not found or already ended"}, status=404)
    return web.json_response({"status": "ok", "giveaway": result})


@_require_auth
async def giveaway_reroll(request: web.Request) -> web.Response:
    message_id = request.match_info["message_id"]
    bot = request.app["bot_holder"]["bot"]
    if bot is None:
        return web.json_response({"error": "Bot not available"}, status=503)
    giveaway_cog = bot.cogs.get("GiveawayCog")
    if giveaway_cog is None:
        return web.json_response({"error": "Giveaway cog not loaded"}, status=503)
    import asyncio
    import concurrent.futures
    fut = asyncio.run_coroutine_threadsafe(giveaway_cog.manager.end(message_id, reroll=True), bot.loop)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: fut.result(timeout=10)
        )
    except concurrent.futures.TimeoutError:
        return web.json_response({"error": "Timed out rerolling giveaway"}, status=504)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)
    if result is None:
        return web.json_response({"error": "Giveaway not found"}, status=404)
    return web.json_response({"status": "ok", "giveaway": result})


@_require_auth
async def giveaway_delete(request: web.Request) -> web.Response:
    message_id = request.match_info["message_id"]
    bot = request.app["bot_holder"]["bot"]
    if bot is None:
        return web.json_response({"error": "Bot not available"}, status=503)
    giveaway_cog = bot.cogs.get("GiveawayCog")
    if giveaway_cog is None:
        return web.json_response({"error": "Giveaway cog not loaded"}, status=503)
    import asyncio
    import concurrent.futures
    fut = asyncio.run_coroutine_threadsafe(giveaway_cog.manager.delete(message_id), bot.loop)
    try:
        deleted = await asyncio.get_event_loop().run_in_executor(
            None, lambda: fut.result(timeout=10)
        )
    except concurrent.futures.TimeoutError:
        return web.json_response({"error": "Timed out deleting giveaway"}, status=504)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)
    if not deleted:
        return web.json_response({"error": "Giveaway not found"}, status=404)
    return web.json_response({"status": "ok"})


@_require_auth
async def giveaway_toggle_exclude(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    message_id = request.match_info["message_id"]
    user_id_str = request.match_info["user_id"]
    try:
        user_id = int(user_id_str)
    except ValueError:
        return web.json_response({"error": "Invalid user_id"}, status=400)
    giveaway = cfg.get_giveaway(message_id)
    if giveaway is None:
        return web.json_response({"error": "Giveaway not found"}, status=404)
    excluded: list[int] = giveaway.get("excluded_entries", [])
    if user_id in excluded:
        excluded.remove(user_id)
        state = "included"
    else:
        excluded.append(user_id)
        state = "excluded"
    cfg.update_giveaway(message_id, {"excluded_entries": excluded})
    return web.json_response({"status": "ok", "state": state, "excluded_entries": excluded})


# ── App factory ─────────────────────────────────────────────────────────────

def create_app(config, persona, bot=None) -> web.Application:
    session_token = secrets.token_hex(32)

    app = web.Application()
    app["config"] = config
    app["persona"] = persona
    app["password"] = config.web_password
    app["session_token"] = session_token
    app["bot_holder"] = {"bot": bot}  # pre-set before startup; update ["bot"] key later without touching app state

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
    app.router.add_post("/api/bot-control", bot_control)
    app.router.add_get("/api/preview_prompts", get_preview_prompts)
    app.router.add_get("/api/status", get_status)
    app.router.add_get("/api/mod-log", get_mod_log)
    app.router.add_get("/api/giveaways", get_giveaways)
    app.router.add_put("/api/giveaway-settings", put_giveaway_settings)
    app.router.add_post("/api/giveaways/{message_id}/end", giveaway_end)
    app.router.add_post("/api/giveaways/{message_id}/reroll", giveaway_reroll)
    app.router.add_delete("/api/giveaways/{message_id}", giveaway_delete)
    app.router.add_post("/api/giveaways/{message_id}/toggle-exclude/{user_id}", giveaway_toggle_exclude)

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

/* ── Exclusion severity rows ── */
.excl-row{display:flex;align-items:center;gap:12px;padding:12px 16px;background:var(--bg-dark);border-radius:4px;margin-bottom:8px;border:1px solid var(--border)}
.excl-topic{flex:1;font-size:14px;font-weight:500}
.sev-toggle{display:flex;gap:4px}
.sev-btn{width:32px;height:28px;border-radius:4px;border:1px solid var(--border);background:var(--bg-light);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;transition:all .15s;color:var(--text-muted)}
.sev-btn:hover{opacity:.85}
.sev-btn.s1.active{background:#248046;border-color:#248046;color:#fff}
.sev-btn.s2.active{background:#f0b232;border-color:#f0b232;color:#1e1f22}
.sev-btn.s3.active{background:#da373c;border-color:#da373c;color:#fff}
/* \u2500\u2500 Channel permission pills \u2500\u2500 */
.chan-perm-btn{padding:2px 7px;border-radius:3px;border:1px solid transparent;font-size:10px;font-weight:700;letter-spacing:.04em;cursor:pointer;transition:background .15s,color .15s;margin-left:4px}
.chan-perm-btn.active{background:#248046;color:#fff;border-color:#248046}
.chan-perm-btn.inactive{background:var(--bg-light);color:var(--text-muted);border-color:var(--border)}
/* ── Channel matrix ── */
.ch-matrix{width:100%;border-collapse:collapse;font-size:13px;min-width:640px}
.ch-matrix th{padding:7px 10px;text-align:center;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--text-muted);border-bottom:2px solid var(--border);white-space:nowrap;cursor:default}
.ch-matrix th:first-child{text-align:left;min-width:160px}
.ch-matrix td{padding:5px 8px;text-align:center;border-bottom:1px solid var(--border)}
.ch-matrix td:first-child{text-align:left;font-weight:500;white-space:nowrap;max-width:200px;overflow:hidden;text-overflow:ellipsis}
.ch-matrix tbody tr:hover{background:var(--bg-elevated,var(--bg-light))}
.ch-tog{display:inline-flex;align-items:center;justify-content:center;width:52px;height:24px;border-radius:4px;border:1px solid transparent;font-size:11px;font-weight:700;letter-spacing:.04em;cursor:pointer;transition:background .12s,color .12s}
.ch-tog.on{background:#248046;color:#fff;border-color:#248046}
.ch-tog.off{background:var(--bg-light);color:var(--text-muted);border-color:var(--border)}
.ch-excl{display:inline-flex;align-items:center;justify-content:center;width:32px;height:24px;border-radius:4px;border:1px solid transparent;font-size:16px;line-height:1;cursor:pointer;transition:background .12s,color .12s}
.ch-excl.on{background:#5865f2;color:#fff;border-color:#5865f2}
.ch-excl.off{background:var(--bg-light);color:var(--text-muted);border-color:var(--border)}
/* ── Perm matrix ── */
.pm-matrix{min-width:unset}
.pm-matrix th{padding:7px 6px;font-size:10px}
.pm-matrix th:first-child{min-width:120px}
.pm-matrix td{padding:5px 4px}
.pm-cell{display:inline-flex;align-items:center;justify-content:center;width:36px;height:24px;border-radius:4px;border:1px solid transparent;font-size:13px;cursor:pointer;transition:background .12s,color .12s}
.pm-cell.on{background:#248046;color:#fff;border-color:#248046}
.pm-cell.off{background:#b03030;color:#fff;border-color:#8b2020}
.pm-cell.inherit{background:var(--bg-light);color:var(--text-muted);border-color:var(--border)}
.pm-cell.locked{cursor:default;background:none;border-color:transparent;opacity:.85}
</style>
</head>
<body>

<!-- ══════ App ══════ -->
<div id="app">
  <div class="layout">
    <!-- Sidebar -->
    <nav class="sidebar">
      <div class="sidebar-title">CyBot Settings</div>
      <div class="sidebar-item active" data-section="general" onclick="showSection('general')">General</div>
      <div class="sidebar-item" data-section="admins" onclick="showSection('admins')">Admin Users</div>
      <div class="sidebar-item" data-section="channels" onclick="showSection('channels')">Channels</div>
      <hr class="sidebar-sep">
      <div class="sidebar-item" data-section="persona" onclick="showSection('persona')">Persona</div>
      <div class="sidebar-item" data-section="video-lines" onclick="showSection('video-lines')">Video Lines</div>
      <div class="sidebar-item" data-section="messages" onclick="showSection('messages')">Example Messages</div>
      <div class="sidebar-item" data-section="exclusions" onclick="showSection('exclusions')">Exclusions</div>
      <div class="sidebar-item" data-section="slang" onclick="showSection('slang')">Slang</div>
      <div class="sidebar-item" data-section="default-responses" onclick="showSection('default-responses')">Default Responses</div>
      <div class="sidebar-item" data-section="system-prompts" onclick="showSection('system-prompts')">System Prompts</div>
      <hr class="sidebar-sep">
      <div class="sidebar-item" data-section="post-settings" onclick="showSection('post-settings')">Post Settings</div>
      <div class="sidebar-item" data-section="interaction-settings" onclick="showSection('interaction-settings')">Interaction Settings</div>
      <div class="sidebar-item" data-section="moderation" onclick="showSection('moderation')">Moderation</div>      <hr class=\"sidebar-sep\">
      <div class="sidebar-item" data-section="giveaways" onclick="showSection('giveaways')">🎉 Giveaways</div>      <hr class=\"sidebar-sep\">
      <div class="sidebar-item" data-section="permissions" onclick="showSection('permissions')">Permissions</div>    </nav>

    <!-- Content -->
    <div class="main"><div class="main-inner">

      <!-- ── General ── -->
      <div id="section-general" class="section active">
        <h2 class="section-title">General Settings</h2>
        <p class="section-desc">Control the bot's running state and connection.</p>
        <div class="form-group">
          <label class="form-label">Bot Status</label>
          <p class="form-hint">Start or stop all bot responses. When stopped, the bot ignores all messages and @mentions.</p>
          <div style="display:flex;gap:12px;margin-top:8px">
            <button class="btn btn-primary" id="btn-bot-start" onclick="botControl('enable')">&#9654; Start</button>
            <button class="btn btn-danger" id="btn-bot-stop" onclick="botControl('disable')">&#9632; Stop</button>
          </div>
          <div id="bot-status-indicator" style="margin-top:12px;font-size:14px;font-weight:600"></div>
        </div>
        <hr class="divider">
        <div class="form-group">
          <label class="form-label">Restart Bot</label>
          <p class="form-hint">Disconnects and reconnects the bot to Discord. Use if the bot appears stuck or unresponsive. It will be back online within ~15 seconds.</p>
          <button class="btn btn-danger" onclick="botControl('restart')" style="margin-top:8px">&#8635; Restart</button>
        </div>
      </div>

      <!-- ── Admins ── -->
      <div id="section-admins" class="section">
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
        <p class="section-desc">Add channels Cy has access to, then use the matrix to control exactly what each one can do.</p>
        <div class="add-row" style="margin-bottom:20px">
          <select id="add-channel-select" class="form-input"><option value="">&#8212; Select a channel to add &#8212;</option></select>
          <button class="btn btn-primary" onclick="addChannel()">Add Channel</button>
        </div>
        <div style="overflow-x:auto">
          <table id="channel-matrix" class="ch-matrix">
            <thead id="channel-matrix-head"></thead>
            <tbody id="channel-matrix-body"></tbody>
          </table>
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
          <label class="form-label">Facts</label>
          <p class="form-hint">Specific facts and knowledge about Cy that influence responses (e.g. "From LA", "Drives a Camaro")</p>
          <div class="tag-container" id="fact-tags"></div>
          <div class="add-row">
            <input type="text" id="add-fact-input" placeholder="Add a fact about Cy">
            <button class="btn btn-primary btn-sm" onclick="addFact()">Add</button>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Vocabulary</label>
          <p class="form-hint">Words and phrases that subtly color Cy's speech (used as flavor, not primary content)</p>
          <div class="tag-container" id="vocab-tags"></div>
          <div class="add-row">
            <input type="text" id="add-vocab-input" placeholder="Add word or phrase">
            <button class="btn btn-primary btn-sm" onclick="addVocab()">Add</button>
          </div>
        </div>
        <hr class="divider">
        <button class="btn btn-primary" onclick="savePersona()">Save Persona</button>
      </div>

      <!-- ── Video Lines ── -->
      <div id="section-video-lines" class="section">
        <h2 class="section-title">Video Lines</h2>
        <p class="section-desc">Direct lines from Cy's videos. These teach the AI his authentic voice and speech patterns. Add as many as you like.</p>
        <div id="video-line-list"></div>
        <button class="btn btn-primary btn-sm" onclick="addVideoLine()" style="margin-top:12px">+ Add Line</button>
        <hr class="divider">
        <button class="btn btn-primary" onclick="saveVideoLines()">Save Video Lines</button>
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

      <!-- ── Exclusions ── -->
      <div id="section-exclusions" class="section">
        <h2 class="section-title">Exclusions</h2>
        <p class="section-desc">Topics that Cy should avoid or block. Severity controls how strictly the topic is filtered. Changes save automatically.</p>
        <p class="form-hint" style="margin-bottom:16px"><b>1</b> = Allowed (no filter) &nbsp; <b>2</b> = Restricted (no direct discussion, tangential OK) &nbsp; <b>3</b> = Blocked (never mention)</p>
        <div id="exclusion-list"></div>
        <div class="add-row">
          <input type="text" id="add-exclusion-input" placeholder="Add word or topic to exclude">
          <select id="add-exclusion-severity" class="form-input" style="width:auto;min-width:60px">
            <option value="3">3</option>
            <option value="2">2</option>
            <option value="1">1</option>
          </select>
          <button class="btn btn-primary btn-sm" onclick="addExclusion()">Add</button>
        </div>
      </div>

      <!-- ── Slang ── -->
      <div id="section-slang" class="section">
        <h2 class="section-title">Slang Dictionary</h2>
        <p class="section-desc">Define slang terms so Cy understands and responds to them correctly. Injected into the system prompt as a glossary. Changes save automatically.</p>
        <div id="slang-list"></div>
        <div class="add-row" style="margin-top:12px">
          <input type="text" id="add-slang-word" placeholder="Slang word or phrase" style="flex:0 0 180px">
          <input type="text" id="add-slang-def" placeholder="Definition / meaning">
          <button class="btn btn-primary btn-sm" onclick="addSlangEntry()">Add</button>
        </div>
      </div>

      <!-- ── Default Responses ── -->
      <div id="section-default-responses" class="section">
        <h2 class="section-title">Default Responses</h2>
        <p class="section-desc">When AI generation fails, times out, or gets blocked, Cy will pick one of these at random. Changes save automatically.</p>
        <div id="default-response-list"></div>
        <div class="add-row">
          <input type="text" id="add-default-response-input" placeholder="Add a fallback response">
          <button class="btn btn-primary btn-sm" onclick="addDefaultResponse()">Add</button>
        </div>
      </div>

      <!-- ── System Prompts ── -->
      <div id="section-system-prompts" class="section">
        <h2 class="section-title">System Prompts</h2>
        <p class="section-desc">The base system prompt is generated from persona settings. You can edit the template structure. Additive prompts are appended per-pipeline.</p>
        <div class="form-group">
          <label class="form-label">Base System Prompt (Generated)</label>
          <p class="form-hint">Read-only \u2014 this is computed from persona data + template. Edit the template to change structure.</p>
          <div id="base-prompt-view">
            <textarea class="form-textarea" id="base-prompt-rendered" rows="14" disabled style="opacity:0.7"></textarea>
            <button class="btn btn-primary btn-sm" style="margin-top:8px" onclick="editTemplate()">Edit Template</button>
          </div>
          <div id="base-prompt-edit" style="display:none">
            <p class="form-hint">Placeholders: {name}, {bio}, {facts}, {writing_style}, {vocabulary}, {example_messages}, {video_lines}</p>
            <textarea class="form-textarea" id="base-prompt-template" rows="14"></textarea>
            <div style="display:flex;gap:8px;margin-top:8px">
              <button class="btn btn-primary" onclick="saveTemplate()">Save Template</button>
              <button class="btn btn-sm" style="background:var(--bg-light)" onclick="cancelTemplateEdit()">Cancel</button>
              <button class="btn btn-sm" style="background:var(--bg-light)" onclick="resetTemplate()">Reset to Default</button>
            </div>
          </div>
        </div>
        <hr class="divider">
        <div class="form-group">
          <label class="form-label">Post Additive Prompt</label>
          <p class="form-hint">Extra instructions appended when generating posts via /cy newpost</p>
          <textarea class="form-textarea" id="post-additive-prompt" rows="4"></textarea>
        </div>
        <div class="form-group">
          <label class="form-label">Interaction Additive Prompt</label>
          <p class="form-hint">Extra instructions appended when replying to @Cy mentions</p>
          <textarea class="form-textarea" id="interaction-additive-prompt" rows="4"></textarea>
        </div>
        <button class="btn btn-primary" onclick="saveAdditivePrompts()">Save Additive Prompts</button>
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
      <!-- ── Logging ── -->
      <!-- ── Moderation ── -->
      <div id="section-moderation" class="section">
        <h2 class="section-title">Moderation</h2>
        <p class="section-desc">Configure welcome messages and view recent moderation actions. Bot log, mod log, and welcome channel destinations are configured in the Channels tab.</p>
        <div class="form-group">
          <label class="form-label">Welcome Message</label>
          <p class="form-hint">Custom text for the welcome embed. Use <code>{user}</code> for the mention and <code>{server}</code> for the server name. Leave blank for the default message.</p>
          <textarea class="form-textarea" id="welcome-message" rows="2" placeholder="Hey {user}, welcome to {server}!"></textarea>
        </div>
        <button class="btn btn-primary" onclick="saveWelcomeSettings()" style="margin-bottom:24px">Save Welcome Message</button>

        <hr class="divider">

        <div class="form-group">
          <label class="form-label">Recent Mod Actions</label>
          <p class="form-hint">Last 200 moderation actions performed via slash commands. Resets on bot restart. Click \u21bb to refresh.</p>
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
            <button class="btn btn-sm" style="background:var(--bg-light)" onclick="loadModLog()">&#8635; Refresh</button>
            <span id="mod-log-count" style="font-size:12px;color:var(--text-muted)"></span>
          </div>
          <div id="mod-log-table-wrap" style="overflow-x:auto">
            <table id="mod-log-table" style="width:100%;border-collapse:collapse;font-size:13px">
              <thead>
                <tr style="color:var(--text-muted);text-align:left;border-bottom:1px solid var(--border)">
                  <th style="padding:6px 8px;font-weight:600;white-space:nowrap">Time</th>
                  <th style="padding:6px 8px;font-weight:600">Action</th>
                  <th style="padding:6px 8px;font-weight:600">Target</th>
                  <th style="padding:6px 8px;font-weight:600">Moderator</th>
                  <th style="padding:6px 8px;font-weight:600">Reason</th>
                  <th style="padding:6px 8px;font-weight:600">Details</th>
                </tr>
              </thead>
              <tbody id="mod-log-body">
                <tr><td colspan="6" style="padding:16px 8px;color:var(--text-muted);text-align:center">No actions yet</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- \\u2500\\u2500 Giveaways \\u2500\\u2500 -->
      <div id=\"section-giveaways\" class=\"section\">
        <h2 class=\"section-title\">&#127881; Giveaways</h2>
        <p class=\"section-desc\">Manage active and past giveaways. Use <code>/giveaway start</code> in Discord to create one. Settings here control defaults used by slash commands.</p>

        <h3 style=\"font-size:14px;font-weight:600;margin-bottom:12px\">Settings</h3>
        <div class=\"form-group\">
          <label class=\"form-label\">Default Giveaway Channel</label>
          <p class=\"form-hint\">Channel <code>/giveaway start</code> posts to when no channel is specified.</p>
          <select class=\"form-input\" id=\"giveaway-default-channel\">
            <option value=\"\">&#8212; None (uses current channel) &#8212;</option>
          </select>
        </div>
        <div class=\"form-group\">
          <label class=\"form-label\">Manager Role IDs</label>
          <p class=\"form-hint\">Space-separated role IDs whose members can use <code>/giveaway</code> commands (in addition to admins).</p>
          <input type=\"text\" class=\"form-input\" id=\"giveaway-manager-roles\" placeholder=\"e.g. 123456789 987654321\">
        </div>
        <button class=\"btn btn-primary\" onclick=\"saveGiveawaySettings()\" style=\"margin-bottom:24px\">Save Settings</button>

        <hr class=\"divider\">
        <h3 style=\"font-size:14px;font-weight:600;margin-bottom:4px\">Active Giveaways</h3>
        <div style=\"display:flex;align-items:center;gap:8px;margin-bottom:12px\">
          <button class=\"btn btn-sm\" style=\"background:var(--bg-light)\" onclick=\"loadGiveaways()\">&#8635; Refresh</button>
          <span id=\"giveaway-active-count\" style=\"font-size:12px;color:var(--text-muted)\"></span>
        </div>
        <div id=\"giveaway-active-list\"><p style=\"color:var(--text-muted);font-size:14px\">Loading\\u2026</p></div>

        <hr class=\"divider\">
        <h3 style=\"font-size:14px;font-weight:600;margin-bottom:12px\">Ended Giveaways</h3>
        <div id=\"giveaway-ended-list\"><p style=\"color:var(--text-muted);font-size:14px\">Loading\\u2026</p></div>
      </div>

      <!-- \\u2500\\u2500 Permissions \\u2500\\u2500 -->
      <div id=\"section-permissions\" class=\"section\">
        <h2 class=\"section-title\">Permissions</h2>
        <p class=\"section-desc\">Control what users can do based on their roles. Uses an \\u201cAllow wins\\u201d model \\u2014 if any of a user\\u2019s roles allows a permission, it\\u2019s granted.</p>
        <div class=\"form-group\">
          <div style=\"display:flex;align-items:center;gap:8px;margin-bottom:16px\">
            <select class=\"form-input\" id=\"perm-role-select\" style=\"flex:1\">
              <option value=\"\">\\u2014 Add a role override \\u2014</option>
            </select>
            <button class=\"btn btn-primary\" onclick=\"addRoleToMatrix()\">Add</button>
          </div>
          <div style=\"overflow-x:auto\">
            <table id=\"perm-matrix\" class=\"ch-matrix pm-matrix\">
              <thead id=\"perm-matrix-head\"></thead>
              <tbody id=\"perm-matrix-body\"></tbody>
            </table>
          </div>
        </div>
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
let _everyoneLocked = true;

const CHAN_PERMS = [
  {key: 'can_post', label: 'POST', title: 'Allow /cy newpost to target this channel'},
  {key: 'can_interact', label: 'INTERACT', title: 'Allow @Cy interaction replies in this channel'},
];
/* Matrix column definitions — toggle cols use channel_permissions, excl cols use top-level config fields */
const MATRIX_COLS = [
  {type:'toggle', key:'can_post',    label:'Post',    title:'Allow /cy newpost to send here'},
  {type:'toggle', key:'can_interact',label:'@Cy',     title:'Allow @Cy mention replies here'},
  {type:'excl', field:'default_channel_id', label:'Default', title:'Default channel for /cy newpost (one channel only)'},
  {type:'excl', field:'log_channel_id',     label:'Bot Log', title:'Bot activity log destination (one channel only)'},
  {type:'excl', field:'mod_log_channel_id', label:'Mod Log', title:'Moderation action log destination (one channel only)'},
  {type:'excl', field:'welcome_channel_id', label:'Welcome', title:'Send member welcome embeds here (one channel only)'},
];
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
const _dSavePost = debounce(savePostSettings, 800);
const _dSaveInteraction = debounce(saveInteractionSettings, 800);
const _dSavePersona = debounce(savePersona, 800);
const _dSaveAdditive = debounce(saveAdditivePrompts, 800);

window.addEventListener('DOMContentLoaded', async () => {
  if (!token) { location.href = '/admin'; return; }
  await loadData();
  startStatusPolling();
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
    await renderAll();
  } catch { location.href = '/admin'; }
}

async function renderAll() {
  renderGeneral(); renderAdmins(); renderChannels(); renderPersona(); renderMessages(); renderVideoLines();
  await renderSystemPrompts();
  renderPostSettings(); renderInteractionSettings(); renderModeration();
  renderPermsMatrix(); populateRoleSelect(); renderExclusions(); renderSlang(); renderDefaultResponses();
  renderGiveawaySettings();
  setupAutoSave();
}

/* ── Navigation ── */
function showSection(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.sidebar-item').forEach(s => s.classList.remove('active'));
  document.getElementById('section-' + name).classList.add('active');
  const si = document.querySelector('.sidebar-item[data-section="' + name + '"]');
  if (si) si.classList.add('active');
  if (name === 'moderation') loadModLog();
  if (name === 'giveaways') loadGiveaways();
}

/* ══════════════════════════════════════════════════════════════════════════
   Admins
   ══════════════════════════════════════════════════════════════════════════ */
function renderAdmins() {
  const el = document.getElementById('admin-list');
  el.innerHTML = '';
  const names = config.user_names || {};
  for (const uid of config.admin_user_ids) {
    const isOwner = (uid === config.owner_id);
    const item = document.createElement('div');
    item.className = 'list-item';
    const left = document.createElement('div');
    const nameSpan = document.createElement('span');
    nameSpan.style.fontWeight = '500';
    nameSpan.textContent = names[uid] || uid;
    left.appendChild(nameSpan);
    if (names[uid]) {
      const idSub = document.createElement('span');
      idSub.className = 'id-text';
      idSub.style.cssText = 'font-size:11px;color:var(--text-muted);margin-left:8px';
      idSub.textContent = uid;
      left.appendChild(idSub);
    }
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
function channelName(cid) {
  const ch = (window._channels || []).find(c => c.id === String(cid));
  return ch ? '#' + ch.name + (ch.guild ? ' \\u00b7 ' + ch.guild : '') : '#' + cid;
}

function populateAddChannelDropdown() {
  const sel = document.getElementById('add-channel-select');
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = '<option value="">&#8212; Select a channel &#8212;</option>';
  for (const ch of (window._channels || [])) {
    if (config.active_channels.includes(ch.id)) continue;
    const opt = document.createElement('option');
    opt.value = ch.id;
    opt.textContent = '#' + ch.name + (ch.guild ? ' (' + ch.guild + ')' : '');
    sel.appendChild(opt);
  }
  if (prev) sel.value = prev;
}

function getChannelPerm(cid, key) {
  return ((config.channel_permissions || {})[String(cid)] || {})[key] !== false;
}

async function setChannelPerm(cid, key, val) {
  if (!config.channel_permissions) config.channel_permissions = {};
  if (!config.channel_permissions[String(cid)]) config.channel_permissions[String(cid)] = {};
  config.channel_permissions[String(cid)][key] = val;
  renderChannels();
  await api('PUT', '/api/config', {channel_permissions: config.channel_permissions});
}

function renderChannels() {
  const head = document.getElementById('channel-matrix-head');
  const body = document.getElementById('channel-matrix-body');
  if (!head || !body) return;

  // Header
  head.innerHTML = '';
  const hrow = document.createElement('tr');
  const thName = document.createElement('th');
  thName.textContent = 'Channel';
  thName.style.textAlign = 'left';
  hrow.appendChild(thName);
  for (const col of MATRIX_COLS) {
    const th = document.createElement('th');
    th.textContent = col.label;
    th.title = col.title;
    hrow.appendChild(th);
  }
  hrow.appendChild(document.createElement('th')); // remove col
  head.appendChild(hrow);

  // Rows
  body.innerHTML = '';
  if (!config.active_channels || !config.active_channels.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = MATRIX_COLS.length + 2;
    td.style.cssText = 'padding:20px 0;text-align:center;color:var(--text-muted);font-style:italic';
    td.textContent = 'No channels added yet';
    tr.appendChild(td);
    body.appendChild(tr);
  } else {
    for (const cid of config.active_channels) {
      const tr = document.createElement('tr');

      // Channel name
      const tdName = document.createElement('td');
      tdName.textContent = channelName(cid);
      tdName.title = channelName(cid);
      tr.appendChild(tdName);

      // Matrix cells
      for (const col of MATRIX_COLS) {
        const td = document.createElement('td');
        if (col.type === 'toggle') {
          const on = getChannelPerm(cid, col.key);
          const btn = document.createElement('button');
          btn.className = 'ch-tog ' + (on ? 'on' : 'off');
          btn.textContent = on ? 'ON' : 'OFF';
          btn.title = col.title;
          btn.onclick = () => setChannelPerm(cid, col.key, !getChannelPerm(cid, col.key));
          td.appendChild(btn);
        } else {
          const on = String(config[col.field] || '') === String(cid);
          const btn = document.createElement('button');
          btn.className = 'ch-excl ' + (on ? 'on' : 'off');
          btn.textContent = on ? '\u25cf' : '\u25cb';
          btn.title = col.title;
          btn.onclick = () => setExclusive(col.field, cid);
          td.appendChild(btn);
        }
        tr.appendChild(td);
      }

      // Remove
      const tdRm = document.createElement('td');
      const rm = document.createElement('button');
      rm.className = 'btn btn-danger btn-sm';
      rm.textContent = 'Remove';
      rm.onclick = () => removeChannel(cid);
      tdRm.appendChild(rm);
      tr.appendChild(tdRm);

      body.appendChild(tr);
    }
  }
  populateAddChannelDropdown();
}

async function setExclusive(field, cid) {
  config[field] = (String(config[field] || '') === String(cid)) ? null : cid;
  renderChannels();
  await api('PUT', '/api/config', {[field]: config[field]});
}

async function addChannel() {
  const sel = document.getElementById('add-channel-select');
  const id = sel.value.trim();
  if (!id) return toast('Select a channel first', 'error');
  if (config.active_channels.includes(id)) return toast('Channel already active', 'error');
  config.active_channels.push(id);
  const r = await api('PUT', '/api/config', {active_channels: config.active_channels});
  if (!r) { config.active_channels.pop(); return; }
  sel.value = '';
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

/* ══════════════════════════════════════════════════════════════════════════
   Persona
   ══════════════════════════════════════════════════════════════════════════ */
function renderPersona() {
  document.getElementById('persona-name').value = persona.name || '';
  document.getElementById('persona-bio').value = persona.bio || '';
  document.getElementById('persona-style').value = persona.writing_style || '';
  renderTags('vocab-tags', persona.vocabulary || [], persona, 'vocabulary');
  renderTags('fact-tags', persona.facts || [], persona, 'facts');
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
  renderTags('vocab-tags', persona.vocabulary, persona, 'vocabulary');
  input.value = '';
}

function addFact() {
  const input = document.getElementById('add-fact-input');
  const val = input.value.trim();
  if (!val) return;
  if (!persona.facts) persona.facts = [];
  persona.facts.push(val);
  renderTags('fact-tags', persona.facts, persona, 'facts');
  input.value = '';
}

async function savePersona() {
  persona.name = document.getElementById('persona-name').value;
  persona.bio = document.getElementById('persona-bio').value;
  persona.writing_style = document.getElementById('persona-style').value;
  const r = await api('PUT', '/api/persona', persona);
  if (r) { toast('Persona saved'); await renderSystemPrompts(); }
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
  const r = await api('PUT', '/api/persona', {
    example_messages: persona.example_messages
  });
  if (r) { toast('Messages saved'); await renderSystemPrompts(); }
}

function autoResize(ta) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }

/* ══════════════════════════════════════════════════════════════════════════
   Video Lines
   ══════════════════════════════════════════════════════════════════════════ */
function renderVideoLines() {
  const el = document.getElementById('video-line-list');
  el.innerHTML = '';
  const lines = persona.video_lines || [];
  for (let i = 0; i < lines.length; i++) {
    const item = document.createElement('div');
    item.className = 'message-item';
    const num = document.createElement('span');
    num.className = 'msg-num';
    num.textContent = (i + 1) + '.';
    item.appendChild(num);
    const ta = document.createElement('textarea');
    ta.value = lines[i];
    ta.rows = 1;
    ta.oninput = function() { persona.video_lines[i] = this.value; autoResize(this); };
    item.appendChild(ta);
    const rm = document.createElement('button');
    rm.className = 'btn btn-danger btn-sm';
    rm.textContent = '\\u2715';
    rm.onclick = () => { persona.video_lines.splice(i, 1); renderVideoLines(); };
    rm.style.marginTop = '4px';
    item.appendChild(rm);
    el.appendChild(item);
    autoResize(ta);
  }
}

function addVideoLine() {
  if (!persona.video_lines) persona.video_lines = [];
  persona.video_lines.push('');
  renderVideoLines();
  const items = document.querySelectorAll('#video-line-list .message-item textarea');
  if (items.length) items[items.length - 1].focus();
}

async function saveVideoLines() {
  const r = await api('PUT', '/api/persona', {
    video_lines: persona.video_lines || []
  });
  if (r) { toast('Video lines saved'); await renderSystemPrompts(); }
}

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
  document.getElementById('interaction-rate-limit').value = is_.rate_limit_seconds ?? 300;
  document.getElementById('interaction-max-tokens').value = is_.max_tokens ?? 256;
  document.getElementById('interaction-temperature').value = is_.temperature ?? 0.9;
}

async function saveInteractionSettings() {
  const rl = parseInt(document.getElementById('interaction-rate-limit').value);
  const mt = parseInt(document.getElementById('interaction-max-tokens').value);
  const tp = parseFloat(document.getElementById('interaction-temperature').value);
  const is_ = config.interaction_settings || {};
  const payload = {
    enabled: document.getElementById('interaction-enabled').checked,
    channel_ids: [],
    rate_limit_seconds: isNaN(rl) ? 300 : rl,
    max_tokens: isNaN(mt) ? 256 : mt,
    temperature: isNaN(tp) ? 0.9 : tp,
  };
  const r = await api('PUT', '/api/config', {interaction_settings: payload});
  if (r) { config.interaction_settings = {...(config.interaction_settings || {}), ...payload}; toast('Interaction settings saved'); }
}

/* ══════════════════════════════════════════════════════════════════════════
   System Prompt Overrides
   ══════════════════════════════════════════════════════════════════════════ */
let _promptData = {};

async function renderSystemPrompts() {
  const data = await api('GET', '/api/preview_prompts');
  if (data) _promptData = data;
  document.getElementById('base-prompt-rendered').value = _promptData.rendered || '';
  document.getElementById('post-additive-prompt').value = (config.post_settings || {}).system_prompt || '';
  document.getElementById('interaction-additive-prompt').value = (config.interaction_settings || {}).system_prompt || '';
}

function editTemplate() {
  document.getElementById('base-prompt-template').value = _promptData.template || '';
  document.getElementById('base-prompt-view').style.display = 'none';
  document.getElementById('base-prompt-edit').style.display = 'block';
}

function cancelTemplateEdit() {
  document.getElementById('base-prompt-edit').style.display = 'none';
  document.getElementById('base-prompt-view').style.display = 'block';
}

function resetTemplate() {
  document.getElementById('base-prompt-template').value = _promptData.default_template || '';
}

async function saveTemplate() {
  const template = document.getElementById('base-prompt-template').value;
  const r = await api('PUT', '/api/config', {system_prompt_template: template});
  if (r) {
    config.system_prompt_template = template;
    await renderSystemPrompts();
    cancelTemplateEdit();
    toast('Template saved');
  }
}

async function saveAdditivePrompts() {
  const postSp = document.getElementById('post-additive-prompt').value;
  const intSp = document.getElementById('interaction-additive-prompt').value;
  const r = await api('PUT', '/api/config', {
    post_settings: {system_prompt: postSp},
    interaction_settings: {system_prompt: intSp}
  });
  if (r) {
    config.post_settings.system_prompt = postSp;
    config.interaction_settings.system_prompt = intSp;
    toast('Additive prompts saved');
  }
}

/* ══════════════════════════════════════════════════════════════════════════
   Permissions
   ══════════════════════════════════════════════════════════════════════════ */
const PERMS = [
  {key: 'bypass_cooldown', name: 'Bypass Cooldown', short: 'Cooldown', desc: 'Skip the rate limit between interactions'},
  {key: 'can_interact', name: 'Can @Cy', short: '@Cy', desc: 'Allowed to mention and interact with Cy'},
  {key: 'can_use_commands', name: 'Use /cy Commands', short: '/cy', desc: 'Access to admin slash commands (in admin channel)'},
  {key: 'can_moderate', name: 'Use /mod Commands', short: '/mod', desc: 'Access to all moderation commands (purge, kick, ban, timeout, unban) from any channel'},
  {key: 'can_view_logs', name: 'Can View Logs', short: 'View Logs', desc: 'Allowed to view the bot activity log channel (configure Discord channel perms accordingly)'},
];

function renderPermsMatrix() {
  const head = document.getElementById('perm-matrix-head');
  const body = document.getElementById('perm-matrix-body');
  if (!head || !body) return;
  // Header row
  head.innerHTML = '';
  const hr = document.createElement('tr');
  const thRole = document.createElement('th');
  thRole.textContent = 'Role';
  hr.appendChild(thRole);
  for (const p of PERMS) {
    const th = document.createElement('th');
    th.title = p.name + ' — ' + p.desc;
    th.textContent = p.short || p.name;
    hr.appendChild(th);
  }
  const thAct = document.createElement('th');
  hr.appendChild(thAct);
  head.appendChild(hr);
  // @everyone row (default_permissions)
  body.innerHTML = '';
  body.appendChild(_buildPermRow(null, '@everyone', config.default_permissions || {}, _everyoneLocked));
  // Role rows
  for (const [rid, vals] of Object.entries(config.role_permissions || {})) {
    const role = (window._roles || []).find(r => r.id === rid);
    body.appendChild(_buildPermRow(rid, role ? role.name : rid, vals, false));
  }
}

function _buildPermRow(rid, name, vals, locked) {
  const tr = document.createElement('tr');
  const tdName = document.createElement('td');
  tdName.textContent = name;
  tr.appendChild(tdName);
  for (const p of PERMS) {
    const td = document.createElement('td');
    const val = vals[p.key];
    if (rid === null) {
      // @everyone: only true/false
      if (locked) {
        const span = document.createElement('span');
        span.className = 'pm-cell locked';
        span.textContent = val ? '\u2713' : '\u2715';
        span.style.color = val ? '#4ade80' : '#f87171';
        td.appendChild(span);
      } else {
        const btn = document.createElement('button');
        btn.className = 'pm-cell ' + (val ? 'on' : 'off');
        btn.textContent = val ? '\u2713' : '\u2715';
        btn.onclick = () => setDefaultPerm(p.key, !val);
        td.appendChild(btn);
      }
    } else {
      // Role row: cycle ON → INHERIT → OFF → ON
      const nextVal = val === true ? null : val === false ? true : false;
      const btn = document.createElement('button');
      btn.className = 'pm-cell ' + (val === true ? 'on' : val === false ? 'off' : 'inherit');
      btn.textContent = val === true ? '\u2713' : val === false ? '\u2715' : '\u2013';
      btn.title = val === true ? 'Allow (click to inherit)' : val === false ? 'Deny (click to allow)' : 'Inherit default (click to deny)';
      btn.onclick = () => setRolePerm(rid, p.key, nextVal);
      td.appendChild(btn);
    }
    tr.appendChild(td);
  }
  const tdAct = document.createElement('td');
  if (rid === null) {
    const lockBtn = document.createElement('button');
    lockBtn.title = _everyoneLocked ? 'Unlock to edit default permissions' : 'Lock';
    lockBtn.textContent = _everyoneLocked ? String.fromCodePoint(0x1F512) : String.fromCodePoint(0x1F513);
    lockBtn.style.cssText = 'background:none;border:none;cursor:pointer;font-size:15px;padding:2px 4px;opacity:.7';
    lockBtn.onclick = () => { _everyoneLocked = !_everyoneLocked; renderPermsMatrix(); };
    tdAct.appendChild(lockBtn);
  } else {
    const rmBtn = document.createElement('button');
    rmBtn.title = 'Remove role override';
    rmBtn.textContent = '\u2715';
    rmBtn.style.cssText = 'background:none;border:1px solid var(--border);color:var(--text-muted);cursor:pointer;border-radius:4px;width:24px;height:24px;font-size:11px';
    rmBtn.onclick = () => removeRoleFromMatrix(rid);
    tdAct.appendChild(rmBtn);
  }
  tr.appendChild(tdAct);
  return tr;
}

async function setDefaultPerm(key, val) {
  if (!config.default_permissions) config.default_permissions = {};
  config.default_permissions[key] = val;
  renderPermsMatrix();
  const r = await api('PUT', '/api/config', {default_permissions: config.default_permissions});
  if (r) toast('Permissions saved');
}

async function setRolePerm(rid, key, val) {
  if (!config.role_permissions) config.role_permissions = {};
  if (!config.role_permissions[rid]) config.role_permissions[rid] = {};
  if (val === null) { delete config.role_permissions[rid][key]; } else { config.role_permissions[rid][key] = val; }
  renderPermsMatrix();
  const r = await api('PUT', '/api/config', {role_permissions: config.role_permissions});
  if (r) toast('Permissions saved');
}

function populateRoleSelect() {
  const sel = document.getElementById('perm-role-select');
  sel.innerHTML = '<option value="">\u2014 Add a role override \u2014</option>';
  const added = Object.keys(config.role_permissions || {});
  for (const role of (window._roles || [])) {
    if (added.includes(role.id)) continue;
    const opt = document.createElement('option');
    opt.value = role.id;
    opt.textContent = role.name;
    sel.appendChild(opt);
  }
}

async function addRoleToMatrix() {
  const sel = document.getElementById('perm-role-select');
  const rid = sel.value;
  if (!rid) return;
  if (!config.role_permissions) config.role_permissions = {};
  if (!config.role_permissions[rid]) config.role_permissions[rid] = {};
  const r = await api('PUT', '/api/config', {role_permissions: config.role_permissions});
  if (r) { renderPermsMatrix(); populateRoleSelect(); toast('Role added'); }
}

async function removeRoleFromMatrix(rid) {
  if (config.role_permissions) delete config.role_permissions[rid];
  const r = await api('PUT', '/api/config', {role_permissions: config.role_permissions || {}});
  if (r) { renderPermsMatrix(); populateRoleSelect(); toast('Role removed'); }
}

/* ══════════════════════════════════════════════════════════════════════════
   Exclusions
   ══════════════════════════════════════════════════════════════════════════ */
function renderExclusions() {
  const el = document.getElementById('exclusion-list');
  el.innerHTML = '';
  const items = config.exclusion_list || [];
  for (let i = 0; i < items.length; i++) {
    const e = items[i];
    const row = document.createElement('div');
    row.className = 'excl-row';
    const topic = document.createElement('span');
    topic.className = 'excl-topic';
    topic.textContent = e.topic;
    row.appendChild(topic);
    const toggle = document.createElement('div');
    toggle.className = 'sev-toggle';
    for (const sev of [1, 2, 3]) {
      const btn = document.createElement('button');
      btn.className = 'sev-btn s' + sev + (e.severity === sev ? ' active' : '');
      btn.textContent = sev;
      btn.onclick = () => toggleExclusionSeverity(i, sev);
      toggle.appendChild(btn);
    }
    row.appendChild(toggle);
    const rm = document.createElement('button');
    rm.className = 'btn btn-danger btn-sm';
    rm.textContent = '\\u2715';
    rm.onclick = () => removeExclusion(i);
    row.appendChild(rm);
    el.appendChild(row);
  }
}

async function addExclusion() {
  const input = document.getElementById('add-exclusion-input');
  const sevSel = document.getElementById('add-exclusion-severity');
  const val = input.value.trim();
  if (!val) return;
  if (!config.exclusion_list) config.exclusion_list = [];
  config.exclusion_list.push({topic: val, severity: parseInt(sevSel.value)});
  renderExclusions();
  input.value = '';
  await saveExclusions();
}

async function toggleExclusionSeverity(index, sev) {
  config.exclusion_list[index].severity = sev;
  renderExclusions();
  await saveExclusions();
}

async function removeExclusion(index) {
  config.exclusion_list.splice(index, 1);
  renderExclusions();
  await saveExclusions();
}

async function saveExclusions() {
  await api('PUT', '/api/config', {exclusion_list: config.exclusion_list || []});
}

/* ══════════════════════════════════════════════════════════════════════════
   Slang
   ══════════════════════════════════════════════════════════════════════════ */
function renderSlang() {
  const el = document.getElementById('slang-list');
  el.innerHTML = '';
  const entries = Object.entries(config.slang_dict || {});
  if (entries.length === 0) {
    const empty = document.createElement('p');
    empty.style.cssText = 'color:var(--text-muted);font-size:14px;margin:0';
    empty.textContent = 'No slang defined yet.';
    el.appendChild(empty);
    return;
  }
  for (const [word, def] of entries) {
    const row = document.createElement('div');
    row.className = 'list-item';
    const left = document.createElement('div');
    left.style.flex = '1';
    const wordSpan = document.createElement('span');
    wordSpan.style.cssText = 'font-weight:600;font-size:14px';
    wordSpan.textContent = word;
    const sep = document.createElement('span');
    sep.style.cssText = 'color:var(--text-muted);margin:0 6px';
    sep.textContent = '\u2014';
    const defSpan = document.createElement('span');
    defSpan.style.cssText = 'color:var(--text-secondary);font-size:13px';
    defSpan.textContent = def;
    left.appendChild(wordSpan);
    left.appendChild(sep);
    left.appendChild(defSpan);
    row.appendChild(left);
    const rm = document.createElement('button');
    rm.className = 'btn btn-danger btn-sm';
    rm.textContent = '\u2715';
    rm.onclick = () => removeSlangEntry(word);
    row.appendChild(rm);
    el.appendChild(row);
  }
}

async function addSlangEntry() {
  const wordInput = document.getElementById('add-slang-word');
  const defInput = document.getElementById('add-slang-def');
  const word = wordInput.value.trim();
  const def = defInput.value.trim();
  if (!word || !def) return;
  if (!config.slang_dict) config.slang_dict = {};
  config.slang_dict[word] = def;
  renderSlang();
  wordInput.value = '';
  defInput.value = '';
  await saveSlang();
}

async function removeSlangEntry(word) {
  if (config.slang_dict) delete config.slang_dict[word];
  renderSlang();
  await saveSlang();
}

async function saveSlang() {
  const r = await api('PUT', '/api/config', {slang_dict: config.slang_dict || {}});
  if (r) toast('Slang saved');
}

/* ══════════════════════════════════════════════════════════════════════════
   Default Responses
   ══════════════════════════════════════════════════════════════════════════ */
function renderDefaultResponses() {
  const el = document.getElementById('default-response-list');
  el.innerHTML = '';
  const items = config.default_responses || [];
  for (let i = 0; i < items.length; i++) {
    const item = document.createElement('div');
    item.className = 'list-item';
    const text = document.createElement('span');
    text.style.fontSize = '14px';
    text.textContent = items[i];
    item.appendChild(text);
    const actions = document.createElement('div');
    actions.className = 'actions';
    const rm = document.createElement('button');
    rm.className = 'btn btn-danger btn-sm';
    rm.textContent = 'Remove';
    rm.onclick = () => removeDefaultResponse(i);
    actions.appendChild(rm);
    item.appendChild(actions);
    el.appendChild(item);
  }
}

async function addDefaultResponse() {
  const input = document.getElementById('add-default-response-input');
  const val = input.value.trim();
  if (!val) return;
  if (!config.default_responses) config.default_responses = [];
  config.default_responses.push(val);
  renderDefaultResponses();
  input.value = '';
  const r = await api('PUT', '/api/config', {default_responses: config.default_responses});
  if (r) toast('Response added');
}

async function removeDefaultResponse(i) {
  config.default_responses.splice(i, 1);
  renderDefaultResponses();
  const r = await api('PUT', '/api/config', {default_responses: config.default_responses});
  if (r) toast('Response removed');
}

/* ── General ── */
function renderGeneral() {
  const enabled = config.bot_enabled !== false;
  const indicator = document.getElementById('bot-status-indicator');
  if (indicator) {
    indicator.textContent = enabled ? '\\u25cf Running' : '\\u25cf Stopped';
    indicator.style.color = enabled ? 'var(--green)' : 'var(--red)';
  }
  const btnStart = document.getElementById('btn-bot-start');
  const btnStop = document.getElementById('btn-bot-stop');
  if (btnStart) btnStart.disabled = enabled;
  if (btnStop) btnStop.disabled = !enabled;
}

async function botControl(action) {
  if (action === 'restart') {
    if (!confirm('Restart the bot? It will reconnect to Discord within ~15 seconds.')) return;
  }
  const r = await api('POST', '/api/bot-control', {action});
  if (!r) return;
  if (action === 'enable') { config.bot_enabled = true; renderGeneral(); toast('Bot started'); }
  else if (action === 'disable') { config.bot_enabled = false; renderGeneral(); toast('Bot stopped'); }
  else if (action === 'restart') { toast('Restarting... bot will reconnect shortly'); }
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

/* ── Auto-save ── */
function setupAutoSave() {
  const b = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('input', fn); };
  const bc = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('change', fn); };
  b('post-max-tokens', _dSavePost); b('post-temperature', _dSavePost);
  bc('interaction-enabled', () => saveInteractionSettings());
  b('interaction-rate-limit', _dSaveInteraction); b('interaction-max-tokens', _dSaveInteraction);
  b('interaction-temperature', _dSaveInteraction);
  b('post-additive-prompt', _dSaveAdditive); b('interaction-additive-prompt', _dSaveAdditive);
  b('persona-name', _dSavePersona); b('persona-bio', _dSavePersona); b('persona-style', _dSavePersona);
}

/* ── Status polling ── */
function startStatusPolling() {
  setInterval(async () => {
    try {
      const s = await api('GET', '/api/status');
      if (!s) return;
      if (s.bot_enabled !== config.bot_enabled) {
        config.bot_enabled = s.bot_enabled;
        renderGeneral();
        toast(s.bot_enabled ? 'Bot started externally' : 'Bot stopped externally');
      }
    } catch {}
  }, 15000);
}

/* ── Global Enter key for add inputs ── */
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const id = e.target.id;
  if (id === 'add-admin-input') addAdmin();
  else if (id === 'add-vocab-input') addVocab();
  else if (id === 'add-fact-input') addFact();
  else if (id === 'add-exclusion-input') addExclusion();
  else if (id === 'add-default-response-input') addDefaultResponse();
});

/* ══════════════════════════════════════════════════════════════════════════
   Moderation
   ══════════════════════════════════════════════════════════════════════════ */

const _MOD_ACTION_COLORS = {
  kick: '#f0b232', ban: '#da373c', unban: '#248046',
  timeout: '#f0b232', untimeout: '#5865f2', purge: '#949ba4',
};
const _MOD_ACTION_ICONS = {
  kick: '\\uD83D\\uDC62', ban: '\\uD83D\\uDD28', unban: '\\u2705',
  timeout: '\\u23F1\\uFE0F', untimeout: '\\u2705', purge: '\\uD83D\\uDDD1\\uFE0F',
};

function renderModeration() {
  const wmEl = document.getElementById('welcome-message');
  if (wmEl) wmEl.value = config.welcome_message || '';
}

async function saveWelcomeSettings() {
  const wm = document.getElementById('welcome-message').value;
  const r = await api('PUT', '/api/config', {welcome_message: wm});
  if (r) { config.welcome_message = wm; toast('Welcome message saved'); }
}

async function loadModLog() {
  const data = await api('GET', '/api/mod-log');
  if (!data) return;
  const tbody = document.getElementById('mod-log-body');
  const countEl = document.getElementById('mod-log-count');
  if (!tbody) return;
  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="padding:16px 8px;color:var(--text-muted);text-align:center">No actions recorded yet</td></tr>';
    if (countEl) countEl.textContent = '';
    return;
  }
  if (countEl) countEl.textContent = data.length + ' action' + (data.length !== 1 ? 's' : '');
  tbody.innerHTML = '';
  for (const entry of data) {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid var(--border)';
    const action = (entry.action || '').toLowerCase();
    const color = _MOD_ACTION_COLORS[action] || '#5865f2';
    const icon = _MOD_ACTION_ICONS[action] || '\\uD83D\\uDEE1\\uFE0F';
    const ts = entry.ts ? new Date(entry.ts).toLocaleString() : '';
    const cells = [
      '<td style="padding:6px 8px;white-space:nowrap;color:var(--text-muted);font-size:12px">' + escHtml(ts) + '</td>',
      '<td style="padding:6px 8px;white-space:nowrap"><span style="color:' + color + ';font-weight:600">' + icon + ' ' + escHtml(entry.action || '') + '</span></td>',
      '<td style="padding:6px 8px;font-size:12px;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + escHtml(entry.target || '') + '">' + escHtml(entry.target || '') + '</td>',
      '<td style="padding:6px 8px;font-size:12px;white-space:nowrap">' + escHtml(entry.moderator || '') + '</td>',
      '<td style="padding:6px 8px;font-size:12px;color:var(--text-secondary)">' + escHtml(entry.reason || '\u2014') + '</td>',
      '<td style="padding:6px 8px;font-size:12px;color:var(--text-muted)">' + escHtml(entry.extra || '') + '</td>',
    ];
    tr.innerHTML = cells.join('');
    tbody.appendChild(tr);
  }
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ══════════════════════════════════════════════════════════════════════════
   Giveaways
   ══════════════════════════════════════════════════════════════════════════ */

let _giveawaySettings = {};

function renderGiveawaySettings() {
  // Populate default channel dropdown
  const sel = document.getElementById('giveaway-default-channel');
  if (!sel) return;
  const prev = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  for (const ch of (window._channels || [])) {
    const opt = new Option('#' + ch.name + ' (' + ch.guild + ')', ch.id);
    sel.add(opt);
  }
  const defCid = String(_giveawaySettings.default_channel_id || '');
  sel.value = defCid;
  const rolesEl = document.getElementById('giveaway-manager-roles');
  if (rolesEl) rolesEl.value = (_giveawaySettings.manager_role_ids || []).join(' ');
}

async function loadGiveaways() {
  const data = await api('GET', '/api/giveaways');
  if (!data) return;
  _giveawaySettings = data.settings || {};
  renderGiveawaySettings();
  const now = Date.now() / 1000;
  const active = (data.giveaways || []).filter(g => !g.ended);
  const ended = (data.giveaways || []).filter(g => g.ended);

  const activeCountEl = document.getElementById('giveaway-active-count');
  if (activeCountEl) activeCountEl.textContent = active.length + ' active';

  document.getElementById('giveaway-active-list').innerHTML =
    active.length ? active.map(g => giveawayCard(g, false, now)).join('') :
    '<p style="color:var(--text-muted);font-size:14px">No active giveaways.</p>';

  document.getElementById('giveaway-ended-list').innerHTML =
    ended.length ? ended.map(g => giveawayCard(g, true, now)).join('') :
    '<p style="color:var(--text-muted);font-size:14px">No ended giveaways.</p>';
}

function giveawayCard(g, ended, now) {
  const remaining = Math.max(0, g.end_time - now);
  const endDate = new Date(g.end_time * 1000).toLocaleString();
  const entries = (g.entries || []).length;
  const excluded = g.excluded_entries || [];
  const mid = escHtml(String(g.message_id));
  const msgUrl = 'https://discord.com/channels/' + g.guild_id + '/' + g.channel_id + '/' + g.message_id;
  const badge = ended
    ? '<span style="background:var(--bg-light);color:var(--text-muted);padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">ENDED</span>'
    : '<span style="background:var(--accent);color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">ACTIVE</span>';
  const timeStr = ended ? 'Ended: ' + endDate : 'Ends in: <b>' + fmtDuration(remaining) + '</b> (' + endDate + ')';
  const actionBtns = ended
    ? '<button class="btn btn-sm" style="background:var(--accent)" data-ga-fn="reroll" data-ga-id="' + mid + '">Reroll</button> '
      + '<button class="btn btn-sm btn-danger" data-ga-fn="delete" data-ga-id="' + mid + '">Delete</button>'
    : '<button class="btn btn-sm btn-danger" data-ga-fn="end" data-ga-id="' + mid + '">Force End</button> '
      + '<button class="btn btn-sm btn-danger" style="margin-left:4px" data-ga-fn="delete" data-ga-id="' + mid + '">Delete</button>';

  /* Participants section — only shown in active giveaways */
  let participantsHtml = '';
  if (!ended) {
    const allEntries = g.entries || [];
    const eligibleCount = allEntries.filter(function(uid) { return excluded.indexOf(uid) === -1; }).length;
    const detailsId = 'ga-participants-' + mid;
    if (allEntries.length === 0) {
      participantsHtml = '<div style="margin-top:10px;font-size:12px;color:var(--text-muted)">No participants yet.</div>';
    } else {
      const rows = allEntries.map(function(uid) {
        const isExcluded = excluded.indexOf(uid) !== -1;
        const icon = isExcluded ? '&#128683;' : '&#9989;';
        const style = isExcluded
          ? 'text-decoration:line-through;color:var(--text-muted);opacity:.6'
          : 'color:var(--text-primary)';
        return '<div style="display:flex;align-items:center;gap:8px;padding:3px 0;border-bottom:1px solid var(--bg-light)">'
          + '<span style="' + style + ';font-size:13px;font-family:monospace;flex:1">&lt;@' + uid + '&gt;</span>'
          + '<button class="btn btn-sm" style="padding:1px 6px;font-size:13px;background:transparent;border:none;cursor:pointer" '
          + 'title="' + (isExcluded ? 'Re-include' : 'Exclude') + '" '
          + 'data-ga-fn="toggleExclude" data-ga-id="' + mid + '" data-ga-uid="' + uid + '">'
          + icon + '</button>'
          + '</div>';
      }).join('');
      participantsHtml = '<details id="' + detailsId + '" style="margin-top:10px">'
        + '<summary style="cursor:pointer;font-size:12px;color:var(--accent);user-select:none">'
        + '&#128101; Participants (' + allEntries.length + ' entered, '
        + eligibleCount + ' eligible)</summary>'
        + '<div style="margin-top:8px;max-height:220px;overflow-y:auto;padding:0 4px">'
        + rows
        + '</div></details>';
    }
  }

  return '<div style="background:var(--bg-dark);border-radius:8px;padding:16px;margin-bottom:12px">'
    + '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;flex-wrap:wrap">'
    + '<div><span style="font-weight:600;font-size:15px">&#127881; ' + escHtml(g.prize) + '</span>&nbsp;&nbsp;' + badge + '</div>'
    + '<div style="display:flex;gap:8px;flex-wrap:wrap">' + actionBtns
    + ' <a href="' + escHtml(msgUrl) + '" target="_blank" style="font-size:12px;color:var(--accent);white-space:nowrap;align-self:center">&#x2197; View</a></div></div>'
    + '<div style="margin-top:8px;font-size:13px;color:var(--text-secondary)">'
    + timeStr + ' &nbsp;|&nbsp; ' + entries + ' ' + (entries === 1 ? 'entry' : 'entries')
    + ' &nbsp;|&nbsp; Winners: ' + g.winner_count
    + (ended && g.winners && g.winners.length ? ' &nbsp;|&nbsp; Drawn: <code>' + escHtml(g.winners.map(function(w){return '<@'+w+'>';}).join(', ')) + '</code>' : '')
    + '</div>'
    + (g.announcement_message ? '<div style="margin-top:8px;font-size:12px;color:var(--text-muted);background:var(--bg-medium);border-radius:4px;padding:6px 10px"><b>Announcement:</b> ' + escHtml(g.announcement_message) + '</div>' : '')
    + participantsHtml
    + '</div>';
}

function fmtDuration(secs) {
  secs = Math.max(0, Math.floor(secs));
  if (secs < 60) return secs + 's';
  if (secs < 3600) { const m = Math.floor(secs/60), s = secs%60; return m + 'm ' + s + 's'; }
  if (secs < 86400) { const h = Math.floor(secs/3600), m = Math.floor((secs%3600)/60); return h + 'h ' + m + 'm'; }
  const d = Math.floor(secs/86400), h = Math.floor((secs%86400)/3600); return d + 'd ' + h + 'h';
}

async function saveGiveawaySettings() {
  const chanId = document.getElementById('giveaway-default-channel').value || null;
  const roleRaw = document.getElementById('giveaway-manager-roles').value.trim();
  const roleIds = roleRaw ? roleRaw.split(/\s+/).filter(Boolean) : [];
  const r = await api('PUT', '/api/giveaway-settings', {
    default_channel_id: chanId || null,
    manager_role_ids: roleIds,
  });
  if (r) { toast('Giveaway settings saved'); await loadGiveaways(); }
}

async function giveawayToggleExclude(msgId, userId) {
  const r = await api('POST', '/api/giveaways/' + msgId + '/toggle-exclude/' + userId, {});
  if (r) { await loadGiveaways(); }
}

async function giveawayForceEnd(msgId) {
  if (!confirm('End this giveaway now and pick winners?')) return;
  const r = await api('POST', '/api/giveaways/' + msgId + '/end', {});
  if (r) { toast('Giveaway ended'); await loadGiveaways(); }
}

async function giveawayReroll(msgId) {
  if (!confirm('Pick a new winner for this ended giveaway?')) return;
  const r = await api('POST', '/api/giveaways/' + msgId + '/reroll', {});
  if (r) { toast('Rerolled!'); await loadGiveaways(); }
}

async function giveawayDelete(msgId) {
  if (!confirm('Delete this giveaway record and its Discord message?')) return;
  const r = await api('DELETE', '/api/giveaways/' + msgId);
  if (r) { toast('Giveaway deleted'); await loadGiveaways(); }
}

/* Delegated click handler for giveaway action buttons */
document.addEventListener('click', function(e) {
  const btn = e.target.closest('[data-ga-fn]');
  if (!btn) return;
  const fn = btn.dataset.gaFn, mid = btn.dataset.gaId;
  if (!mid) return;
  if (fn === 'end') giveawayForceEnd(mid);
  else if (fn === 'reroll') giveawayReroll(mid);
  else if (fn === 'delete') giveawayDelete(mid);
  else if (fn === 'toggleExclude') {
    const uid = btn.dataset.gaUid;
    if (uid) giveawayToggleExclude(mid, uid);
  }
});

</script>
</body>
</html>"""
