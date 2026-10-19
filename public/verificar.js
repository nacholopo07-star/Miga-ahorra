(async function(){
 'use strict';
 const out=document.querySelector('#message');
 const token=location.hash.startsWith('#token=')?decodeURIComponent(location.hash.substring(7)):'';
 if(token){history.replaceState(null,'','/verificar');out.textContent='Comprobando enlace de verificación…';}
 try{
  const initial=await(await fetch('/api/bootstrap',{cache:'no-store'})).json();
  async function send(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':initial.csrf},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw Error(j.error||'No se pudo completar');return j}
  if(token){try{const r=await send('/api/email/verify',{token});out.innerHTML='Correo confirmado. <a href="/">Entrar a MIGA Ahorra</a>';document.querySelector('#resend').hidden=true}catch(e){out.textContent=e.message+' Si el enlace ha caducado, puedes solicitar otro.'}}
  document.querySelector('#resend').onsubmit=async e=>{e.preventDefault();out.textContent='Comprobando…';try{const r=await send('/api/email/resend',{email:document.querySelector('#email').value});out.textContent=r.message}catch(err){out.textContent=err.message}}
 }catch(err){out.textContent='No se ha podido conectar con el servidor de MIGA.'}
})();