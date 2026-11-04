# Despliegue y bitácora del proyecto

> Este documento existe para que cualquier sesión futura (o persona) retome el
> proyecto sin perder contexto. Registrar acá las decisiones importantes.

## Arquitectura

- **App**: Flask + pdfplumber + openpyxl. Sin IA, sin API keys.
- **Base de datos**: doble motor, se elige por la variable de entorno `DATABASE_URL`.
  - Si `DATABASE_URL` **está** definida → usa **PostgreSQL** (producción, Supabase).
  - Si **no** está → cae a un archivo **SQLite** local `furlong.db` (desarrollo).
  - El resto del código es agnóstico al motor (ver la capa `PGConn`/`_conectar` en `app.py`).
- **Servidor de producción**: `gunicorn app:app --workers 1 --timeout 120`
  (timeout alto para PDFs grandes; 1 worker alcanza para esta carga).

## Por qué PostgreSQL/Supabase y no el disco de Render

Render **cobra** por los discos persistentes (no existen en el plan free). La versión
inicial guardaba SQLite en un disco → el Blueprint exigía tarjeta ("Payment Information
Required"). Para mantenerlo **gratis y con datos persistentes** se migró la base a
**PostgreSQL gratuito en Supabase**. Así el Web Service de Render puede ser **free**
(sin disco, sin tarjeta) y los datos viven en Supabase.

## Cómo desplegar (paso a paso)

### 1) Crear la base en Supabase
1. https://supabase.com → New project (elegir org gratuita).
2. Anotar la contraseña de la base que definís al crear el proyecto.
3. Cuando termine de aprovisionar: **Project Settings → Database → Connection string →
   "Session pooler"** (formato URI). Copiar esa URL. Se ve así:
   `postgresql://<rol>.<ref-del-proyecto>@<host-del-pooler>:5432/postgres`
   (con la contraseña incluida: `postgresql://<rol>.<ref-del-proyecto>:<contraseña>@<host-del-pooler>:5432/postgres`).
   - Si la contraseña tiene caracteres especiales (`@`, `%`, `/`, `:`), codificarlos en la URL.

### 2) Crear el Web Service en Render
1. https://dashboard.render.com → **New +** → **Blueprint**.
2. Conectar GitHub y elegir el repo `Matiasfernandez83/PeajeControl-Go` (rama `main`).
3. Render lee `render.yaml` y propone el service `tags-furlong` (plan **free**).
4. En las variables de entorno, cargar **`DATABASE_URL`** con la URL de Supabase del paso 1.
   Es una **variable secreta**: el valor se carga a mano en el panel de Render
   (Environment) y **nunca** se escribe en `render.yaml` ni en el repo.
   (`SECRET_KEY` se genera sola. `RENDER=true` lo setea Render solo → activa cookies Secure.)
5. Opcional: cargar `CONTACTO_EMAIL` y `CONTACTO_WHATSAPP` (datos que muestra la landing; ver abajo).
6. **Apply** y esperar el build (2–4 min).

### 3) Primer ingreso
- Usuario inicial: `admin@peajecontrol.com` / clave `admin`.
- Cambiar la clave desde **Configuración** apenas ingreses.

> Nota plan free de Render: el servicio se duerme tras 15 min sin uso y el primer
> acceso tarda ~30–50 s en despertar. Los datos NO se pierden (están en Supabase).

## Correr local (desarrollo)

    pip install -r requirements.txt
    python app.py            # usa SQLite (furlong.db). Abrir http://localhost:5000

Para probar contra Postgres localmente:

    DATABASE_URL='postgresql://usuario@host:puerto/base' python app.py

## Datos de contacto de la landing

La landing (`/landing`) muestra un mail y un WhatsApp. Se cambian **solo** con variables de entorno
(en Render: Environment), sin tocar código:

| Variable | Ejemplo | Qué hace |
|---|---|---|
| `CONTACTO_EMAIL` | `ventas@midominio.com` | Mail visible y link `mailto:` |
| `CONTACTO_WHATSAPP` | `+54 9 11 1234-5678` | Número visible y link `https://wa.me/<numero>` |

Si no se cargan, la landing muestra datos de ejemplo (`tuemail@peajecontrol.com` / `+54 9 11 0000-0000`).

## Estado actual del despliegue

- **Supabase**: proyecto creado (región `sa-east-1`, ref `<ref-del-proyecto>`).
  - Conviene usar un rol de aplicación dedicado (`<rol>`, con LOGIN y CREATE/USAGE en el schema `public`).
    La contraseña NO se versiona; está solo en la variable secreta `DATABASE_URL` de Render.
  - La app crea su esquema y el usuario admin sola en el primer arranque.
  - Conexión usada: **Session pooler** (IPv4), host `<host-del-pooler>:5432`,
    usuario `<rol>.<ref-del-proyecto>`. (Si Render no conecta, probar con el otro host del pooler,
    por ejemplo `aws-1-...` en vez de `aws-0-...`.)
- **Render**: Web Service `tags-furlong` ya creado (Blueprint desde `main`, plan free).
  `DATABASE_URL` está cargada como variable secreta en el panel de Render.

## Bitácora de cambios

- **Versión inicial**: Flask + SQLite + pdfplumber, 5 módulos (Tablero, Importar,
  Gastos Tarjetas, Reportes, Configuración).
- **Iteración 2**: parseo de fechas flexible (`/ - .`, años de 2 dígitos), diccionarios
  de bancos/categorías ampliados, titular más robusto. Fix: importar Excel/CSV de peajes
  (el stream se leía dos veces). UI: filtros Todos/Verificados/Sin match + barra de
  resumen en Movimientos.
- **Iteración 3 (producción)**: gunicorn `--timeout 120 --workers 1`, requirements
  pineados, `MAX_CONTENT_LENGTH` 25 MB, cookies HttpOnly/SameSite/Secure, escape XSS
  en el detalle.
- **Iteración 4 (esta)**: migración a **PostgreSQL/Supabase** manteniendo compatibilidad
  con SQLite en dev. `render.yaml` pasó a plan free sin disco, con `DATABASE_URL`.
  Probado end-to-end en ambos motores (SQLite y PostgreSQL) y bajo gunicorn.
- **Mejoras menores**: se borró el modal/endpoint de detalle que ya no se usaba (el tablero abre
  `/ver/...`), tipografía Inter propia en `static/fonts/` (sin Google Fonts), caché de estáticos de
  1 año con URLs versionadas (`?v=<huella>`), y contacto de la landing configurable por variables de entorno.
