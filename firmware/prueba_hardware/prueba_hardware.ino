// Prueba de hardware: OLED, touch y microSD.
// Todo se informa por la pantalla y por el serie a 115200 baudios.
// Requiere las librerías "Adafruit SSD1306" y "Adafruit GFX" (Library Manager).

#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

const int OLED_SDA = 22;
const int OLED_SCL = 23;
const uint8_t OLED_ADDR = 0x3C;
const int TOUCH_PIN = 27;
const int SD_SCK = 17, SD_MISO = 18, SD_MOSI = 19, SD_CS = 5;

Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledOk = false;
String sdEstado = "SD: ?";
int toques = 0;
bool tocandoAntes = false;

void mostrar() {
  if (!oledOk) return;
  oled.clearDisplay();
  oled.setTextSize(1);
  oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(0, 0);
  oled.println("PRUEBA HARDWARE");
  oled.println();
  oled.println("OLED: OK");
  oled.println(sdEstado);
  oled.println();
  oled.print("Toques: ");
  oled.println(toques);
  oled.print(digitalRead(TOUCH_PIN) ? "(tocando)" : "");
  oled.display();
}

void escanearI2C() {
  Serial.println("[I2C] Buscando dispositivos...");
  int n = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[I2C] Encontrado en 0x%02X\n", a);
      n++;
    }
  }
  if (n == 0) Serial.println("[I2C] Nada. Revisar SDA=22, SCL=23, VCC y GND de la OLED");
}

void probarSD() {
  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, SPI)) {
    sdEstado = "SD: NO MONTA";
    Serial.println("[SD] FALLA: no monta. Revisar cableado, 5V del modulo, tarjeta en FAT32");
    return;
  }
  uint8_t tipo = SD.cardType();
  const char* nombres[] = {"ninguna", "MMC", "SD", "SDHC", "?"};
  Serial.printf("[SD] Tipo: %s, tamano: %llu MB, usado: %llu MB\n",
                nombres[tipo <= 3 ? tipo : 4], SD.cardSize() / (1024 * 1024),
                SD.usedBytes() / (1024 * 1024));

  const char* ruta = "/prueba.txt";
  const char* texto = "Hola desde la ESP32";
  File f = SD.open(ruta, FILE_WRITE);
  if (!f) {
    sdEstado = "SD: NO ESCRIBE";
    Serial.println("[SD] FALLA: monta pero no puede crear el archivo");
    return;
  }
  f.print(texto);
  f.close();

  f = SD.open(ruta, FILE_READ);
  String leido = f ? f.readString() : "";
  if (f) f.close();
  if (leido == texto) {
    sdEstado = "SD: OK";
    Serial.println("[SD] OK: escribio y leyo /prueba.txt correctamente");
  } else {
    sdEstado = "SD: LEE MAL";
    Serial.printf("[SD] FALLA: leyo \"%s\"\n", leido.c_str());
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("===== PRUEBA DE HARDWARE =====");

  pinMode(TOUCH_PIN, INPUT);

  Wire.begin(OLED_SDA, OLED_SCL);
  escanearI2C();
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  Serial.println(oledOk ? "[OLED] OK" : "[OLED] FALLA: no inicia en 0x3C");

  probarSD();
  mostrar();
  Serial.println("[TOUCH] Toca el sensor: cada toque se cuenta aca y en la pantalla");
}

void loop() {
  bool tocando = digitalRead(TOUCH_PIN) == HIGH;
  if (tocando && !tocandoAntes) {
    toques++;
    Serial.printf("[TOUCH] Toque %d\n", toques);
  }
  if (tocando != tocandoAntes) mostrar();
  tocandoAntes = tocando;
  delay(20);
}
