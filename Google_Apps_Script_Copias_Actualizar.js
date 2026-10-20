/**
 * MIGA Ahorra - receptor de COPIAS CIFRADAS, nunca de SQLite sin cifrar.
 * Configura "Propiedades de secuencia de comandos":
 *   MIGA_BACKUP_SECRET = secreto aleatorio, igual que el de Railway
 *   MIGA_BACKUP_FOLDER_ID = ID de la carpeta privada de Drive
 * Publicar como aplicación web: ejecutar como YO; acceso CUALQUIERA.
 * Sin el secreto, ninguna solicitud puede guardar archivos.
 */
function doPost(e) {
  try {
    const props = PropertiesService.getScriptProperties();
    const secret = props.getProperty('MIGA_BACKUP_SECRET');
    const folderId = props.getProperty('MIGA_BACKUP_FOLDER_ID');
    if (!secret || secret.length < 32 || !folderId) return reply_({ok:false, error:'not-configured'});
    if (!e || !e.postData || !e.postData.contents || e.postData.contents.length > 10500000) {
      return reply_({ok:false, error:'body-size'});
    }
    const input = JSON.parse(e.postData.contents);
    if (typeof input.secret !== 'string' || input.secret !== secret) {
      return reply_({ok:false, error:'not-authorized'});
    }
    const name = String(input.filename || '');
    if (!/^miga-\d{8}-\d{6}-\d{6}\.migaenc$/.test(name)) {
      return reply_({ok:false, error:'invalid-filename'});
    }
    if (typeof input.data !== 'string' || input.data.length > 9700000 || !/^[A-Za-z0-9+/=]+$/.test(input.data)) {
      return reply_({ok:false, error:'invalid-data'});
    }
    const bytes = Utilities.base64Decode(input.data);
    if (bytes.length > 7000000) return reply_({ok:false, error:'file-too-large'});
    // La base de datos se cifra en Railway (AES-256-GCM); Drive solo recibe ciphertext.
    const blob = Utilities.newBlob(bytes, 'application/octet-stream', name);
    const folder = DriveApp.getFolderById(folderId);
    const file = folder.createFile(blob);
    limpiarCopias_(); // Limitar número y antigüedad incluso al reiniciar la app.
    return reply_({ok:true, name:file.getName()});
  } catch (err) {
    // No incluir tokens, IDs ni detalles internos en la respuesta.
    console.error('Error en copia MIGA:', String(err).slice(0, 160));
    return reply_({ok:false, error:'internal-error'});
  }
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
      .setMimeType(ContentService.MimeType.JSON);
}


/** Ejecuta una vez desde el editor para autorizar el acceso a la carpeta. */
function comprobarCarpeta() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('MIGA_BACKUP_FOLDER_ID');
  const secret = props.getProperty('MIGA_BACKUP_SECRET');
  if (!id || !secret || secret.length < 32) throw new Error('Configura las dos propiedades primero.');
  const folder = DriveApp.getFolderById(id);
  console.log('Carpeta de copias disponible: ' + folder.getName());
}

/** Limpieza independiente: solo elimina archivos cifrados que cumplen el patrón MIGA. */
function limpiarCopias_() {
  const id = PropertiesService.getScriptProperties().getProperty('MIGA_BACKUP_FOLDER_ID');
  if (!id) throw new Error('Falta la carpeta de copias.');
  const folder = DriveApp.getFolderById(id);
  const iter = folder.getFiles();
  const files = [];
  while (iter.hasNext()) {
    const f = iter.next();
    if (/^miga-\d{8}-\d{6}-\d{6}\.migaenc$/.test(f.getName())) files.push(f);
  }
  files.sort(function(a,b){ return b.getName().localeCompare(a.getName()); });
  const limit = Date.now() - 30 * 24 * 60 * 60 * 1000;
  files.forEach(function(f,index){
    if (index >= 30 || f.getDateCreated().getTime() < limit) f.setTrashed(true);
  });
}

/** Función pública que ejecuta el disparador diario. */
function limpiarCopiasDiarias() { limpiarCopias_(); }

/** Ejecuta una sola vez: configura una limpieza DIARIA, incluso si Railway se apaga. */
function configurarLimpiezaDiaria() {
  const fn = 'limpiarCopiasDiarias';
  const triggers = ScriptApp.getProjectTriggers();
  if (!triggers.some(function(t){return t.getHandlerFunction() === fn;})) {
    ScriptApp.newTrigger(fn).timeBased().everyDays(1).create();
  }
  limpiarCopias_();
  console.log('Limpieza diaria configurada; máximo 30 copias y 30 días.');
}
