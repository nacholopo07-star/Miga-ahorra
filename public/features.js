/* MIGA Ahorra 8 · Despensa inteligente, optimizador de presupuesto y comparador de cesta.
   No usa servicios de terceros, excepto enlaces a las tiendas. */
'use strict';

function fridgeUsable(item){
  if(!item || !Number.isFinite(Number(item.qty)) || Number(item.qty)<=0)return false;
  if(!item.expiry)return true;
  const date=new Date(`${item.expiry}T23:59:59`);
  return !Number.isNaN(date.getTime())&&date.getTime()>=Date.now();
}
function fridgeDays(item){
  if(!item?.expiry)return null;
  const d=new Date(`${item.expiry}T23:59:59`);
  return Number.isNaN(d.getTime())?null:Math.ceil((d.getTime()-Date.now())/86400000);
}
function fridgeName(id){return state.prices[id]?.name||id}
function fridgeInventory(){return Object.entries(state.fridge||{}).filter(([k,p])=>state.prices[k]&&Number(p?.qty)>0).sort(([ka,a],[kb,b])=>{let da=fridgeDays(a),db=fridgeDays(b);return (da===null?99999:da)-(db===null?99999:db)||fridgeName(ka).localeCompare(fridgeName(kb),'es')})}
function fridgeSave(){
  const clean=Object.fromEntries(fridgeInventory().map(([id,d])=>[id,{qty:Number(d.qty),expiry:d.expiry||''}]));
  state.fridge=clean;
  if(!state.user){try{localStorage.setItem('miga_fridge_guest',JSON.stringify(clean))}catch{};return}
  clearTimeout(fridgeSave.timer);
  fridgeSave.timer=setTimeout(()=>api('/api/pantry',{items:Object.entries(clean).map(([id,d])=>({id,qty:d.qty,expiry:d.expiry}))}).catch(e=>toast('No se guardó la nevera en tu cuenta: '+e.message)),300);
}
function fridgeBadge(item){
  const days=fridgeDays(item);
  if(days===null)return `<span class="fridge-status neutral">Sin fecha</span>`;
  if(days<0)return `<span class="fridge-status expired">Fecha superada · revisar</span>`;
  if(days<=3)return `<span class="fridge-status soon">${days===0?'Fecha de hoy':`Quedan aprox. ${days} día${days===1?'':'s'}`}</span>`;
  return `<span class="fridge-status fresh">${escapeHtml(item.expiry)}</span>`;
}
function renderFridge(){
  if(!state.prices||!Object.keys(state.prices).length||!el('fridgeFood'))return;
  const options=Object.entries(state.prices).sort((a,b)=>a[1].name.localeCompare(b[1].name,'es'));
  const selected=el('fridgeFood').value;
  el('fridgeFood').innerHTML=options.map(([id,p])=>`<option value="${id}" ${selected===id?'selected':''}>${escapeHtml(p.name)}</option>`).join('');
  const selectedP=state.prices[el('fridgeFood').value];
  el('fridgeUnit').textContent=selectedP?.unit||'g';
  const inventory=fridgeInventory(),safe=inventory.filter(([,d])=>fridgeUsable(d)),urgent=inventory.filter(([,d])=>fridgeDays(d)!==null&&fridgeDays(d)<=3),today=inventory.filter(([,d])=>{let x=fridgeDays(d);return x!==null&&x>=0&&x<=3});
  const saved=inventory.reduce((sum,[k,d])=>{if(!fridgeUsable(d))return sum;const p=state.prices[k];return sum+d.qty/p.pack*(p.stores.consum.price||0)},0);
  el('fridgeMetrics').innerHTML=`<div><span>🥬</span><strong>${inventory.length}</strong><small>Alimentos registrados</small></div><div><span>✅</span><strong>${safe.length}</strong><small>Con cantidad disponible</small></div><div><span>⏳</span><strong>${urgent.length}</strong><small>Con fecha próxima o pasada</small></div><div><span>💶</span><strong>${euro(saved)}</strong><small>Valor proporcional orientativo de existencias</small></div>`;
  el('fridgePriority').innerHTML=today.length?`<p class="priority-lead">Puedes dar prioridad a estos ingredientes en tus próximas comidas:</p><div class="priority-list">${today.map(([id,d])=>`<div class="priority-line"><b>${escapeHtml(fridgeName(id))}</b>${fridgeBadge(d)}</div>`).join('')}</div>`:urgent.length?`<div class="fridge-attention">⚠ Hay ingredientes cuya fecha has indicado como pasada. No se usan para calcular qué puedes cocinar.</div>`:'<div class="fridge-empty-priority">🌿 Nada urgente por consumir. Registra fechas si quieres recibir sugerencias de aprovechamiento.</div>';
  el('fridgeList').innerHTML=inventory.length?inventory.map(([id,d])=>`<article class="fridge-item"><div class="fridge-item-icon">${plateIcon(id)}</div><div class="fridge-item-text"><b>${escapeHtml(fridgeName(id))}</b><small>${fmtQty(d.qty,state.prices[id].unit)} · ${fridgeUsable(d)?'Se descuenta de tu compra':'No se descuenta por la fecha'}</small>${fridgeBadge(d)}</div><div class="fridge-actions"><button class="fridge-edit" data-fridge-edit="${id}" title="Modificar cantidad y fecha">Editar</button><button class="fridge-delete" data-fridge-remove="${id}" title="Quitar de la nevera">✕</button></div></article>`).join(''):`<div class="fridge-empty">🧺<strong>Tu nevera empieza aquí</strong><span>Añade arroz, verduras, pollo o lo que tengas y MIGA calculará tu próxima compra.</span></div>`;
  $$('[data-fridge-remove]').forEach(b=>b.onclick=()=>{delete state.fridge[b.dataset.fridgeRemove];delete state.pantry[b.dataset.fridgeRemove];savePantry();fridgeSave();renderFridge();if(state.plan){renderShopping();renderPlan()};toast('Ingrediente eliminado de tu nevera.');});
  $$('[data-fridge-edit]').forEach(b=>b.onclick=()=>{const id=b.dataset.fridgeEdit,v=state.fridge[id];el('fridgeFood').value=id;el('fridgeQty').value=v.qty;el('fridgeExpiry').value=v.expiry||'';el('fridgeUnit').textContent=state.prices[id].unit;el('fridgeQty').focus();scrollTo({top:el('fridgeForm').getBoundingClientRect().top+scrollY-105,behavior:'smooth'});});
}
function fridgeUseForPlate(){
  const keys=fridgeInventory().filter(([,d])=>fridgeUsable(d)).map(([id])=>id);
  if(!keys.length){toast('Añade alimentos a tu nevera para recibir recomendaciones.');navigate('nevera');return}
  state.plateIngredients=new Set(keys);state.plateFromFridge=true;el('plateSearch').value='';navigate('crear');renderPlateBuilder();toast(`Buscando recetas con tus ${keys.length} ingredientes disponibles.`);
}
function bindFridge(){
  el('fridgeFood').onchange=e=>{el('fridgeUnit').textContent=state.prices[e.target.value]?.unit||'g'};
  el('fridgeForm').onsubmit=e=>{e.preventDefault();const id=el('fridgeFood').value,qty=Number(el('fridgeQty').value),expiry=el('fridgeExpiry').value;if(!state.prices[id]||!Number.isFinite(qty)||qty<=0||qty>200000){toast('Introduce un ingrediente y una cantidad válida.');return}state.fridge[id]={qty,expiry};delete state.pantry[id];savePantry();fridgeSave();el('fridgeForm').reset();renderFridge();if(state.plan){renderPlan();renderShopping()};toast('Añadido a tu nevera ✓');};
  el('fridgeSuggest').onclick=fridgeUseForPlate;
  el('plateFromFridge').onclick=fridgeUseForPlate;
}

// When recipes were suggested from the fridge, account for the actual quantities.
// Manual ingredient selection still behaves as before because the user may have unrecorded stock.
const basePlateRecommendation=plateRecommendation;
plateRecommendation=function(r,picks){
  if(!state.plateFromFridge)return basePlateRecommendation(r,picks);
  const list=effectiveIngredients(r),matches=[],missing=[];
  list.forEach(i=>{
    const f=state.fridge[i.id];const available=picks.has(i.id)&&fridgeUsable(f)?Number(f.qty):0;
    if(available>=i.qty)matches.push(i.id);
    else missing.push({...i,qty:Math.max(0.01,i.qty-available)});
  });
  const extraCost=missing.reduce((sum,i)=>{const p=state.prices[i.id];return sum+(p?.stores?.consum?.price||0)*i.qty/p.pack},0);
  return {r,matches,missing,extraCost,coverage:matches.length/Math.max(1,list.length)};
};

// Preferencias de ingredientes no deseados: guardado local, no afecta al catálogo.
let avoided=new Set();
function avoidKey(){return 'miga_avoid_'+(state.user?.id||'guest')}
function loadAvoided(){const v=loadLocal(avoidKey(),[]);avoided=new Set(Array.isArray(v)?v.filter(id=>state.prices[id]):[]);renderAvoided()}
function renderAvoided(){
  const list=el('avoidChips'),s=el('avoidFood');if(!list||!s||!Object.keys(state.prices).length)return;
  s.innerHTML='<option value="">Selecciona un ingrediente…</option>'+Object.entries(state.prices).filter(([id])=>!avoided.has(id)).sort((a,b)=>a[1].name.localeCompare(b[1].name,'es')).map(([id,p])=>`<option value="${id}">${escapeHtml(p.name)}</option>`).join('');
  list.innerHTML=[...avoided].map(id=>`<button type="button" data-unavoid="${id}">✕ ${escapeHtml(fridgeName(id))}</button>`).join('')||'<small>Ninguno excluido. Puedes añadir los que no te gusten.</small>';
  $$('[data-unavoid]').forEach(b=>b.onclick=()=>{avoided.delete(b.dataset.unavoid);localStorage.setItem(avoidKey(),JSON.stringify([...avoided]));renderAvoided()});
}
function bindAvoided(){el('addAvoid').onclick=()=>{const id=el('avoidFood').value;if(!id)return;avoided.add(id);localStorage.setItem(avoidKey(),JSON.stringify([...avoided]));renderAvoided();toast('Evitaré este ingrediente en los próximos menús.')};}

function basketFromIds(ids,servings,store){
  const qty={};
  (ids||[]).forEach(id=>{const r=recipe(id);if(!r)return;effectiveIngredients(r).forEach(i=>qty[i.id]=(qty[i.id]||0)+i.qty*servings/r.servings)});
  const items=Object.entries(qty).filter(([id])=>state.prices[id]).map(([id,amount])=>{
    const p=state.prices[id],have=pantryHave({key:id,qty:amount,pack:p.pack});
    return {id,qty:amount,have,pack:p.pack,needed:Math.max(0,amount-have),packs:Math.max(0,Math.ceil((amount-have-1e-6)/p.pack)),product:p};
  });
  const consum=items.reduce((sum,p)=>sum+p.packs*p.product.stores.consum.price,0),mercadona=items.reduce((sum,p)=>sum+p.packs*p.product.stores.mercadona.price,0);
  const split=items.reduce((sum,p)=>sum+p.packs*Math.min(p.product.stores.mercadona.price,p.product.stores.consum.price),0);
  const consumed=items.reduce((sum,p)=>sum+p.qty/p.pack*(p.product.stores[store]?.price||0),0);
  return {items,consum,mercadona,split,consumed,total:store==='mercadona'?mercadona:consum};
}
function basketFromPlan(p=state.plan){return p?basketFromIds(p.ids,p.servings,p.store||'consum'):null}
function ingredientCostAndOverlap(r,serv,qtyBefore,store){
  const qtyNew={...qtyBefore};
  effectiveIngredients(r).forEach(i=>{qtyNew[i.id]=(qtyNew[i.id]||0)+i.qty*serv/r.servings});
  let total=0,previous=0,extra=0;
  for(const [id,qty] of Object.entries(qtyNew)){
    const p=state.prices[id];if(!p)continue;
    const have=pantryHave({key:id,qty,pack:p.pack});const after=Math.max(0,Math.ceil((qty-have-1e-6)/p.pack));
    const oldQty=qtyBefore[id]||0;
    const haveOld=pantryHave({key:id,qty:oldQty,pack:p.pack});const before=Math.max(0,Math.ceil((oldQty-haveOld-1e-6)/p.pack));
    total+=after*p.stores[store].price;previous+=before*p.stores[store].price;
    if(oldQty>0 && after===before)extra+=.17;
  }
  return {qtyNew,addCost:Math.max(0,total-previous),total,extra};
}
function makeSmartPlan(){
  const budget=Number(el('budget').value),serv=Number(el('people').value),cat=el('diet').value,store=el('plannerStore').value;
  if(!Number.isFinite(budget)||budget<5||budget>1000||!Number.isInteger(serv)||serv<1||serv>8){toast('Revisa presupuesto (5–1000 €) y número de personas.');return}
  const eligible=state.recipes.filter(r=>(cat==='todos'||r.category===cat)&&effectiveIngredients(r).every(i=>!avoided.has(i.id)));
  if(!eligible.length){toast('Ninguna receta coincide. Quita algún ingrediente excluido.');return}
  let best=null;
  // Multiple simple deterministic greedy proposals, considering full packs, repetitions and variety.
  for(let variant=0;variant<12;variant++){
    let ids=[],qty={},counts=new Map(),groups=new Map(),total=0;
    for(let slot=0;slot<14;slot++){
      const candidates=eligible.map(r=>{
        const next=ingredientCostAndOverlap(r,serv,qty,store),repeats=counts.get(r.id)||0,group=proteinGroup(r),grp=groups.get(group)||0;
        const dayCompanion=slot%2===1?ids[slot-1]:null;
        // Dinner generally faster, but not artificially classified as healthier.
        const quality=(repeats*2.4)+(repeats>=2?5:0)+(dayCompanion===r.id?10:0)+(grp*.32)+(slot%2&&r.minutes>35?.8:0);
        const variety=(variant%3===0&&group==='legumbres'?.7:0)+(variant%4===0&&group==='pescado'?.5:0);
        const jitter=(((r.id*37+slot*17+variant*29)%31)/31)*(.7+variant*.09);
        const priceWeight=budget<45?3:budget<80?1.65:.85;
        const proteinBoost=cat==='todos'&&((group==='carne'&&grp<3&&slot>=3)||(group==='pescado'&&grp<2&&slot>=5))?1.35:0;
        return {r,group,next,score:next.addCost*priceWeight+quality+variety+jitter-next.extra-proteinBoost};
      }).sort((a,b)=>a.score-b.score);
      const candidate=candidates[0];
      ids.push(candidate.r.id);qty=candidate.next.qtyNew;total=candidate.next.total;
      counts.set(candidate.r.id,(counts.get(candidate.r.id)||0)+1);groups.set(candidate.group,(groups.get(candidate.group)||0)+1);
    }
    const nRepeat=[...counts.values()].reduce((sum,n)=>sum+Math.max(0,n-2),0);
    const diff=Math.max(0,total-budget),priceScore=total*(budget<45?1.8:budget<80?.5:.2),score=priceScore+diff*5+nRepeat*2+Math.max(0,4-groups.size)*2;
    if(!best||score<best.score)best={score,ids,total};
  }
  state.plan={ids:best.ids,servings:serv,budget,category:cat,store};
  persistPlan();renderPlan();renderShopping();
  toast(best.total>budget?'Menú creado. El precio de envases supera tu objetivo: puedes sustituir platos.':'Menú preparado dentro de tu presupuesto estimado.');
}

// Decorate the original plan view after it renders the week cards.
const baseRenderPlan=renderPlan;
renderPlan=function(){
  baseRenderPlan();
  if(!state.plan)return;
  const b=basketFromPlan(),goal=Number(state.plan.budget)||1,filled=state.plan.ids.filter(id=>id!==null).length;
  el('planCost').textContent=euro(b.total);
  el('planPer').textContent=`${filled} de 14 platos · ${state.plan.servings} persona${state.plan.servings!==1?'s':''} · envases en ${state.plan.store==='consum'?'Consum':'Mercadona'}`;
  el('budgetMeter').style.width=Math.min(100,b.total/goal*100)+'%';el('budgetMeter').classList.toggle('over',b.total>goal);
  el('budgetStatus').innerHTML=b.total>goal?`<strong class="over-label">Te pasas ${euro(b.total-goal)}</strong><span>Precio de envases. Cambia algún plato o ajusta las raciones.</span>`:`<strong>✓ ${filled===14?'Dentro del presupuesto estimado':'Presupuesto parcial'}</strong><span>Margen disponible: ${euro(goal-b.total)}</span>`;
  let pane=el('smartPlanDetails');if(!pane){pane=document.createElement('div');pane.id='smartPlanDetails';pane.className='smart-plan-details';el('budgetMeter').parentElement.after(pane)}
  let allQty=Object.fromEntries(b.items.map(i=>[i.id,i.qty]));
  let savedByFridge=b.items.reduce((sum,i)=>sum+Math.min(i.qty,i.have)/i.pack*i.product.stores[state.plan.store].price,0);
  pane.innerHTML=`<div><span>🍽 Ingredientes consumidos</span><strong>${euro(b.consumed)}</strong></div><div><span>🛒 Envases necesarios</span><strong>${euro(b.total)}</strong></div><div><span>🥬 Aprovechas de tu despensa</span><strong>${euro(savedByFridge)}</strong></div><button type="button" class="plan-compare-link" data-page="compra">Comparar la cesta en ambas tiendas ↗</button>`;
};
const baseRenderShopping=renderShopping;
renderShopping=function(){
  baseRenderShopping();
  const box=el('smartBasket');if(!box||!state.plan){if(box)box.innerHTML='';return}
  const b=basketFromPlan(),better=b.consum<=b.mercadona?'consum':'mercadona',bestSingle=Math.min(b.consum,b.mercadona),savings=bestSingle-b.split;
  const m=b.items.filter(i=>i.packs>0).sort((a,z)=>z.packs*Math.abs(z.product.stores.consum.price-z.product.stores.mercadona.price)-a.packs*Math.abs(a.product.stores.consum.price-a.product.stores.mercadona.price));
  const verified=m.filter(i=>['estimado','referencia'].includes(i.product.stores.consum.status)||['estimado','referencia'].includes(i.product.stores.mercadona.status)).length;
  box.innerHTML=`<div class="smart-basket-heading"><div><span class="eyebrow">COMPARADOR DE CESTA COMPLETA</span><h2>¿Dónde sale mejor esta compra?</h2><p>Coste de comprar los envases que faltan después de descontar tu nevera.</p></div><div class="basket-savings">✳ ${euro(Math.max(0,b.mercadona-b.consum))} de diferencia entre tiendas</div></div><div class="basket-modes"><div class="basket-mode ${better==='consum'?'cheapest':''}"><span>🟠 Toda la compra en Consum</span><strong>${euro(b.consum)}</strong><small>${better==='consum'?'✓ Menor total en una sola tienda':'Un único supermercado'}</small></div><div class="basket-mode ${better==='mercadona'?'cheapest':''}"><span>🟢 Toda la compra en Mercadona</span><strong>${euro(b.mercadona)}</strong><small>${better==='mercadona'?'✓ Menor total en una sola tienda':'Un único supermercado'}</small></div><div class="basket-mode hybrid"><span>⚖️ Dividida por el precio de cada ingrediente</span><strong>${euro(b.split)}</strong><small>${savings>0?'Ahorrarías '+euro(savings)+' frente a la tienda única más barata':'No aporta ahorro adicional'}</small></div></div><div class="basket-detail"><h3>Productos que puedes comparar</h3>${m.length?`<div class="basket-product-list">${m.map(i=>{const chosen=i.product.stores.consum.price<=i.product.stores.mercadona.price?'consum':'mercadona';const x=i.product.stores[chosen];return `<div><div><b>${escapeHtml(i.product.name)}</b><small>${i.packs} envase${i.packs===1?'':'s'} · ${fmtQty(i.pack,i.product.unit)} cada uno</small></div><div class="basket-right"><span>Menor estimación: <strong>${chosen==='consum'?'Consum':'Mercadona'}</strong> · ${euro(i.packs*x.price)}</span>${retailerLink(i.product,chosen,true)}</div></div>`}).join('')}</div>`:'<p>Ya tienes suficientes ingredientes para los platos elegidos.</p>'}</div><div class="smart-disclaimer">* Comparación orientativa de precios registrados, no tarifas en directo. ${verified} de ${m.length} ingredientes a comprar incluyen al menos un precio estimado o de fuente externa. Dividir la cesta puede exigir dos desplazamientos y no incorpora promociones, envío ni costes de transporte. Los enlaces no añaden productos automáticamente al carrito.</div>`;
};



// ¿Qué puedo cocinar hoy? Prioriza inventario REAL (unidades/cantidades) y fechas próximas.
// Aceite y especias solos no se consideran una base para cocinar un plato.
const COOK_PANTRY_BASICS=new Set(['sal','aceite','pimienta','comino','pimenton','curry','salsa_soja','caldo']);
function cookTodayRanking(r,people){
 const ingredients=effectiveIngredients(r),factor=people/(r.servings||2);
 const missing=[],available=[],urgent=[];
 let usedFraction=0,missingValue=0;
 for(const i of ingredients){
   const qty=i.qty*factor,p=state.prices[i.id],fridge=state.fridge[i.id];
   const stored=fridgeUsable(fridge)?Math.max(0,Number(fridge.qty)||0):0;
   const used=Math.min(qty,stored),short=Math.max(0,qty-stored);
   if(qty>0)usedFraction+=used/qty;
   if(used>0){available.push({id:i.id,qty:used});const days=fridgeDays(fridge);if(days!==null&&days>=0&&days<=3)urgent.push({id:i.id,qty:used,days});}
   if(short>0.001){missing.push({id:i.id,qty:short});missingValue+=(p?.stores?.consum?.price||0)*(short/(p?.pack||1));}
 }
 const coverage=ingredients.length?usedFraction/ingredients.length:0;
 return {r,missing,available,urgent,coverage,missingValue,ready:missing.length===0,
   score:(missing.length===0?120:0)+coverage*80+urgent.length*18-4*missing.length-Math.min(30,missingValue*3)};
}
function renderCookToday(){
 const target=el('cookTodayResults'),count=el('cookTodayCount');if(!target||!state.prices||!state.recipes.length)return;
 const list=fridgeInventory().filter(([,data])=>fridgeUsable(data));
 if(list.length&&!list.some(([id])=>!COOK_PANTRY_BASICS.has(id))){
  if(count)count.textContent='Necesitamos algún alimento principal.';
  target.innerHTML='<div class="cook-empty">Añade arroz, verduras, legumbres, carne, pescado u otro alimento principal. Solo aceite, sal o especias no bastan para recomendar platos completos.</div>';return;
 }
 if(!list.length){if(count)count.textContent='Añade ingredientes para recibir ideas.';
  target.innerHTML='<div class="cook-empty"><span>🥬</span><p>Apunta al menos un ingrediente en «Mi nevera» y te enseñaré recetas que lo aprovechen.</p></div>';return;}
 const people=Math.max(1,Math.min(6,Number(el('cookTodayPeople')?.value)||2));
 const ranked=state.recipes.filter(r=>effectiveIngredients(r).every(i=>!avoided.has(i.id))).map(r=>cookTodayRanking(r,people)).filter(x=>x.available.some(i=>!COOK_PANTRY_BASICS.has(i.id))).sort((a,b)=>b.score-a.score||a.missingValue-b.missingValue||a.r.minutes-b.r.minutes).slice(0,8);
 if(count)count.textContent=`${ranked.length} propuestas · ${people} persona${people!==1?'s':''}`;
 target.innerHTML=ranked.map(({r,ready,missing,urgent,coverage,missingValue},idx)=>{
   const badges=[ready?'✓ Tienes todos los ingredientes':`${Math.round(coverage*100)}% de ingredientes disponibles`,urgent.length?`⏳ Aprovecha ${urgent.length} alimento${urgent.length!==1?'s':''} con fecha próxima`:''].filter(Boolean);
   const miss=missing.length?`<div class="cook-missing"><strong>Te faltaría:</strong> ${missing.slice(0,5).map(x=>{
     const p=state.prices[x.id];return `<div class="cook-missing-line"><span>${escapeHtml(p.name)} · ${fmtQty(x.qty,p.unit)}</span>${retailerLinks(p,true)}</div>`;
   }).join('')}${missing.length>5?`<small>Y ${missing.length-5} ingredientes más.</small>`:''}</div>`:'<p class="cook-available">Puedes prepararla con las cantidades que tienes registradas.</p>';
   return `<article class="cook-card"><div class="cook-card-top"><span class="cook-rank">${idx+1}</span><span class="cook-cat">${escapeHtml(cats[r.category]?.name||r.category)}</span><span class="cook-time">⏱ ${r.minutes} min</span></div><h3>${escapeHtml(r.title)}</h3><p class="cook-badges">${badges.map(b=>`<span>${escapeHtml(b)}</span>`).join('')}</p>${miss}<div class="cook-card-bottom"><small>${ready?'Sin compra adicional según tu inventario':`Compra orientativa: ${euro(missingValue)}`}</small><button type="button" class="button primary small" data-cook-recipe="${r.id}">Ver receta →</button></div></article>`;
 }).join('')||'<div class="cook-empty">No hay recomendaciones todavía. Añade ingredientes diferentes a tu nevera.</div>';
 target.querySelectorAll('[data-cook-recipe]').forEach(b=>b.onclick=()=>openRecipe(Number(b.dataset.cookRecipe)));
}
const baseRenderFridgeForCook=renderFridge;
renderFridge=function(){baseRenderFridgeForCook();renderCookToday()};

function initFeatures(){
  bindFridge();bindAvoided();
  el('cookTodayPeople').onchange=()=>renderCookToday();
  el('fridgeSuggest').onclick=()=>{renderCookToday();el('cookTodaySection').scrollIntoView({behavior:'smooth',block:'start'})};
  el('generatePlan').onclick=makeSmartPlan;
  // Reset sólo las anulaciones manuales: la nevera sigue intacta.
  el('clearPantry').onclick=()=>{state.pantry={};savePantry();renderShopping();renderPlan();toast('Se han restaurado las cantidades de tu nevera.')};
  const boot=()=>{
    if(!state.prices||!Object.keys(state.prices).length){setTimeout(boot,80);return}
    loadAvoided();renderFridge();if(state.plan){renderPlan();renderShopping()}
  };
  boot();
}
initFeatures();
