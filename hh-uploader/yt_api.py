"""Our own YouTube uploader / channel manager on the OFFICIAL YouTube Data API v3 (Saksham, 18 Nov 2026: 'automate everything').

Credentials come ONLY from environment variables that Saksham enters himself in the cloud environment settings (never in chat, never in git):
  YT_CLIENT_ID, YT_CLIENT_SECRET  - his own Google Cloud OAuth client ("HH Uploader", web client, published app)
  YT_REFRESH_TOKEN                - from the OAuth Playground, authorised on the Headlines & History brand channel
Scopes: youtube.upload, youtube.force-ssl, yt-analytics.readonly.

What the API can do here: upload, title/description/tags/category/language, made-for-kids, altered/synthetic flag, schedule (publishAt),
custom thumbnail, caption (.srt) upload, add to playlist, read and reply to comments, analytics.
What NO app can do (YouTube does not offer it): end screens, cards, pinning or hearting a comment, Test & compare thumbnails.
IMPORTANT: until Google audits our API project, videos UPLOADED through the API are locked as private by YouTube. Until then Saksham uploads
the file in Studio (as Private, nothing else filled in) and `publish --video-id` / `publish --latest` does everything else.

Usage (repos/OpenMontage/.venv/bin/python or python3):
  python3 tools/yt_api.py whoami
  python3 tools/yt_api.py publish videos/018-nvidia-scandal --latest          # fill in the newest private upload from upload.json
  python3 tools/yt_api.py publish videos/018-nvidia-scandal --video-id ID
  python3 tools/yt_api.py publish videos/018-nvidia-scandal --upload          # full upload (only after the API audit)
  python3 tools/yt_api.py videos                                              # list our videos with status
  python3 tools/yt_api.py comments [--all]                                    # comments we have not replied to yet
  python3 tools/yt_api.py reply COMMENT_ID "text"
  python3 tools/yt_api.py stats VIDEO_ID                                      # views, CTR proxy, average view %, etc.
upload.json (per video folder): {"file", "title", "description", "tags", "categoryId", "language", "madeForKids", "synthetic",
  "publishAt" (ISO with Z or offset, or null for private), "thumbnail", "captions", "playlist"}"""
import os, sys, json, time, argparse, datetime, mimetypes
import requests

API = "https://www.googleapis.com/youtube/v3"; UP = "https://www.googleapis.com/upload/youtube/v3"
_tok = {"v": None, "exp": 0}

def token():
    if _tok["v"] and time.time() < _tok["exp"] - 60: return _tok["v"]
    miss = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN") if not os.environ.get(k)]
    if miss: sys.exit("missing environment variables: " + ", ".join(miss) + " (Saksham adds them in the environment settings)")
    r = requests.post("https://oauth2.googleapis.com/token", data=dict(client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                                                                     refresh_token=os.environ["YT_REFRESH_TOKEN"], grant_type="refresh_token"), timeout=30)
    if r.status_code != 200: sys.exit(f"token refresh failed: {r.status_code} {r.json().get('error')} {r.json().get('error_description', '')}")
    j = r.json(); _tok.update(v=j["access_token"], exp=time.time() + j.get("expires_in", 3600)); return _tok["v"]

def H(extra=None): h = {"Authorization": "Bearer " + token()}; h.update(extra or {}); return h

def call(method, url, **kw):
    for attempt in range(5):
        r = requests.request(method, url, headers=H(kw.pop("headers", None)), timeout=kw.pop("timeout", 60), **kw)
        if r.status_code in (500, 502, 503, 504): time.sleep(2 ** attempt); continue
        if r.status_code >= 400: sys.exit(f"{method} {url.split('?')[0]} -> {r.status_code}: {r.text[:600]}")
        return r.json() if r.text else {}
    sys.exit(f"{method} {url} kept failing")

def me():
    j = call("GET", f"{API}/channels?part=snippet,contentDetails,statistics&mine=true")["items"][0]
    return j

def videos(n=50):
    up = me()["contentDetails"]["relatedPlaylists"]["uploads"]
    items = call("GET", f"{API}/playlistItems?part=contentDetails&maxResults={n}&playlistId={up}").get("items", [])
    ids = ",".join(i["contentDetails"]["videoId"] for i in items)
    if not ids: return []
    return call("GET", f"{API}/videos?part=snippet,status,statistics,processingDetails&id={ids}").get("items", [])

def meta_body(m, vid=None):
    sn = {"title": m["title"][:100], "description": m["description"][:5000], "tags": m.get("tags", []), "categoryId": str(m.get("categoryId", "27")),
          "defaultLanguage": m.get("language", "en-US"), "defaultAudioLanguage": m.get("language", "en-US")}
    st = {"selfDeclaredMadeForKids": bool(m.get("madeForKids", False)), "containsSyntheticMedia": bool(m.get("synthetic", False)),
          "embeddable": True, "license": "youtube", "publicStatsViewable": True}
    if m.get("publishAt"): st.update(privacyStatus="private", publishAt=m["publishAt"])
    else: st["privacyStatus"] = m.get("privacy", "private")
    b = {"snippet": sn, "status": st}
    if vid: b["id"] = vid
    return b

def upload(path, m):
    """resumable upload in 32 MB chunks"""
    size = os.path.getsize(path)
    r = requests.post(f"{UP}/videos?uploadType=resumable&part=snippet,status&notifySubscribers=true", json=meta_body(m),
                      headers=H({"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}), timeout=60)
    if r.status_code != 200: sys.exit(f"upload start failed {r.status_code}: {r.text[:500]}")
    loc = r.headers["Location"]; pos = 0; CH = 32 * 1024 * 1024
    with open(path, "rb") as f:
        while pos < size:
            f.seek(pos); chunk = f.read(CH); end = pos + len(chunk) - 1
            try:
                r = requests.put(loc, data=chunk, headers=H({"Content-Range": f"bytes {pos}-{end}/{size}"}), timeout=600)
            except requests.RequestException as e:
                print("retry after", e); time.sleep(5)
                q = requests.put(loc, headers=H({"Content-Range": f"bytes */{size}"}), timeout=60)
                pos = int(q.headers.get("Range", "bytes=0--1").split("-")[1]) + 1 if q.status_code == 308 else pos; continue
            if r.status_code in (200, 201): print("uploaded"); return r.json()["id"]
            if r.status_code == 308: pos = int(r.headers["Range"].split("-")[1]) + 1 if "Range" in r.headers else 0; print(f"  {pos * 100 // size}%"); continue
            if r.status_code >= 500: time.sleep(5); continue
            sys.exit(f"upload failed {r.status_code}: {r.text[:500]}")

def set_thumbnail(vid, path):
    with open(path, "rb") as f:
        call("POST", f"{UP}/thumbnails/set?videoId={vid}&uploadType=media", data=f.read(), headers={"Content-Type": mimetypes.guess_type(path)[0] or "image/jpeg"})

def set_captions(vid, path, lang="en-US", name="English"):
    have = call("GET", f"{API}/captions?part=snippet&videoId={vid}").get("items", [])
    for c in have:
        if c["snippet"]["language"] == lang and c["snippet"].get("trackKind") == "standard": print("captions already there"); return
    meta = json.dumps({"snippet": {"videoId": vid, "language": lang, "name": name, "isDraft": False}})
    files = {"snippet": ("meta.json", meta, "application/json; charset=UTF-8"), "file": (os.path.basename(path), open(path, "rb").read(), "application/octet-stream")}
    r = requests.post(f"{UP}/captions?part=snippet&uploadType=multipart", files=files, headers=H(), timeout=120)
    if r.status_code >= 400: sys.exit(f"captions failed {r.status_code}: {r.text[:400]}")

def playlist_id(title, create=True):
    for p in call("GET", f"{API}/playlists?part=snippet&mine=true&maxResults=50").get("items", []):
        if p["snippet"]["title"].strip().lower() == title.strip().lower(): return p["id"]
    if not create: return None
    return call("POST", f"{API}/playlists?part=snippet,status", json={"snippet": {"title": title, "defaultLanguage": "en-US"}, "status": {"privacyStatus": "public"}})["id"]

def add_to_playlist(vid, title):
    pid = playlist_id(title)
    have = call("GET", f"{API}/playlistItems?part=contentDetails&maxResults=50&playlistId={pid}").get("items", [])
    if any(i["contentDetails"]["videoId"] == vid for i in have): return
    call("POST", f"{API}/playlistItems?part=snippet", json={"snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid}}})

def publish(folder, video_id=None, latest=False, do_upload=False):
    m = json.load(open(os.path.join(folder, "upload.json")))
    if m.get("publishAt"):
        t = datetime.datetime.fromisoformat(m["publishAt"].replace("Z", "+00:00"))
        if t < datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15): sys.exit("publishAt is in the past or too soon: update upload.json")
    if do_upload: vid = upload(os.path.join(folder, m["file"]), m)
    elif video_id: vid = video_id
    elif latest:
        cand = [v for v in videos(10) if v["status"]["privacyStatus"] == "private" and not v["status"].get("publishAt")]
        if not cand: sys.exit("no private, unscheduled upload found on the channel")
        vid = cand[0]["id"]; print("using newest private upload:", vid, "|", cand[0]["snippet"]["title"])
    else: sys.exit("give --video-id, --latest or --upload")
    call("PUT", f"{API}/videos?part=snippet,status", json=meta_body(m, vid)); print("details + schedule set")
    if m.get("thumbnail"): set_thumbnail(vid, os.path.join(folder, m["thumbnail"])); print("thumbnail set")
    if m.get("captions"): set_captions(vid, os.path.join(folder, m["captions"])); print("captions set")
    if m.get("playlist"): add_to_playlist(vid, m["playlist"]); print("added to playlist", m["playlist"])
    print("done:", f"https://youtu.be/{vid}", "| publishAt", m.get("publishAt"))

def comments(show_all=False):
    ch = me()["id"]; out = []
    for t in call("GET", f"{API}/commentThreads?part=snippet,replies&allThreadsRelatedToChannelId={ch}&maxResults=100&order=time").get("items", []):
        top = t["snippet"]["topLevelComment"]; replies = t.get("replies", {}).get("comments", [])
        answered = any(r["snippet"].get("authorChannelId", {}).get("value") == ch for r in replies)
        if top["snippet"].get("authorChannelId", {}).get("value") == ch: continue
        if show_all or not answered:
            out.append({"id": top["id"], "video": t["snippet"]["videoId"], "author": top["snippet"]["authorDisplayName"],
                        "text": top["snippet"]["textOriginal"], "likes": top["snippet"]["likeCount"], "when": top["snippet"]["publishedAt"], "answered": answered})
    return out

def reply(parent, text):
    return call("POST", f"{API}/comments?part=snippet", json={"snippet": {"parentId": parent, "textOriginal": text}})["id"]

def stats(vid):
    v = call("GET", f"{API}/videos?part=statistics,snippet&id={vid}")["items"][0]
    day = v["snippet"]["publishedAt"][:10]; today = datetime.date.today().isoformat()
    a = requests.get("https://youtubeanalytics.googleapis.com/v2/reports", headers=H(), timeout=60, params=dict(
        ids="channel==MINE", startDate=day, endDate=today, filters=f"video=={vid}",
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,likes,comments,shares"))
    return {"title": v["snippet"]["title"], "public": v["statistics"], "analytics": a.json() if a.status_code == 200 else f"analytics {a.status_code}: {a.text[:200]}"}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("args", nargs="*")
    ap.add_argument("--video-id"); ap.add_argument("--latest", action="store_true"); ap.add_argument("--upload", action="store_true"); ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.cmd == "whoami": c = me(); print(c["snippet"]["title"], c["id"], c["snippet"].get("customUrl"), c["statistics"])
    elif a.cmd == "videos":
        for v in videos(): print(v["id"], v["status"]["privacyStatus"], v["status"].get("publishAt", ""), v["status"].get("uploadStatus"), v["statistics"].get("viewCount"), "|", v["snippet"]["title"])
    elif a.cmd == "publish": publish(a.args[0], a.video_id, a.latest, a.upload)
    elif a.cmd == "comments": print(json.dumps(comments(a.all), indent=1, ensure_ascii=False))
    elif a.cmd == "reply": print("replied", reply(a.args[0], a.args[1]))
    elif a.cmd == "stats": print(json.dumps(stats(a.args[0]), indent=1))
    else: sys.exit(__doc__)
