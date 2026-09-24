"""Every way activation can fail must report the right thing to the installer.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

Exists because of a real bug: pointing the licence server address at the
request PORTAL (which has no activation endpoint, and whose SPA catch-all is
GET-only) returned 405, and the integration told the installer their licence
had been declined. It had not - nothing had even been asked. Only an explicit
{"status": "denied"} is a refusal; everything else is a plumbing problem and
must say so.

    python3 tests/test_activation_errors.py
"""
import asyncio, copy, sys, types, json, base64
from datetime import datetime, timezone
import aiohttp
from aiohttp import web

import os
PKG=os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ar_hdl_buspro")))
FAKE_DISK={}; SESSION=None
def callback(f): return f
class HomeAssistant:
    def __init__(s): s.data={}; s.config_entries=EM(); s._t=[]
    def async_create_task(s,c):
        t=asyncio.ensure_future(c); s._t.append(t); return t
    async def drain(s):
        while s._t:
            p,s._t=s._t,[]; await asyncio.gather(*p,return_exceptions=True)
class FE:
    def __init__(s,i,d=None): s.entry_id=i; s.data=d or {}
class EM:
    def __init__(s): s.entries=[]
    def async_entries(s,d): return list(s.entries)
    def async_update_entry(s,e,data=None):
        if data is not None: e.data=data
class Store:
    def __init__(s,h,v,k): s.key=k
    async def async_load(s): return copy.deepcopy(FAKE_DISK.get(s.key))
    async def async_save(s,d): FAKE_DISK[s.key]=copy.deepcopy(d)
m={}
for n in ("homeassistant","homeassistant.core","homeassistant.helpers",
          "homeassistant.helpers.storage","homeassistant.helpers.instance_id",
          "homeassistant.helpers.aiohttp_client","homeassistant.config_entries"):
    m[n]=types.ModuleType(n)
async def _u(h=None): return "6f1c9a0e8b7d4f2a9c3e5d7b1a0f4e82"
m["homeassistant.core"].HomeAssistant=HomeAssistant
m["homeassistant.core"].callback=callback
m["homeassistant.helpers.storage"].Store=Store
m["homeassistant.helpers.instance_id"].async_get=_u
m["homeassistant.helpers.aiohttp_client"].async_get_clientsession=lambda h: SESSION
m["homeassistant.config_entries"].ConfigEntry=FE
m["homeassistant.helpers"].instance_id=m["homeassistant.helpers.instance_id"]
sys.modules.update(m)
sys.path.insert(0,PKG)
import licensing

R=[]
def check(n,c,d=""):
    R.append((n,bool(c))); print(f"[{'PASS' if c else 'FAIL'}] {n}"+(f"  -- {d}" if d else ""))

async def serve(handler, port, method="post", path="/api/activation/activate"):
    app=web.Application()
    getattr(app.router,f"add_{method}")(path,handler)
    # portal-shaped SPA catch-all: GET only, exactly like license-portal
    app.router.add_get("/{tail:.*}", lambda r: web.Response(text="<html>"))
    runner=web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner,"127.0.0.1",port).start()
    return runner

async def mgr():
    global FAKE_DISK
    FAKE_DISK={}
    h=HomeAssistant(); h.config_entries.entries.append(FE("e0"))
    mg=licensing.ARHDLLicenseManager(h); await mg.async_load(); await h.drain()
    return mg

async def main():
    global SESSION
    SESSION=aiohttp.ClientSession()
    try:
        # --- THE REPORTED CASE: portal has no activation endpoint (405) ---
        app=web.Application()
        app.router.add_get("/{tail:.*}", lambda r: web.Response(text="<html>portal</html>"))
        runner=web.AppRunner(app); await runner.setup()
        await web.TCPSite(runner,"4100").start() if False else await web.TCPSite(runner,"127.0.0.1",4100).start()
        mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4100")
        check("portal URL (405) -> 'no activation endpoint', NOT a refusal",
              reason=="no_activation_endpoint", str(reason))
        await runner.cleanup()

        # --- 404: endpoint not deployed ---
        async def nf(r): return web.json_response({"detail":"Not Found"},status=404)
        rn=await serve(nf,4101); mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4101")
        check("404 -> no_activation_endpoint", reason=="no_activation_endpoint", str(reason))
        await rn.cleanup()

        # --- 500 ---
        async def boom(r): return web.json_response({"detail":"nope"},status=500)
        rn=await serve(boom,4102); mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4102")
        check("500 -> cannot_reach_server", reason=="cannot_reach_server", str(reason))
        await rn.cleanup()

        # --- 200 but junk body ---
        async def junk(r): return web.json_response({"hello":"world"})
        rn=await serve(junk,4103); mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4103")
        check("unexpected body -> cannot_reach_server", reason=="cannot_reach_server", str(reason))
        await rn.cleanup()

        # --- 200 HTML (a proxy serving the SPA) ---
        async def html(r): return web.Response(text="<!doctype html>", content_type="text/html")
        rn=await serve(html,4104); mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4104")
        check("HTML body -> cannot_reach_server", reason=="cannot_reach_server", str(reason))
        await rn.cleanup()

        # --- genuine denial ---
        async def den(r): return web.json_response({"status":"denied","reason":"revoked"})
        rn=await serve(den,4105); mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4105")
        check("explicit denied -> activation_refused (the ONLY refusal)",
              reason=="activation_refused", str(reason))
        await rn.cleanup()

        # --- pending ---
        async def pend(r): return web.json_response({"status":"pending"})
        rn=await serve(pend,4106); mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4106")
        check("pending -> pending_approval", reason=="pending_approval", str(reason))
        await rn.cleanup()

        # --- 429 ---
        async def busy(r): return web.json_response({"detail":"slow"},status=429)
        rn=await serve(busy,4107); mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4107")
        check("429 -> checked_recently", reason=="checked_recently", str(reason))
        await rn.cleanup()

        # --- requester name + email reach the server and persist ---
        seen={}
        async def cap(r):
            seen.update(await r.json()); return web.json_response({"status":"pending"})
        rn=await serve(cap,4108); mg=await mgr()
        check("no contact recorded on a fresh install", not mg.has_contact)
        await mg.async_set_contact("  Kruger Lodge ", "ops@example.co.za ")
        st,reason=await mg.async_activate("http://127.0.0.1:4108")
        check("request carries name + email",
              seen.get("name")=="Kruger Lodge" and seen.get("email")=="ops@example.co.za"
              and seen.get("server_id")==mg.server_id, json.dumps(seen))
        check("contact persisted to storage",
              any(d.get("contact_email")=="ops@example.co.za" for d in FAKE_DISK.values()
                  if isinstance(d,dict)))
        await rn.cleanup()

        # --- connection refused ---
        mg=await mgr()
        st,reason=await mg.async_activate("http://127.0.0.1:4199")
        check("nothing listening -> cannot_reach_server", reason=="cannot_reach_server", str(reason))
    finally:
        await SESSION.close()
    f=[n for n,ok in R if not ok]
    print(f"\n{len(R)-len(f)}/{len(R)} passed")
    return 1 if f else 0
sys.exit(asyncio.run(main()))
