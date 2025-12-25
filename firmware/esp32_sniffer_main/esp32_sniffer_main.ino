#include <WiFi.h>
#include <esp_wifi.h>
#include <vector>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <cmath>

// --- Configuration ---
#define PRINT_INTERVAL 100     
#define PURGE_INTERVAL 10000   
#define QUEUE_SIZE 64          // Hold up to 64 packets in buffer

// --- Data Structures ---
// Minimal struct for the Queue (avoid Strings here to prevent heap fragmentation in ISR)
struct RawPacket {
  uint8_t mac[6];
  int rssi;
  uint8_t typeCode; // 0=Unknown, 1=Router, 2=Station, 3=Deauth
};

struct Device {
  uint8_t mac[6];
  int rssi;
  unsigned long lastSeen;
  uint8_t typeCode;
};

// --- Globals ---
std::vector<Device> foundDevices;
QueueHandle_t packetQueue;
SemaphoreHandle_t dbMutex; // Protects foundDevices vector
int hopInterval = 50;
TaskHandle_t rainbowTaskHandle = NULL;

// --- Helpers ---
String macToString(const uint8_t* mac) {
  String s = "";
  for (int i = 0; i < 6; i++) {
    if (mac[i] < 0x10) s += "0";
    s += String(mac[i], HEX);
    if (i < 5) s += ":";
  }
  return s;
}

// --- WiFi Callback (Producer) ---
// Runs in Interrupt Context - Keep it FAST!
void wifi_promiscuous_rx_cb(void* buf, wifi_promiscuous_pkt_type_t type) {
  wifi_promiscuous_pkt_t* pkt = (wifi_promiscuous_pkt_t*)buf;
  uint8_t* data = pkt->payload;
  
  // Safety: Ensure packet is long enough to contain header (Frame Control + 3 MACs approx)
  if (pkt->rx_ctrl.sig_len < 24) return; 

  // 1. Extract raw data
  int rssi = pkt->rx_ctrl.rssi;
  uint8_t frameControl = data[0];
  
  // 2. Determine type & MAC location (Logic preserved from original)
  uint8_t typeCode = 0; // Unknown
  uint8_t* macAddrPos = &data[10];

  if (frameControl == 0x80) {
    typeCode = 1; // ROUTER
    macAddrPos = &data[10];
  } else if (frameControl == 0x40) {
    typeCode = 2; // STATION
    macAddrPos = &data[10];
  } else if (frameControl == 0xC0) {
    typeCode = 3; // DEAUTH
    macAddrPos = &data[10];
  } else if ((frameControl & 0x0C) == 0x08) { 
    typeCode = 2; // STATION
  }

  // 3. Send to Queue
  RawPacket newPacket;
  memcpy(newPacket.mac, macAddrPos, 6);
  newPacket.rssi = rssi;
  newPacket.typeCode = typeCode;

  // Send to back of queue. If full, we drop the packet (0 wait time in ISR)
  xQueueSendFromISR(packetQueue, &newPacket, NULL);
}

// --- Task: Packet Processor (Consumer) ---
// Reads queue, updates Vector
void processingTask(void * parameter) {
  RawPacket pkt;
  while(1) {
    // Wait for packet (block indefinitely until data arrives)
    if (xQueueReceive(packetQueue, &pkt, portMAX_DELAY)) {
      
      // CRITICAL SECTION: Modifying the Vector
      if (xSemaphoreTake(dbMutex, portMAX_DELAY)) {
        
        bool known = false;
        for (auto &d : foundDevices) {
          if (memcmp(d.mac, pkt.mac, 6) == 0) {
            d.rssi = pkt.rssi;
            d.lastSeen = millis();
            // Update type if we learn more
            if (pkt.typeCode == 3) {
              d.typeCode = 3; // Flag as Attacker/Deauth source
            } else if (d.typeCode != 3 && (d.typeCode == 0 || pkt.typeCode == 1)) {
              d.typeCode = pkt.typeCode;
            }
            known = true;
            break;
          }
        }

        if (!known) {
          Device newDevice;
          memcpy(newDevice.mac, pkt.mac, 6);
          newDevice.rssi = pkt.rssi;
          newDevice.lastSeen = millis();
          newDevice.typeCode = pkt.typeCode;
          foundDevices.push_back(newDevice);
        }
        
        xSemaphoreGive(dbMutex); // Release lock
      }
    }
  }
}

// --- Task: Serial Output & Purge ---
// Handles JSON printing and cleaning old devices
void outputTask(void * parameter) {
  unsigned long lastHeartbeat = 0;
  while(1) {
    vTaskDelay(PRINT_INTERVAL / portTICK_PERIOD_MS); // Wait 100ms

    // Send Heartbeat every 5 seconds
    if (millis() - lastHeartbeat > 5000) {
      Serial.println("{\"msg\":\"HEARTBEAT\"}");
      lastHeartbeat = millis();
    }

    // CRITICAL SECTION: Reading/Purging the Vector
    if (xSemaphoreTake(dbMutex, portMAX_DELAY)) {
      
      Serial.print("{\"devices\":[");
      bool first = true;
      
      for (auto it = foundDevices.begin(); it != foundDevices.end(); ) {
        unsigned long timeSince = millis() - it->lastSeen;
        
        // Print Logic
        if (!first) Serial.print(",");
        Serial.print("{\"mac\":\"");
        Serial.print(macToString(it->mac));
        Serial.print("\",\"rssi\":");
        Serial.print(it->rssi);
        Serial.print(",\"type\":\"");
        
        if (it->typeCode == 1) Serial.print("ROUTER");
        else if (it->typeCode == 2) Serial.print("STATION");
        else if (it->typeCode == 3) Serial.print("DEAUTH");
        else Serial.print("Unknown");
        
        Serial.print("\",\"seen_ms\":");
        Serial.print(timeSince);
        Serial.print("}");
        first = false;

        // Purge Logic
        if (timeSince > PURGE_INTERVAL) {
          it = foundDevices.erase(it);
        } else {
          ++it;
        }
      }
      Serial.println("]}");
      
      xSemaphoreGive(dbMutex); // Release lock
    }
  }
}

// --- Task: Channel Hopper ---
void hopTask(void * parameter) {
  int currentChannel = 1;
  while(1) {
    vTaskDelay(hopInterval / portTICK_PERIOD_MS); 
    esp_wifi_set_channel(currentChannel, WIFI_SECOND_CHAN_NONE);
    currentChannel++;
    if (currentChannel > 13) currentChannel = 1;
  }
}

// --- Task: Rainbow LED Effect ---
void ledRainbowTask(void * parameter) {
  pinMode(LED_RED, OUTPUT);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_BLUE, OUTPUT);
  
  float hue = 0.0;
  while(1) {
    // Sine wave based RGB cycling (Phase shifted by 120 degrees)
    // sin() returns -1 to 1. We map it to 0-255.
    int r = (int)(127.5 * (1.0 + sin(hue)));
    int g = (int)(127.5 * (1.0 + sin(hue + 2.0944))); // +120 deg (2*PI/3)
    int b = (int)(127.5 * (1.0 + sin(hue + 4.1888))); // +240 deg (4*PI/3)
    
    // Active Low Logic (Common Anode): 255 - val
    analogWrite(LED_RED,   255 - r);
    analogWrite(LED_GREEN, 255 - g);
    analogWrite(LED_BLUE,  255 - b);
    
    hue += 0.05; // Speed of cycle
    if (hue > 6.28318) hue -= 6.28318; // Wrap 2*PI
    
    vTaskDelay(20 / portTICK_PERIOD_MS); // 50Hz update rate
  }
}

// --- Task: Serial Input ---
void serialInputTask(void * parameter) {
  while(1) {
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim(); // Remove whitespace/newlines

      if (cmd.equalsIgnoreCase("CLEAR")) {
        if (xSemaphoreTake(dbMutex, portMAX_DELAY)) {
          foundDevices.clear();
          Serial.println("{\"msg\":\"Database Cleared\"}");
          xSemaphoreGive(dbMutex);
        }
      } else if (cmd.equalsIgnoreCase("RESTART")) {
        ESP.restart();
      } else if (cmd.equalsIgnoreCase("DEVICE_TESTER")) {
        // Stop Rainbow if running
        if (rainbowTaskHandle != NULL) {
          vTaskDelete(rainbowTaskHandle);
          rainbowTaskHandle = NULL;
        }

        Serial.println("{\"msg\":\"Testing RGB LED...\"}");
        
        // Setup RGB pins (Active LOW on Nano ESP32)
        pinMode(LED_RED, OUTPUT);
        pinMode(LED_GREEN, OUTPUT);
        pinMode(LED_BLUE, OUTPUT);
        
        // Cycle Colors (2 Seconds Total)
        digitalWrite(LED_RED, LOW); digitalWrite(LED_GREEN, HIGH); digitalWrite(LED_BLUE, HIGH); // Red
        vTaskDelay(500 / portTICK_PERIOD_MS);
        digitalWrite(LED_RED, HIGH); digitalWrite(LED_GREEN, LOW); digitalWrite(LED_BLUE, HIGH); // Green
        vTaskDelay(500 / portTICK_PERIOD_MS);
        digitalWrite(LED_RED, HIGH); digitalWrite(LED_GREEN, HIGH); digitalWrite(LED_BLUE, LOW); // Blue
        vTaskDelay(500 / portTICK_PERIOD_MS);
        digitalWrite(LED_RED, HIGH); digitalWrite(LED_GREEN, HIGH); digitalWrite(LED_BLUE, HIGH); // Off
        vTaskDelay(500 / portTICK_PERIOD_MS);
        
        Serial.println("{\"msg\":\"Test Complete\"}");
      } else if (cmd.startsWith("HOP_SPEED")) {
        int val = cmd.substring(9).toInt();
        if (val >= 10 && val <= 5000) {
          hopInterval = val;
          Serial.print("{\"msg\":\"Hop Speed set to ");
          Serial.print(val);
          Serial.println("ms\"}");
        } else {
          Serial.println("{\"msg\":\"Error: Speed must be 10-5000ms\"}");
        }
      } else if (cmd.startsWith("LED_COLOR")) {
        int r, g, b;
        
        // Stop Rainbow if running
        if (rainbowTaskHandle != NULL) {
          vTaskDelete(rainbowTaskHandle);
          rainbowTaskHandle = NULL;
        }

        // Parse 3 integers: LED_COLOR 255 128 0
        if (sscanf(cmd.c_str(), "LED_COLOR %d %d %d", &r, &g, &b) == 3) {
          // Constrain to 0-255
          r = constrain(r, 0, 255);
          g = constrain(g, 0, 255);
          b = constrain(b, 0, 255);
          
          // Active Low Logic: 255-val because 0 is ON (Low Voltage)
          analogWrite(LED_RED,   255 - r);
          analogWrite(LED_GREEN, 255 - g);
          analogWrite(LED_BLUE,  255 - b);
          Serial.println("{\"msg\":\"LED Color Updated (PWM)\"}");
        } else {
          Serial.println("{\"msg\":\"Usage: LED_COLOR <R> <G> <B> (0-255)\"}");
        }
      } else if (cmd.equalsIgnoreCase("RAINBOW")) {
        if (rainbowTaskHandle == NULL) {
           xTaskCreate(ledRainbowTask, "Rainbow", 2048, NULL, 1, &rainbowTaskHandle);
           Serial.println("{\"msg\":\"Rainbow Mode ON\"}");
        } else {
           vTaskDelete(rainbowTaskHandle);
           rainbowTaskHandle = NULL;
           // Turn off LEDs
           analogWrite(LED_RED, 255);
           analogWrite(LED_GREEN, 255);
           analogWrite(LED_BLUE, 255);
           Serial.println("{\"msg\":\"Rainbow Mode OFF\"}");
        }
      }
    }
    // Check for input every 50ms to yield CPU
    vTaskDelay(50 / portTICK_PERIOD_MS);
  }
}

void setup() {
  Serial.begin(921600);
  
  // Create RTOS Primitives
  packetQueue = xQueueCreate(QUEUE_SIZE, sizeof(RawPacket));
  dbMutex = xSemaphoreCreateMutex();

  // Setup WiFi
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_promiscuous_rx_cb(&wifi_promiscuous_rx_cb);

  // Start Tasks
  // Core 1 (Application) typically runs Arduino Loop. 
  // We can push heavy processing to Core 0 or keep on 1.
  xTaskCreatePinnedToCore(processingTask, "Process", 4096, NULL, 1, NULL, 0); // Core 0
  xTaskCreatePinnedToCore(outputTask,     "Output",  4096, NULL, 1, NULL, 1); // Core 1
  xTaskCreate(hopTask, "Hopper", 2048, NULL, 1, NULL);
  xTaskCreate(serialInputTask, "SerialIn", 2048, NULL, 1, NULL);
}

void loop() {
  vTaskDelete(NULL); // Eliminate the Arduino Loop task entirely
}