# core.py

import requests
from datetime import datetime
import time
from requests.exceptions import RequestException, JSONDecodeError, Timeout
import logging

# data modülünden fonksiyonları import et (aynı dizinde olduğunu varsayıyoruz)
try:
    from data import get_location_from_cache, save_location_to_cache
except ImportError:
    logging.critical("HATA: data.py modülü bulunamadı veya import edilemedi!")
    raise # Programın devam etmesi anlamsız

# API Zaman Aşımı Süreleri ve Gecikme (saniye)
EARTHQUAKE_API_TIMEOUT = 60
LOCATION_API_TIMEOUT = 30
LOCATION_API_RATE_LIMIT_DELAY = 1.1 # OpenStreetMap kullanım politikası için biraz daha güvenli bekleme

def fetch_earthquakes(start: datetime, end: datetime, config: dict):
    """Belirtilen zaman aralığı ve konfigürasyon için EMSC FDSNWS'den deprem verilerini çeker."""
    url = "https://www.seismicportal.eu/fdsnws/event/1/query"
    params = {
        "starttime": start.isoformat(),
        "endtime": end.isoformat(),
        "minlat": config.get("MIN_LAT", 35),
        "maxlat": config.get("MAX_LAT", 43),
        "minlon": config.get("MIN_LON", 25),
        "maxlon": config.get("MAX_LON", 45),
        "minmag": config.get("MIN_MAG", 0.0),
        "format": "json"
    }
    logging.info(f"EMSC API'den veri çekiliyor: {start.isoformat()} - {end.isoformat()}")
    try:
        response = requests.get(url, params=params, timeout=config.get("EARTHQUAKE_API_TIMEOUT", EARTHQUAKE_API_TIMEOUT))
        response.raise_for_status() # HTTP 4xx veya 5xx hatalarını yakala
        try:
            data = response.json()
            features = data.get("features", [])
            logging.info(f"EMSC API'den {len(features)} olay başarıyla alındı.")
            return features
        except JSONDecodeError as e:
            logging.error(f"EMSC API JSON parse etme hatası: {e}. Yanıt: {response.text[:200]}...")
            return []

    except Timeout:
        timeout_duration = config.get("EARTHQUAKE_API_TIMEOUT", EARTHQUAKE_API_TIMEOUT)
        logging.warning(f"EMSC API isteği zaman aşımına uğradı (timeout={timeout_duration}s).")
        raise # Timeout hatasını main.py'nin yakalaması için tekrar yükselt
    except RequestException as e:
        logging.error(f"EMSC API ağ hatası: {e}")
        raise # Diğer ağ hatalarını main.py'nin yakalaması için tekrar yükselt
    except Exception as e:
        logging.exception(f"fetch_earthquakes içinde beklenmedik hata: {e}")
        raise


def get_location_name(lat: float, lon: float, config: dict) -> str:
    """Verilen koordinatlar için OpenStreetMap Nominatim'den konum adını alır."""
    cached = get_location_from_cache(lat, lon)
    if cached:
        logging.debug(f"Konum önbellekten bulundu: ({lat}, {lon}) -> {cached}")
        return cached

    logging.info(f"Nominatim API'den konum adı alınıyor: ({lat}, {lon})")
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "format": "json",
        "lat": lat,
        "lon": lon,
        "zoom": config.get("NOMINATIM_ZOOM", 10),
        "addressdetails": 1
    }
    headers = {"User-Agent": config.get("USER_AGENT", "DepremIzlemeBot/1.0")}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=config.get("LOCATION_API_TIMEOUT", LOCATION_API_TIMEOUT))
        response.raise_for_status()
        data = response.json()
        display_name = data.get("display_name", f"Bilinmeyen Bölge ({lat},{lon})")
        logging.info(f"Nominatim API'den alınan konum: {display_name}")

        save_location_to_cache(lat, lon, display_name)
        time.sleep(config.get("LOCATION_API_RATE_LIMIT_DELAY", LOCATION_API_RATE_LIMIT_DELAY))
        return display_name

    except Timeout:
        timeout_duration = config.get("LOCATION_API_TIMEOUT", LOCATION_API_TIMEOUT)
        logging.warning(f"Nominatim API zaman aşımı (timeout={timeout_duration}s). Koordinat: ({lat}, {lon})")
        return f"Konum Alınamadı (Zaman Aşımı: {lat},{lon})"
    except RequestException as e:
        logging.error(f"Nominatim API ağ hatası ({lat}, {lon}): {e}")
        return f"Konum Alınamadı (Ağ Hatası: {lat},{lon})"
    except JSONDecodeError as e:
        logging.error(f"Nominatim API JSON hatası ({lat}, {lon}): {e}")
        return f"Konum Alınamadı (Veri Hatası: {lat},{lon})"
    except Exception as e:
        logging.exception(f"get_location_name beklenmedik hata ({lat}, {lon}): {e}")
        return f"Konum Alınamadı (Bilinmeyen Hata: {lat},{lon})"