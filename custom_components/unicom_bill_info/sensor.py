import logging
from datetime import timedelta

import async_timeout
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
# from homeassistant.const import UnitOfData # 移除此行，因为UnitOfData无法直接导入
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.core import callback # 新增此行，解决NameError

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=15)  # Default scan interval, overridden by config

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required("openid"): cv.string,
    vol.Optional("name", default="联通数据"): cv.string,
    vol.Optional("refresh_interval", default=15):
        vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
})

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the China Unicom Data sensor from a config entry."""
    openid = config_entry.data["openid"]
    name = config_entry.data["name"]
    refresh_interval = config_entry.data["refresh_interval"]
    create_individual_sensors = config_entry.data.get("create_individual_sensors", False) # 获取配置
    scan_interval_td = timedelta(minutes=refresh_interval)
    domain = config_entry.domain # 获取集成域名

    session = async_get_clientsession(hass)

    coordinator = ChinaUnicomDataUpdateCoordinator(
        hass,
        session,
        openid,
        _LOGGER,
        scan_interval_td,
        domain 
    )

    await coordinator.async_config_entry_first_refresh()

    entities = [
        ChinaUnicomDataSensor(coordinator, name, "voice"),
        ChinaUnicomDataSensor(coordinator, name, "sms"),
        ChinaUnicomDataSensor(coordinator, name, "data"),
        ChinaUnicomBalanceSensor(coordinator, name), # 主余额传感器
    ]

    if create_individual_sensors:
        _LOGGER.debug("Creating individual sensors for %s", name)
        entities.extend([
            # 语音独立实体
            ChinaUnicomVoiceTotalSensor(coordinator, name),
            ChinaUnicomVoiceAvailableSensor(coordinator, name),
            ChinaUnicomVoiceUsageRatioSensor(coordinator, name),
            # 短信独立实体
            ChinaUnicomSmsTotalSensor(coordinator, name),
            ChinaUnicomSmsAvailableSensor(coordinator, name),
            # 流量独立实体
            ChinaUnicomDataTotalSensor(coordinator, name),
            ChinaUnicomDataAvailableSensor(coordinator, name),
            ChinaUnicomDataExceedSensor(coordinator, name),
            ChinaUnicomDataUsageRatioSensor(coordinator, name), 
            # 账户余额独立实体
            ChinaUnicomTotalOwedSensor(coordinator, name),
            ChinaUnicomCreditValueSensor(coordinator, name),
            ChinaUnicomRealFeeNewSensor(coordinator, name),
            ChinaUnicomCanUserValueSensor(coordinator, name),
        ])

    async_add_entities(entities)


class ChinaUnicomDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching China Unicom Data."""

    def __init__(self, hass, session, openid, logger, update_interval, domain):
        """Initialize."""
        self.openid = openid
        self.session = session
        self.headers = {'Content-Type': 'application/json'}
        self._domain = domain
        super().__init__(
            hass,
            logger,
            name="China Unicom Data",
            update_interval=update_interval,
        )

    @property
    def domain(self):
        """Return the domain of the integration."""
        return self._domain

    async def _async_update_data(self):
        """Fetch data from API endpoint."""
        payload = {
            "openid": self.openid,
            "channel": "wxmini"
        }

        try:
            async with async_timeout.timeout(10):
                # Request 1: sspbigball (Voice, SMS, Data usage)
                voice_sms_data_response = await self.session.post(
                    "https://mina.10010.com/wxapplet/weixinNew/sspbigball",
                    json=payload,
                    headers=self.headers
                )
                voice_sms_data = await voice_sms_data_response.json()
                if voice_sms_data.get("code") != "0000":
                    raise UpdateFailed(f"Error fetching voice/sms/data: {voice_sms_data}")

                # Request 2: sspbalcbroadcast (Balance)
                balance_response = await self.session.post(
                    "https://mina.10010.com/wxapplet/weixinNew/sspbalcbroadcast",
                    json=payload,
                    headers=self.headers
                )
                balance_data = await balance_response.json()
                if balance_data.get("code") != "0000":
                    raise UpdateFailed(f"Error fetching balance: {balance_data}")

                return {
                    "voice_sms_data": voice_sms_data["data"],
                    "balance_data": balance_data["data"][0] # Assuming only one item in balance data array
                }

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")


class ChinaUnicomDataSensor(SensorEntity):
    """Representation of a China Unicom Data sensor."""

    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str, sensor_type: str):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self._base_name = base_name
        self._sensor_type = sensor_type
        self._state = None
        self._unit_of_measurement = None
        self._attributes = {}

    @property
    def name(self):
        """Return the name of the sensor."""
        if self._sensor_type == "voice":
            return f"{self._base_name} 语音用量"
        elif self._sensor_type == "sms":
            return f"{self._base_name} 短信用量"
        elif self._sensor_type == "data":
            return f"{self._base_name} 流量用量"
        return f"{self._base_name} 未知"

    @property
    def unique_id(self):
        """Return a unique ID to use for this sensor."""
        return f"china_unicom_{self.coordinator.openid}_{self._sensor_type}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._attributes

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll. Coordinator polls and pushes updates."""
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        """Handle updated data from the coordinator."""
        data = self.coordinator.data["voice_sms_data"]
        updated = False

        if self._sensor_type == "voice":
            for item in data:
                if item.get("SOURCE_TYPE") == "1" and item.get("SPECIAL_TYPE") == "1":
                    self._state = item.get("X_USED_VALUE")
                    self._unit_of_measurement = "" # "分钟" is already in value
                    self._attributes = {
                        "已用": item.get("X_USED_VALUE"),
                        "总量": item.get("ADDUP_UPPER"),
                        "超出": item.get("X_EXCEED_VALUE"),
                        "可用": item.get("X_CANUSE_VALUE"),
                        # 将属性里的使用比例格式化为百分比字符串
                        "使用比例": f"{float(item.get('USED_RATIO', 0)) :.2f}%", 
                    }
                    updated = True
                    break
        elif self._sensor_type == "sms":
            for item in data:
                if item.get("SOURCE_TYPE") == "2" and item.get("SPECIAL_TYPE") == "1":
                    self._state = item.get("X_USED_VALUE")
                    self._unit_of_measurement = "" # "条" is already in value
                    self._attributes = {
                        "已用": item.get("X_USED_VALUE"),
                        "总量": item.get("ADDUP_UPPER"),
                        "超出": item.get("X_EXCEED_VALUE"),
                        "可用": item.get("X_CANUSE_VALUE"),
                        # 将属性里的使用比例格式化为百分比字符串
                        "使用比例": f"{float(item.get('USED_RATIO', 0)) :.2f}%", 
                    }
                    updated = True
                    break
        elif self._sensor_type == "data":
            for item in data:
                if item.get("SOURCE_TYPE") == "3" and item.get("SPECIAL_TYPE") == "0":
                    used_value_str = item.get("X_USED_VALUE", "0.00MB")
                    # Extract numeric value and unit
                    try:
                        value = float(used_value_str.replace('MB', '').replace('GB', ''))
                        unit = used_value_str[-2:] # MB or GB
                    except ValueError:
                        value = 0.0
                        unit = "MB" # Default if parsing fails

                    if unit == "MB":
                        if value >= 1024:
                            self._state = round(value / 1024, 2)
                            self._unit_of_measurement = "GB"
                        else:
                            self._state = value
                            self._unit_of_measurement = "MB"
                    elif unit == "GB":
                        self._state = value
                        self._unit_of_measurement = "GB"

                    # Handle ADDUP_UPPER for data (might be '0.00MB' if unlimited or not tracked this way)
                    addup_upper_str = item.get("ADDUP_UPPER", "0.00MB")
                    canuse_value_str = item.get("X_CANUSE_VALUE", "0.00MB")

                    total_data_mb = self._convert_to_mb(addup_upper_str)
                    available_data_mb = self._convert_to_mb(canuse_value_str)

                    self._attributes = {
                        "已用": f"{self._state} {self._unit_of_measurement}",
                        "总量": f"{self._format_bytes(total_data_mb)}",
                        "可用": f"{self._format_bytes(available_data_mb)}",
                        "超出": item.get("X_EXCEED_VALUE"),
                        # 将属性里的使用比例格式化为百分比字符串
                        "使用比例": f"{float(item.get('USED_RATIO', -1)) :.2f}%" if item.get("USED_RATIO") != "-1" else "N/A"
                    }
                    updated = True
                    break

        if updated:
            self.async_write_ha_state()


    def _convert_to_mb(self, value_str):
        """Converts a string like '46.98MB' or '1.5GB' to MB."""
        if 'MB' in value_str:
            return float(value_str.replace('MB', ''))
        elif 'GB' in value_str:
            return float(value_str.replace('GB', '')) * 1024
        return 0.0

    def _format_bytes(self, mb_value):
        """Formats MB value to MB or GB with appropriate unit."""
        if mb_value >= 1024:
            return f"{round(mb_value / 1024, 2)}GB"
        return f"{round(mb_value, 2)}MB"


class ChinaUnicomBalanceSensor(SensorEntity):
    """Representation of a China Unicom Balance sensor."""

    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "元" # CNY
        self._attributes = {}

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._base_name} 余额"

    @property
    def unique_id(self):
        """Return a unique ID to use for this sensor."""
        return f"china_unicom_{self.coordinator.openid}_balance"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._attributes

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll. Coordinator polls and pushes updates."""
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        """Handle updated data from the coordinator."""
        data = self.coordinator.data["balance_data"]

        self._state = data.get("CANUSE_FEE_CUST")
        self._attributes = {
            "当前余额": data.get("CURNT_BALANCE_CUST"),
            "可用余额": data.get("FEE_AVAILABLE"),
            "总欠费": data.get("ALLBOWE_FEE_CUST"),
            "实时话费": data.get("REAL_FEE_CUST_NEW"),
            "信用额度": data.get("CREDIT_VALUE"),
            "可用赠款": data.get("CAN_USER_VALUE"),
        }
        self.async_write_ha_state()

# 新增的独立传感器类（语音总量和可用）
class ChinaUnicomVoiceTotalSensor(SensorEntity):
    """Representation of China Unicom Voice Total sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "分钟"

    @property
    def name(self):
        return f"{self._base_name} 语音总量"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_voice_total"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "1" and item.get("SPECIAL_TYPE") == "1":
                # 尝试从字符串中提取数字并转换为浮点数
                try:
                    self._state = float(item.get("ADDUP_UPPER", "0分钟").replace('分钟', '').strip())
                except ValueError:
                    self._state = None # 或者设置为0，取决于您希望如何处理无效数据
                self.async_write_ha_state()
                break

class ChinaUnicomVoiceAvailableSensor(SensorEntity):
    """Representation of China Unicom Voice Available sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "分钟"

    @property
    def name(self):
        return f"{self._base_name} 语音可用"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_voice_available"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "1" and item.get("SPECIAL_TYPE") == "1":
                # 尝试从字符串中提取数字并转换为浮点数
                try:
                    self._state = float(item.get("X_CANUSE_VALUE", "0分钟").replace('分钟', '').strip())
                except ValueError:
                    self._state = None # 或者设置为0，取决于您希望如何处理无效数据
                self.async_write_ha_state()
                break

class ChinaUnicomVoiceUsageRatioSensor(SensorEntity):
    """Representation of China Unicom Voice Usage Ratio sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "%"

    @property
    def name(self):
        return f"{self._base_name} 语音使用比例"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_voice_usage_ratio"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "1" and item.get("SPECIAL_TYPE") == "1":
                try:
                    # 将比例值（0-1范围）乘以100作为状态值，Home Assistant会处理显示为百分比
                    self._state = round(float(item.get("USED_RATIO", 0)) , 2) # 修改此处
                except ValueError:
                    self._state = None
                self.async_write_ha_state()
                break

# 新增的短信独立传感器类
class ChinaUnicomSmsTotalSensor(SensorEntity):
    """Representation of China Unicom SMS Total sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "条"

    @property
    def name(self):
        return f"{self._base_name} 短信总量"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_sms_total"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "2" and item.get("SPECIAL_TYPE") == "1":
                try:
                    self._state = float(item.get("ADDUP_UPPER", "0条").replace('条', '').strip())
                except ValueError:
                    self._state = None
                self.async_write_ha_state()
                break

class ChinaUnicomSmsAvailableSensor(SensorEntity):
    """Representation of China Unicom SMS Available sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "条"

    @property
    def name(self):
        return f"{self._base_name} 短信可用"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_sms_available"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "2" and item.get("SPECIAL_TYPE") == "1":
                try:
                    self._state = float(item.get("X_CANUSE_VALUE", "0条").replace('条', '').strip())
                except ValueError:
                    self._state = None
                self.async_write_ha_state()
                break

# 新增的流量独立传感器类
class ChinaUnicomDataUsedSensor(SensorEntity):
    """Representation of China Unicom Data Used sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = None # Will be MB or GB

    @property
    def name(self):
        return f"{self._base_name} 流量已用"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_data_used"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "3" and item.get("SPECIAL_TYPE") == "0":
                used_value_str = item.get("X_USED_VALUE", "0.00MB")
                try:
                    value = float(used_value_str.replace('MB', '').replace('GB', ''))
                    unit = used_value_str[-2:]
                except ValueError:
                    value = 0.0
                    unit = "MB"

                if unit == "MB":
                    if value >= 1024:
                        self._state = round(value / 1024, 2)
                        self._unit_of_measurement = "GB"
                    else:
                        self._state = value
                        self._unit_of_measurement = "MB"
                elif unit == "GB":
                    self._state = value
                    self._unit_of_measurement = "GB"
                self.async_write_ha_state()
                break

class ChinaUnicomDataTotalSensor(SensorEntity):
    """Representation of China Unicom Data Total sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = None

    @property
    def name(self):
        return f"{self._base_name} 流量总量"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_data_total"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "3" and item.get("SPECIAL_TYPE") == "0":
                addup_upper_str = item.get("ADDUP_UPPER", "0.00MB")
                mb_value = self._convert_to_mb(addup_upper_str)
                if mb_value >= 1024:
                    self._state = round(mb_value / 1024, 2)
                    self._unit_of_measurement = "GB"
                else:
                    self._state = round(mb_value, 2)
                    self._unit_of_measurement = "MB"
                self.async_write_ha_state()
                break
    
    def _convert_to_mb(self, value_str):
        if 'MB' in value_str:
            return float(value_str.replace('MB', ''))
        elif 'GB' in value_str:
            return float(value_str.replace('GB', '')) * 1024
        return 0.0

class ChinaUnicomDataAvailableSensor(SensorEntity):
    """Representation of China Unicom Data Available sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = None

    @property
    def name(self):
        return f"{self._base_name} 流量可用"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_data_available"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "3" and item.get("SPECIAL_TYPE") == "0":
                canuse_value_str = item.get("X_CANUSE_VALUE", "0.00MB")
                mb_value = self._convert_to_mb(canuse_value_str)
                if mb_value >= 1024:
                    self._state = round(mb_value / 1024, 2)
                    self._unit_of_measurement = "GB"
                else:
                    self._state = round(mb_value, 2)
                    self._unit_of_measurement = "MB"
                self.async_write_ha_state()
                break
    
    def _convert_to_mb(self, value_str):
        if 'MB' in value_str:
            return float(value_str.replace('MB', ''))
        elif 'GB' in value_str:
            return float(value_str.replace('GB', '')) * 1024
        return 0.0

class ChinaUnicomDataExceedSensor(SensorEntity):
    """Representation of China Unicom Data Exceed sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = None # Will be MB or GB

    @property
    def name(self):
        return f"{self._base_name} 流量超出"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_data_exceed"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "3" and item.get("SPECIAL_TYPE") == "0":
                exceed_value_str = item.get("X_EXCEED_VALUE", "0.00MB")
                mb_value = self._convert_to_mb(exceed_value_str)
                if mb_value >= 1024:
                    self._state = round(mb_value / 1024, 2)
                    self._unit_of_measurement = "GB"
                else:
                    self._state = round(mb_value, 2)
                    self._unit_of_measurement = "MB"
                self.async_write_ha_state()
                break

    def _convert_to_mb(self, value_str):
        if 'MB' in value_str:
            return float(value_str.replace('MB', ''))
        elif 'GB' in value_str:
            return float(value_str.replace('GB', '')) * 1024
        return 0.0

class ChinaUnicomDataUsageRatioSensor(SensorEntity):
    """Representation of China Unicom Data Usage Ratio sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "%"

    @property
    def name(self):
        return f"{self._base_name} 流量使用比例"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_data_usage_ratio"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["voice_sms_data"]
        for item in data:
            if item.get("SOURCE_TYPE") == "3" and item.get("SPECIAL_TYPE") == "0":
                try:
                    # 将比例值（0-1范围）乘以100作为状态值，Home Assistant会处理显示为百分比
                    self._state = round(float(item.get("USED_RATIO", -1)) * 100, 2) if item.get("USED_RATIO") != "-1" else None # 修改此处
                except ValueError:
                    self._state = None
                self.async_write_ha_state()
                break

# === 账户余额独立传感器类 ===

class ChinaUnicomCurrentBalanceSensor(SensorEntity):
    """Representation of China Unicom Current Balance sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "元"

    @property
    def name(self):
        return f"{self._base_name} 当前余额"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_current_balance"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["balance_data"]
        try:
            self._state = float(data.get("CURNT_BALANCE_CUST", "0.00"))
        except ValueError:
            self._state = None
        self.async_write_ha_state()

class ChinaUnicomTotalOwedSensor(SensorEntity):
    """Representation of China Unicom Total Owed Fee sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "元"

    @property
    def name(self):
        return f"{self._base_name} 总欠费"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_total_owed"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["balance_data"]
        try:
            self._state = float(data.get("ALLBOWE_FEE_CUST", "0.00"))
        except ValueError:
            self._state = None
        self.async_write_ha_state()

class ChinaUnicomCreditValueSensor(SensorEntity):
    """Representation of China Unicom Credit Value sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "元" 

    @property
    def name(self):
        return f"{self._base_name} 信用额度"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_credit_value"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["balance_data"]
        try:
            self._state = float(data.get("CREDIT_VALUE", "0.00"))
        except ValueError:
            self._state = None
        self.async_write_ha_state()

class ChinaUnicomRealFeeNewSensor(SensorEntity):
    """Representation of China Unicom New Real Fee sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "元"

    @property
    def name(self):
        return f"{self._base_name} 实时话费"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_real_fee_new"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["balance_data"]
        try:
            self._state = float(data.get("REAL_FEE_CUST_NEW", "0.00"))
        except ValueError:
            self._state = None
        self.async_write_ha_state()

class ChinaUnicomCanUserValueSensor(SensorEntity):
    """Representation of China Unicom Can User Value (Available Grants) sensor."""
    def __init__(self, coordinator: ChinaUnicomDataUpdateCoordinator, base_name: str):
        self.coordinator = coordinator
        self._base_name = base_name
        self._state = None
        self._unit_of_measurement = "元"

    @property
    def name(self):
        return f"{self._base_name} 可用赠款"

    @property
    def unique_id(self):
        return f"china_unicom_{self.coordinator.openid}_can_user_value"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(self.coordinator.domain, self.coordinator.openid)},
            name=self._base_name,
            manufacturer="China Unicom",
        )

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data["balance_data"]
        try:
            self._state = float(data.get("CAN_USER_VALUE", "0.00"))
        except ValueError:
            self._state = None
        self.async_write_ha_state()