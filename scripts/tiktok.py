"""TikTok part of the Daily Brilliancy automation.

connect <code> : exchange an authorization code for tokens and store them encrypted
publish        : post new videos (videos/<name>.mp4 + .json) according to config.json["tiktok_mode"]
                 "inbox"  -> video goes to the TikTok app inbox as a draft (works before TikTok's audit)
                 "direct" -> posted publicly right away (needs TikTok's audit)
                 "off"    -> nothing
"""
import json, os, sys, subprocess, datetime, pathlib, time, requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
STATE = ROOT / "state"; STATE.mkdir(exist_ok=True)
TOKFILE = STATE / "tiktok_token.enc"
REDIRECT = "https://dimanonorm.github.io/daily-brilliancy-videos/tiktok-callback.html"
API = "https://open.tiktokapis.com/v2"
LOG = []

def log(m):
    print(m, flush=True); LOG.append(m)

def env(k):
    v = os.environ.get(k, "").strip().strip('"').strip("'").strip()
    if not v:
        log(f"Missing secret {k}"); sys.exit(1)
    return v

def crypt(data: bytes, decrypt=False):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-a", "-A", "-pass", "env:TT_TOKEN_KEY"]
    if decrypt: args.insert(3, "-d")
    r = subprocess.run(args, input=data, capture_output=True, env={**os.environ, "TT_TOKEN_KEY": env("TT_TOKEN_KEY")})
    if r.returncode: log("Encryption error: " + r.stderr.decode()[:200]); sys.exit(1)
    return r.stdout

def save_tokens(t):
    t["saved_at"] = int(time.time())
    TOKFILE.write_bytes(crypt(json.dumps(t).encode()))

def load_tokens():
    if not TOKFILE.exists():
        log("TikTok is not connected yet (run the 'TikTok connect' workflow)."); return None
    return json.loads(crypt(TOKFILE.read_bytes(), decrypt=True))

def token_call(data):
    r = requests.post(f"{API}/oauth/token/", data={"client_key": env("TT_CLIENT_KEY"), "client_secret": env("TT_CLIENT_SECRET"), **data},
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
    j = r.json()
    if "access_token" not in j:
        log(f"TOKEN ERROR {r.status_code}: {json.dumps(j)[:300]}"); sys.exit(1)
    return j

def connect(code):
    j = token_call({"code": code.strip(), "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    save_tokens(j)
    log(f"TikTok connected. Scopes: {j.get('scope')}; refresh token valid {int(j.get('refresh_expires_in', 0))//86400} days.")

def fresh_access():
    t = load_tokens()
    if not t: return None
    j = token_call({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
    save_tokens(j)
    return j["access_token"]

def api(path, tok, body):
    r = requests.post(f"{API}{path}", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"},
                      data=json.dumps(body), timeout=60)
    j = r.json()
    if j.get("error", {}).get("code", "ok") != "ok":
        raise RuntimeError(f"{path}: {json.dumps(j['error'])[:300]}")
    return j.get("data", {})

def put_file(url, mp4):
    size = mp4.stat().st_size
    with open(mp4, "rb") as f:
        r = requests.put(url, data=f, timeout=900, headers={"Content-Type": "video/mp4", "Content-Length": str(size),
                                                         "Content-Range": f"bytes 0-{size-1}/{size}"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"upload PUT {r.status_code}: {r.text[:200]}")

def caption(meta):
    c = meta.get("tiktok_caption") or (CFG["comment"] + " " + meta["title"] + " " + meta.get("hashtags", ""))
    return c.strip()[:2200]

def publish():
    mode = CFG.get("tiktok_mode", "off")
    if mode == "off":
        log("TikTok posting is off in config.json."); return
    tok = fresh_access()
    if not tok: return
    done = json.loads((STATE / "tiktok_uploaded.json").read_text()) if (STATE / "tiktok_uploaded.json").exists() else {}
    limit = int(CFG.get("max_uploads_per_run", 5))
    for mp in sorted((ROOT / "videos").glob("*.json")):
        name = mp.stem; mp4 = mp.with_suffix(".mp4")
        if name in done or not mp4.exists() or limit <= 0: continue
        meta = json.loads(mp.read_text())
        if meta.get("tiktok") is False: continue
        size = mp4.stat().st_size
        src = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}
        try:
            if mode == "direct":
                info = api("/post/publish/creator_info/query/", tok, {})
                levels = info.get("privacy_level_options", [])
                priv = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in levels else (levels[0] if levels else "SELF_ONLY")
                d = api("/post/publish/video/init/", tok, {"post_info": {"title": caption(meta), "privacy_level": priv,
                        "disable_duet": False, "disable_comment": False, "disable_stitch": False, "video_cover_timestamp_ms": 1000},
                        "source_info": src})
            else:
                d = api("/post/publish/inbox/video/init/", tok, {"source_info": src})
            put_file(d["upload_url"], mp4)
        except RuntimeError as e:
            log(f"TIKTOK FAILED {name}: {e}"); continue
        limit -= 1
        done[name] = {"publish_id": d.get("publish_id"), "mode": mode, "caption": caption(meta),
                      "at": datetime.datetime.utcnow().isoformat() + "Z"}
        (STATE / "tiktok_uploaded.json").write_text(json.dumps(done, indent=2, ensure_ascii=False) + "\n")
        log(f"TIKTOK {'POSTED' if mode == 'direct' else 'SENT TO INBOX'} {name} (publish_id {d.get('publish_id')})")
        time.sleep(5)

if __name__ == "__main__":
    try:
        if len(sys.argv) > 2 and sys.argv[1] == "connect": connect(sys.argv[2])
        else: publish()
    finally:
        (STATE / "tiktok_last_run.md").write_text(f"# TikTok last run {datetime.datetime.utcnow().isoformat()}Z\n\n" + "\n".join(f"- {l}" for l in LOG) + "\n")
