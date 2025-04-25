# data.py

import sqlite3
from typing import Optional, Dict, Any
from datetime import datetime
import logging
import os # DB_FILE kontrolü için

# Bu dosyanın bulunduğu dizini al
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Veritabanı dosyasını bu dizine göre tanımla
DB_FILE = os.path.join(_BASE_DIR, "deprem.db")

def init_db():
    """Veritabanı ve tabloları oluşturur veya varolanı kullanır."""
    try:
        # Veritabanı dosyasının bulunduğu dizin yoksa oluştur
        db_dir = os.path.dirname(DB_FILE)
        if not os.path.exists(db_dir):
             os.makedirs(db_dir)
             logging.info(f"Veritabanı dizini oluşturuldu: {db_dir}")

        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS earthquakes (
                unid TEXT PRIMARY KEY,
                time TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                depth REAL,
                mag REAL,
                region TEXT,
                location TEXT,
                rectime TEXT NOT NULL
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                name TEXT NOT NULL,
                PRIMARY KEY (lat, lon)
            )
            """)
            # İndeksler (okuma performansını artırabilir)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_earthquakes_time ON earthquakes(time)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_locations_lat_lon ON locations(lat, lon)")

            con.commit()
            logging.info(f"Veritabanı '{DB_FILE}' başarıyla başlatıldı/kontrol edildi.")
    except sqlite3.Error as e:
        logging.exception(f"Veritabanı başlatma/bağlanma hatası ({DB_FILE}): {e}")
        raise

def earthquake_exists(unid: str) -> bool:
    """Verilen unid'ye sahip depremin veritabanında olup olmadığını kontrol eder."""
    if not isinstance(unid, str) or not unid: return False
    try:
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute("SELECT 1 FROM earthquakes WHERE unid = ? LIMIT 1", (unid,))
            exists = cur.fetchone() is not None
            # logging.debug(f"Deprem var mı kontrolü: {unid} -> {exists}") # Gerekirse açılabilir
            return exists
    except sqlite3.Error as e:
        logging.error(f"earthquake_exists kontrolü sırasında hata (unid: {unid}): {e}")
        return False # Hata durumunda güvenli varsayım

def save_earthquake(data: Dict[str, Any]):
    """Yeni deprem verisini veritabanına kaydeder."""
    record_time_utc = datetime.utcnow().isoformat()
    required_keys = ["unid", "time", "lat", "lon"]
    if not all(key in data and data[key] is not None for key in required_keys):
         logging.error(f"Kaydedilecek deprem verisinde eksik anahtar(lar) var: {data}")
         return

    unid = data.get('unid')
    logging.info(f"Yeni deprem veritabanına kaydediliyor: {unid}")
    try:
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            # Sözlük kullanarak daha okunaklı ekleme
            cur.execute("""
            INSERT INTO earthquakes (unid, time, lat, lon, depth, mag, region, location, rectime)
            VALUES (:unid, :time, :lat, :lon, :depth, :mag, :region, :location, :rectime)
            """, {
                "unid": unid,
                "time": data.get("time"),
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "depth": data.get("depth"),
                "mag": data.get("mag"),
                "region": data.get("region"),
                "location": data.get("location"),
                "rectime": record_time_utc
                })
            con.commit()
            logging.info(f"Deprem başarıyla kaydedildi: {unid}")
    except sqlite3.IntegrityError:
        logging.warning(f"Deprem zaten var (IntegrityError - unid: {unid}). Kaydedilmedi.")
    except sqlite3.Error as e:
        logging.exception(f"Deprem kaydetme hatası (unid: {unid}): {e}")

def get_location_from_cache(lat: float, lon: float) -> Optional[str]:
    """Verilen koordinat için önbellekten konum adını alır."""
    try:
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute("SELECT name FROM locations WHERE lat=? AND lon=?", (lat, lon))
            row = cur.fetchone()
            return row[0] if row else None
    except sqlite3.Error as e:
        logging.error(f"Konum önbelleği okuma hatası ({lat},{lon}): {e}")
        return None

def save_location_to_cache(lat: float, lon: float, name: str):
    """Alınan konum adını önbelleğe kaydeder."""
    if not isinstance(name, str) or not name: return # Boş isim kaydetme
    logging.debug(f"Konum önbelleğe kaydediliyor: ({lat}, {lon}) -> {name[:50]}...") # İsmi kısaltarak logla
    try:
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute("INSERT OR IGNORE INTO locations (lat, lon, name) VALUES (?, ?, ?)", (lat, lon, name))
            con.commit()
    except sqlite3.Error as e:
        logging.exception(f"Konum önbelleğe kaydetme hatası ({lat},{lon}): {e}")