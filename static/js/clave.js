/* Ojito de las claves: alterna entre mostrar y ocultar el texto de un <input type=password>.
   Lo usan login.html y las pantallas de la app (boton incluido en templates/_ojo.html). */
function verClave(btn) {
  var input = btn.parentNode.querySelector('input'),
      mostrar = input.type === 'password';
  input.type = mostrar ? 'text' : 'password';
  btn.classList.toggle('ver', mostrar);
  btn.setAttribute('aria-pressed', mostrar);
  btn.setAttribute('aria-label', mostrar ? 'Ocultar clave' : 'Mostrar clave');
}
