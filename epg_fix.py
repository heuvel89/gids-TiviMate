#!/usr/bin/env python3
"""
Gidsrepareerder voor IPTV.

Wat het doet:
  1. Haalt je zenderlijst (M3U) op via de geheime link M3U_URL.
  2. Haalt openbare, betere gidsen op (zie bronnen.txt).
  3. Koppelt elke zender uit je lijst op naam aan de goede gids
     ("UK: Sky Sports F1 UHD" -> "Sky Sports F1").
  4. Schrijft één nieuwe gids (epg.xml.gz) met de codes (tvg-id) van JOUW lijst,
     zodat hij precies past in TV-Mate of een andere speler.
  5. Zenders die niet te koppelen zijn houden de gids van je aanbieder.
  6. Maakt een rapport (rapport.html) met wat wel en niet gekoppeld is.

Er worden nooit links of wachtwoorden in de uitvoer of in de logs gezet.
Alleen standaard-Python, niets te installeren.
"""

import difflib
import json
import gzip
import html
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
UITVOER = os.environ.get("UITVOER_MAP", os.path.join(HIER, "site"))
USER_AGENT = os.environ.get("USER_AGENT", "okhttp/4.12.0")

# ---------------------------------------------------------------- landen

LAND_CODES = {
    "nl": "nl", "ned": "nl", "nld": "nl", "holland": "nl",
    "uk": "uk", "gb": "uk", "en": "uk", "eng": "uk",
    "be": "be", "bel": "be", "de": "de", "ger": "de", "fr": "fr",
    "es": "es", "it": "it", "us": "us", "usa": "us", "pt": "pt",
    "tr": "tr", "pl": "pl", "ie": "ie", "ae": "ar", "ara": "ar", "arab": "ar",
    "aus": "au", "can": "ca", "ind": "in", "bra": "br", "mex": "mx",
}
LAND_NAMEN = {
    "netherlands": "nl", "nederland": "nl", "dutch": "nl",
    "united kingdom": "uk", "england": "uk", "britain": "uk",
    "belgium": "be", "belgie": "be", "germany": "de", "deutschland": "de",
}

# Voorvoegsel zoals "NL:", "NL |", "|NL|", "[UK]", "UK -", "UK ➾"
PREFIX_RE = re.compile(
    r"^\s*[\[\(\|]?\s*([A-Za-z]{2,3})\s*[\]\)\|:\-–➾»>•·]+\s*(.*)$"
)

# Woorden die alleen over beeldkwaliteit of de aanbieder gaan
RUIS = {
    "hd", "uhd", "fhd", "sd", "hq", "lq", "4k", "8k", "hevc", "h265", "h264",
    "50fps", "60fps", "fps", "raw", "backup", "bk", "vip", "multi", "live",
    "1080p", "720p", "1080", "720", "2160p", "hdr", "dolby", "plus1080",
}
GETAL_WOORDEN = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


def log(msg):
    print(msg, flush=True)


def verberg(waarde):
    """Laat GitHub deze waarde in de logs vervangen door ***."""
    if waarde and os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{waarde}", flush=True)


def ascii_vorm(tekst):
    tekst = unicodedata.normalize("NFKD", tekst)
    return "".join(c for c in tekst if not unicodedata.combining(c))


def splits_land(naam):
    """'UK: Sky Sports F1 UHD' -> ('uk', 'Sky Sports F1 UHD')."""
    m = PREFIX_RE.match(naam)
    if m and m.group(2).strip():
        code = m.group(1).lower()
        return LAND_CODES.get(code, code), m.group(2).strip()
    return None, naam


def land_uit_groep(groep):
    g = ascii_vorm(groep or "").lower()
    land, _ = splits_land(groep or "")
    if land:
        return land
    for naam, code in LAND_NAMEN.items():
        if naam in g:
            return code
    m = re.match(r"^\W*([A-Z]{2,3})\s", groep or "")  # "NL SPORT", "UK ENTERTAINMENT"
    if m:
        code = m.group(1).lower()
        return LAND_CODES.get(code, code)
    return None


def normaliseer(naam):
    """Maak van een zendernaam een vergelijkbare sleutel."""
    t = ascii_vorm(naam).lower()
    t = t.replace("&", " and ").replace("+1", " plus1 ").replace("+ 1", " plus1 ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    woorden = []
    for w in t.split():
        w = GETAL_WOORDEN.get(w, w)
        if w in RUIS:
            continue
        woorden.append(w)
    return " ".join(woorden)


def normaliseer_gids_id(cid):
    """'Sky.Sports.F1.HD.uk' -> ('uk', 'sky sports f1')."""
    land = None
    m = re.match(r"^(.*)\.([a-z]{2,3})$", cid, re.I)
    basis = cid
    if m:
        code = m.group(2).lower()
        land = LAND_CODES.get(code, code)
        basis = m.group(1)
    basis = re.sub(r"[._]+", " ", basis)
    basis = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", basis)  # SkySports -> Sky Sports
    return land, normaliseer(basis)


# ---------------------------------------------------------------- ophalen

def download(url, doel, naam, pogingen=3):
    """Download naar bestand. Geeft True/False. Toont nooit de link."""
    if os.path.exists(url):  # lokaal bestand (voor testen)
        shutil.copyfile(url, doel)
        return True
    for poging in range(1, pogingen + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as r, open(doel, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            grootte = os.path.getsize(doel) / 1e6
            log(f"  ✓ {naam} opgehaald ({grootte:.1f} MB)")
            return True
        except urllib.error.HTTPError as e:
            log(f"  ✗ {naam}: server gaf foutcode {e.code} (poging {poging})")
        except urllib.error.URLError as e:
            reden = getattr(e, "reason", None)
            soort = type(reden).__name__ if reden is not None else "onbekend"
            nr = getattr(reden, "errno", None)
            log(f"  ✗ {naam}: verbinding mislukt ({soort}, code {nr}) (poging {poging})")
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ {naam}: {type(e).__name__} (poging {poging})")
        time.sleep(5 * poging)
    return False


def open_xml(pad):
    with open(pad, "rb") as f:
        begin = f.read(2)
    return gzip.open(pad, "rb") if begin == b"\x1f\x8b" else open(pad, "rb")


def iter_elementen(pad, tag):
    """Loop zuinig door een (grote) XMLTV-gids heen."""
    with open_xml(pad) as f:
        ctx = ET.iterparse(f, events=("start", "end"))
        _, root = next(ctx)
        for event, el in ctx:
            if event == "end" and el.tag in ("channel", "programme"):
                if el.tag == tag:
                    yield el
                root.clear()


# ---------------------------------------------------------------- playlist

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def lees_playlist(pad):
    zenders = []
    kop_epg = None
    with open_xml(pad) as f:
        tekst = f.read().decode("utf-8", errors="replace")
    regels = tekst.splitlines()
    huidig = None
    for regel in regels:
        regel = regel.strip()
        if regel.startswith("#EXTM3U"):
            attrs = dict(ATTR_RE.findall(regel))
            kop_epg = (attrs.get("x-tvg-url") or attrs.get("url-tvg") or "").split(",")[0].strip() or None
        elif regel.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(regel))
            naam = regel.rsplit(",", 1)[-1].strip() if "," in regel else ""
            huidig = {
                "id": attrs.get("tvg-id", "").strip(),
                "naam": naam or attrs.get("tvg-name", "").strip(),
                "groep": attrs.get("group-title", "").strip(),
                "logo": attrs.get("tvg-logo", "").strip(),
            }
        elif regel and not regel.startswith("#") and huidig is not None:
            pad_deel = urllib.parse.urlparse(regel).path.lower()
            is_vod = "/movie/" in pad_deel or "/series/" in pad_deel or re.search(
                r"\.(mp4|mkv|avi)$", pad_deel)
            if not is_vod:
                zenders.append(huidig)
            huidig = None
    return zenders, kop_epg


def is_scheiding(naam):
    return bool(re.search(r"#{2,}|={3,}|-{3,}|\*{3,}|▬|═", naam)) or not normaliseer(naam)


# ---------------------------------------------------------------- instellingen

def lees_bronnen():
    bronnen = []
    with open(os.path.join(HIER, "bronnen.txt"), encoding="utf-8") as f:
        for regel in f:
            regel = regel.split("#", 1)[0].strip()
            if not regel:
                continue
            delen = regel.split(None, 1)
            if len(delen) == 2:
                land = LAND_CODES.get(delen[0].lower(), delen[0].lower())
                bronnen.append((land, delen[1].strip()))
    return bronnen


def lees_koppelingen():
    """Regels 'zendernaam in je lijst = gids-code'."""
    koppel = {}
    pad = os.path.join(HIER, "koppelingen.txt")
    if not os.path.exists(pad):
        return koppel
    with open(pad, encoding="utf-8") as f:
        for regel in f:
            regel = regel.strip()
            if not regel or regel.startswith("#") or "=" not in regel:
                continue
            links, rechts = regel.rsplit("=", 1)
            koppel[normaliseer(splits_land(links.strip())[1])] = rechts.strip()
            koppel["__exact__" + links.strip().lower()] = rechts.strip()
    return koppel


# ---------------------------------------------------------------- hoofdwerk

def main():
    m3u_url = os.environ.get("M3U_URL", "").strip()
    if not m3u_url:
        log("FOUT: geheim M3U_URL ontbreekt. Zie stap 3 in README.")
        sys.exit(1)
    verberg(m3u_url)
    log("Link-controle: begint met http(s):// = "
        + ("ja" if re.match(r"^https?://", m3u_url) else "NEE (fout!)")
        + ", bevat spaties = " + ("JA (fout!)" if re.search(r"\s", m3u_url) else "nee"))

    tmp = tempfile.mkdtemp()
    os.makedirs(UITVOER, exist_ok=True)

    # 1. Playlist
    log("1. Zenderlijst ophalen")
    pl_pad = os.path.join(tmp, "playlist.m3u")
    zenders, kop_epg = [], None
    if download(m3u_url, pl_pad, "zenderlijst (M3U)"):
        zenders, kop_epg = lees_playlist(pl_pad)
    if not zenders:
        log("  M3U leverde niets op, probeer Xtream-methode")
        zenders = xtream_zenders(m3u_url, tmp)
    if not zenders:
        log("FOUT: zenderlijst kon niet worden opgehaald. Klopt je link nog?")
        sys.exit(1)
    zenders = [z for z in zenders if not is_scheiding(z["naam"])]
    log(f"  {len(zenders)} live zenders gevonden")

    # 2. Bronnen
    bronnen = lees_bronnen()
    landen = {land for land, _ in bronnen}
    log(f"2. Openbare gidsen ophalen ({len(bronnen)} bronnen, landen: {', '.join(sorted(landen))})")
    bron_paden = []
    for i, (land, url) in enumerate(bronnen):
        p = os.path.join(tmp, f"bron{i}.xml")
        if download(url, p, f"gids {i + 1} ({land})"):
            bron_paden.append((i, land, p))

    # 3. Index van gidszenders: (land, sleutel) -> (bron, id)
    log("3. Gidszenders inlezen")
    index = {}
    gids_info = {}          # (bron, id) -> {naam, icon, land}
    id_index = {}           # id (lowercase) -> (bron, id)
    for i, land, p in bron_paden:
        try:
            for el in iter_elementen(p, "channel"):
                cid = el.get("id", "")
                if not cid:
                    continue
                namen = [dn.text or "" for dn in el.findall("display-name")]
                icon = el.find("icon")
                sleutel_land, id_sleutel = normaliseer_gids_id(cid)
                eig_land = land if land not in ("alle", "all", "*") else (sleutel_land or "?")
                key = (i, cid)
                gids_info[key] = {
                    "naam": namen[0] if namen else cid,
                    "icon": icon.get("src") if icon is not None else "",
                    "land": eig_land,
                }
                id_index.setdefault(cid.lower(), key)
                for s in [id_sleutel] + [normaliseer(splits_land(n)[1]) for n in namen]:
                    if s:
                        index.setdefault((eig_land, s), key)
        except ET.ParseError:
            log(f"  ✗ gids {i + 1} is beschadigd, overgeslagen")
    log(f"  {len(gids_info)} gidszenders beschikbaar")

    # 4. Koppelen
    log("4. Koppelen")
    handmatig = lees_koppelingen()
    koppeling = {}           # playlist tvg-id -> (bron, id)
    gekoppeld, niet_gekoppeld, zonder_id, conflicten = [], [], [], []
    for z in zenders:
        land_prefix, kale_naam = splits_land(z["naam"])
        land = land_prefix or land_uit_groep(z["groep"])
        sleutel = normaliseer(kale_naam)
        doel = None
        hand = handmatig.get("__exact__" + z["naam"].lower()) or handmatig.get(sleutel)
        if hand:
            doel = id_index.get(hand.lower())
            if not doel:
                niet_gekoppeld.append((z, land, f"koppeling '{hand}' bestaat niet in de gidsen"))
                continue
        elif land in landen:
            doel = index.get((land, sleutel))
        else:
            continue  # land niet in bronnen: laat aanbieder-gids staan
        if not doel:
            niet_gekoppeld.append((z, land, ""))
            continue
        if not z["id"]:
            zonder_id.append((z, doel))
            continue
        if z["id"] in koppeling:
            if koppeling[z["id"]] != doel:
                conflicten.append((z, doel, koppeling[z["id"]]))
            continue
        koppeling[z["id"]] = doel
        gekoppeld.append((z, doel))
    log(f"  gekoppeld: {len(gekoppeld)} | niet gevonden: {len(niet_gekoppeld)} | "
        f"zonder tvg-id: {len(zonder_id)} | dubbele code: {len(conflicten)}")

    # omgekeerd: gidszender -> lijst playlist-ids
    naar_playlist = defaultdict(list)
    for pid, doel in koppeling.items():
        naar_playlist[doel].append(pid)

    # 5. Gids van aanbieder (voor de rest)
    aanbieder_pad = None
    if os.environ.get("AANBIEDER_GIDS", "ja").lower() in ("ja", "yes", "true", "1"):
        epg_url = os.environ.get("EPG_URL", "").strip() or kop_epg or afgeleide_epg_url(m3u_url)
        if epg_url:
            verberg(epg_url)
            log("5. Gids van aanbieder ophalen (voor zenders die niet gerepareerd worden)")
            p = os.path.join(tmp, "aanbieder.xml")
            if download(epg_url, p, "aanbieder-gids"):
                aanbieder_pad = p
        else:
            log("5. Geen gids-link van aanbieder gevonden, overgeslagen")

    alle_ids = {z["id"] for z in zenders if z["id"]}
    rest_ids = alle_ids - set(koppeling)

    # 6. Schrijven
    log("6. Nieuwe gids schrijven")
    xml_pad = os.path.join(tmp, "epg.xml")
    aantal_prog = 0
    with open(xml_pad, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write('<tv generator-info-name="gidsrepareerder">\n')
        geschreven = set()
        naam_per_id = {}
        for z in zenders:
            if z["id"]:
                naam_per_id.setdefault(z["id"], z)
        for pid, doel in koppeling.items():
            z = naam_per_id[pid]
            info = gids_info[doel]
            out.write(kanaal_xml(pid, z["naam"], z["logo"] or info["icon"]))
            geschreven.add(pid)
        aanbieder_kanalen = 0
        if aanbieder_pad:
            try:
                for el in iter_elementen(aanbieder_pad, "channel"):
                    cid = el.get("id", "")
                    if cid in rest_ids and cid not in geschreven:
                        el.tail = "\n"
                        out.write(ET.tostring(el, encoding="unicode"))
                        geschreven.add(cid)
                        aanbieder_kanalen += 1
            except ET.ParseError:
                log("  ✗ gids van aanbieder is beschadigd, deels gebruikt")
        # Programma's uit openbare gidsen
        for i, land, p in bron_paden:
            try:
                for el in iter_elementen(p, "programme"):
                    pids = naar_playlist.get((i, el.get("channel", "")))
                    if not pids:
                        continue
                    for pid in pids:
                        el.set("channel", pid)
                        el.tail = "\n"
                        out.write(ET.tostring(el, encoding="unicode"))
                        aantal_prog += 1
            except ET.ParseError:
                log(f"  ✗ gids {i + 1} raakte halverwege beschadigd")
        # Programma's van aanbieder voor de rest
        if aanbieder_pad:
            try:
                for el in iter_elementen(aanbieder_pad, "programme"):
                    if el.get("channel", "") in rest_ids:
                        el.tail = "\n"
                        out.write(ET.tostring(el, encoding="unicode"))
                        aantal_prog += 1
            except ET.ParseError:
                pass
        out.write("</tv>\n")
    log(f"  {len(geschreven)} zenders, {aantal_prog} programma's")

    gz_pad = os.path.join(UITVOER, "epg.xml.gz")
    with open(xml_pad, "rb") as f_in, gzip.open(gz_pad, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    if os.path.getsize(xml_pad) < 90e6:
        shutil.copyfile(xml_pad, os.path.join(UITVOER, "epg.xml"))
    elif os.path.exists(os.path.join(UITVOER, "epg.xml")):
        os.remove(os.path.join(UITVOER, "epg.xml"))

    # 7. Rapport
    schrijf_rapport(gekoppeld, niet_gekoppeld, zonder_id, conflicten, gids_info,
                    index, len(zenders), aantal_prog, aanbieder_pad is not None)
    log("Klaar ✓")
    shutil.rmtree(tmp, ignore_errors=True)


def xtream_zenders(m3u_url, tmp):
    """Zenderlijst via player_api.php (als get.php geblokkeerd is)."""
    u = urllib.parse.urlparse(m3u_url)
    q = urllib.parse.parse_qs(u.query)
    if "username" not in q or "password" not in q:
        return []
    pad = re.sub(r"[^/]*$", "player_api.php", u.path or "/", count=1)

    def api(actie):
        query = urllib.parse.urlencode({"username": q["username"][0],
                                        "password": q["password"][0], "action": actie})
        url = urllib.parse.urlunparse((u.scheme, u.netloc, pad, "", query, ""))
        verberg(url)
        doel = os.path.join(tmp, actie + ".json")
        if not download(url, doel, f"zenderlijst ({actie})"):
            return []
        try:
            with open(doel, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except ValueError:
            log(f"  ✗ {actie}: antwoord was geen geldige lijst")
            return []

    cats = {str(c.get("category_id")): c.get("category_name", "") for c in api("get_live_categories")}
    zenders = []
    for st in api("get_live_streams"):
        zenders.append({
            "id": str(st.get("epg_channel_id") or "").strip(),
            "naam": str(st.get("name") or "").strip(),
            "groep": cats.get(str(st.get("category_id")), ""),
            "logo": str(st.get("stream_icon") or "").strip(),
        })
    return zenders


def afgeleide_epg_url(m3u_url):
    """Xtream: .../get.php?username=..&password=.. -> .../xmltv.php?username=..&password=.."""
    u = urllib.parse.urlparse(m3u_url)
    q = urllib.parse.parse_qs(u.query)
    if "username" in q and "password" in q:
        nieuwe_q = urllib.parse.urlencode(
            {"username": q["username"][0], "password": q["password"][0]})
        pad = re.sub(r"[^/]*$", "xmltv.php", u.path or "/", count=1)
        return urllib.parse.urlunparse((u.scheme, u.netloc, pad, "", nieuwe_q, ""))
    return None


def kanaal_xml(cid, naam, icon):
    s = f'<channel id="{html.escape(cid, quote=True)}"><display-name>{html.escape(naam)}</display-name>'
    if icon:
        s += f'<icon src="{html.escape(icon, quote=True)}"/>'
    return s + "</channel>\n"


def schrijf_rapport(gekoppeld, niet, zonder_id, conflicten, gids_info, index,
                    totaal, aantal_prog, aanbieder):
    e = html.escape
    nu = datetime.now(timezone.utc).astimezone().strftime("%d-%m-%Y %H:%M")
    per_land = defaultdict(list)
    for (land, sleutel), key in index.items():
        per_land[land].append((sleutel, key))
    sleutels_per_land = {l: [s for s, _ in v] for l, v in per_land.items()}
    key_per = {l: dict(v) for l, v in per_land.items()}

    def suggesties(z, land):
        sleutel = normaliseer(splits_land(z["naam"])[1])
        lijst = sleutels_per_land.get(land, [])
        uit = []
        for s in difflib.get_close_matches(sleutel, lijst, n=3, cutoff=0.55):
            key = key_per[land][s]
            uit.append(f"{e(gids_info[key]['naam'])} → <code>{e(key[1])}</code>")
        return "<br>".join(uit) or "–"

    rijen_ok = "".join(
        f"<tr><td>{e(z['naam'])}</td><td>{e(gids_info[d]['naam'])}</td><td><code>{e(d[1])}</code></td></tr>"
        for z, d in sorted(gekoppeld, key=lambda x: x[0]["naam"].lower()))
    rijen_niet = "".join(
        f"<tr><td>{e(z['naam'])}</td><td>{e(reden) or suggesties(z, land)}</td></tr>"
        for z, land, reden in sorted(niet, key=lambda x: x[0]["naam"].lower()))
    rijen_zonder = "".join(f"<li>{e(z['naam'])}</li>" for z, _ in zonder_id)
    rijen_conf = "".join(
        f"<li>{e(z['naam'])} (code <code>{e(z['id'])}</code> wordt al voor een andere zender gebruikt)</li>"
        for z, _, _ in conflicten)

    pagina = f"""<!DOCTYPE html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gidsrapport</title><style>
body{{font-family:system-ui,sans-serif;margin:16px;line-height:1.45;color:#1b1b1b;background:#fafafa}}
h1{{font-size:1.4em}}h2{{font-size:1.1em;margin-top:1.6em}}
table{{border-collapse:collapse;width:100%;font-size:.9em;background:#fff}}
td,th{{border-bottom:1px solid #e3e3e3;padding:6px;text-align:left;vertical-align:top}}
code{{background:#eef;padding:1px 4px;border-radius:3px;font-size:.9em;word-break:break-all}}
.kaart{{background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:12px;margin:8px 0}}
details summary{{cursor:pointer;font-weight:600}}
</style></head><body>
<h1>📺 Gidsrapport</h1>
<div class="kaart">Bijgewerkt: <b>{nu}</b><br>
Zenders in je lijst: <b>{totaal}</b><br>
Gerepareerd met betere gids: <b>{len(gekoppeld)}</b><br>
Niet gevonden: <b>{len(niet)}</b><br>
Programma's in nieuwe gids: <b>{aantal_prog}</b><br>
Gids van aanbieder voor de rest: <b>{'ja' if aanbieder else 'nee'}</b></div>
<p>Gidslink voor TV-Mate: <code>epg.xml.gz</code> in deze map.</p>

<h2>❌ Niet gevonden ({len(niet)})</h2>
<p>Staat de goede zender bij de suggesties? Zet dan in <code>koppelingen.txt</code> een regel:<br>
<code>zendernaam zoals in je lijst = code</code></p>
<table><tr><th>Zender in je lijst</th><th>Suggesties</th></tr>{rijen_niet}</table>
{"<h2>⚠️ Geen tvg-id in je lijst</h2><p>Deze zenders hebben bij je aanbieder geen code, dus de speler kan er geen gids aan koppelen.</p><ul>" + rijen_zonder + "</ul>" if rijen_zonder else ""}
{"<h2>⚠️ Dubbele codes</h2><p>Je aanbieder geeft verschillende zenders dezelfde code. Dat kan een gids niet oplossen.</p><ul>" + rijen_conf + "</ul>" if rijen_conf else ""}
<h2>✅ Gerepareerd ({len(gekoppeld)})</h2>
<details><summary>Toon lijst</summary>
<table><tr><th>Zender in je lijst</th><th>Gekoppeld aan</th><th>Code</th></tr>{rijen_ok}</table></details>
</body></html>"""
    with open(os.path.join(UITVOER, "rapport.html"), "w", encoding="utf-8") as f:
        f.write(pagina)
    with open(os.path.join(UITVOER, "index.html"), "w", encoding="utf-8") as f:
        f.write('<!DOCTYPE html><meta http-equiv="refresh" content="0; url=rapport.html">')


if __name__ == "__main__":
    main()
