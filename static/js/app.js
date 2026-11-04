/* PeajeControl Go - comportamiento general de la app (pantallas que extienden base.html).
   Las funciones quedan globales porque los botones del HTML las llaman con onclick="...". */

/* ----- Tema noche / dia -----
   El tema inicial lo fija un script minimo en el <head> (evita el parpadeo).
   Aca solo se cambia y se guarda en localStorage ('pc-tema'). */
function cambiarTema() {
  var html = document.documentElement,
      tema = html.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  html.setAttribute('data-theme', tema);
  try { localStorage.setItem('pc-tema', tema); } catch (e) {}
  if (window.pcRedibujar) window.pcRedibujar();   /* repinta las particulas con los colores del tema */
}

/* ----- Utilidades de formato ----- */
function pesos(n) {
  return '$' + Number(n || 0).toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/* Escapa texto antes de meterlo en HTML (los datos vienen de facturas importadas). */
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function vacioHtml(icono, titulo, ayuda) {
  return '<div class="vacio"><svg class="i"><use href="#i-' + icono + '"/></svg><b>' + esc(titulo) + '</b>' +
         (ayuda ? '<span>' + esc(ayuda) + '</span>' : '') + '</div>';
}

/* ----- Abrir el detalle en otra pestana ----- */
function abrirPestana(tipo, valor) {
  window.open('/ver/' + tipo + (valor ? '?v=' + encodeURIComponent(valor) : ''), '_blank', 'noopener');
}

/* Doble click sobre una fila con data-abrir: la abre en otra pestana (salvo que se haga sobre un control). */
document.addEventListener('dblclick', function (e) {
  if (e.target.closest('a,button,input,select,textarea,label')) return;
  var fila = e.target.closest('tr[data-abrir]');
  if (fila) window.open(fila.getAttribute('data-abrir'), '_blank', 'noopener');
});

/* ----- Modal de detalle (drill-down de los KPI) ----- */
var modalDetalle = document.getElementById('modal');
var modalTitulo = document.getElementById('modal-titulo');
var modalCuerpo = document.getElementById('modal-cuerpo');

function cerrarModal() { modalDetalle.classList.remove('abierto'); }
modalDetalle.addEventListener('click', function (e) { if (e.target.id === 'modal') cerrarModal(); });

function tablaPorResponsable(filas) {
  var h = '<div class="tabla-wrap"><table><thead><tr>' +
    '<th>Responsable</th><th class="num">Operaciones</th><th class="num">Patentes</th><th class="num">Monto</th>' +
    '</tr></thead><tbody>';
  filas.forEach(function (f) {
    h += '<tr><td><b>' + esc(f.dueno) + '</b></td><td class="num">' + esc(f.n) + '</td><td class="num">' +
         esc(f.patentes) + '</td><td class="num">' + pesos(f.monto) + '</td></tr>';
  });
  return h + '</tbody></table></div>';
}

function celdaOrigen(f) {
  if (f.archivo_id) {
    return '<a class="origen" href="/archivo/' + f.archivo_id + '" target="_blank" rel="noopener" title="Ver la factura">' +
           '<svg class="i"><use href="#i-archivo"/></svg><span>' + esc(f.archivo) + '</span></a>';
  }
  return '<span class="origen">' + esc(f.archivo || '—') + '</span>';
}

function tablaMovimientos(filas) {
  var h = '<div class="tabla-wrap"><table><thead><tr>' +
    '<th>Fecha</th><th>TAG</th><th>Patente</th><th>Concepto</th><th>Responsable</th><th>Factura/Origen</th><th class="num">Monto</th>' +
    '</tr></thead><tbody>';
  filas.forEach(function (f) {
    h += '<tr><td>' + esc(f.fecha) + '</td><td class="mono">' + esc(f.tag) + '</td><td class="mono">' + esc(f.patente) +
         '</td><td>' + esc(f.concepto) + '</td><td>' + esc(f.dueno) + '</td><td>' + celdaOrigen(f) +
         '</td><td class="num">' + pesos(f.monto) + '</td></tr>';
  });
  return h + '</tbody></table></div>';
}

async function abrirDetalle(tipo, valor) {
  modalTitulo.textContent = 'Cargando...';
  modalCuerpo.innerHTML = vacioHtml('buscar', 'Buscando registros...');
  modalDetalle.classList.add('abierto');
  try {
    var resp = await fetch('/detalle/' + tipo + (valor ? '?v=' + encodeURIComponent(valor) : ''));
    var d = await resp.json();
    modalTitulo.textContent = d.titulo + ' (' + d.filas.length + ')';
    if (!d.filas.length) {
      modalCuerpo.innerHTML = vacioHtml('bandeja', 'Sin registros', 'No hay datos para mostrar en esta tarjeta.');
      return;
    }
    var enlace = d.pagina
      ? '<p class="mini" style="margin:12px 0"><a class="btn chico sec" href="' + esc(d.pagina) +
        '" target="_blank" rel="noopener">Abrir en pestaña nueva</a></p>'
      : '';
    modalCuerpo.innerHTML = enlace + (d.modo === 'grupo' ? tablaPorResponsable(d.filas) : tablaMovimientos(d.filas));
  } catch (e) {
    modalCuerpo.innerHTML = vacioHtml('alerta', 'No se pudo cargar', 'Probá de nuevo en unos segundos.');
  }
}
