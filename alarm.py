#!/usr/bin/env python3
"""
Galatasaray Bilet Alarmı
========================
Galatasaray'ın resmi sitesini ve Google Haberler'i kontrol eder.
Sadece Galatasaray'ın KENDİ STADINDA (RAMS Park) oynayacağı
Şampiyonlar Ligi maçlarının bilet satış duyurularını Telegram'dan bildirir.
Deplasman maçları, basketbol/voleybol ve gazi-engelli listeleri bildirilmez.
Bilet SATIN ALMAZ; sadece haber verir, bileti sen alırsın.

Kullanım:
    python alarm.py           # kontrol et, yeni duyuru varsa bildir
    python alarm.py --test    # Telegram'a deneme mesajı gönder
    python alarm.py --list    # şu an bulunan duyuruları göster (bildirim yok)

Sadece Python'un standart kütüphanesini kullanır (kurulum gerekmez).
"""

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
SEEN_PATH = BASE / "seen.json"
UA = {"User-Agent": "Mozilla/5.0 (bilet-alarm; +https://github.com)"}

GS_URL = "https://www.galatasaray.org/anasayfa"
NEWS_QUERIES = [
    "Galatasaray Şampiyonlar Ligi bilet satışa",
    "Galatasaray maç biletleri satışta",
    "site:galatasaray.org bilet",
]


# Resmi Şampiyonlar Ligi fikstür haberi. Eleme turları için yeni fikstür
# yayınlanırsa bu listeye eklenebilir.
FIXTURE_URLS = [
    "https://www.galatasaray.org/haber/futbol/sampiyonlar-ligi/sampiyonlar-ligi-fiksturumuz/60836",
]

EXCLUDE = ("gazi", "engelli", "deplasman", "misafir", "daikin", "çağdaş", "mct",
           "voleybol", "basketbol", "kadın", "sultanlar", "euroleague", "eurocup")


def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="ignore")


def home_opponents():
    """Resmi fikstürden 'Galatasaray - X' satırlarındaki rakipleri (iç saha) alır."""
    opps = set()
    for url in FIXTURE_URLS:
        try:
            text = html.unescape(re.sub(r"<[^>]+>", "\n", get(url))).replace("\xa0", " ")
        except Exception as e:
            print("Uyarı: fikstür okunamadı:", e)
            continue
        for opp in re.findall(r"Galatasaray\s*[-–]\s*([A-Za-zÇĞİÖŞÜçğıöşü. ]+?)\s+\d", text):
            opps.add(tr_lower(opp.strip()))
    return opps


def is_home_ucl(title, opps):
    # Google Haberler başlık sonundaki " - Yayıncı" kısmını at
    t = re.sub(r"\s[-–]\s*[^-–]*$", "", tr_lower(title)) if " - " in title else tr_lower(title)
    if "bilet" not in t or any(k in t for k in EXCLUDE):
        return False
    # "X - Galatasaray" -> deplasman
    if re.search(r"\w\s*[-–]\s*galatasaray", t) and not re.search(r"galatasaray\s*[-–]", t):
        return False
    if any(o and o in t for o in opps):
        return True
    # Fikstürde olmayan (ör. eleme turu) maçlar: ŞL + iç saha işareti
    return is_ucl(title) and ("rams park" in t or bool(re.search(r"galatasaray\s*[-–]\s*\w", t)))


# ------------------------------------------------------------- kaynaklar
def from_galatasaray_org():
    """Resmi sitedeki futbol haberlerinden başlığında 'bilet' geçenler."""
    s = get(GS_URL)
    items = []
    for href, title in re.findall(
            r'href="(/haber/futbol[^"]+)"[^>]*>\s*([^<]{5,})', s):
        title = html.unescape(title).strip()
        if "bilet" in tr_lower(title):
            items.append({"id": href, "title": title,
                          "url": "https://www.galatasaray.org" + href,
                          "source": "GALATASARAY.ORG (resmi)", "official": True})
    return items


def from_google_news():
    items = []
    for q in NEWS_QUERIES:
        url = ("https://news.google.com/rss/search?hl=tr&gl=TR&ceid=TR:tr&q="
               + urllib.parse.quote(q + " when:7d"))
        s = get(url)
        for block in re.findall(r"<item>(.*?)</item>", s, re.S):
            title = html.unescape(re.search(r"<title>(.*?)</title>", block, re.S).group(1))
            link = re.search(r"<link>(.*?)</link>", block, re.S).group(1).strip()
            t = tr_lower(title)
            official = "galatasaray.org" in t
            on_sale = any(k in t for k in ("satışa çıktı", "satışta", "satışa sunuldu",
                                            "satış başladı", "satışa açıldı"))
            question = any(k in t for k in ("ne zaman", "mı?", "mi?", "çıkacak"))
            # Resmi GS duyuruları her zaman; diğer siteler sadece
            # Şampiyonlar Ligi + "satışa çıktı" (soru başlıkları hariç)
            if "bilet" in t and (official or (on_sale and not question)):
                items.append({"id": "gn:" + re.sub(r"\W+", "", t)[:120],
                              "title": title, "url": link,
                              "source": "GALATASARAY.ORG (resmi)" if official else "Google Haberler",
                              "official": official})
    return items


# ------------------------------------------------------------- telegram
def notify(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("(Telegram ayarlı değil, mesaj yazdırılıyor)\n" + text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                           data=data, timeout=15)


def is_ucl(title):
    t = tr_lower(title)
    return any(k in t for k in ("şampiyonlar ligi", "uefa", "champions"))


def fmt(item):
    tag = "⭐ ŞAMPİYONLAR LİGİ · RAMS Park\n"
    return f"{tag}{item['title']}\nKaynak: {item['source']}\n{item['url']}"


# ------------------------------------------------------------- main
def collect():
    items, errors = [], []
    for fn in (from_galatasaray_org, from_google_news):
        try:
            items += fn()
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
    opps = home_opponents()
    if not opps:
        errors.append("iç saha rakipleri bulunamadı, sadece başlık kuralları kullanılıyor")
    items = [it for it in items if is_home_ucl(it["title"], opps)]
    uniq = {}
    for it in items:
        uniq.setdefault(it["id"], it)
    return list(uniq.values()), errors


def main():
    args = sys.argv[1:]
    if "--test" in args:
        notify("🔔 Bilet alarmı kuruldu! Galatasaray bilet satış duyurusu "
               "çıktığında buraya mesaj gelecek. ⚽")
        return
    items, errors = collect()
    for e in errors:
        print("Uyarı:", e)
    if "--list" in args:
        for it in items:
            print("-", fmt(it).replace("\n", " | "))
        return

    first_run = not SEEN_PATH.exists()
    seen = set(json.loads(SEEN_PATH.read_text())) if not first_run else set()
    new = [it for it in items if it["id"] not in seen]

    if first_run:
        print(f"İlk çalışma: {len(items)} mevcut duyuru kaydedildi, bildirim gönderilmedi.")
    elif new:
        new.sort(key=lambda x: not x["official"])  # resmi duyurular önce
        unofficial = [it for it in new if not it["official"]]
        new = [it for it in new if it["official"]] + unofficial[:2]
        for it in new:
            notify("🎟️ BİLET DUYURUSU!\n\n" + fmt(it) +
                   "\n\nPasso: https://www.passo.com.tr")
            print("Bildirildi:", it["title"])
    else:
        print("Yeni duyuru yok.")

    seen |= {it["id"] for it in items}
    SEEN_PATH.write_text(json.dumps(sorted(seen)[-2000:], ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main()
