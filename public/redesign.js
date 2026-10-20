
'use strict';
function updateMigaDashboard(){
 const count=Object.entries(state.fridge||{}).filter(([id,v])=>state.prices[id]&&Number(v.qty)>0).length;
 const meals=state.plan?.ids?.filter(id=>id!==null&&id!==undefined).length||0;
 const f=document.getElementById('dashFridge'),m=document.getElementById('dashMeals'),p=document.getElementById('dashPrice');
 if(f)f.textContent=count+' alimento'+(count!==1?'s':'');
 if(m)m.textContent=meals+' / 14 platos';
 if(p){const b=state.plan&&Object.keys(state.prices).length&&typeof basketFromPlan==='function'?basketFromPlan():null;p.textContent=b?euro(b.total):'Aún sin menú';}
 document.querySelectorAll('.bottom-nav button[data-page]').forEach(btn=>btn.classList.toggle('current',location.hash==='#'+btn.dataset.page));
 const quick=document.getElementById('quickFridge');if(quick)quick.classList.toggle('current',location.hash==='#nevera');
}
(function(){
 const tip=document.getElementById('onboardTip');if(!tip)return;
 let dismissed=null;try{dismissed=sessionStorage.getItem('miga9_tip_seen')}catch(e){/* private browsing */}
 if(!dismissed){tip.hidden=false;}
 function close(){tip.hidden=true;try{sessionStorage.setItem('miga9_tip_seen','1')}catch(e){}}
 document.getElementById('tipClose').addEventListener('click',close);
 document.getElementById('tipOpenFridge').addEventListener('click',close);
 window.addEventListener('hashchange',()=>{updateMigaDashboard();if(location.hash==='#nevera')close()});
 setTimeout(updateMigaDashboard,300);
})();

// Cada receta puede compartirse como pagina individual, preparada para buscadores.
function migaRecipeSlug(title){return title.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}
function migaRecipeUrl(r){return location.origin+'/recetas/'+migaRecipeSlug(r.title)}
async function shareMigaRecipe(r){
 const url=migaRecipeUrl(r);
 // No enviar enlaces localhost a otras personas: solo funcionan en este ordenador.
 const local=/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
 if(local){
  if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(url);toast('Enlace local copiado. Podras compartirlo cuando MIGA este publicada.');return}catch(e){}}
  toast('Para compartir con otras personas, primero publica MIGA en un dominio.');return;
 }
 if(navigator.share){try{await navigator.share({title:r.title,text:r.description,url});return}catch(e){if(e.name==='AbortError')return;}}
 if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(url);toast('Enlace de receta copiado ✓');return}catch(e){}}
 window.prompt('Copia este enlace de receta:',url);
}
function renderFeaturedRecipe(){
 const root=document.getElementById('featuredRecipe');if(!root||!state.recipes.length)return;
 // Recomendación estable durante un día sin rastrear usuarios.
 const today=new Date();const k=Math.floor(Date.UTC(today.getFullYear(),today.getMonth(),today.getDate())/86400000);
 const r=state.recipes[((k%state.recipes.length)+state.recipes.length)%state.recipes.length];
 const desc=escapeHtml(r.description),name=escapeHtml(r.title);
 root.innerHTML=`<div class="featured-info"><span class="eyebrow">✳ UNA IDEA PARA HOY</span><h2>${name}</h2><p>${desc}</p><div class="featured-metrics"><span>⏱ ${r.minutes} min</span><span>👥 ${r.servings} raciones</span><span>🛒 Desde ${euro(cost(r,'consum')/r.servings)} por ración*</span></div><div class="featured-actions"><button class="button primary" type="button" id="featuredCook">Ver receta →</button><button class="button subdued" type="button" id="featuredShare">↗ Compartir</button></div><small>* Coste orientativo de ingredientes consumidos, no de envases.</small></div><img class="featured-photo" alt="Fotografía ilustrativa de ${name}" loading="lazy" src="${escapeHtml(r.photo)}" data-fallback="${escapeHtml(r.fallback)}">`;
 setupImageFallback(root);
 document.getElementById('featuredCook').addEventListener('click',()=>openRecipe(r.id));
 document.getElementById('featuredShare').addEventListener('click',()=>shareMigaRecipe(r));
}

// Asegura el modulo destacado incluso si los datos cargan antes que este script.
if(typeof state!=="undefined" && state.recipes.length)renderFeaturedRecipe();
