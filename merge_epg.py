#!/usr/bin/env python3
"""
Stiahne SK1 a CZ1 XMLTV (.xml.gz) z epgshare01.online
+ SK a CZ EPG z iptv-org (synchronizované s iptv-org M3U playlistmi),
zlúči ich do jedného <tv> koreňa a uloží ako epg.xml.gz.
"""
import gzip
import io
import xml.etree.ElementTree as ET
import requests

SOURCES = [
    "https://epgshare01.online/epgshare01/epg_ripper_SK1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CZ1.xml.gz",
    "https://iptv-org.github.io/epg/guides/sk.epg.xml",
    "https://iptv-org.github.io/epg/guides/cz.epg.xml",
]

OUTPUT_GZ = "epg.xml.gz"


def download_and_parse(url: str) -> ET.Element:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    # epgshare01 súbory sú gzip, iptv-org sú plain XML
    if url.endswith(".gz"):
        xml_bytes = gzip.decompress(resp.content)
    else:
        xml_bytes = resp.content
    return ET.fromstring(xml_bytes)


def merge() -> None:
    merged_root = ET.Element("tv")

    seen_channel_ids = set()
    channel_elements = []
    programme_elements = []

    for url in SOURCES:
        print(f"Sťahujem: {url}")
        root = download_and_parse(url)

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
