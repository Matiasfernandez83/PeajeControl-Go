/* Configuracion: cambia de solapa (General / Usuarios / Sistema) mostrando un solo panel. */
function tab(id,el){
  document.querySelectorAll('.pane').forEach(p=>p.style.display='none');
  document.getElementById(id).style.display='block';
  document.querySelectorAll('.tabs a').forEach(a=>a.classList.remove('act'));
  el.classList.add('act');
  return false;
}
