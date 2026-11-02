# PeajeControl Go — Funciones del producto (checklist vivo)

Este documento es la fuente de verdad de lo que la app **tiene y debe mantener**.
Todo agente, antes de tocar el código, lo lee para no perder funciones entre cambios.
Si se agrega o cambia una función, se actualiza acá en el mismo trabajo.

- **Repo:** `Matiasfernandez83/PeajeControl-Go` (antes `tags-furlong`)
- **En vivo:** https://tags-furlong.onrender.com (Render, auto-deploy al mergear a `main`)
- **Stack:** Flask · PostgreSQL/Supabase en prod (SQLite en local) · pdfplumber · **sin IA, sin API keys**

## Módulos
- [x] **Tablero**: KPIs (monto, camiones, responsables, operaciones) con **doble-click** que abre modal de detalle; gráficos por responsable; verificación de TAGs.
- [x] **Importar**: sube facturas de peajes en PDF/Excel/CSV; extrae pases; cruza cada TAG contra la flota por los últimos 8 dígitos; **semáforo de control** (suma vs total del documento).
- [x] **Gastos Tarjetas**: sube resúmenes bancarios; tarjetas por banco; deuda consolidada; vencimientos; categorización (PEAJE/COMBUSTIBLE/MANTENIMIENTO/GASTO).
- [x] **Reportes**: historial de archivos, **re-descarga** del PDF original, export maestro a Excel.
- [x] **Configuración**: gestión de usuarios de la empresa, cambio de clave, estado del sistema.
- [x] **Empresas** (solo superadmin): alta/baja de empresas cliente y su admin.
- [x] **Landing** pública en `/landing`.

## Multi-empresa (multi-tenant)
- [x] Cada empresa con datos **aislados** por `empresa_id` en TODAS las consultas (una empresa nunca ve, descarga ni borra datos de otra).
- [x] Registro/alta de empresas con su primer admin.
- [x] Roles: `superadmin` (plataforma), `admin` (empresa), `operador`.

## Seguridad (implementado)
- [x] Contraseñas hasheadas (werkzeug).
- [x] **CSRF** en todos los formularios POST (token por sesión).
- [x] **Headers**: X-Frame-Options, nosniff, Referrer-Policy, CSP, HSTS (prod).
- [x] **SECRET_KEY** sin fallback fijo.
- [x] **Rate limiting** de login (5 intentos / 5 min).
- [x] Cookies Secure/HttpOnly/SameSite; sesión 8 h; regeneración al loguear.
- [x] Admin inicial con clave aleatoria/ADMIN_PASSWORD (nunca "admin").
- [x] Límite de subida 25 MB; sanitización anti-fórmulas en export Excel.
- [x] XSS del modal de detalle escapado.

## UX
- [x] **Ojito** para ver/ocultar la contraseña en TODOS los campos de clave (login, cambiar clave, crear usuario, crear empresa).
- [x] Responsive (escritorio, tablet, celular).

## Calidad
- [x] `tests/test_security.py` corre con SQLite local (`python3 tests/test_security.py`). Mantener en verde ante cada cambio.

## Pendientes / ideas (sin hacer)
- [ ] Dominio propio `.com.ar` + hosting pago (sacar el cold-start del plan free).
- [ ] Logs de auditoría (login y acciones sensibles).
- [ ] Backups de la base con prueba de restauración.
