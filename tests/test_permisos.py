# Tests de sesion al dia, permisos por rol, claves, aislamiento entre empresas, duplicados y errores de lectura.
# SQLite: python3 -m pytest tests/test_permisos.py   |   PostgreSQL: bash tests/run_pg_tests.sh tests/test_permisos.py
import io, logging, os, re
import _entorno   # elige SQLite o PostgreSQL y prepara las variables antes de importar app
import pytest

import app as A
from openpyxl import Workbook

app = A.app
SUPER, CLAVE_SUPER = "admin@peajecontrol.com", os.environ["ADMIN_PASSWORD"]
CLAVE = "clave-larga-1"

# ---------------- ayudas ----------------
def q(sql, params=(), uno=False):
    """Consulta suelta (abre, ejecuta, confirma y devuelve la conexion al pool)."""
    con = A._conectar()
    try:
        cur = con.execute(sql, params)
        devuelve = sql.lstrip().upper().startswith("SELECT") or "RETURNING" in sql.upper()
        r = (cur.fetchone() if uno else cur.fetchall()) if devuelve else None
        con.commit()
        return r
    finally:
        con.close()

def crear_empresa(nombre):
    return q("INSERT INTO empresas(nombre,creado) VALUES(?,?) RETURNING id", (nombre, "01/01/2026"), uno=True)["id"]

def crear_usuario(eid, email, rol, clave=CLAVE):
    return q("INSERT INTO usuarios(empresa_id,email,clave,nombre,rol) VALUES(?,?,?,?,?) RETURNING id",
             (eid, email, A.generate_password_hash(clave), email.split("@")[0], rol), uno=True)["id"]

def n(sql, params=()):
    return q(sql, params, uno=True)["n"]

def token(c, ruta="/login"):
    c.get(ruta)
    with c.session_transaction() as s:
        return s["_csrf"]

def entrar(email, clave=CLAVE):
    A.INTENTOS.clear()
    c = app.test_client()
    r = c.post("/login", data={"email": email, "clave": clave, "_csrf": token(c)})
    assert r.status_code == 302 and not r.headers["Location"].endswith("/login"), f"no pudo entrar {email}"
    return c

def post(c, ruta, data=None, ref="/config"):
    d = dict(data or {}); d["_csrf"] = token(c, ref)
    return c.post(ruta, data=d, content_type="multipart/form-data")

def seguir(c, r):
    assert r.status_code == 302, r.status_code
    return c.get(r.headers["Location"]).get_data(as_text=True)

def xlsx(filas):
    wb = Workbook(); ws = wb.active
    for f in filas: ws.append(f)
    b = io.BytesIO(); wb.save(b); b.seek(0); return b

def peajes(tag="94281610", monto=1500):
    return xlsx([["FECHA", "TAG", "PATENTE", "CONCEPTO", "IMPORTE"], ["01/03/2026", tag, "AB123CD", "Peaje X", monto]])

def csv_resumen(monto="250,00"):
    return io.BytesIO(f"FECHA;DESCRIPCION;MONTO\n01/03/2026;PEAJE AUSA;{monto}\n".encode())

@pytest.fixture(scope="module")
def mundo():
    """Dos empresas con admin y operador cada una (mas un superadmin para el aislamiento y los permisos)."""
    ea, eb = crear_empresa("Perm A SA"), crear_empresa("Perm B SA")
    ids = {"ea": ea, "eb": eb,
           "adm_a": crear_usuario(ea, "perm-adm-a@t.com", "admin"), "op_a": crear_usuario(ea, "perm-op-a@t.com", "operador"),
           "adm_b": crear_usuario(eb, "perm-adm-b@t.com", "admin"), "op_b": crear_usuario(eb, "perm-op-b@t.com", "operador"),
           "sup_a": crear_usuario(ea, "perm-sup-a@t.com", "superadmin")}
    yield ids
    for t in ("movimientos", "flota", "archivos", "resumenes", "gastos", "usuarios"):
        for e in (ea, eb): q(f"DELETE FROM {t} WHERE empresa_id=?", (e,))
    for e in (ea, eb): q("DELETE FROM empresas WHERE id=?", (e,))

def cargar_datos(c):
    """Deja en la empresa del cliente: 1 factura de peajes (con su archivo), 1 resumen de tarjeta y 1 equipo."""
    r = post(c, "/importar", {"archivo": (peajes(), "fac.xlsx")}, "/importar"); assert r.status_code == 302
    r = post(c, "/gastos", {"archivo": (csv_resumen(), "res.csv")}, "/gastos"); assert r.status_code == 302
    post(c, "/flota/agregar", {"tag": "11112222", "patente": "ZZ111ZZ", "dueno": "X"}, "/flota")

# ---------------- 1) sesion revalidada ----------------
def test_usuario_borrado_con_sesion_abierta_queda_afuera(mundo):
    uid = crear_usuario(mundo["ea"], "perm-fugaz@t.com", "operador")
    c = entrar("perm-fugaz@t.com")
    tk = token(c, "/importar")                      # token CSRF valido de la sesion abierta
    assert c.get("/movimientos").status_code == 200
    q("DELETE FROM usuarios WHERE id=?", (uid,))
    antes = n("SELECT COUNT(*) n FROM archivos WHERE empresa_id=?", (mundo["ea"],))
    # un POST con token valido pero usuario inexistente: no importa nada y lo manda al login
    r = c.post("/importar", data={"_csrf": tk, "archivo": (peajes("55556666"), "intruso.xlsx")}, content_type="multipart/form-data")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    assert n("SELECT COUNT(*) n FROM archivos WHERE empresa_id=?", (mundo["ea"],)) == antes
    assert n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=? AND archivo='intruso.xlsx'", (mundo["ea"],)) == 0
    with c.session_transaction() as s:
        assert "uid" not in s
    for ruta in ("/movimientos", "/reportes", "/flota", "/exportar", "/ver/monto", "/config"):   # y no ve nada
        r = c.get(ruta); assert r.status_code == 302 and r.headers["Location"].endswith("/login"), ruta

def test_aviso_al_cerrar_la_sesion(mundo):
    uid = crear_usuario(mundo["ea"], "perm-aviso@t.com", "operador")
    c = entrar("perm-aviso@t.com")
    q("DELETE FROM usuarios WHERE id=?", (uid,))
    r = c.get("/flota"); assert r.status_code == 302
    assert "Tu sesión ya no es válida" in c.get(r.headers["Location"]).get_data(as_text=True)

def test_empresa_borrada_con_sesion_abierta_queda_afuera():
    e = crear_empresa("Efimera SA"); crear_usuario(e, "perm-efimera@t.com", "admin")
    c = entrar("perm-efimera@t.com")
    assert c.get("/flota").status_code == 200
    q("DELETE FROM empresas WHERE id=?", (e,))   # solo la empresa: el usuario queda huerfano
    r = c.get("/flota")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    q("DELETE FROM usuarios WHERE empresa_id=?", (e,))

def test_cambio_de_rol_se_aplica_al_toque(mundo):
    uid = crear_usuario(mundo["ea"], "perm-ascenso@t.com", "operador")
    c = entrar("perm-ascenso@t.com")
    post(c, "/flota/agregar", {"tag": "77778888", "patente": "RR777RR"}, "/flota")
    fid = q("SELECT id FROM flota WHERE tag='77778888' AND empresa_id=?", (mundo["ea"],), uno=True)["id"]
    post(c, f"/flota/borrar/{fid}", {}, "/flota")
    assert q("SELECT 1 x FROM flota WHERE id=?", (fid,), uno=True), "el operador no debe borrar"
    q("UPDATE usuarios SET rol='admin' WHERE id=?", (uid,))                 # lo ascienden con la sesion abierta
    post(c, f"/flota/borrar/{fid}", {}, "/flota")
    assert not q("SELECT 1 x FROM flota WHERE id=?", (fid,), uno=True), "ya es admin: tiene que poder"
    with c.session_transaction() as s:
        assert s["rol"] == "admin"
    q("UPDATE usuarios SET rol='operador' WHERE id=?", (uid,))              # y lo degradan
    post(c, "/flota/agregar", {"tag": "99990000", "patente": "DD000DD"}, "/flota")
    fid = q("SELECT id FROM flota WHERE tag='99990000' AND empresa_id=?", (mundo["ea"],), uno=True)["id"]
    post(c, f"/flota/borrar/{fid}", {}, "/flota")
    assert q("SELECT 1 x FROM flota WHERE id=?", (fid,), uno=True), "degradado: ya no puede borrar"
    assert "/empresas" not in c.get("/").get_data(as_text=True)

def test_cambio_de_empresa_se_aplica_al_toque(mundo):
    uid = crear_usuario(mundo["ea"], "perm-muda@t.com", "operador")
    c = entrar("perm-muda@t.com")
    post(c, "/flota/agregar", {"tag": "31313131", "patente": "MU111MU"}, "/flota")
    assert "MU111MU" in c.get("/flota").get_data(as_text=True)
    q("UPDATE usuarios SET empresa_id=? WHERE id=?", (mundo["eb"], uid))
    assert "MU111MU" not in c.get("/flota").get_data(as_text=True)
    with c.session_transaction() as s:
        assert s["empresa_id"] == mundo["eb"]

def test_cambio_de_clave_cierra_las_otras_sesiones(mundo):
    crear_usuario(mundo["ea"], "perm-dos@t.com", "operador")
    c1, c2 = entrar("perm-dos@t.com"), entrar("perm-dos@t.com")
    assert post(c1, "/config/clave", {"actual": CLAVE, "nueva": "otra-clave-9"}).status_code == 302
    assert c1.get("/flota").status_code == 200, "la sesion que cambio la clave sigue"
    r = c2.get("/flota"); assert r.status_code == 302 and r.headers["Location"].endswith("/login")

def test_demo_no_se_ve_afectado():
    c = app.test_client(); assert c.get("/demo").status_code in (302, 503)

# ---------------- 2) permisos por rol ----------------
def test_operador_no_borra_ni_reemplaza_pero_si_carga_y_edita(mundo):
    adm, op = entrar("perm-adm-a@t.com"), entrar("perm-op-a@t.com")
    cargar_datos(adm)
    ea = mundo["ea"]
    nmov = n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (ea,))
    rid = q("SELECT id FROM resumenes WHERE empresa_id=? AND archivo='res.csv'", (ea,), uno=True)["id"]
    fid = q("SELECT id FROM flota WHERE empresa_id=? AND tag='11112222'", (ea,), uno=True)["id"]
    nflota = n("SELECT COUNT(*) n FROM flota WHERE empresa_id=?", (ea,))
    # el operador: todo esto se rechaza con aviso
    r = post(op, "/flota", {"archivo": (xlsx([["TAG", "PATENTE"], ["12121212", "NN111NN"]]), "f.xlsx")}, "/flota")
    assert "Solo un administrador puede reemplazar la flota" in seguir(op, r)
    assert n("SELECT COUNT(*) n FROM flota WHERE empresa_id=?", (ea,)) == nflota
    r = post(op, "/movimientos/borrar", {"archivo": "fac.xlsx"}, "/importar"); r2 = r
    assert n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (ea,)) == nmov
    assert q("SELECT 1 x FROM archivos WHERE empresa_id=? AND nombre='fac.xlsx'", (ea,), uno=True)
    mid = q("SELECT id FROM movimientos WHERE empresa_id=? LIMIT 1", (ea,), uno=True)["id"]
    post(op, "/movimientos/borrar", {"ids": str(mid)}, "/movimientos")
    assert n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (ea,)) == nmov
    post(op, f"/gastos/borrar/{rid}", {}, "/gastos")
    assert q("SELECT 1 x FROM resumenes WHERE id=?", (rid,), uno=True)
    post(op, "/reportes/borrar", {"nombres": "fac.xlsx"}, "/reportes")
    assert q("SELECT 1 x FROM archivos WHERE empresa_id=? AND nombre='fac.xlsx'", (ea,), uno=True)
    post(op, f"/flota/borrar/{fid}", {}, "/flota")
    assert q("SELECT 1 x FROM flota WHERE id=?", (fid,), uno=True)
    # el operador SI puede: cargar facturas, agregar/editar equipos y ver todo
    r = post(op, "/importar", {"archivo": (peajes("20202020", 700), "fac-op.xlsx")}, "/importar")
    assert "fac-op.xlsx" in seguir(op, r)
    post(op, "/flota/agregar", {"tag": "30303030", "patente": "OP111OP"}, "/flota")
    oid = q("SELECT id FROM flota WHERE empresa_id=? AND tag='30303030'", (ea,), uno=True)["id"]
    post(op, f"/flota/editar/{oid}", {"tag": "30303030", "patente": "OP222OP", "dueno": "Edit"}, "/flota")
    assert q("SELECT patente FROM flota WHERE id=?", (oid,), uno=True)["patente"] == "OP222OP"
    for ruta in ("/", "/movimientos", "/importar", "/flota", "/gastos", "/reportes", "/config", "/exportar", "/ver/monto"):
        assert op.get(ruta).status_code == 200, ruta

def test_botones_ocultos_para_operador(mundo):
    adm, op = entrar("perm-adm-a@t.com"), entrar("perm-op-a@t.com")
    cargar_datos(adm)
    fuera = {"/movimientos": ('action="/movimientos/borrar"',), "/importar": ('action="/movimientos/borrar"',),
             "/reportes": ('action="/reportes/borrar"',), "/gastos": ('action="/gastos/borrar/',),
             "/flota": ("confirmarBaja", "Actualizar base")}
    for ruta, textos in fuera.items():
        h_op, h_adm = op.get(ruta).get_data(as_text=True), adm.get(ruta).get_data(as_text=True)
        for t in textos:
            assert t not in h_op, f"{ruta}: el operador ve «{t}»"
            assert t in h_adm, f"{ruta}: el admin deberia ver «{t}»"
    assert "Agregar equipo" in op.get("/flota").get_data(as_text=True)
    cfg_op = op.get("/config").get_data(as_text=True)
    assert "/config/usuario/borrar/" not in cfg_op and "/usuarios/clave/" not in cfg_op and 'action="/config/usuario"' not in cfg_op
    assert "/usuarios/clave/" in adm.get("/config").get_data(as_text=True)

def test_admin_puede_todo_lo_suyo(mundo):
    adm = entrar("perm-adm-a@t.com"); ea = mundo["ea"]
    cargar_datos(adm)
    post(adm, "/reportes/borrar", {"nombres": "fac.xlsx"}, "/reportes")
    assert not q("SELECT 1 x FROM archivos WHERE empresa_id=? AND nombre='fac.xlsx'", (ea,), uno=True)
    assert n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=? AND archivo='fac.xlsx'", (ea,)) == 0
    rid = q("SELECT id FROM resumenes WHERE empresa_id=? AND archivo='res.csv'", (ea,), uno=True)["id"]
    post(adm, f"/gastos/borrar/{rid}", {}, "/gastos")
    assert not q("SELECT 1 x FROM resumenes WHERE id=?", (rid,), uno=True)
    r = post(adm, "/flota", {"archivo": (xlsx([["TAG", "PATENTE"], ["12121212", "NN111NN"]]), "f.xlsx")}, "/flota")
    assert "Base actualizada" in seguir(adm, r)
    assert n("SELECT COUNT(*) n FROM flota WHERE empresa_id=?", (ea,)) == 1

# ---------------- 3) claves ----------------
def test_cambiar_clave_exige_la_actual_y_8_caracteres(mundo):
    crear_usuario(mundo["ea"], "perm-clave@t.com", "operador")
    c = entrar("perm-clave@t.com")
    hash0 = q("SELECT clave FROM usuarios WHERE email='perm-clave@t.com'", uno=True)["clave"]
    assert "actual no es correcta" in seguir(c, post(c, "/config/clave", {"actual": "equivocada", "nueva": "nueva-clave-1"}))
    assert "actual no es correcta" in seguir(c, post(c, "/config/clave", {"nueva": "nueva-clave-1"}))   # sin la actual
    assert "al menos 8" in seguir(c, post(c, "/config/clave", {"actual": CLAVE, "nueva": "corta12"}))
    assert q("SELECT clave FROM usuarios WHERE email='perm-clave@t.com'", uno=True)["clave"] == hash0
    assert "Clave actualizada" in seguir(c, post(c, "/config/clave", {"actual": CLAVE, "nueva": "nueva-clave-1"}))
    entrar("perm-clave@t.com", "nueva-clave-1")

def test_cambiar_clave_frena_los_intentos_con_la_actual(mundo):
    crear_usuario(mundo["ea"], "perm-fuerza@t.com", "operador")
    c = entrar("perm-fuerza@t.com")
    for _ in range(A.MAX_INTENTOS): post(c, "/config/clave", {"actual": "mala", "nueva": "nueva-clave-1"})
    assert "Demasiados intentos" in seguir(c, post(c, "/config/clave", {"actual": CLAVE, "nueva": "nueva-clave-1"}))
    A.INTENTOS.clear()

def test_clave_minima_en_alta_de_usuario_y_empresa(mundo):
    adm = entrar("perm-adm-a@t.com")
    assert "al menos 8" in seguir(adm, post(adm, "/config/usuario", {"email": "perm-nuevo@t.com", "clave": "1234567", "nombre": "N"}))
    assert not q("SELECT 1 x FROM usuarios WHERE email='perm-nuevo@t.com'", uno=True)
    assert "creado" in seguir(adm, post(adm, "/config/usuario", {"email": "perm-nuevo@t.com", "clave": "12345678", "nombre": "N"}))
    sup = entrar(SUPER, CLAVE_SUPER)
    post(sup, "/empresas/crear", {"empresa": "Perm Corta SA", "email": "perm-corta@t.com", "clave": "1234567", "nombre": "C"}, "/empresas")
    assert not q("SELECT 1 x FROM empresas WHERE nombre='Perm Corta SA'", uno=True)

# ---------------- 4) reset de clave ----------------
def test_admin_resetea_a_su_operador_con_clave_generada_una_vez(mundo):
    adm = entrar("perm-adm-a@t.com")
    r = post(adm, f"/usuarios/clave/{mundo['op_a']}", {"nueva": ""})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    clave = re.search(r'user-select:all">([^<]+)<', r.get_data(as_text=True)).group(1)
    assert len(clave) >= A.MIN_CLAVE
    entrar("perm-op-a@t.com", clave)
    assert clave not in adm.get("/config").get_data(as_text=True), "la clave temporal no se vuelve a mostrar"
    q("UPDATE usuarios SET clave=? WHERE id=?", (A.generate_password_hash(CLAVE), mundo["op_a"]))

def test_reset_con_clave_escrita_y_sus_limites(mundo):
    adm = entrar("perm-adm-a@t.com")
    assert "al menos 8" in seguir(adm, post(adm, f"/usuarios/clave/{mundo['op_a']}", {"nueva": "corta"}))
    assert "actualizada" in seguir(adm, post(adm, f"/usuarios/clave/{mundo['op_a']}", {"nueva": "escrita-por-admin"}))
    entrar("perm-op-a@t.com", "escrita-por-admin")
    q("UPDATE usuarios SET clave=? WHERE id=?", (A.generate_password_hash(CLAVE), mundo["op_a"]))
    assert "propia clave" in seguir(adm, post(adm, f"/usuarios/clave/{mundo['adm_a']}", {"nueva": "otra-clave-larga"}))   # la propia: por /config/clave

def test_reset_respeta_el_aislamiento_entre_empresas(mundo):
    adm_a = entrar("perm-adm-a@t.com")
    for objetivo in ("op_b", "adm_b"):
        r = post(adm_a, f"/usuarios/clave/{mundo[objetivo]}", {"nueva": "hackeada-123"})
        assert "no encontrado" in seguir(adm_a, r)
    with pytest.raises(AssertionError):
        entrar("perm-op-b@t.com", "hackeada-123")
    with pytest.raises(AssertionError):
        entrar("perm-adm-b@t.com", "hackeada-123")
    # ni a un superadmin de su propia empresa
    post(adm_a, f"/usuarios/clave/{mundo['sup_a']}", {"nueva": "hackeada-123"})
    with pytest.raises(AssertionError):
        entrar("perm-sup-a@t.com", "hackeada-123")
    # ni el operador resetea a nadie
    op_a = entrar("perm-op-a@t.com")
    post(op_a, f"/usuarios/clave/{mundo['adm_a']}", {"nueva": "hackeada-123"})
    with pytest.raises(AssertionError):
        entrar("perm-adm-a@t.com", "hackeada-123")

def test_superadmin_resetea_a_cualquier_empresa_desde_empresas(mundo):
    sup = entrar(SUPER, CLAVE_SUPER)
    html = sup.get("/empresas").get_data(as_text=True)
    assert f"/usuarios/clave/{mundo['op_b']}" in html and "perm-op-b@t.com" in html
    r = post(sup, f"/usuarios/clave/{mundo['op_b']}", {"nueva": "puesta-por-super", "volver": "empresas"}, "/empresas")
    assert r.headers["Location"].endswith("/empresas")
    entrar("perm-op-b@t.com", "puesta-por-super")
    r = post(sup, f"/usuarios/clave/{mundo['adm_b']}", {"nueva": "", "volver": "empresas"}, "/empresas")
    assert r.status_code == 200 and "perm-adm-b@t.com" in r.get_data(as_text=True)
    q("UPDATE usuarios SET clave=? WHERE id IN (?,?)", (A.generate_password_hash(CLAVE), mundo["op_b"], mundo["adm_b"]))

def test_reset_cierra_la_sesion_abierta_del_usuario(mundo):
    c_op = entrar("perm-op-a@t.com"); adm = entrar("perm-adm-a@t.com")
    post(adm, f"/usuarios/clave/{mundo['op_a']}", {"nueva": "reseteada-1234"})
    r = c_op.get("/flota"); assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    q("UPDATE usuarios SET clave=? WHERE id=?", (A.generate_password_hash(CLAVE), mundo["op_a"]))

def test_admin_de_empresa_no_borra_a_un_superadmin(mundo):
    adm = entrar("perm-adm-a@t.com")
    post(adm, f"/config/usuario/borrar/{mundo['sup_a']}", {})
    assert q("SELECT 1 x FROM usuarios WHERE id=?", (mundo["sup_a"],), uno=True)

# ---------------- 5) configuracion ----------------
def _kpi(html, etiqueta):
    m = re.search(re.escape(etiqueta) + r'</div>.*?class="v">([^<]+)<', html, re.S)
    return m.group(1) if m else None

def test_config_tamano_de_base_solo_superadmin_y_datos_solo_de_su_empresa(mundo):
    adm_a, adm_b, sup = entrar("perm-adm-a@t.com"), entrar("perm-adm-b@t.com"), entrar(SUPER, CLAVE_SUPER)
    cargar_datos(adm_a)
    h_a, h_b, h_s = (c.get("/config").get_data(as_text=True) for c in (adm_a, adm_b, sup))
    assert "Base de datos" not in h_a and "Base de datos" not in h_b and " MB" not in h_a
    assert "MB" in h_s and "Base de datos" in h_s
    assert _kpi(h_a, "Archivos cargados") == str(n("SELECT COUNT(*) n FROM archivos WHERE empresa_id=?", (mundo["ea"],)))
    assert _kpi(h_b, "Archivos cargados") == str(n("SELECT COUNT(*) n FROM archivos WHERE empresa_id=?", (mundo["eb"],)))
    assert _kpi(h_a, "Movimientos") == str(n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (mundo["ea"],)))
    assert _kpi(h_a, "Archivos cargados") != _kpi(h_b, "Archivos cargados")

# ---------------- 6) archivos duplicados ----------------
def _hay_indice_unico():
    if A.USE_PG:
        return bool(q("SELECT 1 x FROM pg_indexes WHERE indexname='ux_arch_emp_huella'", uno=True))
    con = A._conectar()
    try:
        return any(i["name"] == "ux_arch_emp_huella" and i["unique"] for i in con.execute("PRAGMA index_list(archivos)").fetchall())
    finally:
        con.close()

def test_indice_unico_parcial_existe_y_es_idempotente():
    assert _hay_indice_unico()
    con = A._conectar()
    try:
        A._indice_unico_huella(con); A._indice_unico_huella(con); A.migrar(con)   # repetirlo no rompe nada
    finally:
        con.close()
    assert _hay_indice_unico()

def test_indice_unico_reglas():
    e1, e2 = crear_empresa("Idx 1"), crear_empresa("Idx 2")
    try:
        ins = lambda e, h: q("INSERT INTO archivos(empresa_id,nombre,tipo,huella) VALUES(?,?,?,?)", (e, f"a{h}{e}", "Peajes", h))
        ins(e1, "H1"); ins(e2, "H1")          # misma huella en empresas distintas: permitido
        ins(e1, ""); ins(e1, "")              # huellas vacias: no cuentan
        con = A._conectar()
        try:
            with pytest.raises(A.INTEGRITY_ERRORS):
                con.execute("INSERT INTO archivos(empresa_id,nombre,tipo,huella) VALUES(?,?,?,?)", (e1, "otro", "Peajes", "H1"))
            con.rollback()
        finally:
            con.close()
    finally:
        for e in (e1, e2):
            q("DELETE FROM archivos WHERE empresa_id=?", (e,)); q("DELETE FROM empresas WHERE id=?", (e,))

def test_migracion_con_duplicados_previos_conserva_los_datos():
    e = crear_empresa("Idx Viejo")
    try:
        q("DROP INDEX IF EXISTS ux_arch_emp_huella")
        for i in (1, 2, 3):
            q("INSERT INTO archivos(empresa_id,nombre,tipo,huella) VALUES(?,?,?,?)", (e, f"dup{i}", "Peajes", "HDUP"))
        con = A._conectar()
        try:
            A._indice_unico_huella(con)
        finally:
            con.close()
        filas = q("SELECT nombre, huella FROM archivos WHERE empresa_id=? ORDER BY id", (e,))
        assert [f["nombre"] for f in filas] == ["dup1", "dup2", "dup3"], "no se pierde ningun archivo"
        assert [f["huella"] for f in filas] == ["HDUP", "", ""]
        assert _hay_indice_unico()
    finally:
        q("DELETE FROM archivos WHERE empresa_id=?", (e,)); q("DELETE FROM empresas WHERE id=?", (e,))

def test_misma_factura_dos_veces_y_carga_simultanea_sin_error_500(mundo, monkeypatch):
    adm = entrar("perm-adm-a@t.com"); ea = mundo["ea"]
    post(adm, "/reportes/borrar", {"nombres": "dup.xlsx"}, "/reportes")
    assert "dup.xlsx" in seguir(adm, post(adm, "/importar", {"archivo": (peajes("44445555", 321), "dup.xlsx")}, "/importar"))
    assert "ya estaba cargado" in seguir(adm, post(adm, "/importar", {"archivo": (peajes("44445555", 321), "dup.xlsx")}, "/importar"))
    # carrera: la verificacion previa no la ve (como si otra carga la estuviera guardando) -> el indice unico la frena
    monkeypatch.setattr(A, "buscar_duplicado", lambda *a, **k: None)
    nmov = n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (ea,))
    r = post(adm, "/importar", {"archivo": (peajes("44445555", 321), "dup-otro-nombre.xlsx")}, "/importar")
    assert r.status_code == 302
    assert "ya estaba cargado" in seguir(adm, r)
    assert n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (ea,)) == nmov, "no queda nada a medias"
    assert n("SELECT COUNT(*) n FROM archivos WHERE empresa_id=? AND nombre LIKE 'dup%'", (ea,)) == 1
    # lo mismo con resumenes de tarjeta
    post(adm, "/gastos", {"archivo": (csv_resumen("999,00"), "r1.csv")}, "/gastos")
    r = post(adm, "/gastos", {"archivo": (csv_resumen("999,00"), "r2.csv")}, "/gastos")
    assert r.status_code == 302 and "ya estaba cargado" in seguir(adm, r)
    assert n("SELECT COUNT(*) n FROM resumenes WHERE empresa_id=? AND archivo IN ('r1.csv','r2.csv')", (ea,)) == 1
    monkeypatch.undo()
    post(adm, "/reportes/borrar", {"nombres": ["dup.xlsx", "r1.csv"]}, "/reportes")

def test_borrar_movimientos_por_archivo_borra_tambien_el_archivo(mundo):
    adm = entrar("perm-adm-a@t.com"); ea = mundo["ea"]
    post(adm, "/importar", {"archivo": (peajes("66667777", 11), "porarch.xlsx")}, "/importar")
    assert q("SELECT 1 x FROM archivos WHERE empresa_id=? AND nombre='porarch.xlsx'", (ea,), uno=True)
    post(adm, "/movimientos/borrar", {"archivo": "porarch.xlsx"}, "/importar")
    assert n("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=? AND archivo='porarch.xlsx'", (ea,)) == 0
    assert not q("SELECT 1 x FROM archivos WHERE empresa_id=? AND nombre='porarch.xlsx'", (ea,), uno=True)
    assert "porarch.xlsx" in seguir(adm, post(adm, "/importar", {"archivo": (peajes("66667777", 11), "porarch.xlsx")}, "/importar"))   # se puede volver a cargar
    # y no toca archivos de otra empresa con el mismo nombre
    post(entrar("perm-adm-b@t.com"), "/movimientos/borrar", {"archivo": "porarch.xlsx"}, "/importar")
    assert q("SELECT 1 x FROM archivos WHERE empresa_id=? AND nombre='porarch.xlsx'", (ea,), uno=True)
    post(adm, "/reportes/borrar", {"nombres": "porarch.xlsx"}, "/reportes")

# ---------------- 7) errores crudos ----------------
def _flash_de(c, r):
    return seguir(c, r)

@pytest.mark.parametrize("ruta,nombre,contenido", [
    ("/importar", "roto.xlsx", b"esto no es un zip"),
    ("/importar", "roto.pdf", b"%PDF-1.4 basura que no es un pdf"),
    ("/gastos", "roto.xlsx", b"PK\x03\x04 truncado"),
    ("/gastos", "roto.pdf", b"%PDF-1.7\n\x00\x01\x02"),
    ("/flota", "roto.xlsx", b"nada de nada"),
])
def test_archivo_corrupto_da_mensaje_amigable_y_detalle_solo_al_log(mundo, caplog, ruta, nombre, contenido):
    adm = entrar("perm-adm-a@t.com")
    flota_antes = n("SELECT COUNT(*) n FROM flota WHERE empresa_id=?", (mundo["ea"],))
    with caplog.at_level(logging.ERROR, logger=app.logger.name):
        r = post(adm, ruta, {"archivo": (io.BytesIO(contenido), nombre)}, ruta)
    assert r.status_code == 302
    html = adm.get(r.headers["Location"]).get_data(as_text=True)
    assert f"No pudimos leer «{nombre}». Revisá que sea el formato correcto." in html
    for tecnico in ("Traceback", "BadZipFile", "not a zip", "openpyxl", "pdfminer", "PDFSyntaxError", "Exception", "Error:"):
        assert tecnico not in html, f"se filtro detalle tecnico: {tecnico}"
    assert any("no se pudo leer" in rec.getMessage() and rec.exc_info for rec in caplog.records), "el detalle va al log"
    assert n("SELECT COUNT(*) n FROM flota WHERE empresa_id=?", (mundo["ea"],)) == flota_antes

def test_errores_propios_siguen_con_su_mensaje_claro(mundo):
    adm = entrar("perm-adm-a@t.com")
    h = seguir(adm, post(adm, "/importar", {"archivo": (io.BytesIO(b"hola;chau\n1;2\n"), "sincolumnas.csv")}, "/importar"))
    assert "no se detectaron las columnas" in h

# ---------------- 8) hallazgos de la auditoria ----------------
def test_paginas_con_digitos_raros_no_dan_500(mundo):
    adm = entrar("perm-adm-a@t.com")
    for ruta in ("/movimientos?p=²", "/flota?p=²", "/gastos?p=³", "/ver/monto?p=" + "9" * 5000, "/movimientos?p=٣"):
        assert adm.get(ruta).status_code == 200, ruta
    assert post(adm, "/movimientos/borrar", {"ids": ["²", "9" * 5000]}, "/movimientos").status_code == 302

def test_confirmacion_de_borrar_empresa_escapa_el_nombre():
    sup = entrar(SUPER, CLAVE_SUPER)
    e = crear_empresa("Mala'); alert(1); ('")
    try:
        html = sup.get("/empresas").get_data(as_text=True)
        assert "alert(1)" in html and "\\u0027); alert(1); (\\u0027" in html and "'); alert(1)" not in html
    finally:
        q("DELETE FROM empresas WHERE id=?", (e,))
