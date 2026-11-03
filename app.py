# PeajeControl Go - Flask + pdfplumber. BD: SQLite (dev) o PostgreSQL (prod, via DATABASE_URL). Sin IA, sin API keys.
import csv, io, os, re, sqlite3, base64, json, secrets, hmac, time, unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from flask import Flask, g, request, redirect, session, render_template, send_file, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import load_workbook, Workbook

app = Flask(__name__)
# Sin clave fija: si falta SECRET_KEY se genera una al azar (las sesiones no sobreviven al reinicio).
IN_PROD = bool(os.environ.get("RENDER")) or os.environ.get("FLASK_ENV") == "production"
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    app.secret_key = secrets.token_hex(32)
    print("ADVERTENCIA: falta la variable SECRET_KEY; se usa una clave aleatoria "
          "(las sesiones se pierden al reiniciar). Definila en el entorno.")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # tope de subida por request (varios archivos): 60 MB
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=IN_PROD,
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=8))

# ================= SEGURIDAD: headers, CSRF, rate limit =================
@app.after_request
def headers_seguridad(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    # La app usa estilos y scripts inline, por eso 'unsafe-inline'.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if IN_PROD:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]

@app.context_processor
def inyectar_csrf():
    return {"csrf_token": csrf_token}

@app.before_request
def validar_csrf():
    if request.method == "POST":
        enviado, esperado = request.form.get("_csrf", ""), session.get("_csrf", "")
        if not esperado or not hmac.compare_digest(enviado, esperado):
            return "Solicitud invalida (CSRF). Recargá la pagina e intentá de nuevo.", 400

INTENTOS = defaultdict(list)  # clave (email o IP) -> instantes de logins fallidos
MAX_INTENTOS, VENTANA = 5, 300  # 5 fallos cada 5 minutos

def _claves_login(email):
    ip = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or request.remote_addr
    return ["e:" + email, "i:" + str(ip)]

def _bloqueado(claves):
    ahora = time.time()
    for k in claves:
        INTENTOS[k] = [t for t in INTENTOS[k] if ahora - t < VENTANA]
        if len(INTENTOS[k]) >= MAX_INTENTOS: return True
    return False

# ================= BASE DE DATOS (SQLite en dev / PostgreSQL en produccion) =================
# Si existe DATABASE_URL (p.ej. la conexion de Supabase en Render) se usa PostgreSQL;
# si no, se cae a un archivo SQLite local. El resto del codigo es agnostico al motor.
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(DATABASE_URL)
DB = os.environ.get("DB_PATH", "furlong.db")

if USE_PG:
    import psycopg2, psycopg2.extras, psycopg2.pool

    # Pool de conexiones tibias: evita re-conectar y re-hacer el handshake SSL en cada
    # request (clave porque la base esta lejos del servidor). Cada request toma una
    # conexion del pool y la devuelve; conserva las transacciones por request.
    _POOL = None
    def _get_pool():
        global _POOL
        if _POOL is None:
            kw = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3}
            if "sslmode=" not in DATABASE_URL and not any(h in DATABASE_URL for h in ("localhost", "127.0.0.1")):
                kw["sslmode"] = "require"
            _POOL = psycopg2.pool.ThreadedConnectionPool(1, 5, DATABASE_URL, **kw)
        return _POOL

    class _Cur:
        """Envuelve un cursor de psycopg2 imitando la API de sqlite3 (fetchone/all, iter)."""
        def __init__(self, c): self._c = c
        def fetchone(self): return self._c.fetchone()
        def fetchall(self): return self._c.fetchall()
        def __iter__(self): return iter(self._c.fetchall())

    class PGConn:
        """Conexion PostgreSQL (tomada del pool) con la misma interfaz que se usa sobre sqlite3."""
        def __init__(self):
            self._conn = _get_pool().getconn()
            self._bad = False
        def execute(self, sql, params=()):
            try:
                cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(sql.replace("?", "%s"), params)
                return _Cur(cur)
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                self._bad = True; raise
        def executemany(self, sql, seq):
            try:
                cur = self._conn.cursor(); cur.executemany(sql.replace("?", "%s"), list(seq)); cur.close()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                self._bad = True; raise
        def executescript(self, sql):
            cur = self._conn.cursor(); cur.execute(sql); cur.close()
        def commit(self): self._conn.commit()
        def rollback(self):
            try: self._conn.rollback()
            except Exception: self._bad = True
        def close(self):
            # Devuelve la conexion al pool (o la descarta si quedo en mal estado).
            try: _get_pool().putconn(self._conn, close=self._bad)
            except Exception:
                try: self._conn.close()
                except Exception: pass

    INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg2.IntegrityError)
else:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)

def _conectar():
    if USE_PG:
        return PGConn()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def db():
    if "db" not in g:
        g.db = _conectar()
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

# Tablas que pertenecen a una empresa (multi-inquilino: cada fila lleva empresa_id).
TABLAS_TENANT = ("usuarios", "flota", "movimientos", "archivos", "resumenes", "gastos")

def _esquema():
    pk = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY"
    real = "DOUBLE PRECISION" if USE_PG else "REAL"
    return f"""
    CREATE TABLE IF NOT EXISTS empresas(id {pk}, nombre TEXT, creado TEXT);
    CREATE TABLE IF NOT EXISTS usuarios(id {pk}, empresa_id INTEGER, email TEXT UNIQUE,
        clave TEXT, nombre TEXT, rol TEXT DEFAULT 'operador');
    CREATE TABLE IF NOT EXISTS flota(id {pk}, empresa_id INTEGER, tag TEXT, patente TEXT,
        dueno TEXT, equipo TEXT, estado TEXT, marca TEXT);
    CREATE TABLE IF NOT EXISTS movimientos(id {pk}, empresa_id INTEGER, fecha TEXT, tag TEXT,
        patente TEXT, concepto TEXT, monto {real}, dueno TEXT, equipo TEXT,
        verificado INTEGER DEFAULT 0, archivo TEXT, creado TEXT);
    CREATE TABLE IF NOT EXISTS archivos(id {pk}, empresa_id INTEGER, nombre TEXT, tipo TEXT,
        total_doc {real} DEFAULT 0, contenido TEXT, mime TEXT, creado TEXT);
    CREATE TABLE IF NOT EXISTS resumenes(id {pk}, empresa_id INTEGER, archivo TEXT, banco TEXT,
        titular TEXT, periodo TEXT, vencimiento TEXT, total_resumen {real}, creado TEXT);
    CREATE TABLE IF NOT EXISTS gastos(id {pk}, empresa_id INTEGER, resumen_id INTEGER,
        fecha TEXT, concepto TEXT, categoria TEXT, monto {real});
    CREATE INDEX IF NOT EXISTS idx_mov_tag ON movimientos(tag);
    CREATE INDEX IF NOT EXISTS idx_flota_tag ON flota(tag);
    """

def _columnas(con, tabla):
    if USE_PG:
        rows = con.execute("SELECT column_name FROM information_schema.columns "
                           "WHERE table_schema='public' AND table_name=?", (tabla,)).fetchall()
        return {r["column_name"] for r in rows}
    return {r["name"] for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()}

def _fix_archivos_unique(con):
    # Antes archivos.nombre era UNIQUE global. Con multi-empresa se quita: el mismo
    # nombre de archivo puede repetirse entre empresas distintas.
    if USE_PG:
        con.execute("ALTER TABLE archivos DROP CONSTRAINT IF EXISTS archivos_nombre_key")
        return
    for idx in con.execute("PRAGMA index_list(archivos)").fetchall():
        if idx["unique"]:
            cols = [c["name"] for c in con.execute(f"PRAGMA index_info({idx['name']})").fetchall()]
            if cols == ["nombre"]:
                con.execute("""CREATE TABLE archivos_new(id INTEGER PRIMARY KEY, empresa_id INTEGER,
                    nombre TEXT, tipo TEXT, total_doc REAL DEFAULT 0, contenido TEXT, mime TEXT, creado TEXT)""")
                con.execute("""INSERT INTO archivos_new(id,empresa_id,nombre,tipo,total_doc,contenido,mime,creado)
                    SELECT id,empresa_id,nombre,tipo,total_doc,contenido,mime,creado FROM archivos""")
                con.execute("DROP TABLE archivos")
                con.execute("ALTER TABLE archivos_new RENAME TO archivos")
                break

def migrar(con):
    # Actualiza bases creadas antes de multi-empresa sin perder datos.
    for tabla in TABLAS_TENANT:
        if "empresa_id" not in _columnas(con, tabla):
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN empresa_id INTEGER")
    _fix_archivos_unique(con)
    # Indices por empresa (se crean recien aca, ya con la columna presente).
    con.execute("CREATE INDEX IF NOT EXISTS idx_mov_emp ON movimientos(empresa_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_arch_emp ON archivos(empresa_id, nombre)")
    # Indices para las pantallas pesadas (filtros por empresa + orden/agrupacion). Compatibles con SQLite y Postgres.
    for ddl in ("idx_mov_emp_id ON movimientos(empresa_id, id)",
                "idx_mov_emp_patente ON movimientos(empresa_id, patente)",
                "idx_flota_emp_tag ON flota(empresa_id, tag)",
                "idx_gastos_emp ON gastos(empresa_id, id)",
                "idx_gastos_resumen ON gastos(resumen_id)",
                "idx_res_emp ON resumenes(empresa_id)",
                "idx_usr_emp ON usuarios(empresa_id)"):
        con.execute("CREATE INDEX IF NOT EXISTS " + ddl)

def init_db():
    con = _conectar()
    con.executescript(_esquema())
    migrar(con)
    row = con.execute("SELECT id FROM empresas ORDER BY id LIMIT 1").fetchone()
    if row:
        emp_id = row["id"]
    else:
        emp_id = con.execute("INSERT INTO empresas(nombre,creado) VALUES(?,?) RETURNING id",
                             ("Administración", datetime.now().strftime("%d/%m/%Y"))).fetchone()["id"]
    # Asigna a la empresa por defecto cualquier dato preexistente (migracion).
    for tabla in TABLAS_TENANT:
        con.execute(f"UPDATE {tabla} SET empresa_id=? WHERE empresa_id IS NULL", (emp_id,))
    if not con.execute("SELECT 1 FROM usuarios").fetchone():
        # Instancia nueva: la clave del admin sale de ADMIN_PASSWORD o se genera y se imprime en el log.
        clave_admin = os.environ.get("ADMIN_PASSWORD")
        if not clave_admin:
            clave_admin = secrets.token_urlsafe(12)
            print(f"ADMIN INICIAL: admin@peajecontrol.com / {clave_admin}  (cambiala desde Configuracion)")
        con.execute("INSERT INTO usuarios(empresa_id,email,clave,nombre,rol) VALUES(?,?,?,?,?)",
                    (emp_id, "admin@peajecontrol.com", generate_password_hash(clave_admin),
                     "Administrador", "superadmin"))
    else:
        # El admin original pasa a superadmin (contempla el email viejo y el nuevo).
        con.execute("UPDATE usuarios SET rol='superadmin' WHERE email IN (?,?) AND rol='admin'",
                    ("admin@peajecontrol.com", "admin@transportefurlong.com.ar"))
    # Transicion de marca (idempotente): renombra el admin y la empresa por defecto viejos.
    if not con.execute("SELECT 1 FROM usuarios WHERE email='admin@peajecontrol.com'").fetchone():
        con.execute("UPDATE usuarios SET email='admin@peajecontrol.com', nombre='Administrador' "
                    "WHERE email='admin@transportefurlong.com.ar'")
    con.execute("UPDATE empresas SET nombre='Administración' WHERE nombre='Transporte Furlong'")
    con.commit(); con.close()

# ================= UTILIDADES =================
def norm_tag(v):
    s = re.sub(r"[^A-Z0-9]", "", str(v or "").upper())
    d = re.sub(r"[^0-9]", "", s)
    return d[-8:] if len(d) >= 8 else d

def norm_monto(v):
    """Dinero en coma argentina (40.000,00) o punto (9896.82). Acepta negativos y '$'."""
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r"[^\d.,\-]", "", str(v or ""))
    neg = s.startswith("-"); s = s.replace("-", "")
    if not s: return 0.0
    if "," in s and "." in s:   # el ultimo separador es el decimal
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:              # solo coma: decimal (salvo miles tipo 1,234,567)
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    elif "." in s:
        if s.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", s):   # 1.234.567 o .000 = miles
            s = s.replace(".", "")
    try: r = float(s)
    except ValueError: return 0.0
    return -r if neg else r

RE_FECHA = re.compile(r"\b(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})\b")

def fmt_fecha(v):
    if isinstance(v, datetime): return v.strftime("%d/%m/%Y")
    m = RE_FECHA.search(str(v or ""))
    if not m: return str(v or "").strip()[:10]
    d, mo, y = re.split(r"[/.\-]", m.group(1))
    if len(y) == 2: y = "20" + y
    try: return f"{int(d):02d}/{int(mo):02d}/{int(y):04d}"
    except ValueError: return m.group(1)

def pesos(n):
    return f"${n:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")

app.jinja_env.filters["pesos"] = pesos

KEYS = {
    "tag": ["NUMERO DE TAG", "NUMERO TAG", "NUMEROTAG", "NRO TAG", "N TAG", "TAG", "DISPOSITIVO"],
    "patente": ["PATENTE", "DOMINIO"],
    "dueno": ["RESPONSABLE DE USUARIO", "RESPONSABLE", "DUENO", "DUEÑO", "TITULAR"],
    "equipo": ["EQUIPOS", "EQUIPO"], "estado": ["ESTADO"], "marca": ["MARCA"],
    "fecha": ["FECHA"], "concepto": ["CONCEPTO", "DESCRIPCION", "ESTACION", "DETALLE", "PEAJE"],
    "monto": ["IMPORTE", "MONTO", "VALOR", "TOTAL"],
}

def leer_filas(archivo, campos=None, clave=None):
    """CSV/Excel -> filas. En Excel con varias hojas y `campos`/`clave`, usa la primera hoja
    cuyo encabezado contiene `clave` (si no hay ninguna, junta todas como antes)."""
    nombre = archivo.filename.lower()
    if nombre.endswith(".csv"):
        texto = archivo.read().decode("utf-8-sig", errors="replace")
        sep = ";" if texto.count(";") > texto.count(",") else ","
        return [row for row in csv.reader(io.StringIO(texto), delimiter=sep)]
    wb = load_workbook(archivo, read_only=True, data_only=True)
    hojas = [[list(row) for row in hoja.iter_rows(values_only=True)] for hoja in wb.worksheets]
    if campos and clave:
        for filas in hojas:
            mapa, _ = mapear_columnas(filas, campos)
            if mapa and clave in mapa: return filas
    return [f for filas in hojas for f in filas]

def mapear_columnas(filas, campos):
    for i, fila in enumerate(filas[:15]):
        celdas = [re.sub(r"[^A-ZÑ ]", "", str(c or "").upper().strip()) for c in fila]
        mapa = {}
        for campo in campos:   # la clave mas especifica (primera de KEYS) gana, sin importar la columna
            for k in KEYS[campo]:
                j = next((j for j, c in enumerate(celdas) if c and k in c and j not in mapa.values()), None)
                if j is not None:
                    mapa[campo] = j; break
        if len(mapa) >= 2:
            return mapa, i + 1
    return None, 0

# ================= EXTRACCION PDF (sin IA) =================
RE_MONTO = re.compile(r"\$?\s*\d{1,3}(?:\.\d{3})*,\d{2}|\$?\s*\d+,\d{2}")
RE_TOTAL_TARJETA = re.compile(r"(TOTAL\s+A\s+PAGAR|SALDO\s+ACTUAL|TOTAL\s+CONSUMOS|PAGO\s+TOTAL)\D*?([\d.,]+,\d{2})", re.I)
RE_PERIODO = re.compile(r"(CIERRE|PERIODO|RESUMEN)\D{0,20}(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", re.I)
RE_VENC = re.compile(r"(VENCIMIENTO|VENCE|VTO)\D{0,20}(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", re.I)
BANCOS = ["GALICIA", "SANTANDER", "BBVA", "MACRO", "NACION", "PROVINCIA", "ICBC", "HSBC",
          "PATAGONIA", "SUPERVIELLE", "CREDICOOP", "COMAFI", "ITAU", "CITI", "BIND", "HIPOTECARIO",
          "CIUDAD", "SANTA FE", "CORDOBA", "ENTRE RIOS", "SAN JUAN", "ROELA", "COLUMBIA",
          "BRUBANK", "UALA", "UALÁ", "MERCADO PAGO", "MERCADOPAGO", "REBA", "WILOBANK",
          "VISA", "MASTERCARD", "MAESTRO", "AMEX", "AMERICAN EXPRESS", "CABAL", "NARANJA", "CENCOSUD"]

def texto_pdf(archivo):
    import pdfplumber
    texto = ""
    with pdfplumber.open(archivo) as pdf:
        for pag in pdf.pages:
            texto += (pag.extract_text() or "") + "\n"
    if len(texto.strip()) < 40 or texto.count("(cid:") * 7 > len(texto) * 0.3:
        raise ValueError("El PDF parece escaneado o ilegible; subí el Excel/CSV del resumen.")
    return texto

def categorizar(desc):
    d = desc.upper()
    if any(k in d for k in ("PEAJE", "AUSA", "AUSOL", "AUTOPISTA", "TELEPASE", "TELEPEAJE", "AUBASA",
        "CORREDOR VIAL", "RICCHERI", "ILLIA", "DELLEPIANE", "ACCESO OESTE", "ACCESO NORTE",
        "CAMINOS DEL", "AUTOPISTAS DEL", "RUTAS DEL", "PANAMERICANA", "PASE URBANO")): return "PEAJE"
    if any(k in d for k in ("YPF", "AXION", "SHELL", "PUMA", "GNC", "COMBUSTIBLE", "NAFTA", "GASOIL",
        "REFINOR", "ESTACION DE SERVICIO", "LUBRICANTES YPF")): return "COMBUSTIBLE"
    if any(k in d for k in ("SERVICE", "TALLER", "REPUESTO", "GOMERIA", "NEUMATICO", "LUBRICANTE",
        "MECANIC", "FILTRO", "ACEITE", "AUTOELEVADOR", "CUBIERTA")): return "MANTENIMIENTO"
    return "GASTO"

# --- Lector de facturas de peaje: un lector por emisor (ver docs/FORMATOS_PEAJE.md) ---
# Dinero con dos estilos: coma argentina (40.000,00) o punto (9896.82). Siempre 2 decimales.
_MONTO = r"(?:-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+[.,]\d{2})"
RE_MONTO_PEAJE = re.compile(r"(?<![\d.,])(?:" + _MONTO + r")(?![\d.,]*\d)")
RE_PASE_TAG = re.compile(r"\bSI(\d{8,14})(?:-[A-Z]{1,3})?\b")
RE_PATENTE = re.compile(r"\b(?:[A-Z]{2}\d{3}[A-Z]{2}|[A-Z]{3}\d{3})\b")
RE_CABECERA_DETALLE = re.compile(r"^\s*Cant(?:idad)?\b.*(?:Descripci[oó]n|Precio)", re.I)
RE_FIN_DETALLE = re.compile(r"^\s*(Su saldo|TOTAL|CAE\b|Por falta|SUBTOTAL|Subtotal|Total s/|Comprobante|La presente|\*Redondeo|Esta Agencia|Vencimiento)", re.I)
RE_SOLO_NUMEROS = re.compile(r"^[\d.,\s\-]*(?:Bs\.As\.|Total)?[\d.,\s\-]*$")

# (nombre, texto del encabezado en mayusculas sin tildes, modo de lectura)
EMISORES = [
    ("AUSA", r"AUTOPISTAS URBANAS", "tag"),
    ("AUSOL (Autopistas del Sol)", r"AUTOPISTAS DEL SOL", "tag_previo"),
    ("AUBASA", r"AUTOPISTAS DE BUENOS AIRES|IMPRESO POR AUBASA", "tag"),
    ("Autovia del Mercosur", r"AUTOVIA DEL MERCOSUR", "tag"),
    ("GCO (Grupo Concesionario del Oeste)", r"GRUPO CONCESIONARIO DEL OESTE", "tag"),
    ("CEAMSE (Oeste km 22)", r"C\.?E\.?A\.?M\.?S\.?E", "tag_previo"),
    ("Caminos de las Sierras", r"CAMINOS DE LAS SIERRAS", "patente"),
    ("AP01 Santa Fe", r"AUTOPISTA PROVINCIAL AP ?01|ESTANISLAO LOPEZ", "tag"),
]

def sin_tildes(t):
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")

def norm_patente(v):
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())

def detectar_emisor(texto):
    cab = sin_tildes(texto[:2500]).upper()
    for nombre, patron, modo in EMISORES:
        if re.search(patron, cab): return nombre, modo
    return None, "tag"

def _fecha_doc(texto):
    m = re.search(r"Fecha\s*:?\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", texto) or RE_FECHA.search(texto)
    return fmt_fecha(m.group(1)) if m else ""

def _neto_declarado(texto, emisor):
    """Subtotal neto (sin IVA) que declara la factura: primera cifra de la fila de totales."""
    neto = 0.0
    for linea in texto.split("\n"):
        if not RE_SOLO_NUMEROS.match(linea): continue
        toks = RE_MONTO_PEAJE.findall(linea)
        if len(toks) >= 4:
            i = 2 if emisor == "Caminos de las Sierras" else 0   # Caminos: Total s/desc | descuento | Subtotal
            neto = norm_monto(toks[i])
    return neto

def _limpiar_concepto(seg):
    seg = re.sub(r"^\s*GASTOS ADMINISTRATIVOS\s+" + _MONTO + r"\s*", "", seg, flags=re.I)
    seg = re.sub(r"^\s*\d+\s+", "", seg)
    return re.sub(r"\s{2,}", " ", seg).strip(" -|$") or "Peaje"

def parsear_factura_peajes(archivo):
    """Lee una factura de peaje (PDF con texto). Devuelve dict: emisor, items, ajustes, neto, total_doc."""
    return analizar_texto_peajes(texto_pdf(archivo))

def analizar_texto_peajes(texto):
    emisor, modo = detectar_emisor(texto)
    fecha_doc = _fecha_doc(texto)
    items, ajustes = [], []
    en_detalle, tag_actual = False, None
    for linea in texto.split("\n"):
        if RE_CABECERA_DETALLE.match(linea):
            en_detalle = True; continue
        if en_detalle and RE_FIN_DETALLE.match(linea):
            en_detalle = False; continue
        if not en_detalle: continue
        if modo == "patente":
            m = re.match(r"^\s*(\d+)\s*-\s*([A-Z0-9]{6,7})\s*-\s*(.*?)\s+" + _MONTO + r"\s+\(\d+[.,]\d+\)\s+(" + _MONTO + r")\s*$", linea)
            if m:
                items.append({"fecha": fecha_doc, "tag": "", "patente": norm_patente(m.group(2)),
                              "concepto": m.group(3)[:80], "monto": norm_monto(m.group(4)), "cantidad": int(m.group(1))})
            continue
        if modo == "tag_previo":
            mt = re.match(r"^\s*SI(\d{8,14})\b", linea)
            if mt:
                tag_actual = norm_tag(mt.group(1)); continue
            md = re.match(r"^\s*(\d+)\s+(.+?)\s+(" + _MONTO + r")\s*$", linea) or \
                 re.match(r"^\s*()(GASTOS ADMINISTRATIVOS.*?)\s+(" + _MONTO + r")\s*$", linea, re.I)
            if md:
                monto = norm_monto(md.group(3))
                if tag_actual and md.group(1):
                    if monto != 0:
                        items.append({"fecha": fecha_doc, "tag": tag_actual, "concepto": md.group(2)[:80],
                                      "monto": monto, "cantidad": int(md.group(1))})
                else:
                    ajustes.append((md.group(2), monto))
            continue
        # modo "tag": el importe de cada pase es el numero que sigue a su TAG (hay varios pases por linea)
        ms = list(RE_PASE_TAG.finditer(linea))
        ini = 0
        for k, mt in enumerate(ms):
            resto = linea[mt.end():]
            ma = re.match(r"\s*(" + _MONTO + r")(?![\d.,]*\d)", resto)
            if not ma: continue
            seg = linea[ini:mt.start()]
            gm = re.match(r"^\s*GASTOS ADMINISTRATIVOS\s+(" + _MONTO + r")", seg, re.I)
            if gm: ajustes.append(("GASTOS ADMINISTRATIVOS", norm_monto(gm.group(1))))
            q = re.match(r"^\s*(?:GASTOS ADMINISTRATIVOS\s+" + _MONTO + r"\s*)?(\d+)\s", seg, re.I)
            monto = norm_monto(ma.group(1))
            ini = mt.end() + ma.end()
            if monto == 0: continue
            items.append({"fecha": fecha_doc, "tag": norm_tag(mt.group(1)), "concepto": _limpiar_concepto(seg)[:80],
                          "monto": monto, "cantidad": int(q.group(1)) if q else 1})
        if not ms:
            ma = re.match(r"^\s*(?:\d+\s+)?([A-Za-zÁÉÍÓÚÑ][^\d]*?)\s+(" + _MONTO + r")\s*$", linea)
            if ma and re.search(r"GASTOS|BONIF|DESC|AJUSTE|REDONDEO", ma.group(1), re.I):
                ajustes.append((ma.group(1).strip(), norm_monto(ma.group(2))))
    neto = _neto_declarado(texto, emisor)
    # AUSA: el AJUSTE CONTRIBUCION ESPECIAL va como "No gravado", fuera del neto de pases.
    aj = sum(m for n, m in ajustes if "CONTRIBUCION" not in n.upper())
    return {"emisor": emisor, "items": items, "ajustes": ajustes, "neto": neto,
            "total_doc": round(neto - aj, 2) if neto else 0.0}

def parsear_pdf_peajes(archivo):
    r = parsear_factura_peajes(archivo)
    if not r["emisor"] and not r["items"]:
        raise ValueError("no reconozco el emisor de este PDF (no es una factura de peaje conocida).")
    return r["items"], r["total_doc"], r["emisor"]

def parsear_pdf_tarjeta(archivo):
    texto = texto_pdf(archivo)
    tu = texto.upper()
    banco = next((b for b in BANCOS if b in tu), "Banco")
    titular = ""
    for pat in (r"TITULAR\W{0,5}([A-ZÑÁÉÍÓÚ][A-ZÑÁÉÍÓÚ ,.]{3,40})",
                r"SR[A]?\.?\W{1,3}([A-ZÑÁÉÍÓÚ][A-ZÑÁÉÍÓÚ ,.]{3,40})"):
        m = re.search(pat, tu)
        if m:
            titular = re.sub(r"\s{2,}", " ", m.group(1)).strip(" ,.").title()
            break
    m = RE_PERIODO.search(texto)
    periodo = fmt_fecha(m.group(2)) if m else (fmt_fecha(RE_FECHA.search(texto).group(1)) if RE_FECHA.search(texto) else "")
    m = RE_VENC.search(texto)
    venc = fmt_fecha(m.group(2)) if m else "-"
    total = 0.0
    for m in RE_TOTAL_TARJETA.finditer(texto):
        total = max(total, norm_monto(m.group(2)))
    items = []
    for linea in texto.split("\n"):
        lu = linea.upper()
        if any(p in lu for p in ("TOTAL", "SALDO", "PAGO MINIMO", "CIERRE", "VENCIMIENTO", "CUIT", "LIMITE", "TASA", "INTERES")):
            continue
        f = RE_FECHA.search(linea); montos = RE_MONTO.findall(linea)
        if not f or not montos: continue
        monto = norm_monto(montos[-1])
        if monto <= 0: continue
        desc = re.sub(r"\s{2,}", " ", RE_MONTO.sub("", RE_FECHA.sub("", linea))).strip(" -|$*")
        if len(desc) < 3: continue
        items.append({"fecha": fmt_fecha(f.group(1)), "concepto": desc[:80],
                      "categoria": categorizar(desc), "monto": monto})
    return {"banco": banco.title(), "titular": titular, "periodo": periodo,
            "vencimiento": venc, "total": total}, items

def extraer_items_peajes(arch, contenido):
    if arch.filename.lower().endswith(".pdf"):
        return parsear_pdf_peajes(io.BytesIO(contenido))
    arch.seek(0)
    campos = ["fecha", "tag", "patente", "concepto", "monto"]
    filas = leer_filas(arch, campos, "monto")
    mapa, inicio = mapear_columnas(filas, campos)
    if not mapa or "monto" not in mapa:
        raise ValueError("no se detectaron las columnas (se necesita al menos IMPORTE/MONTO).")
    items = []
    for fila in filas[inicio:]:
        get = lambda c: fila[mapa[c]] if c in mapa and mapa[c] < len(fila) else ""
        monto = norm_monto(get("monto"))
        if monto == 0: continue
        items.append({"fecha": fmt_fecha(get("fecha")), "tag": norm_tag(get("tag")),
                      "patente": str(get("patente") or "").strip().upper(),
                      "concepto": str(get("concepto") or "Peaje").strip(), "monto": monto})
    return items, 0.0, "Excel/CSV"

def guardar_archivo(nombre, tipo, total_doc, contenido, mime):
    b64 = base64.b64encode(contenido).decode() if contenido else ""
    d = db()
    # Reemplazo scopeado por empresa (equivale a un upsert por empresa_id+nombre).
    d.execute("DELETE FROM archivos WHERE empresa_id=? AND nombre=?", (emp(), nombre))
    d.execute("""INSERT INTO archivos(empresa_id,nombre,tipo,total_doc,contenido,mime,creado)
        VALUES(?,?,?,?,?,?,?)""",
        (emp(), nombre, tipo, total_doc, b64, mime, datetime.now().strftime("%d/%m/%Y %H:%M")))

# ================= AUTENTICACION =================
def emp():
    """Empresa (inquilino) del usuario logueado. Toda consulta de datos se filtra por esto."""
    return session.get("empresa_id")

@app.before_request
def requiere_login():
    if request.endpoint not in ("login", "static", "landing") and not session.get("uid"):
        return redirect("/login")

@app.route("/landing")
def landing():
    return render_template("landing.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        claves = _claves_login(email)
        if _bloqueado(claves):
            flash("Demasiados intentos fallidos. Esperá 5 minutos e intentá de nuevo.")
            return render_template("login.html"), 429
        u = db().execute("SELECT * FROM usuarios WHERE email=?", (email,)).fetchone()
        if u and check_password_hash(u["clave"], request.form.get("clave", "")):
            for k in claves: INTENTOS.pop(k, None)
            session.clear()  # anti session-fixation
            session.permanent = True
            session.update(uid=u["id"], email=u["email"], nombre=u["nombre"],
                           rol=u["rol"], empresa_id=u["empresa_id"])
            return redirect("/")
        for k in claves: INTENTOS[k].append(time.time())
        flash("Credenciales invalidas.")
    return render_template("login.html")

@app.route("/salir")
def salir():
    session.clear(); return redirect("/login")

@app.errorhandler(413)
def archivo_muy_grande(_):
    return "El archivo supera el limite permitido (25 MB). Dividilo o comprimilo.", 413

def xl(v):
    """Anti inyeccion de formulas: un texto que empieza con = + - @ (o control) se prefija con comilla."""
    if isinstance(v, str) and v and (v[0] in "=+-@" or ord(v[0]) < 32):
        return "'" + v
    return v

# ================= 1) TABLERO =================
@app.route("/")
def dashboard():
    d = db(); E = emp()
    kpi = d.execute("""SELECT COUNT(*) n, COALESCE(SUM(monto),0) total,
        COUNT(DISTINCT NULLIF(patente,'')) patentes, COUNT(DISTINCT NULLIF(dueno,'')) duenos,
        COALESCE(SUM(verificado),0) ok FROM movimientos WHERE empresa_id=?""", (E,)).fetchone()
    top = d.execute("""SELECT COALESCE(NULLIF(dueno,''),'Sin asignar') dueno, SUM(monto) t,
        COUNT(*) n FROM movimientos WHERE empresa_id=? GROUP BY 1 ORDER BY t DESC LIMIT 10""", (E,)).fetchall()
    dist = d.execute("""SELECT CASE WHEN verificado=1 THEN 'Verificados en flota'
        ELSE 'Sin match de TAG' END k, COUNT(*) n, SUM(monto) t FROM movimientos
        WHERE empresa_id=? GROUP BY 1""", (E,)).fetchall()
    ult = d.execute("SELECT * FROM movimientos WHERE empresa_id=? ORDER BY id DESC LIMIT 20", (E,)).fetchall()
    maximo = max([r["t"] for r in top], default=1)
    return render_template("dashboard.html", kpi=kpi, top=top, dist=dist, ult=ult, maximo=maximo)

# --- Endpoint JSON para modales de doble-click (drill-down) ---
@app.route("/detalle/<tipo>")
def detalle(tipo):
    d = db(); E = emp()
    arg = request.args.get("v", "")
    if tipo == "monto" or tipo == "operaciones":
        rows = d.execute("SELECT * FROM movimientos WHERE empresa_id=? ORDER BY monto DESC LIMIT 1000", (E,)).fetchall()
        titulo = "Monto total" if tipo == "monto" else "Operaciones"
    elif tipo == "camiones":
        rows = d.execute("""SELECT * FROM movimientos WHERE empresa_id=? AND id IN (
            SELECT MAX(id) FROM movimientos WHERE patente<>'' AND empresa_id=? GROUP BY patente)
            ORDER BY monto DESC""", (E, E)).fetchall()
        titulo = "Camiones activos"
    elif tipo == "duenos":
        rows = d.execute("""SELECT dueno, COUNT(*) n, SUM(monto) monto,
            COUNT(DISTINCT patente) patentes FROM movimientos WHERE dueno!='' AND empresa_id=?
            GROUP BY dueno ORDER BY monto DESC""", (E,)).fetchall()
        return jsonify(titulo="Responsables", modo="grupo",
                       filas=[dict(r) for r in rows])
    elif tipo == "dueno":
        rows = d.execute("SELECT * FROM movimientos WHERE dueno=? AND empresa_id=? ORDER BY monto DESC", (arg, E)).fetchall()
        titulo = f"Detalle: {arg}"
    else:
        rows = d.execute("SELECT * FROM movimientos WHERE empresa_id=? ORDER BY id DESC LIMIT 500", (E,)).fetchall()
        titulo = "Movimientos"
    return jsonify(titulo=titulo, modo="detalle", filas=[dict(r) for r in rows])

@app.route("/movimientos")
def movimientos():
    q = request.args.get("q", "").strip()
    dueno = request.args.get("dueno", "").strip()
    vista = request.args.get("vista", "detalle")
    d = db(); E = emp()
    if vista == "vehiculo":
        grupos = d.execute("""SELECT COALESCE(NULLIF(patente,''),'S/D') g, COUNT(*) n,
            SUM(monto) t, MAX(dueno) sub FROM movimientos WHERE empresa_id=? GROUP BY 1 ORDER BY t DESC""", (E,)).fetchall()
        return render_template("movimientos.html", vista=vista, grupos=grupos, q=q, movs=[], etiqueta="Vehiculo")
    if vista == "responsable":
        grupos = d.execute("""SELECT COALESCE(NULLIF(dueno,''),'Sin asignar') g, COUNT(*) n,
            SUM(monto) t, COUNT(DISTINCT patente) sub FROM movimientos WHERE empresa_id=? GROUP BY 1 ORDER BY t DESC""", (E,)).fetchall()
        return render_template("movimientos.html", vista=vista, grupos=grupos, q=q, movs=[], etiqueta="Responsable")
    filtro = request.args.get("filtro", "")
    args, conds = [E], ["empresa_id=?"]
    if q:
        conds.append("(tag LIKE ? OR patente LIKE ? OR dueno LIKE ? OR concepto LIKE ?)")
        args += [f"%{q}%"] * 4
    if dueno:
        conds.append("COALESCE(NULLIF(dueno,''),'Sin asignar')=?"); args.append(dueno)
    if filtro == "verificado":
        conds.append("verificado=1")
    elif filtro == "sinmatch":
        conds.append("verificado=0")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    movs = d.execute("SELECT * FROM movimientos" + where + " ORDER BY id DESC LIMIT 500", args).fetchall()
    resumen = d.execute("SELECT COUNT(*) n, COALESCE(SUM(monto),0) t FROM movimientos" + where, args).fetchone()
    return render_template("movimientos.html", vista="detalle", movs=movs, q=q or dueno,
                           qtext=q, dueno=dueno, filtro=filtro, resumen=resumen, grupos=[])

@app.route("/movimientos/borrar", methods=["POST"])
def borrar_movimientos():
    ids = [int(i) for i in request.form.getlist("ids") if str(i).isdigit()]
    arch = request.form.get("archivo")
    d = db(); E = emp()
    if ids:
        d.executemany("DELETE FROM movimientos WHERE id=? AND empresa_id=?", [(i, E) for i in ids])
    elif arch:
        d.execute("DELETE FROM movimientos WHERE archivo=? AND empresa_id=?", (arch, E))
    d.commit(); flash("Registros eliminados.")
    return redirect(request.referrer or "/movimientos")

# ================= 2) IMPORTAR =================
MAX_ARCHIVOS_CARGA = 15   # tope de archivos por carga multiple

def procesar_peaje(arch, flota_map, flota_pat):
    """Procesa UN archivo de peajes. Devuelve el resumen: archivo, emisor, pasadas, estado, mensaje."""
    r = {"archivo": arch.filename, "emisor": "", "pasadas": 0, "estado": "error", "mensaje": ""}
    try:
        contenido = arch.read()
        items, total_doc, r["emisor"] = extraer_items_peajes(arch, contenido)
    except Exception as e:
        r["mensaje"] = str(e); return r
    if not items:
        r["mensaje"] = ("sin movimientos (si es un PDF escaneado, subi el Excel/CSV)"
                        if arch.filename.lower().endswith(".pdf") else "sin movimientos")
        return r
    nuevos, total = [], 0.0
    for it in items:   # por TAG (ultimos 8 digitos); sin TAG (Caminos de las Sierras) por patente
        m = flota_map.get(it["tag"]) if it["tag"] else flota_pat.get(norm_patente(it.get("patente")))
        nuevos.append((emp(), it["fecha"], it["tag"], (m["patente"] if m else it.get("patente", "")),
                       it["concepto"], it["monto"], (m["dueno"] if m else ""),
                       (m["equipo"] if m else ""), 1 if m else 0, arch.filename, datetime.now().isoformat()))
        total += it["monto"]
    d = db()
    d.executemany("""INSERT INTO movimientos(empresa_id,fecha,tag,patente,concepto,monto,
        dueno,equipo,verificado,archivo,creado) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", nuevos)
    mime = "application/pdf" if arch.filename.lower().endswith(".pdf") else "application/vnd.ms-excel"
    guardar_archivo(arch.filename, "Peajes", total_doc, contenido, mime)
    d.commit()
    ok = sum(1 for n in nuevos if n[8])
    r["pasadas"] = len(nuevos)
    r["mensaje"] = f"{len(nuevos)} pasadas ({pesos(total)}), verificadas {ok}."
    if not total_doc:
        r["estado"] = "sin_control"
    elif abs(total_doc - total) < 1:
        r["estado"] = "ok"; r["mensaje"] += f" Control OK ({pesos(total_doc)})."
    else:
        r["estado"] = "diferencia"
        r["mensaje"] += f" Documento declara {pesos(total_doc)}, diferencia {pesos(abs(total_doc - total))}."
    return r

@app.route("/importar", methods=["GET", "POST"])
def importar():
    d = db()
    if request.method == "POST":
        archivos = [a for a in request.files.getlist("archivo") if a and a.filename]
        if not archivos:
            flash("Elegi al menos un archivo.")
            return redirect("/importar")
        omitidos = archivos[MAX_ARCHIVOS_CARGA:]
        archivos = archivos[:MAX_ARCHIVOS_CARGA]
        flota_rows = d.execute("SELECT * FROM flota WHERE empresa_id=?", (emp(),)).fetchall()   # una sola vez
        flota_map = {f["tag"]: f for f in flota_rows}
        flota_pat = {norm_patente(f["patente"]): f for f in flota_rows if f["patente"]}
        iconos = {"ok": "✔", "sin_control": "✔", "diferencia": "⚠", "error": "✖"}
        for a in archivos:   # si un archivo falla, los demas igual se procesan
            r = procesar_peaje(a, flota_map, flota_pat)
            emisor = f" [{r['emisor']}]" if r["emisor"] else ""
            flash(f"{iconos[r['estado']]} {r['archivo']}{emisor}: {r['mensaje']}")
        if omitidos:
            flash(f"⚠ Se procesaron los primeros {MAX_ARCHIVOS_CARGA} archivos; quedaron sin procesar {len(omitidos)}.")
        return redirect("/importar")
    flota_n = d.execute("SELECT COUNT(*) n FROM flota WHERE empresa_id=?", (emp(),)).fetchone()["n"]
    archivos = d.execute("""SELECT m.archivo, COUNT(*) n, SUM(m.monto) t, COALESCE(MAX(a.total_doc),0) td
        FROM movimientos m LEFT JOIN archivos a ON a.nombre=m.archivo AND a.empresa_id=?
        WHERE m.empresa_id=? GROUP BY m.archivo ORDER BY MAX(m.id) DESC""", (emp(), emp())).fetchall()
    return render_template("importar.html", archivos=archivos, flota_n=flota_n)

@app.route("/flota/plantilla")
def flota_plantilla():
    wb = Workbook(); ws = wb.active; ws.title = "Flota"
    ws.append(["NUMERO DE TAG", "PATENTE", "RESPONSABLE", "EQUIPO", "ESTADO", "MARCA"])
    ws.append(["94281610", "NTF780", "REQUIERE APROB GP", "EQUIPO 464", "ACTIVO", "IVECO"])
    ws.append(["99694502", "AB120KT", "REQUIERE APROB WM", "EQUIPO 854", "ACTIVO", "MERCEDES BENZ"])
    for col, ancho in zip("ABCDEF", (16, 12, 24, 14, 10, 18)):
        ws.column_dimensions[col].width = ancho
    b = io.BytesIO(); wb.save(b); b.seek(0)
    return send_file(b, as_attachment=True, download_name="plantilla_flota.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/flota", methods=["GET", "POST"])
def flota():
    d = db()
    if request.method == "POST" and request.files.get("archivo"):
        try:
            campos = ["tag", "patente", "dueno", "equipo", "estado", "marca"]
            filas = leer_filas(request.files["archivo"], campos, "tag")
            mapa, inicio = mapear_columnas(filas, campos)
        except Exception as e:
            mapa, inicio, filas = None, 0, []
            flash(f"Error al leer: {e}")
        if filas and (not mapa or "tag" not in mapa):
            flash("No se detectaron las columnas (se necesita al menos TAG).")
        elif mapa:
            vistos, nuevos = set(), []
            for fila in filas[inicio:]:
                get = lambda c: str(fila[mapa[c]] or "").strip() if c in mapa and mapa[c] < len(fila) else ""
                t = norm_tag(get("tag"))
                if t and t not in vistos:
                    vistos.add(t)
                    nuevos.append((emp(), t, get("patente").upper(), get("dueno"), get("equipo"), get("estado"), get("marca")))
            d.execute("DELETE FROM flota WHERE empresa_id=?", (emp(),))
            d.executemany("INSERT INTO flota(empresa_id,tag,patente,dueno,equipo,estado,marca) VALUES(?,?,?,?,?,?,?)", nuevos)
            d.commit()
            flash(f"Base actualizada: {len(nuevos)} unidades unicas.")
        return redirect("/flota")
    unidades = d.execute("SELECT * FROM flota WHERE empresa_id=? ORDER BY dueno, patente", (emp(),)).fetchall()
    return render_template("flota.html", unidades=unidades)

# ================= 3) GASTOS TARJETAS =================
def procesar_resumen(arch):
    """Procesa UN resumen de tarjeta. Devuelve (ok, mensaje); nunca corta la carga de los demas."""
    d = db()
    try:
        contenido = arch.read()
        if arch.filename.lower().endswith(".pdf"):
            meta, items = parsear_pdf_tarjeta(io.BytesIO(contenido))
        else:
            arch.seek(0); campos = ["fecha", "concepto", "monto"]
            filas = leer_filas(arch, campos, "monto")
            mapa, inicio = mapear_columnas(filas, campos)
            if not mapa or "monto" not in mapa:
                raise ValueError("no se detectaron columnas FECHA/DESCRIPCION/MONTO.")
            items = []
            for fila in filas[inicio:]:
                get = lambda c: fila[mapa[c]] if c in mapa and mapa[c] < len(fila) else ""
                monto = norm_monto(get("monto"))
                if monto == 0: continue
                desc = str(get("concepto") or "").strip()
                items.append({"fecha": fmt_fecha(get("fecha")), "concepto": desc,
                              "categoria": categorizar(desc), "monto": monto})
            meta = {"banco": "Excel", "titular": "", "periodo": "", "vencimiento": "-",
                    "total": sum(i["monto"] for i in items)}
        if not items:
            raise ValueError("El PDF parece escaneado o ilegible; subí el Excel/CSV del resumen."
                             if arch.filename.lower().endswith(".pdf") else "no se encontraron consumos en el resumen.")
        cur = d.execute("""INSERT INTO resumenes(empresa_id,archivo,banco,titular,periodo,vencimiento,total_resumen,creado)
            VALUES(?,?,?,?,?,?,?,?) RETURNING id""", (emp(), arch.filename, meta["banco"], meta["titular"], meta["periodo"],
            meta["vencimiento"], meta["total"], datetime.now().isoformat()))
        rid = cur.fetchone()["id"]
        d.executemany("INSERT INTO gastos(empresa_id,resumen_id,fecha,concepto,categoria,monto) VALUES(?,?,?,?,?,?)",
                      [(emp(), rid, i["fecha"], i["concepto"], i["categoria"], i["monto"]) for i in items])
        mime = "application/pdf" if arch.filename.lower().endswith(".pdf") else "application/vnd.ms-excel"
        guardar_archivo(arch.filename, "Resumen tarjeta", meta["total"], contenido, mime)
        d.commit()
        return True, f"{arch.filename}: resumen {meta['banco']} procesado, {len(items)} consumos, total {pesos(meta['total'])}."
    except Exception as e:
        d.rollback()
        return False, f"{arch.filename}: {e}"

@app.route("/gastos", methods=["GET", "POST"])
def gastos():
    d = db()
    if request.method == "POST":
        archivos = [a for a in request.files.getlist("archivo") if a and a.filename]
        if not archivos:
            flash("Elegi al menos un archivo.")
            return redirect("/gastos")
        for a in archivos[:MAX_ARCHIVOS_CARGA]:
            ok, msg = procesar_resumen(a)
            flash(("✔ " if ok else "✖ ") + msg)
        if len(archivos) > MAX_ARCHIVOS_CARGA:
            flash(f"⚠ Se procesaron los primeros {MAX_ARCHIVOS_CARGA} archivos; quedaron sin procesar {len(archivos) - MAX_ARCHIVOS_CARGA}.")
        return redirect("/gastos")
    E = emp()
    resumenes = d.execute("""SELECT r.*, COUNT(g.id) n, COALESCE(SUM(g.monto),0) suma,
        COALESCE(SUM(CASE WHEN g.categoria='PEAJE' THEN g.monto ELSE 0 END),0) peajes
        FROM resumenes r LEFT JOIN gastos g ON g.resumen_id=r.id
        WHERE r.empresa_id=? GROUP BY r.id ORDER BY r.id DESC""", (E,)).fetchall()
    cat = request.args.get("cat", "")
    sql = "SELECT g.*, r.banco, r.archivo FROM gastos g JOIN resumenes r ON r.id=g.resumen_id WHERE g.empresa_id=?"
    args = [E]
    if cat: sql += " AND g.categoria=?"; args.append(cat)
    detalle = d.execute(sql + " ORDER BY g.id DESC LIMIT 500", args).fetchall()
    deuda = d.execute("SELECT COALESCE(SUM(total_resumen),0) t, COUNT(*) n FROM resumenes WHERE empresa_id=?", (E,)).fetchone()
    cats = d.execute("SELECT categoria, COUNT(*) n, SUM(monto) t FROM gastos WHERE empresa_id=? GROUP BY categoria ORDER BY t DESC", (E,)).fetchall()
    return render_template("gastos.html", resumenes=resumenes, detalle=detalle, deuda=deuda, cat=cat, cats=cats)

@app.route("/gastos/borrar/<int:rid>", methods=["POST"])
def borrar_resumen(rid):
    d = db(); E = emp()
    d.execute("DELETE FROM gastos WHERE resumen_id=? AND empresa_id=?", (rid, E))
    d.execute("DELETE FROM resumenes WHERE id=? AND empresa_id=?", (rid, E))
    d.commit(); flash("Resumen eliminado.")
    return redirect("/gastos")

@app.route("/gastos/exportar")
def exportar_gastos():
    wb = Workbook(); ws = wb.active; ws.title = "Gastos"
    ws.append(["FECHA", "CONCEPTO", "CATEGORIA", "MONTO", "BANCO", "RESUMEN ORIGEN"])
    for m in db().execute("""SELECT g.*, r.banco, r.archivo FROM gastos g
        JOIN resumenes r ON r.id=g.resumen_id WHERE g.empresa_id=? ORDER BY r.id, g.id""", (emp(),)):
        ws.append([xl(v) for v in (m["fecha"], m["concepto"], m["categoria"], m["monto"], m["banco"], m["archivo"])])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"gastos_tarjetas_{datetime.now():%Y%m%d}.xlsx")

# ================= 4) REPORTES =================
@app.route("/reportes")
def reportes():
    d = db(); E = emp()
    n = d.execute("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (E,)).fetchone()["n"]
    ng = d.execute("SELECT COUNT(*) n FROM gastos WHERE empresa_id=?", (E,)).fetchone()["n"]
    historial = d.execute("SELECT nombre,tipo,total_doc,creado FROM archivos WHERE empresa_id=? ORDER BY id DESC", (E,)).fetchall()
    return render_template("reportes.html", n=n, ng=ng, historial=historial)

@app.route("/reportes/descargar/<path:nombre>")
def descargar_archivo(nombre):
    r = db().execute("SELECT contenido,mime,nombre FROM archivos WHERE nombre=? AND empresa_id=?", (nombre, emp())).fetchone()
    if not r or not r["contenido"]:
        flash("Archivo no disponible."); return redirect("/reportes")
    data = base64.b64decode(r["contenido"])
    return send_file(io.BytesIO(data), as_attachment=True, download_name=r["nombre"],
                     mimetype=r["mime"] or "application/octet-stream")

@app.route("/reportes/borrar", methods=["POST"])
def borrar_archivo():
    nombres = request.form.getlist("nombres") or ([request.form["nombre"]] if request.form.get("nombre") else [])
    d = db(); E = emp()
    for nombre in nombres:
        d.execute("DELETE FROM movimientos WHERE archivo=? AND empresa_id=?", (nombre, E))
        for r in d.execute("SELECT id FROM resumenes WHERE archivo=? AND empresa_id=?", (nombre, E)).fetchall():
            d.execute("DELETE FROM gastos WHERE resumen_id=? AND empresa_id=?", (r["id"], E))
        d.execute("DELETE FROM resumenes WHERE archivo=? AND empresa_id=?", (nombre, E))
        d.execute("DELETE FROM archivos WHERE nombre=? AND empresa_id=?", (nombre, E))
    d.commit(); flash(f"{len(nombres)} archivo(s) y sus registros eliminados.")
    return redirect("/reportes")

@app.route("/exportar")
def exportar():
    wb = Workbook(); ws = wb.active; ws.title = "Movimientos"
    ws.append(["FECHA", "TAG", "PATENTE", "CONCEPTO", "MONTO", "RESPONSABLE", "EQUIPO", "VERIFICADO", "ARCHIVO"])
    for m in db().execute("SELECT * FROM movimientos WHERE empresa_id=? ORDER BY dueno, fecha", (emp(),)):
        ws.append([xl(v) for v in (m["fecha"], m["tag"], m["patente"], m["concepto"], m["monto"],
                   m["dueno"], m["equipo"], "SI" if m["verificado"] else "NO", m["archivo"])])
    ws2 = wb.create_sheet("Gastos tarjetas")
    ws2.append(["FECHA", "CONCEPTO", "CATEGORIA", "MONTO", "BANCO", "RESUMEN"])
    for m in db().execute("""SELECT g.*, r.banco, r.archivo FROM gastos g
        JOIN resumenes r ON r.id=g.resumen_id WHERE g.empresa_id=? ORDER BY r.id, g.id""", (emp(),)):
        ws2.append([xl(v) for v in (m["fecha"], m["concepto"], m["categoria"], m["monto"], m["banco"], m["archivo"])])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"reporte_maestro_{datetime.now():%Y%m%d}.xlsx")

# ================= 5) CONFIGURACION =================
@app.route("/config")
def config():
    d = db(); E = emp()
    usuarios = d.execute("SELECT id,email,nombre,rol FROM usuarios WHERE empresa_id=? ORDER BY id", (E,)).fetchall()
    stats = {
        "movs": d.execute("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (E,)).fetchone()["n"],
        "gastos": d.execute("SELECT COUNT(*) n FROM gastos WHERE empresa_id=?", (E,)).fetchone()["n"],
        "flota": d.execute("SELECT COUNT(*) n FROM flota WHERE empresa_id=?", (E,)).fetchone()["n"],
        "mb": (round(d.execute("SELECT pg_database_size(current_database()) s").fetchone()["s"] / 1048576, 2)
               if USE_PG else (round(os.path.getsize(DB) / 1048576, 2) if os.path.exists(DB) else 0)),
    }
    return render_template("config.html", usuarios=usuarios, stats=stats)

@app.route("/config/clave", methods=["POST"])
def cambiar_clave():
    nueva = request.form["nueva"].strip()
    if len(nueva) < 6:
        flash("La clave debe tener al menos 6 caracteres.")
    else:
        db().execute("UPDATE usuarios SET clave=? WHERE id=?",
                     (generate_password_hash(nueva), session["uid"]))
        db().commit(); flash("Clave actualizada.")
    return redirect("/config")

@app.route("/config/usuario", methods=["POST"])
def crear_usuario():
    if session.get("rol") not in ("admin", "superadmin"):
        flash("Solo un administrador puede crear usuarios."); return redirect("/config")
    email = request.form["email"].strip().lower()
    clave = request.form["clave"].strip()
    nombre = request.form["nombre"].strip()
    if len(clave) < 6:
        flash("La clave debe tener al menos 6 caracteres.")
    else:
        try:
            db().execute("INSERT INTO usuarios(empresa_id,email,clave,nombre) VALUES(?,?,?,?)",
                         (emp(), email, generate_password_hash(clave), nombre))
            db().commit(); flash(f"Usuario {email} creado.")
        except INTEGRITY_ERRORS:
            db().rollback(); flash("Ese correo ya existe.")
    return redirect("/config")

@app.route("/config/usuario/borrar/<int:uid>", methods=["POST"])
def borrar_usuario(uid):
    if session.get("rol") not in ("admin", "superadmin"):
        flash("Solo un administrador puede eliminar usuarios."); return redirect("/config")
    if uid == session["uid"]:
        flash("No podes eliminar tu propio usuario.")
    else:
        db().execute("DELETE FROM usuarios WHERE id=? AND empresa_id=?", (uid, emp())); db().commit()
        flash("Usuario eliminado.")
    return redirect("/config")

# ================= 6) EMPRESAS (solo superadmin) =================
@app.route("/empresas")
def empresas():
    if session.get("rol") != "superadmin":
        flash("Acceso solo para el administrador de la plataforma."); return redirect("/")
    filas = db().execute("""SELECT e.id, e.nombre, e.creado,
        (SELECT COUNT(*) FROM usuarios u WHERE u.empresa_id=e.id) usuarios,
        (SELECT COUNT(*) FROM flota f WHERE f.empresa_id=e.id) flota,
        (SELECT COUNT(*) FROM movimientos m WHERE m.empresa_id=e.id) movimientos
        FROM empresas e ORDER BY e.id""").fetchall()
    return render_template("empresas.html", empresas=filas)

@app.route("/empresas/crear", methods=["POST"])
def crear_empresa():
    if session.get("rol") != "superadmin":
        flash("Acceso solo para el administrador de la plataforma."); return redirect("/")
    nombre_emp = request.form.get("empresa", "").strip()
    email = request.form.get("email", "").strip().lower()
    clave = request.form.get("clave", "").strip()
    nombre_admin = request.form.get("nombre", "").strip()
    if not nombre_emp or not email or len(clave) < 6:
        flash("Completá empresa, email y una clave de al menos 6 caracteres.")
        return redirect("/empresas")
    d = db()
    try:
        rid = d.execute("INSERT INTO empresas(nombre,creado) VALUES(?,?) RETURNING id",
                        (nombre_emp, datetime.now().strftime("%d/%m/%Y"))).fetchone()["id"]
        d.execute("INSERT INTO usuarios(empresa_id,email,clave,nombre,rol) VALUES(?,?,?,?,?)",
                  (rid, email, generate_password_hash(clave), nombre_admin or "Administrador", "admin"))
        d.commit()
        flash(f"Empresa '{nombre_emp}' creada. Admin: {email}")
    except INTEGRITY_ERRORS:
        d.rollback(); flash("Ese correo ya está en uso por otro usuario.")
    return redirect("/empresas")

@app.route("/empresas/borrar/<int:eid>", methods=["POST"])
def borrar_empresa(eid):
    if session.get("rol") != "superadmin":
        flash("Acceso solo para el administrador de la plataforma."); return redirect("/")
    if eid == emp():
        flash("No podés borrar tu propia empresa."); return redirect("/empresas")
    d = db()
    for tabla in ("movimientos", "flota", "archivos", "resumenes", "gastos", "usuarios"):
        d.execute(f"DELETE FROM {tabla} WHERE empresa_id=?", (eid,))
    d.execute("DELETE FROM empresas WHERE id=?", (eid,))
    d.commit(); flash("Empresa y todos sus datos eliminados.")
    return redirect("/empresas")

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
