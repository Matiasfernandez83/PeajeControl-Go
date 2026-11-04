# Formatos de facturas de peaje (lector determinístico, sin IA ni OCR)

Código: `app.py` (`EMISORES`, `analizar_texto_peajes`, `norm_monto`, `norm_tag`).
Validación contra archivos reales: `python3 tests/validar_reales.py "<carpeta>"`.
Tests: `python3 tests/test_lectura_peajes.py`.

## Reglas de oro
- **TAG** = siempre los **últimos 8 dígitos** del número que sigue a `SI…` (10, 12 o 14 dígitos; se ignora el sufijo `-HN`/`-HP`).
- **Caminos de las Sierras** se identifica siempre por **patente** (no tiene TAG).
- **Dinero** con dos estilos: coma argentina (`40.000,00`, punto = miles) o punto (`9896.82`, punto = decimal). Si hay coma, la coma es el decimal; si solo hay punto, un punto con 3 dígitos detrás (`40.000`) es miles y con 2 (`9896.82`) es decimal. Se aceptan negativos (descuentos/bonificaciones).
- **Matching con la flota**: por TAG (últimos 8) y, sin TAG, por patente normalizada (mayúsculas, sin espacios ni guiones).
- Cada línea de detalle solo se lee entre el encabezado `Cant… Descripción/Precio…` y el pie (`Su saldo`, `CAE`, `TOTAL`, `Subtotal`…), para no leer números de pie de página.

## Semáforo
`total_doc` = **neto sin IVA** que declara la factura (primera cifra de la fila de totales: Subtotal / Gravado; en Caminos la tercera: Subtotal) **menos los ajustes sin TAG** que forman parte de ese neto (`GASTOS ADMINISTRATIVOS`, `BONIFICACION…`). Así se compara contra la suma de los pases. Tolerancia de $1 (las facturas redondean por línea; difieren en centavos). `AJUSTE CONTRIBUCION ESPECIAL` de AUSA es "No gravado" y no entra en el neto.
Los ajustes sin TAG no se guardan como movimientos (no pertenecen a una unidad); quedan en `ajustes` del resultado de `parsear_factura_peajes`.

## Total a pagar y bonificación (solo para control, no son movimientos)
Se guardan en `archivos.total_pagar` y `archivos.bonificacion` (0 si no se encuentra) y se muestran en /importar, /ver/factura y el modal.
`total_pagar` = importe final de la factura (con IVA e impuestos, bonificación ya descontada), sin el recargo del 5%. Patrón por emisor:
- AUBASA, Mercosur, GCO, AP01: línea `TOTAL A PAGAR <importe>` de la última hoja (se ignora `TOTAL A PAGAR * VER ULTIMA HOJA *`).
- CEAMSE y AUSOL: última cifra de la fila de totales (`... TOTAL`).
- AUSA: línea `Total` y el importe en la línea siguiente.
- Caminos de las Sierras: `... Total 967922.86` al final de la fila de totales.
`bonificacion` = suma (en positivo) de las líneas con `BONIF` (`BONIFICACION PROMO TELEPEAJE` sin TAG en AP01, `BONIFICACION TIGRE` en AUSOL). Los gastos administrativos no se descuentan ni se guardan.

## Catálogo

| # | Emisor | Detección (encabezado) | ID | Decimal | Línea típica | Particularidad |
|---|--------|------------------------|----|---------|--------------|----------------|
| 1 | AUSA | `AUTOPISTAS URBANAS` | TAG | coma | `3 CIRCUITO KDT-CAT.8 SI900097162682-HN 18.134,49` | TAG de 12 dígitos con sufijo -HN/-HP; línea `AJUSTE CONTRIBUCION ESPECIAL` (no gravado) |
| 2 | GCO – Grupo Concesionario del Oeste | `GRUPO CONCESIONARIO DEL OESTE` | TAG | coma | `5 PASADAS TELEPASE-CAT.3 SI9094204388 8216,10` | Facturas quincenales, varias hojas |
| 3 | AUBASA | `AUTOPISTAS DE BUENOS AIRES` / `IMPRESO POR AUBASA` | TAG | **punto** | `GASTOS ADMINISTRATIVOS 12.40 1 PEAJE DOCK SUD-CAT.6 SI9091354667 9896.82` | **Dos pases por línea**; el prefijo `GASTOS ADMINISTRATIVOS 12.40` no es un pase (va a ajustes) |
| 4 | Autovía del Mercosur | `AUTOVIA DEL MERCOSUR` | TAG | **punto** | `1 ZARATE-CAT 2 SI9097326424 3890.35 1 ZARATE-CAT 4 SI9097314703 7780.70` | **Varios pases por línea**: el importe es el que sigue a cada TAG |
| 5 | Caminos de las Sierras | `CAMINOS DE LAS SIERRAS` | **PATENTE** | **punto** | `3 - AC556CH - A. Pilar - Córdoba Km. 681- 5/6 ejes 6960.80 (21,00) 20882.41` | Monto = último número (neto). Patentes viejas (`MEX634`) y nuevas (`AC556CH`). Líneas de descripción cortadas (`mts.`) se ignoran. Cada hoja repite una fila de subtotal; se usa la última |
| 6 | AUSOL – Autopistas del Sol | `AUTOPISTAS DEL SOL` | TAG (encabezado) | coma | `SI9060569853 0,00` y debajo `1 CAMPANA PASADAS HP - CAT 6 3943,76` | El TAG va en una línea propia con 0,00 y los pases debajo, sin TAG. Hay bonificaciones negativas (se conservan). Pases con importe 0 se omiten |
| 7 | AP01 Santa Fe | `AUTOPISTA PROVINCIAL AP 01` / `ESTANISLAO LOPEZ` | TAG | coma | `3 TRONCAL ALDAO-CAT.4 SI9094392549 40.000,00` | Descuentos con TAG en negativo (`DESC.TRAMO CORTO`); `BONIFICACION PROMO TELEPEAJE` sin TAG (ajuste) |
| 8 | AUBASA (variante) | igual que 3 | TAG | punto | igual que 3 | La factura chica (`5459348`) es de una sola hoja con el mismo formato |
| 9 | CEAMSE – Estación Oeste km 22 | `C.E.A.M.S.E.` | TAG (línea previa) | coma | `SI9091354670` y debajo `57 TRANSACCIONES TELEPASE - CAT 7 659504,25` | El TAG va **antes** de su línea de importe y puede quedar en otra hoja. `GASTOS ADMINISTRATIVOS 9,93` primero (ajuste). Antes se la tomaba por GCO: el PDF `Cpte_787851_*` es de CEAMSE |

## No soportado (sin OCR por decisión del dueño)
- PDF escaneado o con fuente ilegible (`(cid:…)`): mensaje "El PDF parece escaneado o ilegible; subí el Excel/CSV del resumen."
- Resúmenes de tarjeta en PDF: solo lectura de Excel/CSV. `Resumen … Visa 502219012.pdf` no tiene texto y `resumen BBVA.pdf` usa fuente cid; ambos dan ese mensaje.
- `33ee69e2-….pdf` **no es un peaje ni CEAMSE**: es una factura de Starlink Argentina (tiene texto). El lector responde "no reconozco el emisor de este PDF".

## Cómo sumar un emisor nuevo
1. Extraer el texto (`pdfplumber`) y mirar el encabezado y una fila de detalle.
2. Agregar una fila a `EMISORES` con su texto de encabezado y el modo: `tag` (TAG y monto en la misma línea), `tag_previo` (TAG en línea propia, pases debajo) o `patente`.
3. Agregar un caso en `tests/test_lectura_peajes.py` y correr `tests/validar_reales.py`.
