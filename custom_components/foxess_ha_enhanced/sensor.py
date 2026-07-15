from __future__ import annotations

from collections import namedtuple
from datetime import date, datetime, timedelta
from dateutil import parser
import time
import logging
import json
import hashlib
import asyncio
import voluptuous as vol

from homeassistant.components.rest.data import RestData
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
    PLATFORM_SCHEMA,
    SensorEntity,
)


from homeassistant.const import (
    ATTR_DATE,
    ATTR_TIME,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_NAME,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfEnergy,
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfFrequency,
    UnitOfReactivePower,
    PERCENTAGE,
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util.ssl import SSLCipherList
from homeassistant.helpers.icon import icon_for_battery_level
import homeassistant.helpers.config_validation as cv

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)
_ENDPOINT_OA_DOMAIN = "https://www.foxesscloud.com"
_ENDPOINT_OA_BATTERY_SETTINGS = "/op/v0/device/battery/soc/get?sn="
_ENDPOINT_OA_REPORT = "/op/v0/device/report/query"
_ENDPOINT_OA_DEVICE_DETAIL = "/op/v0/device/detail"
_ENDPOINT_OA_DEVICE_DETAIL_V1 = "/op/v1/device/detail"
_ENDPOINT_OA_DEVICE_VARIABLES = "/op/v0/device/real/query"
_ENDPOINT_OA_DEVICE_VARIABLES_V1 = "/op/v1/device/real/query"
_ENDPOINT_OA_DAILY_GENERATION = "/op/v0/device/generation?sn="

METHOD_POST = "POST"
METHOD_GET = "GET"
DEFAULT_ENCODING = "UTF-8"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_TIMEOUT = 75  # increase the size of inherited timeout, the API is a bit slow

ATTR_DEVICE_SN = "deviceSN"
ATTR_PLANTNAME = "plantName"
ATTR_MODULESN = "moduleSN"
ATTR_DEVICE_TYPE = "deviceType"
ATTR_MASTER = "masterVersion"
ATTR_MANAGER = "managerVersion"
ATTR_SLAVE = "slaveVersion"
ATTR_BATTERYLIST = "batteryList"
ATTR_LASTCLOUDSYNC = "lastCloudSync"

BATTERY_LEVELS = {"High": 80, "Medium": 50, "Low": 25, "Empty": 10}

CONF_APIKEY = "apiKey"
CONF_DEVICESN = "deviceSN"
CONF_DEVICEID = "deviceID"
CONF_SYSTEM_ID = "system_id"
CONF_EXTPV = "extendPV"
CONF_XTZONE = "xtZone"
CONF_GET_VARIABLES = "Restrict"
CONF_V1_API = "Use_V1_Api"
CONF_EVO = "Evo"
CONF_REFRESH_INTERVAL = "refreshInterval"
RETRY_NEXT_SLOT = -1
DNS_ERROR = 101

DEFAULT_NAME = "FoxESS"
DEFAULT_VERIFY_SSL = False  # True
DEFAULT_USE_V1_API = True

SCAN_MINUTES = 5  # default interval in minutes between API requests
SCAN_INTERVAL = timedelta(minutes=SCAN_MINUTES)
# Cycle length in ticks before resetting to tslice=0 (battery settings + daily gen refresh).
# At the default 5-min interval: 12 ticks × 5 min = 60-minute cycle.
TICK_CYCLE_LENGTH = 11

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
        vol.Required(CONF_APIKEY): cv.string,
        vol.Required(CONF_DEVICESN): cv.string,
        vol.Required(CONF_DEVICEID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_EXTPV): cv.boolean,
        vol.Optional(CONF_XTZONE): cv.boolean,
        vol.Optional(CONF_GET_VARIABLES): cv.boolean,
        vol.Optional(CONF_V1_API): cv.boolean,
        vol.Optional(CONF_EVO): cv.boolean,
        vol.Optional(CONF_REFRESH_INTERVAL, default=SCAN_MINUTES): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=30)
        ),
    }
)

token = None
last_api = 0
RestrictGetVar = False
xtzone = False
V1_Api = DEFAULT_USE_V1_API
Evo = False


def _initial_all_data():
    all_data = {
        "report": {},
        "reportDailyGeneration": {},
        "raw": {},
        "battery": {},
        "addressbook": {},
        "online": False,
        "workMode": None,
    }
    all_data["addressbook"]["hasBattery"] = False
    all_data["addressbook"]["status"] = "3"
    return all_data


class FoxESSCoordinator(DataUpdateCoordinator):
    def __init__(
        self,
        hass,
        *,
        device_id,
        device_sn,
        api_key,
        name_prefix,
        refresh_interval,
        ext_pv=False,
        xt_zone=False,
        restrict_get_var=False,
        v1_api=DEFAULT_USE_V1_API,
        evo=False,
    ):
        self.device_id = device_id
        self.device_sn = device_sn
        self.api_key = api_key
        self.name_prefix = name_prefix
        self.ext_pv = ext_pv
        self.xt_zone = xt_zone
        self.restrict_get_var = restrict_get_var
        self.v1_api = v1_api
        self.evo = evo
        self._refresh_interval = refresh_interval
        self._timeslice = RETRY_NEXT_SLOT
        self._last_hour = None
        self._last_api = 0
        self._api_calls_today = 0
        self._api_calls_date = date.today()
        self._all_data = _initial_all_data()
        super().__init__(
            hass,
            _LOGGER,
            name=name_prefix,
            update_interval=timedelta(minutes=refresh_interval),
        )

    @property
    def api_calls_today(self):
        return self._api_calls_today

    def increment_api_call(self):
        today = date.today()
        if self._api_calls_date != today:
            self._api_calls_date = today
            self._api_calls_today = 0
        self._api_calls_today += 1

    async def _async_update_data(self):
        allData = self._all_data
        _LOGGER.debug("Updating data from https://www.foxesscloud.com/")
        hournow = datetime.now().strftime("%H")
        tslice = self._timeslice + 1
        self._timeslice = tslice

        _LOGGER.debug("Poll tick: %s, tslice %s", self.device_sn, tslice)
        geterror = False

        # Refresh device detail every 3 ticks (~15 min at the default 5-min interval).
        # tslice==0 also satisfies % 3, so device detail is always fetched on startup.
        if tslice % 3 == 0:
            if self.evo:
                geterror = await getOADeviceList(
                    self.hass, allData, self.device_sn, self.api_key, coordinator=self
                )
            else:
                geterror = await getOADeviceDetail(
                    self.hass, allData, self.device_sn, self.api_key, coordinator=self
                )
            await asyncio.sleep(1)

        if not geterror:
            if allData["addressbook"]["status"] is not None:
                statetest = int(allData["addressbook"]["status"])
                if statetest in [3]:
                    allData["raw"]["runningState"] = "164"
            else:
                statetest = 0
            _LOGGER.debug("Statetest %s", statetest)

            if statetest in [1, 2]:
                allData["online"] = True
                # Battery settings and daily generation are slow-changing; refresh once per cycle.
                if tslice == 0:
                    await getOABatterySettings(
                        self.hass, allData, self.device_sn, self.api_key, coordinator=self
                    )
                    await asyncio.sleep(1)
                geterror = await getRaw(
                    self.hass, allData, self.api_key, self.device_sn, coordinator=self
                )
                if not geterror:
                    if tslice % 3 == 0:
                        await asyncio.sleep(1)
                        geterror = await getReport(
                            self.hass, allData, self.api_key, self.device_sn, coordinator=self
                        )
                        if not geterror:
                            await asyncio.sleep(1)
                            work_mode_error = await getWorkMode(
                                self.hass, allData, self.device_sn, self.api_key, coordinator=self
                            )
                            if work_mode_error:
                                _LOGGER.debug("getWorkMode returned error")
                            if tslice == 0:
                                await asyncio.sleep(1)
                                geterror = await getReportDailyGeneration(
                                    self.hass,
                                    allData,
                                    self.api_key,
                                    self.device_sn,
                                    coordinator=self,
                                )
                                if geterror:
                                    _LOGGER.debug("getReportDailyGeneration returned error")
                        else:
                            _LOGGER.debug("getReport returned error")
                        if geterror:
                            geterror = False
                            allData["online"] = False
                else:
                    _LOGGER.debug("getRaw failed for SN: %s", self.device_sn)
                    if statetest == 2:
                        _LOGGER.debug("Inverter in alarm state for SN: %s", self.device_sn)
                        allData["online"] = False
                    else:
                        if geterror == DNS_ERROR:
                            _LOGGER.warning("Fox Cloud - DNS failure, will retry next poll")
                        else:
                            allData["online"] = False
                    geterror = False
            else:
                if statetest == 3:
                    allData["online"] = False
                    _LOGGER.debug("Inverter off-line for SN: %s", self.device_sn)

            if not allData["online"]:
                _LOGGER.warning("%s Inverter is off-line, waiting to retry", self.name_prefix)
        else:
            _LOGGER.warning(
                "%s Cloud timeout on Device Detail, will retry next poll.",
                self.name_prefix,
            )

        # Reset the cycle counter so battery settings and daily gen are refreshed each hour.
        if tslice >= TICK_CYCLE_LENGTH:
            tslice = RETRY_NEXT_SLOT
        _LOGGER.debug("Poll tick complete %s, next tslice %s", self.device_sn, tslice)

        if self._last_hour != hournow:
            self._last_hour = hournow

        self._timeslice = tslice
        _LOGGER.debug(allData)
        return allData


async def create_foxess_coordinator(hass, config):
    name = config.get(CONF_NAME, DEFAULT_NAME)
    deviceID = config.get(CONF_DEVICEID, config.get(CONF_DEVICESN))
    devicesn = config.get(CONF_DEVICESN)
    apiKey = config.get(CONF_APIKEY)
    ExtPV = config.get(CONF_EXTPV, False) is True
    xtZone = config.get(CONF_XTZONE, False) is True
    Restrict = config.get(CONF_GET_VARIABLES, False) is True
    V1 = config.get(CONF_V1_API) is not False
    evo = config.get(CONF_EVO, False) is True
    refresh_interval = int(config.get(CONF_REFRESH_INTERVAL, SCAN_MINUTES))

    _LOGGER.debug("Device SN: %s", devicesn)
    _LOGGER.debug("Device ID: %s", deviceID)
    _LOGGER.debug("FoxESS Scan Interval: %s minutes", refresh_interval)
    _LOGGER.debug("Cross Time Zone: %s", xtZone)
    _LOGGER.debug("Restrict Variables: %s", Restrict)
    _LOGGER.debug("Extended PV: %s", ExtPV)
    _LOGGER.debug("v1 Api Calls: %s", V1)
    _LOGGER.debug("EVO: %s", evo)
    if V1:
        _LOGGER.debug("v1 Api Calls Enabled")
    else:
        _LOGGER.warning("v1 Api Calls Disabled, using v0")
    if ExtPV:
        _LOGGER.warning("Extended PV 1-18 strings enabled")
    else:
        _LOGGER.debug("Extended PV Disabled")
    if Restrict:
        _LOGGER.warning("Get Variables is in restricted mode")
    else:
        _LOGGER.debug("Get Variables is full variable mode")

    coordinator = FoxESSCoordinator(
        hass,
        device_id=deviceID,
        device_sn=devicesn,
        api_key=apiKey,
        name_prefix=name,
        refresh_interval=refresh_interval,
        ext_pv=ExtPV,
        xt_zone=xtZone,
        restrict_get_var=Restrict,
        v1_api=V1,
        evo=evo,
    )
    await coordinator.async_refresh()
    return coordinator


def _build_entities(coordinator):
    name = coordinator.name_prefix
    deviceID = coordinator.device_id
    entities = [
        FoxESSCurrent(coordinator, name, deviceID, "PV1 Current", "pv1-current", "pv1Current"),
        FoxESSPower(coordinator, name, deviceID, "PV1 Power", "pv1-power", "pv1Power"),
        FoxESSVolt(coordinator, name, deviceID, "PV1 Volt", "pv1-volt", "pv1Volt"),
        FoxESSCurrent(coordinator, name, deviceID, "PV2 Current", "pv2-current", "pv2Current"),
        FoxESSPower(coordinator, name, deviceID, "PV2 Power", "pv2-power", "pv2Power"),
        FoxESSVolt(coordinator, name, deviceID, "PV2 Volt", "pv2-volt", "pv2Volt"),
        FoxESSCurrent(coordinator, name, deviceID, "PV3 Current", "pv3-current", "pv3Current"),
        FoxESSPower(coordinator, name, deviceID, "PV3 Power", "pv3-power", "pv3Power"),
        FoxESSVolt(coordinator, name, deviceID, "PV3 Volt", "pv3-volt", "pv3Volt"),
        FoxESSCurrent(coordinator, name, deviceID, "PV4 Current", "pv4-current", "pv4Current"),
        FoxESSPower(coordinator, name, deviceID, "PV4 Power", "pv4-power", "pv4Power"),
        FoxESSVolt(coordinator, name, deviceID, "PV4 Volt", "pv4-volt", "pv4Volt"),
        FoxESSCurrent(coordinator, name, deviceID, "PV5 Current", "pv5-current", "pv5Current"),
        FoxESSPower(coordinator, name, deviceID, "PV5 Power", "pv5-power", "pv5Power"),
        FoxESSVolt(coordinator, name, deviceID, "PV5 Volt", "pv5-volt", "pv5Volt"),
        FoxESSCurrent(coordinator, name, deviceID, "PV6 Current", "pv6-current", "pv6Current"),
        FoxESSPower(coordinator, name, deviceID, "PV6 Power", "pv6-power", "pv6Power"),
        FoxESSVolt(coordinator, name, deviceID, "PV6 Volt", "pv6-volt", "pv6Volt"),
        FoxESSPower(coordinator, name, deviceID, "PV Power", "pv-power", "pvPower"),
        FoxESSCurrent(coordinator, name, deviceID, "R Current", "r-current", "RCurrent"),
        FoxESSFreq(coordinator, name, deviceID, "R Freq", "r-freq", "RFreq"),
        FoxESSPower(coordinator, name, deviceID, "R Power", "r-power", "RPower"),
        FoxESSPowerString(coordinator, name, deviceID, "Meter2 Power", "meter2-power", "meterPower2"),
        FoxESSVolt(coordinator, name, deviceID, "R Volt", "r-volt", "RVolt"),
        FoxESSCurrent(coordinator, name, deviceID, "S Current", "s-current", "SCurrent"),
        FoxESSFreq(coordinator, name, deviceID, "S Freq", "s-freq", "SFreq"),
        FoxESSPower(coordinator, name, deviceID, "S Power", "s-power", "SPower"),
        FoxESSVolt(coordinator, name, deviceID, "S Volt", "s-volt", "SVolt"),
        FoxESSCurrent(coordinator, name, deviceID, "T Current", "t-current", "TCurrent"),
        FoxESSFreq(coordinator, name, deviceID, "T Freq", "t-freq", "TFreq"),
        FoxESSPower(coordinator, name, deviceID, "T Power", "t-power", "TPower"),
        FoxESSVolt(coordinator, name, deviceID, "T Volt", "t-volt", "TVolt"),
        FoxESSReactivePower(coordinator, name, deviceID),
        FoxESSPowerFactor(coordinator, name, deviceID),
        FoxESSTemp(coordinator, name, deviceID, "Bat Temperature", "bat-temperature", "batTemperature"),
        FoxESSTemp(coordinator, name, deviceID, "Bat Temperature2", "bat-temperature2", "batTemperature_2"),
        FoxESSTemp(coordinator, name, deviceID, "Ambient Temperature", "ambient-temperature", "ambientTemperation"),
        FoxESSTemp(coordinator, name, deviceID, "Boost Temperature", "boost-temperature", "boostTemperation"),
        FoxESSTemp(coordinator, name, deviceID, "Inv Temperature", "inv-temperature", "invTemperation"),
        FoxESSBatSoC(coordinator, name, deviceID, "Bat SoC", "bat-soc", "SoC"),
        FoxESSBatSoC(coordinator, name, deviceID, "Bat SoC1", "bat-soc1", "SoC_1"),
        FoxESSBatSoC(coordinator, name, deviceID, "Bat SoC2", "bat-soc2", "SoC_2"),
        FoxESSBatSoC(coordinator, name, deviceID, "Bat SoH", "bat-soh", "SOH"),
        FoxESSPower(coordinator, name, deviceID, "Inverter Bat Power", "inv-Bat-Power", "invBatPower"),
        FoxESSPower(coordinator, name, deviceID, "Inverter Bat Power2", "inv-Bat-Power2", "invBatPower_2"),
        FoxESSBatMinSoC(coordinator, name, deviceID),
        FoxESSBatMinSoConGrid(coordinator, name, deviceID),
        FoxESSSolarPower(coordinator, name, deviceID),
        FoxESSEnergyThroughput(coordinator, name, deviceID),
        FoxESSEnergySolar(coordinator, name, deviceID),
        FoxESSInverter(coordinator, name, deviceID),
        FoxESSPowerString(coordinator, name, deviceID, "Generation Power", "-generation-power", "generationPower"),
        FoxESSPowerString(coordinator, name, deviceID, "Grid Consumption Power", "grid-consumption-power", "gridConsumptionPower"),
        FoxESSPowerString(coordinator, name, deviceID, "FeedIn Power", "feedIn-power", "feedinPower"),
        FoxESSPowerString(coordinator, name, deviceID, "Bat Discharge Power", "bat-discharge-power", "batDischargePower"),
        FoxESSPowerString(coordinator, name, deviceID, "Bat Charge Power", "bat-charge-power", "batChargePower"),
        FoxESSPowerString(coordinator, name, deviceID, "Load Power", "load-power", "loadsPower"),
        FoxESSEnergyGenerated(coordinator, name, deviceID, "Energy Generated", "energy-generated", "value"),
        FoxESSEnergyGenerated(coordinator, name, deviceID, "Energy Generated Month", "energy-generated-month", "month"),
        FoxESSEnergyGenerated(coordinator, name, deviceID, "Energy Generated Cumulative", "energy-generated-cumulative", "cumulative"),
        FoxESSEnergyGridConsumption(coordinator, name, deviceID),
        FoxESSEnergyFeedin(coordinator, name, deviceID),
        FoxESSEnergyBatCharge(coordinator, name, deviceID),
        FoxESSEnergyBatDischarge(coordinator, name, deviceID),
        FoxESSEnergyLoad(coordinator, name, deviceID),
        FoxESSPVEnergyTotal(coordinator, name, deviceID),
        FoxESSResidualEnergy(coordinator, name, deviceID),
        FoxESSResponseTime(coordinator, name, deviceID),
        FoxESSRunningState(coordinator, name, deviceID, "Running State", "running-state", "runningState"),
        FoxESSBatteryMode(coordinator, name, deviceID),
        FoxESSApiCallCount(coordinator, name, deviceID),
        # EPS (Emergency Power Supply) sensors
        FoxESSPower(coordinator, name, deviceID, "EPS Power", "eps-power", "epsPower"),
        FoxESSCurrent(coordinator, name, deviceID, "EPS Current R", "eps-current-r", "epsCurrentR"),
        FoxESSCurrent(coordinator, name, deviceID, "EPS Current S", "eps-current-s", "epsCurrentS"),
        FoxESSCurrent(coordinator, name, deviceID, "EPS Current T", "eps-current-t", "epsCurrentT"),
        FoxESSPower(coordinator, name, deviceID, "EPS Power R", "eps-power-r", "epsPowerR"),
        FoxESSPower(coordinator, name, deviceID, "EPS Power S", "eps-power-s", "epsPowerS"),
        FoxESSPower(coordinator, name, deviceID, "EPS Power T", "eps-power-t", "epsPowerT"),
        FoxESSVolt(coordinator, name, deviceID, "EPS Volt R", "eps-volt-r", "epsVoltR"),
        FoxESSVolt(coordinator, name, deviceID, "EPS Volt S", "eps-volt-s", "epsVoltS"),
        FoxESSVolt(coordinator, name, deviceID, "EPS Volt T", "eps-volt-t", "epsVoltT"),
        # Fault diagnostics
        FoxESSFaultCount(coordinator, name, deviceID),
    ]

    if coordinator.ext_pv:
        entities.extend(
            [
                FoxESSCurrent(coordinator, name, deviceID, "PV7 Current", "pv7-current", "pv7Current"),
                FoxESSPower(coordinator, name, deviceID, "PV7 Power", "pv7-power", "pv7Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV7 Volt", "pv7-volt", "pv7Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV8 Current", "pv8-current", "pv8Current"),
                FoxESSPower(coordinator, name, deviceID, "PV8 Power", "pv8-power", "pv8Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV8 Volt", "pv8-volt", "pv8Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV9 Current", "pv9-current", "pv9Current"),
                FoxESSPower(coordinator, name, deviceID, "PV9 Power", "pv9-power", "pv9Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV9 Volt", "pv9-volt", "pv9Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV10 Current", "pv10-current", "pv10Current"),
                FoxESSPower(coordinator, name, deviceID, "PV10 Power", "pv10-power", "pv10Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV10 Volt", "pv10-volt", "pv10Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV11 Current", "pv11-current", "pv11Current"),
                FoxESSPower(coordinator, name, deviceID, "PV11 Power", "pv11-power", "pv11Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV11 Volt", "pv11-volt", "pv11Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV12 Current", "pv12-current", "pv12Current"),
                FoxESSPower(coordinator, name, deviceID, "PV12 Power", "pv12-power", "pv12Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV12 Volt", "pv12-volt", "pv12Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV13 Current", "pv13-current", "pv13Current"),
                FoxESSPower(coordinator, name, deviceID, "PV13 Power", "pv13-power", "pv13Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV13 Volt", "pv13-volt", "pv13Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV14 Current", "pv14-current", "pv14Current"),
                FoxESSPower(coordinator, name, deviceID, "PV14 Power", "pv14-power", "pv14Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV14 Volt", "pv14-volt", "pv14Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV15 Current", "pv15-current", "pv15Current"),
                FoxESSPower(coordinator, name, deviceID, "PV15 Power", "pv15-power", "pv15Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV15 Volt", "pv15-volt", "pv15Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV16 Current", "pv16-current", "pv16Current"),
                FoxESSPower(coordinator, name, deviceID, "PV16 Power", "pv16-power", "pv16Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV16 Volt", "pv16-volt", "pv16Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV17 Current", "pv17-current", "pv17Current"),
                FoxESSPower(coordinator, name, deviceID, "PV17 Power", "pv17-power", "pv17Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV17 Volt", "pv17-volt", "pv17Volt"),
                FoxESSCurrent(coordinator, name, deviceID, "PV18 Current", "pv18-current", "pv18Current"),
                FoxESSPower(coordinator, name, deviceID, "PV18 Power", "pv18-power", "pv18Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV18 Volt", "pv18-volt", "pv18Volt"),
            ]
        )

    return entities


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the FoxESS sensor."""
    coordinator = await create_foxess_coordinator(hass, config)

    if not coordinator.last_update_success:
        _LOGGER.error(
            "FoxESS Cloud initialisation failed, Fatal Error - correct error and restart Home Assistant"
        )
        return False

    async_add_entities(_build_entities(coordinator))


class GetAuth:
    def get_signature(self, token, path, lang="en"):
        """
        This function is used to generate a signature consisting of URL, token, and timestamp, and return a dictionary containing the signature and other information.
            :param token: your key
            :param path:  your request path
            :param lang: language, default is English.
            :return: with authentication header
        """
        timestamp = round(time.time() * 1000)
        signature = rf"{path}\r\n{token}\r\n{timestamp}"
        # or use user_agent_rotator.get_random_user_agent() for user-agent
        result = {
            "token": token,
            "lang": lang,
            "timestamp": str(timestamp),
            "Content-Type": "application/json",
            "signature": self.md5c(text=signature),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/117.0.0.0 Safari/537.36",
            "Connection": "close",
        }

        return result

    @staticmethod
    def md5c(text="", _type="lower"):
        # lgtm[py/weak-sensitive-data-hashing] FoxESS OpenAPI requires an MD5 request signature.
        res = hashlib.md5(text.encode(encoding="UTF-8")).hexdigest()
        if _type.__eq__("lower"):
            return res
        else:
            return res.upper()


async def waitforAPI(coordinator=None):
    global last_api
    # wait for openAPI, there is a minimum of 1 second allowed between OpenAPI query calls
    # check if last_api call was less than a second ago and if so delay the balance of 1 second
    now = time.time()
    last = coordinator._last_api if coordinator is not None else last_api
    diff = now - last if last != 0 else 1
    diff = round((diff + 0.2), 2)
    if diff < 1:
        await asyncio.sleep(diff)
        _LOGGER.debug("API enforced delay, wait: %s", diff)
    now = time.time()
    if coordinator is not None:
        coordinator._last_api = now
        coordinator.increment_api_call()
    else:
        last_api = now
    return False


async def getOADeviceDetail(hass, allData, devicesn, apiKey, coordinator=None):
    await waitforAPI(coordinator)

    v1_api = coordinator.v1_api if coordinator is not None else V1_Api
    if v1_api:
        path = _ENDPOINT_OA_DEVICE_DETAIL_V1
        _LOGGER.debug("Device Detail using V1 API")
    else:
        path = _ENDPOINT_OA_DEVICE_DETAIL

    headerData = GetAuth().get_signature(token=apiKey, path=path)

    path = _ENDPOINT_OA_DOMAIN + path + "?sn="
    _LOGGER.debug("OADevice Detail fetch %s%s", path, devicesn)
    timestamp = round(time.time() * 1000)

    restOADeviceDetail = RestData(
        hass,
        METHOD_GET,
        path + devicesn,
        DEFAULT_ENCODING,
        None,
        headerData,
        None,
        None,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )
    await restOADeviceDetail.async_update()

    if restOADeviceDetail.data is None or restOADeviceDetail.data == "":
        _LOGGER.debug("Unable to get OA Device Detail from FoxESS Cloud")
        return True
    else:
        response = json.loads(restOADeviceDetail.data)
        if response["errno"] == 0 and (response["msg"]=='success' or response["msg"]=='Operation successful'):
            ResponseTime = round(time.time() * 1000) - timestamp
            if ResponseTime > 0:
                allData["raw"]["ResponseTime"] = ResponseTime
            else:
                allData["raw"]["ResponseTime"] = 0
            _LOGGER.debug("OA Device Detail Good Response: %s", response["result"])
            result = response["result"]
            allData["addressbook"] = result
            # manually poke this in as on the old cloud it was called plantname, need to keep in line with old entity name
            plantName = result["stationName"]
            allData["addressbook"]["plantName"] = plantName
            testBattery = result["hasBattery"]
            if testBattery:
                _LOGGER.debug("OA Device Detail System has Battery: %s", testBattery)
            else:
                _LOGGER.debug("OA Device Detail System has No Battery: %s", testBattery)
                allData["addressbook"][ATTR_BATTERYLIST] = "No Battery"
            return False
        else:
            _LOGGER.error("OA Device Detail Bad Response: %s", response)
            return True


async def getOADeviceList(hass, allData, devicesn, apiKey, coordinator=None):
    await waitforAPI(coordinator)

    path = "/op/v0/device/list"
    headerData = GetAuth().get_signature(token=apiKey, path=path)

    path = _ENDPOINT_OA_DOMAIN + "/op/v0/device/list"
    _LOGGER.debug("OADevice List fetch %s%s", path, devicesn)
    timestamp = round(time.time() * 1000)

    listData = (
        '{ "currentPage": 1, "pageSize": 10}'
    )

    restOADeviceList = RestData(
        hass,
        METHOD_POST,
        path,
        DEFAULT_ENCODING,
        None,
        headerData,
        None,
        listData,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )
    await restOADeviceList.async_update()

    if restOADeviceList.data is None or restOADeviceList.data == "":
        _LOGGER.debug("Unable to get OA Device List from FoxESS Cloud")
        return True
    else:
        response = json.loads(restOADeviceList.data)
        if response["errno"] == 0 and (response["msg"]=='success' or response["msg"]=='Operation successful'):
            ResponseTime = round(time.time() * 1000) - timestamp
            if ResponseTime > 0:
                allData["raw"]["ResponseTime"] = ResponseTime
            else:
                allData["raw"]["ResponseTime"] = 0
            _LOGGER.debug("OA Device List Good Response: %s", response["result"])
            result = json.loads(restOADeviceList.data)["result"]["data"]
            for item in result:
                variableName = item["stationName"]
                _LOGGER.debug("OA Device List item: %s", item)
                break
            allData["addressbook"] = item
            plantName = item["stationName"]
            allData["addressbook"]["plantName"] = plantName
            allData["addressbook"]["masterVersion"] = 'not provided'
            allData["addressbook"]["managerVersion"] = 'not provided'
            allData["addressbook"]["slaveVersion"] = 'not provided'
            allData["addressbook"]["batteryList"] = 'not provided'
            testBattery = item["hasBattery"]
            if testBattery:
                _LOGGER.debug("OA Device List System has Battery: %s", testBattery)
            else:
                _LOGGER.debug("OA Device List System has No Battery: %s", testBattery)
                allData["addressbook"][ATTR_BATTERYLIST] = "No Battery"

            return False
        else:
            _LOGGER.error("OA Device List Bad Response: %s", response)
            return True


async def getOABatterySettings(hass, allData, devicesn, apiKey, coordinator=None):
    await waitforAPI(coordinator)  # check for api delay

    path = "/op/v0/device/battery/soc/get"
    headerData = GetAuth().get_signature(token=apiKey, path=path)

    path = _ENDPOINT_OA_DOMAIN + _ENDPOINT_OA_BATTERY_SETTINGS
    if "hasBattery" not in allData["addressbook"]:
        hasBattery = False
    else:
        hasBattery = allData["addressbook"]["hasBattery"]

    if hasBattery:
        # only make this call if device detail reports battery fitted
        _LOGGER.debug("OABattery Settings fetch %s %s", path, devicesn)
        restOABatterySettings = RestData(
            hass,
            METHOD_GET,
            path + devicesn,
            DEFAULT_ENCODING,
            None,
            headerData,
            None,
            None,
            DEFAULT_VERIFY_SSL,
            SSLCipherList.PYTHON_DEFAULT,
            DEFAULT_TIMEOUT,
        )
        await restOABatterySettings.async_update()

        if restOABatterySettings.data is None:
            _LOGGER.debug("Unable to get OA Battery Settings from FoxESS Cloud")
            return True
        else:
            response = json.loads(restOABatterySettings.data)
            if response["errno"] == 0 and (response["msg"]=='success' or response["msg"]=='Operation successful'):
                _LOGGER.debug(
                    "OA Battery Settings Good Response: %s", response["result"]
                )
                result = response["result"]
                minSoc = result["minSoc"]
                minSocOnGrid = result["minSocOnGrid"]
                allData["battery"]["minSoc"] = minSoc
                allData["battery"]["minSocOnGrid"] = minSocOnGrid
                _LOGGER.debug(
                    "OA Battery Settings read MinSoc: %d, MinSocOnGrid: %d",
                    minSoc,
                    minSocOnGrid,
                )
                return False
            else:
                _LOGGER.error("OA Battery Settings Bad Response: %s", response)
                return True
    else:
        # device detail reports no battery fitted so reset these variables to show unknown
        allData["battery"]["minSoc"] = None
        allData["battery"]["minSocOnGrid"] = None
        return False


async def getReport(hass, allData, apiKey, devicesn, coordinator=None):
    await waitforAPI(coordinator)  # check for api delay

    path = _ENDPOINT_OA_REPORT
    headerData = GetAuth().get_signature(token=apiKey, path=path)

    path = _ENDPOINT_OA_DOMAIN + _ENDPOINT_OA_REPORT
    _LOGGER.debug("OA Report fetch %s ", path)

    now = datetime.now()
    month = str(datetime.now().month)  # now.strftime("%-m")

    reportData = (
        '{"sn":"'
        + devicesn
        + '","year":'
        + now.strftime("%Y")
        + ',"month":'
        + month
        + ',"dimension":"month","variables":["feedin","generation","gridConsumption","chargeEnergyToTal","dischargeEnergyToTal","loads","PVEnergyTotal"]}'
    )

    _LOGGER.debug("getReport OA request: %s", reportData)

    restOAReport = RestData(
        hass,
        METHOD_POST,
        path,
        DEFAULT_ENCODING,
        None,
        headerData,
        None,
        reportData,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )

    await restOAReport.async_update()

    if restOAReport.data is None or restOAReport.data == "":
        _LOGGER.debug("Unable to get OA Report from FoxESS Cloud")
        return True
    else:
        # Openapi responded so process data
        response = json.loads(restOAReport.data)
        if response["errno"] == 0 and (response["msg"]=='success' or response["msg"]=='Operation successful'):
            _LOGGER.debug(
                "OA Report Data fetched OK: %s %s ", response, restOAReport.data[:350]
            )
            result = json.loads(restOAReport.data)["result"]
            today = int(
                now.strftime("%d")
            )  # need today as an integer to locate in the monthly report index
            for item in result:
                variableName = item["variable"]
                # Daily reports break down the data hour by month for each day
                # so locate the current days index and use that as the sum
                index = 1
                cumulative_total = 0
                for dataItem in item["values"]:
                    if today == index:  # we're only interested in the total for today
                        if dataItem != None:
                            cumulative_total = dataItem
                        else:
                            _LOGGER.debug("Report month fetch, None received")
                        break
                    index += 1
                    # cumulative_total += dataItem
                allData["report"][variableName] = round(cumulative_total, 3)
                _LOGGER.debug(
                    "OA Report Variable: %s, Total: %s", variableName, cumulative_total
                )
            return False
        else:
            _LOGGER.debug("OA Report Bad Response: %s %s ", response, restOAReport.data)
            return True


async def getWorkMode(hass, allData, devicesn, apiKey, coordinator=None):
    await waitforAPI(coordinator)

    path = "/op/v0/device/setting/get"
    headerData = GetAuth().get_signature(token=apiKey, path=path)
    workModeData = json.dumps({"sn": devicesn, "key": "WorkMode"})

    restOAWorkMode = RestData(
        hass,
        METHOD_GET,
        _ENDPOINT_OA_DOMAIN + path,
        DEFAULT_ENCODING,
        None,
        headerData,
        None,
        workModeData,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )

    await restOAWorkMode.async_update()

    if restOAWorkMode.data is None or restOAWorkMode.data == "":
        _LOGGER.debug("Unable to get OA Work Mode from FoxESS Cloud")
        return True

    response = json.loads(restOAWorkMode.data)
    if response["errno"] == 0 and (response["msg"] == 'success' or response["msg"] == 'Operation successful'):
        allData["workMode"] = response.get("result", {}).get("value")
        _LOGGER.debug("OA Work Mode fetched: %s", allData["workMode"])
        return False

    _LOGGER.debug("OA Work Mode Bad Response: %s", response)
    return True


async def getReportDailyGeneration(hass, allData, apiKey, devicesn, coordinator=None):
    await waitforAPI(coordinator)  # check for api delay

    path = "/op/v0/device/generation"
    headerData = GetAuth().get_signature(token=apiKey, path=path)

    path = _ENDPOINT_OA_DOMAIN + _ENDPOINT_OA_DAILY_GENERATION
    _LOGGER.debug("getReportDailyGeneration fetch %s ", path)

    generationData = '{"sn":"' + devicesn + '","dimension":"day"}'

    _LOGGER.debug("getReportDailyGeneration OA request: %s", generationData)

    restOAgen = RestData(
        hass,
        METHOD_GET,
        path + devicesn,
        DEFAULT_ENCODING,
        None,
        headerData,
        None,
        generationData,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )

    await restOAgen.async_update()

    if restOAgen.data is None or restOAgen.data == "":
        _LOGGER.debug("Unable to get OA Daily Generation Report from FoxESS Cloud")
        return True
    else:
        response = json.loads(restOAgen.data)
        if response["errno"] == 0 and (response["msg"]=='success' or response["msg"]=='Operation successful'):
            _LOGGER.debug(
                "OA Daily Generation Report Data fetched OK Response: %s",
                restOAgen.data[:500],
            )

            parsed = json.loads(restOAgen.data)["result"]
            if "today" not in parsed:
                allData["reportDailyGeneration"]["value"] = 0
                _LOGGER.debug(
                    "OA Daily Generation Report data, today has no value: %s set to 0",
                    parsed,
                )
            else:
                allData["reportDailyGeneration"]["value"] = parsed["today"]
                _LOGGER.debug(
                    "OA Daily Generation Report data: todays value %s ", parsed["today"]
                )
            if "month" not in parsed:
                allData["reportDailyGeneration"]["month"] = 0
                _LOGGER.debug(
                    "OA Daily Generation Report data, month has no value: %s set to 0",
                    parsed,
                )
            else:
                allData["reportDailyGeneration"]["month"] = parsed["month"]
                _LOGGER.debug(
                    "OA Daily Generation Report data: month value %s ", parsed["month"]
                )
            if "cumulative" not in parsed:
                allData["reportDailyGeneration"]["cumulative"] = 0
                _LOGGER.debug(
                    "OA Daily Generation Report data, cumulative has no value: %s set to 0",
                    parsed,
                )
            else:
                allData["reportDailyGeneration"]["cumulative"] = parsed["cumulative"]
                _LOGGER.debug(
                    "OA Daily Generation Report data: cumulative value %s ",
                    parsed["cumulative"],
                )
            return False
        else:
            _LOGGER.debug(
                "OA Daily Generation Report Bad Response: %s %s ",
                response,
                restOAgen.data,
            )
            return True


async def getRaw(hass, allData, apiKey, devicesn, coordinator=None):
    await waitforAPI(coordinator)  # check for api delay

    # "deviceSN" used for OpenAPI and it only fetches the real time data

    # build the devicesn string
    v1_api = coordinator.v1_api if coordinator is not None else V1_Api
    if v1_api:
        dsn = '{"sns":["' + devicesn + '"] }' 
    else:
        dsn = '{"sn":"' + devicesn + '" }' 

    restrict_get_var = coordinator.restrict_get_var if coordinator is not None else RestrictGetVar
    v1_api = coordinator.v1_api if coordinator is not None else V1_Api
    xt_zone = coordinator.xt_zone if coordinator is not None else xtzone

    if restrict_get_var:
        _LOGGER.debug("Getting Device Variable in restricted mode")
        # build the devicesn string
        if v1_api:
            dsn = '{"sns":["' + devicesn + '"] ' 
        else:
            dsn = '{"sn":"' + devicesn + '"' 

        rawData = (
            dsn + ',"variables":["ambientTemperation", "batChargePower", "batCurrent", "batCurrent_1", "batCurrent_2", "batDischargePower", "batTemperature", "batTemperature_1", "batTemperature_2", "batVolt", "batVolt_1", "batVolt_2", "boostTemperation", "chargeTemperature", "dspTemperature", "epsCurrentR", "epsCurrentS", "epsCurrentT", "epsPower", "epsPowerR", "epsPowerS", "epsPowerT", "epsVoltR", "epsVoltS", "epsVoltT", "feedinPower", "generationPower", "gridConsumptionPower", "input", "invBatCurrent", "invBatPower", "invBatVolt", "invTemperation", "loadsPower", "loadsPowerR", "loadsPowerS", "loadsPowerT", "meterPower", "meterPower2", "meterPowerR", "meterPowerS", "meterPowerT", "PowerFactor", "pv1Current", "pv1Power", "pv1Volt", "pv2Current", "pv2Power", "pv2Volt", "pv3Current", "pv3Power", "pv3Volt", "pv4Current", "pv4Power", "pv4Volt", "pvPower", "RCurrent", "ReactivePower", "RFreq", "RPower", "RVolt", "SCurrent", "SFreq", "SoC", "SPower", "SVolt", "TCurrent", "TFreq", "TPower", "TVolt", "SoC_1", "Soc_2", "ResidualEnergy", "energyThroughput", "runningState", "currentFaultCount"] }'
        )
    else:
        rawData = dsn # '{"sn":"' + dsn + '" }'

    _LOGGER.debug("getRaw OA request: %s", rawData)

    timestamp = round(time.time() * 1000)

    if v1_api:
        path = _ENDPOINT_OA_DEVICE_VARIABLES_V1
        _LOGGER.debug("Using V1 API")
    else:
        path = _ENDPOINT_OA_DEVICE_VARIABLES

    headerData = GetAuth().get_signature(token=apiKey, path=path)

    path = _ENDPOINT_OA_DOMAIN + path
    _LOGGER.debug("Path: %s", path)

    restOADeviceVariables = RestData(
        hass,
        METHOD_POST,
        path,
        DEFAULT_ENCODING,
        None,
        headerData,
        None,
        rawData,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )

    await restOADeviceVariables.async_update()
    if restOADeviceVariables.last_exception is not None:
        lastex = str(restOADeviceVariables.last_exception)
        _LOGGER.debug("Getvar exception: %s", lastex)
        if "Timeout while contacting DNS servers" in lastex:
            _LOGGER.debug("Getvar DNS exception: %s", lastex)
            return DNS_ERROR
            # [Timeout while contacting DNS servers]

    if restOADeviceVariables.data is None or restOADeviceVariables.data == "":
        _LOGGER.debug("Unable to get OA Variables from FoxESS Cloud")
        return True
    else:
        # Openapi responded correctly
        response = json.loads(restOADeviceVariables.data)
        if response["errno"] == 0 and (response["msg"]=='success' or response["msg"]=='Operation successful'):
            ResponseTime = round(time.time() * 1000) - timestamp
            if ResponseTime > 0:
                allData["raw"]["ResponseTime"] = ResponseTime
            else:
                allData["raw"]["ResponseTime"] = 0

            test = json.loads(restOADeviceVariables.data)["result"]

            timercv = test[0].get("time")
            try:
                # format is "2025-02-21 16:38:29 GMT+0000" strptime is useless at international dates, so work out the offset
                # tsrcv = datetime.strptime(testt, "%Y-%m-%d %H:%M:%S %Z%z") fails on some countries
                _LOGGER.debug("OA Variables time: %s ", timercv)
                tzoffsetsign = timercv[23:24]
                tzoffsethr = int(timercv[24:26])
                tzoffsetmin = int(timercv[26:28])
                tzfull = str(timercv[23:28])
                _LOGGER.debug(
                    "OA Variables tzoffsign: %s, hr: %s, min: %s, full: %s",
                    tzoffsetsign,
                    tzoffsethr,
                    tzoffsetmin,
                    tzfull,
                )
                if tzoffsetsign in ["+"]:
                    tzoffset = (tzoffsethr * 3600 + tzoffsetmin * 60) * 1
                else:
                    tzoffset = (tzoffsethr * 3600 + tzoffsetmin * 60) * -1
                tsrcv = (parser.parse(timercv, ignoretz=True)).timestamp()
                zulu = datetime.now().astimezone().strftime("%z")
                if zulu != tzfull:
                    if xt_zone:
                        _LOGGER.debug(
                            "OA Variables tsrcv applying offset: %s, offset: %s, zulu: %s",
                            tsrcv,
                            tzoffset,
                            zulu,
                        )
                        tsrcv = tsrcv - tzoffset
                else:
                    _LOGGER.debug(
                        "OA Variables tsrcv is local: %s, zulu: %s, offset: %s ",
                        tsrcv,
                        zulu,
                        tzoffset,
                    )
            except:
                tsrcv = 0
            age = 0
            if tsrcv != 0:
                testd = datetime.now()
                tsnow = round(time.time())
                age = round(tsnow - tsrcv)
                _LOGGER.debug(
                    "OA Variables time: %s vs %s timestamps r:%s now:%s, age: %s",
                    timercv,
                    testd,
                    tsrcv,
                    tsnow,
                    age,
                )
                if age > 361:
                    _LOGGER.debug(
                        "OA Variables invalid age: %s vs %s timestamps r:%s now:%s, age: %s",
                        timercv,
                        testd,
                        tsrcv,
                        tsnow,
                        age,
                    )

            result = test[0].get("datas")
            _LOGGER.debug("OA Variables Good Response: %s", result)
            # allData['raw'] = {}
            for (
                item
            ) in result:  # json.loads(result): # restOADeviceVariables.data)['result']:
                variableName = item["variable"]
                # If value exists
                if item.get("value") is not None:
                    variableValue = item["value"]
                else:
                    variableValue = 0
                    _LOGGER.debug("Variable %s no value, set to zero", variableName)
                # fix for various battery and scale items
                if variableName == "SoC_1":
                    variableName = "SoC_1"  # do nothing for the moment, future release might align this correctly to use SoC
                elif variableName == "batTemperature_1":
                    variableName = "batTemperature"  # use entity for single battery systems
                elif variableName == "invBatPower_1":
                    variableName = "invBatPower"  # use entity for single battery systems
                elif variableName == "ResidualEnergy":
                    if item.get("unit") is not None:
                        scale=item["unit"]
                        if scale in ['1.0kWh', 'kWh', None]:
                            variableValue = round((variableValue * 100),2)
                            _LOGGER.debug("OA Variables ResidualEnergy Scale: *100 %s", scale)
                        elif scale=="0.1kWh":
                            variableValue = round((variableValue * 10),2)
                            _LOGGER.debug("OA Variables ResidualEnergy Scale: *10 %s", scale)
                        else:
                            _LOGGER.debug("OA Variables ResidualEnergy Scale: %s", scale)

                allData["raw"][variableName] = variableValue
                _LOGGER.debug(
                    "Var: %s, SN: %s set to %s",
                    variableName,
                    devicesn,
                    allData["raw"][variableName],
                )

                if variableName == "runningState" and (
                    "hasBattery" in allData["addressbook"]
                ):
                    hasBat = allData["addressbook"]["hasBattery"]
                    if not hasBat:
                        # solar only inverter
                        _LOGGER.debug(
                            "TestState: %s, hasBat: %s online: %s",
                            variableValue,
                            hasBat,
                            allData["online"],
                        )
                        if variableValue is not None:
                            if variableValue == "161" or variableValue == "162":
                                # waiting and solar only so set off-line flag
                                if age < 361:
                                    _LOGGER.debug(
                                        "Waiting but data less than 5 minutes old - allow sample, RunningState: %s, hasBat: %s online: %s",
                                        variableValue,
                                        hasBat,
                                        allData["online"],
                                    )
                                else:
                                    allData["online"] = False
                                    _LOGGER.debug(
                                        "Waiting so set off-line state, TestState: %s, hasBat: %s online: %s",
                                        variableValue,
                                        hasBat,
                                        allData["online"],
                                    )
                            elif variableValue == "163" and not allData["online"]:
                                # on-grid but showing off-line wait for it to be set on-line by OADeviceDetail
                                # allData["online"] = False
                                _LOGGER.debug(
                                    "Inverter on-grid but off-line wait for OADevice to confirm, TestState: %s, hasBat: %s",
                                    variableValue,
                                    hasBat,
                                )

            return False
        else:
            _LOGGER.debug("OA Device Variables Bad Response: %s", response)
            return True


class FoxESSBaseEntity(CoordinatorEntity):
    @property
    def device_info(self):
        from homeassistant.helpers.entity import DeviceInfo

        info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            name=self.coordinator.name_prefix,
            manufacturer="FoxESS",
        )
        if self.coordinator.data and "addressbook" in self.coordinator.data:
            ab = self.coordinator.data["addressbook"]
            model = ab.get("deviceType")
            if model:
                info["model"] = model
            sw = ab.get("masterVersion")
            if sw and sw != "not provided":
                info["sw_version"] = sw
        return info


class FoxESSPowerString(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSCurrent(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSFreq(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSPower(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSVolt(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSReactivePower(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.REACTIVE_POWER
    _attr_native_unit_of_measurement = UnitOfReactivePower.VOLT_AMPERE_REACTIVE

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Reactive Power")
        self._attr_name = name + " - Reactive Power"
        self._attr_unique_id = deviceID + "reactive-power"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if "ReactivePower" not in self.coordinator.data["raw"]:
                _LOGGER.debug("ReactivePower None")
            else:
                return self.coordinator.data["raw"]["ReactivePower"] * 1000
        return None


class FoxESSPowerFactor(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Power Factor")
        self._attr_name = name + " - Power Factor"
        self._attr_unique_id = deviceID + "power-factor"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if "PowerFactor" not in self.coordinator.data["raw"]:
                _LOGGER.debug("PowerFactor None")
            else:
                return self.coordinator.data["raw"]["PowerFactor"]
        return None


class FoxESSEnergyGenerated(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self._keyValue not in self.coordinator.data["reportDailyGeneration"]:
            _LOGGER.debug("%s None", self._keyValue)
        else:
            if self.coordinator.data["reportDailyGeneration"][self._keyValue] == 0:
                energygenerated = 0
            else:
                energygenerated = self.coordinator.data["reportDailyGeneration"][
                    self._keyValue
                ]
                if energygenerated > 0:
                    energygenerated = round(energygenerated, 3)
                else:
                    energygenerated = 0
            return energygenerated
        return None


class FoxESSEnergyThroughput(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Energy Throughput")
        self._attr_name = name + " - Energy Throughput"
        self._attr_unique_id = deviceID + "energy-throughput"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "energyThroughput" not in self.coordinator.data["raw"]:
            _LOGGER.debug("raw Energy Throughput None")
        else:
            if self.coordinator.data["raw"]["energyThroughput"] == 0:
                energygenerated = 0
            else:
                energygenerated = self.coordinator.data["raw"]["energyThroughput"]
                if energygenerated > 0:
                    energygenerated = round(energygenerated, 3)
                else:
                    energygenerated = 0
            return energygenerated
        return None


class FoxESSEnergyGridConsumption(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Grid Consumption")
        self._attr_name = name + " - Grid Consumption"
        self._attr_unique_id = deviceID + "grid-consumption"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "gridConsumption" not in self.coordinator.data["report"]:
            _LOGGER.debug("report gridConsumption None")
        else:
            if self.coordinator.data["report"]["gridConsumption"] == 0:
                energygrid = 0
            else:
                energygrid = self.coordinator.data["report"]["gridConsumption"]
            return energygrid
        return None


class FoxESSEnergyFeedin(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - FeedIn")
        self._attr_name = name + " - FeedIn"
        self._attr_unique_id = deviceID + "feedIn"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "feedin" not in self.coordinator.data["report"]:
            _LOGGER.debug("report feedin None")
        else:
            if self.coordinator.data["report"]["feedin"] == 0:
                energyfeedin = 0
            else:
                energyfeedin = self.coordinator.data["report"]["feedin"]
            return energyfeedin
        return None


class FoxESSEnergyBatCharge(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Bat Charge")
        self._attr_name = name + " - Bat Charge"
        self._attr_unique_id = deviceID + "bat-charge"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "chargeEnergyToTal" not in self.coordinator.data["report"]:
            _LOGGER.debug("report chargeEnergyToTal None")
        else:
            if self.coordinator.data["report"]["chargeEnergyToTal"] == 0:
                energycharge = 0
            else:
                energycharge = self.coordinator.data["report"]["chargeEnergyToTal"]
            return energycharge
        return None

class FoxESSMaxBatChargeCurrent(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Max Bat Charge Current")
        self._attr_name = name + " - Max Bat Charge Current"
        self._attr_unique_id = deviceID + "max-bat-charge-charge"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "maxChargeCurrent" not in self.coordinator.data["raw"]:
            _LOGGER.debug("report maxChargeCurrent None")
        else:
            if self.coordinator.data["raw"]["maxChargeCurrent"] == 0:
                charge = 0
            else:
                charge = self.coordinator.data["raw"]["maxChargeCurrent"]
            return charge
        return None

class FoxESSMaxBatDischargeCurrent(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Max Bat Discharge Current")
        self._attr_name = name + " - Max Bat Discharge Current"
        self._attr_unique_id = deviceID + "max-bat-discharge-charge"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "maxDischargeCurrent" not in self.coordinator.data["raw"]:
            _LOGGER.debug("report maxDischargeCurrent None")
        else:
            if self.coordinator.data["raw"]["maxDischargeCurrent"] == 0:
                charge = 0
            else:
                charge = self.coordinator.data["raw"]["maxDischargeCurrent"]
            return charge
        return None


class FoxESSEnergyBatDischarge(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Bat Discharge")
        self._attr_name = name + " - Bat Discharge"
        self._attr_unique_id = deviceID + "bat-discharge"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "dischargeEnergyToTal" not in self.coordinator.data["report"]:
            _LOGGER.debug("report dischargeEnergyToTal None")
        else:
            if self.coordinator.data["report"]["dischargeEnergyToTal"] == 0:
                energydischarge = 0
            else:
                energydischarge = self.coordinator.data["report"][
                    "dischargeEnergyToTal"
                ]
            return energydischarge
        return None


class FoxESSEnergyLoad(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Load")
        self._attr_name = name + " - Load"
        self._attr_unique_id = deviceID + "load"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "loads" not in self.coordinator.data["report"]:
            _LOGGER.debug("report loads None")
        else:
            if self.coordinator.data["report"]["loads"] == 0:
                energyload = 0
            else:
                energyload = self.coordinator.data["report"]["loads"]
            # round
            return round(energyload, 3)
        return None


class FoxESSPVEnergyTotal(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - PV Energy Total")
        self._attr_name = name + " - PVEnergyTotal"
        self._attr_unique_id = deviceID + "PVEnergyTotal"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "PVEnergyTotal" not in self.coordinator.data["report"]:
            _LOGGER.debug("report PVEnergyTotal None")
        else:
            if self.coordinator.data["report"]["PVEnergyTotal"] == 0:
                energyload = 0
            else:
                energyload = self.coordinator.data["report"]["PVEnergyTotal"]
            # round
            return round(energyload, 3)
        return None


class FoxESSInverter(FoxESSBaseEntity, SensorEntity):
    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Inverter")
        self._attr_name = name + " - Inverter"
        self._attr_unique_id = deviceID + "Inverter"
        self._attr_icon = "mdi:solar-power"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
                ATTR_DEVICE_SN,
                ATTR_PLANTNAME,
                ATTR_MODULESN,
                ATTR_DEVICE_TYPE,
                ATTR_MASTER,
                ATTR_MANAGER,
                ATTR_SLAVE,
                ATTR_BATTERYLIST,
                ATTR_LASTCLOUDSYNC,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data["online"] or (
            self.coordinator.data["online"] == False
            and int(self.coordinator.data["addressbook"]["status"]) in [1, 2, 3]
        ):
            if "status" not in self.coordinator.data["addressbook"]:
                _LOGGER.debug("addressbook status None")
            else:
                if int(self.coordinator.data["addressbook"]["status"]) == 1:
                    return "on-line"
                else:
                    if int(self.coordinator.data["addressbook"]["status"]) == 2:
                        return "in-alarm"
                    else:
                        return "off-line"
        return None

    @property
    def extra_state_attributes(self):           
        if "status" not in self.coordinator.data["addressbook"]:
            _LOGGER.debug("addressbook status attributes None")
            return None
        return {
            ATTR_DEVICE_SN: self.coordinator.data["addressbook"][ATTR_DEVICE_SN],
            ATTR_PLANTNAME: self.coordinator.data["addressbook"][ATTR_PLANTNAME],
            ATTR_MODULESN: self.coordinator.data["addressbook"][ATTR_MODULESN],
            ATTR_DEVICE_TYPE: self.coordinator.data["addressbook"][ATTR_DEVICE_TYPE],
            ATTR_MASTER: self.coordinator.data["addressbook"][ATTR_MASTER],
            ATTR_MANAGER: self.coordinator.data["addressbook"][ATTR_MANAGER],
            ATTR_SLAVE: self.coordinator.data["addressbook"][ATTR_SLAVE],
            ATTR_BATTERYLIST: self.coordinator.data["addressbook"][ATTR_BATTERYLIST],
            ATTR_LASTCLOUDSYNC: datetime.now(),
        }


class FoxESSRunningState(FoxESSBaseEntity, SensorEntity):
    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self._attr_icon = "mdi:state-machine"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                res = self.coordinator.data["raw"][self._keyValue]
                if res == "160":
                    resText = f"{res}: self-test"
                elif res == "161":
                    resText = f"{res}: waiting"
                elif res == "162":
                    resText = f"{res}: checking"
                elif res == "163":
                    resText = f"{res}: on-grid"
                elif res == "164":
                    resText = f"{res}: off-grid"
                elif res == "165":
                    resText = f"{res}: fault"
                elif res == "166":
                    resText = f"{res}: permanent-fault"
                elif res == "167":
                    resText = f"{res}: standby"
                elif res == "168":
                    resText = f"{res}: upgrading"
                elif res == "169":
                    resText = f"{res}: fct"
                elif res == "170":
                    resText = f"{res}: illegal"
                else:
                    _LOGGER.debug("runcode %s", res)
                    resText = f"{res}: unknown code"
                return resText
        return None


class FoxESSEnergySolar(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Solar")
        self._attr_name = name + " - Solar"
        self._attr_unique_id = deviceID + "solar"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if "loads" not in self.coordinator.data["report"]:
            loads = 0
        else:
            loads = float(self.coordinator.data["report"]["loads"])

        if "chargeEnergyToTal" not in self.coordinator.data["report"]:
            charge = 0
        else:
            charge = float(self.coordinator.data["report"]["chargeEnergyToTal"])

        if "feedin" not in self.coordinator.data["report"]:
            feedIn = 0
        else:
            feedIn = float(self.coordinator.data["report"]["feedin"])

        if "gridConsumption" not in self.coordinator.data["report"]:
            gridConsumption = 0
        else:
            gridConsumption = float(self.coordinator.data["report"]["gridConsumption"])

        if "dischargeEnergyToTal" not in self.coordinator.data["report"]:
            discharge = 0
        else:
            discharge = float(self.coordinator.data["report"]["dischargeEnergyToTal"])

        energysolar = round((loads + charge + feedIn - gridConsumption - discharge), 3)
        if energysolar < 0:
            energysolar = 0
        return round(energysolar, 3)


class FoxESSSolarPower(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Solar Power")
        self._attr_name = name + " - Solar Power"
        self._attr_unique_id = deviceID + "solar-power"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if "loadsPower" not in self.coordinator.data["raw"]:
            loads = 0
        else:
            loads = float(self.coordinator.data["raw"]["loadsPower"])

        if "batChargePower" not in self.coordinator.data["raw"]:
            charge = 0
        else:
            if self.coordinator.data["raw"]["batChargePower"] is None:
                charge = 0
            else:
                charge = float(self.coordinator.data["raw"]["batChargePower"])

        if "feedinPower" not in self.coordinator.data["raw"]:
            feedIn = 0
        else:
            feedIn = float(self.coordinator.data["raw"]["feedinPower"])

        if "gridConsumptionPower" not in self.coordinator.data["raw"]:
            gridConsumption = 0
        else:
            gridConsumption = float(
                self.coordinator.data["raw"]["gridConsumptionPower"]
            )

        if "batDischargePower" not in self.coordinator.data["raw"]:
            discharge = 0
        else:
            if self.coordinator.data["raw"]["batDischargePower"] is None:
                discharge = 0
            else:
                discharge = float(self.coordinator.data["raw"]["batDischargePower"])

        # check if what was returned (that some time was negative) is <0, so fix it
        total = loads + charge + feedIn - gridConsumption - discharge
        if total < 0:
            total = 0
        return round(total, 3)


class FoxESSBatSoC(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None

    @property
    def icon(self):
        return icon_for_battery_level(battery_level=self.native_value, charging=None)


class FoxESSBatMinSoC(FoxESSBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Bat MinSoC")
        self._attr_name = name + " - Bat MinSoC"
        self._attr_unique_id = deviceID + "bat-minsoc"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["battery"]:
            if "minSoc" not in self.coordinator.data["battery"]:
                _LOGGER.debug("minSoc None")
            else:
                return self.coordinator.data["battery"]["minSoc"]
        return None

    @property
    def icon(self):
        return icon_for_battery_level(battery_level=self.native_value, charging=None)


class FoxESSBatMinSoConGrid(FoxESSBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Bat minSocOnGrid")
        self._attr_name = name + " - Bat minSocOnGrid"
        self._attr_unique_id = deviceID + "bat-minSocOnGrid"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["battery"]:
            if "minSocOnGrid" not in self.coordinator.data["battery"]:
                _LOGGER.debug("minSocOnGrid None")
            else:
                return self.coordinator.data["battery"]["minSocOnGrid"]
        return None

    @property
    def icon(self):
        return icon_for_battery_level(battery_level=self.native_value, charging=None)


class FoxESSTemp(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSResidualEnergy(FoxESSBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Residual Energy")
        self._attr_name = name + " - Residual Energy"
        self._attr_unique_id = deviceID + "residual-energy"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if "ResidualEnergy" not in self.coordinator.data["raw"]:
                _LOGGER.debug("ResidualEnergy None")
            else:
                re = self.coordinator.data["raw"]["ResidualEnergy"]
                if re > 0:
                    if re > 50: # if openAPI scale is invalid (bug)
                        re = re / 100
                else:
                    re = 0
                return re
        return None


class FoxESSResponseTime(FoxESSBaseEntity, SensorEntity):
    _attr_native_unit_of_measurement = "mS"

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Response Time")
        self._attr_name = name + " - Response Time"
        self._attr_unique_id = deviceID + "response-time"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if "ResponseTime" not in self.coordinator.data["raw"]:
            _LOGGER.debug("ResponseTime None")
        else:
            return self.coordinator.data["raw"]["ResponseTime"]
        return None


class FoxESSApiCallCount(FoxESSBaseEntity, SensorEntity):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "calls"
    _attr_icon = "mdi:api"

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        self._attr_name = name + " - API Calls Today"
        self._attr_unique_id = deviceID + "api-calls-today"

    @property
    def native_value(self):
        return self.coordinator.api_calls_today

    @property
    def extra_state_attributes(self):
        return {
            "daily_limit": 1440,
            "remaining_calls": max(0, 1440 - self.coordinator.api_calls_today),
        }


class FoxESSBatteryMode(FoxESSBaseEntity, SensorEntity):
    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        self._attr_name = name + " - Battery Mode"
        self._attr_unique_id = deviceID + "battery-mode"
        self._attr_icon = "mdi:battery-sync"

    @property
    def native_value(self):
        if "workMode" in self.coordinator.data:
            return self.coordinator.data["workMode"]
        return None


class FoxESSFaultCount(FoxESSBaseEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Fault Count")
        self._attr_name = name + " - Fault Count"
        self._attr_unique_id = deviceID + "fault-count"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if "currentFaultCount" not in self.coordinator.data["raw"]:
                _LOGGER.debug("currentFaultCount None")
            else:
                return self.coordinator.data["raw"]["currentFaultCount"]
        return None


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(_build_entities(coordinator))
