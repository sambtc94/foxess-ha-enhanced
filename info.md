## 💽 Version
{% if version_installed == version_available %} 
👍 You already have the latest released version installed. 
{% endif %}

<h2 align="center">
   <a href="https://www.fox-ess.com">FoxESS</a> and<a href="https://www.home-assistant.io"> Home Assistant</a> integration  🏡 ☀
   </br></br>
   <img src="https://github.com/home-assistant/brands/raw/master/custom_integrations/foxess/logo.png" >
   </br>
   <a href="https://github.com/hacs/default"><img src="https://img.shields.io/badge/HACS-default-sucess"></a>
   <a href="https://github.com/macxq/foxess-ha/actions/workflows/HACS.yaml/badge.svg?branch=main"><img src="https://github.com/macxq/foxess-ha/actions/workflows/HACS.yaml/badge.svg?branch=main"/></a>
    <a href="https://github.com/macxq/foxess-ha/actions/workflows/hassfest.yaml/badge.svg"><img src="https://github.com/macxq/foxess-ha/actions/workflows/hassfest.yaml/badge.svg"/></a>
    </br>
</h2>

## ⚙️ Installation & Updates

Use HACS to install and update this integration. After installation, restart Home Assistant.

## Manual installation

Create `/homeassistant/custom_components/foxess_ha_enhanced` and copy the integration files into it, then restart Home Assistant.

## Configuration

New installations should use the Home Assistant config flow:

1. Open **Settings → Devices & services**.
2. Select **Add integration**.
3. Search for **foxess-ha-enhanced**.
4. Enter the FoxESS API key, inverter serial number, and a device ID.

The API key is generated in FoxESS Cloud under **User Profile → API Management**. Keep it private.

For legacy YAML installations, add:

```yaml
sensor:
  - platform: foxess_ha_enhanced
    deviceID: enter_your_inverter_id
    deviceSN: enter_your_inverter_serial_number
    apiKey: enter_your_personal_api_key
```

#### Configuration notes

- Username and password are not required; the integration uses a FoxESS personal API key.
- `deviceSN` is the inverter serial number.
- For new installations, use the inverter serial number as `deviceID`. Keep an existing legacy `deviceID` unchanged to preserve entity history.
- Optional settings include `name`, `extendPV`, `xtZone`, `Restrict`, `Evo`, and `refreshInterval`.
- `refreshInterval` is measured in minutes and accepts values from 1 to 30. The default is 5 minutes.

- `your_inverter_serial_number` is the serial number of the inverter this integration will be gathering data from, you can see the deviceSN by logging into the Foxesscloud.com website, in the left hand menu click on 'Device', then 'Inverter' this will display a table and your Inverter SN - the format will be similar to : 60BHnnnnnnXnnn - copy and paste this into the config setting deviceSN: replacing the text `foxesscloud_inverter_serial_number`

- `your_personal_api_key` is a personal api_key that is generated in your profile selection of your Foxesscloud account. To do this log into the Foxesscloud.com website, click on the 'profile icon' in the top right corner and select 'User Profile'. Then on the menu on the left hand side select 'API Management' and click 'Generate API Key, the long string that it generates should be copied and pasted into the platform config setting of your configuration.yaml apiKey: replacing the text `foxesscloud_personal_api_key` (see example above).

- `deviceID` is used to build Home Assistant entity IDs. For legacy installations, it may be the UUID found in the FoxESS Cloud inverter details URL. Do not change it after installation unless you accept losing the existing entity history.
- Multi-inverter support - if you have more than one FoxESS device in your installation, you can leverage the optional `name` field in your config,
   ```
   sensor:
     - platform: foxess_ha_enhanced
       name: Fox1
       deviceID: enter_your_inverter_id_1
       deviceSN: enter_your_inverter_serial_number_1
       apiKey: enter_your_personal_api_key_1
     - platform: foxess_ha_enhanced
       name: Fox2
       deviceID: enter_your_inverter_id_2
       deviceSN: enter_your_inverter_serial_number_2
       apiKey: enter_your_personal_api_key_2
   ```
 



## Provided entities

HA Entity  | Measurement
|---|---|
Inverter |  on-line/off-line/in-alarm
Generation Power  |  kW 
Grid Consumption Power  |  kW  
FeedIn Power  |  kW  
Bat Discharge Power  |  kW   
Bat Charge Power  |  kW  
Solar Power | kW
Load Power | kW
Meter2 Power | kW
PV1 Current | A
PV1 Power | kW
PV1 Volt | V
PV2 Current | A
PV2 Power | kW
PV2 Volt | V
PV3 Current | A
PV3 Power | kW
PV3 Volt | V
PV4 Current | A
PV4 Power | kW
PV4 Volt | V
PV Power | kW
R Current | A
R Freq | Hz
R Power | kW
R Volt | V
S Current | A
S Freq | Hz
S Power | kW
S Volt | V
T Current | A
T Freq | Hz
T Power | kW
T Volt | V
Reactive Power | kVar
Energy Generated  |  kWh 
Grid Consumption  |  kWh 
FeedIn  |  kWh  
Solar  |  kWh 
Load |  kWh 
Bat Charge  |  kWh 
Bat Discharge  |  kWh  
Bat SoC | % (for single battery systems)
Bat SoC1 | % (for dual battery systems)
Bat SoC2 | % (for dual battery systems)
Bat Temp | °C 
Ambient Temp | °C
Boost Temp | °C
Inv Temp | °C
Residual Energy | kWh
minSoC | %
minSoC on Grid | %
Peak Shaving Import Limit | W
Peak Shaving Battery SOC | %
Mode Scheduler | on/off
Mode Scheduler Schedule | number of configured groups and schedule attributes
Power Factor | %
API Response Time | mS

### Controls

- **Work Mode** select: `SelfUse`, `Feedin`, `Backup`, and `PeakShaving` for immediate inverter control.
- **Min SoC** and **Min SoC on Grid** number controls.
- **Peak Shaving Import Limit** and **Peak Shaving Battery SOC** number controls, when supported by the inverter.
- **Mode Scheduler** switch, when supported by the inverter.

Force Charge and Force Discharge are scheduler-only modes. They are not offered by the immediate Work Mode selector.

### Scheduler action

Use the `foxess_ha_enhanced.refresh_scheduler` action to re-read the scheduler time segments. It requires the inverter serial number:

```yaml
device_sn: YOUR_INVERTER_SERIAL
```

Use the `foxess_ha_enhanced.set_scheduler` action to write scheduler time groups. Example:

```yaml
device_sn: YOUR_INVERTER_SERIAL
is_default: false
groups:
  - enable: 1
    startHour: 0
    startMinute: 0
    endHour: 5
    endMinute: 59
    workMode: SelfUse
    minSocOnGrid: 10
    fdSoc: 90
    fdPwr: 3000
    maxSoc: 100
```

Scheduler groups support `SelfUse`, `Feedin`, `Backup`, `ForceCharge`, and `ForceDischarge`. The integration uses the scheduler V3 API for reading and writing groups.

Peak Shaving is separate from scheduler groups. It limits grid import and preserves a configured battery SOC. FoxESS does not document a fixed priority between Peak Shaving and the scheduler; behavior can depend on inverter firmware.

💡 If you want to understand energy generation per string check out this wiki [article](https://github.com/macxq/foxess-ha/wiki/Understand-PV-string-power-generation-using-foxess-ha)

## 🤔 Troubleshooting 

API Error summary:

- `{"errno":41930,"result":null}` ⟶ incorrect inverter id
- `{"errno":40261,"result":null}` ⟶ incorrect inverter id
- `{"errno":41807,"result":null}` ⟶ wrong user name or password
- `{"errno":41808,"result":null}` ⟶ token expired
- `{"errno":41809,"result":null}` ⟶ invalid token
- `{"errno":40256,"result":null}` ⟶ Request header parameters are missing. Check whether the request headers are consistent with OpenAPI requirements.
- `{"errno":40257,"result":null}` ⟶ Request body parameters are invalid. Check whether the request body is consistent with OpenAPI requirements.
- `{"errno":40400,"result":null}` ⟶ The number of requests is too frequent. Please reduce the frequency of access.


Increase log level in your `/configuration.yaml` by adding:

```yaml
logger:
  default: warning
  logs:
    custom_components.foxess_ha_enhanced: debug
```

## FoxESS Open API Access and Limits
FoxESS provide an OpenAPI that allows registered users to make request to return datasets.

The OpenAPI access is limited and each user must have a personal_api_key to access it, this personal_api_key can be generated by logging into the FoxESS cloud wesbite - then click on the Profile Icon in the top right hand corner of the screen, select User Profile and then from the menu select API Management, click the button to 'Generate API Key' - this long string of numbers is your personal_api_key and must be used for access to your systems details.

The OpenAPI has a limit of 1,440 API calls per day, after which the OpenAPI will stop responding to requests and generate a "40400" error.

This sounds like a large number of calls, but bear in mind that multiple API calls have to be made on each scan to gain the complete dataset for a system.

The integration paces the number of API calls that are made, with the following frequency -

- Site status and plant details - every 15 minutes
- Real time variables - every 5 minutes
- Cumulative total reports (generation, feedin, gridConsumption, BatterychargeTotal, Batterydischargetotal, home load) - every 15 minutes
- Daily Generation report (Daily Energy Generated - 'total yield') - every 30 minutes
- Battery minSoC settings - every 30 minutes
- Scheduler status and groups - every 15 minutes
- Peak Shaving settings - every 15 minutes

The integration uses approximately 36 API calls an hour (864 a day and well within the 1,440 limit) with the default refresh interval.

If you have multiple inverters in your account, you will receive 1,440 calls per inverter, so for 2 inverters you will have 2,880 api calls.


## 📚 Usefull wiki articles
* [Understand PV string power generation using foxess ha](https://github.com/macxq/foxess-ha/wiki/Understand-PV-string-power-generation-using-foxess-ha)
* [Sample sensors for better solar monitoring](https://github.com/macxq/foxess-ha/wiki/Sample-sensors-for-better-solar-monitoring)
* [How to fix Energy Dashboard data (statistic data)](https://github.com/macxq/foxess-ha/wiki/How-to-fix-Energy-Dashboard-data-(statistic-data))
