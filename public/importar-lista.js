/* MIGA — Foto de lista a Mi nevera, reconocimiento local con Tesseract.js.
   La imagen se procesa en el navegador; SOLO los productos revisados se guardan en SQLite. */
'use strict';
(function(){
  const byId=id=>document.getElementById(id);
  const input=byId('listPhoto'),btn=byId('recognizePhoto'),status=byId('photoOCRStatus');
  const preview=byId('photoPreview'),review=byId('photoReview'),rows=byId('photoReviewRows');
  const accept=byId('purchasedList'),add=byId('addDetected');
  let selectedFile=null, pictureURL=null, detected=[];
  const aliases={
    arroz:['arroz','rice'], pasta:['pasta','macarrones','espaguetis','spaghetti','fideos gruesos'], pasta_sg:['pasta sin gluten','macarrones sin gluten'],
    lentejas:['lentejas','lenteja'], garbanzos:['garbanzos','garbanzo'], alubias:['alubias','judias blancas','judias cocidas'], huevos:['huevos','huevo','docena huevos'],
    patata:['patatas','patata','papas'], cebolla:['cebollas','cebolla'], ajo:['ajos','ajo'], zanahoria:['zanahorias','zanahoria'],
    calabacin:['calabacin','calabacines'],pimiento:['pimientos','pimiento','pimiento rojo','pimiento verde'],tomate:['tomate fresco','tomates','tomate'],
    tomate_trit:['tomate triturado','tomate frito','tomate bote'],lechuga:['lechuga'],pepino:['pepino','pepinos'],brocoli:['brocoli'],
    champi:['champiñones','champiñon','setas'],espinacas:['espinacas'],pollo:['pechuga de pollo','pollo','pechugas'],
    pavo:['filetes de pavo','pavo'],atun:['atun','latas de atun'],merluza:['merluza'],yogur:['yogur','yogures','yogurt'],
    avena:['avena','copos de avena'],platano:['platanos','platano','banana'],manzana:['manzanas','manzana'],limon:['limones','limon'],
    leche:['leche'],aceite:['aceite de oliva','aceite'],queso:['queso rallado','queso'],pan:['barra de pan','pan de barra','pan'],
    tortillas:['tortillas de trigo','tortillas','wraps'],maiz:['maiz','lata de maiz'],guisantes:['guisantes'],salsa_soja:['salsa soja','salsa de soja'],
    pimenton:['pimenton'],comino:['comino'],curry:['curry'],sal:['sal fina','sal'],pimienta:['pimienta'],caldo:['caldo de verduras','caldo'],
    fideos:['fideos sopa','fideos'],pan_sg:['pan sin gluten'],lomo_cerdo:['lomo de cerdo','lomo'],picada_mixta:['carne picada mixta','carne picada','picada mixta']
  };
  const norm=str=>String(str||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9.,\s]/g,' ').replace(/\s+/g,' ').trim();
  function chooseId(line){
    const text=' '+norm(line)+' ',catalog=state.prices||{};let best=null,weight=0;
    Object.entries(catalog).forEach(([id,p])=>{
      const choices=[...(aliases[id]||[]),p.name];
      choices.forEach(term=>{const t=norm(term).replace(/\./g,'');if(t.length<3)return;
        if(text.includes(' '+t+' ')&&t.length>weight){best=id;weight=t.length}
      });
    });
    // Los nombres compuestos de productos específicos prevalecen sobre los generales.
    if(/\bsin gluten\b/.test(text)){
      if(best==='pasta' && catalog.pasta_sg)best='pasta_sg';
      if(best==='pan' && catalog.pan_sg)best='pan_sg';
    }
    return best;
  }
  function readQuantity(line,product){
    const txt=norm(line),unit=product?.unit||'g';
    const m=txt.match(/(?:^|\s)(\d+(?:[.,]\d+)?)\s*(kilogramos?|kgs?|kg|gramos?|grs?|gr|g|litros?|lts?|l|mililitros?|ml|unidades?|uds?|ud|huevos?)\b/);
    if(m){let v=Number(m[1].replace(',','.'));if(['kg','kgs','kilogramo','kilogramos'].includes(m[2]))v*=1000;
      if(['l','lt','lts','litro','litros'].includes(m[2]))v*=1000;
      if(m[2]==='huevo'||m[2]==='huevos'||m[2].startsWith('ud')||m[2].startsWith('unidad')){
        if(unit!=='ud')return product?.pack||1000;
      }
      if(unit==='ud' && !['ud','uds','unidad','unidades','huevo','huevos'].includes(m[2]))return product?.pack||1;
      if(unit==='g' && ['ml','l','lt','lts','litro','litros','mililitro','mililitros'].includes(m[2]))return product?.pack||500;
      if(unit==='ml' && ['kg','kgs','gramos','gramo','gr','g','grs'].includes(m[2]))return product?.pack||1000;
      return Math.max(0.1,Math.min(200000,v));
    }
    const packs=txt.match(/(?:^|\s)(\d+)\s*(?:x\s*)?(?:paquetes?|botes?|bolsas?|latas?|botellas?|briks?|bricks?)\b/);
    if(packs) return Math.min(200000,Number(packs[1])*(product?.pack||1));
    const number=txt.match(/^\s*(\d+(?:[.,]\d+)?)\s*(?:x\s*)?(?=[a-z])/);
    if(number){let v=Number(number[1].replace(',','.'));return Math.min(200000,unit==='ud'?v:v*(product?.pack||1))}
    return Math.min(200000,product?.pack||1);
  }
  function parse(text){
    return text.replace(/\r/g,'').split(/\n+/).map(x=>x.trim()).filter(x=>x.length>1 && x.length<160).slice(0,70).map(line=>{
      const id=chooseId(line),p=state.prices[id];return {line,id:id||'',qty:readQuantity(line,p),selected:!!id};
    });
  }
  const escape=s=>String(s||'').replace(/[&<>"']/g,v=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[v]));
  function render(){
    const opts=Object.entries(state.prices||{}).sort((a,b)=>a[1].name.localeCompare(b[1].name,'es'));
    byId('photoReviewCount').textContent=detected.filter(i=>i.id).length+' coincidencias de '+detected.length+' líneas';
    rows.innerHTML=detected.map((item,index)=>{
      const p=state.prices[item.id];return `<div class="photo-review-row" data-row="${index}">
        <label class="ocr-pick" aria-label="Añadir línea ${index+1}"><input type="checkbox" data-ocr-check="${index}" ${item.selected?'checked':''} ${!item.id?'disabled':''}></label>
        <div class="ocr-match"><small>Detectado: ${escape(item.line)}</small><select data-ocr-product="${index}" aria-label="Producto detectado en línea ${index+1}"><option value="">${item.id?'No importar':'⚠ No reconocido — elige alimento'}</option>${opts.map(([id,pr])=>`<option value="${escape(id)}" ${item.id===id?'selected':''}>${escape(pr.name)}</option>`).join('')}</select></div>
        <label class="ocr-quantity">Cantidad <input type="number" data-ocr-qty="${index}" min="0.1" max="200000" step="0.1" value="${item.qty}" ${!p?'disabled':''}></label><span class="ocr-unit">${p?.unit||'—'}</span><button type="button" data-ocr-delete="${index}" aria-label="Descartar línea ${index+1}" title="Quitar">✕</button>
      </div>`;
    }).join('')||'<p>No se han detectado líneas. Prueba una foto más nítida o pega el texto de la lista.</p>';
    rows.querySelectorAll('[data-ocr-product]').forEach(ctrl=>ctrl.addEventListener('change',()=>{
      const i=detected[Number(ctrl.dataset.ocrProduct)],old=state.prices[i.id];i.id=ctrl.value;
      if(i.id){const p=state.prices[i.id];i.selected=true;if(!old||old.unit!==p.unit)i.qty=p.pack;}
      else i.selected=false;
      render();
    }));
    rows.querySelectorAll('[data-ocr-qty]').forEach(ctrl=>ctrl.addEventListener('change',()=>{detected[Number(ctrl.dataset.ocrQty)].qty=Number(ctrl.value);toggle()}));
    rows.querySelectorAll('[data-ocr-check]').forEach(ctrl=>ctrl.addEventListener('change',()=>{detected[Number(ctrl.dataset.ocrCheck)].selected=ctrl.checked;toggle()}));
    rows.querySelectorAll('[data-ocr-delete]').forEach(ctrl=>ctrl.addEventListener('click',()=>{detected.splice(Number(ctrl.dataset.ocrDelete),1);render()}));
    toggle();
  }
  function toggle(){add.disabled=!accept.checked||!detected.some(i=>i.selected&&i.id&&Number(i.qty)>0&&Number(i.qty)<=200000)}
  accept.addEventListener('change',toggle);
  function show(text){detected=parse(text);accept.checked=false;review.hidden=false;render();review.scrollIntoView({behavior:'smooth',block:'start'});if(!detected.length)status.textContent='No encontré productos. Puedes pegar el texto o intentar otra foto.';}
  input.addEventListener('change',()=>{
    selectedFile=input.files?.[0]||null;
    btn.disabled=!selectedFile;status.textContent='';review.hidden=true;
    if(pictureURL)URL.revokeObjectURL(pictureURL);pictureURL=null;
    if(!selectedFile){preview.hidden=true;return}
    if(!['image/jpeg','image/png','image/webp'].includes(selectedFile.type)||selectedFile.size>8*1024*1024){status.textContent='Elige una imagen JPG, PNG o WEBP de menos de 8 MB.';btn.disabled=true;return}
    pictureURL=URL.createObjectURL(selectedFile);preview.innerHTML=`<img alt="Vista previa de tu lista" src="${pictureURL}"><span>${escape(selectedFile.name)}</span>`;preview.hidden=false;
  });
  function loadTesseract(){
    if(window.Tesseract?.createWorker)return Promise.resolve();
    if(loadTesseract.promise)return loadTesseract.promise;
    loadTesseract.promise=new Promise((resolve,reject)=>{
      const s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js';
      s.onload=()=>window.Tesseract?.createWorker?resolve():reject(new Error('El lector no se pudo iniciar.'));
      s.onerror=()=>reject(new Error('No se pudo descargar el lector de imágenes. Comprueba tu conexión.'));
      document.head.appendChild(s);
    }).catch(e=>{loadTesseract.promise=null;throw e});
    return loadTesseract.promise;
  }
  async function optimizedImage(file){
    const image=await createImageBitmap(file),ratio=Math.min(1,1800/Math.max(image.width,image.height));
    const c=document.createElement('canvas');c.width=Math.round(image.width*ratio);c.height=Math.round(image.height*ratio);
    c.getContext('2d').drawImage(image,0,0,c.width,c.height);image.close?.();
    return c;
  }
  btn.addEventListener('click',async()=>{
    if(!selectedFile)return;btn.disabled=true;status.textContent='Preparando el reconocimiento…';let worker=null;
    try{
      await loadTesseract();status.textContent='Leyendo la foto en este dispositivo (puede tardar unos segundos)…';
      worker=await Tesseract.createWorker('spa',1,{
        langPath:'https://cdn.jsdelivr.net/npm/@tesseract.js-data/spa@1.0.0/4.0.0_best_int',
        logger:m=>{if(m.status==='recognizing text')status.textContent=`Reconociendo la lista… ${Math.round((m.progress||0)*100)} %`}
      });
      const canvas=await optimizedImage(selectedFile);
      const output=await worker.recognize(canvas);
      const text=output?.data?.text||'';
      status.textContent=text.trim()?'Lectura terminada. Corrige las cantidades antes de importar.':'No he podido leer la foto; prueba con mejor luz o pega el texto.';
      show(text);
    }catch(e){status.textContent='No se ha podido leer la foto: '+e.message+'. Puedes introducir la lista como texto justo debajo.';}
    finally{if(worker)await worker.terminate().catch(()=>{});btn.disabled=!selectedFile}
  });
  byId('processText').addEventListener('click',()=>{show(byId('ocrText').value);status.textContent='Lista interpretada. Revisa antes de guardar.'});
  byId('clearDetected').addEventListener('click',()=>{detected=[];review.hidden=true;accept.checked=false;status.textContent='Importación cancelada.'});
  add.addEventListener('click',()=>{
    if(!accept.checked)return;let count=0;
    for(const item of detected){const qty=Number(item.qty);if(!item.selected||!state.prices[item.id]||!Number.isFinite(qty)||qty<=0||qty>200000)continue;
      const previous=state.fridge[item.id]||{};state.fridge[item.id]={qty:Math.min(200000,(Number(previous.qty)||0)+qty),expiry:previous.expiry||''};
      delete state.pantry[item.id];count++;
    }
    if(!count)return;
    savePantry();fridgeSave();renderFridge();if(state.plan){renderShopping();renderPlan()};if(typeof updateMigaDashboard==='function')updateMigaDashboard();
    detected=[];review.hidden=true;accept.checked=false;input.value='';selectedFile=null;btn.disabled=true;
    if(pictureURL){URL.revokeObjectURL(pictureURL);pictureURL=null}preview.hidden=true;
    status.textContent=`✓ ${count} producto${count===1?'':'s'} añadido${count===1?'':'s'} a Mi nevera. Si ya estaba registrado, he sumado la cantidad.`;
    toast(`${count} productos añadidos a tu nevera ✓`);
    byId('fridgeList').scrollIntoView({behavior:'smooth',block:'center'});
  });
  // Contact form: store private message on server; optional SMTP delivery.
  const form=byId('contactForm');
  form.addEventListener('submit',async e=>{
    e.preventDefault();const f=new FormData(form),payload=Object.fromEntries(f.entries());payload.consent=f.has('consent');
    const send=byId('sendContact'),result=byId('contactResult');send.disabled=true;result.textContent='Enviando…';
    try{const res=await api('/api/contacto',payload);result.textContent=res.delivered?'✓ Mensaje enviado al correo de MIGA. ¡Gracias!':'✓ Mensaje guardado para el administrador de MIGA. El aviso por correo está pendiente de configuración.';form.reset();}
    catch(err){result.textContent='No hemos podido registrar el mensaje: '+err.message}
    finally{send.disabled=false}
  });
  // The admin can consult contact requests without exposing them to regular users.
  const baseAdmin=renderAdmin;
  renderAdmin=async function(){
    await baseAdmin();
    if(state.user?.role!=='admin')return;
    try{
      const data=await api('/api/admin/contactos');let section=byId('adminContacts');
      if(!section){section=document.createElement('section');section.id='adminContacts';section.className='admin-contacts';byId('adminContent').appendChild(section)}
      section.innerHTML='<h2>✉ Mensajes de contacto</h2>'+(data.contacts?.length?data.contacts.map(m=>`<article class="contact-message"><div><strong>${escape(m.subject)} · ${escape(m.name)}</strong><small>${escape(m.created_at)}</small></div><p>${escape(m.message)}</p><small><a href="mailto:${encodeURIComponent(m.email)}">${escape(m.email)}</a> · ${m.delivered?'Aviso enviado por correo':'Guardado en MIGA (SMTP pendiente)'}</small></article>`).join(''):'<p>Todavía no hay mensajes de contacto.</p>');
    }catch(err){console.warn('No se pudieron cargar los mensajes:',err.message)}
  };
})();
