# Local AlphaESS access

Research checked **22 October 2026**. This describes protocol evidence, not a commissioned replacement for the current cloud controller. No inverter writes were made for this research.

**Local access is credible through Modbus; the cloud OpenAPI cannot simply be pointed at a LAN address.** First identify the exact model/revision and demonstrate read-only access. Keep the existing controller as the only writer until the local adapter has been tested.

## What “local” means for SMILE models

AlphaESS's manufacturer-authored **Modbus RTU/TCP V2.1 manual** distinguishes hardware: original **SMILE-T10** uses its Dispatch connector, RS485 RTU, pins 3B/6A at 9600 baud. Its TCP sections instead identify SMILE5, Hi5, Hi10, G3-S3.6/B5/S5 and BAT-8.2PHA LAN interfaces. Thus a generic T10 cloud model string does not establish native TCP support; Hi10/T10-HV and original T10 must not be conflated. The manual specifies default address `0x55` (decimal **85**), TCP port **502**, holding-register reads with function **03**, command intervals greater than 100 ms TCP / 300 ms RTU and timeouts greater than 10 s. [Vendor PDF, pp. 5–13](https://alphaess.de/Public/Uploads/uploadfile/files/20230926/AlphaESS-Handbuch_SMILE_ModBus_RTU_TCP_V21.pdf); [readable mirror of the same vendor-authored manual](https://www.photovoltaikforum.com/core/file-download/252344/).

On native TCP hardware, use its documented LAN connection. On RTU-only hardware, NAS access needs an appropriate RS485 adapter or RS485-to-Ethernet gateway. A gateway's raw RTU-over-TCP framing differs from native Modbus TCP; evcc exposes both choices. Wi-Fi cloud connectivity alone is not evidence of either local interface. No documented local HTTP equivalent of AlphaESS OpenAPI was established by this review. [evcc connection options](https://docs.evcc.io/en/meters/alpha-ess-storion-smile/).

## Telemetry and control evidence

The maintained [evcc AlphaESS template source](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/alpha-ess-smile.yaml) reads these **zero-based holding-register addresses**:

| Measurement | Decimal / hex | Register count | Decode |
| --- | --- | --- | --- |
| Battery SOC | 258 / `0x0102` | 1 | unsigned 16-bit × 0.1 % |
| Battery power | 294 / `0x0126` | 1 | signed 16-bit W |
| Grid power | 33 / `0x0021` | 2 | signed 32-bit W |
| DC PV string 1 | 1055 / `0x041F` | 2 | unsigned 32-bit W |

The same adapter implements **normal self-consumption, hold and grid charge** using period-control register 2127 and charge-cutoff SOC register 2133. Hold uses an enabled charging window with a low target SOC. It does **not** demonstrate a universal battery power-setpoint interface or justify forced battery export on this installation. It requires charging windows configured first. Its prose initialization register/value list is internally inconsistent; do not copy that list into a write script. [Source](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/alpha-ess-smile.yaml), [device setup documentation](https://docs.evcc.io/en/meters/alpha-ess-storion-smile/).

An additional [direct Home Assistant integration](https://github.com/senalse/ha-alphaess-modbus) exposes local measurements, schedules and dispatch. Its [register definitions](https://github.com/senalse/ha-alphaess-modbus/blob/main/custom_components/alphaess_modbus/const.py) identify serial number at `0x064A` (15 registers), DSP version at `0x0640` (5), and ARM version at `0x0645` (5), decoded as strings. These are useful read-only identity checks, with private comparison to cloud identity. This is community implementation evidence, not proof every firmware supports dispatch or every claimed network setting exists. Its README reports single-client TCP limitations and model-specific scaling, both reasons to serialize probes and validate against this device.

## Read-only network observations

A connect-only check of the current LAN found two port-502 listeners. Standard read-device-identification positively identified one as a ventilation controller. The other did not answer a unit-85 SOC read using either native TCP or RTU-over-TCP framing with an 11-second timeout. No AlphaESS endpoint was identified, no registers were written, and the working cloud controller was retained. Private addresses and raw responses remain in ignored operational outputs.

This is a bounded discovery result, not proof that the inverter cannot support local access: the interface may require enabling, a different network/port, or a physical RS485 gateway.

## Recommended commissioning sequence

1. Locate the inverter's LAN address and check exact hardware/revision. Open port 502 alone is not identity evidence.
2. Use a single connection and a small allowlist of FC03 reads at unit 85; correlate SOC, grid/battery power and private serial with cloud data. A failed optional identity request does not establish incompatibility. Avoid broad register scans.
3. Add a read-only local provider with timestamps, stale/error reporting and validated sign/scaling. Initially compare it alongside cloud telemetry; no second writer is needed. Local Modbus and cloud access are separate transports, but simultaneous **control** would still target the same inverter settings.
4. Before switching control, verify supported windows, cutoff/reserve semantics, readback, restart/outage recovery and real measured behavior. Stop cloud actuation before enabling local writes. Do not run evcc/another controller as an additional writer merely to obtain telemetry.

This would remove the cloud dependency for proven local telemetry/control, while market prices and weather forecasts would still require their own external sources. Existing safety and single-writer rules remain applicable.
