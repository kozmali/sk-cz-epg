#!/usr/bin/env python3
"""
Stiahne SK1 a CZ1 XMLTV (.xml.gz) z epgshare01.online
+ SK a CZ EPG z iptv-org (synchronizované s iptv-org M3U playlistmi),
+ lokálny fallback_epg.py výstup pre kanály bez vlastného EPG zdroja,
zlúči ich do jedného <tv> koreňa a uloží ako epg.xml.gz.
"""
import gzip
import io
import os
import subprocess
import xml.etree.ElementTree as ET

import requests

SOURCES = [
    "https://epgshare01.online/epgshare01/epg_ripper_SK1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CZ1.xml.gz",
    "https://iptv-epg.org/files/epg-cz.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Slovakia/slovakia1.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Slovakia/slovakia2.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Czech/czech1.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Czech/czech2.xml",
]

OUTPUT_GZ = "epg.xml.gz"
FALLBACK_SCRIPT = "fallback_epg.py"
FALLBACK_GZ = "epg_fallback.xml.gz"


def download_and_parse(url: str) -> ET.Element:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    # epgshare01 súbory sú gzip, iptv-org sú plain XML
    if url.endswith(".gz"):
        xml_bytes = gzip.decompress(resp.content)
    else:
        xml_bytes = resp.content
    return ET.fromstring(xml_bytes)


def parse_local_gz(path: str) -> ET.Element:
    with gzip.open(path, "rb") as f:
        xml_bytes = f.read()
    return ET.fromstring(xml_bytes)


def run_fallback_scraper() -> None:
    """Spusti fallback_epg.py (lokálne scrapery pre LifeTv, TV Považie,
    RTG int., MTR, TV Ružinov, TV Doktor), ktoré vyrobí epg_fallback.xml.gz.
    Ak skript zlyhá alebo subor neexistuje, jednoducho sa preskoci -
    hlavny merge nesmie spadnut len preto, ze jeden z malych zdrojov
    je momentalne nedostupny."""
    if not os.path.exists(FALLBACK_SCRIPT):
        print(f"[fallback] {FALLBACK_SCRIPT} sa v repozitari nenasiel, preskakujem")
        return
    try:
        print(f"[fallback] spustam {FALLBACK_SCRIPT}...")
        subprocess.run(["python", FALLBACK_SCRIPT], check=True)
    except Exception as e:
        print(f"[fallback] chyba pri behu {FALLBACK_SCRIPT}: {e}")


def merge() -> None:
    merged_root = ET.Element("tv")

    seen_channel_ids = set()
    channel_elements = []
    programme_elements = []

    for url in SOURCES:
        print(f"Sťahujem: {url}")
        try:
            root = download_and_parse(url)
        except Exception as e:
            print(f"  zlyhalo ({e}), preskakujem tento zdroj")
            continue

        # zachovaj atribúty koreňa z prvého zdroja (generator-info-name a pod.)
        if not merged_root.attrib:
            merged_root.attrib.update(root.attrib)

        for child in root:
            if child.tag == "channel":
                cid = child.attrib.get("id")
                if cid in seen_channel_ids:
                    continue  # ochrana proti duplicitným kanálom
                seen_channel_ids.add(cid)
                channel_elements.append(child)
            elif child.tag == "programme":
                programme_elements.append(child)

    # --- lokálny fallback zdroj (LifeTv, TV Považie, RTG int., MTR, TV Ružinov, TV Doktor) ---
    run_fallback_scraper()
    if os.path.exists(FALLBACK_GZ):
        print(f"Pripájam lokálny fallback zdroj: {FALLBACK_GZ}")
        try:
            fallback_root = parse_local_gz(FALLBACK_GZ)
            for child in fallback_root:
                if child.tag == "channel":
                    cid = child.attrib.get("id")
                    if cid in seen_channel_ids:
                        continue
                    seen_channel_ids.add(cid)
                    channel_elements.append(child)
                elif child.tag == "programme":
                    programme_elements.append(child)
        except Exception as e:
            print(f"  zlyhalo parsovanie fallback zdroja ({e}), preskakujem")
    else:
        print(f"[fallback] {FALLBACK_GZ} neexistuje, preskakujem")

    # XMLTV konvencia: všetky <channel> elementy najprv, potom <programme>
    for el in channel_elements:
        merged_root.append(el)
    for el in programme_elements:
        merged_root.append(el)

    # Zapis priamo do .gz bez medzisúboru epg.xml (vyhneme sa veľkým súborom v repe)
    output = io.BytesIO()
    tree = ET.ElementTree(merged_root)
    tree.write(output, encoding="utf-8", xml_declaration=True)

    with gzip.open(OUTPUT_GZ, "wb") as f_out:
        f_out.write(output.getvalue())

    print(f"Uložené: {OUTPUT_GZ} ({len(channel_elements)} kanálov, {len(programme_elements)} programov)")


if __name__ == "__main__":
    merge()
