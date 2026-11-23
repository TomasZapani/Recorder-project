// Diagnóstico completo de la placa ESP32 (sin librerías externas).
// Ideal: placa sola, sólo el USB. Si hay OLED/SD conectadas, las detecta y
// saltea sus pines en la prueba de GPIO. Resultado por serie a 115200.
#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <esp_partition.h>
#include <esp_chip_info.h>
#include <esp_mac.h>
#include <driver/gpio.h>

int fallas = 0, avisos = 0;
void ok(const char* t) { Serial.printf("  [OK]    %s\n", t); }
void mal(const char* t) { Serial.printf("  [FALLA] %s\n", t); fallas++; }
void aviso(const char* t) { Serial.printf("  [AVISO] %s\n", t); avisos++; }
char buf[160];

bool oledConectada = false, sdConectada = false;

void pruebaChip() {
  Serial.println("\n== Chip ==");
  esp_chip_info_t ci;
  esp_chip_info(&ci);
  uint8_t mac[6];
  esp_efuse_mac_get_default(mac);
  Serial.printf("  Modelo %s rev %d, %d nucleos, %d MHz\n", ESP.getChipModel(), ci.revision, ci.cores, ESP.getCpuFreqMHz());
  Serial.printf("  MAC %02X:%02X:%02X:%02X:%02X:%02X\n", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  Serial.printf("  Flash %u MB, PSRAM %u KB, heap libre %u KB\n", ESP.getFlashChipSize() >> 20, ESP.getPsramSize() >> 10, ESP.getFreeHeap() >> 10);
  Serial.printf("  Temperatura interna aprox: %.1f C\n", temperatureRead());
  if (ci.cores == 2) ok("Dos nucleos"); else mal("No se ven los dos nucleos");
}

volatile uint32_t cuenta0 = 0, cuenta1 = 0;
void tareaCuenta(void* p) {
  volatile uint32_t* c = (volatile uint32_t*)p;
  for (;;) {
    (*c)++;
    if ((*c & 0xFFFF) == 0) vTaskDelay(1);
  }
}

void pruebaNucleos() {
  Serial.println("\n== Nucleos ==");
  TaskHandle_t a, b;
  xTaskCreatePinnedToCore(tareaCuenta, "c0", 2048, (void*)&cuenta0, 1, &a, 0);
  xTaskCreatePinnedToCore(tareaCuenta, "c1", 2048, (void*)&cuenta1, 1, &b, 1);
  delay(500);
  vTaskDelete(a);
  vTaskDelete(b);
  Serial.printf("  Cuentas: nucleo0=%u nucleo1=%u\n", cuenta0, cuenta1);
  if (cuenta0 > 1000 && cuenta1 > 1000) ok("Los dos nucleos ejecutan"); else mal("Un nucleo no ejecuta");
}

void pruebaRAM() {
  Serial.println("\n== RAM ==");
  size_t n = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) - 1024;
  uint32_t* m = (uint32_t*)malloc(n);
  if (!m) { mal("No se pudo reservar RAM"); return; }
  size_t w = n / 4;
  bool bien = true;
  const uint32_t patrones[] = {0x00000000, 0xFFFFFFFF, 0xAAAAAAAA, 0x55555555};
  for (uint32_t pat : patrones) {
    for (size_t i = 0; i < w; i++) m[i] = pat ^ i;
    for (size_t i = 0; i < w && bien; i++) if (m[i] != (pat ^ i)) bien = false;
  }
  free(m);
  snprintf(buf, sizeof buf, "%u KB de RAM escritos y leidos con 4 patrones", (unsigned)(n >> 10));
  if (bien) ok(buf); else mal("Error de RAM (dato leido distinto al escrito)");
}

void pruebaFlash() {
  Serial.println("\n== Memoria flash ==");
  const esp_partition_t* p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "spiffs");
  if (!p) { aviso("No hay particion spiffs para probar escritura"); return; }
  const size_t T = 4096;
  int sectores = min((int)(p->size / T), 16);
  uint8_t* e = (uint8_t*)malloc(T);
  uint8_t* l = (uint8_t*)malloc(T);
  bool bien = true;
  for (int s = 0; s < sectores && bien; s++) {
    for (size_t i = 0; i < T; i++) e[i] = (uint8_t)(i * 7 + s * 13);
    if (esp_partition_erase_range(p, s * T, T) != ESP_OK ||
        esp_partition_write(p, s * T, e, T) != ESP_OK ||
        esp_partition_read(p, s * T, l, T) != ESP_OK || memcmp(e, l, T)) bien = false;
  }
  free(e);
  free(l);
  snprintf(buf, sizeof buf, "%d sectores de flash borrados, escritos y verificados", sectores);
  if (bien) ok(buf); else mal("Error escribiendo/leyendo la flash");
}

void pruebaI2C() {
  Serial.println("\n== I2C / OLED (SDA 22, SCL 23) ==");
  Wire.begin(22, 23);
  int n = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      n++;
      if (a == 0x3C || a == 0x3D) oledConectada = true;
    }
  }
  Serial.printf("  %d direcciones responden (lo normal con la OLED sola es 1)\n", n);
  if (n > 2) aviso("Bus I2C con ruido: responden direcciones que no existen (cables, OLED o tension)");
  if (oledConectada) ok("OLED responde");
  else Serial.println("  (no hay OLED conectada; se prueban 22/23 como GPIO)");
  Wire.end();
}

void pruebaSD() {
  Serial.println("\n== microSD (SCK 17, MISO 18, MOSI 19, CS 5) ==");
  SPI.begin(17, 18, 19, 5);
  if (!SD.begin(5, SPI)) {
    Serial.println("  (no hay SD o no responde; se prueban sus pines como GPIO)");
    SPI.end();
    return;
  }
  sdConectada = true;
  Serial.printf("  Tarjeta %llu MB, tipo %d\n", SD.cardSize() >> 20, SD.cardType());
  File f = SD.open("/diag.bin", FILE_WRITE);
  bool bien = f;
  uint8_t b[512];
  for (int k = 0; k < 64 && bien; k++) {
    for (int i = 0; i < 512; i++) b[i] = i ^ k;
    bien = f.write(b, 512) == 512;
  }
  if (f) f.close();
  f = SD.open("/diag.bin");
  if (!f) bien = false;
  for (int k = 0; k < 64 && bien; k++) {
    if (f.read(b, 512) != 512) { bien = false; break; }
    for (int i = 0; i < 512; i++) if (b[i] != (uint8_t)(i ^ k)) bien = false;
  }
  if (f) f.close();
  SD.remove("/diag.bin");
  if (bien) ok("SD: 32 KB escritos y verificados"); else mal("SD: falla escribiendo/leyendo");
}

void pruebaGPIO() {
  Serial.println("\n== GPIO (pull-up / pull-down / manejar y releer) ==");
  int pines[] = {2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33};
  int malos = 0, probados = 0;
  for (int p : pines) {
    if (oledConectada && (p == 22 || p == 23)) continue;
    if (sdConectada && (p == 5 || p == 17 || p == 18 || p == 19)) continue;
    gpio_num_t g = (gpio_num_t)p;
    gpio_reset_pin(g);
    gpio_set_direction(g, GPIO_MODE_INPUT);
    gpio_set_pull_mode(g, GPIO_PULLUP_ONLY);
    delayMicroseconds(300);
    int up = gpio_get_level(g);
    gpio_set_pull_mode(g, GPIO_PULLDOWN_ONLY);
    delayMicroseconds(300);
    int dn = gpio_get_level(g);
    gpio_set_pull_mode(g, GPIO_FLOATING);
    gpio_set_direction(g, GPIO_MODE_INPUT_OUTPUT);
    gpio_set_level(g, 1);
    delayMicroseconds(50);
    int h = gpio_get_level(g);
    gpio_set_level(g, 0);
    delayMicroseconds(50);
    int lo = gpio_get_level(g);
    gpio_reset_pin(g);
    probados++;
    if (up == 1 && dn == 0 && h == 1 && lo == 0) continue;
    malos++;
    snprintf(buf, sizeof buf, "GPIO%d: pullup=%d pulldown=%d alto=%d bajo=%d%s", p, up, dn, h, lo,
             p == 27 ? " (si el touch TTP223 esta conectado es normal)" : "");
    if (p == 27) aviso(buf); else mal(buf);
  }
  snprintf(buf, sizeof buf, "%d de %d pines responden bien", probados - malos, probados);
  if (malos == 0) ok(buf); else Serial.printf("  %s\n", buf);
}

void pruebaADC() {
  Serial.println("\n== ADC y DAC ==");
  int pines[] = {32, 33, 34, 35, 36, 39};
  for (int p : pines) Serial.printf("  GPIO%d: %4d\n", p, analogRead(p));
  // El DAC del 25 genera tension y el ADC2 del mismo pin la mide (WiFi apagado).
  int niveles[] = {50, 128, 200};
  bool bien = true;
  for (int v : niveles) {
    dacWrite(25, v);
    delay(5);
    int r = analogRead(25), esperado = v * 4095 / 255;
    Serial.printf("  DAC25=%3d -> ADC lee %4d (ideal ~%4d)\n", v, r, esperado);
    if (abs(r - esperado) > 900) bien = false;
  }
  dacDisable(25);
  if (bien) ok("DAC y ADC funcionan"); else aviso("DAC->ADC lejos de lo esperado");
}

void pruebaTouch() {
  Serial.println("\n== Sensores tactiles internos (no toques nada) ==");
  int pines[] = {4, 0, 2, 15, 13, 12, 14, 33, 32};
  int malos = 0;
  for (int p : pines) {
    int v = touchRead(p);
    Serial.printf("  T GPIO%-2d = %d\n", p, v);
    if (v == 0) malos++;
  }
  if (malos == 0) ok("Todos los canales tactiles leen"); else aviso("Algun canal tactil lee 0 (posible pin en corto)");
}

void pruebaWiFi() {
  Serial.println("\n== WiFi ==");
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  int n = WiFi.scanNetworks();
  if (n <= 0) { mal("El escaneo WiFi no encontro redes"); return; }
  for (int i = 0; i < min(n, 8); i++) Serial.printf("  %-24s %4d dBm  canal %d\n", WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i));
  int mejor = WiFi.RSSI(0);
  snprintf(buf, sizeof buf, "Radio WiFi OK: %d redes, la mas fuerte %d dBm", n, mejor);
  ok(buf);
  if (mejor < -80) aviso("Senal muy baja: antena o distancia al router");
  WiFi.scanDelete();
  WiFi.mode(WIFI_OFF);
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n\n######## DIAGNOSTICO ESP32 ########");
  Serial.printf("Motivo del ultimo reinicio: %d (1=encendido, 9=brownout)\n", (int)esp_reset_reason());
  if (esp_reset_reason() == ESP_RST_BROWNOUT) aviso("Se reinicio por baja tension (brownout)");
  pruebaChip();
  pruebaNucleos();
  pruebaRAM();
  pruebaFlash();
  pruebaI2C();
  pruebaSD();
  pruebaGPIO();
  pruebaADC();
  pruebaTouch();
  pruebaWiFi();
  Serial.printf("\n######## RESULTADO: %d fallas, %d avisos ########\n", fallas, avisos);
  pinMode(2, OUTPUT);
}

void loop() {
  digitalWrite(2, !digitalRead(2));  // LED azul: lento = todo bien, rapido = hubo fallas
  delay(fallas ? 150 : 600);
}
