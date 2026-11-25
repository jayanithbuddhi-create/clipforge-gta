#!/usr/bin/env python3
"""ClipForge GTA v3 - 4 agenti gratuiti su GitHub Actions.

  Scout  - classifica dei top streamer GTA in live (per spettatori) + le clip GTA piu' viste delle ultime 24 ore
  Hunter - scarica le clip virali, registra la live leggendo anche la CHAT e le clip fatte dagli spettatori,
           trova la facecam e misura l'hype secondo per secondo (urla + azione + chat)
  Judge  - voto ASSOLUTO 0-100 (non relativo alla live): scarta tempi morti, menu e caricamenti,
           ascolta cosa viene detto (Whisper) e non pubblica niente se non e' successo niente
  Editor - 9:16 con facecam sopra e gioco sotto, micro zoom sui picchi, sottotitoli animati parola
           per parola (Whisper), titolo piccolo in inglese, teaser iniziale, credito allo streamer

Uso:  python clipforge/run.py scout|record|analyze|edit|publish   (un passo per agente nel workflow)
      TEST_FILE=video.mp4 python clipforge/run.py all              (prova locale)
"""
import json, math, os, random, re, shutil, socket, subprocess, sys, threading, time, urllib.request
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "_work")
PAGES = os.path.join(ROOT, "_pages")
STATE = os.path.join(WORK, "state.json")
FONTS = os.path.join(ROOT, "fonts")
FONT_B = os.path.join(FONTS, "Inter-Bold.otf")
FONT_S = os.path.join(FONTS, "Inter-SemiBold.otf")
CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
OUT_W, OUT_H, SPLIT, FPS = 1080, 1920, 0.4, 30
CAM_PRESETS = {"tl": [0.01, 0.02, 0.24, 0.30], "tr": [0.75, 0.02, 0.24, 0.30],
               "bl": [0.01, 0.66, 0.24, 0.30], "br": [0.74, 0.66, 0.24, 0.30]}

# ---------------------------------------------------------------- stili (dalla ricerca sui clipper GTA)
STYLES = {
    "hype": {"nome": "Hype & rage", "colore": "&H0000D4FF", "w_audio": 1.6, "w_scene": 0.5, "durata": 26, "dopo": 5,
             "zoom": 0.14, "shake": 0,
             "hooks": {"both": ["{n} completely lost it", "{n} was NOT ready for this", "This is why {n} rage quits",
                                "{n}'s reaction says everything"],
                       "audio": ["{n} completely lost it", "{n} could not believe it", "{n} screamed so loud here"],
                       "scene": ["{n} did not see this coming", "Wait for {n}'s reaction"],
                       "weak": ["Wait for {n}'s reaction"],
                       "chat": ["Chat went crazy when {n} did this", "{n}'s chat could not believe this", "Twitch chat lost it here"]},
             "cta": ["Would you have stayed calm? 👇", "Rate this reaction 1-10 👇"], "emoji": "😭"},
    "funny": {"nome": "Funny RP", "colore": "&H0091FF4D", "w_audio": 1.5, "w_scene": 0.4, "durata": 30, "dopo": 5,
              "zoom": 0.10, "shake": 0,
              "hooks": {"both": ["This RP scene went so wrong", "{n} had no idea what was coming", "Nobody in the city expected this"],
                        "audio": ["{n} said this with a straight face", "The way {n} said this", "{n} cannot be serious"],
                        "scene": ["{n} really thought this would work", "This plan was doomed from the start"],
                        "weak": ["{n} really thought this would work"],
                        "chat": ["Chat could not stop laughing at this", "{n}'s chat lost it at this RP scene"]},
              "cta": ["Tag someone who would do this 💀", "Who's the funniest in the city? 👇"], "emoji": "💀"},
    "action": {"nome": "Inseguimenti & azione", "colore": "&H005C3BFF", "w_audio": 1.1, "w_scene": 1.0, "durata": 25, "dopo": 4,
               "zoom": 0.16, "shake": 9,
               "hooks": {"both": ["{n}'s getaway went way too far", "The cops did not expect this", "Best escape in GTA RP?"],
                         "audio": ["{n} almost got caught here", "This chase got intense"],
                         "scene": ["Watch this getaway till the end", "This crash was not in the plan", "{n} pulled this off??"],
                         "weak": ["Watch this getaway till the end"],
                         "chat": ["Chat went insane during this chase", "{n}'s chat could not believe this escape"]},
               "cta": ["Would you have escaped? 👇", "Cops or robbers? 👇"], "emoji": "🚔"},
    "drama": {"nome": "Drama RP", "colore": "&H00FF8CB4", "w_audio": 1.7, "w_scene": 0.3, "durata": 45, "dopo": 6,
              "zoom": 0.08, "shake": 0,
              "hooks": {"both": ["This argument got out of hand", "{n} finally snapped", "This RP drama was insane"],
                        "audio": ["{n} said what everyone was thinking", "This conversation got heated", "{n} did not hold back"],
                        "scene": ["This is how it all ended", "{n} made the wrong call"],
                        "weak": ["{n} made the wrong call"],
                        "chat": ["Chat was shook after this", "{n}'s chat picked a side instantly"]},
              "cta": ["Who was right here? 👇", "Whose side are you on? 👇"], "emoji": "😳"},
}


def env(k, d=""):
    """Valore dal workflow (pulsante Run) o dal comando inviato dal telefono (richiesta GitHub)."""
    return (os.environ.get(k) or os.environ.get("CMD_" + k) or d).strip()


def log(*a):
    print("[ClipForge]", *a, flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def load_cfg():
    with open(os.path.join(ROOT, "config.json")) as f:
        return json.load(f)


def save_state(st):
    os.makedirs(WORK, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"jobs": [], "clips": []}


# ================================================================ SCOUT
def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request("https://gql.twitch.tv/gql", data=body,
                                 headers={"Client-ID": CID, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


NODE = ("node{title viewersCount language createdAt previewImageURL(width:440,height:248) big:previewImageURL(width:1280,height:720) "
        "broadcaster{login displayName profileImageURL(width:70)} freeformTags{name}}")


def english_gta_lives(n=30):
    try:
        d = gql('query($n:Int){game(name:"Grand Theft Auto V"){streams(first:$n, options:{broadcasterLanguages:[EN]})'
                '{edges{%s}}}}' % NODE, {"n": n})
        edges = (((d.get("data") or {}).get("game") or {}).get("streams") or {}).get("edges") or []
    except Exception as e:
        log("Scout: lista live non disponibile ->", e)
        return []
    out = []
    for e in edges:
        nd = e.get("node") or {}
        b = nd.get("broadcaster") or {}
        if not b.get("login") or (nd.get("language") or "EN") != "EN":
            continue
        out.append({"login": b["login"].lower(), "nome": b.get("displayName") or b["login"], "titolo": nd.get("title") or "",
                    "spettatori": nd.get("viewersCount") or 0, "anteprima": nd.get("previewImageURL") or "",
                    "avatar": b.get("profileImageURL") or "", "tag": [t["name"] for t in nd.get("freeformTags") or []][:4],
                    "dal": nd.get("createdAt") or "", "grande": nd.get("big") or ""})
    out.sort(key=lambda l: -l["spettatori"])
    return out


def channel_live(login):
    try:
        d = gql('query($l:String!){user(login:$l){login displayName profileImageURL(width:70) stream{title viewersCount '
                'language createdAt previewImageURL(width:440,height:248) big:previewImageURL(width:1280,height:720) '
                'game{name}}}}', {"l": login})
        u = (d.get("data") or {}).get("user")
        if not u or not u.get("stream"):
            return None
        s = u["stream"]
        return {"login": login, "nome": u.get("displayName") or login, "titolo": s.get("title") or "",
                "spettatori": s.get("viewersCount") or 0, "anteprima": s.get("previewImageURL") or "",
                "avatar": u.get("profileImageURL") or "", "gioco": (s.get("game") or {}).get("name") or "",
                "lingua": s.get("language") or "", "dal": s.get("createdAt") or "", "tag": [], "grande": s.get("big") or ""}
    except Exception as e:
        log("Scout:", login, "non controllabile ->", e)
        return None


def is_gta(game, title):
    t = f"{game} {title}".lower()
    return "grand theft auto" in t or re.search(r"\bgta\b|\bgtarp\b|nopixel", t) is not None


def auto_style(title, tags):
    t = (title + " " + " ".join(tags)).lower()
    if re.search(r"chase|cop|police|heist|robber|race|getaway", t):
        return "action"
    if re.search(r"\brp\b|roleplay|nopixel|role play|prodigy|onx", t):
        return "funny"
    return "hype"


def parse_link(s):
    s = s.strip()
    m = re.search(r"clips\.twitch\.tv/(?:embed\?clip=)?([A-Za-z0-9_-]{4,100})", s) or \
        re.search(r"twitch\.tv/[A-Za-z0-9_]+/clip/([A-Za-z0-9_-]{4,100})", s)
    if m:
        return "clip", m.group(1), f"https://clips.twitch.tv/{m.group(1)}"
    m = re.search(r"twitch\.tv/(?:\w+/)?videos?/(\d+)", s)
    if m:
        return "vod", m.group(1), f"https://www.twitch.tv/videos/{m.group(1)}"
    m = re.search(r"twitch\.tv/(?:popout/)?([A-Za-z0-9_]{2,25})", s) or re.fullmatch(r"@?([A-Za-z0-9_]{2,25})", s)
    if m:
        return "live", m.group(1).lower(), f"https://www.twitch.tv/{m.group(1).lower()}"
    raise SystemExit("Link Twitch non valido: " + s)


def iso_ts(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


CLIP_NODE = ("slug title viewCount durationSeconds createdAt thumbnailURL language videoOffsetSeconds "
             "broadcaster{login displayName profileImageURL(width:70)}")


def _clip_row(nd):
    b = nd.get("broadcaster") or {}
    return {"slug": nd.get("slug") or "", "titolo": (nd.get("title") or "").strip(), "views": nd.get("viewCount") or 0,
            "durata": nd.get("durationSeconds") or 0, "creata": iso_ts(nd.get("createdAt")),
            "anteprima": nd.get("thumbnailURL") or "", "lingua": (nd.get("language") or "").lower(),
            "offset": nd.get("videoOffsetSeconds"), "login": (b.get("login") or "").lower(),
            "nome": b.get("displayName") or b.get("login") or "", "avatar": b.get("profileImageURL") or ""}


def top_gta_clips(n=30):
    """Le clip GTA V piu' viste delle ultime 24 ore: momenti che il pubblico di Twitch ha GIA' scelto come forti."""
    tries = ['query($n:Int){game(name:"Grand Theft Auto V"){clips(first:$n, criteria:{period:LAST_DAY, sort:VIEWS_DESC, '
             'languages:[EN]}){edges{node{%s}}}}}' % CLIP_NODE,
             'query($n:Int){game(name:"Grand Theft Auto V"){clips(first:$n, criteria:{period:LAST_DAY, sort:VIEWS_DESC})'
             '{edges{node{%s}}}}}' % CLIP_NODE]
    for q in tries:
        try:
            d = gql(q, {"n": n})
            edges = ((((d.get("data") or {}).get("game") or {}).get("clips")) or {}).get("edges")
            if edges is None:
                continue
            out = [_clip_row(e.get("node") or {}) for e in edges]
            out = [c for c in out if c["slug"] and c["login"] and c["lingua"] in ("", "en")]
            out.sort(key=lambda c: -c["views"])
            return out
        except Exception as e:
            log("Scout: clip virali non disponibili ->", e)
    return []


def clip_info(slug):
    try:
        nd = gql("query($s:ID!){clip(slug:$s){%s}}" % CLIP_NODE, {"s": slug})["data"]["clip"]
        return _clip_row(nd) if nd else None
    except Exception as e:
        log("Scout: info clip non disponibili ->", e)
        return None


def channel_clips(login, since):
    """Clip create dagli spettatori su questo canale dopo `since`: dove il pubblico ha premuto 'Clip'."""
    for sort in ("CREATED_AT_DESC", "VIEWS_DESC"):
        try:
            d = gql('query($l:String!){user(login:$l){clips(first:50, criteria:{period:LAST_DAY, sort:%s})'
                    '{edges{node{%s}}}}}' % (sort, CLIP_NODE), {"l": login})
            edges = ((((d.get("data") or {}).get("user") or {}).get("clips")) or {}).get("edges")
            if edges is None:
                continue
            return [c for c in (_clip_row(e.get("node") or {}) for e in edges) if c["creata"] >= since]
        except Exception as e:
            log("Hunter: clip degli spettatori non disponibili ->", e)
    return []


def vod_channel(vid):
    try:
        return gql("query($id:ID!){video(id:$id){owner{login}}}", {"id": vid})["data"]["video"]["owner"]["login"]
    except Exception:
        return "streamer"


def cam_for(cfg, ch):
    for c in cfg.get("canali", []):
        if c["nome"].lower() == ch:
            return c.get("facecam", "auto")
    return cfg.get("facecam_default", "auto")


def gallery():
    try:
        return json.load(open(os.path.join(PAGES, "clips.json")))
    except Exception:
        return []


def recent_clipped(window=3600):
    now = time.time()
    return {c["ch"] for c in gallery() if now - c.get("created", 0) < window and c.get("fonte") != "virale"}


def done_slugs():
    return {c.get("slug") for c in gallery() if c.get("slug")}


def clip_job(c, cfg, style_in="", cam_in=""):
    return {"tipo": "clip", "url": f"https://clips.twitch.tv/{c['slug']}", "ch": c["login"], "nome": c["nome"] or c["login"],
            "stile": style_in or auto_style(c["titolo"], []), "facecam": cam_in or cam_for(cfg, c["login"]),
            "minuti": 0, "da": 0, "titolo": c["titolo"], "slug": c["slug"], "views": c["views"], "webcam": c.get("webcam")}


def scout():
    cfg = load_cfg()
    fetch_pages()
    st = {"jobs": [], "clips": [], "inizio": int(time.time())}
    link, style_in, cam_in = env("LINK"), env("STILE"), env("FACECAM")
    minutes = max(1.0, min(30.0, float(env("MINUTI") or cfg.get("minuti", 10))))
    lives = english_gta_lives(40)
    log(f"Scout: {len(lives)} live GTA V in inglese adesso")
    mine = []
    for c in cfg.get("canali", []):
        info = channel_live(c["nome"].lower())
        if info and is_gta(info["gioco"], info["titolo"]):
            info["tuo"] = True
            mine.append(info)
            log(f"  {info['login']}: LIVE su GTA ({info['spettatori']} spettatori)")
        else:
            log(f"  {c['nome']}: " + ("live ma non su GTA" if info else "offline"))
    seen = {m["login"] for m in mine}
    excl = set(x.lower() for x in cfg.get("escludi", []))
    live_list = [l for l in mine + [l for l in lives if l["login"] not in seen] if l["login"] not in excl]
    top = sorted(live_list, key=lambda l: -l["spettatori"])
    for k, l in enumerate(top):
        l["rank"] = k + 1                                      # classifica per spettatori (anche i tuoi canali)
    for l in live_list[:30]:
        l["stile"] = auto_style(l["titolo"], l.get("tag", []))
        f = webcam_in_preview(l.pop("grande", "") or l["anteprima"])
        l["webcam"] = [round(v, 4) for v in f[:4]] if f else None
    for l in live_list[30:]:
        l.pop("grande", None)
        l["stile"] = auto_style(l["titolo"], l.get("tag", []))
        l["webcam"] = None
    log(f"Scout: {sum(1 for l in live_list if l.get('webcam'))} live su {len(live_list)} hanno la webcam")
    if top:
        log("Scout: top 5 per spettatori -> " + ", ".join(f"{l['nome']} ({l['spettatori']})" for l in top[:5]))

    # clip virali di oggi: i momenti GTA piu' visti delle ultime 24 ore
    virali = [c for c in top_gta_clips(40) if c["login"] not in excl and c["views"] >= int(cfg.get("min_views_clip_virale", 1500))
              and 6 <= (c["durata"] or 30) <= 90]
    for c in virali[:16]:
        f = webcam_in_preview(c["anteprima"], 0.7)
        c["webcam"] = [round(v, 4) for v in f[:4]] if f else None
        c["stile"] = auto_style(c["titolo"], [])
    log(f"Scout: {len(virali)} clip GTA virali oggi" + (f", la prima ha {virali[0]['views']} views" if virali else ""))

    if link:
        kind, ident, url = parse_link(link)
        if kind == "clip":
            c = clip_info(ident) or {"slug": ident, "login": "streamer", "nome": "streamer", "titolo": "", "views": 0}
            st["jobs"].append(clip_job(c, cfg, style_in, cam_in))
        else:
            ch = vod_channel(ident) if kind == "vod" else ident
            info = next((l for l in live_list if l["login"] == ch), {})
            st["jobs"].append({"tipo": kind, "url": url, "ch": ch, "nome": info.get("nome") or ch,
                               "stile": style_in or info.get("stile") or cfg.get("stile_default", "hype"),
                               "facecam": cam_in or cam_for(cfg, ch), "minuti": minutes,
                               "da": float(os.environ.get("DA") or 0), "titolo": info.get("titolo", ""),
                               "webcam": info.get("webcam"), "dal": info.get("dal", ""), "manuale": True})
        st["jobs"][-1]["manuale"] = True
    elif env("SOLO_SCOUT") != "1":
        # 1) momenti gia' provati: le clip piu' viste di oggi, prima quelle con la webcam (formato facecam + gioco)
        used = done_slugs()
        fresh = [c for c in virali if c["slug"] not in used]
        fresh.sort(key=lambda c: (not c.get("webcam"), -c["views"]))
        per_ch = {}
        for c in fresh:
            if len([j for j in st["jobs"] if j["tipo"] == "clip"]) >= int(cfg.get("clip_virali_per_giro", 2)):
                break
            if per_ch.get(c["login"]):
                continue                                          # varieta': un solo streamer per giro
            per_ch[c["login"]] = 1
            st["jobs"].append(clip_job(c, cfg, style_in, cam_in))
        # 2) la live piu' seguita (con webcam), letta anche dalla chat
        recent = recent_clipped()
        others = [l for l in top if not l.get("tuo")] if cfg.get("scopri_top_gta", True) else []
        if cfg.get("solo_con_webcam", True):
            others = [l for l in others if l.get("webcam")]
        n_live = 0
        for l in mine + others:
            if n_live >= int(cfg.get("max_live_per_giro", 1)):
                break
            if l["login"] in recent and not l.get("tuo"):
                continue
            n_live += 1
            st["jobs"].append({"tipo": "live", "url": f"https://www.twitch.tv/{l['login']}", "ch": l["login"],
                               "nome": l.get("nome") or l["login"], "stile": style_in or l["stile"],
                               "facecam": cam_in or cam_for(cfg, l["login"]), "minuti": minutes, "da": 0,
                               "titolo": l["titolo"], "webcam": l.get("webcam"), "dal": l.get("dal", ""),
                               "spettatori": l["spettatori"]})
    for j in st["jobs"]:
        if j["stile"] not in STYLES:
            j["stile"] = "hype"
        what = f"clip virale ({j.get('views', 0)} views)" if j["tipo"] == "clip" else f"{j['minuti']:.0f} min di {j['tipo']}"
        log(f"Scout: {j['ch']} · {what} · stile {STYLES[j['stile']]['nome']}")
    st["live"] = live_list[:30]
    st["virali"] = [{k: c.get(k) for k in ("slug", "titolo", "views", "durata", "creata", "anteprima", "login", "nome",
                                            "avatar", "webcam", "stile")} for c in virali[:20]]
    save_state(st)
    publish(st, final=False)


# ================================================================ HUNTER: registrazione
QUALITY = "1080p60,1080p,936p60,900p60,720p60,720p,best"

# Parole ed emote che in chat segnalano un momento forte (risata, shock, "clippalo")
CHAT_HYPE = re.compile(r"(?i)\b(kekw|lulw?|omegalul|lmf?a+o+|lo+l|icant|pog\w*|omg|wtf|holy|no ?way|bro+|dead+|monka\w*|"
                       r"aware|o7|sadge|ez+|gg+|ratio|jesus|insane|crazy|wild)\b")
CHAT_EMOJI = ("💀", "😂", "🤣", "😭", "😱", "🔥")


def chat_weight(msg):
    m = msg.strip()
    w = 1.0
    if CHAT_HYPE.search(m) or any(e in m for e in CHAT_EMOJI):
        w += 1.5
    if re.search(r"(?i)\bclip", m):
        w += 2.5                                   # "CLIP IT" = il pubblico ci dice dov'e' il momento
    if re.fullmatch(r"(?i)\s*([wl]+|\?+|!+|[A-Z ]{6,})\s*", m):
        w += 1.0                                   # spam di W / L / ??? / maiuscolo = reazione collettiva
    return w


class ChatLogger(threading.Thread):
    """Legge la chat Twitch in sola lettura (utente anonimo) e segna l'ora di ogni messaggio con il suo peso."""

    def __init__(self, ch):
        super().__init__(daemon=True)
        self.ch, self.msgs, self.halt = ch.lower(), [], threading.Event()

    def run(self):
        while not self.halt.is_set():
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=15)
                s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{self.ch}\r\n".encode())
                s.settimeout(2)
                buf = b""
                while not self.halt.is_set():
                    try:
                        data = s.recv(16384)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for ln in lines:
                        if ln.startswith(b"PING"):
                            s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif b" PRIVMSG " in ln:
                            rest = ln.split(b" PRIVMSG ", 1)[1]
                            txt = rest.split(b" :", 1)[1].decode("utf-8", "ignore") if b" :" in rest else ""
                            self.msgs.append((time.time(), chat_weight(txt)))
                s.close()
            except Exception as e:
                log("Hunter: chat interrotta, riprovo ->", e)
                self.halt.wait(3)


def record():
    st, cfg = load_state(), load_cfg()
    for i, j in enumerate(st["jobs"]):
        if j.get("tipo") == "clip":
            out = os.path.join(WORK, f"rec_{i}.mp4")
            log(f"Hunter: scarico la clip virale di {j['ch']} ({j.get('views', 0)} views)")
            subprocess.run(["streamlink", "--retry-open", "3", "-f", "-o", out, j["url"], "1080p60,1080p,720p60,720p,best"],
                           check=False, timeout=600)
            j["file"] = out if os.path.exists(out) and os.path.getsize(out) > 300_000 else None
            if not j["file"]:
                log("Hunter: clip non scaricabile", j["url"])
            continue
        out = os.path.join(WORK, f"rec_{i}.ts")
        secs = int(j["minuti"] * 60)
        cmd = ["streamlink", "--twitch-disable-ads", "--retry-open", "3", "--retry-streams", "5",
               "--stream-segment-threads", "3", "--hls-duration", str(secs), "-f", "-o", out]
        if j.get("da"):
            cmd += ["--hls-start-offset", str(int(j["da"] * 60))]
        cmd += [j["url"], QUALITY]
        live = j.get("tipo") == "live"
        chat = ChatLogger(j["ch"]) if live and cfg.get("chat", True) else None
        if chat:
            chat.start()
        log(f"Hunter: registro {j['ch']} per {j['minuti']:.0f} min" + (" e leggo la chat" if chat else ""))
        started, t0 = time.time(), None
        proc = subprocess.Popen(cmd)
        while proc.poll() is None:
            if t0 is None and os.path.exists(out) and os.path.getsize(out) > 0:
                t0 = time.time()                   # primo secondo davvero registrato: allinea la chat al video
            if time.time() - started > secs + 600:
                log("Hunter: registrazione oltre il tempo, uso quello che c'e'")
                proc.kill()
                break
            time.sleep(0.5)
        t0 = t0 or started
        j["file"] = out if os.path.exists(out) and os.path.getsize(out) > 2_000_000 else None
        if not j["file"]:
            log("Hunter: niente di utile da", j["ch"])
        if chat:
            chat.halt.set()
            msgs = [(round(t - t0, 2), w) for t, w in chat.msgs if t >= t0 - 5]
            json.dump(msgs, open(os.path.join(WORK, f"chat_{i}.json"), "w"))
            j["chat"] = f"chat_{i}.json"
            log(f"Hunter: {len(msgs)} messaggi in chat ({len(msgs) / max(1, secs) * 60:.0f} al minuto)")
            time.sleep(20)                        # lascia agli spettatori il tempo di pubblicare le ultime clip
            marks = []
            start_live = iso_ts(j.get("dal"))
            for c in channel_clips(j["ch"], t0 - 120):
                d = float(c["durata"] or 30)
                if c.get("offset") is not None and start_live:
                    s0 = start_live + float(c["offset"]) - t0
                else:
                    s0 = c["creata"] - t0 - d - 4
                if s0 + d > 0 and s0 < secs + 20:
                    marks.append({"s": round(s0, 1), "e": round(s0 + d, 1), "views": c["views"], "titolo": c["titolo"]})
            j["clip_spettatori"] = marks
            log(f"Hunter: {len(marks)} clip create dagli spettatori durante la registrazione")
    save_state(st)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", path], capture_output=True, text=True).stdout
    j = json.loads(out)
    return int(j["streams"][0]["width"]), int(j["streams"][0]["height"]), float(j["format"]["duration"])


# ================================================================ HUNTER: analisi
def audio_db(path, n):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    out = np.full(n, -90.0)
    for s in range(n):
        seg = a[s * 8000:(s + 1) * 8000]
        if len(seg):
            out[s] = max(-90.0, 20 * math.log10(math.sqrt(float(np.mean(seg ** 2))) + 1e-9))
    return out


def motion(path, n, cam):
    W, H = 48, 27
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-an", "-vf", f"fps=1,scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W).astype(np.float32)
    mask = np.ones((H, W), bool)
    if cam:
        x, y, w, h = cam
        mask[int(y * H):int(math.ceil((y + h) * H)), int(x * W):int(math.ceil((x + w) * W))] = False
    mot, cx, lum = np.zeros(n), np.full(n, 0.5), np.full(n, 128.0)
    xs = (np.arange(W) + 0.5) / W
    for i in range(min(n, len(fr))):
        lum[i] = float(fr[i][mask].mean()) if mask.any() else float(fr[i].mean())
    for i in range(1, min(n, len(fr))):
        d = np.abs(fr[i] - fr[i - 1]) * mask
        mot[i] = d.sum() / max(1, mask.sum()) / 255
        big = np.where(d > 18, d, 0)
        if big.sum() > 0:
            cx[i] = float((big.sum(axis=0) * xs).sum() / big.sum())
    return mot, cx, lum


_YN = None


def face_model():
    """YuNet (OpenCV): trova i volti molto meglio del vecchio Haar, anche piccoli e un po' girati."""
    global _YN
    if _YN is not None:
        return _YN or None
    try:
        import cv2
        path = os.environ.get("YUNET_ONNX") or os.path.join(ROOT, "_models", "yunet.onnx")
        _YN = cv2.FaceDetectorYN.create(path, "", (320, 320), 0.7, 0.3, 50) if os.path.exists(path) else False
    except Exception as e:
        log("Rilevatore volti non disponibile ->", e)
        _YN = False
    return _YN or None


def faces_in(img):
    """Volti in un'immagine BGR: lista di (cx, cy, w, h, punteggio) normalizzati 0-1."""
    det = face_model()
    if det is None or img is None:
        return []
    h, w = img.shape[:2]
    det.setInputSize((w, h))
    _, f = det.detect(img)
    if f is None:
        return []
    return [((x + fw / 2) / w, (y + fh / 2) / h, fw / w, fh / h, float(sc)) for x, y, fw, fh, *rest, sc in f.tolist()
            if fw / w > 0.012]


def webcam_in_preview(url, soglia=0.8):
    """Lo Scout guarda l'anteprima della live: c'e' una faccia vera (webcam)?"""
    try:
        import cv2
        data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        fs = [f for f in faces_in(img) if f[4] >= soglia]
        return max(fs, key=lambda f: f[4]) if fs else None
    except Exception:
        return None


def cam_box(cx, cy, fw, sw, shh):
    """Dalla faccia al riquadro webcam, con la stessa forma del riquadro in alto nella clip (1080x768)."""
    w = min(0.5, max(0.11, fw * 2.6))
    h = min(0.6, w * sw / shh / (OUT_W / (OUT_H * SPLIT)))
    x = min(max(0, cx - w / 2), 1 - w)
    y = min(max(0, cy - h * 0.42), 1 - h)
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def find_facecam(path, dur, sw, shh, hint=None):
    """La webcam e' la faccia che resta nello stesso punto in quasi tutti i fotogrammi."""
    if face_model() is None:
        log("Hunter: rilevatore di volti non disponibile")
        return cam_box(*hint[:3], sw, shh) if hint else None
    W, H, N, hits = 1280, 720, 16, []
    for k in range(N):
        t = dur * (k + 0.5) / N
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.1f}", "-i", path, "-frames:v", "1",
                              "-vf", f"scale={W}:{H}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"], capture_output=True).stdout
        if len(raw) < W * H * 3:
            continue
        img = np.frombuffer(raw[:W * H * 3], np.uint8).reshape(H, W, 3)
        hits += [(k,) + f for f in faces_in(img)]
    log(f"Hunter: {len(hits)} volti visti in {N} fotogrammi")
    best, best_n = None, 0
    for _, cx, cy, fw, fh, sc in hits:
        near = [q for q in hits if abs(q[1] - cx) < 0.035 and abs(q[2] - cy) < 0.05]
        n = len({q[0] for q in near})
        if n > best_n:
            best_n, best = n, near
    if best_n >= max(5, int(N * 0.4)):
        cx, cy, fw = (float(np.median([b[i] for b in best])) for i in (1, 2, 3))
        log(f"Hunter: webcam trovata in {best_n}/{N} fotogrammi a ({cx:.2f},{cy:.2f})")
        return cam_box(cx, cy, fw, sw, shh)
    if hint:
        log("Hunter: uso la webcam vista dallo Scout nell'anteprima")
        return cam_box(*hint[:3], sw, shh)
    return None


def smooth(x, w=3):
    k = np.ones(w) / w
    return np.convolve(np.pad(x, (w // 2, w // 2), mode="edge"), k, mode="valid")[:len(x)]


def robust_z(x):
    md = np.median(x)
    mad = np.median(np.abs(x - md)) * 1.4826
    if mad < 1e-6:
        sd = x.std()
        return np.zeros_like(x) if sd < 1e-6 else np.maximum(0, (x - x.mean()) / sd)
    return np.maximum(0, (x - md) / mad)


def baseline_z(x, w=60):
    n, h = len(x), w // 2
    d = np.array([x[i] - np.median(x[max(0, i - h):min(n, i + h + 1)]) for i in range(n)])
    mad = np.median(np.abs(d)) * 1.4826 or 1
    return np.maximum(0, d / mad)


def chat_series(path, n, lag=5.0):
    """Hype della chat secondo per secondo, spostato indietro del ritardo di reazione degli spettatori."""
    try:
        msgs = json.load(open(path))
    except Exception:
        return None
    if len(msgs) < 30:                          # chat troppo lenta: non e' un segnale affidabile
        return None
    arr = np.zeros(n)
    for t, w in msgs:
        i = int(round(t - lag))
        if 0 <= i < n:
            arr[i] += w
    return smooth(baseline_z(smooth(arr, 5), 90), 3)


def marks_series(marks, n):
    """Le clip fatte dagli spettatori: piu' peso verso la fine, dove di solito c'e' il momento."""
    out = np.zeros(n)
    for m in marks or []:
        a, b = int(max(0, m["s"])), int(min(n, math.ceil(m["e"])))
        if b <= a:
            continue
        w = 2.0 + math.log10(1 + m.get("views", 0))
        out[a:b] = np.maximum(out[a:b], w * np.linspace(0.4, 1.0, b - a))
    return out


def bounds(score, peak, style, dur, thr):
    """Inizio dove parte la situazione (non 20 s prima), fine appena dopo il payoff."""
    n = len(score)
    pre_max = max(8, style["durata"] - style["dopo"])
    s, last, quiet = peak, peak, 0
    while s > 0 and peak - s < pre_max:
        s -= 1
        if score[s] >= thr:
            last, quiet = s, 0
        else:
            quiet += 1
            if quiet >= 5 and peak - s >= 6:
                break
    start = max(0, min(last - 2, peak - 6))
    e, last, quiet = peak, peak, 0
    while e < n - 1 and e - peak < style["dopo"] + 8:
        e += 1
        if score[e] >= thr:
            last, quiet = e, 0
        else:
            quiet += 1
            if quiet >= 3 and e - peak >= style["dopo"]:
                break
    end = min(dur, max(peak + style["dopo"], last + 1.5))
    if end - start < 12:                       # minimo 12 s: serve contesto per capire la scena
        start = max(0, end - 12)
        end = min(dur, max(end, start + 12))
    return float(start), float(end)


def candidates(score, max_n, style, dur, thr):
    n = len(score)
    taken, picks = np.zeros(n, bool), []
    for i in np.argsort(-score):
        if len(picks) >= max_n or score[i] < thr * 1.6:
            break
        if taken[i]:
            continue
        start, end = bounds(score, int(i), style, dur, thr)
        if end - start < min(8, dur):
            continue
        picks.append({"peak": int(i), "start": start, "end": end, "score": float(score[i])})
        taken[max(0, int(start) - 3):min(n, int(math.ceil(end)) + 3)] = True
    return picks


def micro_peaks(score, zA, zS, c, mx, k=4):
    """Picchi secondari dentro la clip: dove mettere i micro zoom (faccia se urla, gioco se azione)."""
    a, b = int(c["start"]), int(c["end"])
    out = []
    for i in sorted(range(a + 1, b - 1), key=lambda i: -score[i]):
        if score[i] < 0.42 * mx or score[i] < score[i - 1] or score[i] < score[i + 1]:
            continue
        if all(abs(i - o["t"]) >= 3 for o in out):
            out.append({"t": i, "forza": float(min(1.0, score[i] / mx)), "su": "faccia" if zA[i] >= zS[i] else "gioco"})
        if len(out) >= k:
            break
    return sorted(out, key=lambda o: o["t"])


HYPE_WORDS = re.compile(r"(?i)\b(no way|oh my god|oh my gosh|what the|bro|dude|run|go go|holy|wait|stop|why|"
                        r"let'?s go|are you serious|dead|help|get out|shoot|crazy|insane|yo+|nah)\b")


def speech_score(words, length):
    """Il Judge ascolta: dialogo fitto, esclamazioni e risate = scena con qualcosa da raccontare."""
    if not words:
        return 0.0, ""
    txt = " ".join(w["w"] for w in words)
    rate = len(words) / max(1.0, length)
    excl = txt.count("!") + txt.count("?") * 0.5
    hype = len(HYPE_WORDS.findall(txt))
    laugh = len(re.findall(r"(?i)\b(ha){2,}|\blol\b|\blmao\b", txt))
    sc = 0.45 * min(1, rate / 2.2) + 0.25 * min(1, excl / 4) + 0.2 * min(1, hype / 3) + 0.1 * min(1, laugh / 2)
    return float(sc), txt


def judge(c, sig, score, words, length, base=None):
    """Voto ASSOLUTO: un urlo vero, una chat che esplode, clip degli spettatori. Una live piatta resta sotto soglia."""
    zA, zS, zC, cm, dead = sig["zA"], sig["zS"], sig["zC"], sig["cm"], sig["dead"]
    n = len(score)
    a, b = int(c["start"]), min(n, int(math.ceil(c["end"])))
    near = [i for i in range(c["peak"] - 4, c["peak"] + 5) if 0 <= i < n]
    pa, ps = float(max(zA[near])), float(max(zS[near]))
    pc = float(max(zC[a:b])) if zC is not None and b > a else 0.0
    marks = float(max(cm[a:b])) if b > a else 0.0
    dead_f = float(dead[a:b].mean()) if b > a else 1.0
    active = float((score[a:b] > 1.0).mean()) if b > a else 0.0
    sp, txt = speech_score(words, length)
    R, A = min(1, pa / 6), min(1, ps / 5)
    C = min(1, max(pc / 5, marks / 4))
    if base is not None:                       # clip virale: il pubblico l'ha gia' votata con le views
        v = base + 8 * R + 4 * A + 6 * sp - 10 * dead_f
    elif zC is not None or marks > 0:
        v = 12 + 24 * R + 12 * A + 26 * C + 14 * sp + 10 * active - 18 * dead_f
    else:                                      # VOD o chat assente: urla e azione pesano di piu'
        v = 14 + 34 * R + 18 * A + 16 * sp + 12 * active - 18 * dead_f
    pct = lambda x: int(round(max(0, min(1, x)) * 100))
    why = []
    if marks > 0:
        why.append("clippata dagli spettatori")
    if C >= 0.5 and pc > 0:
        why.append("chat esplosa")
    if R >= 0.5:
        why.append("urlo/reazione forte")
    if A >= 0.5:
        why.append("tanta azione")
    if sp >= 0.45:
        why.append("dialogo intenso")
    if base is not None:
        why.insert(0, "tra le clip GTA più viste oggi")
    c.update(viral=int(round(max(5, min(98, v)))), has_a=R >= 0.3, has_s=A >= 0.3, has_c=C >= 0.4,
             subs={"Reazione": pct(R), "Azione": pct(A), "Chat": pct(C), "Parlato": pct(sp)},
             perche=" · ".join(why) or "picco di hype", dead=round(dead_f, 2), testo=txt[:400])
    return c


def analyze():
    st, cfg = load_state(), load_cfg()
    st["analisi"] = []
    model = cfg.get("whisper_model", "base.en")
    for i, j in enumerate(st["jobs"]):
        if not j.get("file"):
            continue
        src = j["file"]
        sw, shh, dur = probe(src)
        style = STYLES.get(j["stile"], STYLES["hype"])
        viral_clip = j.get("tipo") == "clip"
        cam = None
        if j["facecam"] == "auto":
            cam = find_facecam(src, dur, sw, shh, j.get("webcam"))
            if not cam:
                log("Hunter: nessuna facecam fissa trovata, uso il gioco a tutto schermo")
        elif j["facecam"] in CAM_PRESETS:
            cam = CAM_PRESETS[j["facecam"]]
        elif isinstance(j["facecam"], list):
            cam = j["facecam"]
        n = int(math.ceil(dur)) + 1
        log(f"Hunter: analizzo {j['ch']} ({sw}x{shh}, {dur / 60:.1f} min)")
        try:
            db = audio_db(src, n)
        except Exception as e:
            log("Hunter: audio non leggibile ->", e)
            db = np.full(n, -90.0)
        zA = smooth(baseline_z(db, 60 if not viral_clip else 20), 3)
        mot, cx, lum = motion(src, n, cam)
        zS = smooth(robust_z(mot), 5)
        zC = chat_series(os.path.join(WORK, j["chat"]), n) if j.get("chat") else None
        cm = marks_series(j.get("clip_spettatori"), n)
        if j.get("chat"):
            log("Hunter: chat " + ("usata come segnale" if zC is not None else "troppo lenta, ignorata"))
        # tempi morti: silenzio + immagine ferma (menu, caricamenti, AFK) o schermo nero
        quiet = db < (np.median(db) - 9)
        static = mot < max(0.0025, float(np.percentile(mot, 10)))
        dead = ((quiet & static) | (lum < 12)).astype(float)
        dead = smooth(dead, 3)
        score = style["w_audio"] * zA + style["w_scene"] * zS + 0.35 * np.minimum(zA, zS)
        if zC is not None:
            score = score + 1.3 * zC + 0.4 * np.minimum(zA, zC)
        score = smooth(score + 0.9 * cm - 1.2 * dead, 3)
        score[:3] *= 0.3
        score[-3:] *= 0.3
        score = np.maximum(score, 0)
        mx = max(1e-6, float(score.max()))
        thr = max(0.8, float(np.percentile(score, 60)))
        keep = int(cfg.get("clip_per_live", 2)) if not viral_clip else 1
        if viral_clip:
            # gia' scelta dal pubblico: la tengo intera se e' corta, altrimenti prendo la finestra migliore
            if dur <= style["durata"] + 12:
                cands = [{"peak": int(np.argmax(score[:max(1, n - 2)])), "start": 0.0, "end": float(dur), "score": mx}]
            else:
                cands = candidates(score, 2, style, dur, thr) or [{"peak": int(np.argmax(score)), "start": 0.0,
                                                                    "end": float(min(dur, style["durata"])), "score": mx}]
            views = max(1, int(j.get("views") or 1))
            base = max(48, min(80, 52 + 9 * math.log10(views / 500)))
        else:
            cands = candidates(score, max(keep * 3, 6), style, dur, thr)
            base = None
        # il Judge ascolta i candidati migliori prima di votare
        ranked = sorted(cands, key=lambda c: -c["score"])[:6]
        sig = {"zA": zA, "zS": zS, "zC": zC, "cm": cm, "dead": dead}
        for c in ranked:
            words = transcribe(src, model, c["start"], c["end"] - c["start"]) if cfg.get("sottotitoli", True) else []
            judge(c, sig, score, words, c["end"] - c["start"], base)
        ranked.sort(key=lambda c: -c["viral"])
        manual = bool(j.get("manuale"))
        soglia = int(cfg.get("voto_minimo", 60))
        chosen = [c for k, c in enumerate(ranked) if k < keep and (c["viral"] >= soglia or (manual and k == 0)
                                                                    or (viral_clip and k == 0))]
        log(f"Judge: {len(ranked)} candidati -> voti " + ", ".join(f"{c['viral']} ({c['perche']})" for c in ranked))
        if not chosen:
            log(f"Judge: niente sopra {soglia}/100 in questa live: non pubblico clip mediocri")
        for c in chosen:
            a, b = int(c["start"]), int(math.ceil(c["end"]))
            k_follow = 0.8 if cam else 0.35         # senza facecam il gioco e' piu' stretto: resta sul personaggio al centro
            c["cx"] = float(np.clip(0.5 + (np.median(cx[a:b]) - 0.5) * k_follow, 0.3, 0.7)) if cfg.get("segui_azione", True) else 0.5
            c["zooms"] = micro_peaks(score, zA, zS, c, mx) if cfg.get("micro_zoom", True) else []
        step = max(1, n // 160)
        st["analisi"].append({"ch": j["ch"], "durata": round(dur), "passo": step, "stile": j["stile"], "tipo": j.get("tipo", "live"),
                              "hype": [round(float(v), 2) for v in score[::step]],
                              "urla": [round(float(v), 2) for v in zA[::step]],
                              "azione": [round(float(v), 2) for v in zS[::step]],
                              "chat": [round(float(v), 2) for v in zC[::step]] if zC is not None else [],
                              "spettatori": [m["s"] for m in j.get("clip_spettatori") or []],
                              "scelte": [{"start": round(c["start"]), "end": round(c["end"]), "viral": c["viral"],
                                          "perche": c["perche"], "zoom": [z["t"] for z in c["zooms"]]} for c in chosen],
                              "scartate": [{"start": round(c["start"]), "end": round(c["end"]), "viral": c["viral"]}
                                           for c in ranked if c not in chosen],
                              "soglia": soglia, "candidati": len(ranked), "facecam": bool(cam), "quando": int(time.time())})
        j.update(sw=sw, shh=shh, dur=dur, cam=cam, chosen=chosen)
    save_state(st)


# ================================================================ EDITOR
def even(v):
    return max(2, int(v) // 2 * 2)


def zoom_filter(peaks, amp, w, h, shake=0):
    """Micro zoom: entra veloce (0,12 s) e torna piano (0,45 s) su ogni picco."""
    if not peaks:
        return f"scale={w}:{h}:flags=lanczos"
    T = f"(on/{FPS})"
    terms = [f"{amp * (0.6 + 0.4 * p['forza']):.3f}*if(lt({T},{p['tt']:.2f}),exp(-pow(({T}-{p['tt']:.2f})/0.12,2)),"
             f"exp(-pow(({T}-{p['tt']:.2f})/0.45,2)))" for p in peaks]
    z = "1+" + "+".join(terms)
    sx = f"+{shake}*sin(on*2.1)*(zoom-1)/{amp:.3f}" if shake else ""
    return f"zoompan=z='{z}':d=1:s={w}x{h}:fps={FPS}:x='iw/2-(iw/zoom/2){sx}':y='ih/2-(ih/zoom/2)'"


def build_base(src, j, c, style, out, teaser=True):
    """Primo passaggio: layout 9:16 + micro zoom + teaser. Senza testi (prima servono i tempi dei sottotitoli)."""
    sw, shh, cam = j["sw"], j["shh"], j["cam"]
    split = cam is not None and sw > shh
    top = int(OUT_H * SPLIT) if split else 0
    gh_out = OUT_H - top
    parts = [(c["peak"] - 0.4, 1.5), (c["start"], c["end"] - c["start"])] \
        if teaser and c["peak"] - c["start"] > 4 else [(c["start"], c["end"] - c["start"])]
    args, fc = ["ffmpeg", "-v", "error", "-y"], []
    for (ss, d) in parts:
        args += ["-ss", f"{ss:.2f}", "-t", f"{d:.2f}", "-i", src]
    for i, (ss, d) in enumerate(parts):
        local = [dict(z, tt=z["t"] - ss) for z in c.get("zooms", []) if 0.3 < z["t"] - ss < d - 0.3]
        if i == 0 and len(parts) == 2:
            local = [{"tt": 0.45, "forza": 1.0, "su": "gioco"}]
        face = [z for z in local if z["su"] == "faccia"]
        game = [z for z in local if z["su"] == "gioco"] if split else local
        if split:
            x, y, w, h = cam
            cw, chh, cx0, cy0 = even(w * sw), even(h * shh), even(x * sw), even(y * shh)
            gw = min(sw, even(shh * OUT_W / gh_out))
            gx = even(max(0, min(sw - gw, c["cx"] * sw - gw / 2)))
            fc.append(f"[{i}:v]fps={FPS},split=2[c{i}][g{i}];"
                      f"[c{i}]crop={cw}:{chh}:{cx0}:{cy0},scale={OUT_W}:{top}:force_original_aspect_ratio=increase:flags=lanczos,"
                      f"crop={OUT_W}:{top},setsar=1,{zoom_filter(face, style['zoom'] * 1.2, OUT_W, top)},setsar=1[cc{i}];"
                      f"[g{i}]crop={gw}:{shh}:{gx}:0,scale={OUT_W}:{gh_out}:flags=lanczos,setsar=1,"
                      f"{zoom_filter(game, style['zoom'], OUT_W, gh_out, style['shake'])},setsar=1[gg{i}];"
                      f"[cc{i}][gg{i}]vstack,format=yuv420p[v{i}]")
        else:
            gw2 = min(sw, even(shh * OUT_W / OUT_H))
            gx2 = even(max(0, min(sw - gw2, c["cx"] * sw - gw2 / 2)))
            fc.append(f"[{i}:v]fps={FPS},crop={gw2}:{shh}:{gx2}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1,"
                      f"{zoom_filter(local, style['zoom'], OUT_W, OUT_H, style['shake'])},setsar=1,format=yuv420p[v{i}]")
        fc.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    if len(parts) == 2:
        fc.append("[v1]fade=t=in:st=0:d=0.2:color=white[v1f];[v0][a0][v1f][a1]concat=n=2:v=1:a=1[v][a]")
    else:
        fc.append("[v0]null[v];[a0]anull[a]")
    args += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "15", "-c:a", "aac", "-b:a", "192k", out]
    sh(args)
    return split, top, (1.5 if len(parts) == 2 else 0), sum(p[1] for p in parts)


_WHISPER = None


def transcribe(path, model_name, ss=None, t=None):
    global _WHISPER
    try:
        from faster_whisper import WhisperModel
    except Exception:
        if _WHISPER is None:
            log("Whisper non installato: niente ascolto ne' sottotitoli")
            _WHISPER = False
        return []
    if _WHISPER is False:
        return []
    try:
        if _WHISPER is None:
            _WHISPER = WhisperModel(model_name, device="cpu", compute_type="int8")
        rng = (["-ss", f"{ss:.2f}", "-t", f"{t:.2f}"] if ss is not None else [])
        raw = subprocess.run(["ffmpeg", "-v", "error", *rng, "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout          # audio gia' pronto: niente PyAV
        audio = np.frombuffer(raw, dtype=np.float32).copy()
        segs, _ = _WHISPER.transcribe(audio, word_timestamps=True, vad_filter=True, language="en", beam_size=3)
        words = []
        for s in segs:
            for w in s.words or []:
                t = w.word.strip()
                if t:
                    words.append({"t0": float(w.start), "t1": float(w.end), "w": t})
        return words
    except Exception as e:
        log("Editor: trascrizione fallita ->", e)
        return []


def ass_time(t):
    t = max(0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_ass(words, style, path, y):
    """Sottotitoli stile TikTok: 2-3 parole alla volta, parola attiva colorata con un piccolo 'pop'."""
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            "Style: Cap,Inter,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,6,3,5,60,60,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= 3 or w["t0"] - cur[-1]["t1"] > 0.6 or len(" ".join(x["w"] for x in cur + [w])) > 22
                    or re.search(r"[.!?]$", cur[-1]["w"])):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    lines = []
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["t0"] if gi + 1 < len(groups) else 1e9
        for k, w in enumerate(g):
            t1 = g[k + 1]["t0"] if k + 1 < len(g) else min(max(w["t1"], w["t0"] + 0.3), nxt)
            t1 = max(t1, w["t0"] + 0.05)
            txt = []
            for m, x in enumerate(g):
                clean = re.sub(r"[{}\\]", "", x["w"]).upper()
                txt.append("{\\c" + style["colore"] + "&\\fscx114\\fscy114\\t(0,90,\\fscx100\\fscy100)}" + clean + "{\\c&H00FFFFFF&}"
                           if m == k else clean)
            lines.append(f"Dialogue: 0,{ass_time(w['t0'])},{ass_time(t1)},Cap,,0,0,0,,{{\\pos(540,{y})}}" + " ".join(txt))
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n")
    return len(lines)


def wrap(text, width=30):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:2])


BAD = re.compile(r"\b(fuck|shit|bitch|nigg|fag|retard|cunt|whore)", re.I)


def quote_hook(words, peak_t):
    """Frase corta e forte detta vicino al picco = hook tra virgolette (formato molto usato nei clip RP)."""
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.!?]$", w["w"]):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    best, score = None, 0
    for s in sents:
        txt = " ".join(x["w"] for x in s).strip()
        n = len(txt.split())
        if not (3 <= n <= 8) or abs(s[0]["t0"] - peak_t) > 6 or BAD.search(txt):
            continue
        sc = (2 if "!" in txt else 1 if "?" in txt else 0.3) + \
             (1 if re.search(r"\b(no way|bro|what|why|oh my|run|dude|wait|stop)\b", txt.lower()) else 0)
        if sc > score:
            best, score = txt, sc
    return f'"{best.rstrip(".")}"' if best and score >= 1.3 else None


def title_hook(title):
    """Il titolo dato dallo spettatore che ha creato la clip virale e' spesso il miglior hook: lo pulisco."""
    t = re.sub(r"[^\x20-\x7E]", " ", title or "")
    t = re.sub(r"\s+", " ", re.sub(r"[@#]\w+", "", t)).strip(" -|:")
    n = len(t.split())
    if not (3 <= n <= 10) or BAD.search(t) or len(re.findall(r"[A-Za-z]", t)) < 8:
        return None
    if t.isupper():                            # TUTTO MAIUSCOLO: minuscolo, ma tengo le sigle corte (CG, PD, RP)
        t = " ".join(w if len(w) <= 3 else w.lower() for w in t.split())
    first = t.split()[0]
    return t[0].upper() + t[1:] if first.islower() else t


def style_hex(style):
    c = style["colore"][4:]          # &H00BBGGRR -> RRGGBB
    return c[4:6] + c[2:4] + c[0:2]


def finish(base, c, ch, style, split, top, teaser_len, total, words, out_mp4, out_jpg):
    ch_file, ass_file = os.path.join(WORK, "ch.txt"), os.path.join(WORK, "subs.ass")
    hook_lines = wrap(c["hook"]).split("\n")
    hook_files = []
    for k, line in enumerate(hook_lines):
        hf = os.path.join(WORK, f"hook{k}.txt")
        with open(hf, "w") as f:
            f.write(line)
        hook_files.append(hf)
    with open(ch_file, "w") as f:
        f.write(f"twitch.tv/{ch}")
    sub_y = (top + int((OUT_H - top) * 0.6)) if split else 1330
    n_sub = make_ass(words, style, ass_file, sub_y) if words else 0
    nl, lh = len(hook_lines), 66
    ty = (top if split else 250) - (nl * lh) // 2 + 4
    esc = lambda p: p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = []
    if split:
        vf.append(f"drawbox=x=0:y={top - 2}:w=iw:h=4:color=black:t=fill")
    if n_sub:
        vf.append(f"subtitles='{esc(ass_file)}':fontsdir='{esc(FONTS)}'")
    for k, hf in enumerate(hook_files):
        vf.append(f"drawtext=fontfile='{esc(FONT_B)}':textfile='{esc(hf)}':fontsize=42:fontcolor=0x111111:"
                  f"box=1:boxcolor=white:boxborderw=18:x=(w-text_w)/2:y={ty + k * lh}:alpha='min(1,t/0.25)'")
    vf.append(f"drawtext=fontfile='{esc(FONT_S)}':textfile='{esc(ch_file)}':fontsize=30:fontcolor=white:box=1:"
              f"boxcolor=black@0.55:boxborderw=14:x=(w-text_w)/2:y=h-250")
    fc = f"[0:v]{','.join(vf)}[vt];[vt][1:v]overlay=x='-w+W*t/{total:.2f}':y=H-10:shortest=1[v]"
    sh(["ffmpeg", "-v", "error", "-y", "-i", base, "-f", "lavfi", "-t", f"{total:.2f}", "-i",
        f"color=c=0x{style_hex(style)}:s={OUT_W}x10:r={FPS}", "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", out_mp4])
    peak_at = teaser_len + (c["peak"] - c["start"])
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, peak_at):.2f}", "-i", out_mp4, "-frames:v", "1",
        "-vf", "scale=360:-2", "-q:v", "4", out_jpg])
    return n_sub


def caption(c, ch, style_key, title):
    """Dalla ricerca: 1 riga di hook con gioco e tipo di momento, 1 call to action, 5 hashtag pertinenti."""
    style = STYLES[style_key]
    slug = re.sub(r"\W", "", ch.lower())   # ch = nome visualizzato
    rp = bool(re.search(r"\brp\b|roleplay|nopixel", (title or "").lower())) or style_key in ("funny", "drama")
    moment = {"hype": "reaction", "funny": "RP moment", "action": "getaway", "drama": "RP drama"}[style_key]
    tags = [f"#{slug}", "#gtarp" if rp else "#gta5", "#gta", "#twitchclips", "#fyp"]
    cta = style["cta"][int(time.time()) % len(style["cta"])]
    return f"{c['hook'].strip(chr(34))} {style['emoji']} {ch} {moment} in GTA {'RP' if rp else 'V'}\n{cta}\n\n" + " ".join(dict.fromkeys(tags))


def edit():
    st, cfg = load_state(), load_cfg()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    made, used = [], set()
    for i, j in enumerate(st["jobs"]):
        if not j.get("chosen"):
            continue
        style = STYLES.get(j["stile"], STYLES["hype"])
        bank = style["hooks"]
        for k, c in enumerate(j["chosen"]):
            cid = f"{j['ch']}_{stamp}_{i}{k}"
            base, mp4, jpg = (os.path.join(WORK, cid + e) for e in ("_base.mp4", ".mp4", ".jpg"))
            log(f"Editor: clip {k + 1}/{len(j['chosen'])} di {j['ch']} (voto {c['viral']}, {len(c.get('zooms', []))} micro zoom)")
            try:
                split, top, teaser_len, total = build_base(j["file"], j, c, style, base, cfg.get("teaser", True))
                words = transcribe(base, cfg.get("whisper_model", "base.en")) if cfg.get("sottotitoli", True) else []
                kind = "chat" if c.get("has_c") else "both" if c["has_a"] and c["has_s"] else "audio" if c["has_a"] \
                    else "scene" if c["has_s"] else "weak"
                hook = title_hook(j.get("titolo", "")) if j.get("tipo") == "clip" else None
                hook = hook or (quote_hook(words, teaser_len + c["peak"] - c["start"]) if j["stile"] != "action" else None)
                nm, off = j.get("nome") or j["ch"], int(time.time()) // 60
                own = [h.format(n=nm) for h in bank[kind]]                 # prima gli hook adatti a questo tipo di momento
                own = own[off % len(own):] + own[:off % len(own)]
                rest = [h.format(n=nm) for kk in ("both", "audio", "scene") for h in bank[kk]]
                pick = next((h for h in own + rest if h not in used), own[0])
                c["hook"] = hook if hook and hook not in used else pick
                used.add(c["hook"])
                n_sub = finish(base, c, j["ch"], style, split, top, teaser_len, total, words, mp4, jpg)
                os.remove(base)
            except Exception as e:
                log("Editor: montaggio fallito ->", e)
                continue
            made.append({"id": cid, "file": f"clips/{cid}.mp4", "poster": f"clips/{cid}.jpg", "ch": j["ch"], "stile": j["stile"],
                         "hook": c["hook"], "caption": caption(c, j.get("nome") or j["ch"], j["stile"], j.get("titolo", "")),
                         "viral": c["viral"], "subs": c["subs"], "zoom": len(c.get("zooms", [])), "sottotitoli": n_sub > 0,
                         "facecam": bool(j.get("cam")), "durata": round(total, 1), "created": int(time.time()),
                         "at": round(c["start"]), "mb": round(os.path.getsize(mp4) / 1048576, 1),
                         "fonte": "virale" if j.get("tipo") == "clip" else j.get("tipo", "live"), "slug": j.get("slug"),
                         "views": j.get("views"), "perche": c.get("perche", "")})
            log(f"Editor: fatto '{c['hook']}' · {n_sub} sottotitoli · {made[-1]['mb']} MB")
    st["clips"] = made
    save_state(st)


# ================================================================ PUBBLICA (galleria su gh-pages)
def fetch_pages():
    if os.path.isfile(os.path.join(PAGES, "index.html")):
        return
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    shutil.rmtree(PAGES, ignore_errors=True)
    if repo and token:
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "gh-pages",
                            f"https://x-access-token:{token}@github.com/{repo}.git", PAGES])
        if r.returncode == 0:
            return
    os.makedirs(PAGES, exist_ok=True)


def publish(st=None, final=True):
    st = st or load_state()
    cfg = load_cfg()
    fetch_pages()
    os.makedirs(os.path.join(PAGES, "clips"), exist_ok=True)
    db_path = os.path.join(PAGES, "clips.json")
    try:
        clips = json.load(open(db_path))
    except Exception:
        clips = []
    new = st.get("clips", []) if final else []
    for c in new:
        shutil.copy(os.path.join(WORK, c["id"] + ".mp4"), os.path.join(PAGES, c["file"]))
        shutil.copy(os.path.join(WORK, c["id"] + ".jpg"), os.path.join(PAGES, c["poster"]))
    clips = new + clips
    keep = int(cfg.get("tieni_clip", 40))
    for old in clips[keep:]:
        for f in (old["file"], old["poster"]):
            p = os.path.join(PAGES, f)
            if os.path.exists(p):
                os.remove(p)
    clips = clips[:keep]
    json.dump(clips, open(db_path, "w"), ensure_ascii=False, indent=1)
    if "live" in st:
        json.dump({"aggiornato": int(time.time()), "live": st["live"], "virali": st.get("virali", [])},
                  open(os.path.join(PAGES, "live.json"), "w"),
                  ensure_ascii=False, indent=1)
    try:
        status = json.load(open(os.path.join(PAGES, "status.json")))
    except Exception:
        status = {}
    status.update({"controllato": int(time.time()), "in_corso": not final,
                   "lavori": [{"ch": j["ch"], "stile": j["stile"], "minuti": j["minuti"], "tipo": j.get("tipo", "live")}
                              for j in st.get("jobs", [])]})
    if final:
        status["nuove_clip"] = len(new)
        if st.get("analisi"):
            lives_a = [a for a in st["analisi"] if a.get("tipo") != "clip"]
            status["analisi"] = (lives_a or st["analisi"])[-1]          # il radar mostra la live (piu' interessante)
    json.dump(status, open(os.path.join(PAGES, "status.json"), "w"), ensure_ascii=False, indent=1)
    shutil.copy(os.path.join(ROOT, "site", "index.html"), os.path.join(PAGES, "index.html"))
    open(os.path.join(PAGES, ".nojekyll"), "w").close()
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and token):
        log("Prova locale: galleria in", PAGES)
        return
    shutil.rmtree(os.path.join(PAGES, ".git"), ignore_errors=True)
    g = lambda *a: sh(["git", "-C", PAGES, *a])
    g("init", "-q", "-b", "gh-pages")
    g("config", "user.name", "ClipForge GTA")
    g("config", "user.email", "clipforge@users.noreply.github.com")
    g("add", "-A")
    g("commit", "-q", "-m", f"ClipForge: {len(new)} nuove clip" if final else "ClipForge: Scout")
    g("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{repo}.git", "gh-pages")
    log("Pubblica: galleria aggiornata" + (f" con {len(new)} nuove clip" if final else " (live e stato)"))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    os.makedirs(WORK, exist_ok=True)
    if stage == "scout":
        scout()
    elif stage == "record":
        record()
    elif stage == "analyze":
        analyze()
    elif stage == "edit":
        edit()
    elif stage == "publish":
        publish(final=True)
    elif stage == "all":
        if os.environ.get("TEST_FILE"):
            save_state({"jobs": [{"url": "", "ch": "teststreamer", "stile": os.environ.get("STILE", "hype"),
                                  "facecam": os.environ.get("FACECAM", "auto"), "minuti": 0, "da": 0, "titolo": "NoPixel RP", "tipo": "vod", "manuale": True,
                                  "file": os.environ["TEST_FILE"]}], "clips": [], "live": []})
        else:
            scout()
            record()
        analyze()
        edit()
        publish(final=True)
