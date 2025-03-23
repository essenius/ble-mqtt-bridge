#!/usr/bin/env python3

from bluepy3 import btle
import timeit
import time
import sys
import argparse
import logging
import paho.mqtt.client as mqtt
import ssl
import config
import socket
import os
import RPi.GPIO as GPIO

class ScanDelegate(btle.DefaultDelegate):
    def __init__(self, config, logger):
        btle.DefaultDelegate.__init__(self)
        self.name = config["deviceName"]
        self.serviceUuid = config["serviceUuid"]
        self.logger = logger
        self.foundDevice = None

    def handleDiscovery(self, dev, isNewDev, isNewData):
        if (self.foundDevice != None):
            self.logger.debug("Skipping {0}".format(dev.addr))
            return
        localName = 9
        # shortLocalName = 8
        # complete128bServices = 7
        if isNewDev:
            self.logger.info("Discovered device '{0}'".format(dev.addr))
        elif isNewData:
            self.logger.debug("Received new data from device '{0}'".format(dev.addr))
        for (adType, desc, value) in dev.getScanData():
            self.logger.info("* {0} = '{1}' {2}".format(desc, value, adType))
            if (adType == localName) and value.startswith(self.name):   # or adType == shortLocalName
                self.logger.info("found sensor '{0}' via name".format(value))
                dev.name = value
                self.foundDevice = dev
                return
            #if adType == complete128bServices and value == self.serviceUuid:
            #    dev.name = dev.getValueText(localName)
            #    self.logger.info("found sensor '{0}' via service UUID".format(dev.name))
            #    self.foundDevice = dev
            #    return

class DeviceScanner():
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.device = None

    def scan(self):
        self.logger.info("Searching for sensor '{0}*'".format(self.config["deviceName"]))
        scanTime = 0.5
        scanDelegate = ScanDelegate(self.config, logger)
        scanner = btle.Scanner().withDelegate(scanDelegate)
        scanner.clear()
        scanner.start()
        startTime = timeit.default_timer()
        while (scanDelegate.foundDevice == None) and timeit.default_timer() - startTime < self.config["scanTime"]:
            scanner.process(scanTime)
        scanner.stop()
        device = scanDelegate.foundDevice
        if device == None:
            self.logger.warning("Did not find sensor '{0}*'".format(self.config["deviceName"]))
        return device

class Property():
    def __init__(self, name, unit):
        self.name = name
        self.unit = unit

class ReceiveDelegate(btle.DefaultDelegate):
    def __init__(self, properties, mqttClient):
        btle.DefaultDelegate.__init__(self)
        self.properties = properties
        self.mqttClient = mqttClient

    def handleNotification(self, cHandle, data):
        payload = data.decode('utf-8')
        if self.properties[cHandle].unit != "":
            payload += " " + self.properties[cHandle].unit
        self.mqttClient.publishValue(entity=self.properties[cHandle].name, payload=payload)

class DeviceConnector():
    def __init__(self, scanDevice, mqttClient, logger):
        self.scanDevice = scanDevice
        self.mqttClient = mqttClient
        self.logger = logger
        self.device = None
        self.properties = {}

    def connect(self):
        self.logger.info("Connecting to sensor '{0}' ({1})".format(scanDevice.name, scanDevice.addr))
        try:
            self.device = btle.Peripheral(self.scanDevice.addr, addrType=self.scanDevice.addrType)
        except btle.BTLEDisconnectError as e:
            self.logger.error(e)
            self.device = None
            return None
        self.logger.info("Device State: {0}".format(self.device.getState()))
        self._activateNotifications()
        self.receiveDelegate = ReceiveDelegate(self.properties, self.mqttClient)
        self.device.setDelegate(self.receiveDelegate)
        return self.device

    def _activateNotifications(self):
        complete128bServices = 7
        clientCharacteristicUUID = 0x2902
        descriptorUUID = 0x2901
        notifyBit = 0x10
        writeBit = 0x08
        writeNoResponseBit = 0x04
        serviceUUID = self.scanDevice.getValueText(complete128bServices)
        service = self.device.getServiceByUUID(serviceUUID)
        for char in service.getCharacteristics():
            # Note that .getHandle() is something else than .handle.
            handle = char.getHandle()
            self.logger.debug("char {0} {1} {2}".format(handle, char.uuid, char.propertiesToString()))
            if char.properties & notifyBit == notifyBit:
                for desc in char.getDescriptors():
                    self.logger.debug("Decriptor {0} {1} {2} {3}".format(desc.handle, desc.uuid, str(desc), self.device.readCharacteristic(desc.handle).decode('UTF-8')))
                    if desc.uuid == clientCharacteristicUUID:
                        self.logger.info("Enabling notification for handle {0} via client characteristic {1}".format(handle, desc.handle))
                        self.device.writeCharacteristic(desc.handle, b"\x01\x00")
                    if desc.uuid == descriptorUUID:
                        propertyDescription = self.device.readCharacteristic(desc.handle).decode('UTF-8').rsplit(" (", 1)
                        if len(propertyDescription) == 1:
                            propertyDescription.append("")
                        else:
                            if (propertyDescription[1][-1] == ")"):
                                propertyDescription[1] = propertyDescription[1][:-1]
                        propertyDescription[0] = propertyDescription[0].replace(" ", "_")
                        self.properties[handle] = Property(name=propertyDescription[0], unit=propertyDescription[1])
            elif char.properties & writeBit == writeBit or char.properties & writeNoResponseBit == writeNoResponseBit :
                # this must be the reset property, as that's the only writable one.
                self.logger.info("Resetting sensor")
                char.write(b"\x01")

class MqttClient(mqtt.Client):
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        super(MqttClient, self).__init__(socket.gethostname() + config["clientIdSuffix"])

    def on_connect(self, mqttc, userdata, flags, rc):
        self.connectionCode = rc
        logger.info("Connected to MQTT broker with result code '{0}'".format(str(rc)))

    def run(self):
        self.connectionCode = -1
        self.tls_set(ca_certs=self.config["caCert"]) 
        self.username_pw_set(username=self.config["username"], password=self.config["password"])
        self.loop_start()
        try:
            self.connect(self.config["broker"], self.config["port"], self.config["timeout"])
        except Exception as exception:
            self.logger.error("Exception '{0}' connecting to MQTT broker".format(exception))
            return False
        while self.connectionCode == -1:
            self.logger.info("Waiting for MQTT")
            time.sleep(1)
        if self.connectionCode != 0:
            self.logger.error("Error '{0}' connecting to MQTT broker".format(self.connectionCode))
            return False
        else:
            self.logger.info("Connected to MQTT broker")
            return True

    def publishState(self, payload):
        self.publish(topic="bridges/ble/state", payload=payload)

    def publishValue(self, entity, payload):
        logger.info("publishing {0}: {1}".format(entity, payload))
        self.publish(topic=self.topicTemplate.format(entity, "state"), payload=payload )

    def setSensorName(self, name):
        self.topicTemplate = "sensors/ble/{0}/{1}".format(name, "{0}/{1}")

class MqttHandler(logging.StreamHandler):
    def __init__(self, broker):
        logging.StreamHandler.__init__(self)
        self.broker = broker

    def emit(self, record):
        self.broker.publishState(self.format(record))

def setLogging():
    parser = argparse.ArgumentParser()
    parser.add_argument("-log", "--log", default="warning", help=("Provide logging level. Example --log debug, default='warning'"))

    options = parser.parse_args()
    levels = { 'critical': logging.CRITICAL, 'error': logging.ERROR, 'warn': logging.WARNING,
      'warning': logging.WARNING, 'info': logging.INFO, 'debug': logging.DEBUG  }
    level = levels.get(options.log.lower())
    if level is None:
        raise ValueError(
            f"log level given: {options.log}"
            f" -- must be one of: {' | '.join(levels.keys())}")
    logging.basicConfig(format='%(asctime)s %(levelname)-8s: %(message)s', level=level, datefmt='%Y-%m-%d %H:%M:%S')

resetPin = 13

GPIO.setmode(GPIO.BOARD)
GPIO.setup(resetPin, GPIO.OUT)
GPIO.output(resetPin, GPIO.LOW)

setLogging()
logger = logging.getLogger('ble-mqtt-bridge')

mqttClient = MqttClient(config.mqtt, logger)
if not mqttClient.run():
    logger.critical("Could not start MQTT client. Exiting.")
    GPIO.cleanup()
    exit(1)

mqttHandler = MqttHandler(mqttClient)
mqttHandler.setLevel(logging.DEBUG)
mqttHandler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(mqttHandler)

scanDevice = None
device = None
scanAttempt = 0
try:
    while True:
        if scanDevice == None:
            connectionAttempt = 0
            scanAttempt += 1
            scanDevice = DeviceScanner(config.sensor, logger).scan()
            if scanDevice != None:
                scanAttempt = 0
            else:
                if scanAttempt >= config.sensor["maxScanRetries"]:
                    logger.warning("Scan retry threshold exceeded. Resetting Bluetooth adaptor and sensor")
                    GPIO.output(resetPin, HIGH)
                    time.sleep(0.1)
                    GPIO.output(resetPin, LOW)
                    os.system("sudo hciconfig hci0 reset")
                    os.system(" sudo invoke-rc.d bluetooth restart")
                    time.sleep(1)
                    scanAttempt = 0
                else:
                    logger.warning("Device not found (attempt {0})".format(scanAttempt))
        if scanDevice != None and device == None:
            scanDeviceName = scanDevice.name
            connectionAttempt += 1
            mqttClient.setSensorName(scanDeviceName)
            connector = DeviceConnector(scanDevice, mqttClient, logger)
            try:
                device = connector.connect()
            except Exception as ex:
                template = "An exception of type {0} occurred trying to connect. Forcing rescan. Exception arguments:{1!r}"
                message = template.format(type(ex).__name__, ex.args)
                logger.error(message)
                scanDevice = None
                device = None
                logger.info("Passed putting device and scanDevice on None")
            if device != None:
                logger.info("Connected to sensor '{0}', using topic template '{1}'.".format(scanDeviceName, mqttClient.topicTemplate))
                connectionAttempt = 0
            else:
                logger.warning("Could not connect to sensor '{0}' (attempt {1})".format(scanDeviceName, connectionAttempt))
                if connectionAttempt >= config.sensor["maxConnectionRetries"]:
                    logger.warning("Connection retry threshold exceeded. Forcing rescan.")
                    scanDevice = None
        if device != None:
            try:
                if device.waitForNotifications(1):
                    pass
            except (btle.BTLEDisconnectError, AttributeError):
                logger.warning("Disconnected.")
                device = None
except KeyboardInterrupt:
    logger.info("Keyboard Interrupt")
    pass
finally:
    logger.info("Exiting")
    GPIO.cleanup()
    try:
        device.disconnect()
    except Exception:
        pass
    logger.info("Disconnected from sensor")
    logger.removeHandler(mqttHandler)
    mqttClient.loop_stop()
    mqttClient.disconnect()
    logger.info("Disconnected from MQTT broker.")
    logging.shutdown()