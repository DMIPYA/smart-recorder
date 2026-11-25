#include <Arduino.h>
#include <SD.h>
#include <SPI.h>

#ifndef SMART_RECORDER_MAX4466
#define SMART_RECORDER_MAX4466 0
#endif

#if SMART_RECORDER_MAX4466
#include <esp_adc/adc_continuous.h>
#else
#include <ESP_I2S.h>
#endif

constexpr int kButton = 9;  // Onboard BOOT button. Use it after power-up.
constexpr int kLed = 8;  // Active low on the usual SuperMini.
#if SMART_RECORDER_MAX4466
constexpr adc_channel_t kMicChannel = ADC_CHANNEL_4;  // ADC1 on GPIO4.
#else
constexpr int kBclk = 3;
constexpr int kWordSelect = 4;
constexpr int kMicData = 5;
#endif
constexpr int kSdClock = 6;
constexpr int kSdMiso = 7;
constexpr int kSdMosi = 10;
constexpr int kSdSelect = 1;
constexpr uint32_t kRate = 16000;
constexpr uint32_t kBytesPerSecond = kRate * sizeof(int16_t);
constexpr uint32_t kClipBytes = 5 * 60 * kBytesPerSecond;
constexpr uint32_t kCheckpointMs = 10000;
constexpr size_t kWavHeaderSize = 44;
constexpr size_t kFrames = 512;

File clip;
#if SMART_RECORDER_MAX4466
adc_continuous_handle_t microphone = nullptr;
bool adcStarted = false;
adc_digi_output_data_t adcSamples[kFrames];
static_assert(sizeof(adcSamples[0]) == SOC_ADC_DIGI_RESULT_BYTES);
int32_t micDcQ8 = 0;
bool micDcInitialized = false;
#else
I2SClass microphone;
int32_t stereo[kFrames * 2];
#endif
int16_t mono[kFrames];
char clipPath[16];
uint32_t nextFile = 1;
uint32_t pcmBytes = 0;
uint32_t checkpointAt = 0;
uint32_t lastAudioAt = 0;
uint32_t buttonChangedAt = 0;
int buttonReading = HIGH;
int buttonStable = HIGH;
bool recording = false;
bool fault = false;

void put16(uint8_t *out, uint16_t value) {
  out[0] = static_cast<uint8_t>(value);
  out[1] = static_cast<uint8_t>(value >> 8);
}

void put32(uint8_t *out, uint32_t value) {
  put16(out, static_cast<uint16_t>(value));
  put16(out + 2, static_cast<uint16_t>(value >> 16));
}

bool writeWavHeader() {
  uint8_t header[kWavHeaderSize] = {};
  memcpy(header, "RIFF", 4);
  put32(header + 4, 36 + pcmBytes);
  memcpy(header + 8, "WAVEfmt ", 8);
  put32(header + 16, 16);
  put16(header + 20, 1);  // PCM
  put16(header + 22, 1);  // Mono
  put32(header + 24, kRate);
  put32(header + 28, kBytesPerSecond);
  put16(header + 32, 2);  // Bytes per sample frame
  put16(header + 34, 16);
  memcpy(header + 36, "data", 4);
  put32(header + 40, pcmBytes);

  if (!clip.seek(0) || clip.write(header, sizeof(header)) != sizeof(header)) {
    return false;
  }
  clip.flush();
  checkpointAt = millis();
  return clip.seek(kWavHeaderSize + pcmBytes);
}

bool openClip() {
  while (nextFile <= 99999) {
    snprintf(clipPath, sizeof(clipPath), "/REC%05lu.WAV",
             static_cast<unsigned long>(nextFile++));
    if (SD.exists(clipPath)) {
      continue;
    }
    clip = SD.open(clipPath, FILE_WRITE);
    if (!clip) {
      return false;
    }
    pcmBytes = 0;
    if (!writeWavHeader()) {
      clip.close();
      return false;
    }
    Serial.printf("Recording %s\n", clipPath);
    return true;
  }
  return false;
}

bool closeClip() {
  if (!clip) {
    return true;
  }
  const bool ok = writeWavHeader();
  clip.close();
  return ok;
}

bool startMicrophone() {
#if SMART_RECORDER_MAX4466
  adc_continuous_handle_cfg_t handleConfig = {};
  handleConfig.max_store_buf_size = 32 * 1024;
  handleConfig.conv_frame_size = sizeof(adcSamples);
  if (adc_continuous_new_handle(&handleConfig, &microphone) != ESP_OK) {
    return false;
  }

  adc_digi_pattern_config_t input = {};
  input.atten = ADC_ATTEN_DB_12;
  input.channel = kMicChannel;
  input.unit = ADC_UNIT_1;
  input.bit_width = ADC_BITWIDTH_12;

  adc_continuous_config_t config = {};
  config.pattern_num = 1;
  config.adc_pattern = &input;
  config.sample_freq_hz = kRate;
  config.conv_mode = ADC_CONV_SINGLE_UNIT_1;
  config.format = ADC_DIGI_OUTPUT_FORMAT_TYPE2;
  if (adc_continuous_config(microphone, &config) != ESP_OK) {
    return false;
  }

  micDcInitialized = false;
  adcStarted = adc_continuous_start(microphone) == ESP_OK;
  return adcStarted;
#else
  microphone.setPins(kBclk, kWordSelect, -1, kMicData);
  microphone.setTimeout(120);
  // The INMP441 sends signed 24-bit samples in the left 32-bit I2S slot.
  return microphone.begin(I2S_MODE_STD, kRate, I2S_DATA_BIT_WIDTH_32BIT,
                          I2S_SLOT_MODE_STEREO);
#endif
}

void stopMicrophone() {
#if SMART_RECORDER_MAX4466
  if (microphone != nullptr) {
    if (adcStarted) {
      adc_continuous_stop(microphone);
      adcStarted = false;
    }
    adc_continuous_deinit(microphone);
    microphone = nullptr;
  }
#else
  microphone.end();
#endif
}

void stopRecording(const char *error) {
  recording = false;
  stopMicrophone();
  if (!closeClip() && error == nullptr) {
    error = "Could not finish WAV header";
  }
  SD.end();
  fault = error != nullptr;
  Serial.println(fault ? error : "Stopped. It is safe to remove the card.");
}

void startRecording() {
  fault = false;
  if (!SD.begin(kSdSelect, SPI, 4000000)) {
    stopRecording("microSD mount failed");
    return;
  }
  if (!openClip()) {
    stopRecording("Could not open a new WAV file");
    return;
  }
  if (!startMicrophone()) {
    stopRecording("Could not start audio input");
    return;
  }
  lastAudioAt = millis();
  recording = true;
}

bool buttonPressed() {
  const int reading = digitalRead(kButton);
  if (reading != buttonReading) {
    buttonReading = reading;
    buttonChangedAt = millis();
  }
  if (buttonReading != buttonStable && millis() - buttonChangedAt >= 30) {
    buttonStable = buttonReading;
    return buttonStable == LOW;
  }
  return false;
}

void captureBlock() {
#if SMART_RECORDER_MAX4466
  uint32_t bytes = 0;
  const esp_err_t status = adc_continuous_read(
      microphone, reinterpret_cast<uint8_t *>(adcSamples), sizeof(adcSamples), &bytes, 120);
  if (status != ESP_OK && status != ESP_ERR_TIMEOUT) {
    stopRecording(status == ESP_ERR_INVALID_STATE ? "ADC buffer overflow" : "ADC read failed");
    return;
  }
  constexpr size_t frameBytes = sizeof(adcSamples[0]);
#else
  const size_t bytes = microphone.readBytes(reinterpret_cast<char *>(stereo), sizeof(stereo));
  constexpr size_t frameBytes = 2 * sizeof(int32_t);
#endif
  if (bytes == 0) {
    if (millis() - lastAudioAt >= 2000) {
      stopRecording("No audio samples for two seconds");
    }
    return;
  }
  if (bytes % frameBytes != 0) {
    stopRecording("Incomplete audio sample");
    return;
  }
  lastAudioAt = millis();

  const size_t frames = bytes / frameBytes;
  for (size_t i = 0; i < frames; ++i) {
#if SMART_RECORDER_MAX4466
    if (adcSamples[i].type2.channel != kMicChannel) {
      stopRecording("Unexpected ADC channel");
      return;
    }
    // The MAX4466 output sits near half its supply voltage in silence.
    // Track and remove that DC offset before writing signed PCM samples.
    const int32_t sampleQ8 = static_cast<int32_t>(adcSamples[i].type2.data) * 256;
    if (!micDcInitialized) {
      micDcQ8 = sampleQ8;
      micDcInitialized = true;
    }
    const int32_t centeredQ8 = sampleQ8 - micDcQ8;
    micDcQ8 += centeredQ8 / 128;
    const int32_t pcm = centeredQ8 / 16;
    mono[i] = static_cast<int16_t>(constrain(pcm, -32768, 32767));
#else
    mono[i] = static_cast<int16_t>(stereo[i * 2] >> 16);
#endif
  }
  const size_t bytesToWrite = frames * sizeof(mono[0]);
  const size_t written = clip.write(reinterpret_cast<const uint8_t *>(mono), bytesToWrite);
  pcmBytes += written - written % sizeof(mono[0]);
  if (written != bytesToWrite) {
    stopRecording("microSD write failed or card is full");
    return;
  }
  if (pcmBytes >= kClipBytes) {
    if (!closeClip() || !openClip()) {
      stopRecording("Could not roll over to the next WAV file");
    }
  } else if (millis() - checkpointAt >= kCheckpointMs && !writeWavHeader()) {
    stopRecording("Could not update WAV header");
  }
}

void setup() {
  pinMode(kLed, OUTPUT);
  digitalWrite(kLed, HIGH);
  pinMode(kButton, INPUT_PULLUP);
  buttonReading = buttonStable = digitalRead(kButton);
  Serial.begin(115200);
  SPI.begin(kSdClock, kSdMiso, kSdMosi, kSdSelect);
  Serial.println("Ready. Press the record button to start.");
}

void loop() {
  if (buttonPressed()) {
    if (recording) {
      stopRecording(nullptr);
    } else {
      startRecording();
    }
  }

  const bool ledOn = recording || (fault && (millis() / 180) % 2 == 0);
  digitalWrite(kLed, ledOn ? LOW : HIGH);
  if (recording) {
    captureBlock();
  } else {
    delay(5);
  }
}
