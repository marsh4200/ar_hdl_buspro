<p align="center">
  <img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/hdl_ha_logo_bounce.png" width="600" alt="AR HDL BUSPRO: HDL Buspro for Home Assistant" />
</p>

<h1 align="center">HDL Buspro &amp; Wireless Buspro for Home Assistant</h1>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=marsh4200&repository=ar_hdl_buspro&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Add to HACS"></a>
</p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/marsh4200/ar_hdl_buspro/releases"><img src="https://img.shields.io/github/v/release/marsh4200/ar_hdl_buspro" alt="GitHub release"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2024.4%2B-18BCF2.svg" alt="Home Assistant 2024.4+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-proprietary-8C00AA.svg" alt="License"></a>
</p>

<p align="center">
  <b>Point it at your gateway, press <i>Scan bus</i>, tick what you want. Done.</b><br>
  Lights, relays, curtains, floor heating, AC, sensors, dry contacts and wireless panels, all set up from the UI.<br>
  No YAML. No hunting for addresses.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#set-up-in-7-steps">Set up</a> ·
  <a href="#features">Features</a> ·
  <a href="#reference">Reference</a> ·
  <a href="#troubleshooting">Troubleshooting</a> ·
  <a href="#licensing">Licensing</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/overview-dashboard.png" width="860" alt="Lights, relays, curtains, floor heating, AC and sensors from an HDL Buspro bus in Home Assistant">
</p>

---

## Why AR HDL BUSPRO

| Feature | What it does |
|---|---|
| 🔍 **One scan finds everything** | Every device on the bus is found, named and classified for you. Self-describing hardware reports its own type and channel layout; everything else is matched against a built-in catalogue of **~1,800 HDL device types** |
| 📡 **Finds your gateway** | A broadcast probe on UDP/6000 lists every HDL gateway on the wire, even across IP subnets on the same switch |
| ✂️ **One entity per channel** | A 12-channel relay becomes 12 switches, a 6-channel dimmer 6 lights, a 2-curtain module 2 covers |
| 💡 **Keypad LEDs stay in step** | Button LEDs follow their relays, even when the relay is switched from Home Assistant |
| 🔘 **Spare buttons become triggers** | Point any free keypad button at Home Assistant and use it in automations |
| 🛡️ **Ignores the neighbours** | Only telegrams from *your* gateway are processed, so other HDL systems on a shared network never leak in |
| 🔁 **Self-healing** | Automatic reconnect with backoff; quiet devices aren't polled forever |

Part of the **1PM-HDL** suite · [1pm.co.za](https://www.1pm.co.za/)

<details>
<summary><b>🆕 What's new in 5.0.11</b></summary>

- **Automatic device identification.** Supported HDL hardware now describes itself during the bus scan (what it is and how many relays, dimmers, curtains or inputs it has), and a built-in catalogue of ~1,800 HDL device types names and classifies everything else. Silent relay and dimmer modules import all their channels, not just channel 1.
- **Keypad buttons in Home Assistant.** A spare keypad button (one with no relay behind it) can be programmed to talk to Home Assistant, appearing as an on/off switch and firing an event on every press. See [Keypad buttons in Home Assistant](#keypad-buttons-in-home-assistant).
- **Keypad LED sync.** Keypad button LEDs now follow the relays they control, even when the relay is switched from Home Assistant. Works for wired keypads and Buspro wireless panels. See [Keypad LED sync](#keypad-led-sync).
- **Humidity on touch panels.** Granite Display and 4" touch panels now report humidity as well as temperature, and both sensors are added automatically when the panel is imported.
- **Wall panels import their sensors and climate.** DLP, Enviro, Granite and "with temperature" panels list their temperature sensor, floor heating and AC as separate lines in the scan, instead of being skipped as keypads.
- **HDL-MRCU home control unit** imports its 4 dimmer channels as dimmable lights automatically.
- **Wireless Buspro panels** import their built-in relays, and the 1-relay and 3-relay versions of `0x13C2` are told apart when the panel reports its relay count.
- **Corrected device types.** `0x0011` is a 6-channel 0-10V dimmer, `0x0141` a 12-in-1 sensor, `0x1209` / `0x120B` IP gateways and `0x0457` a logic module. Gateways and logic modules are labelled and left out of import.
- **Less bus traffic.** Devices that never answer a status request are no longer asked every 20 seconds forever: they get a few quick retries, then a slow background check.

Sorry to anyone who had devices come in as the wrong type on earlier versions. See [Upgrading](#upgrading) for how to bring an existing install up to date.

</details>

## Supported hardware

| Platform | HDL hardware | Notes |
|---|---|---|
| **Light** | Dimmer modules (MDT0601, MD0602, MRDA06 0-10V, …), DALI controllers (MC48-DALI, MC64-DALI, MC64-DALI2, AC64-DALI), MRCU dimmer channels, relay channels | Dimmable or on/off, ramp time, keypad LED sync, DALI groups |
| **Switch** | Relay modules (MR0410, MR0816, MR1210, MR1616, …), MRCU relays, Buspro wireless panels with built-in relays | One entity per relay channel, keypad LED sync |
| **Cover** | Curtain modules (MW02 / MWM / MVSM families) **and** relay-pair curtains | Open / close / stop plus position: real on HDL curtain motors, estimated elsewhere |
| **Climate** | Floor heating (DLP panels), AC via an IR module, AC via a touch panel | See [Air conditioning](#air-conditioning) |
| **Sensor** | 12-in-1, 8-in-1, 7-in-1 / MSP07M, temperature sensors, touch-panel temperature and humidity | Broadcast plus optional polling |
| **Binary sensor** | Motion, dry-contact zones, universal switches, channel status | One entity per dry-contact zone |
| **Universal switch** | IR modules (HDL-MIRC04.40) and any universal-switch number | Virtual on/off flags for scenes and logic |
| **Keypad button** | Any spare keypad button sending a universal switch to Home Assistant | On/off switch + press event |

---

## Install

**HACS (recommended)**

1. HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/marsh4200/ar_hdl_buspro` as an **Integration**
3. Install **AR HDL BUSPRO**, then restart Home Assistant

Or use the **Add to HACS** button at the top of this page.

**Manual:** copy `custom_components/ar_hdl_buspro` into your `config/custom_components/` folder and restart Home Assistant.

## Set up in 7 steps

From nothing to a working system in about five minutes.

### 1. Add the hub

**Settings → Devices & Services → Add Integration → AR HDL BUSPRO**, then **Add hub**. There's nothing to fill in; the next screen finds the gateway for you.

<p align="center"><img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/setup-1-add-hub.png" width="760" alt="AR HDL BUSPRO integration page with the Add hub button"></p>

### 2. Pick your gateway

Every HDL gateway that answers on UDP/6000 is listed with the number of bus devices it heard, so on a site with several gateways the busy one is obvious. Use **Scan again** if the gateway was still booting, or **Enter address manually** if broadcasts are blocked.

On the next screen the port is normally `6000`, and *Local IP* stays blank unless you need to bind to a specific interface.

<p align="center"><img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/setup-2-gateway.png" width="520" alt="Gateway picker listing detected HDL Buspro gateways with bus device counts"></p>

### 3. Open Configure

The hub appears straight away under **Hubs**. Click the ⚙️ **gear**: everything else lives there.

<p align="center"><img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/setup-3-hub.png" width="760" alt="AR HDL BUSPRO hub entry with the configure gear icon"></p>

### 4. Choose *Scan bus for devices*

<table>
<tr>
<td width="50%"><img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/setup-4-menu.png" alt="AR HDL BUSPRO configuration menu"></td>
<td>

| Option | What it's for |
|---|---|
| **Licence** | Server ID, licence status and key |
| **Gateway settings** | Change host, port or local IP |
| **Detect gateway on the network** | Re-run the probe (new DHCP lease, moved VLAN) |
| **Scan bus for devices** | ⬅️ **Start here** |
| **Add a device** | Manual entry for anything the scan can't reach |
| **Edit a device** | Rename, channel, dimmable, curtain, keypad LEDs, presets… |
| **Remove devices** | Tick and remove several at once (hardware untouched) |

</td>
</tr>
</table>

### 5. Pick how long to listen

<p align="center"><img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/setup-5-duration.png" width="520" alt="Bus scan listen duration slider"></p>

| Duration | When to use it |
|---|---|
| **10–15 s** | Quick re-scan after adding a module or two (default) |
| **30 s** | First scan on a normal house |
| **45–60 s** | Large sites. Press a few keypad buttons and dim some lights while it runs |

Longer is never wrong, it just costs you the wait.

### 6. Let it work

A live countdown runs while the bus is listened to. At `0s` it switches to **confirming**: every device found is asked directly for its channel count, identity and keypad programming. That takes a few extra seconds and is time-capped on very large buses.

<p align="center"><img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/setup-6-progress.png" width="780" alt="Bus scan countdown, then confirming channel counts"></p>

### 7. Tick and import

<p align="center"><img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/setup-7-results.png" width="660" alt="Scan results with type, HDL type code, channels and model for each device"></p>

Each line shows the **bus address** (subnet.device), **type**, **HDL type code**, **channels** and **model**. `✓ in config` means it's already set up; re-importing only fills gaps.

- **Wall panels** (DLP, Granite, Enviro) get one extra line per feature underneath: tick the temperature sensor, floor heating or AC that's actually installed.
- **Gateways, logic modules and plain keypads** are labelled and import nothing.
- **Split channels** (on by default) turns each channel into its own entity so you can name every load.
- **Dimmer type codes**: if a dimmer came in as a switch, put its code here (e.g. `0x0269`). It's remembered for good.

Submit, and your HDL system is in Home Assistant. Nothing is overwritten or deleted, so you can re-scan any time.

---

## Features

### Finding your gateway

You never need to know the gateway's IP up front. HDL gateways broadcast on UDP `6000` to `255.255.255.255`, which crosses IP subnets as long as the devices share an L2 switch.

- **During setup**, a broadcast probe lists every gateway that answers.
- **Later**, *Configure → Detect gateway on the network* runs the same probe. Handy after a new DHCP lease, a VLAN move, or on a site you didn't install.

> **Several HDL systems on one network?** Detection lists them all. Once you pick one, the source-IP filter ignores the others, so neighbouring installations never leak into your entities.

### The bus scan

No need to walk the site writing down subnet, device and channel numbers: the integration interrogates the bus for you. The scan runs in four phases, each time-capped:

| Phase | What happens |
|---|---|
| **1. Broadcast discovery** | Every 2.5 s a round of read requests covers every device class (channel status, sensors, motion, panel temperature, floor heating, dry contacts, universal switches, curtains, device name, and HDL's "read device module" `0xE548`). All other traffic in the window is harvested too |
| **2. Directed follow-up** | Most relay and dimmer modules only answer a channel-status read sent to their own address, so each device found is asked for its channel count |
| **3. Identification** | Anything that hasn't described itself is asked what it is, and its name (the device's own remark) is read so entities carry real names |
| **4. Keypad programming** | Keypads and panels are asked what each button does, so relays can be linked for [Keypad LED sync](#keypad-led-sync) |

<details>
<summary><b>How a device is identified</b></summary>

<br>

Each device is classified using the most reliable information available, in this order:

| Priority | Source | Example |
|---|---|---|
| 1 | **Confirmed codes** pinned in the integration (from real installations) | `0x01BD` → 8-channel relay |
| 2 | **The device's own description** (its reply to `0xE548`) | "2 buttons + 1 relay" |
| 3 | **HDL device catalogue** (~1,800 type codes: model, family, channel layout) | `0x01C0` → HDL-MR0810, 8 relays |
| 4 | **What the device answered** during the scan | Answered a curtain read → cover |

A device that answers a channel-status read is never overruled by the catalogue, so real hardware always wins over a lookup. Panels follow what they answer: one that reports floor heating becomes a climate device, one that reports an onboard temperature becomes a sensor.

| The device answered… | Classified as |
|---|---|
| Sensor / sensors-in-one status | Sensor (temperature + lux + motion, humidity where fitted) |
| Floor-heating status | Climate |
| Panel temperature only | Sensor (temperature, plus humidity on touch panels) |
| Dry-contact status | Binary sensor (one per zone) |
| Curtain status | Cover |
| Channel status, with dimmer evidence or a dimmer type code | Light |
| Channel status, otherwise | Switch |
| Only ever *sent* commands | Keypad (labelled, not imported) |

Unknown hardware falls back to a switch on channel 1: never lost, always editable afterwards.

</details>

<details>
<summary><b>Reading the results list</b></summary>

<br>

```
1.13  switch  ·  0x01C1  ·  12ch  ·  Relay module                          ✓ in config
1.21  light   ·  0x026D  ·  6ch   ·  Dimmer module (6ch)
1.37  switch  ·  0x0DCE  ·  18 relay + 4 dimmer  ·  Home control unit (HDL-MRCU)
2.1   switch  ·  "Lounge"  ·  0x13C3  ·  3ch  ·  Wireless relay panel (3 relays)
2.51  cover   ·  0x02C9  ·  2ch   ·  Curtain module (2ch, HDL-MW02.431)
2.60  keypad  ·  0x00AF  ·  Wall keypad  ·  buttons only, no entities
1.38  gateway ·  0x1209  ·  IP gateway (HDL-MBUS01IP.431)  ·  no entities
```

Left to right: **bus address** (subnet.device), **classified type**, the device's **name** if it has one, **HDL type code**, **channels**, and the **model**. Where a device described itself, its own function list is shown in brackets, e.g. `[2 button + 1 relay]`.

**Wall panels with more than buttons** (DLP, Enviro, Granite Display, "with temperature" panels…) get one extra line per feature:

```
2.60  keypad  ·  "Lounge DLP" · 0x00AF · Wall keypad  ·  buttons — tick its features below
2.60  temperature sensor  ·  Lounge DLP
2.60  floor heating       ·  Lounge DLP
2.60  air conditioner     ·  Lounge DLP
```

Each feature imports as its own entity and shows `✓ in config` once it's in.

</details>

<details>
<summary><b>Split channels, dimmers, keypads and re-scanning</b></summary>

<br>

**Split channels** (on by default) turns a `12ch` relay into 12 switches named `HDL 1.13 ch1` … `HDL 1.13 ch12`, ready to rename. It applies to lights, switches and covers. Mixed modules come in per channel with the right type each: an HDL-MRCU imports channels 1–17 and 22 as switches and 18–21 as dimmable lights. Turn it off to import a single entity per physical module.

**Dimmer imported as a switch?** Most dimmers are recognised from the catalogue. If one still lands as a switch, copy its type code from the results (e.g. `0x0269`) into **Dimmer type codes** (comma-separated for several: `0x0269, 0x0602`) and import. The codes are remembered permanently for every future scan on this entry.

**Plain keypads** are labelled `buttons only, no entities`. Their presses act on the loads you *did* import, and their LEDs are kept in step by [Keypad LED sync](#keypad-led-sync). **Buspro wireless panels** are the exception: they have relays built in, so they import as switches. **Gateways and logic modules** are labelled `no entities`.

**Re-scanning is always safe.** Import never deletes or overwrites a device. Existing (subnet, device, channel) combinations are skipped, and each scan refreshes keypad LED links for the keypads it could read.

</details>

### Keypad LED sync

An HDL keypad's button LED shows the button's own state, not the relay's. Switch the relay from Home Assistant, another keypad or HDL logic, and the LED goes out of step. AR HDL BUSPRO fixes that automatically:

- **The bus scan reads each keypad's button programming** (wired keypads and wireless panels) and links every button that switches a **single** relay or dimmer channel to that entity.
- **Whenever the channel changes**, from anywhere, the linked LEDs are set to match.
- Buttons that drive **several** channels (an "all lights" button) are left unlinked. A wireless panel whose buttons can't be read falls back to button N = relay N.

<p align="center"><img src="https://raw.githubusercontent.com/marsh4200/ar_hdl_buspro/main/images/keypad-led-buttons.png" width="520" alt="Edit a device with the Keypad LED buttons field"></p>

Links live under **Configure → Edit a device → Keypad LED buttons** as `subnet.device:button`, comma-separated. Add, change or clear them any time; leave it empty to turn sync off for that entity.

```
2.1:2              # button 2 on the panel at 2.1
2.1:2, 1.50:4      # two keypads (two-way switching)
```

`2.8.3` is also accepted and saved as `2.8:3`. **DLP buttons** are numbered across pages: page 1 is buttons 1–8, page 2 is 9–16, and so on.

> **LED sync runs in Home Assistant.** If Home Assistant is off, panels go back to plain HDL behaviour.

### Keypad buttons in Home Assistant

A keypad often has more buttons than loads. A spare button can become a **trigger for Home Assistant**: it shows up as an on/off switch, toggles with each press, and fires an event for your automations. Home Assistant answers on the bus at its own address, **`250.250`**.

**1. On the keypad**, the spare button needs to send a **Universal Switch** to **`250.250`** with a free switch number, e.g. **200** (a different number per button). Ask whoever programmed your HDL system to set this up.

**2. In Home Assistant**, run **Scan bus for devices**. The scan reads the button's programming and creates a **keypad button** entity, already linked to its LED. Or add it by hand: *Add a device → Keypad button*, switch number `200`, Keypad LED buttons `2.1:4`.

The **Keypad LED buttons** field is the **keypad's own address and button**, not `250.250`. Not sure what they are? Open **Developer Tools → Events**, listen to `ar_hdl_buspro_keypad_button` and press the button: the event shows `keypad` (e.g. `2.8`) and `button` (e.g. `3`), so the link is `2.8:3`.

**What you get**

- **Press the button** → the entity toggles, the LED behaves normally (Home Assistant confirms the command so the keypad doesn't flash), and `ar_hdl_buspro_keypad_button` fires. DLPs send their real state and are followed exactly; panels that always send the same status toggle on each press.
- **Toggle the entity in Home Assistant** → the button's LED follows.
- The state survives a Home Assistant restart. Several keypads can share one switch number and stay in step.

Nothing is switched on the bus. It's a trigger, so wire it to anything:

```yaml
triggers:
  - trigger: event
    event_type: ar_hdl_buspro_keypad_button
    event_data:
      switch_number: 200
      state: "on"        # the new state; leave out to fire on every press
actions:
  - action: scene.turn_on
    target:
      entity_id: scene.movie_night
```

> If the LED **flashes three times and goes off**, the keypad isn't getting Home Assistant's confirmation: check the target is `250.250`, the integration is running and licensed, and the gateway is reachable.

### Buspro wireless panels

Wireless wall panels (`0x1391`, `0x13C2`, `0x13C3`) have relays built in behind the switch, usually with more buttons than relays. They import as **one switch per relay** with [Keypad LED sync](#keypad-led-sync).

- `0x1391` and `0x13C3` import 3 relays.
- `0x13C2` is sold with **1 or 3 relays** under the same code. If the panel reports its relay count during the scan, that's used; otherwise it imports 3 and you can delete the unused channels.

Wireless panels don't answer HDL's channel-status read, so their state comes from switching events. They're asked a few times after a restart, then left alone instead of being polled forever.

### Sensors, temperature and humidity

Multi-sensors import as a **bundle** under one device.

| Hardware | Readings |
|---|---|
| 12-in-1 (HDL-MS12.2C) | Temperature, lux, motion |
| 8-in-1 (HDL-MS08M.2C) | Temperature, lux, motion |
| 7-in-1 / MSP07M sensors-in-one | Temperature, lux, motion, humidity (when reported) |
| Granite Display / 4" touch panels | Temperature and **humidity** |
| HDL-MTS04 temperature sensor | Temperature |

Touch panels report humidity through HDL's analog-value read, the same way HDL's own software does; importing a Granite Display adds both sensors. By hand: *Add a device → Sensor*, sensor kind **humidity**, hardware kind **panel**. Every sensor exposes `last_telegram` and `raw_payload` attributes so you can see exactly what the hardware sent.

### DALI

HDL DALI controllers import as **one dimmable light per DALI address** that has a ballast fitted, named by address (e.g. *Lobby DALI 12*). If the controller doesn't report its fitted addresses, all its channels are imported. Each **DALI group** with fitted members also gets a light of its own: it switches and dims the whole group in one command and shows the brightest member's level.

### Curtains

Curtain modules (MW02, MWM and MVSM) import as **covers** with open / close / stop; a two-curtain module splits into two covers.

**HDL curtain and roller motors** (MWM / MVSM families) report their own position, so the **position slider** sends a real percentage and shows where the curtain actually is, including after a keypad or remote moves it and after a restart. This is detected automatically; nothing to set.

On **curtain controllers without a position report** (MW02 / MW04) the slider is driven by **travel time** (default 30 s, per device). It's an *estimate*, because those modules take no percentage. Fully open and fully closed are always exact (the module's limit switches decide), and the estimate resyncs whenever the module reports a real endpoint. **Recalibrate via nearest endpoint before repositioning** (off by default, under *Edit a device*) runs to the nearer endpoint first.

**Relay-pair curtains** (a motor on two relay channels plus a travel timer) are added by hand: *Add a device → Cover → mode: relay pair*.

### Air conditioning

The scan doesn't find these; add them under **Configure → Add a device → Climate**.

| | Via an IR module | Via a touch panel |
|---|---|---|
| **Hardware** | HDL-MIRC04.40 (4 "live AC panel" channels) | HDL-MPTL4C.48 Granite Display, HDL-MPTLC43.46-A Enviro and family |
| **Address** | the **module's** subnet/device | the **panel's** subnet/device |
| **Climate protocol** | *Air conditioner via IR module* | *Air conditioner via touch panel* |
| **HVAC No.** | 1–4, one entity per AC | the panel's AC slot (AC 1 = 1, …) |
| **Modes** | Cool, Heat, Fan only, Auto, Dry | Cool, Heat |
| **Fan** | Auto, Low, Medium, High | Auto, Low, Medium, High |
| **Notes** | Narrow **AC HVAC modes to offer** for cooling-only units | Room temperature from the panel (temperature channel `1`, or `0` for none). Setpoint 16–30 °C. Screen changes show up straight away |

The IR mapping comes from real bus captures ([issue #17](https://github.com/marsh4200/ar_hdl_buspro/issues/17)) cross-checked against HDL's AC spec. Swing and the panel's own setpoint limits aren't supported yet. If something misbehaves, please open an issue with a [debug log](#debug-logging).

### Adding and editing devices by hand

Everything the scanner does you can do manually, and everything it imports you can refine:

- **Add a device**: light, switch, universal switch, cover, climate, sensor, binary sensor or keypad button, with subnet / device / channel and type-specific options.
- **Edit a device**: name, channel, dimmable, curtain number, cover mode, presets, scan interval, keypad LED buttons…
- **Remove devices**: tick several and remove them together; the hardware is untouched.

A per-device **scan interval** (sensors and binary sensors) turns on polling; `0` relies on bus broadcasts only.

---

## Reference

<details>
<summary><b>Recognised HDL type codes</b></summary>

<br>

These codes are confirmed from real installations and always take priority. Anything not listed is still identified automatically.

| Code | Classified as | Hardware |
|---|---|---|
| `0x0095` / `0x009C` | Climate | DLP panels |
| `0x0086` | Sensor or Climate | HDL-MTS04.20 4-ch temperature sensor on HDL, DLP2 panel on Smart-Bus; decided by what the device answers |
| `0x0890` | Climate | HDL-MPTL4C.48 Granite Display; also imports its temperature and humidity sensors |
| `0x0260` / `0x026D` / `0x0269` / `0x027E` | Light (dimmer) | MDT0601 / MD0602 / MDT06015 6-ch dimmers |
| `0x0011` | Light (dimmer) | HDL-MRDA06 / SB-DN-6B0-10v 6-ch 0-10V dimmer |
| `0x164B` | Light (dimmer) | HDL-MPD01-RF.28 1-ch wireless dimmer |
| `0x01AC`, `0x01BD`, `0x01BF`, `0x01C1`, `0x01C2` | Switch | Relay modules (4 / 8 / 12 / 16 ch) |
| `0x0DCE` | Switch + Light | HDL-MRCU home control unit: channels 1–17 and 22 as switches, 18–21 as dimmable lights |
| `0x1391` / `0x13C3` / `0x13C2` | Switch | Buspro wireless panels with built-in relays |
| `0x1589` / `0x158A` | Switch | HDL-MPR01-RF.28 1-ch / HDL-MPR02-RF.28 2-ch wireless relays |
| `0x25E5` / `0x25E8` / `0x02C9` | Cover | Curtain motors and HDL-MW02.431 2-ch curtain module |
| `0x0073` / `0x0077` / `0x0166` | Binary sensor | 4-zone and 24-zone dry-contact modules |
| `0x0134` / `0x0141` / `0x0135` | Sensor bundle | 12-in-1 (HDL-MS12.2C) / 8-in-1 |
| `0x0138` / `0x0148` / `0x0150` | Sensor bundle | 7-in-1 / MSP07M sensors-in-one |
| `0x0516` / `0x0517` | Universal switch | HDL-MIRC04.40 IR emitter/receiver module |
| `0x012B`, `0x00AF`, `0x08DB`, `0x080D`, `0x084D`, `0x239C`, `0x238C`, `0x08CA` | Keypad | Wall keypads, DLP panels and Granite Display keypads (labelled, not imported) |
| `0x02F5` / `0x1209` / `0x120B` / `0x0455` / `0x0457` | Gateway / Logic | Mesh gateway, HDL-MBUS01IP.431 IP gateways, logic modules (labelled, not imported) |

Found a device that comes in wrong? The scan log prints every device's type code. Open an ["Unrecognised device / type code"](https://github.com/marsh4200/ar_hdl_buspro/issues/new?template=unsupported_device.yml) issue with the code and what the hardware is, and it gets added.

</details>

<details>
<summary><b>Services</b></summary>

<br>

**`ar_hdl_buspro.activate_scene`**

```yaml
action: ar_hdl_buspro.activate_scene
data:
  address: [1, 74]        # subnet, device id
  scene_address: [3, 5]   # area, scene number
```

**`ar_hdl_buspro.set_universal_switch`**

```yaml
action: ar_hdl_buspro.set_universal_switch
data:
  address: [1, 74]
  switch_number: 100
  status: 1               # 1 = on, 0 = off
```

**`ar_hdl_buspro.send_message`**: raw telegram, for anything else

```yaml
# Single-channel control: channel 1 to 100% over 3 seconds
action: ar_hdl_buspro.send_message
data:
  address: [1, 74]
  operate_code: [0, 49]        # 0x0031 SingleChannelControl
  payload: [1, 100, 0, 3]      # channel, level %, running-time min, sec
```

```yaml
# Set a keypad button's LED: button 2 on the panel at 2.1, on
action: ar_hdl_buspro.send_message
data:
  address: [2, 1]
  operate_code: [227, 216]     # 0xE3D8 panel control
  payload: [17, 2, 1]          # 17 = button status, button, 1 on / 0 off
```

If HDL's protocol can say it, `send_message` can send it.

</details>

<details>
<summary><b>How the connection works</b></summary>

<br>

- HDL Buspro over IP is **connectionless UDP**. The gateway can't be "pinged", so the config entry is created immediately and connectivity shows through entity availability.
- The integration binds UDP port `6000` to hear broadcasts. If something else on the host owns 6000, it falls back to another port: **commands still work**, but broadcasts from other bus devices are missed (polling still works).
- The **source-IP filter** is installed on connect and shown in diagnostics. Without it, a neighbouring HDL system's broadcasts would appear as phantom devices.
- After a restart, each channel is asked for its status a few times. Devices that never answer are then checked every 10 minutes instead of every 20 seconds, which keeps the bus and wireless mesh quiet.
- Downloadable config-entry **diagnostics** redact the host.

</details>

### Debug logging

Run this in **Developer tools → Actions → YAML mode**:

```yaml
action: logger.set_level
data:
  custom_components.ar_hdl_buspro: debug
  ar_hdl_buspro.telegram: debug
  ar_hdl_buspro.buspro: debug
```

Reproduce the problem (or run a bus scan), then **Settings → System → Logs → Download full log**. Set it back to `info` afterwards.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| **Gateway detection finds nothing** | HA and the gateway must share an L2 segment for broadcasts. Routed networks / VLANs block them: allow UDP/6000 broadcast forwarding or use *Enter address manually*. Check the host firewall isn't dropping UDP/6000 |
| **Scan finds nothing, but the gateway was detected** | Make sure nothing else on the same host (another Buspro program, another HA instance) is bound to port 6000 on the same host. Try a 60-second scan and press a few keypad buttons during it |
| **A DLP or wall panel shows as "keypad"** | Tick its feature lines underneath (temperature sensor, floor heating, air conditioner). The panel line itself only covers the buttons |
| **A device came in as the wrong type** | Open an issue with its type code from the scan list. For dimmers, use the *Dimmer type codes* box meanwhile |
| **Keypad LED doesn't follow a relay** | Check *Edit a device → Keypad LED buttons* on that relay. Empty means no link was found: run a bus scan, or type it in (`subnet.device:button`) |
| **Keypad LED lights for the wrong relay** | Remove the wrong entry from *Keypad LED buttons*, or re-run a bus scan |
| **Spare keypad button flashes 3 times and goes off** | Its target must be Universal Switch at **`250.250`**; check the integration is running and licensed |
| **Spare keypad button does nothing in HA** | Add it as **Keypad button** (not *Universal switch*) with the same switch number, or run a bus scan |
| **Keypad button works but its LED doesn't follow HA** | `keypad_leds` shows `none`: set *Keypad LED buttons* to the keypad's address and button, e.g. `2.8:3` |
| **Wireless panel shows extra relay channels** | A 1-relay `0x13C2` that didn't report its relay count imports 3; delete the unused two |
| **Humidity unavailable on a touch panel** | Check hardware kind is **panel**, then open an issue with the `last_telegram` / `raw_payload` attributes |
| **Devices flicker unavailable** | Look for reconnect messages in the log. Persistent drops usually mean duplicate IPs or a flaky switch port on the gateway |
| **Phantom devices from a neighbour's system** | Shouldn't happen; check diagnostics shows your gateway's IP in the source-IP filter |
| **Entities respond but sensor broadcasts never arrive** | The port-6000 fallback is in effect. Free UDP/6000 on the host, or set a per-device scan interval |

## Upgrading

Updating never changes devices you've already set up: names, entities and settings stay as they are. New identification only applies to new scans and imports.

When a new version is out, Home Assistant may show **"AR HDL BUSPRO x.y.z is available"** under **Settings**. Nothing has changed on your system; it's a reminder to update through HACS and disappears once you have.

1. Update through HACS and restart Home Assistant.
2. Run **Configure → Scan bus for devices** and submit. Missing channels and keypad LED links are added; nothing is duplicated.
3. Entities created under a wrong type on an older version aren't removed automatically. Delete them under *Remove devices*, then re-import with a scan if they should exist.

**Coming from the legacy `buspro` integration?** Legacy entries (`host` / `port`) are migrated automatically on first load. Your entities keep working; then run **Scan bus for devices** to pull in everything the old integration couldn't do.

---

## Support the project

⭐ Star the repo · 🐛 [Report a bug or unrecognised device](https://github.com/marsh4200/ar_hdl_buspro/issues) · 💡 Suggest a feature

Type-code contributions are especially welcome: every one makes the next site's scan smarter.

## Licensing

AR HDL BUSPRO is proprietary, licensed software (see [LICENSE](LICENSE)). It is not free or open source, and may not be redistributed or modified. Third-party components it includes keep their own terms; see [NOTICE](NOTICE).

Each Home Assistant install generates its own **Server ID** the first time the integration is set up. It's shown on the Licence step during setup, and any time under **Settings → Devices & Services → AR HDL BUSPRO → Configure → Licence**.

**Getting a licence**

1. Open the **Licence** step (shown during setup, or later under **Configure → Licence**). Leave the licence server address as it is and press **Submit**.
2. Enter your **name** and **email** and press **Request licence**. Nothing is sent until both are filled in.
3. Your request is sent for approval. Once it's approved, the integration activates by itself, or you can open **Licence** and press **Submit** again to check straight away.

**Alternatively**, request a licence at [activatelicense.arsmarthome.co.za](https://activatelicense.arsmarthome.co.za) with your Server ID. You'll be sent a licence key to paste into the **Licence key** box on the same screen.


**UNUSED LICENSES ARE BANNED PERMANENTLY**
