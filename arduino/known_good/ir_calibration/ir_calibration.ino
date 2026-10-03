/*
  REPLAST - IR sensor calibration sketch
  Team REPLAST, WinE AFFAIR 2026

  Works out the detection threshold for the Sharp analog IR sensor by
  measuring the empty tray and each material, then doing the arithmetic
  for you.

  Wiring:
    Sharp sensor red    -> 5V
    Sharp sensor black  -> GND
    Sharp sensor yellow -> A2
    47 uF capacitor across the sensor's red and black, close to it

  Serial, 9600 baud, line ending = Newline

  Commands:
    e    sample the EMPTY tray for 3 seconds
    i    sample an ITEM for 3 seconds (repeat for each material)
    c    compute and print the recommended threshold
    l    toggle live readings on and off
    r    reset everything and start again

  Method:
    The threshold must sit above the highest reading the empty tray
    ever gives, and below the lowest reading any item ever gives.
    Putting it halfway between those two extremes gives equal margin
    on both sides.
*/

const uint8_t PIN_IR = A2;

const unsigned long SAMPLE_MS   = 3000;   // how long each capture runs
const unsigned int  SAMPLE_GAP  = 50;     // ms between samples, 20 per second

// Empty tray statistics
bool  haveEmpty   = false;
int   emptyMin    = 1023, emptyMax = 0;
long  emptySum    = 0;
int   emptyCount  = 0;

// Item statistics, accumulated across every item you sample
bool  haveItem    = false;
int   itemMin     = 1023, itemMax = 0;
long  itemSum     = 0;
int   itemCount   = 0;
int   itemRuns    = 0;

bool liveOn = false;

char rxBuf[8];
uint8_t rxLen = 0;

// Average of a few reads. The Sharp sensor is noisy, so a single
// analogRead is not worth much on its own.
int readIR() {
  int sum = 0;
  for (uint8_t i = 0; i < 4; i++) sum += analogRead(PIN_IR);
  return sum / 4;
}

void sampleEmpty() {
  Serial.println(F("\nSampling EMPTY tray. Keep everything clear."));
  delay(500);

  int lo = 1023, hi = 0;
  long sum = 0;
  int n = 0;

  unsigned long start = millis();
  while (millis() - start < SAMPLE_MS) {
    int v = readIR();
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    sum += v;
    n++;
    delay(SAMPLE_GAP);
  }

  // Merge with anything captured before
  if (!haveEmpty) { emptyMin = lo; emptyMax = hi; emptySum = sum; emptyCount = n; }
  else {
    if (lo < emptyMin) emptyMin = lo;
    if (hi > emptyMax) emptyMax = hi;
    emptySum += sum;
    emptyCount += n;
  }
  haveEmpty = true;

  Serial.print(F("  samples "));  Serial.print(n);
  Serial.print(F("   min "));     Serial.print(lo);
  Serial.print(F("   max "));     Serial.print(hi);
  Serial.print(F("   mean "));    Serial.println(sum / n);
  Serial.print(F("  empty so far: min "));
  Serial.print(emptyMin);
  Serial.print(F("  max "));
  Serial.println(emptyMax);
}

void sampleItem() {
  Serial.println(F("\nSampling ITEM. Hold it where it will sit in the bin."));
  delay(500);

  int lo = 1023, hi = 0;
  long sum = 0;
  int n = 0;

  unsigned long start = millis();
  while (millis() - start < SAMPLE_MS) {
    int v = readIR();
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    sum += v;
    n++;
    delay(SAMPLE_GAP);
  }

  if (!haveItem) { itemMin = lo; itemMax = hi; itemSum = sum; itemCount = n; }
  else {
    if (lo < itemMin) itemMin = lo;
    if (hi > itemMax) itemMax = hi;
    itemSum += sum;
    itemCount += n;
  }
  haveItem = true;
  itemRuns++;

  Serial.print(F("  samples "));  Serial.print(n);
  Serial.print(F("   min "));     Serial.print(lo);
  Serial.print(F("   max "));     Serial.print(hi);
  Serial.print(F("   mean "));    Serial.println(sum / n);
  Serial.print(F("  items so far: "));
  Serial.print(itemRuns);
  Serial.print(F(" run(s), weakest reading "));
  Serial.println(itemMin);
}

void compute() {
  Serial.println(F("\n================ RESULT ================"));

  if (!haveEmpty || !haveItem) {
    Serial.println(F("Need both an empty sample (e) and at least"));
    Serial.println(F("one item sample (i) before computing."));
    return;
  }

  Serial.print(F("Empty tray : min "));
  Serial.print(emptyMin);
  Serial.print(F("  max "));
  Serial.print(emptyMax);
  Serial.print(F("  mean "));
  Serial.println(emptySum / emptyCount);

  Serial.print(F("Items      : min "));
  Serial.print(itemMin);
  Serial.print(F("  max "));
  Serial.print(itemMax);
  Serial.print(F("  mean "));
  Serial.print(itemSum / itemCount);
  Serial.print(F("   over "));
  Serial.print(itemRuns);
  Serial.println(F(" run(s)"));

  int gap = itemMin - emptyMax;

  if (gap <= 0) {
    Serial.println(F("\nPROBLEM: the weakest item reads no higher than"));
    Serial.println(F("the empty tray. No threshold can separate them."));
    Serial.println(F("Move the sensor closer, re-aim it, or change the"));
    Serial.println(F("background behind where the item sits."));
    return;
  }

  int threshold  = (emptyMax + itemMin) / 2;
  int marginLow  = threshold - emptyMax;
  int marginHigh = itemMin - threshold;
  int hysteresis = gap / 4;
  if (hysteresis < 10) hysteresis = 10;

  Serial.print(F("\nSeparation between them: "));
  Serial.println(gap);

  Serial.println(F("\nPut these into the firmware:"));
  Serial.print(F("  int irThreshold = "));
  Serial.print(threshold);
  Serial.println(F(";"));
  Serial.print(F("  const int IR_HYSTERESIS = "));
  Serial.print(hysteresis);
  Serial.println(F(";"));

  Serial.print(F("\nMargin above empty: "));
  Serial.print(marginLow);
  Serial.print(F("   margin below weakest item: "));
  Serial.println(marginHigh);

  if (gap < 50) {
    Serial.println(F("\nWARNING: separation under 50 counts is tight."));
    Serial.println(F("Expect occasional missed or false detections."));
    Serial.println(F("Moving the sensor closer usually helps most."));
  } else if (gap < 100) {
    Serial.println(F("\nWorkable, but not generous. Fine if the sensor"));
    Serial.println(F("and the item position stay fixed."));
  } else {
    Serial.println(F("\nGood separation. This should be reliable."));
  }

  Serial.println(F("========================================\n"));
}

void resetAll() {
  haveEmpty = false; emptyMin = 1023; emptyMax = 0; emptySum = 0; emptyCount = 0;
  haveItem  = false; itemMin  = 1023; itemMax  = 0; itemSum  = 0; itemCount  = 0;
  itemRuns  = 0;
  Serial.println(F("\nReset. Start again with e."));
}

void handle(char* cmd) {
  switch (cmd[0]) {
    case 'e': sampleEmpty(); break;
    case 'i': sampleItem();  break;
    case 'c': compute();     break;
    case 'r': resetAll();    break;
    case 'l':
      liveOn = !liveOn;
      Serial.println(liveOn ? F("live readings ON") : F("live readings OFF"));
      break;
    default:
      Serial.println(F("e = empty   i = item   c = compute   l = live   r = reset"));
  }
}

void setup() {
  Serial.begin(9600);
  Serial.println(F("REPLAST IR calibration"));
  Serial.println(F("----------------------"));
  Serial.println(F("1. Clear the tray, type  e"));
  Serial.println(F("2. Hold a PET bottle,  type  i"));
  Serial.println(F("3. Hold a can,         type  i"));
  Serial.println(F("4. Hold cardboard,     type  i"));
  Serial.println(F("5. Type  c  for the answer"));
  Serial.println(F("\nl toggles live readings, r resets\n"));
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLen > 0) { rxBuf[rxLen] = '\0'; handle(rxBuf); rxLen = 0; }
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = tolower(c);
    }
  }

  if (liveOn) {
    Serial.println(readIR());
    delay(200);
  }
}
