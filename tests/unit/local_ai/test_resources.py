"""Battery readings: macOS's ioreg output and the energy a run used."""

import plistlib

from scripts.local_ai.benchmark import _battery
from scripts.local_ai.resources import BatterySample, mac_battery


def _ioreg(**battery: object) -> bytes:
    return plistlib.dumps([battery])


def test_apple_silicon_keeps_the_raw_charge_in_battery_data() -> None:
    plist = _ioreg(
        Voltage=12247,
        ExternalConnected=False,
        CurrentCapacity=86,  # A percentage on Apple silicon, never the charge.
        BatteryData={"AppleRawCurrentCapacity": 3272},
    )
    assert mac_battery(plist) == BatterySample(40072, False, 3272, 12247)


def test_intel_macs_report_the_raw_charge_at_the_top() -> None:
    plist = _ioreg(AppleRawCurrentCapacity=5000, Voltage=11400, ExternalConnected=True)
    assert mac_battery(plist) == BatterySample(57000, True, 5000, 11400)


def test_no_battery_or_unreadable_output_is_no_reading() -> None:
    assert mac_battery(plistlib.dumps([])) is None
    assert mac_battery(b"not a plist") is None
    assert mac_battery(b"<?xml version='1.0'?><plist><array><dict>") is None
    assert mac_battery(_ioreg(Voltage=12000, ExternalConnected=False)) is None
    assert mac_battery(_ioreg(AppleRawCurrentCapacity=3000, Voltage="12000")) is None


def test_charge_used_is_counted_at_the_average_voltage() -> None:
    before = BatterySample(40072, False, 3272, 12247)
    after = BatterySample(38880, False, 3200, 12150)
    # 72 mAh at 12.1985 V, not the 1,192 mWh the products differ by.
    assert _battery(before, after) == {
        "before": {
            "remaining_mwh": 40072,
            "on_ac_power": False,
            "remaining_mah": 3272,
            "voltage_mv": 12247,
        },
        "after": {
            "remaining_mwh": 38880,
            "on_ac_power": False,
            "remaining_mah": 3200,
            "voltage_mv": 12150,
        },
        "used_mwh": 878,
    }


def test_windows_energy_readings_are_subtracted() -> None:
    battery = _battery(BatterySample(50000, False), BatterySample(47500, False))
    assert isinstance(battery, dict)
    assert battery["used_mwh"] == 2500


def test_a_run_on_mains_power_reports_no_energy() -> None:
    battery = _battery(BatterySample(50000, True), BatterySample(49000, False))
    assert isinstance(battery, dict)
    assert battery["used_mwh"] is None
