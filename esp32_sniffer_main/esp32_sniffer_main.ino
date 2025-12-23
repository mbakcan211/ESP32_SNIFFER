#include <WiFi.h>
#include <esp_wifi.h>
#include <vector>

// --- Configuration ---
#define HOP_INTERVAL 50        
#define PRINT_INTERVAL 100     // Faster updates (1 second) for the GUI
#define PURGE_INTERVAL 10000    

struct Device {
  String mac;
  int rssi;
  unsigned long lastSeen;
  String type;
};

std::vector<Device> foundDevices;

String macToString(uint8_t* mac) {
  String s = "";
  for (int i = 0; i < 6; i++) {
    if (mac[i] < 0x10) s += "0";
    s += String(mac[i], HEX);
    if (i < 5) s += ":";
  }
  return s;
}

void wifi_promiscuous_rx_cb(void* buf, wifi_promiscuous_pkt_type_t type) {
  wifi_promiscuous_pkt_t* pkt = (wifi_promiscuous_pkt_t*)buf;
  uint8_t* data = pkt->payload;
  int rssi = pkt->rx_ctrl.rssi;
  
  uint8_t frameControl = data[0];
  String detectedType = "Unknown";
  uint8_t* macAddrPos = &data[10]; 

  if (frameControl == 0x80) {
    detectedType = "ROUTER"; 
    macAddrPos = &data[10];  
  } else if (frameControl == 0x40) {
    detectedType = "STATION"; 
    macAddrPos = &data[10];   
  } else if ((frameControl & 0x0C) == 0x08) { 
     detectedType = "STATION"; 
  }

  uint8_t srcMac[6];
  memcpy(srcMac, macAddrPos, 6);
  String macStr = macToString(srcMac);

  bool known = false;
  for (int i = 0; i < foundDevices.size(); i++) {
    if (foundDevices[i].mac == macStr) {
      foundDevices[i].rssi = rssi;
      foundDevices[i].lastSeen = millis();
      if (foundDevices[i].type == "Unknown" || detectedType == "ROUTER") {
         foundDevices[i].type = detectedType;
      }
      known = true;
      break;
    }
  }

  if (!known) {
    Device newDevice;
    newDevice.mac = macStr;
    newDevice.rssi = rssi;
    newDevice.lastSeen = millis();
    newDevice.type = detectedType;
    foundDevices.push_back(newDevice);
  }
}

void setup() {
  Serial.begin(921600);
  while (!Serial);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_promiscuous_rx_cb(&wifi_promiscuous_rx_cb);
}

void loop() {
  static unsigned long lastPrintTime = 0;
  static unsigned long lastHopTime = 0;
  static int currentChannel = 1;

  if (millis() - lastHopTime > HOP_INTERVAL) {
    lastHopTime = millis();
    esp_wifi_set_channel(currentChannel, WIFI_SECOND_CHAN_NONE);
    currentChannel++;
    if (currentChannel > 13) currentChannel = 1;
  }

  if (millis() - lastPrintTime > PRINT_INTERVAL) {
    lastPrintTime = millis();

    // --- JSON OUTPUT START ---
    // We send one big JSON array containing all active devices
    Serial.print("{\"devices\":[");
    bool first = true;
    
    for (auto it = foundDevices.begin(); it != foundDevices.end(); ) {
      unsigned long timeSince = millis() - it->lastSeen;
      
      if (!first) Serial.print(",");
      
      Serial.print("{\"mac\":\"");
      Serial.print(it->mac);
      Serial.print("\",\"rssi\":");
      Serial.print(it->rssi);
      Serial.print(",\"type\":\"");
      Serial.print(it->type);
      Serial.print("\",\"seen_ms\":");
      Serial.print(timeSince);
      Serial.print("}");
      
      first = false;

      if (timeSince > PURGE_INTERVAL) {
        it = foundDevices.erase(it);
      } else {
        ++it;
      }
    }
    Serial.println("]}"); 
    // --- JSON OUTPUT END ---
  }
}