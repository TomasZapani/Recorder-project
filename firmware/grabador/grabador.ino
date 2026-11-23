// Grabador: un apretón corto empieza a grabar a la microSD, otro termina.
// Al terminar se conecta al WiFi de casa, sube la grabación a la web y muestra en
// la OLED qué pasa en la compu (transcribiendo, armando la minuta, lista).
// Pensado para quedar enchufado a la corriente.
//
// En la microSD (FAT32), un archivo /wifi.txt:
//   red=NombreDeLaRed
//   clave=LaClave
//   compu=192.168.1.4:5000
//
// Las grabaciones quedan en /grab/NNNNN.raw (muestras uint16 del ADC a 16 kHz, el
// mismo formato que manda firmware/microfono por USB). Al lado, NNNNN.txt con el
// tamaño y el CRC32: mientras exista, la grabación está pendiente de subir. Cuando la
// web confirma, el .txt se borra y el .raw pasa a .sub (se borra solo si falta lugar).
//
// Requiere las librerías "Adafruit SSD1306" y "Adafruit GFX" (Library Manager).

#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// --- pines (ver README.md) ---------------------------------------------------
const int MIC_PIN = 35;  // ADC1: nunca mover a un pin de ADC2 (no anda con WiFi)
const int TOUCH_PIN = 27;
const int LED_PIN = 2;  // LED azul de la placa: muestra el estado aunque la OLED no ande
const int OLED_SDA = 22, OLED_SCL = 23;
const uint8_t OLED_ADDR = 0x3C;
const int SD_SCK = 17, SD_MISO = 18, SD_MOSI = 19, SD_CS = 5;

// --- audio -------------------------------------------------------------------
const int SAMPLE_RATE = 16000;
const int BUF_MUESTRAS = 2048;  // 4 KB por buffer, 128 ms de audio
const int N_BUFS = 12;          // ~1,5 s de colchón para cuando la SD se cuelga
const size_t BUF_BYTES = BUF_MUESTRAS * 2;

// --- subida ------------------------------------------------------------------
const size_t PEDAZO_SUBIDA = 512 * 1024;     // bytes por pedido HTTP
const uint64_t LIBRE_MINIMO = 300ULL << 20;  // por debajo, se borran grabaciones ya subidas
const uint32_t REINTENTO_MS = 30000;
const uint32_t CONSULTA_MS = 3000;

static uint16_t bufs[N_BUFS][BUF_MUESTRAS];
static uint16_t descarte[BUF_MUESTRAS];  // si no hay buffer libre se mide igual, para no perder el ritmo
static QueueHandle_t libres, llenos;      // índices de bufs
static TaskHandle_t tareaMuestreoH;

static volatile bool grabando = false;
static volatile bool muestreoTerminado = true;
static volatile uint32_t perdidos = 0;  // buffers descartados por SD lenta
static volatile uint8_t nivel = 0;      // 0..100, para la barrita
static volatile bool errorEscritura = false;
static volatile bool toque = false;
static volatile uint32_t ultimoAvance = 0;  // última vez que la subida movió bytes
static bool sdFallo = false;                // la SD dejó de responder al leer

static File archivo;
static uint32_t crcGrabacion;
static uint64_t bytesGrabados;
static uint32_t inicioGrabacion;

Adafruit_SSD1306 oled(128, 64, &Wire, -1);
Preferences prefs;
String wifiRed, wifiClave, compu;
String idPlaca;

// --- lo que muestra la pantalla ----------------------------------------------
enum Modo { ARRANCANDO, SIN_SD, LISTO, GRABANDO, GUARDANDO, CONECTANDO, SUBIENDO };

struct Pantalla {
  Modo modo = ARRANCANDO;
  char aviso[22] = "";  // problema actual: "Sin WiFi", "Compu no responde"...
  char compu[22] = "";  // qué pasa en la compu con la última grabación
  uint8_t subida = 0;   // % del archivo que se está subiendo
  uint16_t pendientes = 0;
  uint32_t libreMB = 0;
  bool wifi = false;
};
static Pantalla pantalla;
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

void setModo(Modo m) { portENTER_CRITICAL(&mux); pantalla.modo = m; portEXIT_CRITICAL(&mux); }
void setTexto(char* destino, const char* texto) {
  portENTER_CRITICAL(&mux);
  strlcpy(destino, texto, sizeof(pantalla.aviso));
  portEXIT_CRITICAL(&mux);
}
void setAviso(const char* t) { setTexto(pantalla.aviso, t); }
void setCompu(const char* t) { setTexto(pantalla.compu, t); }

// --- CRC32 (el mismo que zlib.crc32, que usa la web para verificar) ----------
static uint32_t tablaCrc[256];
void armarTablaCrc() {
  for (uint32_t i = 0; i < 256; i++) {
    uint32_t c = i;
    for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320 ^ (c >> 1) : c >> 1;
    tablaCrc[i] = c;
  }
}
uint32_t crc32(uint32_t crc, const uint8_t* datos, size_t n) {
  crc = ~crc;
  while (n--) crc = tablaCrc[(crc ^ *datos++) & 0xFF] ^ (crc >> 8);
  return ~crc;
}

// --- tareas de audio ---------------------------------------------------------

// Core 1, solo para esto: mide el micrófono 16.000 veces por segundo con espera
// activa (el método ya medido: 15998–16016 Hz). esp_timer_get_time es de 64 bits;
// micros() da la vuelta a los 71 minutos y cortaría una clase larga.
void tareaMuestreo(void*) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    int64_t t0 = esp_timer_get_time();
    uint64_t n = 0;
    while (grabando) {
      uint8_t idx;
      uint16_t* buf = descarte;
      bool hayLibre = xQueueReceive(libres, &idx, 0) == pdTRUE;
      if (hayLibre) buf = bufs[idx];
      uint16_t minimo = 4095, maximo = 0;
      for (int i = 0; i < BUF_MUESTRAS; i++) {
        int64_t objetivo = t0 + (int64_t)(n * 1000000ULL / SAMPLE_RATE);
        while (esp_timer_get_time() < objetivo) { }
        uint16_t v = analogRead(MIC_PIN);
        buf[i] = v;
        if (v < minimo) minimo = v;
        if (v > maximo) maximo = v;
        n++;
      }
      nivel = min(100, (maximo - minimo) * 100 / 2400);
      if (hayLibre) xQueueSend(llenos, &idx, portMAX_DELAY);
      else perdidos++;
    }
    muestreoTerminado = true;
  }
}

// Core 0: pasa los buffers llenos a la SD y calcula el CRC al vuelo.
void tareaEscritura(void*) {
  uint32_t bloques = 0;
  for (;;) {
    uint8_t idx;
    xQueueReceive(llenos, &idx, portMAX_DELAY);
    const uint8_t* datos = (const uint8_t*)bufs[idx];
    size_t escritos = archivo.write(datos, BUF_BYTES);
    if (escritos != BUF_BYTES) errorEscritura = true;
    crcGrabacion = crc32(crcGrabacion, datos, escritos);
    bytesGrabados += escritos;
    if (++bloques % 64 == 0) archivo.flush();  // cada ~8 s: un corte de luz pierde poco
    xQueueSend(libres, &idx, portMAX_DELAY);
  }
}

// --- touch y pantalla --------------------------------------------------------

void tareaTouch(void*) {
  bool antes = false;
  uint32_t ultimo = 0;
  int seguidos = 0;
  for (;;) {
    bool ahora = digitalRead(TOUCH_PIN) == HIGH;  // pulsador: HIGH al apretar
    if (!ahora && seguidos) Serial.printf("[TOUCH] Pulso de %d ms\n", seguidos * 20);
    seguidos = ahora ? seguidos + 1 : 0;
    bool firme = seguidos >= 8;  // 160 ms apretado: descarta rebotes y pulsos sueltos
    if (firme && !antes && millis() - ultimo > 600) {
      toque = true;
      ultimo = millis();
      Serial.println("[TOUCH] Toque");
    }
    antes = firme || (antes && ahora);

    // Si la subida se colgó (la SD o el WiFi dejan de responder y una llamada no vuelve),
    // se reinicia: la subida sigue después desde el último byte que confirmó la compu.
    Modo m = pantalla.modo;
    if (m == SUBIENDO && millis() - ultimoAvance > 60000) {
      Serial.println("[UP] Subida colgada hace 60 s: reinicio");
      delay(100);
      ESP.restart();
    }

    // LED: fijo grabando, parpadeo rápido conectando o subiendo, apagado en reposo.
    bool parpadeo = (millis() / 150) % 2;
    digitalWrite(LED_PIN, m == GRABANDO || m == GUARDANDO || ((m == CONECTANDO || m == SUBIENDO) && parpadeo));
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void centrado(const char* texto, int y, int tam) {
  int16_t x1, y1;
  uint16_t w, h;
  oled.setTextSize(tam);
  oled.getTextBounds(texto, 0, y, &x1, &y1, &w, &h);
  oled.setCursor((128 - w) / 2, y);
  oled.print(texto);
}

void barra(int y, int alto, int porcentaje) {
  oled.drawRect(0, y, 128, alto, SSD1306_WHITE);
  oled.fillRect(2, y + 2, (124 * constrain(porcentaje, 0, 100)) / 100, alto - 4, SSD1306_WHITE);
}

void pie(const Pantalla& p) {
  char linea[24];
  if (p.pendientes) snprintf(linea, sizeof linea, "SD %.1f GB  %u pend.", p.libreMB / 1024.0, p.pendientes);
  else snprintf(linea, sizeof linea, "SD %.1f GB libres", p.libreMB / 1024.0);
  oled.setTextSize(1);
  oled.setCursor(0, 56);
  oled.print(linea);
}

void dibujar() {
  Pantalla p;
  portENTER_CRITICAL(&mux);
  p = pantalla;
  portEXIT_CRITICAL(&mux);

  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  char t[24];

  switch (p.modo) {
    case ARRANCANDO:
      centrado("RECORDER", 20, 2);
      centrado("arrancando...", 44, 1);
      break;

    case SIN_SD:
      centrado("SIN microSD", 8, 1);
      centrado("Revisa la tarjeta", 28, 1);
      centrado("(FAT32) y reinicia", 40, 1);
      break;

    case LISTO:
      oled.setCursor(0, 0);
      oled.print("RECORDER");
      oled.setCursor(104, 0);
      oled.print(p.wifi ? "WiFi" : "");
      centrado("LISTO", 14, 2);
      centrado("Apreta para grabar", 33, 1);
      centrado(p.aviso[0] ? p.aviso : p.compu, 44, 1);
      pie(p);
      break;

    case GRABANDO: {
      uint32_t s = (millis() - inicioGrabacion) / 1000;
      bool parpadeo = (millis() / 500) % 2;
      if (parpadeo) oled.fillCircle(4, 4, 4, SSD1306_WHITE);
      oled.setCursor(12, 1);
      oled.print("GRABANDO");
      if (s >= 3600) snprintf(t, sizeof t, "%u:%02u:%02u", s / 3600, s / 60 % 60, s % 60);
      else snprintf(t, sizeof t, "%02u:%02u", s / 60, s % 60);
      centrado(t, 16, s >= 3600 ? 2 : 3);
      barra(42, 8, nivel);
      if (perdidos || errorEscritura) {
        snprintf(t, sizeof t, errorEscritura ? "ERROR AL ESCRIBIR SD" : "Perdidos: %u", perdidos);
        oled.setCursor(0, 56);
        oled.print(t);
      } else {
        pie(p);
      }
      break;
    }

    case GUARDANDO:
      centrado("Guardando...", 26, 1);
      break;

    case CONECTANDO:
      centrado("Conectando", 16, 1);
      centrado("al WiFi...", 28, 1);
      pie(p);
      break;

    case SUBIENDO:
      centrado("SUBIENDO", 4, 2);
      snprintf(t, sizeof t, "%u %%", p.subida);
      centrado(t, 24, 1);
      barra(36, 10, p.subida);
      pie(p);
      break;
  }
  oled.display();
}

// La pantalla se inicializa y se usa siempre desde esta tarea (core 0): el bus I2C
// queda en manos de un solo dueño.
// Cada ~5 s se le vuelve a mandar la configuración: si la OLED se reinicia por un
// bajón de tensión (picos del WiFi, falso contacto en la protoboard) queda negra
// hasta que alguien la configure de nuevo, y así se recupera sola.
void tareaPantalla(void*) {
  Wire.begin(OLED_SDA, OLED_SCL);
  bool ok = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  Serial.println(ok ? "[OLED] OK" : "[OLED] FALLA: no inicia en 0x3C");
  for (uint32_t n = 0;; n++) {
    if (n % 33 == 32) {
      bool antes = ok;
      ok = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR, false, false);
      if (ok != antes) Serial.println(ok ? "[OLED] Volvio a responder" : "[OLED] Dejo de responder: revisar cables");
    }
    if (ok) dibujar();
    if (n == 0) Serial.println("[OLED] Primera pantalla dibujada");
    vTaskDelay(pdMS_TO_TICKS(150));
  }
}

// --- microSD -----------------------------------------------------------------

String ruta(uint32_t num, const char* ext) {
  char r[24];
  snprintf(r, sizeof r, "/grab/%05lu.%s", (unsigned long)num, ext);
  return String(r);
}

void actualizarEspacio() {
  uint64_t libre = SD.totalBytes() - SD.usedBytes();
  portENTER_CRITICAL(&mux);
  pantalla.libreMB = libre >> 20;
  portEXIT_CRITICAL(&mux);
}

// Lista los números de grabación con cierta extensión, del más viejo al más nuevo.
int listar(const char* ext, uint32_t* nums, int max) {
  int n = 0;
  File dir = SD.open("/grab");
  if (!dir) return 0;
  for (File f = dir.openNextFile(); f && n < max; f = dir.openNextFile()) {
    String nombre = f.name();
    f.close();
    if (nombre.endsWith(String(".") + ext)) nums[n++] = nombre.toInt();
  }
  dir.close();
  for (int i = 1; i < n; i++)  // pocas: inserción alcanza
    for (int j = i; j > 0 && nums[j] < nums[j - 1]; j--) std::swap(nums[j], nums[j - 1]);
  return n;
}

void contarPendientes() {
  uint32_t nums[200];
  int n = listar("txt", nums, 200);
  portENTER_CRITICAL(&mux);
  pantalla.pendientes = n;
  portEXIT_CRITICAL(&mux);
}

void escribirDatos(uint32_t num, uint64_t bytes, uint32_t crc, uint32_t perd) {
  File f = SD.open(ruta(num, "txt"), FILE_WRITE);
  f.printf("bytes=%llu\ncrc=%08lx\nperdidos=%lu\n", bytes, (unsigned long)crc, (unsigned long)perd);
  f.close();
}

// Si se cortó la luz grabando, queda un .raw sin .txt: se le calcula el CRC y entra
// a la cola de subida como cualquier otra (se pierde como mucho el último pedazo).
// Rehace el .txt con lo que de verdad hay en el .raw. Si está vacío, borra los dos.
uint64_t rehacerDatos(uint32_t num) {
  static uint8_t buf[4096];
  File f = SD.open(ruta(num, "raw"));
  uint64_t total = f ? f.size() & ~1ULL : 0;
  uint32_t crc = 0;
  for (uint64_t leidos = 0; leidos < total;) {
    size_t r = f.read(buf, min<uint64_t>(sizeof buf, total - leidos));
    if (!r) { total = leidos; break; }
    crc = crc32(crc, buf, r);
    leidos += r;
  }
  if (f) f.close();
  if (total) {
    escribirDatos(num, total, crc, 0);
  } else {
    SD.remove(ruta(num, "raw"));
    SD.remove(ruta(num, "txt"));
  }
  return total;
}

void recuperarCortadas() {
  uint32_t nums[200];
  int n = listar("raw", nums, 200);
  for (int i = 0; i < n; i++) {
    if (SD.exists(ruta(nums[i], "txt"))) continue;
    uint64_t total = rehacerDatos(nums[i]);
    Serial.printf("[SD] Recuperada la grabacion %lu (%llu bytes)\n", (unsigned long)nums[i], total);
  }
}

// Informa por serie qué hay en /grab, para revisar la tarjeta sin sacarla.
void listarGrabaciones() {
  File dir = SD.open("/grab");
  if (!dir) return;
  Serial.println("[SD] Contenido de /grab:");
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    Serial.printf("[SD]   %-12s %8lu bytes  (%.1f s)\n", f.name(), (unsigned long)f.size(),
                  f.size() / 2.0 / SAMPLE_RATE);
    f.close();
  }
  dir.close();
}

void hacerLugar() {
  uint32_t nums[200];
  int n = listar("sub", nums, 200);
  for (int i = 0; i < n && SD.totalBytes() - SD.usedBytes() < LIBRE_MINIMO; i++) {
    SD.remove(ruta(nums[i], "sub"));
    Serial.printf("[SD] Borrada la grabacion ya subida %lu para hacer lugar\n", (unsigned long)nums[i]);
  }
}

// --- grabar ------------------------------------------------------------------

uint32_t numActual = 0;

void empezarGrabacion() {
  WiFi.disconnect(true);  // nunca WiFi mientras graba
  WiFi.mode(WIFI_OFF);
  portENTER_CRITICAL(&mux);
  pantalla.wifi = false;
  portEXIT_CRITICAL(&mux);

  hacerLugar();
  numActual = prefs.getUInt("seq", 0) + 1;
  prefs.putUInt("seq", numActual);
  archivo = SD.open(ruta(numActual, "raw"), FILE_WRITE);
  if (!archivo) {
    setAviso("No pude crear archivo");
    return;
  }
  crcGrabacion = 0;
  bytesGrabados = 0;
  perdidos = 0;
  errorEscritura = false;
  xQueueReset(libres);
  xQueueReset(llenos);
  for (uint8_t i = 0; i < N_BUFS; i++) xQueueSend(libres, &i, 0);

  setAviso("");
  setCompu("");
  inicioGrabacion = millis();
  muestreoTerminado = false;
  grabando = true;
  setModo(GRABANDO);
  xTaskNotifyGive(tareaMuestreoH);
  Serial.printf("[REC] Grabando %s\n", ruta(numActual, "raw").c_str());
}

void terminarGrabacion() {
  setModo(GUARDANDO);
  grabando = false;
  while (!muestreoTerminado) vTaskDelay(pdMS_TO_TICKS(5));
  while (uxQueueMessagesWaiting(libres) < N_BUFS) vTaskDelay(pdMS_TO_TICKS(5));  // la SD terminó
  archivo.close();
  escribirDatos(numActual, bytesGrabados, crcGrabacion, perdidos);
  Serial.printf("[REC] Listo: %llu bytes, %lu buffers perdidos\n", bytesGrabados, (unsigned long)perdidos);
  actualizarEspacio();
  contarPendientes();
  setModo(LISTO);
}

// --- WiFi y subida -----------------------------------------------------------

String idSubida(uint32_t num) {
  char id[32];
  snprintf(id, sizeof id, "%s-%05lu", idPlaca.c_str(), (unsigned long)num);
  return String(id);
}

String url(const String& id, const char* resto) {
  return "http://" + compu + "/api/grabador/" + id + resto;
}

// Saca un número o un texto de una respuesta JSON chica, sin librerías.
long long numeroJson(const String& json, const char* clave) {
  int i = json.indexOf(String("\"") + clave + "\":");
  return i < 0 ? -1 : atoll(json.c_str() + i + strlen(clave) + 3);
}
String textoJson(const String& json, const char* clave) {
  int i = json.indexOf(String("\"") + clave + "\":");
  if (i < 0) return "";
  int a = json.indexOf('"', i + strlen(clave) + 3);
  int b = json.indexOf('"', a + 1);
  return a < 0 || b < 0 ? "" : json.substring(a + 1, b);
}

bool conectarWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  setModo(CONECTANDO);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(wifiRed.c_str(), wifiClave.c_str());
  for (int i = 0; i < 150 && WiFi.status() != WL_CONNECTED; i++) {
    if (toque) break;
    vTaskDelay(pdMS_TO_TICKS(100));
  }
  bool ok = WiFi.status() == WL_CONNECTED;
  portENTER_CRITICAL(&mux);
  pantalla.wifi = ok;
  portEXIT_CRITICAL(&mux);
  setModo(LISTO);
  if (ok) {
    WiFi.setSleep(false);  // sin ahorro de energía: con él la subida va a ~15 KB/s
    Serial.printf("[WiFi] Conectado, IP %s\n", WiFi.localIP().toString().c_str());
  }
  return ok;
}

String seguimiento;  // id de la última grabación subida, para preguntar cómo va
uint32_t proximoIntento = 0, proximaConsulta = 0;

// Manda un pedazo del archivo con un PUT armado a mano. HTTPClient::sendRequest con un
// Stream espera para siempre si el archivo dice no tener bytes disponibles; acá cada
// lectura y cada escritura tiene salida. Devuelve el código HTTP (o -1) y el cuerpo.
int putPedazo(const String& id, File& f, uint64_t desde, size_t n, String& resp) {
  // Múltiplo exacto del MSS de lwIP (1436): con 4096 cada escritura dejaba un segmento
  // impar que Windows confirma con ACK demorado (~200 ms) y la subida no pasaba de 14 KB/s.
  static uint8_t buf[1436 * 8];
  int dosPuntos = compu.indexOf(':');
  String host = dosPuntos < 0 ? compu : compu.substring(0, dosPuntos);
  uint16_t puerto = dosPuntos < 0 ? 80 : compu.substring(dosPuntos + 1).toInt();

  if (!f.seek(desde)) {
    Serial.printf("[UP] No pude ir al byte %llu del archivo\n", desde);
    return -1;
  }
  WiFiClient c;
  c.setTimeout(15000);  // ms (en el core 3.x es en milisegundos, no segundos)
  if (!c.connect(host.c_str(), puerto, 3000)) return -1;
  c.setNoDelay(true);
  c.printf("PUT /api/grabador/%s?desde=%llu HTTP/1.1\r\nHost: %s\r\n"
           "Content-Type: application/octet-stream\r\nContent-Length: %u\r\nConnection: close\r\n\r\n",
           id.c_str(), desde, compu.c_str(), (unsigned)n);
  uint32_t tInicio = millis(), msSd = 0;
  for (size_t falta = n; falta;) {
    uint32_t t = millis();
    size_t r = f.read(buf, min(sizeof buf, falta));
    msSd += millis() - t;
    if (!r) {
      Serial.printf("[UP] La SD no devolvio datos (pos %lu de %lu)\n", (unsigned long)f.position(), (unsigned long)f.size());
      sdFallo = true;
      return -1;
    }
    for (size_t hecho = 0; hecho < r;) {
      size_t w = c.write(buf + hecho, r - hecho);
      if (!w) return -1;  // conexión caída
      hecho += w;
      ultimoAvance = millis();
    }
    falta -= r;
  }
  uint32_t msTotal = millis() - tInicio;
  Serial.printf("[UP] %u bytes en %lu ms (SD %lu ms, WiFi %lu ms) = %lu KB/s\n", (unsigned)n,
                (unsigned long)msTotal, (unsigned long)msSd, (unsigned long)(msTotal - msSd),
                (unsigned long)(n / max<uint32_t>(msTotal, 1)));

  // La respuesta entera, con pausas: los read*() de Stream esperan girando sin soltar
  // el procesador, y 15 s así disparan el watchdog.
  String todo;
  for (uint32_t t0 = millis(); millis() - t0 < 15000;) {
    if (c.available()) {
      while (c.available()) todo += (char)c.read();
    } else if (!c.connected()) {
      break;
    } else {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
  c.stop();
  int fin = todo.indexOf("\r\n\r\n");
  resp = fin < 0 ? "" : todo.substring(fin + 4);
  int codigo = todo.startsWith("HTTP/") ? todo.substring(todo.indexOf(' ') + 1).toInt() : 0;
  return codigo ? codigo : -1;
}

// Sube una grabación. Devuelve true si la web la confirmó.
bool subir(uint32_t num) {
  File datos = SD.open(ruta(num, "txt"));
  String texto = datos.readString();
  datos.close();
  uint64_t total = strtoull(texto.substring(texto.indexOf("bytes=") + 6).c_str(), nullptr, 10);
  String crc = texto.substring(texto.indexOf("crc=") + 4, texto.indexOf("crc=") + 12);
  String id = idSubida(num);

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(15000);

  http.begin(url(id, ""));
  int codigo = http.GET();
  String resp = http.getString();
  http.end();
  if (codigo != 200) {
    setAviso("Compu no responde");
    Serial.printf("[UP] GET %d\n", codigo);
    return false;
  }
  uint64_t enviados = numeroJson(resp, "recibidos");

  File f = SD.open(ruta(num, "raw"));
  Serial.printf("[UP] %s: archivo %s, %lu bytes, faltan %llu\n", ruta(num, "raw").c_str(),
                f ? "abierto" : "NO ABRE", f ? (unsigned long)f.size() : 0UL, total - enviados);
  if (!f) {
    // Ya subida: la SD renombró el .raw pero no llegó a borrar el .txt. Sin esto, la
    // cola queda trabada para siempre en esta grabación.
    if (SD.exists(ruta(num, "sub"))) {
      SD.remove(ruta(num, "txt"));
      Serial.printf("[UP] Grabacion %lu ya estaba subida: borro el .txt que quedo\n", (unsigned long)num);
      return true;
    }
    setAviso("No pude leer la SD");
    return false;
  }
  if (f.size() < total) {
    // La SD no llegó a escribir todo (falla de la tarjeta o de la alimentación): se
    // sube lo que haya. Si no hay nada, se descarta, así no traba a las que siguen.
    f.close();
    uint64_t hay = rehacerDatos(num);
    Serial.printf("[UP] Grabacion %lu incompleta: quedan %llu de %llu bytes\n", (unsigned long)num, hay, total);
    return hay == 0 || subir(num);
  }
  ultimoAvance = millis();
  setModo(SUBIENDO);
  while (enviados < total) {
    if (toque) { f.close(); setModo(LISTO); return false; }
    portENTER_CRITICAL(&mux);
    pantalla.subida = enviados * 100 / total;
    portEXIT_CRITICAL(&mux);
    size_t n = min<uint64_t>(PEDAZO_SUBIDA, total - enviados);
    uint32_t t0 = millis();
    codigo = putPedazo(id, f, enviados, n, resp);
    Serial.printf("[UP] PUT %d en %lu ms: %s\n", codigo, (unsigned long)(millis() - t0), resp.c_str());
    long long recibidos = numeroJson(resp, "recibidos");
    if ((codigo == 200 || codigo == 409) && recibidos >= 0) {
      enviados = recibidos;  // con 409, la web dice desde dónde seguir
    } else {
      f.close();
      if (sdFallo) {  // la tarjeta se colgó: desmontarla y montarla de nuevo suele revivirla
        sdFallo = false;
        SD.end();
        bool ok = SD.begin(SD_CS, SPI);
        Serial.printf("[SD] Remontada: %s\n", ok ? "OK" : "FALLA");
      }
      setModo(LISTO);
      setAviso("Se corto la subida");
      Serial.printf("[UP] PUT %d\n", codigo);
      return false;
    }
  }
  f.close();

  char q[64];
  snprintf(q, sizeof q, "/fin?bytes=%llu&crc=%s", total, crc.c_str());
  http.begin(url(id, q));
  codigo = http.POST("");
  resp = http.getString();
  http.end();
  setModo(LISTO);
  if (codigo != 200) {
    setAviso(codigo == 409 ? "Llego mal, reintento" : "Error en la compu");
    Serial.printf("[UP] fin %d %s\n", codigo, resp.c_str());
    return false;
  }
  SD.remove(ruta(num, "txt"));
  SD.rename(ruta(num, "raw"), ruta(num, "sub"));
  Serial.printf("[UP] Subida %s\n", id.c_str());
  setAviso("");
  setCompu("Subida!");
  seguimiento = id;
  proximaConsulta = millis() + 1000;
  return true;
}

void subirPendientes() {
  uint32_t nums[200];
  int n = listar("txt", nums, 200);
  if (!n) return;
  if (wifiRed.isEmpty() || compu.isEmpty()) {
    setAviso("Falta /wifi.txt");
    proximoIntento = millis() + REINTENTO_MS;
    return;
  }
  if (!conectarWifi()) {
    if (!toque) setAviso("Sin WiFi");
    proximoIntento = millis() + REINTENTO_MS;
    return;
  }
  for (int i = 0; i < n && !toque; i++) {
    if (!subir(nums[i])) {
      proximoIntento = millis() + REINTENTO_MS;
      break;
    }
    contarPendientes();
  }
  contarPendientes();
}

// Pregunta a la web cómo va la última grabación subida.
void consultarCompu() {
  proximaConsulta = millis() + CONSULTA_MS;
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(5000);
  http.begin(url(seguimiento, "/estado"));
  int codigo = http.GET();
  String resp = http.getString();
  http.end();
  if (codigo != 200) return;

  String fase = textoJson(resp, "fase");
  char t[22];
  if (fase == "transcribiendo") {
    float p = resp.substring(resp.indexOf("\"progreso\":") + 11).toFloat();
    snprintf(t, sizeof t, "Transcribiendo %d%%", (int)(p * 100));
  } else if (fase == "en cola") strcpy(t, "En cola en la compu");
  else if (fase == "resumiendo") strcpy(t, "Armando la minuta");
  else if (fase == "lista") strcpy(t, "Minuta lista!");
  else if (fase == "transcripta") strcpy(t, "Transcripta (sin IA)");
  else if (fase == "error") strcpy(t, "Error en la compu");
  else strcpy(t, "");
  setCompu(t);
  if (fase != "transcribiendo" && fase != "en cola" && fase != "resumiendo") seguimiento = "";
}

// --- lógica principal ---

void tareaControl(void*) {
  for (;;) {
    if (toque) {
      toque = false;
      if (grabando) terminarGrabacion();
      else empezarGrabacion();
      proximoIntento = millis();  // recién terminada: subir ya
    } else if (!grabando) {
      if (pantalla.pendientes && (int32_t)(millis() - proximoIntento) >= 0) subirPendientes();
      else if (seguimiento.length() && (int32_t)(millis() - proximaConsulta) >= 0) consultarCompu();
    }
    vTaskDelay(pdMS_TO_TICKS(30));
  }
}

// --- arranque ----------------------------------------------------------------

void leerWifiTxt() {
  File f = SD.open("/wifi.txt");
  if (!f) {
    Serial.println("[WiFi] No hay /wifi.txt en la microSD");
    return;
  }
  while (f.available()) {
    String linea = f.readStringUntil('\n');
    linea.trim();
    int igual = linea.indexOf('=');
    if (igual < 0 || linea.startsWith("#")) continue;
    String clave = linea.substring(0, igual), valor = linea.substring(igual + 1);
    clave.trim();
    valor.trim();
    if (clave == "red") wifiRed = valor;
    else if (clave == "clave") wifiClave = valor;
    else if (clave == "compu") compu = valor;
  }
  f.close();
  Serial.printf("[WiFi] Red \"%s\", compu %s\n", wifiRed.c_str(), compu.c_str());
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n===== RECORDER =====");

  pinMode(TOUCH_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  analogReadResolution(12);
  analogSetPinAttenuation(MIC_PIN, ADC_11db);
  armarTablaCrc();
  prefs.begin("grabador");
  char id[8];
  snprintf(id, sizeof id, "%06llx", ESP.getEfuseMac() & 0xFFFFFF);
  idPlaca = id;

  xTaskCreatePinnedToCore(tareaPantalla, "pantalla", 6144, nullptr, 1, nullptr, 0);

  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, SPI)) {
    Serial.println("[SD] FALLA: no monta");
    setModo(SIN_SD);
    for (;;) vTaskDelay(portMAX_DELAY);
  }
  SD.mkdir("/grab");
  leerWifiTxt();
  recuperarCortadas();
  listarGrabaciones();
  actualizarEspacio();
  contarPendientes();

  libres = xQueueCreate(N_BUFS, sizeof(uint8_t));
  llenos = xQueueCreate(N_BUFS, sizeof(uint8_t));
  xTaskCreatePinnedToCore(tareaMuestreo, "muestreo", 4096, nullptr, 5, &tareaMuestreoH, 1);
  xTaskCreatePinnedToCore(tareaEscritura, "escritura", 4096, nullptr, 4, nullptr, 0);
  xTaskCreatePinnedToCore(tareaTouch, "touch", 2048, nullptr, 3, nullptr, 0);
  setModo(LISTO);
  xTaskCreatePinnedToCore(tareaControl, "control", 8192, nullptr, 2, nullptr, 0);
}

// loop() corre en core 1, que mientras graba queda ocupado midiendo el micrófono:
// la lógica vive en tareaControl, en core 0, y loop() no se usa.
void loop() {
  vTaskDelete(nullptr);
}
