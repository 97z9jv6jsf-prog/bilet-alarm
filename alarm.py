#!/usr/bin/env python3
"""
Galatasaray Bilet Alarmı
========================
Galatasaray'ın KENDİ STADINDA (RAMS Park / Ali Sami Yen) oynayacağı
UEFA Şampiyonlar Ligi maçlarının biletleri SATIŞA ÇIKTIĞI ANDA
Telegram'dan bildirim gönderir.

Nasıl çalışır:
  1. galatasaray.org'daki yeni haberleri numarasına göre tek tek kontrol eder.
  2. Şampiyonlar Ligi + iç saha bilet duyurusu bulunca metindeki satış
     tarih/saatlerini okur (ör. "31 Ekim Cumartesi günü saat 11.00'de GS logolu
     Passo Taraftar Kartlarına ...").
  3. Her satış aşaması için:
        - satış takvimi açıklandığında bilgi mesajı
        - satıştan 30 dakika önce hatırlatma
        - satışın başladığı dakikada "SATIŞ BAŞLADI" mesajı
  4. Yedek olarak Google Haberler'de "biletleri satışa çıktı" haberlerini izler.

Bilet SATIN ALMAZ; sadece haber verir.

Kullanım:
    python alarm.py            # kontrol et, zamanı gelen bildirimleri gönder
    python alarm.py --test     # Telegram'a deneme mesajı
    python alarm.py --status   # bekleyen satış aşamalarını göster

Sadece Python standart kütüphanesi kullanılır.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
STATE_PATH = BASE / "state.json"
TR = ZoneInfo("Europe/Istanbul")
UA = {"User-Agent": "Mozilla/5.0 (bilet-alarm)"}

ARTICLE_URL = "https://www.galatasaray.org/haber/x/x/{}"
START_ID = 61340          # ilk kurulumda taramaya buradan başlanır
SCAN_AHEAD = 40           # her çalışmada en fazla bu kadar yeni numara denenir
MAX_MISSES = 12           # art arda bu kadar boş numara görülürse durulur
REMIND_BEFORE = timedelta(minutes=30)
WAIT_WINDOW = timedelta(minutes=11)   # satış bu süre içindeyse tam dakikasını bekle

MONTHS = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
          "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12}
EXCLUDE = ("gazi", "engelli", "deplasman", "misafir")


# ------------------------------------------------------------- yardımcılar
def now_tr():
    return datetime.now(TR)


def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="ignore")


def page_text(raw):
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", raw)).replace("\xa0", " ")
    return re.sub(r"[ \t]*\n\s*", "\n", t)


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"last_id": START_ID, "events": [], "sent": [], "seen_news": []}


def save_state(st):
    st["sent"] = st["sent"][-500:]
    st["seen_news"] = st["seen_news"][-1000:]
    cutoff = (now_tr() - timedelta(days=30)).isoformat()
    st["events"] = [e for e in st["events"] if e["when"] > cutoff]
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def notify(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("(Telegram ayarlı değil)\n" + text + "\n")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                           data=data, timeout=15)


# ------------------------------------------------------------- duyuru okuma
DT_RE = re.compile(
    r"(\d{1,2})\s+(ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık)"
    r"(?:\s+(\d{4}))?\s+\w+\s+günü\s+saat\s+(\d{1,2})[.:](\d{2})", re.I)


def parse_sales(text, ref):
    """Duyuru metninden satış aşamalarını [(datetime, açıklama)] olarak çıkarır."""
    low = tr_lower(text)
    phases = []
    for m in DT_RE.finditer(low):
        after = low[m.end():m.end() + 160]
        before = low[max(0, m.start() - 60):m.start()]
        # Maç saati cümlesini atla ("... saat 20.45'te VfB Stuttgart takımı ile ...")
        if re.search(r"takımı ile|ile karşı|karşı karşıya", after[:90]):
            continue
        if not re.search(r"bilet|satış|satın|sahipler|kartlar|passo|kombine|taraftar",
                         before + after):
            continue
        day, mon, year, hh, mm = m.groups()
        year = int(year) if year else ref.year
        dt = datetime(year, MONTHS[mon], int(day), int(hh), int(mm), tzinfo=TR)
        if not m.group(3) and dt < ref - timedelta(days=60):
            dt = dt.replace(year=year + 1)
        # Kime açık? "...’de GS logolu Passo Taraftar Kartlarına," gibi kısmı al
        who = re.sub(r"^[’'`]?\s*(?:te|ta|de|da|den|dan|ten|tan)?n?\b\s*", "",
                     text[m.end():m.end() + 160])
        who = re.split(r"[,.;\n]", who)[0].strip()
        who = re.sub(r"\s*(itibaren|satışa sunulacak|satışa çıkacak).*$", "", who).strip()
        phases.append((dt, who[:90]))
    uniq = {}
    for dt, who in phases:
        uniq.setdefault(dt, who)
    return sorted(uniq.items())


def is_home_ucl_article(title, text):
    t, b = tr_lower(title.replace("- GALATASARAY.ORG", "")), tr_lower(text)
    return ("bilet" in t and not any(k in t for k in EXCLUDE)
            and "şampiyonlar ligi" in b
            and ("ali sami yen" in b or "rams park" in b)
            and not re.search(r"\w\s+-\s+galatasaray\b", t))


def scan_galatasaray(st):
    """Yeni haber numaralarını dener, ŞL iç saha bilet duyurularını döndürür."""
    found, misses, i = [], 0, st["last_id"]
    for i in range(st["last_id"] + 1, st["last_id"] + 1 + SCAN_AHEAD):
        try:
            raw = get(ARTICLE_URL.format(i))
        except Exception:
            raw = ""
        m = re.search(r"<title>(.*?)</title>", raw, re.S)
        title = html.unescape(m.group(1)).replace("- GALATASARAY.ORG", "").strip() if m else ""
        if not title or "SAYFA BULUNAMADI" in title:
            misses += 1
            if misses >= MAX_MISSES:
                break
            continue
        misses = 0
        st["last_id"] = i
        text = page_text(raw)
        if is_home_ucl_article(title, text):
            found.append({"id": i, "title": title, "text": text,
                          "url": ARTICLE_URL.format(i).replace("/x/x/", "/futbol/bilet/")})
    return found


def google_news_fallback(st):
    """Yedek: haber sitelerinde 'Galatasaray-X biletleri satışa çıktı' (ŞL, iç saha)."""
    q = urllib.parse.quote("Galatasaray Şampiyonlar Ligi biletleri satışa çıktı when:2d")
    s = get(f"https://news.google.com/rss/search?hl=tr&gl=TR&ceid=TR:tr&q={q}")
    out = []
    for block in re.findall(r"<item>(.*?)</item>", s, re.S):
        title = html.unescape(re.search(r"<title>(.*?)</title>", block, re.S).group(1))
        link = re.search(r"<link>(.*?)</link>", block, re.S).group(1).strip()
        t = re.sub(r"\s-\s[^-]*$", "", tr_lower(title))   # " - Yayıncı" kısmını at
        if ("bilet" in t and re.search(r"galatasaray\s*[-–]\s*\w", t)
                and any(k in t for k in ("satışa çıktı", "satışta", "satışa sunuldu"))
                and not any(k in t for k in EXCLUDE + ("ne zaman", "mı?", "mi?"))):
            key = re.sub(r"\W+", "", t)[:100]
            if key not in st["seen_news"]:
                out.append((key, title, link))
    return out


# ------------------------------------------------------------- ana akış
def fmt_dt(dt):
    days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    return f"{dt:%d.%m.%Y} {days[dt.weekday()]} {dt:%H:%M}"


def process(st, now):
    # 1) Yeni resmi duyurular -> satış takvimi
    for art in scan_galatasaray(st):
        phases = parse_sales(art["text"], now)
        print(f"Duyuru bulundu: {art['title']} -> {len(phases)} satış aşaması")
        lines = []
        for dt, who in phases:
            eid = f"{art['id']}@{dt.isoformat()}"
            if not any(e["eid"] == eid for e in st["events"]):
                st["events"].append({"eid": eid, "when": dt.isoformat(), "who": who,
                                     "title": art["title"], "url": art["url"]})
            state = "🟢 ŞU AN SATIŞTA" if dt <= now else "⏳"
            lines.append(f"{state} {fmt_dt(dt)} → {who or 'satış'}")
        if not phases:
            lines.append("Satış saati metinden okunamadı, duyuruyu kontrol et.")
        notify("📢 ŞAMPİYONLAR LİGİ BİLET SATIŞ TAKVİMİ (RAMS Park)\n\n"
               f"{art['title']}\n\n" + "\n".join(lines) +
               "\n\nHer aşamadan 30 dk önce ve satış başladığı anda tekrar haber vereceğim."
               f"\n\n{art['url']}\nPasso: https://www.passo.com.tr")
        # Duyuru anında zaten başlamış aşamaları "başladı" olarak tekrar gönderme
        for dt, _ in phases:
            if dt <= now:
                st["sent"] += [f"{art['id']}@{dt.isoformat()}:remind",
                               f"{art['id']}@{dt.isoformat()}:start"]

    # 2) Zamanı gelen hatırlatma / başlangıç bildirimleri
    for e in sorted(st["events"], key=lambda x: x["when"]):
        when = datetime.fromisoformat(e["when"])
        rk, sk = e["eid"] + ":remind", e["eid"] + ":start"
        if sk in st["sent"]:
            continue
        if now >= when - REMIND_BEFORE and now < when and rk not in st["sent"]:
            mins = int((when - now).total_seconds() // 60) + 1
            notify(f"⏰ {mins} DAKİKA SONRA BİLET SATIŞI BAŞLIYOR!\n\n{e['title']}\n"
                   f"{fmt_dt(when)} → {e['who']}\n\nPasso'ya şimdiden giriş yap: "
                   f"https://www.passo.com.tr")
            st["sent"].append(rk)
        if now < when <= now + WAIT_WINDOW:
            wait = (when - now_tr()).total_seconds()
            if wait > 0:
                print(f"Satışa {wait:.0f} sn var, bekleniyor...")
                save_state(st)
                time.sleep(wait)
            now = now_tr()
        if now >= when:
            late = int((now - when).total_seconds() // 60)
            extra = f" ({late} dk önce başladı)" if late >= 2 else ""
            notify(f"🎟️ BİLET SATIŞI BAŞLADI!{extra}\n\n{e['title']}\n"
                   f"{e['who']}\n\nHemen Passo'ya gir: https://www.passo.com.tr\n{e['url']}")
            st["sent"] += [rk, sk]

    # 3) Yedek kaynak
    try:
        for key, title, link in google_news_fallback(st):
            notify(f"🎟️ BİLETLER SATIŞTA (haber kaynağı)\n\n{title}\n{link}\n\n"
                   "Passo: https://www.passo.com.tr")
            st["seen_news"].append(key)
    except Exception as e:
        print("Uyarı: Google Haberler okunamadı:", e)


def main():
    args = sys.argv[1:]
    if "--test" in args:
        notify("🔔 Bilet alarmı güncellendi! RAMS Park'taki Şampiyonlar Ligi maçlarının "
               "biletleri satışa çıktığı anda (ve 30 dk önce) buraya mesaj gelecek. ⚽")
        return
    first_run = not STATE_PATH.exists()
    st = load_state()
    if "--status" in args:
        print(f"Son taranan haber no: {st['last_id']}")
        for e in st["events"]:
            print(f"- {fmt_dt(datetime.fromisoformat(e['when']))} | {e['title']} | {e['who']}")
        return
    if first_run:
        # Eski haberleri ve eski Google haberlerini bildirmeden işaretle
        scan_galatasaray(st)
        try:
            st["seen_news"] += [k for k, _, _ in google_news_fallback(st)]
        except Exception:
            pass
        print(f"İlk kurulum: haber no {st['last_id']} itibarıyla izleniyor.")
    else:
        process(st, now_tr())
    save_state(st)
    print(f"Tamam. Son haber no: {st['last_id']}, bekleyen aşama: "
          f"{sum(1 for e in st['events'] if e['eid'] + ':start' not in st['sent'])}")


if __name__ == "__main__":
    main()
