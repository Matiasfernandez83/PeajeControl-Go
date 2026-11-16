# Tests de la etapa "legal y pulido": privacidad, terminos, og:image, favicon, paginas de error, registro de errores,
# tabla de recientes y texto de gastos. SQLite: python3 -m pytest tests -q  |  PostgreSQL: bash tests/run_pg_tests.sh
import io, os, re, struct
import pytest
import _entorno   # elige SQLite o PostgreSQL y prepara las variables antes de importar app

import app as A

app = A.app; app.config["TESTING"] = False
ADMIN, CLAVE = "admin@peajecontrol.com", os.environ["ADMIN_PASSWORD"]


@pytest.fixture(scope="module", autouse=True)
def demo_sembrada():
    A.sembrar_demo()
    yield
    con = A._conectar()
    for e in con.execute("SELECT id FROM empresas WHERE demo=1").fetchall():
        for t in A.TABLAS_TENANT:
            con.execute(f"DELETE FROM {t} WHERE empresa_id=?", (e["id"],))
        con.execute("DELETE FROM empresas WHERE id=?", (e["id"],))
    con.commit(); con.close()


def cliente_demo():
    c = app.test_client(); assert c.get("/demo").status_code == 302
    return c

def cliente_admin():
    A.INTENTOS.clear()
    c = app.test_client(); c.get("/login")
    with c.session_transaction() as s:
        tok = s["_csrf"]
    r = c.post("/login", data={"email": ADMIN, "clave": CLAVE, "_csrf": tok})
    assert r.status_code == 302
    return c


# ---------- privacidad y terminos ----------
@pytest.mark.parametrize("ruta,clave", [("/privacidad", "Ley 25.326"), ("/terminos", "no reemplaza la revisión contable")])
def test_paginas_legales_publicas(ruta, clave):
    r = app.test_client().get(ruta)
    assert r.status_code == 200
    t = r.get_data(as_text=True)
    assert clave in t
    assert "Última actualización:" in t
    assert "Texto modelo: conviene que lo revise un abogado antes de cobrar." in t

def test_privacidad_contenido_clave():
    t = app.test_client().get("/privacidad").get_data(as_text=True)
    for frag in ("Matías Federico Fernández", "AAIP", "Agencia de Acceso a la Información Pública", "cookie de sesión",
                 "demo", "datos ficticios", "Acceso", "Rectificación", "Supresión"):
        assert frag in t, frag

def test_privacidad_no_inventa_datos(monkeypatch):
    monkeypatch.delenv("LEGAL_CUIT", raising=False); monkeypatch.delenv("LEGAL_DOMICILIO", raising=False)
    monkeypatch.setenv("CONTACTO_EMAIL", "")
    t = app.test_client().get("/privacidad").get_data(as_text=True)
    assert "CUIT" not in t and "Domicilio" not in t and "mailto:" not in t
    monkeypatch.setenv("LEGAL_CUIT", "20-12345678-9"); monkeypatch.setenv("LEGAL_DOMICILIO", "Calle Falsa 123")
    t = app.test_client().get("/privacidad").get_data(as_text=True)
    assert "20-12345678-9" in t and "Calle Falsa 123" in t

@pytest.mark.parametrize("ruta", ["/landing", "/", "/login"])
def test_enlaces_legales_en_landing_y_login(ruta):
    t = app.test_client().get(ruta).get_data(as_text=True)
    assert 'href="/privacidad"' in t and 'href="/terminos"' in t

def test_enlaces_legales_en_la_app_y_la_demo():
    for c in (cliente_demo(), cliente_admin()):
        t = c.get("/").get_data(as_text=True)
        assert 'href="/privacidad"' in t and 'href="/terminos"' in t
    c = cliente_demo()   # la demo puede leer las paginas legales sin que la franja la bloquee
    assert c.get("/privacidad").status_code == 200 and c.get("/terminos").status_code == 200


# ---------- imagen para compartir e iconos ----------
def test_og_image_absoluta_y_archivo_existe():
    t = app.test_client().get("/landing").get_data(as_text=True)
    og = re.search(r'<meta property="og:image" content="([^"]+)"', t).group(1)
    assert og == "https://peajecontrol.com.ar/static/img/og-peajecontrol.png" and "?v=" not in og
    assert '<meta property="og:image:width" content="1200">' in t and '<meta property="og:image:height" content="630">' in t
    assert '<meta name="twitter:card" content="summary_large_image">' in t
    assert re.search(r'<meta name="twitter:image" content="https://peajecontrol.com.ar/static/img/', t)
    r = app.test_client().get(og.replace("https://peajecontrol.com.ar", ""))
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert struct.unpack(">II", r.data[16:24]) == (1200, 630)

def test_favicon():
    c = app.test_client()
    assert c.get("/favicon.ico").status_code == 200
    t = c.get("/landing").get_data(as_text=True)
    for ruta in re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"', t):
        assert c.get(ruta).status_code == 200, ruta


# ---------- paginas de error ----------
def _es_amigable(r, codigo):
    t = r.get_data(as_text=True)
    assert r.status_code == codigo and "Volver al inicio" in t and 'href="/"' in t and "PeajeControl" in t
    assert "Traceback" not in t and "NotFound" not in t
    return t

def test_404_sin_sesion_con_sesion_y_demo():
    for c in (app.test_client(), cliente_admin(), cliente_demo()):
        t = _es_amigable(c.get("/esto-no-existe"), 404)
        assert "No encontramos esa página" in t

def test_405_amigable():
    _es_amigable(app.test_client().get("/movimientos/borrar"), 405)   # ruta que solo acepta POST

def test_500_amigable_y_registrado(monkeypatch):
    def roto(*a, **k): raise RuntimeError("fallo con postgresql://usuario:CLAVE-SECRETA@host/db password=otra-clave")
    monkeypatch.setattr(A, "filtro_detalle", roto)
    h = next(x for x in app.logger.handlers if isinstance(x.formatter, A._FormatoSeguro))
    buf = io.StringIO(); monkeypatch.setattr(h, "stream", buf)
    c = cliente_demo()
    t = _es_amigable(c.get("/ver/monto?v=dato-sensible"), 500)
    assert "Algo salió mal" in t and "RuntimeError" not in t
    log = buf.getvalue()
    assert re.search(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ERROR GET /ver/monto uid=demo empresa=\d+ \|", log, re.M)
    assert "RuntimeError" in log and "Traceback" in log
    assert "CLAVE-SECRETA" not in log and "otra-clave" not in log and "dato-sensible" not in log

def test_429_amigable():
    with app.test_request_context("/login"):
        cuerpo, codigo = A.error_429(None)
    assert codigo == 429 and "Demasiados intentos" in cuerpo and "Volver al inicio" in cuerpo


# ---------- tablero y gastos ----------
def test_tabla_de_recientes_incluye_monto_y_oculta_equipo_en_pantallas_chicas():
    t = cliente_demo().get("/").get_data(as_text=True)
    tabla = t[t.index("Movimientos recientes"):]
    cab = re.search(r"<tr><th>Fecha</th>.*?</tr>", tabla, re.S).group(0)
    assert '<th class="num">Monto</th>' in cab
    assert 'class="ocultar-mov ocultar-ch">Equipo' in cab   # la columna secundaria se oculta en escritorio angosto
    css = open(os.path.join(A.app.static_folder, "css", "app.css"), encoding="utf-8").read()
    assert re.search(r"@media\(max-width:1400px\)\{\s*\.ocultar-ch\{display:none\}", css)

def test_texto_de_gastos_coherente():
    t = cliente_admin().get("/gastos").get_data(as_text=True)
    assert "Subí el Excel o CSV del banco" in t and "PDF o Excel del banco" not in t
    assert "Formato recomendado: Excel o CSV" in t and "solo si tiene texto" in t and "escaneados" in t
    accept = re.search(r'name="archivo" accept="([^"]+)"', t).group(1)
    assert set(accept.split(",")) == {".pdf", ".xlsx", ".csv"}

def test_tablero_sin_texto_en_ingles():
    t = cliente_demo().get("/").get_data(as_text=True)
    assert "<h1>Tablero</h1>" in t and "Dashboard" not in t and "Sistema online" not in t
