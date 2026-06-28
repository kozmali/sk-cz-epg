#!/usr/bin/env python3
"""
fallback_epg.py

Doplnkový EPG scraper pre lokálne/regionálne stanice, ktoré nemajú EPG
v žiadnom z hlavných XMLTV zdrojov (epgshare01, iptv-org, globetvapp...).

Vystupom je epg_fallback.xml.gz vo formáte XMLTV (<channel> + <programme>),
ktorý sa potom v merge_epg.py jednoducho prida medzi ostatne SOURCES
(rovnaky princip ako uz teraz davas dokopy epgshare01 + iptv-org atd.).

STAV JEDNOTLIVYCH ZDROJOV (overene 28.6.2026):
  - lifetv      -> OK, overena struktura, realny denny program na tyzdne dopredu
  - povazie     -> OK, overena struktura, fixny opakujuci sa 24h rozvrh
  - rtg_int     -> OK, overena struktura (HTML denne stranky s datumom v URL)
  - mtr         -> CIASTOCNE, len genericky 2h opakujuci sa blok (nazvy typov
                   programov, nie konkretne tituly), navyse rotuje podla
                   tyzdna v mesiaci
  - ruzinov     -> NEOVERENE - tvr.sk ma v robots.txt zakaz automatizovaneho
                   pristupu, takze som sa tam necez svoj fetch nedostal a
                   nevidel realnu HTML strukturu. Funkcia nizsie je best-effort
                   sablona - over si CSS selektory v devtools prehliadaca
                   a uprav podla skutocnej stranky.
  - plzen, polar2, liptov -> VYNECHANE, vid poznamky na konci suboru

Pouzitie:
  pip install requests beautifulsoup4 python-dateutil --break-system-packages
  python3 fallback_epg.py
"""
import gzip
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

OUTPUT_GZ = "epg_fallback.xml.gz"
TZ = timezone(timedelta(hours=2))  # CEST, leto - over si DST podla obdobia
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


PURE_TIME_RE = re.compile(r"^(\d{2}):(\d{2})$")
INLINE_TIME_RE = re.compile(r"^(\d{2}):(\d{2})\s*[-:]?\s*(.+)$")


def parse_schedule_lines(lines):
    """Univerzalny parser pre 'HH:MM Nazov' rozvrhy.

    Zvladne DVE rozne situacie, ktore sa v praxi striedaju podla toho,
    ci je cas v rovnakom textovom uzle ako nazov, alebo v samostatnom
    <strong>/<b> tagu (vtedy BeautifulSoup.get_text("\\n") rozdeli
    cas a nazov na dva riadky):
      1) jeden riadok: '05:10 Nazov relacie' / '05:10 - Nazov'
      2) dva riadky:   '05:10' (samotny cas) potom dalsi neprazdny
                        riadok s nazvom

    Vrati zoznam (hh, mm, title) v poradi vystupu.
    """
    results = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        m_pure = PURE_TIME_RE.match(line)
        if m_pure:
            hh, mm = int(m_pure.group(1)), int(m_pure.group(2))
            j = i + 1
            title = None
            while j < n:
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                # ak dalsi neprazdny riadok je uz dalsi cas, tento blok
                # je bez nazvu - preskocime ho bez priradenia title
                if PURE_TIME_RE.match(nxt) or INLINE_TIME_RE.match(nxt):
                    break
                title = nxt
                j += 1
                break
            if title:
                results.append((hh, mm, title))
            i = j if title else i + 1
            continue

        m_inline = INLINE_TIME_RE.match(line)
        if m_inline:
            hh, mm, title = m_inline.groups()
            results.append((int(hh), int(mm), title.strip()))

        i += 1
    return results


# ---------------------------------------------------------------------------
# 1) LifeTv  (https://www.lifetv.sk/program/)
# ---------------------------------------------------------------------------
def scrape_lifetv():
    """LifeTv ma na jednej stranke vypisany cely program po dnoch,
    nazvy dni su slovenske ('Pondelok 22.6.2026', ...), pod kazdym
    je zoznam HH:MM Nazov relacie (cas je v <strong>, takze cas
    a nazov mozu skoncit na samostatnych riadkoch - pozri
    parse_schedule_lines)."""
    programmes = []
    try:
        resp = requests.get("https://www.lifetv.sk/program/", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n")
    except Exception as e:
        print(f"[lifetv] chyba pri stahovani: {e}")
        return programmes

    day_re = re.compile(
        r"(Pondelok|Utorok|Streda|Štvrtok|Piatok|Sobota|Nedeľa)\s+(\d{1,2})\.(\d{1,2})\.(\d{4})"
    )

    lines = text.splitlines()
    # najprv rozdelime cely text na bloky podla dna (kazdy blok = vsetky
    # riadky medzi jednym a dalsim vyskytom day_re)
    day_blocks = []  # list of (datetime, [lines])
    current_lines = None
    for line in lines:
        stripped = line.strip()
        m = day_re.match(stripped)
        if m:
            _, d, mo, y = m.groups()
            try:
                day_dt = datetime(int(y), int(mo), int(d))
            except ValueError:
                day_dt = None
            if day_dt:
                current_lines = []
                day_blocks.append((day_dt, current_lines))
            else:
                current_lines = None
            continue
        if current_lines is not None:
            current_lines.append(line)

    seen_dates = set()
    for day_date, block_lines in day_blocks:
        key = day_date.date()
        if key in seen_dates:
            continue
        items = parse_schedule_lines(block_lines)
        # blok konci tam, kde zacina dalsi den, ale ak je VŠEOBECNÝ PROGRAM
        # alebo iny nesuvisiaci text, parse_schedule_lines ho jednoducho
        # nenajde ako HH:MM riadok, takze je to v poriadku ignorovat
        if not items:
            continue
        seen_dates.add(key)
        items = sorted(set(items), key=lambda x: (x[0], x[1]))
        for idx, (hh, mm, title) in enumerate(items):
            start = day_date.replace(hour=hh, minute=mm, tzinfo=TZ)
            if idx + 1 < len(items):
                nh, nm, _ = items[idx + 1]
                stop = day_date.replace(hour=nh, minute=nm, tzinfo=TZ)
                if stop <= start:
                    stop += timedelta(days=1)
            else:
                stop = start + timedelta(hours=1)
            programmes.append(("LifeTV.sk", start, stop, title))

    print(f"[lifetv] najdenych {len(programmes)} programov")
    return programmes


# ---------------------------------------------------------------------------
# 2) TV Považie (http://tvpovazie.sk/index.php/program)
# ---------------------------------------------------------------------------
def scrape_povazie(days_ahead=7):
    """TV Považie ma FIXNY rozvrh - rovnaka tabulka sa opakuje kazdy den
    (00:00-10:00 blok zopakovany aj 12:00-22:00). Naskrabeme raz a
    vygenerujeme pre kazdy nasledujuci den."""
    programmes = []
    try:
        resp = requests.get("http://tvpovazie.sk/index.php/program", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[povazie] chyba pri stahovani: {e}")
        return programmes

    row_re = re.compile(r"(\d{2}):(\d{2})\s*-\s*(.+)")
    slots = []  # (hh, mm, title)
    for cell in soup.find_all("td"):
        txt = cell.get_text(strip=True)
        m = row_re.match(txt)
        if m:
            hh, mm, title = m.groups()
            slots.append((int(hh), int(mm), title.strip()))

    if not slots:
        print("[povazie] nenasiel som ziadne riadky rozvrhu, overiť strukturu stranky")
        return programmes

    # odstranime duplicity a zoradime podla casu
    seen = set()
    uniq_slots = []
    for s in slots:
        key = (s[0], s[1])
        if key not in seen:
            seen.add(key)
            uniq_slots.append(s)
    uniq_slots.sort(key=lambda x: (x[0], x[1]))

    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    for day_offset in range(days_ahead):
        day = today + timedelta(days=day_offset)
        for idx, (hh, mm, title) in enumerate(uniq_slots):
            start = day.replace(hour=hh, minute=mm)
            if idx + 1 < len(uniq_slots):
                nh, nm, _ = uniq_slots[idx + 1]
                stop = day.replace(hour=nh, minute=nm)
            else:
                stop = day + timedelta(days=1)
            if stop <= start:
                stop += timedelta(days=1)
            programmes.append(("TV.Považie.cz", start, stop, title))

    print(f"[povazie] vygenerovanych {len(programmes)} programov (fixny rozvrh x {days_ahead} dni)")
    return programmes


# ---------------------------------------------------------------------------
# 3) RTG int. (http://rtgtv.ru/schedule/rtgint/<YYYY-MM-DD>)
# ---------------------------------------------------------------------------
def scrape_rtg_int(days_ahead=6):
    """RTG ma pekne cisty denny rozvrh s datumom rovno v URL."""
    programmes = []
    today = datetime.now(TZ).date()

    day_programmes = {}
    for day_offset in range(days_ahead):
        day = today + timedelta(days=day_offset)
        url = f"http://rtgtv.ru/schedule/rtgint/{day.isoformat()}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text("\n")
        except Exception as e:
            print(f"[rtg_int] chyba pri stahovani {url}: {e}")
            continue

        items = parse_schedule_lines(text.splitlines())
        if items:
            day_programmes[day] = items

    sorted_days = sorted(day_programmes.keys())
    for i, day in enumerate(sorted_days):
        items = day_programmes[day]
        day_dt = datetime(day.year, day.month, day.day, tzinfo=TZ)
        for idx, (hh, mm, title) in enumerate(items):
            start = day_dt.replace(hour=hh, minute=mm)
            if idx + 1 < len(items):
                nh, nm, _ = items[idx + 1]
                stop = day_dt.replace(hour=nh, minute=nm)
                if stop <= start:
                    stop += timedelta(days=1)
            else:
                stop = start + timedelta(minutes=30)
            programmes.append(("RTGINT.ru", start, stop, title))

    print(f"[rtg_int] najdenych {len(programmes)} programov")
    return programmes


# ---------------------------------------------------------------------------
# 4) MTR - Mestská televízia Ružomberok (genericky 2h opakujuci sa blok)
# ---------------------------------------------------------------------------
def scrape_mtr(days_ahead=7):
    """MTR ma 2-hodinovu sluku co sa opakuje kazdu parnu hodinu:
    30 min Spravy, 30 min Relacia (rotuje podla tyzdna v mesiaci),
    20 min Dvojtyzdennik, 10 min Bez komentara, 10 min Videoklipy,
    20 min Infotext. Konkretne nazvy relacii sa menia, takze davame
    len genericke bloky - lepsie ako ziadne EPG."""
    programmes = []
    week_variants = {1: "Téma", 2: "Dokument", 3: "Reportáž", 4: "Play"}
    block = [
        (0, "Správy"),
        (30, None),  # Relácia - nazov podla tyzdna v mesiaci
        (60, "Dvojtýždenník"),
        (80, "Bez komentára"),
        (90, "Videoklipy"),
        (100, "Infotext"),
    ]
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    for day_offset in range(days_ahead):
        day = today + timedelta(days=day_offset)
        week_of_month = ((day.day - 1) // 7) + 1
        relacia_name = week_variants.get(week_of_month, "Relácia")
        for loop_start_hour in range(0, 24, 2):
            loop_start = day.replace(hour=loop_start_hour)
            for idx, (offset_min, title) in enumerate(block):
                start = loop_start + timedelta(minutes=offset_min)
                stop_offset = block[idx + 1][0] if idx + 1 < len(block) else 120
                stop = loop_start + timedelta(minutes=stop_offset)
                programmes.append(("Mestská.televízia.Ružomberok.cz", start, stop, title or relacia_name))

    print(f"[mtr] vygenerovanych {len(programmes)} genericky-robi blokov (x {days_ahead} dni)")
    return programmes


# ---------------------------------------------------------------------------
# 5) TV Doktor (https://www.tvdoktor.sk/program/)
# ---------------------------------------------------------------------------
def scrape_tvdoktor():
    """TV Doktor ma na jednej stranke realny program na ~7 dni dopredu,
    rozdeleny do tabulek po dnoch s hlavickou 'Nedeľa 28.06.' (bez roku -
    rok si dopocitame podla aktualneho datumu, s rolloverom okolo NY)."""
    programmes = []
    try:
        resp = requests.get("https://www.tvdoktor.sk/program/", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n")
    except Exception as e:
        print(f"[tvdoktor] chyba pri stahovani: {e}")
        return programmes

    day_re = re.compile(
        r"(Pondelok|Utorok|Streda|Štvrtok|Piatok|Sobota|Nedeľa)\s+(\d{1,2})\.(\d{1,2})\."
    )

    lines = text.splitlines()
    day_blocks = []
    current_lines = None
    today = datetime.now(TZ).date()

    for line in lines:
        stripped = line.strip()
        m = day_re.match(stripped)
        if m:
            _, d, mo = m.groups()
            day, month = int(d), int(mo)
            year = today.year
            try:
                candidate = datetime(year, month, day).date()
            except ValueError:
                current_lines = None
                continue
            # rocny rollover - ak by datum vysiel viac ako ~2 mesiace v
            # minulosti, je to skor o rok neskor (TV Doktor ukazuje len
            # buduce dni, takze toto riesi hlavne prelom december/januar)
            if (today - candidate).days > 60:
                candidate = datetime(year + 1, month, day).date()
            current_lines = []
            day_blocks.append((candidate, current_lines))
            continue
        if current_lines is not None:
            current_lines.append(line)

    seen_dates = set()
    for day_date, block_lines in day_blocks:
        if day_date in seen_dates:
            continue
        items = parse_schedule_lines(block_lines)
        if not items:
            continue
        seen_dates.add(day_date)
        items = sorted(set(items), key=lambda x: (x[0], x[1]))
        day_dt = datetime(day_date.year, day_date.month, day_date.day, tzinfo=TZ)
        for idx, (hh, mm, title) in enumerate(items):
            start = day_dt.replace(hour=hh, minute=mm)
            if idx + 1 < len(items):
                nh, nm, _ = items[idx + 1]
                stop = day_dt.replace(hour=nh, minute=nm)
                if stop <= start:
                    stop += timedelta(days=1)
            else:
                stop = start + timedelta(hours=1)
            programmes.append(("TVDOKTOR.sk", start, stop, title))

    print(f"[tvdoktor] najdenych {len(programmes)} programov")
    return programmes


# ---------------------------------------------------------------------------
# 6) TV Ružinov - NEOVERENA SABLONA (robots.txt blokuje moj fetch)
# ---------------------------------------------------------------------------
def scrape_ruzinov():
    """POZOR: nemohol som si overit skutocnu HTML strukturu tvr.sk/program/,
    pretoze robots.txt explicitne zakazuje automatizovany pristup a moj
    nastroj to respektuje. Teraz pouziva ten isty robustny parser ako
    lifetv/rtg_int/tvdoktor (zvladne aj cas oddeleny od nazvu na inom
    riadku), takze sance na uspech su vyssie - ale stale over si vystup."""
    programmes = []
    try:
        resp = requests.get("https://tvr.sk/program/", headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ruzinov] chyba pri stahovani (alebo blokovane na ich strane): {e}")
        return programmes

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n")
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    items = parse_schedule_lines(text.splitlines())

    if not items:
        print("[ruzinov] ZIADNE polozky najdene - struktura stranky je inak. "
              "Skontroluj HTML rucne a uprav scrape_ruzinov().")
        return programmes

    items = sorted(set(items), key=lambda x: (x[0], x[1]))
    for idx, (hh, mm, title) in enumerate(items):
        start = today.replace(hour=hh, minute=mm)
        stop = (today.replace(hour=items[idx + 1][0], minute=items[idx + 1][1])
                if idx + 1 < len(items) else start + timedelta(hours=1))
        if stop <= start:
            stop += timedelta(days=1)
        programmes.append(("TV.Ružinov.cz", start, stop, title))

    print(f"[ruzinov] najdenych {len(programmes)} programov (over si spravnost!)")
    return programmes


# ---------------------------------------------------------------------------
# XMLTV vystup
# ---------------------------------------------------------------------------
CHANNEL_DISPLAY_NAMES = {
    "LifeTV.sk": "LifeTv",
    "TV.Považie.cz": "TV Považie",
    "RTGINT.ru": "RTG int.",
    "Mestská.televízia.Ružomberok.cz": "MTR",
    "TV.Ružinov.cz": "TV Ružinov",
    "TVDOKTOR.sk": "TV Doktor",
}


def build_xmltv(all_programmes):
    root = ET.Element("tv")
    seen_channels = set()
    for cid in CHANNEL_DISPLAY_NAMES:
        if any(p[0] == cid for p in all_programmes) and cid not in seen_channels:
            seen_channels.add(cid)
            ch = ET.SubElement(root, "channel", {"id": cid})
            dn = ET.SubElement(ch, "display-name")
            dn.text = CHANNEL_DISPLAY_NAMES[cid]

    for cid, start, stop, title in all_programmes:
        prog = ET.SubElement(root, "programme", {
            "start": fmt(start),
            "stop": fmt(stop),
            "channel": cid,
        })
        title_el = ET.SubElement(prog, "title", {"lang": "sk"})
        title_el.text = title

    return root


def main():
    all_programmes = []
    all_programmes += scrape_lifetv()
    all_programmes += scrape_povazie()
    all_programmes += scrape_rtg_int()
    all_programmes += scrape_mtr()
    all_programmes += scrape_tvdoktor()
    all_programmes += scrape_ruzinov()  # best-effort, over si vystup

    root = build_xmltv(all_programmes)
    output = io.BytesIO()
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    with gzip.open(OUTPUT_GZ, "wb") as f_out:
        f_out.write(output.getvalue())
    print(f"Uložené: {OUTPUT_GZ} ({len(all_programmes)} programov spolu)")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# VYNECHANE / NEMOZNE:
#
# - Plzeň TV (pecka.tv): stránka sama priznáva "EPG: Ne" a navyše program
#   je JS-rendered. EPG pre tento kanál reálne nikde neexistuje.
#
# - Polar2 TV (polar.cz/polar2): program sa nahráva cez JavaScript/AJAX,
#   statické HTML obsahuje len "Načítám...". Dalo by sa to riešiť cez
#   Playwright (rovnaký nástroj, čo už používaš v dotaznik_bot), ale to
#   znamená spúšťať headless browser v GitHub Actions - výrazne ťažšie
#   a pomalšie ako čisté requests scrapovanie. Daj vedieť, či to chceš
#   riešiť aj takto.
#
# - TV Liptov (tvprogram.idnes.cz/tv-liptov): stránka blokuje môj fetch
#   úplne (pravdepodobne anti-bot/Cloudflare). Skús vlastný requests
#   scraper priamo z GitHub Actions - možno tam prejde, keďže IP rozsahy
#   GitHubu nemusia byť blokované rovnako ako môj nástroj. Ak nie,
#   pravdepodobne treba Playwright aj tu.
# ---------------------------------------------------------------------------
