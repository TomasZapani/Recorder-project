// Firmware de prueba del micrófono: samplea GPIO 35 a 16 kHz y manda bloques
// de 512 muestras uint16 por serie a 921600 baudios, con header "AUD0".
// Lo lee grabar_wav.py.

const int MIC_PIN = 35;  // ADC1_CH7 — coincide con el cableado
const int SAMPLE_RATE = 16000;
const int BLOCK = 512;

uint16_t buf[BLOCK];

void setup() {
  Serial.begin(921600);
  analogReadResolution(12);
  analogSetPinAttenuation(MIC_PIN, ADC_11db);
}

void loop() {
  static uint64_t n = 0;
  static uint64_t t0 = micros();

  for (int i = 0; i < BLOCK; i++) {
    uint64_t target = t0 + (n * 1000000ULL) / SAMPLE_RATE;
    while ((int64_t)(micros() - target) < 0) { }
    buf[i] = analogRead(MIC_PIN);
    n++;
  }

  Serial.write((const uint8_t*)"AUD0", 4);
  Serial.write((const uint8_t*)buf, BLOCK * 2);
}
