/*
 * LEGO sorter conveyor controller — Arduino UNO R4 WiFi + TB6600
 *
 * Hardware (as-built 2026-07-18): see hardware/feeder/wiring.md
 *   D2 = STEP (PUL), D3 = DIR  — swap jumpers if no motion
 *   Motor: STEPPERONLINE 17HS19-2004S1 (200 full steps/rev)
 *
 * Default speed: ~1 motor revolution / 18.8 s (18820 ms) — tuned on belt.
 * Set PULSES_PER_REV to match the TB6600 microstep DIP (SW1–SW3).
 *
 * Serial (115200):
 *   START STOP ADVANCE STATUS HELP
 *   FORWARD REVERSE
 *   SPEED <ms_per_rev>   e.g. SPEED 18820
 *   FASTER SLOWER        (step from current; floor ~6 s/rev)
 *
 * WiFi: joins WIFI_SSID from wifi_secrets.h; if that fails, softAP
 *   SSID "LegoConveyor" / pass "legosort" → http://192.168.4.1
 */

#include <WiFiS3.h>
#include "wifi_secrets.h"

// --- pins -----------------------------------------------------------------
const int PIN_STEP = 2;
const int PIN_DIR = 3;
// Set true if a HIGH on DIR is the belt-forward direction; flip if reversed.
const bool DIR_FORWARD_HIGH = false;
// TB6600 common-anode (PUL+/DIR+ → 5V, PUL−/DIR− → MCU) needs active-LOW
// step pulses. Set false only if you wired common-cathode (PUL− → GND).
const bool STEP_ACTIVE_LOW = true;

// --- motion ---------------------------------------------------------------
// Match TB6600 microstep DIP (SW1–SW3). Your ~11 s/rev at "3 s" setting
// means the driver is almost certainly on 32× (6400 pulse/rev), not 8×.
//   200  = full step
//   800  = 4×
//  1600  = 8×
//  3200  = 16×
//  6400  = 32×  ← default for this belt
const long PULSES_PER_REV = 6400;
const unsigned long DEFAULT_MS_PER_REV = 18820;  // tuned on belt
const unsigned long MIN_MS_PER_REV = 6000;         // as fast as we want (~6 s/rev)
const unsigned long MAX_MS_PER_REV = 30000;        // slower cap
// FASTER/SLOWER multiply period (more noticeable than ±500 ms).
const float SPEED_FASTER_FACTOR = 0.75f;
const float SPEED_SLOWER_FACTOR = 1.33f;

// ADVANCE indexes this many motor revolutions (no IR yet).
const float ADVANCE_REVS = 1.0f;

// Max step pulses to emit in one loop() pass when catching up after WiFi work.
const int MAX_STEPS_PER_LOOP = 40;

// --- WiFi / softAP --------------------------------------------------------
const char *AP_SSID = "LegoConveyor";
const char *AP_PASS = "legosort";  // min 8 chars for WPA
const unsigned long WIFI_CONNECT_TIMEOUT_MS = 45000;

WiFiServer server(80);

// --- state ----------------------------------------------------------------
enum RunMode { MODE_IDLE, MODE_RUN, MODE_ADVANCE };

RunMode mode = MODE_IDLE;
long stepsRemaining = 0;
unsigned long nextStepUs = 0;
unsigned long msPerRev = DEFAULT_MS_PER_REV;
volatile unsigned long stepPeriodUs =
    (DEFAULT_MS_PER_REV * 1000UL) / (unsigned long)PULSES_PER_REV;
bool directionForward = true;
String serialLine;
bool wifiStaOk = false;
bool wifiApOk = false;

void applySpeed(unsigned long newMsPerRev) {
  if (newMsPerRev < MIN_MS_PER_REV) {
    newMsPerRev = MIN_MS_PER_REV;
  }
  if (newMsPerRev > MAX_MS_PER_REV) {
    newMsPerRev = MAX_MS_PER_REV;
  }
  msPerRev = newMsPerRev;
  stepPeriodUs = (msPerRev * 1000UL) / (unsigned long)PULSES_PER_REV;
  if (stepPeriodUs < 1) {
    stepPeriodUs = 1;
  }
  // Apply immediately if currently stepping (don't wait out old period).
  if (mode != MODE_IDLE) {
    nextStepUs = micros();
  }
  Serial.print(F("speed ms_per_rev="));
  Serial.print(msPerRev);
  Serial.print(F("  step_us="));
  Serial.println(stepPeriodUs);
}

void applyDirection() {
  bool level = directionForward ? DIR_FORWARD_HIGH : !DIR_FORWARD_HIGH;
  digitalWrite(PIN_DIR, level ? HIGH : LOW);
}

void setDirectionForward(bool forward) {
  directionForward = forward;
  applyDirection();
}

void stopMotor() {
  mode = MODE_IDLE;
  stepsRemaining = 0;
}

void startContinuous() {
  applyDirection();
  mode = MODE_RUN;
  nextStepUs = micros();
}

void startAdvance() {
  applyDirection();
  stepsRemaining = (long)(ADVANCE_REVS * (float)PULSES_PER_REV + 0.5f);
  if (stepsRemaining < 1) {
    stepsRemaining = 1;
  }
  mode = MODE_ADVANCE;
  nextStepUs = micros();
}

const char *statusString() {
  switch (mode) {
    case MODE_RUN:
    case MODE_ADVANCE:
      return "RUNNING";
    case MODE_IDLE:
    default:
      return "IDLE";
  }
}

const char *dirString() {
  return directionForward ? "FWD" : "REV";
}

void pulseStep() {
  // Many TB6600 clones want ≥10–20 µs pulse width; 5 µs is often too short.
  const int pulseUs = 20;
  if (STEP_ACTIVE_LOW) {
    digitalWrite(PIN_STEP, LOW);
    delayMicroseconds(pulseUs);
    digitalWrite(PIN_STEP, HIGH);
  } else {
    digitalWrite(PIN_STEP, HIGH);
    delayMicroseconds(pulseUs);
    digitalWrite(PIN_STEP, LOW);
  }
}

void serviceMotor() {
  if (mode == MODE_IDLE) {
    return;
  }

  // Emit as many due steps as we can this pass. WiFi/HTTP work can make
  // loop() slower than one step period; without catch-up, FASTER looks like
  // a no-op and you get a fixed ~loop-rate speed (~11 s/rev at 32×).
  unsigned long period = stepPeriodUs;
  if (period < 1) {
    period = 1;
  }

  int emitted = 0;
  while (emitted < MAX_STEPS_PER_LOOP) {
    unsigned long now = micros();
    if ((long)(now - nextStepUs) < 0) {
      break;
    }

    pulseStep();
    emitted++;
    nextStepUs += period;

    if (mode == MODE_ADVANCE) {
      stepsRemaining--;
      if (stepsRemaining <= 0) {
        stopMotor();
        Serial.println(F("status=IDLE"));
        return;
      }
    }
  }

  // If we were badly behind, resync so we don't spiral.
  unsigned long now = micros();
  if ((long)(now - nextStepUs) > 0) {
    nextStepUs = now;
  }
}

void printStatus() {
  Serial.print(F("status="));
  Serial.print(statusString());
  Serial.print(F("  dir="));
  Serial.print(dirString());
  Serial.print(F("  ms_per_rev="));
  Serial.print(msPerRev);
  Serial.print(F("  ip="));
  if (wifiStaOk) {
    Serial.println(WiFi.localIP());
  } else if (wifiApOk) {
    Serial.print(WiFi.localIP());
    Serial.println(F(" (softAP)"));
  } else {
    Serial.println(F("0.0.0.0"));
  }
}

void handleCommand(String cmd) {
  cmd.trim();
  // Keep a copy before uppercasing for SPEED parsing
  String raw = cmd;
  cmd.toUpperCase();
  if (cmd.length() == 0) {
    return;
  }

  if (cmd == "START") {
    startContinuous();
    printStatus();
  } else if (cmd == "STOP") {
    stopMotor();
    printStatus();
  } else if (cmd == "ADVANCE") {
    startAdvance();
    printStatus();
  } else if (cmd == "FORWARD" || cmd == "FWD") {
    setDirectionForward(true);
    printStatus();
  } else if (cmd == "REVERSE" || cmd == "REV") {
    setDirectionForward(false);
    printStatus();
  } else if (cmd == "FASTER") {
    applySpeed((unsigned long)(msPerRev * SPEED_FASTER_FACTOR));
    printStatus();
  } else if (cmd == "SLOWER") {
    applySpeed((unsigned long)(msPerRev * SPEED_SLOWER_FACTOR));
    printStatus();
  } else if (cmd.startsWith("SPEED")) {
    // SPEED 3000  → milliseconds per motor revolution
    int space = raw.indexOf(' ');
    if (space < 0) {
      Serial.println(F("Usage: SPEED <ms_per_rev>   e.g. SPEED 3000"));
      return;
    }
    long value = raw.substring(space + 1).toInt();
    if (value <= 0) {
      Serial.println(F("SPEED must be a positive number (ms per rev)"));
      return;
    }
    applySpeed((unsigned long)value);
    printStatus();
  } else if (cmd == "STATUS") {
    printStatus();
  } else if (cmd == "HELP" || cmd == "?") {
    Serial.println(F("Commands:"));
    Serial.println(F("  START STOP ADVANCE STATUS"));
    Serial.println(F("  FORWARD REVERSE"));
    Serial.println(F("  SPEED <ms_per_rev>   (default 18820)"));
    Serial.println(F("  FASTER SLOWER        (step from current; floor ~6 s/rev)"));
    Serial.print(F("PULSES_PER_REV="));
    Serial.println(PULSES_PER_REV);
  } else {
    Serial.print(F("Unknown command: "));
    Serial.println(cmd);
  }
}

void serviceSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialLine.length() > 0) {
        handleCommand(serialLine);
        serialLine = "";
      }
    } else {
      serialLine += c;
      if (serialLine.length() > 64) {
        serialLine = "";
      }
    }
  }
}

bool connectSta() {
  if (String(WIFI_SSID) == "YOUR_SSID_HERE" || String(WIFI_SSID).length() == 0) {
    Serial.println(F("wifi_secrets.h not configured — skipping STA"));
    return false;
  }

  WiFi.disconnect();
  delay(200);

  Serial.print(F("Connecting to: "));
  Serial.println(WIFI_SSID);
  Serial.println(F("(UNO R4 WiFi is 2.4 GHz only)"));
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  unsigned long start = millis();
  while (true) {
    if (millis() - start > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println();
      Serial.print(F("WiFi failed. status="));
      Serial.print(WiFi.status());
      Serial.print(F(" ip="));
      Serial.println(WiFi.localIP());
      return false;
    }

    IPAddress ip = WiFi.localIP();
    if (WiFi.status() == WL_CONNECTED && ip != IPAddress(0, 0, 0, 0)) {
      Serial.println();
      Serial.print(F("WiFi OK  ip="));
      Serial.println(ip);
      return true;
    }

    delay(500);
    Serial.print('.');
    if (((millis() - start) / 500) % 10 == 0) {
      Serial.print(F(" s="));
      Serial.print(WiFi.status());
      Serial.print(F(" ip="));
      Serial.println(WiFi.localIP());
    }
  }
}

bool startSoftAp() {
  Serial.print(F("Starting softAP "));
  Serial.println(AP_SSID);
  if (!WiFi.beginAP(AP_SSID, AP_PASS)) {
    Serial.println(F("softAP failed"));
    return false;
  }
  delay(500);
  Serial.print(F("softAP ip="));
  Serial.println(WiFi.localIP());
  Serial.println(F("Join WiFi 'LegoConveyor' / pass 'legosort' → http://192.168.4.1"));
  return true;
}

void sendHttpResponse(WiFiClient &client, const char *body) {
  client.println(F("HTTP/1.1 200 OK"));
  client.println(F("Content-Type: text/html; charset=utf-8"));
  client.println(F("Connection: close"));
  client.println();
  client.print(body);
}

void sendControlPage(WiFiClient &client) {
  String html;
  html.reserve(1400);
  html += F("<!DOCTYPE html><html><head><meta name=viewport content=\"width=device-width,initial-scale=1\">");
  html += F("<title>Conveyor</title><style>");
  html += F("body{font-family:system-ui;margin:2rem;background:#111;color:#eee}");
  html += F("button{font-size:1.1rem;margin:.35rem;padding:.7rem 1.1rem;border-radius:8px;border:0}");
  html += F(".go{background:#3c3}.stop{background:#c33;color:#fff}.adv{background:#36c;color:#fff}");
  html += F(".dir{background:#666;color:#fff}.spd{background:#963;color:#fff}");
  html += F("p{opacity:.85}</style></head><body>");
  html += F("<h1>Conveyor</h1><p>");
  html += statusString();
  html += F(" · ");
  html += dirString();
  html += F(" · ");
  html += String(msPerRev);
  html += F(" ms/rev</p>");
  html += F("<p><a href=\"/start\"><button class=go>START</button></a>");
  html += F("<a href=\"/stop\"><button class=stop>STOP</button></a>");
  html += F("<a href=\"/advance\"><button class=adv>ADVANCE</button></a></p>");
  html += F("<p><a href=\"/forward\"><button class=dir>FORWARD</button></a>");
  html += F("<a href=\"/reverse\"><button class=dir>REVERSE</button></a></p>");
  html += F("<p><a href=\"/faster\"><button class=spd>FASTER</button></a>");
  html += F("<a href=\"/slower\"><button class=spd>SLOWER</button></a>");
  html += F("<a href=\"/speeddefault\"><button class=spd>DEFAULT</button></a></p>");
  html += F("<p><a href=\"/speed6000\"><button class=spd>6s/rev</button></a>");
  html += F("<a href=\"/speed12000\"><button class=spd>12s/rev</button></a>");
  html += F("<a href=\"/speed24000\"><button class=spd>24s/rev</button></a></p>");
  html += F("<p>Serial: START STOP FORWARD REVERSE SPEED 18820 FASTER SLOWER</p>");
  html += F("</body></html>");
  sendHttpResponse(client, html.c_str());
}

void serviceHttp() {
  WiFiClient client = server.available();
  if (!client) {
    return;
  }

  String req;
  unsigned long timeout = millis() + 1000;
  while (client.connected() && millis() < timeout) {
    if (client.available()) {
      char c = client.read();
      req += c;
      if (req.endsWith("\r\n\r\n")) {
        break;
      }
      if (req.length() > 512) {
        break;
      }
    }
  }

  // Only match the request line (avoid Referer: .../start false hits).
  String line = req;
  int lineEnd = req.indexOf('\r');
  if (lineEnd < 0) {
    lineEnd = req.indexOf('\n');
  }
  if (lineEnd >= 0) {
    line = req.substring(0, lineEnd);
  }

  if (line.indexOf("GET /start") >= 0) {
    startContinuous();
  } else if (line.indexOf("GET /stop") >= 0) {
    stopMotor();
  } else if (line.indexOf("GET /advance") >= 0) {
    startAdvance();
  } else if (line.indexOf("GET /forward") >= 0) {
    setDirectionForward(true);
  } else if (line.indexOf("GET /reverse") >= 0) {
    setDirectionForward(false);
  } else if (line.indexOf("GET /faster") >= 0) {
    applySpeed((unsigned long)(msPerRev * SPEED_FASTER_FACTOR));
  } else if (line.indexOf("GET /slower") >= 0) {
    applySpeed((unsigned long)(msPerRev * SPEED_SLOWER_FACTOR));
  } else if (line.indexOf("GET /speeddefault") >= 0) {
    applySpeed(DEFAULT_MS_PER_REV);
  } else if (line.indexOf("GET /speed6000") >= 0) {
    applySpeed(6000);
  } else if (line.indexOf("GET /speed12000") >= 0) {
    applySpeed(12000);
  } else if (line.indexOf("GET /speed24000") >= 0) {
    applySpeed(24000);
  }

  sendControlPage(client);
  delay(1);
  client.stop();
}

void setup() {
  pinMode(PIN_STEP, OUTPUT);
  pinMode(PIN_DIR, OUTPUT);
  digitalWrite(PIN_STEP, STEP_ACTIVE_LOW ? HIGH : LOW);
  applySpeed(DEFAULT_MS_PER_REV);
  setDirectionForward(true);

  Serial.begin(115200);
  while (!Serial && millis() < 3000) {
  }
  delay(200);

  Serial.println();
  Serial.println(F("feeder: UNO R4 WiFi + TB6600"));
  Serial.print(F("WiFi firmware: "));
  Serial.println(WiFi.firmwareVersion());
  Serial.print(F("ms_per_rev="));
  Serial.println(msPerRev);

  wifiStaOk = connectSta();
  if (!wifiStaOk) {
    WiFi.disconnect();
    wifiApOk = startSoftAp();
  }

  if (wifiStaOk || wifiApOk) {
    server.begin();
  }

  printStatus();
  Serial.println(F("Ready. HELP for commands."));
}

void loop() {
  serviceMotor();
  serviceSerial();
  if (wifiStaOk || wifiApOk) {
    serviceHttp();
  }
}
