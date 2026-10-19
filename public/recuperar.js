(async function(){
 const request=document.querySelector('#request'),change=document.querySelector('#change'),status=document.querySelector('#status');
 const token=location.hash.startsWith('#token=')?decodeURIComponent(location.hash.slice(7)):'';
 if(token){request.hidden=true;change.hidden=false;history.replaceState(null,'','/recuperar');}
 try{
  const bootstrap=await (await fetch('/api/bootstrap',{cache:'no-store'})).json();
  async function send(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':bootstrap.csrf},body:JSON.stringify(data)});const obj=await r.json();if(!r.ok)throw new Error(obj.error||obj.detail||'Ha ocurrido un error');return obj}
  request.addEventListener('submit',async e=>{e.preventDefault();status.textContent='Procesando…';try{await send('/api/password/forgot',{email:document.querySelector('#email').value});status.textContent='Si la cuenta existe y el correo está configurado, recibirás un enlace. Mira también en Spam.'}catch(x){status.textContent=x.message}});
  change.addEventListener('submit',async e=>{e.preventDefault();status.textContent='Procesando…';try{await send('/api/password/reset',{token,password:document.querySelector('#password').value});status.innerHTML='Contraseña cambiada. <a href="/">Volver para iniciar sesión</a>';change.hidden=true}catch(x){status.textContent=x.message}});
 }catch(x){status.textContent='No se ha podido conectar con el servidor.'}
})();
