#!/usr/bin/env python3
"""
Stiahne SK1 a CZ1 XMLTV (.xml.gz) z epgshare01.online,
zlúči ich do jedného <tv> koreňa a uloží ako epg.xml + epg.xml.gz.
"""
import gzip
import shutil
import xml.etree.ElementTree as ET
import requests

SOURCES = [
    "https://epgshare01.online/epgshare01/epg_ripper_SK1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CZ1.xml.gz",
]

OUTPUT_XML = "epg.xml"
OUTPUT_GZ = "epg.xml.gz"


def download_and_parse(url: str) -> ET.Element:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    xml_bytes = gzip.decompress(resp.content)
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

    tree = ET.ElementTree(merged_root)
    tree.write(OUTPUT_XML, encoding="utf-8", xml_declaration=True)
    print(f"Uložené: {OUTPUT_XML} ({len(channel_elements)} kanálov, {len(programme_elements)} programov)")

    with open(OUTPUT_XML, "rb") as f_in, gzip.open(OUTPUT_GZ, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    print(f"Uložené: {OUTPUT_GZ}")


if __name__ == "__main__":
    merge()
