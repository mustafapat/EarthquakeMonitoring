# main.py

import time
from datetime import datetime, timedelta, timezone # timezone eklendi
import requests.exceptions
import sqlite3
import logging
from typing import Dict, Any, Optional, Tuple
import sys # Hata durumunda çıkış için

# --- Yapılandırma Değişkenleri ---
# Dosya ve API Ayarları
DB_FILE = "deprem.db"
USER_AGENT = "DepremIzlemeBot/1.1 (Python; https://github.com/kullanici/proje)" # Kendi bilgilerinizi ekleyin
# Veri Çekme Ayarları
FETCH_INTERVAL_SECONDS = 30
FETCH_TIME_WINDOW_HOURS = 2
EARTHQUAKE_API_TIMEOUT = 60 # core.py'deki ile aynı olmalı
LOCATION_API_TIMEOUT = 30   # core.py'deki ile aynı olmalı
LOCATION_API_RATE_LIMIT_DELAY = 1.1 # core.py'deki ile aynı olmalı
# Bölgesel Filtreleme
MIN_LAT = 35.0
MAX_LAT = 43.0
MIN_LON = 25.0
MAX_LON = 45.0
MIN_MAG = 2.0 # Sadece belirli büyüklük üzerindekileri almak için (0.0 hepsi demek)
# Başlangıç Özeti Ayarları
INITIAL_SUMMARY_HOURS = 24
# Diğer Ayarlar
LOGGING_LEVEL = logging.INFO # DEBUG, INFO, WARNING, ERROR, CRITICAL
NOMINATIM_ZOOM = 10         # Konum adı detay seviyesi

# Konfigürasyonu core modülüne aktarmak için sözlük yapısı
CORE_CONFIG = {
    "EARTHQUAKE_API_TIMEOUT": EARTHQUAKE_API_TIMEOUT,
    "LOCATION_API_TIMEOUT": LOCATION_API_TIMEOUT,
    "LOCATION_API_RATE_LIMIT_DELAY": LOCATION_API_RATE_LIMIT_DELAY,
    "USER_AGENT": USER_AGENT,
    "NOMINATIM_ZOOM": NOMINATIM_ZOOM,
    "MIN_LAT": MIN_LAT,
    "MAX_LAT": MAX_LAT,
    "MIN_LON": MIN_LON,
    "MAX_LON": MAX_LON,
    "MIN_MAG": MIN_MAG
}

# --- Gerekli Modülleri Import Et ---
try:
    from data import init_db, earthquake_exists, save_earthquake
    from core import fetch_earthquakes, get_location_name
except ImportError as e:
     logging.critical(f"HATA: Gerekli modüller (data.py, core.py) bulunamadı: {e}")
     sys.exit(f"HATA: data.py veya core.py bulunamadı. Dosyaların main.py ile aynı dizinde olduğundan emin olun.")

# --- Saat Dilimi Ayarı ---
try:
    from zoneinfo import ZoneInfo
    TURKEY_TZ = ZoneInfo("Europe/Istanbul")
    logging.info("Saat dilimi için 'zoneinfo' kullanılıyor.")
except ImportError:
    try:
        from pytz import timezone as ZoneInfo
        TURKEY_TZ = ZoneInfo("Europe/Istanbul")
        logging.info("Saat dilimi için alternatif 'pytz' kullanılıyor.")
    except ImportError:
        logging.warning("'zoneinfo' veya 'pytz' bulunamadı. Saat dilimi dönüşümü yapılamayacak.")
        TURKEY_TZ = None

# --- Logging Ayarları ---
logging.basicConfig(level=LOGGING_LEVEL, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# --- Yardımcı Fonksiyonlar ---

def parse_event_time(time_str: Optional[str]) -> Optional[datetime]:
    """Gelen ISO zaman metnini UTC aware datetime nesnesine çevirir."""
    if not isinstance(time_str, str) or not time_str:
        return None

    parse_str = None
    original_str_for_parsing = time_str
    try:
        if original_str_for_parsing.endswith('Z'):
            original_str_for_parsing = original_str_for_parsing[:-1]

        fmt = '%Y-%m-%dT%H:%M:%S'
        if '.' in original_str_for_parsing:
            parts = original_str_for_parsing.split('.')
            if len(parts) == 2:
                ms = parts[1][:6] # En fazla 6 hane mikrosaniye
                original_str_for_parsing = f"{parts[0]}.{ms}"
                fmt += '.%f'
            else: # Garip format, sadece saniyeye kadar al
                 original_str_for_parsing = parts[0]
        else: # Mikrosaniye yok
             pass # fmt zaten doğru

        # strptime ile naive datetime oluştur
        dt_naive = datetime.strptime(original_str_for_parsing, fmt)
        # timezone.utc ekleyerek aware hale getir
        return dt_naive.replace(tzinfo=timezone.utc)

    except ValueError as ve:
        logging.error(f"Zaman ayrıştırma hatası (strptime - ValueError)! Orijinal: '{time_str}'. Format: '{fmt}'. Denenen: '{original_str_for_parsing}'. Hata: {ve}")
        return None
    except Exception as e:
        logging.exception(f"Zaman ayrıştırma sırasında beklenmedik hata (Orijinal: {time_str}): {e}")
        return None

def format_local_time(dt_utc: Optional[datetime], target_tz: Optional[ZoneInfo]) -> str:
    """UTC datetime nesnesini yerel saate çevirir ve formatlar."""
    if dt_utc is None:
        return "Zaman Bilgisi Yok"
    if target_tz is None:
        # Timezone bilgisi yoksa UTC ve offset göster
        return dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC%z') # %z +0000 ekler

    try:
        dt_local = dt_utc.astimezone(target_tz)
        # Zaman dilimi adı (örn: TRT) ve offset'i ekle (%Z%z)
        return dt_local.strftime('%Y-%m-%d %H:%M:%S %Z%z')
    except Exception as e:
        logging.error(f"Yerel saate dönüştürme hatası: {e}")
        return dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC%z') + " (Dönüşüm Hatası)"


def calculate_delay(event_time_utc: Optional[datetime], record_time_utc: datetime) -> Optional[float]:
    """İki UTC aware datetime arasındaki farkı dakika cinsinden hesaplar."""
    if event_time_utc is None:
        return None
    try:
        # İkisi de aware olmalı (record_time_utc'yi aware yaptık)
        time_diff = record_time_utc - event_time_utc
        diff_minutes = time_diff.total_seconds() / 60
        # Çok eski olaylar için negatif çıkabilir, bunu sıfır yapalım
        return max(0, round(diff_minutes, 1))
    except Exception as e:
        logging.warning(f"Gecikme süresi hesaplanırken hata oluştu: {e}")
        return None


def print_earthquake_details(data: Dict[str, Any], local_time_str: str, delay_minutes: Optional[float]):
    """Yeni deprem detaylarını formatlı bir şekilde yazdırır."""
    # Daha temiz bir çıktı için hizalama
    print("\n" + "=" * 20 + " YENİ DEPREM KAYDI " + "=" * 20)
    print(f"  {'Büyüklük':<15}: {mag_str}")
    print(f"  {'Konum':<15}: {data.get('location', 'Bilinmiyor')}")
    print(f"  {'Koordinat':<15}: {data.get('lat', '?')}, {data.get('lon', '?')}")
    print(f"  {'Oluş Zamanı':<15}: {local_time_str}")
    if delay_minutes is not None:
        print(f"  {'Gecikme Süresi':<15}: {delay_minutes} dakika")
    else:
        print(f"  {'Gecikme Süresi':<15}: Hesaplanamadı")

    mag_str = f"M{data.get('mag', '?'):.1f}" if data.get('mag') is not None else "M?"
    depth_str = f"{data.get('depth', '?'):.1f} km" if data.get('depth') is not None else "? km"

    print(f"  {'Derinlik':<15}: {depth_str}")    
    print(f"  {'Bölge (EMSC)':<15}: {data.get('region', 'Bilinmiyor')}")
    print("=" * 58 + "\n")


def process_new_earthquake(item: Dict[str, Any], config: Dict[str, Any], target_tz: Optional[ZoneInfo]) -> bool:
    """API'den gelen tek bir deprem olayını işler."""
    processed_new = False
    unid = None # Hata durumunda loglama için
    try:
        props = item.get("properties", {})
        unid = props.get("unid")

        if not unid:
            logging.warning(f"Atlanıyor: 'unid' bulunamayan kayıt: {item.get('id','ID Yok')}")
            return False

        # Veritabanında bu deprem zaten var mı?
        if earthquake_exists(unid):
            # logging.debug(f"Zaten mevcut: {unid}") # Gerekirse açılabilir
            return False

        # --- Bu deprem yeni, işlemeye devam et ---
        logging.info(f"Yeni deprem bulundu, işleniyor: {unid}")

        event_time_str = props.get("time")
        lat = props.get("lat")
        lon = props.get("lon")

        # Kayıt anını al (timezone aware - UTC) - DÜZELTME
        record_time_utc = datetime.now(timezone.utc)

        # Zamanı ayrıştır ve formatla
        event_time_utc = parse_event_time(event_time_str)
        local_time_str = format_local_time(event_time_utc, target_tz)

        # Gecikmeyi hesapla
        delay_minutes = calculate_delay(event_time_utc, record_time_utc)

        # Konum adını al (önbellek veya API)
        if lat is not None and lon is not None:
            location_name = get_location_name(lat, lon, config)
        else:
            logging.warning(f"Konum bilgisi eksik ({unid}), konum adı alınamıyor.")
            location_name = "Konum Bilgisi Eksik"

        # Veritabanına kaydedilecek veriyi hazırla
        data_to_save = {
            "unid": unid,
            "time": event_time_str, # Orijinal UTC zamanını kaydet
            "lat": lat,
            "lon": lon,
            "depth": props.get("depth"),
            "mag": props.get("mag"),
            "region": props.get("flynn_region", "Bilinmeyen Bölge"),
            "location": location_name
        }

        # Veritabanına kaydet
        save_earthquake(data_to_save)

        # Detayları yazdır
        print_earthquake_details(data_to_save, local_time_str, delay_minutes)
        processed_new = True

    except Exception as e:
        logging.exception(f"Kayıt işlenirken hata oluştu (ID: {unid if unid else 'Bilinmiyor'}): {e}")
        logging.error(f"İşlenen Kayıt Verisi: {item}")

    return processed_new


def print_initial_summary(hours: int, target_tz: Optional[ZoneInfo]):
    """Başlangıçta veritabanındaki son depremleri yazdırır."""
    logging.info(f"Başlangıç özeti için veritabanı okunuyor (Son {hours} saat)...")
    end_time_utc = datetime.now(timezone.utc)
    start_time_utc = end_time_utc - timedelta(hours=hours)
    try:
        with sqlite3.connect(DB_FILE) as con:
            # Veriye daha kolay erişim için dictionary olarak alalım
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("""
            SELECT time, mag, location, rectime FROM earthquakes
            WHERE time >= ?
            ORDER BY time DESC
            """, (start_time_utc.isoformat(timespec='seconds'),)) # Sadece saniye hassasiyetinde karşılaştır
            kayıtlar = cur.fetchall()
    except sqlite3.Error as e:
        logging.error(f"Başlangıç özeti alınırken veritabanı okuma hatası: {e}")
        kayıtlar = []

    # Özeti yazdırma kısmı
    local_now_str = datetime.now(target_tz).strftime('%Y-%m-%d %H:%M:%S %Z') if target_tz else datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    print(f"\n--- Son {hours} Saatin Özeti ({local_now_str}) ---")
    print(f"Veritabanında {len(kayıtlar)} deprem kaydı bulundu.")
    print("-" * 80)
    if kayıtlar:
        # Başlıkları yazdır
        print(f"{'Oluş Zamanı (Yerel)':<32} | {'Kayıt (UTC)':<28} | {'Büy.':<6} | Konum")
        print("-" * 80)
        for row in kayıtlar:
            mag_str = f"M{row['mag']:.1f}" if row['mag'] is not None else "M?"
            loc_str = row['location'] if row['location'] else "Bilinmeyen"
            rec_time_str = row['rectime'] if row['rectime'] else "Yok"
            # Zamanı ayrıştır ve yerel saate çevir
            event_time_utc = parse_event_time(row['time'])
            event_time_local_str = format_local_time(event_time_utc, target_tz)

            # Çıktıyı hizala
            print(f"{event_time_local_str:<32} | {rec_time_str:<28} | {mag_str:<6} | {loc_str}")
    else:
        print("Veritabanında gösterilecek kayıt bulunamadı.")
    print("-" * 80)


# --- Ana Program ---

def main():
    """Ana program fonksiyonu."""
    try:
        init_db()
        print_initial_summary(INITIAL_SUMMARY_HOURS, TURKEY_TZ)

        print("\n--- Yeni Depremler İçin İzleme Başlatıldı ---")
        logging.info(f"İzleme başlatıldı. Kontrol aralığı: {FETCH_INTERVAL_SECONDS} sn, Zaman penceresi: {FETCH_TIME_WINDOW_HOURS} saat.")

        while True:
            any_new_processed = False # Bu döngüde yeni deprem işlendi mi?
            try:
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(hours=FETCH_TIME_WINDOW_HOURS)

                records = fetch_earthquakes(start_time, end_time, CORE_CONFIG)

                if records:
                    for item in records:
                        processed = process_new_earthquake(item, CORE_CONFIG, TURKEY_TZ)
                        if processed:
                            any_new_processed = True
                else:
                    logging.info("Bu periyotta işlenecek olay alınamadı/bulunamadı.")

                if not any_new_processed:
                    logging.info("Kontrol tamamlandı, yeni deprem işlenmedi.")

            except requests.exceptions.Timeout:
                logging.warning("Veri çekme zaman aşımına uğradı. Sonraki kontrolde tekrar denenecek.")
                # İsteğe bağlı: Timeout sonrası daha uzun bekleme
                # time.sleep(FETCH_INTERVAL_SECONDS)
            except requests.exceptions.RequestException as e:
                 logging.warning(f"Ağ hatası nedeniyle veri çekilemedi ({e}). Sonraki kontrolde tekrar denenecek.")
            except Exception as loop_error:
                logging.exception(f"Ana döngüde beklenmedik hata: {loop_error}")
                # Beklenmedik hata sonrası biraz bekle
                time.sleep(FETCH_INTERVAL_SECONDS)

            logging.info(f"Sonraki kontrol için {FETCH_INTERVAL_SECONDS} saniye bekleniyor...")
            time.sleep(FETCH_INTERVAL_SECONDS)

    except KeyboardInterrupt:
         print("\nProgram kullanıcı tarafından sonlandırıldı.")
         logging.info("Program kullanıcı tarafından (KeyboardInterrupt) sonlandırıldı.")
    except Exception as critical_error:
        logging.exception(f"Program kritik bir hata nedeniyle durdu: {critical_error}")
        print(f"\n!!! PROGRAM KRİTİK HATA NEDENİYLE DURDU: {critical_error} !!!")
        sys.exit(1) # Hata kodu ile çıkış

if __name__ == "__main__":
    main()