#include <WiFi.h>
#include <esp_wifi.h>
#include <vector>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>

// --- BLE Includes ---
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// --- Configuration ---
#define HOP_INTERVAL 50        
#define PRINT_INTERVAL 100     
#define PURGE_INTERVAL 10000   
#define QUEUE_SIZE 64          

// --- BLE UART UUIDs (Nordic UART Service) ---
// These standard UUIDs allow the device to work with "Serial Bluetooth Terminal" apps
#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E" 
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

// --- Objects ---
BLEServer* pServer = NULL;
BLECharacteristic* pTxCharacteristic = NULL;
bool deviceConnected = false;

// --- Data Structures ---
struct RawPacket {
  uint8_t mac[6];
  int rssi;
  uint8_t typeCode; 
};

struct Device {
  String mac;
  int rssi;
  unsigned long lastSeen;
  String type;
};

// --- Globals ---
std::vector<Device> foundDevices;
QueueHandle_t packetQueue;
SemaphoreHandle_t dbMutex; 

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

// --- BLE Server Callbacks ---
class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
    };
    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
      // Restart advertising immediately so you can reconnect
      pServer->getAdvertising()->start();
    }
};

// --- WiFi Callback (Producer) ---
void wifi_promiscuous_rx_cb(void* buf, wifi_promiscuous_pkt_type_t type) {
  wifi_promiscuous_pkt_t* pkt = (wifi_promiscuous_pkt_t*)buf;
  uint8_t* data = pkt->payload;
  
  int rssi = pkt->rx_ctrl.rssi;
  uint8_t frameControl = data[0];
  
  uint8_t typeCode = 0; 
  uint8_t* macAddrPos = &data[10];

  if (frameControl == 0x80) {
    typeCode = 1; // ROUTER
    macAddrPos = &data[10];
  } else if (frameControl == 0x40) {
    typeCode = 2; // STATION
    macAddrPos = &data[10];
  } else if ((frameControl & 0x0C) == 0x08) { 
    typeCode = 2; // STATION
  }

  RawPacket newPacket;
  memcpy(newPacket.mac, macAddrPos, 6);
  newPacket.rssi = rssi;
  newPacket.typeCode = typeCode;

  xQueueSendFromISR(packetQueue, &newPacket, NULL);
}

// --- Task: Packet Processor (Consumer) ---
void processingTask(void * parameter) {
  RawPacket pkt;
  while(1) {
    if (xQueueReceive(packetQueue, &pkt, portMAX_DELAY)) {
      
      String macStr = macToString(pkt.mac);
      String typeStr = "Unknown";
      if (pkt.typeCode == 1) typeStr = "ROUTER";
      if (pkt.typeCode == 2) typeStr = "STATION";

      if (xSemaphoreTake(dbMutex, portMAX_DELAY)) {
        bool known = false;
        for (auto &d : foundDevices) {
          if (d.mac == macStr) {
            d.rssi = pkt.rssi;
            d.lastSeen = millis();
            if (d.type == "Unknown" || typeStr == "ROUTER") d.type = typeStr;
            known = true;
            break;
          }
        }
        if (!known) {
          Device newDevice;
          newDevice.mac = macStr;
          newDevice.rssi = pkt.rssi;
          newDevice.lastSeen = millis();
          newDevice.type = typeStr;
          foundDevices.push_back(newDevice);
        }
        xSemaphoreGive(dbMutex);
      }
    }
  }
}

// --- Task: Output (USB + BLE) ---
void outputTask(void * parameter) {
  while(1) {
    vTaskDelay(PRINT_INTERVAL / portTICK_PERIOD_MS); 

    if (xSemaphoreTake(dbMutex, portMAX_DELAY)) {
      
      String json = "{\"devices\":[";
      bool first = true;
      
      for (auto it = foundDevices.begin(); it != foundDevices.end(); ) {
        unsigned long timeSince = millis() - it->lastSeen;
        
        if (!first) json += ",";
        json += "{\"mac\":\"" + it->mac + "\",";
        json += "\"rssi\":" + String(it->rssi) + ",";
        json += "\"type\":\"" + it->type + "\",";
        json += "\"seen_ms\":" + String(timeSince) + "}";
        first = false;

        if (timeSince > PURGE_INTERVAL) {
          it = foundDevices.erase(it);
        } else {
          ++it;
        }
      }
      json += "]}"; 
      
      // 1. Send to USB
      Serial.println(json);
      
      // 2. Send to BLE (if connected)
      if (deviceConnected) {
        // BLE packets have a size limit (usually ~20 bytes by default, but up to 512 with negotiation).
        // Since JSON can be long, we might need to rely on the client handling fragmentation,
        // or just set the value. Large JSONs might get truncated on basic BLE viewers.
        pTxCharacteristic->setValue((uint8_t*)json.c_str(), json.length());
        pTxCharacteristic->notify();
      }
      
      xSemaphoreGive(dbMutex); 
    }
  }
}

// --- Task: Channel Hopper ---
void hopTask(void * parameter) {
  int currentChannel = 1;
  while(1) {
    vTaskDelay(HOP_INTERVAL / portTICK_PERIOD_MS); 
    esp_wifi_set_channel(currentChannel, WIFI_SECOND_CHAN_NONE);
    currentChannel++;
    if (currentChannel > 13) currentChannel = 1;
  }
}

void setup() {
  Serial.begin(115200); // Standard baud rate is fine for S3 USB CDC

  // --- BLE Setup ---
  BLEDevice::init("ESP32_S3_Sniffer");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  BLEService *pService = pServer->createService(SERVICE_UUID);

  pTxCharacteristic = pService->createCharacteristic(
                    CHARACTERISTIC_UUID_TX,
                    BLECharacteristic::PROPERTY_NOTIFY
                  );
  pTxCharacteristic->addDescriptor(new BLE2902());

  pService->start();
  pServer->getAdvertising()->start();
  Serial.println("BLE Started. Waiting for client...");

  // --- RTOS & WiFi Setup ---
  packetQueue = xQueueCreate(QUEUE_SIZE, sizeof(RawPacket));
  dbMutex = xSemaphoreCreateMutex();

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_promiscuous_rx_cb(&wifi_promiscuous_rx_cb);

  // Pinning tasks (S3 also has 2 cores)
  xTaskCreatePinnedToCore(processingTask, "Process", 4096, NULL, 1, NULL, 0); 
  xTaskCreatePinnedToCore(outputTask,     "Output",  8192, NULL, 1, NULL, 1); 
  xTaskCreate(hopTask, "Hopper", 2048, NULL, 1, NULL);
}

void loop() {
  vTaskDelete(NULL); 
}