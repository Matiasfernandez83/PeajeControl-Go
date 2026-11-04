/* Flota: modales de agregar/editar equipo y de confirmar la baja (ver templates/flota.html). */
function abrirEquipo(b){
  var f=document.getElementById('eq-form'), ed=!!b;
  document.getElementById('eq-titulo').textContent=ed?'Editar equipo':'Agregar equipo';
  f.action=ed?'/flota/editar/'+b.dataset.id:'/flota/agregar';
  ['tag','patente','dueno','equipo','estado','marca'].forEach(function(k){document.getElementById('eq-'+k).value=ed?(b.dataset[k]||''):'';});
  document.getElementById('modal-equipo').classList.add('abierto');
  document.getElementById('eq-tag').focus();
}
function cerrarEquipo(){document.getElementById('modal-equipo').classList.remove('abierto');}
function confirmarBaja(b){
  var que=[b.dataset.tag&&('TAG '+b.dataset.tag), b.dataset.patente&&('patente '+b.dataset.patente)].filter(Boolean).join(' / ');
  document.getElementById('baja-texto').textContent='Se va a eliminar el equipo '+que+'. Esta acción no se puede deshacer.';
  document.getElementById('baja-form').action='/flota/borrar/'+b.dataset.id;
  document.getElementById('modal-baja').classList.add('abierto');
}
function cerrarBaja(){document.getElementById('modal-baja').classList.remove('abierto');}
['modal-equipo','modal-baja'].forEach(function(id){var m=document.getElementById(id);
  m.addEventListener('click',function(e){if(e.target===m)m.classList.remove('abierto');});});
document.addEventListener('keydown',function(e){if(e.key==='Escape'){cerrarEquipo();cerrarBaja();}});
