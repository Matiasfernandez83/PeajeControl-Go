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
