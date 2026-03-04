#!/usr/bin/env python3
"""
GRIMOIRE - Horror Film Oracle
Backend server using Python stdlib only
"""

import http.server
import socketserver
import json
import sqlite3
import os
import urllib.parse
from pathlib import Path

PORT = int(os.environ.get("PORT", 8000))
BASE_DIR = Path(os.path.abspath(__file__)).parent
DB_PATH = BASE_DIR / "data" / "grimoire.db"
STATIC_PATH = BASE_DIR / "static"


# ─── DATABASE SETUP ───────────────────────────────────────────────────────────

def init_db():
    """Initialize SQLite database with schema."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY,
            tmdb_id INTEGER UNIQUE,
            title TEXT NOT NULL,
            original_title TEXT,
            year INTEGER,
            overview TEXT,
            poster_path TEXT,
            backdrop_path TEXT,
            vote_average REAL,
            vote_count INTEGER,
            popularity REAL,
            runtime INTEGER,
            tagline TEXT,
            subgenres TEXT,
            mood_tags TEXT,
            atmosphere TEXT,
            themes TEXT,
            director TEXT,
            origin_country TEXT,
            language TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER UNIQUE,
            status TEXT,
            rating INTEGER,
            notes TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # --- NUEVO CÓDIGO DE DESCARGA / SEEDING AUTOMÁTICO ---
    c.execute("SELECT COUNT(*) FROM movies")
    if c.fetchone()[0] == 0:
        print("[INFO] La base de datos está vacía. Invocando catálogo de películas...")
        import urllib.request
        import csv
        import io
        
        # Dataset público y gratuito de películas de terror extraído de TMDB (~32k películas)
        url = "https://raw.githubusercontent.com/rfordatascience/tidytuesday/master/data/2022/2022-11-01/horror_movies.csv"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                content = response.read().decode('utf-8')
                reader = csv.DictReader(io.StringIO(content))
                
                movies_to_insert = []
                for row in reader:
                    # Extraer el año de la fecha de estreno (YYYY-MM-DD)
                    year = None
                    if row.get('release_date'):
                        try:
                            year = int(row.get('release_date').split('-')[0])
                        except ValueError:
                            pass
                            
                    # Conversores seguros para evitar crasheos con datos vacíos
                    def get_float(v):
                        try: return float(v)
                        except: return 0.0
                        
                    def get_int(v):
                        try: return int(v)
                        except: return 0
                        
                    movies_to_insert.append((
                        get_int(row.get('id')),
                        row.get('title') or "Desconocido",
                        row.get('original_title'),
                        year,
                        row.get('overview'),
                        row.get('poster_path'),
                        row.get('backdrop_path'),
                        get_float(row.get('vote_average')),
                        get_int(row.get('vote_count')),
                        get_float(row.get('popularity')),
                        get_int(row.get('runtime')),
                        row.get('tagline'),
                        row.get('genre_names'),
                        row.get('original_language')
                    ))
                
                c.executemany("""
                    INSERT OR IGNORE INTO movies (
                        tmdb_id, title, original_title, year, overview, 
                        poster_path, backdrop_path, vote_average, vote_count, 
                        popularity, runtime, tagline, subgenres, language
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, movies_to_insert)
                
            print(f"[INFO] ¡Grimorio actualizado con {len(movies_to_insert)} películas de terror!")
        except Exception as e:
            print(f"[ERROR] Falló la invocación del catálogo: {e}")
    # -----------------------------------------------------

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── RECOMMENDATION ENGINE ────────────────────────────────────────────────────

# ─── RECOMMENDATION ENGINE & SCRAPING ─────────────────────────────────────────
import urllib.request
import re

SUBGENRE_MAP = {
    "supernatural": ["ghost", "demon", "possession", "exorcism", "haunting", "spirit", "paranormal", "poltergeist"],
    "slasher": ["killer", "murder", "stalker", "serial killer", "massacre", "slaughter", "machete", "giallo"],
    "psychological": ["mind", "paranoia", "sanity", "delusion", "identity", "psychological", "thriller", "trauma", "madness"],
    "body_horror": ["mutation", "body", "flesh", "transformation", "infection", "parasite", "grotesque", "gore", "cronenberg"],
    "cosmic": ["lovecraft", "cosmic", "ancient", "void", "eldritch", "unknowable", "cult", "cthulhu"],
    "found_footage": ["found footage", "documentary", "camera", "recording", "tape", "mockumentary", "pov"],
    "folk_horror": ["folk", "pagan", "ritual", "cult", "village", "rural", "tradition", "midsommar", "wicker"],
    "zombie": ["zombie", "undead", "outbreak", "apocalypse", "plague", "virus", "infection", "flesh eating"],
    "vampire": ["vampire", "blood", "immortal", "fangs", "bite", "nosferatu", "dracula"],
    "creature": ["monster", "creature", "beast", "alien", "mutant", "predator", "kaiju", "sasquatch"],
    "gothic": ["gothic", "castle", "aristocrat", "victorian", "dark romance", "forbidden", "macabre", "manor"],
    "survival": ["survival", "trapped", "escape", "wilderness", "isolated", "hunt", "woods", "cabin"],
    "witchcraft": ["witch", "coven", "spell", "black magic", "brujas", "witches", "witchcraft", "occult", "satanic"],
    "demonic": ["demon", "devil", "satan", "hell", "pazuzu", "baphomet", "evil", "incubus", "possession"],
    "alien_scifi": ["alien", "extraterrestrial", "space", "ship", "ufo", "sci-fi", "abduction"]
}

MOOD_ATMOSPHERE_MAP = {
    "terrified": {"keywords": ["relentless", "brutal", "unforgiving", "graphic"], "score_boost": 1.4},
    "unsettled": {"keywords": ["dread", "slow burn", "atmospheric", "psychological"], "score_boost": 1.3},
    "thrilled": {"keywords": ["tension", "suspense", "twist", "chase"], "score_boost": 1.2},
    "curious": {"keywords": ["mystery", "investigation", "lore", "mythology"], "score_boost": 1.1},
    "nostalgic": {"keywords": ["classic", "retro", "80s", "70s"], "score_boost": 1.0},
    "adventurous": {"keywords": ["action", "survival", "creature", "monster"], "score_boost": 1.2},
    "melancholic": {"keywords": ["grief", "loss", "haunting", "melancholy"], "score_boost": 1.3},
}

DECADE_WEIGHTS = {
    "classic": (1920, 1969), "70s80s": (1970, 1989),
    "90s00s": (1990, 2009), "recent": (2010, 2026), "any": (1920, 2026),
}

# DICCIONARIO DE TRADUCCIÓN PROFUNDO: Mapeo de conceptos específicos del usuario
SPANISH_TO_ENGLISH_KEYWORDS = {
    "bruja": ["witch", "coven"], "brujas": ["witch", "coven", "witches"],
    "zombi": ["zombie", "undead"], "zombis": ["zombies", "undead", "infected"],
    "vampiro": ["vampire", "dracula"], "vampiros": ["vampires", "dracula", "bloodsucker"],
    "fantasma": ["ghost", "spirit", "haunting", "poltergeist"], "fantasmas": ["ghosts", "spirits", "entity"],
    "demonio": ["demon", "devil", "possession", "incubus"], "demonios": ["demons", "exorcism", "entity"],
    "alien": ["alien", "extraterrestrial"], "extraterrestre": ["alien", "ufo", "sci-fi"],
    "monstruo": ["monster", "creature", "beast"], "monstruos": ["monsters", "beasts"],
    "sangre": ["blood", "gore", "bloody"], "asesino": ["killer", "slasher", "psychopath", "murderer"],
    "secta": ["cult", "ritual", "wicker"], "culto": ["cult", "sacrifice", "occult"],
    "bosque": ["woods", "forest", "trees"], "cabaña": ["cabin", "hut", "isolated"],
    "casa": ["house", "mansion", "home"], "maldita": ["haunted", "cursed"],
    "muñeco": ["doll", "dummy", "puppet"], "payaso": ["clown", "pennywise", "circus"],
    "espacio": ["space", "spaceship", "astronaut"], "posesion": ["possession", "exorcism"],
    "garaje": ["garage", "basement", "underground", "parking"], "sotano": ["basement", "cellar"],
    "adolescente": ["teen", "teenager", "youth"], "adolescentes": ["teens", "teenagers", "high school", "college", "youth"],
    "asilo": ["asylum", "sanatorium", "hospital"], "manicomio": ["asylum", "mental institution", "madhouse"],
    "nieve": ["snow", "winter", "cold", "blizzard", "ice"], "aislado": ["isolated", "remote"],
    "venganza": ["revenge", "vengeance", "payback"], "virus": ["virus", "infection", "outbreak"]
}

def scrape_real_movie_data(tmdb_id, original_title, year):
    """Scrapea la sinopsis oficial en español de TMDB y las notas reales de Rotten Tomatoes."""
    data = {
        "overview_es": "Sinopsis clasificada o no disponible en los archivos oficiales.",
        "critic_score": "N/A",
        "audience_score": "N/A"
    }
    # Falsificamos el User-Agent para evitar bloqueos anti-bot
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    # 1. Extraer la sinopsis REAL en español desde TMDB
    try:
        tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}?language=es-ES"
        req = urllib.request.Request(tmdb_url, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as response:
            html = response.read().decode('utf-8')
            match = re.search(r'<div class="overview" dir="auto">\s*<p>(.*?)</p>', html, re.DOTALL)
            if match:
                synopsis = match.group(1).strip()
                if synopsis and "No tenemos una sinopsis" not in synopsis:
                    data["overview_es"] = synopsis
    except Exception:
        pass

    # 2. Extraer notas REALES desde Rotten Tomatoes
    try:
        # Limpiar el título para la URL de Rotten Tomatoes (ej: "The Blair Witch Project" -> "the_blair_witch_project")
        clean_title = re.sub(r'[^a-z0-9]+', '_', str(original_title).lower()).strip('_')
        rt_urls = [
            f"https://www.rottentomatoes.com/m/{clean_title}",
            f"https://www.rottentomatoes.com/m/{clean_title}_{year}" # Fallback común para remakes
        ]
        
        html_rt = ""
        for url in rt_urls:
            try:
                req_rt = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req_rt, timeout=3) as response:
                    html_rt = response.read().decode('utf-8')
                    break 
            except Exception:
                continue
        
        if html_rt:
            # Scrapeo de Tomatometer (Crítica)
            critic_match = re.search(r'tomatometerscore="(\d+)"', html_rt, re.IGNORECASE) or re.search(r'"tomatometerScore":\{"score":(\d+)', html_rt)
            if critic_match:
                data["critic_score"] = critic_match.group(1) + "%"
            
            # Scrapeo de Audience Score (Público)
            audience_match = re.search(r'audiencescore="(\d+)"', html_rt, re.IGNORECASE) or re.search(r'"audienceScore":\{"score":(\d+)', html_rt)
            if audience_match:
                data["audience_score"] = audience_match.group(1) + "%"
    except Exception:
        pass

    return data

# NUEVOS MAPEOS PARA EL ALGORITMO ESTRICTO
SETTING_MAP = {
    "casa o mansión": ["house", "mansion", "home", "estate", "apartment", "haunted"],
    "naturaleza salvaje": ["woods", "forest", "mountain", "ocean", "sea", "desert", "wild", "camp", "cabin"],
    "ciudad": ["city", "urban", "streets", "town"],
    "recinto cerrado": ["hotel", "hospital", "asylum", "bunker", "school", "facility", "trapped", "prison"],
    "lugar sagrado": ["church", "cemetery", "temple", "cult", "holy", "graveyard", "convent", "ritual"]
}

PROTAGONIST_MAP = {
    "niños o adolescentes": ["child", "children", "kid", "teen", "teenager", "youth", "school", "camp"],
    "última superviviente": ["final girl", "woman", "girl", "survivor", "she", "her"],
    "investigador o detective": ["detective", "investigator", "police", "cop", "search", "journalist", "reporter"],
    "familia en peligro": ["family", "parents", "mother", "father", "daughter", "son", "brother", "sister"],
    "ver desde el lado oscuro": ["killer", "monster", "vampire", "revenge", "perspective"]
}

def score_movie(movie: dict, profile: dict) -> float:
    """Multi-dimensional strict scoring algorithm with smart synergy."""
    excluded = profile.get("excluded_ids", [])
    if movie.get("tmdb_id") in excluded:
        return -9999

    vote_avg = movie.get("vote_average") or 0.0
    min_rating = float(profile.get("min_rating", 6.0))
    if vote_avg < min_rating:
        return -9999

    # Empezamos con una base pequeña.
    score = 10.0 
    
    full_text = f"{movie.get('title','')} {movie.get('overview','')} {movie.get('subgenres','')} {movie.get('mood_tags','')} {movie.get('themes','')}".lower()

    subgenre_label_map = {
        "fantasmas": "supernatural", "slasher": "slasher", "psicológico": "psychological", 
        "body horror": "body_horror", "terror cósmico": "cosmic", "found footage": "found_footage",
        "folk horror": "folk_horror", "zombis": "zombie", "vampiros": "vampire", 
        "criatura / monstruo": "creature", "gótico": "gothic", "terror sci-fi": "alien_scifi"
    }

    # Definimos qué géneros son IRREFUTABLES y cuáles son MÁS FLEXIBLES
    STRICT_SUBGENRES = {"found_footage", "folk_horror", "body_horror", "cosmic", "alien_scifi"}
    # Los demás (supernatural, slasher, psychological, zombie, etc.) se consideran amplios/flexibles

    # ── 1. SUBGÉNERO (LÓGICA INTELIGENTE ESTRICTA VS AMPLIA) ──────────────────
    raw_subgenres = profile.get("subgenres", [])
    if isinstance(raw_subgenres, str): raw_subgenres = [raw_subgenres]
    desired_subs = [subgenre_label_map.get(s.lower(), s.lower().replace(" ", "_")) for s in raw_subgenres]

    if desired_subs:
        matched_genre = False
        # Comprobamos si el usuario ha pedido algún género estricto
        is_strict_request = any(sg in STRICT_SUBGENRES for sg in desired_subs)
        
        for sg in desired_subs:
            keywords = SUBGENRE_MAP.get(sg, [sg])
            if sg in (movie.get("subgenres") or "").lower() or any(kw in full_text for kw in keywords):
                matched_genre = True
                score += 5000  # Gran impulso por acertar el género
                # Si acierta un género estricto, le damos un bonus extra de sinergia
                if sg in STRICT_SUBGENRES:
                    score *= 1.5 
                break
        
        if not matched_genre:
            if is_strict_request:
                # CASTIGO IRREFUTABLE: Si pide Found Footage y no lo es, a la basura.
                return -9999
            else:
                # CASTIGO FLEXIBLE: Si pide "fantasmas" y no lo es, penalizamos duro (x0.05), 
                # pero dejamos que un NLP o Escenario perfecto la pueda salvar.
                score *= 0.05 

    # ── 2. ESCENARIO Y PROTAGONISTA (SINERGIA ACUMULATIVA) ────────────────────
    # Si aciertan, multiplican. Si fallan, no restan, porque el usuario puede no ser estricto con esto.
    setting = profile.get("setting", "").lower()
    if setting in SETTING_MAP:
        if any(kw in full_text for kw in SETTING_MAP[setting]):
            score *= 1.3
            score += 800

    protagonist = profile.get("protagonist", "").lower()
    if protagonist in PROTAGONIST_MAP:
        if any(kw in full_text for kw in PROTAGONIST_MAP[protagonist]):
            score *= 1.3
            score += 800

    # ── 3. ÉPOCA ESTRICTA ─────────────────────────────────────────────────────
    raw_era = profile.get("era", "Me da igual")
    era_label_map = {
        "antes de los 70": (1920, 1969), "los 70 y los 80": (1970, 1989),
        "los 90 y los 2000": (1990, 2009), "2010 hasta hoy": (2010, 2026)
    }
    year = movie.get("year") or 2000
    if raw_era.lower() in era_label_map:
        start_y, end_y = era_label_map[raw_era.lower()]
        if start_y <= year <= end_y:
            score *= 1.4
            score += 500
        else:
            # Penalizamos la época, pero no la eliminamos si el NLP o el género son perfectos
            score *= 0.2 

    # ── 4. FREE TEXT NLP (SINERGIA FINAL) ─────────────────────────────────────
    user_plot = (profile.get("plot_desc") or profile.get("plot_description") or "").lower()
    if user_plot and len(user_plot) > 4:
        stop = {"con","que","en","de","la","el","los","las","una","un","por","para","como",
                "pero","algo","quiero","sobre","pelicula","peli","trate","vaya","historia",
                "busco","ver","terror","miedo","sean","sus","y","o","a","al","del","lo"}
        
        import re
        words = [w for w in re.sub(r'[^\w\s]', '', user_plot).split() if len(w) > 3 and w not in stop]
        
        if words:
            matched_keywords = 0
            for w in words:
                search_terms = SPANISH_TO_ENGLISH_KEYWORDS.get(w, [w])
                if any(f" {term}" in full_text or f"{term} " in full_text for term in search_terms):
                    matched_keywords += 1
                elif w in full_text:
                    matched_keywords += 0.5

            if matched_keywords == 0:
                # Si el usuario detalla una trama y la peli no tiene nada que ver, penalizamos fuertemente.
                score *= 0.05
            else:
                # Sinergia explosiva: Si ha acertado género, época, y ADEMÁS las palabras clave,
                # esta película es "LA" película.
                score *= (2.0 ** matched_keywords) 
                score += (1500 * matched_keywords)

    return round(score, 2)

def get_recommendations(profile: dict, limit: int = 5) -> list:
    """Get top N recommendations from database based on user profile."""
    conn = get_db()
    c = conn.cursor()

    excluded_ids = profile.get("excluded_ids", [])
    placeholders = ",".join("?" * len(excluded_ids)) if excluded_ids else "0"

    # Seleccionamos un pool grande para asegurar que el NLP tenga donde buscar
    query = f"""
        SELECT m.*, ul.tmdb_id as in_library
        FROM movies m
        LEFT JOIN user_library ul ON m.tmdb_id = ul.tmdb_id
        WHERE ul.tmdb_id IS NULL
        {'AND m.tmdb_id NOT IN (' + placeholders + ')' if excluded_ids else ''}
        AND m.vote_count > 50
        ORDER BY m.popularity DESC
        LIMIT 800
    """

    rows = c.execute(query, excluded_ids if excluded_ids else []).fetchall()
    conn.close()

    scored = []
    for row in rows:
        movie = dict(row)
        s = score_movie(movie, profile)
        if s > 0:
            scored.append((s, movie))

    scored.sort(key=lambda x: x[0], reverse=True)
    
    # Preparamos los datos finales con Scraping Real en caliente
    results = []
    for s, m in scored[:limit]:
        
        # Scrapeamos los datos en tiempo real (Sinopsis ES y Notas Rotten Tomatoes)
        real_data = scrape_real_movie_data(m.get("tmdb_id"), m.get("original_title") or m.get("title"), m.get("year"))
        
        m["overview_es"] = real_data["overview_es"]
        
        # Fallback de notas: Si la peli es tan de culto que no está en Rotten Tomatoes, usamos la nota de TMDB
        base_score = int((m.get("vote_average", 0) * 10))
        m["critic_score"] = real_data["critic_score"] if real_data["critic_score"] != "N/A" else f"? (TMDB: {base_score}%)"
        m["audience_score"] = real_data["audience_score"] if real_data["audience_score"] != "N/A" else f"{base_score}%"
        
        results.append({"score": s, **m})
        
    return results


# ─── HTTP REQUEST HANDLERS ────────────────────────────────────────────────────

class GrimoireHandler(http.server.SimpleHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # Silence default logging

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.serve_file(STATIC_PATH / "index.html", "text/html")
        elif path == "/api/library":
            self.handle_get_library()
        elif path == "/api/stats":
            self.handle_stats()
        elif path.startswith("/api/movie/"):
            tmdb_id = path.split("/")[-1]
            self.handle_get_movie(tmdb_id)
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        data = self.read_body()

        if path == "/api/recommend":
            self.handle_recommend(data)
        elif path == "/api/movies/bulk":
            self.handle_bulk_import(data)
        elif path == "/api/library/add":
            self.handle_add_to_library(data)
        elif path == "/api/library/rate":
            self.handle_rate_movie(data)
        elif path == "/api/library/remove":
            self.handle_remove_from_library(data)
        else:
            self.send_error(404, "Not found")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except:
            return {}

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, filepath, content_type):
        filepath = Path(filepath).resolve()
        if not filepath.exists():
            print(f"[ERROR] File not found: {filepath}")
            print(f"[DEBUG] BASE_DIR={BASE_DIR}  STATIC_PATH={STATIC_PATH}  exists={STATIC_PATH.exists()}")
            error_html = (
                "<!DOCTYPE html><html><body style=\"background:#0a0608;color:#e8dcc8;"
                "font-family:monospace;padding:3rem;text-align:center\">"
                "<h2 style=\"color:#8b0000\">GRIMOIRE — Archivo no encontrado</h2>"
                f"<p>Ruta buscada: <code>{filepath}</code></p>"
                "<p>Asegurate de que <b>index.html</b> esta dentro de la carpeta <b>static/</b></p>"
                "<pre style=\"text-align:left;display:inline-block;color:#c9a84c\">"
                "grimoire/\n├── server.py\n└── static/\n    └── index.html</pre>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(error_html))
            self.end_headers()
            self.wfile.write(error_html)
            return
        with open(filepath, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    # ── API HANDLERS ──────────────────────────────────────────────────────────

    def handle_recommend(self, data):
        profile = data.get("profile", {})
        
        # --- GAG RADIACTIVO / TROLL DETECTION ---
        user_plot = (profile.get("plot_desc") or profile.get("plot_description") or "").lower()
        
        import re
        # Limpiamos signos de puntuación para que no escapen al filtro (ej: "basura.")
        clean_plot = re.sub(r'[^\w\s]', '', user_plot)
        words = clean_plot.split()
        
        troll_words = {
            "tonto", "idiota", "imbecil", "mierda", "puta", "cabron", "estupido", 
            "basura", "polla", "culo", "joder", "pene", "cojones", "puto", "perra"
        }
        
        # Detecta si hay insultos o si ha aporreado el teclado (ej: "asdfghjkl")
        is_keyboard_smash = any(len(w) > 7 and not any(v in w for v in "aeiou") for w in words)
        has_troll_word = any(tw in words for tw in troll_words)
        
        if has_troll_word or is_keyboard_smash:
            self.send_json({
                "radioactive_troll": True, 
                "message": "⚠️ ANOMALÍA DETECTADA ⚠️\nEl Oráculo no procesa escoria intelectual. Tu sistema ha sido contaminado."
            })
            return
        # ----------------------------------------

        limit = data.get("limit", 3)
        results = get_recommendations(profile, limit)
        self.send_json({"results": results, "count": len(results)})
        
        # Aporreo de teclado (palabra muy larga sin vocales)
        is_keyboard_smash = any(len(w) > 6 and not any(v in w for v in "aeiou") for w in user_plot.split())
        
        if any(w in user_plot for w in troll_words) or is_keyboard_smash:
            self.send_json({
                "radioactive_troll": True, 
                "message": "ANOMALÍA DETECTADA. El Oráculo no procesa escoria intelectual. Tu sistema ha sido contaminado."
            })
            return
        # ----------------------------------------

        limit = data.get("limit", 3)
        results = get_recommendations(profile, limit)
        self.send_json({"results": results, "count": len(results)})
        movies = data.get("movies", [])
        if not movies:
            self.send_json({"error": "No movies provided"}, 400)
            return

        conn = get_db()
        c = conn.cursor()
        inserted = 0
        updated = 0

        for m in movies:
            try:
                existing = c.execute("SELECT id FROM movies WHERE tmdb_id=?", (m.get("tmdb_id"),)).fetchone()
                if existing:
                    c.execute("""
                        UPDATE movies SET title=?, year=?, overview=?, poster_path=?, backdrop_path=?,
                        vote_average=?, vote_count=?, popularity=?, runtime=?, tagline=?,
                        subgenres=?, mood_tags=?, atmosphere=?, themes=?, director=?,
                        origin_country=?, language=?
                        WHERE tmdb_id=?
                    """, (
                        m.get("title"), m.get("year"), m.get("overview"),
                        m.get("poster_path"), m.get("backdrop_path"),
                        m.get("vote_average"), m.get("vote_count"), m.get("popularity"),
                        m.get("runtime"), m.get("tagline"),
                        m.get("subgenres"), m.get("mood_tags"), m.get("atmosphere"),
                        m.get("themes"), m.get("director"),
                        m.get("origin_country"), m.get("language"),
                        m.get("tmdb_id")
                    ))
                    updated += 1
                else:
                    c.execute("""
                        INSERT INTO movies (tmdb_id, title, original_title, year, overview,
                        poster_path, backdrop_path, vote_average, vote_count, popularity,
                        runtime, tagline, subgenres, mood_tags, atmosphere, themes, director,
                        origin_country, language)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        m.get("tmdb_id"), m.get("title"), m.get("original_title"),
                        m.get("year"), m.get("overview"),
                        m.get("poster_path"), m.get("backdrop_path"),
                        m.get("vote_average"), m.get("vote_count"), m.get("popularity"),
                        m.get("runtime"), m.get("tagline"),
                        m.get("subgenres"), m.get("mood_tags"), m.get("atmosphere"),
                        m.get("themes"), m.get("director"),
                        m.get("origin_country"), m.get("language")
                    ))
                    inserted += 1
            except Exception as e:
                pass  # Skip malformed entries

        conn.commit()
        conn.close()
        self.send_json({"inserted": inserted, "updated": updated, "total": inserted + updated})

    def handle_get_library(self):
        conn = get_db()
        rows = conn.execute("""
            SELECT * FROM user_library ORDER BY added_at DESC
        """).fetchall()
        conn.close()
        self.send_json({"library": [dict(r) for r in rows]})

    def handle_add_to_library(self, data):
        conn = get_db()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO user_library (tmdb_id, title, poster_path, year, watched)
                VALUES (?,?,?,?,?)
            """, (data.get("tmdb_id"), data.get("title"), data.get("poster_path"),
                  data.get("year"), data.get("watched", 0)))
            conn.commit()
            self.send_json({"success": True})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)
        finally:
            conn.close()

    def handle_rate_movie(self, data):
        conn = get_db()
        try:
            conn.execute("""
                UPDATE user_library SET rating=?, review=?, rated_at=CURRENT_TIMESTAMP
                WHERE tmdb_id=?
            """, (data.get("rating"), data.get("review"), data.get("tmdb_id")))
            conn.commit()
            self.send_json({"success": True})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)
        finally:
            conn.close()

    def handle_remove_from_library(self, data):
        conn = get_db()
        try:
            conn.execute("DELETE FROM user_library WHERE tmdb_id=?", (data.get("tmdb_id"),))
            conn.commit()
            self.send_json({"success": True})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)
        finally:
            conn.close()

    def handle_get_movie(self, tmdb_id):
        conn = get_db()
        row = conn.execute("SELECT * FROM movies WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        conn.close()
        if row:
            self.send_json(dict(row))
        else:
            self.send_json({"error": "Not found"}, 404)

    def handle_stats(self):
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
        library_count = conn.execute("SELECT COUNT(*) FROM user_library").fetchone()[0]
        rated_count = conn.execute("SELECT COUNT(*) FROM user_library WHERE rating IS NOT NULL").fetchone()[0]
        conn.close()
        self.send_json({
            "total_movies": total,
            "library_count": library_count,
            "rated_count": rated_count
        })


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Verify static folder exists ───────────────────────────
    if not STATIC_PATH.exists():
        print(f"[ERROR] No se encuentra la carpeta 'static/'")
        print(f"[ERROR] Ruta esperada: {STATIC_PATH}")
        print("[SOLUCIÓN] Crea la carpeta 'static' junto a server.py y coloca index.html dentro.")
        exit(1)
    if not (STATIC_PATH / "index.html").exists():
        print(f"[ERROR] No se encuentra static/index.html")
        print(f"[ERROR] Ruta esperada: {STATIC_PATH / 'index.html'}")
        print("[SOLUCIÓN] Descarga index.html y colócalo en la carpeta 'static/'.")
        exit(1)

    init_db()
    print(f"""
╔═══════════════════════════════════════════════════╗
║           G R I M O I R E                        ║
║       Horror Film Oracle — v1.0                  ║
╠═══════════════════════════════════════════════════╣
║  Abre en tu navegador:                           ║
║  >>> http://localhost:{PORT}                       ║
╠═══════════════════════════════════════════════════╣
║  Directorio base : {str(BASE_DIR)[:46]:46s}  ║
║  Carpeta static  : OK                            ║
║  Base de datos   : OK                            ║
╚═══════════════════════════════════════════════════╝
Ctrl+C para detener el servidor.
    """)

    with socketserver.TCPServer(("", PORT), GrimoireHandler) as httpd:
        httpd.allow_reuse_address = True
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[GRIMOIRE] Servidor detenido.")
