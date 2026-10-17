"""Daily Brilliancy YouTube automation.

- Uploads every videos/<name>.mp4 that has a videos/<name>.json metadata file
  and is not yet recorded in state/uploaded.json (only when config.json allows it).
- Makes sure every recent video on the channel has the standard comment.
"""
import json, os, sys, time, datetime, pathlib, requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
STATE = ROOT / "state"
API = "https://www.googleapis.com/youtube/v3"
LOG = []

def log(msg):
    print(msg, flush=True)
    LOG.append(msg)

def load(name, default):
    p = STATE / name
    return json.loads(p.read_text()) if p.exists() else default

def save(name, data):
    STATE.mkdir(exist_ok=True)
    (STATE / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

def token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token"}, timeout=60)
    if r.status_code != 200:
        log(f"TOKEN ERROR {r.status_code}: {r.text[:300]}")
        sys.exit(1)
    return r.json()["access_token"]

def api(method, path, tok, **kw):
    r = requests.request(method, API + path, headers={"Authorization": f"Bearer {tok}"}, timeout=120, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
    return r.json() if r.text else {}

def upload(tok, mp4, meta):
    body = {
        "snippet": {"title": meta["title"][:100], "description": meta.get("description", ""),
                    "tags": meta.get("tags", []), "categoryId": str(meta.get("categoryId", CFG.get("categoryId", "20")))},
        "status": {"privacyStatus": meta.get("privacy", CFG.get("privacy", "public")),
                   "selfDeclaredMadeForKids": False}}
    if meta.get("publishAt"):
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = meta["publishAt"]
    size = mp4.stat().st_size
    r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                      data=json.dumps(body), timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"upload init {r.status_code}: {r.text[:400]}")
    with open(mp4, "rb") as f:
        r2 = requests.put(r.headers["Location"], data=f, headers={"Content-Type": "video/mp4"}, timeout=900)
    if r2.status_code not in (200, 201):
        raise RuntimeError(f"upload put {r2.status_code}: {r2.text[:400]}")
    v = r2.json()
    return v["id"], v.get("status", {})

def my_channel(tok):
    d = api("GET", "/channels?part=id,contentDetails&mine=true", tok)
    it = d["items"][0]
    return it["id"], it["contentDetails"]["relatedPlaylists"]["uploads"]

def recent_uploads(tok, playlist, n):
    d = api("GET", f"/playlistItems?part=contentDetails,snippet&maxResults={n}&playlistId={playlist}", tok)
    return [(i["contentDetails"]["videoId"], i["snippet"]["title"]) for i in d.get("items", [])]

def has_my_comment(tok, vid, chan, text):
    try:
        d = api("GET", f"/commentThreads?part=snippet&videoId={vid}&maxResults=100&order=time", tok)
    except RuntimeError as e:
        if "commentsDisabled" in str(e) or "403" in str(e):
            return None
        raise
    for t in d.get("items", []):
        s = t["snippet"]["topLevelComment"]["snippet"]
        if s.get("authorChannelId", {}).get("value") == chan and text.lower() in s.get("textOriginal", "").lower():
            return True
    return False

def comment(tok, vid, text):
    api("POST", "/commentThreads?part=snippet", tok, json={
        "snippet": {"videoId": vid, "topLevelComment": {"snippet": {"textOriginal": text}}}})

def main():
    tok = token()
    chan, playlist = my_channel(tok)
    log(f"Channel: {chan}")
    uploaded = load("uploaded.json", {})
    commented = load("commented.json", {})
    text = CFG["comment"]

    # 1) uploads
    if CFG.get("youtube_upload") or os.environ.get("FORCE_UPLOAD") == "1":
        limit = int(CFG.get("max_uploads_per_run", 5))
        for meta_path in sorted((ROOT / "videos").glob("*.json")):
            name = meta_path.stem
            mp4 = meta_path.with_suffix(".mp4")
            if name in uploaded or not mp4.exists():
                continue
            if limit <= 0:
                log("Upload limit for this run reached; the rest go next run.")
                break
            meta = json.loads(meta_path.read_text())
            try:
                vid, st = upload(tok, mp4, meta)
            except RuntimeError as e:
                log(f"UPLOAD FAILED {name}: {e}")
                if "quotaExceeded" in str(e) or "uploadLimitExceeded" in str(e):
                    break
                continue
            limit -= 1
            uploaded[name] = {"id": vid, "url": f"https://youtube.com/shorts/{vid}",
                              "privacy": st.get("privacyStatus"), "at": datetime.datetime.utcnow().isoformat() + "Z"}
            save("uploaded.json", uploaded)
            log(f"UPLOADED {name} -> https://youtube.com/shorts/{vid} (privacy: {st.get('privacyStatus')})")
            time.sleep(5)
    else:
        log("YouTube upload is switched off in config.json (waiting for Google's API audit).")

    # 2) comments on recent channel videos
    for vid, title in recent_uploads(tok, playlist, int(CFG.get("check_last_n", 25))):
        if vid in commented:
            continue
        try:
            h = has_my_comment(tok, vid, chan, text)
            if h is None:
                log(f"Comments unavailable on {vid} ({title}); will retry later.")
                continue
            if not h:
                comment(tok, vid, text)
                log(f"COMMENTED on {vid} ({title})")
            else:
                log(f"Already has comment: {vid} ({title})")
            commented[vid] = {"title": title, "at": datetime.datetime.utcnow().isoformat() + "Z"}
            save("commented.json", commented)
        except RuntimeError as e:
            log(f"COMMENT FAILED {vid} ({title}): {e}")

if __name__ == "__main__":
    try:
        main()
    finally:
        STATE.mkdir(exist_ok=True)
        (STATE / "last_run.md").write_text(
            f"# Last run {datetime.datetime.utcnow().isoformat()}Z\n\n" + "\n".join(f"- {l}" for l in LOG) + "\n")
