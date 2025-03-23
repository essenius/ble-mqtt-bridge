import secrets

## secrets.py contains:
# mqtt = {
#     "broker": "name of the MQTT broker",
#     "username": "MQTT user",
#     "password": "password for the MQTT user",
# }
#
# sensor = {
#     "serviceUuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
# }
## xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx is the UUID of the service that the sensor advertises (hexadecimal, lowercase)

mqtt = {
    "broker": secrets.mqtt["broker"],
    "port": 8883,
    "timeout": 60,
    "caCert": "../ca.crt",
    "username": secrets.mqtt["username"],
    "password": secrets.mqtt["password"],
    "clientIdSuffix": "-ble-broker-pi4",
}

sensor = {
    "deviceName": "Power",
    "serviceUuid": secrets.sensor["serviceUuid"],
    "scanTime": 20,
    "maxConnectionRetries": 10,
    "maxScanRetries": 10,
}
