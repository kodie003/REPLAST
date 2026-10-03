/*
  REPLAST - IR sensor check (v2)

  Purpose: find out how the Sharp analog IR sensor reads with the platform
  EMPTY and with each kind of ITEM on it, and work out the threshold the
  controller firmware should use.

  Written to compile on the Arduino UNO R4 WiFi with no extra libraries.
  (It avoids F() inside ?: and tolower(), which can upset the R4 core.)

  Wiring (same as before):
    Sharp red    -> 5V
    Sharp black  -> GND
    Sharp yellow -> A2
    47 uF capacitor across red/black, close to the sensor

  Serial Monitor: 9600 baud, line ending "Newline".
  Type a letter and press Enter:
    e   measure the EMPTY platform for 3 s   (do this 2-3 times)
    i   measure an ITEM for 3 s              (once per item: bottle, can, paper...)
    c   compute the threshold
    l   live readings on/off (one number every 0.2 s)
    r   reset and start again

  Every result line starts with a fixed word (EMPTY, ITEM, RESULT) so the
  output can be copied straight back to the software team.
*/

const int PIN_IR = A2;

const unsigned long SAMPLE_MS  = 3000;  // length of one measurement
const unsigned long SAMPLE_GAP = 50;    // ms between readings (20 per second)

// Running statistics. "lo/hi" are the most extreme readings seen.
struct Stats {
  int  lo;
  int  hi;
  long sum;
  long count;
  int  runs;
};

Stats emptyStats;
Stats itemStats;

bool liveOn = false;
unsigned long lastLive = 0;

String rx = "";

void clearStats(Stats &s) {
  s.lo = 32767;
  s.hi = -1;
  s.sum = 0;
  s.count = 0;
  s.runs = 0;
}

// The Sharp sensor is noisy, so average 4 quick reads.
int readIR() {
  long sum = 0;
  for (int k = 0; k < 4; k++) sum += analogRead(PIN_IR);
  return (int)(sum / 4);
}

// Measure for SAMPLE_MS and add the readings to s. Prints one summary line.
void measure(Stats &s, const char *label) {
  Serial.print("Measuring ");
  Serial.print(label);
  Serial.println(" for 3 s - keep still...");
  delay(500);

  int lo = 32767, hi = -1;
  long sum = 0, n = 0;
  unsigned long start = millis();
  while (millis() - start < SAMPLE_MS) {
    int v = readIR();
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    sum += v;
    n++;
    delay(SAMPLE_GAP);
  }

  if (lo < s.lo) s.lo = lo;
  if (hi > s.hi) s.hi = hi;
  s.sum += sum;
  s.count += n;
  s.runs++;

  // Example: EMPTY run=1 n=55 min=80 max=95 mean=88
  Serial.print(label);
  Serial.print(" run=");  Serial.print(s.runs);
  Serial.print(" n=");    Serial.print(n);
  Serial.print(" min=");  Serial.print(lo);
  Serial.print(" max=");  Serial.print(hi);
  Serial.print(" mean="); Serial.println(sum / n);
}

void compute() {
  if (emptyStats.runs == 0 || itemStats.runs == 0) {
    Serial.println("RESULT need at least one 'e' and one 'i' first");
    return;
  }

  long emptyMean = emptyStats.sum / emptyStats.count;
  long itemMean  = itemStats.sum / itemStats.count;

  // Normally an item makes the reading go UP (closer = higher voltage).
  // Check instead of assuming, so a reversed mounting is caught.
  bool itemHigher = itemMean > emptyMean;

  int gap;        // clear space between the two ranges
  int threshold;
  if (itemHigher) {
    gap = itemStats.lo - emptyStats.hi;
    threshold = (emptyStats.hi + itemStats.lo) / 2;
  } else {
    gap = emptyStats.lo - itemStats.hi;
    threshold = (emptyStats.lo + itemStats.hi) / 2;
  }

  int hysteresis = gap / 4;
  if (hysteresis < 10) hysteresis = 10;

  Serial.println("---------- copy everything from here ----------");
  Serial.print("RESULT empty_min=");  Serial.print(emptyStats.lo);
  Serial.print(" empty_max=");        Serial.print(emptyStats.hi);
  Serial.print(" empty_mean=");       Serial.print(emptyMean);
  Serial.print(" empty_runs=");       Serial.println(emptyStats.runs);
  Serial.print("RESULT item_min=");   Serial.print(itemStats.lo);
  Serial.print(" item_max=");         Serial.print(itemStats.hi);
  Serial.print(" item_mean=");        Serial.print(itemMean);
  Serial.print(" item_runs=");        Serial.println(itemStats.runs);
  Serial.print("RESULT direction=");
  Serial.print(itemHigher ? "ITEM_HIGHER" : "ITEM_LOWER");
  Serial.print(" gap=");              Serial.print(gap);
  Serial.print(" threshold=");        Serial.print(threshold);
  Serial.print(" hysteresis=");       Serial.println(hysteresis);

  if (gap <= 0) {
    Serial.println("RESULT verdict=OVERLAP (empty and items overlap - re-aim or move sensor closer)");
  } else if (gap < 50) {
    Serial.println("RESULT verdict=TIGHT (works, but expect occasional mistakes)");
  } else if (gap < 100) {
    Serial.println("RESULT verdict=WORKABLE");
  } else {
    Serial.println("RESULT verdict=GOOD");
  }
  Serial.println("---------- to here ----------");
}

void handle(String cmd) {
  cmd.trim();
  cmd.toLowerCase();
  if (cmd.length() == 0) return;

  char c = cmd.charAt(0);
  if (c == 'e') {
    measure(emptyStats, "EMPTY");
  } else if (c == 'i') {
    measure(itemStats, "ITEM");
  } else if (c == 'c') {
    compute();
  } else if (c == 'l') {
    liveOn = !liveOn;
    if (liveOn) Serial.println("live ON");
    else        Serial.println("live OFF");
  } else if (c == 'r') {
    clearStats(emptyStats);
    clearStats(itemStats);
    Serial.println("reset - start again with e");
  } else {
    Serial.println("e = empty | i = item | c = compute | l = live | r = reset");
  }
}

void setup() {
  Serial.begin(9600);
  // The R4's USB serial needs a moment; wait up to 3 s for the monitor.
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { }

  analogReadResolution(10);  // 0..1023, same scale as the old sketch
  pinMode(PIN_IR, INPUT);
  clearStats(emptyStats);
  clearStats(itemStats);

  Serial.println("REPLAST IR check v2 ready");
  Serial.println("1) Clear the platform, type e (repeat 2-3 times)");
  Serial.println("2) Put a PET bottle where items sit, type i");
  Serial.println("3) Same for a can, a crushed can, paper, cardboard (type i each time)");
  Serial.println("4) Type c and copy the RESULT lines back");
}

void loop() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      handle(rx);
      rx = "";
    } else if (rx.length() < 16) {
      rx += ch;
    }
  }

  if (liveOn && millis() - lastLive >= 200) {
    lastLive = millis();
    Serial.println(readIR());
  }
}
