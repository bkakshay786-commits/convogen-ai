#!/usr/bin/env python3
"""
H-ADE - ESP32 AI Firmware Builder (engine v3.3, integration steps I5-I7)
==================================================================
A single-file, zero-dependency (Python standard library only) engine for
the H-ADE core loop. v3 adds the Natural Language -> Specification
engine (roadmap Step 1):

    requirement text
      -> Spec Agent (LLM) writes project.json, OR asks for clarification
      -> Registry Validator (plain code) checks the spec against the
         supported palette: component types, pin rules, rule sanity
         (invalid specs are fed back to the Spec Agent, max 3 rounds)
      -> Firmware Generator (LLM) writes MicroPython FROM the spec
      -> Test Generator (plain code) derives a simulator profile and
         behaviour assertions FROM THE SAME SPEC - independently of
         the firmware, so the tests cannot inherit the firmware's bugs
      -> simulated run -> assertions on serial output AND pin activity
      -> on failure, the real failure is fed back -> retry (max N)
      -> bundle + project.zip

Two honesty rules are enforced by construction:
  * Unknown components or ambiguous requirements produce a
    CLARIFICATION outcome (exit code 3), never invented hardware.
  * A simulator pass is labelled "Digitally validated", never
    "Physically verified" (that label needs a real board run).

Server contract (unchanged from v2, plus the clarification outcome):
    python hade.py --requirement "<text>" --out <dir> --attempts 5
  - progress events are printed as lines:  HADE_EVENT {json}
  - the LAST line of stdout is one JSON summary object
  - exit codes: 0 = passed validation, 2 = attempts exhausted,
    3 = clarification needed, 1 = engine error

Standalone use:
    python hade.py            classic project, live (needs GROQ_API_KEY)
    python hade.py --stub     offline demo of every path (no key):
                              classic + greenhouse specs, deliberate
                              firmware bugs the loop must catch, and a
                              clarification case

Output bundle (in --out, default ./output/<spec name>/):
    main.py         the final firmware
    project.json    the validated specification (source of truth, v3)
    hardware.json   components + pin map (derived view of the spec)
    bom.md          bill of materials with indicative prices
    wiring.md       wiring instructions
    sim_config.json the test scenario the firmware was validated against
    report.md       attempts evidence + validation labels
    README.md       setup / flashing instructions
    serial_log.txt  serial output of the final simulated run
    attempts/       every generated attempt, kept as evidence
    project.zip     the whole bundle as one downloadable file
"""

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
import zipfile
from datetime import datetime

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

ENGINE_VERSION = "3.3"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_ROOT = os.path.join(SCRIPT_DIR, "output")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("HADE_MODEL", "qwen/qwen3.8-27b")

DEFAULT_ATTEMPTS = 5      # firmware self-healing cap (roadmap: max five)
MAX_SPEC_ROUNDS = 3       # spec-agent attempts before giving up / asking

# --------------------------------------------------------------------------
# Supported-components registry (the "hardware knowledge")
# The Spec Agent may only design with what is listed here, and the
# Registry Validator enforces it in code. This registry is the seed of
# the H-ADE moat: it grows one verified entry at a time.
#
# Since engine 3.2 (milestone I6.5) each entry is a full DESCRIPTOR and
# the single source of truth for its component: the prompt text, the
# validator, the metric tables, the BOM, the wiring instructions and
# the hardware view are all DERIVED from these descriptors - nothing
# about a component is restated anywhere else. A component may only be
# registered when its descriptor is complete: purpose, pins with
# direction/protocol, electrical data, firmware API, simulator support,
# test sabotages, BOM and wiring.
# --------------------------------------------------------------------------

REGISTRY = {
    "dht22": {
        "kind": "sensor",
        "label": "DHT22 temperature/humidity sensor",
        "purpose": "Measures ambient temperature and relative humidity",
        "metrics": ["temperature", "humidity"],
        "metric_details": {
            "temperature": {"unit": "C", "range": (-10.0, 60.0),
                            "profile_delta": 3.0, "neutral": 26.0,
                            "profile_clamp": (-5.0, 55.0)},
            "humidity": {"unit": "%", "range": (0.0, 100.0),
                         "profile_delta": 8.0, "neutral": 55.0,
                         "profile_clamp": (2.0, 98.0)},
        },
        "pin_roles": ["data"],
        "pins_detail": [{"role": "data", "direction": "bidirectional",
                         "protocol": "one-wire"}],
        "electrical": {"supply_v": [3.3], "logic_v": 3.3,
                       "notes": "10k pull-up between DATA and 3V3 "
                                "(many modules include it)"},
        "api": "sensor = dht.DHT22(machine.Pin(<data>)); call "
               "sensor.measure() before sensor.temperature() / "
               "sensor.humidity()",
        "prompt_line": "dht22: temperature + humidity sensor. "
                       "Pins: {\"data\": <gpio>}.\n"
                       "  API: dht.DHT22(machine.Pin(n)); measure() "
                       "before temperature() /\n"
                       "  humidity(). At most ONE dht22 per project.",
        "simulator": {"stub": "dht",
                      "channels": ["temperature", "humidity"],
                      "failures": []},
        "tests": {"sabotage": ["read_bug"]},
        "bom": {"name": "DHT22 (AM2302) temperature/humidity sensor "
                        "module",
                "qty": 1, "price_inr": [250, 350],
                "note": "Cheaper alternative: DHT11 (Rs 90-130, less "
                        "accurate); add a 10k pull-up resistor (Rs 5) "
                        "if your module lacks one"},
        "wiring": ["- VCC -> ESP32 3V3",
                   "- GND -> ESP32 GND",
                   "- DATA -> GPIO {data} (10k pull-up resistor "
                   "between DATA and 3V3; many DHT22 modules already "
                   "include it)"],
        "hw_notes": "10k pull-up between DATA and 3V3; call measure() "
                    "before temperature()/humidity()",
    },
    "relay": {
        "kind": "actuator",
        "label": "Relay module (1-channel, active-HIGH)",
        "purpose": "Switches an electrical load (fan, pump, lamp, "
                   "heater) on and off",
        "metrics": [],
        "pin_roles": ["control"],
        "pins_detail": [{"role": "control", "direction": "output",
                         "protocol": "gpio"}],
        "electrical": {"supply_v": [5.0, 3.3], "logic_v": 3.3,
                       "notes": "Module supply per vendor (5V "
                                "typical); the contact side rating is "
                                "separate - see the safety notes"},
        "print_base": "RELAY",
        "api": "pin = machine.Pin(<control>, machine.Pin.OUT); "
               "pin.value(1) = ON, pin.value(0) = OFF (active-HIGH)",
        "prompt_line": "relay: relay module, switches a load (fan, "
                       "pump, lamp, heater...).\n"
                       "  Pins: {\"control\": <gpio>}. Active-HIGH.",
        "simulator": {"stub": "pin", "channels": [], "failures": []},
        "tests": {"sabotage": ["dropped_rule"]},
        "bom": {"name": "1-channel relay module, 5V, with optocoupler",
                "qty": 1, "price_inr": [90, 150],
                "note": "Keep the load side unwired during firmware "
                        "tests"},
        "wiring": ["- IN -> GPIO {control}",
                   "- VCC -> 5V (or 3V3, per your module), "
                   "GND -> GND",
                   "- The relay is ACTIVE-HIGH: GPIO HIGH = "
                   "relay ON."],
        "hw_notes": "value(1) = ON, value(0) = OFF (active-HIGH); "
                    "state printed as {print_name}:ON/OFF",
    },
    "led": {
        "kind": "actuator",
        "label": "LED indicator",
        "purpose": "Visual indicator for alarms and status",
        "metrics": [],
        "pin_roles": ["control"],
        "pins_detail": [{"role": "control", "direction": "output",
                         "protocol": "gpio"}],
        "electrical": {"supply_v": [3.3], "logic_v": 3.3,
                       "notes": "220 ohm series resistor required to "
                                "limit the LED current"},
        "print_base": "LED",
        "api": "pin = machine.Pin(<control>, machine.Pin.OUT); "
               "pin.value(1) = lit, pin.value(0) = off",
        "prompt_line": "led: LED indicator (alarm / status light). "
                       "Pins: {\"control\": <gpio>}.",
        "simulator": {"stub": "pin", "channels": [], "failures": []},
        "tests": {"sabotage": ["dropped_rule"]},
        "bom": {"name": "LED 5mm + 220 ohm resistor",
                "qty": 1, "price_inr": [5, 15],
                "note": "The resistor is not optional - it limits "
                        "the LED current"},
        "wiring": ["- GPIO {control} -> 220 ohm resistor -> LED "
                   "anode (long leg)",
                   "- LED cathode (short leg) -> ESP32 GND"],
        "hw_notes": "value(1) = lit; 220 ohm series resistor; state "
                    "printed as {print_name}:ON/OFF",
    },
    "buzzer": {
        "kind": "actuator",
        "label": "Active buzzer module",
        "purpose": "Audible alarm (sound) - on/off only",
        "metrics": [],
        "pin_roles": ["control"],
        "pins_detail": [{"role": "control", "direction": "output",
                         "protocol": "gpio"}],
        "electrical": {"supply_v": [5.0, 3.3], "logic_v": 3.3,
                       "notes": "Active type with built-in "
                                "oscillator; a passive buzzer needs "
                                "PWM and is not supported"},
        "print_base": "BUZZER",
        "api": "pin = machine.Pin(<control>, machine.Pin.OUT); "
               "pin.value(1) = sounding, pin.value(0) = silent "
               "(active buzzer with built-in oscillator - GPIO "
               "on/off only, no PWM tones)",
        "prompt_line": "buzzer: active buzzer module (audible "
                       "alarm). Pins: {\"control\": <gpio>}.\n"
                       "  GPIO on/off only - it has a built-in "
                       "oscillator, so there are no\n"
                       "  tones, melodies or volume control.",
        "simulator": {"stub": "pin", "channels": [], "failures": []},
        "tests": {"sabotage": ["dropped_rule"]},
        "bom": {"name": "Active buzzer module, 3-pin (GPIO on/off)",
                "qty": 1, "price_inr": [40, 90],
                "note": "Must be the ACTIVE type (built-in "
                        "oscillator). A passive buzzer needs PWM "
                        "tones, which this version does not generate"},
        "wiring": ["- I/O (signal) -> GPIO {control}",
                   "- VCC -> 5V (or 3V3, per your module), "
                   "GND -> GND",
                   "- Active buzzer with a built-in oscillator: "
                   "GPIO HIGH = sounding, LOW = silent. No tone "
                   "or PWM is needed - it is fully on or off."],
        "hw_notes": "value(1) = sounding, value(0) = silent (active "
                    "buzzer, built-in oscillator); state printed as "
                    "{print_name}:ON/OFF",
    },
}

# Pins that must never be used at all in a spec: UART0 (1, 3) and the
# ESP32 boot-strapping pins (0, 2, 12, 15).
FORBIDDEN_PINS = {0, 1, 2, 3, 12, 15}
# GPIO 34-39 are input-only on ESP32: fine for a sensor data line,
# never for an output.
INPUT_ONLY_PINS = {34, 35, 36, 37, 38, 39}

# Metric knowledge lives in the registry descriptors (metric_details);
# these tables are derived views for the validator / test generator.
METRIC_DETAILS = {}
for _info in REGISTRY.values():
    METRIC_DETAILS.update(_info.get("metric_details", {}))
METRIC_RANGES = {name: (d["range"][0], d["range"][1], d["unit"])
                 for name, d in METRIC_DETAILS.items()}
# How far beyond a threshold the simulator profile steps to prove the
# rule really switches (kept well inside the sensor's physical range).
PROFILE_DELTA = {name: d["profile_delta"]
                 for name, d in METRIC_DETAILS.items()}
NEUTRAL_POINT = {name: d["neutral"]
                 for name, d in METRIC_DETAILS.items()}
# Where fire-values are clamped when synthesizing test profiles.
METRIC_PROFILE_CLAMP = {name: d["profile_clamp"]
                        for name, d in METRIC_DETAILS.items()}

def _build_registry_text():
    """The prompt-facing palette description, GENERATED from the
    registry descriptors - the Spec Agent can never be told about
    hardware the validator does not know. (Engine 3.1 kept a
    hand-written copy of this text beside the registry; the two could
    drift apart. The pin numbers below come from FORBIDDEN_PINS /
    INPUT_ONLY_PINS themselves.)"""
    lines = ["SUPPORTED HARDWARE (nothing outside this list may be "
             "used):",
             '- Board: ESP32 development board ("esp32"), 3.3V logic.']
    for info in REGISTRY.values():
        lines.append("- " + info["prompt_line"])
    forbidden = sorted(FORBIDDEN_PINS)
    forbidden_text = ", ".join(str(p) for p in forbidden[:-1]) \
        + f" or {forbidden[-1]}"
    lines.append(
        "PIN RULES: every GPIO 0-39; pins must be unique across the "
        "project;\n"
        f"never use GPIO {forbidden_text}; outputs (relay/led "
        "control) may\n"
        f"never use the input-only GPIO {min(INPUT_ONLY_PINS)}-"
        f"{max(INPUT_ONLY_PINS)}.")
    return "\n".join(lines)


REGISTRY_TEXT = _build_registry_text()

# --------------------------------------------------------------------------
# Built-in specifications
# --------------------------------------------------------------------------

CLASSIC_SPEC = {
    "name": "dht22-relay-control",
    "title": "DHT22 Relay Temperature Control",
    "board": "esp32",
    "components": [
        {"id": "sensor1", "type": "dht22",
         "label": "DHT22 temperature/humidity sensor", "pins": {"data": 4}},
        {"id": "relay1", "type": "relay",
         "label": "Relay module", "pins": {"control": 5}},
    ],
    "rules": [
        {"metric": "temperature", "op": ">", "threshold": 30,
         "unit": "C", "actuator": "relay1"},
    ],
    "sampling_interval_s": 2,
}

GREENHOUSE_SPEC = {
    "name": "smart-greenhouse-monitor",
    "title": "Smart Greenhouse Monitoring System",
    "board": "esp32",
    "components": [
        {"id": "sensor1", "type": "dht22",
         "label": "DHT22 temperature/humidity sensor", "pins": {"data": 4}},
        {"id": "fan", "type": "relay",
         "label": "Relay module - ventilation fan", "pins": {"control": 5}},
        {"id": "alarm", "type": "led",
         "label": "LED - low-humidity alarm", "pins": {"control": 18}},
    ],
    "rules": [
        {"metric": "temperature", "op": ">", "threshold": 32,
         "unit": "C", "actuator": "fan"},
        {"metric": "humidity", "op": "<", "threshold": 40,
         "unit": "%", "actuator": "alarm"},
    ],
    "sampling_interval_s": 2,
}

BUZZER_ALARM_SPEC = {
    "name": "temperature-sound-alarm",
    "title": "Temperature Sound Alarm",
    "board": "esp32",
    "components": [
        {"id": "sensor1", "type": "dht22",
         "label": "DHT22 temperature/humidity sensor", "pins": {"data": 4}},
        {"id": "buzzer1", "type": "buzzer",
         "label": "Active buzzer - overheat alarm", "pins": {"control": 18}},
    ],
    "rules": [
        {"metric": "temperature", "op": ">", "threshold": 30,
         "unit": "C", "actuator": "buzzer1"},
    ],
    "sampling_interval_s": 2,
}

GREENHOUSE_BUZZER_SPEC = {
    "name": "greenhouse-sound-alarm",
    "title": "Greenhouse Sound Alarm",
    "board": "esp32",
    "components": [
        {"id": "sensor1", "type": "dht22",
         "label": "DHT22 temperature/humidity sensor", "pins": {"data": 4}},
        {"id": "fan", "type": "relay",
         "label": "Relay module - ventilation fan", "pins": {"control": 5}},
        {"id": "alarm", "type": "buzzer",
         "label": "Active buzzer - low-humidity alarm",
         "pins": {"control": 18}},
    ],
    "rules": [
        {"metric": "temperature", "op": ">", "threshold": 32,
         "unit": "C", "actuator": "fan"},
        {"metric": "humidity", "op": "<", "threshold": 40,
         "unit": "%", "actuator": "alarm"},
    ],
    "sampling_interval_s": 2,
}

# Bill-of-materials knowledge per component type. Prices are INDICATIVE
# Indian market ranges (Robu / Robocraze / Amazon.in class sellers);
# the BOM file itself tells the user to verify before ordering.
BOM_BASE = [
    ("ESP32 DevKit development board", 1, (350, 450),
     "Any ESP32-WROOM-32 dev board works"),
    ("Breadboard, 400 points", 1, (80, 120), ""),
    ("Jumper wires (M-M / M-F set)", 1, (60, 100), ""),
    ("USB data cable for the ESP32 board", 1, (100, 150),
     "Must be a data cable, not a charge-only cable"),
]
# Derived from the registry descriptors (each carries its own bom
# entry) - the tuple shape write_bom consumes: (name, qty, (low, high)
# price range in INR, note). Prices are INDICATIVE Indian market
# ranges, not verified live prices.
BOM_BY_TYPE = {
    ctype: (info["bom"]["name"], info["bom"]["qty"],
            tuple(info["bom"]["price_inr"]), info["bom"]["note"])
    for ctype, info in REGISTRY.items()
}

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def event(**fields):
    """Machine-readable progress line for the calling server.
    Human-readable lines are printed separately; the server picks out
    lines starting with HADE_EVENT and parses the JSON after the prefix."""
    fields.setdefault("engine", ENGINE_VERSION)
    print("HADE_EVENT " + json.dumps(fields), flush=True)


def load_env_key(name):
    """Read a key from the process env, falling back to a local .env file.
    (The ConvoGen server passes its own environment, so in integrated mode
    the key comes from ConvoGen's .env - one source of truth.)"""
    if os.environ.get(name):
        return os.environ[name]
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def extract_code(text):
    """Pull the code out of an LLM reply (fenced block or raw code)."""
    match = re.search(r"```(?:python|micropython)?\s*\n(.*?)```", text, re.S)
    if match:
        return match.group(1).strip() + "\n"
    return text.strip() + "\n"


def extract_json(text):
    """Pull a JSON object out of an LLM reply. Returns the parsed object
    or None. Tolerates code fences and surrounding prose."""
    if not text:
        return None
    match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    candidate = match.group(1) if match else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(candidate[start:end + 1])
    except (ValueError, TypeError):
        return None


def fmt_num(value):
    """30 -> '30', 32.5 -> '32.5' (spec thresholds read naturally)."""
    number = float(value)
    return str(int(number)) if number == int(number) else str(number)


# --------------------------------------------------------------------------
# LLM access (Groq, OpenAI-compatible) - standard library only
# --------------------------------------------------------------------------

def call_llm(messages, temperature=0.2):
    api_key = load_env_key("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not found. Put it in a .env file next to hade.py "
            "(one line: GROQ_API_KEY=...) or run with --stub."
        )
    payload = {
        "model": MODEL,
        "temperature": temperature,
        "messages": messages,
    }
    request = urllib.request.Request(
        GROQ_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
            # Groq sits behind Cloudflare, which rejects the default
            # Python-urllib signature with "403 error code: 1010"
            # (seen live 2026-11-10). A normal browser User-Agent
            # avoids that false-positive bot block.
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/130.0.0.0 Safari/537.36",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Groq API error {exc.code}: {body}")
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------
# Project understanding (I7) - archetype catalog, intent classifier,
# slot extraction and slot checks. All plain code: the classifier is a
# hint plus a slot computation around the Spec Agent, which stays the
# author of the spec. A slot is exactly one of three kinds:
#   number   - a threshold / count the user must state
#   decision - a design choice the user must make
#   registry - hardware the registry does not have yet; the
#              clarification names it and the milestone that adds it
# A slot counts as FILLED only when the user's text states it
# explicitly (conservative extraction below). Open required slots are
# handed to the Spec Agent as must-ask, and a spec that quietly fills
# one anyway is converted to a slot-template clarification by the
# post-check - the project-specific questions are guaranteed by code,
# not left to the LLM's luck.
# --------------------------------------------------------------------------

_TEMP_KEYWORDS = ["temperature", "temp", "hot", "heat", "warm"]
_HUM_KEYWORDS = ["humidity", "humid"]
_ALARM_CUES = {"buzzer": ["buzzer", "sound", "audible", "beep"],
               "led": ["led"]}

ARCHETYPES = {
    "greenhouse": {
        "label": "Smart greenhouse",
        "buildable": True,
        "signals": [("greenhouse", 4), ("grow tent", 2),
                    ("seedlings", 1), ("plants", 1), ("grow", 1)],
        "slots": [
            {"id": "temp_threshold", "kind": "number",
             "metric": "temperature", "unit": "C",
             "keywords": _TEMP_KEYWORDS,
             "question": "At what temperature (°C) should the "
                         "ventilation fan turn on? Please give a "
                         "number, e.g. 'above 32°C'."},
            {"id": "humidity_threshold", "kind": "number",
             "metric": "humidity", "unit": "%",
             "keywords": _HUM_KEYWORDS,
             "question": "At what humidity (%) should the alarm "
                         "activate? Please give a number, e.g. "
                         "'below 40%'."},
        ],
    },
    "industrial_monitoring": {
        "label": "Industrial temperature monitoring",
        "buildable": True,
        "signals": [("industrial", 3), ("server room", 3),
                    ("factory", 2), ("warehouse", 2),
                    ("monitoring", 2), ("monitor", 1)],
        "slots": [
            {"id": "temp_threshold", "kind": "number",
             "metric": "temperature", "unit": "C",
             "keywords": _TEMP_KEYWORDS + ["degrees"],
             "question": "Above what temperature (°C) should the "
                         "alert trigger? Please give a number."},
            {"id": "alarm_actuator", "kind": "decision",
             "options": ["led", "buzzer", "both"], "cues": _ALARM_CUES,
             "question": "How should the alert be signalled - an "
                         "LED, a buzzer (sound), or both?"},
        ],
    },
    "fire_safety": {
        "label": "Fire detection system",
        "buildable": True,
        "signals": [("fire detection", 3), ("fire", 3), ("smoke", 2),
                    ("flame", 2)],
        "slots": [
            {"id": "detection_method", "kind": "decision",
             "options": ["temperature", "smoke_gas"],
             "cues": {"temperature": ["temperature", "heat"],
                      "smoke_gas": ["smoke", "gas", "flame sensor",
                                    "mq-2", "mq2"]},
             "registry_options": {
                 "smoke_gas": ("smoke/gas sensor", None)},
             "question": "How should fire be detected - by "
                         "temperature rise (buildable today with "
                         "the DHT22), or by a smoke/gas sensor "
                         "(not supported yet)?"},
            {"id": "temp_threshold", "kind": "number",
             "metric": "temperature", "unit": "C",
             "keywords": _TEMP_KEYWORDS,
             "question": "Above what temperature (°C) should the "
                         "fire alarm trigger? Please give a number."},
            {"id": "alarm_actuator", "kind": "decision",
             "options": ["led", "buzzer", "both"], "cues": _ALARM_CUES,
             "question": "How should the fire alarm be signalled - "
                         "a buzzer (sound), an LED, or both?"},
        ],
    },
    "parking": {
        "label": "Smart parking system",
        "buildable": False,
        "signals": [("parking", 3), ("car park", 3), ("vehicle", 2)],
        "slots": [
            {"id": "detection_method", "kind": "decision",
             "options": ["ultrasonic", "ir"],
             "cues": {"ultrasonic": ["ultrasonic", "distance"],
                      "ir": ["ir obstacle", "infrared", "ir sensor"]},
             "registry_options": {
                 "ultrasonic": ("ultrasonic distance sensor", "I10"),
                 "ir": ("IR obstacle sensor", "I10")},
             "question": "How should vehicle presence be detected "
                         "- an ultrasonic distance sensor, or an IR "
                         "obstacle sensor? (Neither is in the "
                         "registry yet - vehicle sensing arrives "
                         "in I10, so a parking project cannot be "
                         "built today; your answer records the "
                         "design.)"},
            {"id": "slot_count", "kind": "decision",
             "options": ["one", "multiple"],
             "cues": {"one": ["one slot", "single slot", "1 slot"],
                      "multiple": ["multiple", "several", "many slots",
                                   "2 slots", "3 slots", "4 slots"]},
             "question": "Should the system monitor one parking "
                         "slot or multiple slots?"},
        ],
    },
    "irrigation": {
        "label": "Smart irrigation system",
        "buildable": False,
        "signals": [("irrigation", 3), ("water my plants", 3),
                    ("water the plants", 3), ("watering", 2),
                    ("soil", 2)],
        "slots": [
            {"id": "soil_sensor", "kind": "registry",
             "component": "soil moisture sensor", "milestone": "I9",
             "question": "Watering from soil moisture needs a soil "
                         "moisture sensor, which is not supported "
                         "yet - it is planned as registry addition "
                         "I9, so this project cannot be built today. "
                         "(The questions below record your design "
                         "for when it lands.)"},
            {"id": "soil_threshold", "kind": "number",
             "metric": "soil_moisture", "unit": "%",
             "keywords": ["soil", "moisture"],
             "question": "What soil moisture level (%) should "
                         "start the watering? e.g. 'below 30%'."},
            {"id": "pump_actuator", "kind": "decision",
             "options": ["relay"],
             "cues": {"relay": ["relay", "pump"]},
             "question": "The pump must be switched by a supported "
                         "actuator - a relay module is the right "
                         "fit. Should the pump be controlled by "
                         "the relay?"},
        ],
    },
    "security": {
        "label": "Home security system",
        "buildable": False,
        "signals": [("security", 3), ("intruder", 2), ("burglar", 2),
                    ("alarm system", 2), ("motion", 1)],
        "slots": [
            {"id": "trigger", "kind": "registry",
             "component": "PIR motion sensor", "milestone": "I10",
             "question": "Motion detection needs a PIR motion "
                         "sensor, which is not supported yet - it "
                         "arrives in I10 (a push-button trigger "
                         "arrives earlier, in I8). A security alarm "
                         "is not offered on temperature thresholds "
                         "- that would not detect an intruder."},
            {"id": "alarm_actuator", "kind": "decision",
             "options": ["led", "buzzer", "both"], "cues": _ALARM_CUES,
             "question": "When the alarm triggers, should it sound "
                         "a buzzer, light an LED, or both?"},
        ],
    },
    "street_lighting": {
        "label": "Automatic street lighting",
        "buildable": False,
        "signals": [("street lighting", 3), ("street light", 3),
                    ("streetlight", 3), ("lamp post", 2), ("dusk", 1)],
        "slots": [
            {"id": "ldr_sensor", "kind": "registry",
             "component": "LDR light sensor", "milestone": "I9",
             "question": "Switching at dusk needs an LDR light "
                         "sensor, which is not supported yet - it "
                         "is planned as registry addition I9, so "
                         "this project cannot be built today."},
            {"id": "light_threshold", "kind": "number",
             "metric": "light", "unit": "%",
             "keywords": ["dark", "dusk", "light", "lux"],
             "question": "At what light level should the lamp "
                         "turn on? You can give a percentage of "
                         "darkness, e.g. 'when it is 70% dark'."},
            {"id": "lamp_actuator", "kind": "decision",
             "options": ["relay", "led"],
             "cues": {"relay": ["lamp", "street light", "streetlight",
                                "lamp post"],
                      "led": ["led", "model", "indicator"]},
             "question": "What should be switched - a real lamp "
                         "(via the relay module) or an LED (e.g. "
                         "for a model or prototype)?"},
        ],
    },
}


def classify_intent(text):
    """Deterministic archetype scoring (code, not AI). Returns
    (archetype_id, top_score, all_scores): the top archetype wins
    only with score >= 2 and a lead of >= 1 over the runner-up,
    otherwise the project is 'custom' - today's free-form flow.
    Longer signal phrases consume their text span so a short phrase
    inside a longer one ('monitor' inside 'monitoring') is not
    double-counted."""
    text_l = (text or "").lower()
    scores = {}
    for aid, arch in ARCHETYPES.items():
        spans = []
        total = 0
        phrases = sorted(arch["signals"], key=lambda p: -len(p[0]))
        for phrase, weight in phrases:
            start = text_l.find(phrase)
            while start != -1:
                end = start + len(phrase)
                if not any(start < e and s < end for s, e in spans):
                    spans.append((start, end))
                    total += weight
                    break
                start = text_l.find(phrase, start + 1)
        if total:
            scores[aid] = total
    if not scores:
        return "custom", 0, scores
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, top_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if top_score >= 2 and top_score - second >= 1:
        return top, top_score, scores
    return "custom", top_score, scores


_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(°\s*c|degrees?|c|%|percent)?")


def _extract_number(text_l, slot):
    """The value for a number slot, or None. Conservative: a number
    counts only if its unit is compatible with the slot and it sits
    near one of the slot's keywords (or it is the ONLY compatible
    number in the text). Out-of-range values for known metrics are
    treated as not stated."""
    metric = slot.get("metric")
    want_pct = slot.get("unit") == "%"
    candidates = []
    for match in _NUM_RE.finditer(text_l):
        unit = (match.group(2) or "").replace(" ", "")
        is_pct = unit in ("%", "percent")
        is_temp = unit in ("c", "°c", "degree", "degrees")
        if want_pct and is_temp:
            continue
        if not want_pct and is_pct:
            continue
        candidates.append((match.start(), match.end(),
                           float(match.group(1))))
    if not candidates:
        return None
    chosen = None
    keyword_spans = []
    for keyword in slot.get("keywords", []):
        start = text_l.find(keyword)
        while start != -1:
            keyword_spans.append((start, start + len(keyword)))
            start = text_l.find(keyword, start + 1)
    best = None
    for num_start, num_end, value in candidates:
        for key_start, key_end in keyword_spans:
            if key_end <= num_start:
                dist = num_start - key_end
            elif num_end <= key_start:
                dist = key_start - num_end
            else:
                dist = 0
            if dist <= 60 and (best is None or dist < best[0]):
                best = (dist, value)
    if best is not None:
        chosen = best[1]
    elif len(candidates) == 1:
        chosen = candidates[0][2]
    if chosen is None:
        return None
    if metric in METRIC_RANGES:
        low, high, _unit = METRIC_RANGES[metric]
        if not (low <= chosen <= high):
            return None
    return chosen


def _extract_decision(text_l, slot):
    """The chosen option for a decision slot, or None: an option is
    chosen only when one of its cue phrases appears in the text."""
    cued = []
    if "both" in slot.get("options", []) and "both" in text_l:
        return "both"
    for option in slot.get("options", []):
        cues = slot.get("cues", {}).get(option, [])
        if any(cue in text_l for cue in cues):
            cued.append(option)
    if len(cued) >= 2 and "both" in slot.get("options", []):
        return "both"
    if len(cued) == 1:
        return cued[0]
    if len(cued) > 1:
        return cued[0]
    return None


def analyze_slots(text, archetype_id):
    """Split an archetype's required slots into (filled, open) using
    ONLY explicit statements in the user's text."""
    arch = ARCHETYPES[archetype_id]
    text_l = (text or "").lower()
    filled, open_slots = {}, []
    for slot in arch["slots"]:
        if slot["kind"] == "number":
            value = _extract_number(text_l, slot)
        elif slot["kind"] == "decision":
            value = _extract_decision(text_l, slot)
        else:  # registry slots can never be filled by text
            value = None
        if value is None:
            open_slots.append(slot)
        else:
            filled[slot["id"]] = value
    return filled, open_slots


def _slot_record(slot):
    record = {"id": slot["id"], "kind": slot["kind"],
              "question": slot["question"], "required": True,
              "blocks_build": slot["kind"] == "registry"}
    if "options" in slot:
        record["options"] = slot["options"]
    if slot["kind"] == "registry":
        record["component"] = slot["component"]
        record["milestone"] = slot["milestone"]
    return record


def _registry_option_note(slot, value):
    component, milestone = slot["registry_options"][value]
    if milestone:
        return (f"{component} is not in the supported registry yet "
                f"- it is planned as registry addition {milestone}, "
                "so this part of the project cannot be built today.")
    return (f"{component} is not in the supported registry yet, and "
            "no milestone is assigned to it yet, so this part of the "
            "project cannot be built today.")


def slot_clarification(archetype_id, slots_to_ask, filled=None):
    """Compose the deterministic clarification for a set of slots:
    template questions in catalog order (max 4), the structured
    slots[] records, and the unsupported-hardware list."""
    arch = ARCHETYPES[archetype_id]
    questions, unsupported, records = [], [], []
    for slot in slots_to_ask:
        records.append(_slot_record(slot))
        if slot["kind"] == "registry":
            unsupported.append(slot["component"])
        if len(questions) < 4:
            questions.append(slot["question"])
    for slot_id, value in (filled or {}).items():
        slot = next((s for s in arch["slots"] if s["id"] == slot_id),
                    None)
        if slot and value in slot.get("registry_options", {}):
            component = slot["registry_options"][value][0]
            if component not in unsupported:
                unsupported.append(component)
            note = _registry_option_note(slot, value)
            if note not in questions:
                questions.insert(0, note)
    return {"questions": questions, "unsupported": unsupported,
            "slots": records, "archetype": archetype_id}


def intent_blocker(archetype_id, filled, open_slots):
    """A clarification dict when the project cannot proceed at all
    today (a registry slot is open, a chosen option needs missing
    hardware, or the archetype is not buildable yet); else None.
    When it blocks, ALL open slots are asked - the answers record
    the design for the milestone that unblocks it."""
    arch = ARCHETYPES[archetype_id]
    registry_open = any(s["kind"] == "registry" for s in open_slots)
    chosen_missing = any(
        value in slot.get("registry_options", {})
        for slot in arch["slots"] for slot_id, value in filled.items()
        if slot["id"] == slot_id)
    if registry_open or chosen_missing or not arch["buildable"]:
        if open_slots or chosen_missing:
            return slot_clarification(archetype_id, open_slots, filled)
    return None


def post_check_slots(spec, archetype_id, open_slots):
    """The anti-guessing post-check: the subset of open slots that a
    returned spec quietly filled anyway (a rule on an unstated
    metric, an alarm output the user never picked, a detection basis
    the user never chose). Those become slot clarifications."""
    bad = []
    for slot in open_slots:
        if slot["kind"] == "number" and slot.get("metric") \
                in METRIC_RANGES:
            if any(r.get("metric") == slot["metric"]
                   for r in spec.get("rules", [])):
                bad.append(slot)
        elif slot["id"] == "detection_method":
            bad.append(slot)
        elif slot["id"] == "alarm_actuator":
            types = {c.get("type") for c in spec.get("components", [])}
            if types & {"led", "buzzer"}:
                bad.append(slot)
    return bad


def archetype_context_block(archetype_id, filled, open_slots):
    """Facts + must-ask list appended to the Spec Agent's user turn
    when an archetype was classified."""
    arch = ARCHETYPES[archetype_id]
    lines = ["",
             "ARCHETYPE CONTEXT (from H-ADE's project-understanding "
             "layer - computed by code from the user's own words):",
             f"- Project archetype: {archetype_id} ({arch['label']}). "
             f"Set \"archetype\": \"{archetype_id}\" in the spec."]
    by_id = {s["id"]: s for s in arch["slots"]}
    for slot_id, value in filled.items():
        slot = by_id[slot_id]
        unit = slot.get("unit", "")
        lines.append(f"- Stated by the user: {slot_id} = "
                     f"{fmt_num(value) if isinstance(value, (int, float)) else value}"
                     f"{unit if slot['kind'] == 'number' else ''} "
                     "(use verbatim).")
    if open_slots:
        lines.append("- Still unknown - do NOT guess any of these; "
                     "if the specification needs one, return a "
                     "clarification request instead: "
                     + ", ".join(s["id"] for s in open_slots) + ".")
    lines.append("- Give every component a short \"purpose\".")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Spec Agent - turns the requirement into project.json (or asks).
# The agent PROPOSES; the Registry Validator (plain code, below) DECIDES.
# --------------------------------------------------------------------------

SPEC_SYSTEM_PROMPT = f"""You are the Hardware Specification Agent of H-ADE.
You turn a user's project description into a structured specification
(project.json) using ONLY the supported hardware palette. You never
invent components, sensors, pins or capabilities.

{REGISTRY_TEXT}

Reply with ONLY a JSON object, no prose, in one of two shapes:

1) A specification:
{{"status": "ok", "spec": {{
  "name": "<slug: lowercase letters, digits, hyphens>",
  "title": "<short human title>",
  "board": "esp32",
  "components": [
    {{"id": "<short id>", "type": "dht22", "label": "<what it is>",
      "pins": {{"data": 4}}}},
    {{"id": "<short id>", "type": "relay", "label": "<what it switches>",
      "pins": {{"control": 5}}}}
  ],
  "rules": [
    {{"metric": "temperature", "op": ">", "threshold": 30, "unit": "C",
      "actuator": "<component id>"}}
  ],
  "sampling_interval_s": 2
}}}}

2) A clarification request, when the description cannot be built with
the palette or is ambiguous:
{{"status": "needs_clarification",
  "questions": ["<one clear question per problem>"],
  "unsupported": ["<each requested thing the palette cannot do>"]}}

DESIGN RULES:
- Loads a user wants switched (fan, pump, lamp, heater, motor on/off)
  map to a relay. An alarm or indicator maps to an led; if the user
  asks for sound, an audible alarm or a buzzer, use a buzzer instead
  (active buzzer: on/off only - if the user wants melodies, specific
  tones or volume control, ask for clarification).
- Use the exact thresholds and comparisons the user states. If a rule
  has no clear threshold ("when it gets hot"), DO NOT guess a number -
  ask for clarification instead.
- Every actuator needs at least one rule; every metric must come from
  a sensor in the project; at most 6 components and 4 rules.
- Choose sensible free pins that obey the pin rules.
- If the user message includes an ARCHETYPE CONTEXT block: its facts
  are exact user statements - use those values verbatim. Its open
  slots are must-ask: if the specification needs a value for an open
  slot, return a clarification request instead of guessing. Set
  "archetype" in the spec to the context's archetype id (or "custom"
  when there is no context block).
- Give every component a short "purpose": why that part is in this
  project (e.g. "Switches the ventilation fan").

EXAMPLE - user: "Create a smart greenhouse monitoring system. Monitor
temperature and humidity, control a ventilation fan when temperature
exceeds 32C, and activate an alarm when humidity falls below 40%."
{{"status": "ok", "spec": {{"name": "smart-greenhouse-monitor",
"title": "Smart Greenhouse Monitoring System", "board": "esp32",
"components": [
  {{"id": "sensor1", "type": "dht22",
    "label": "DHT22 temperature/humidity sensor", "pins": {{"data": 4}}}},
  {{"id": "fan", "type": "relay",
    "label": "Relay module - ventilation fan", "pins": {{"control": 5}}}},
  {{"id": "alarm", "type": "led",
    "label": "LED - low-humidity alarm", "pins": {{"control": 18}}}}],
"rules": [
  {{"metric": "temperature", "op": ">", "threshold": 32, "unit": "C",
    "actuator": "fan"}},
  {{"metric": "humidity", "op": "<", "threshold": 40, "unit": "%",
    "actuator": "alarm"}}],
"sampling_interval_s": 2}}}}
"""


def validate_spec(spec):
    """The Registry Validator. Returns a list of human-readable problems
    (empty list = the spec is buildable with the supported palette).
    This is plain code on purpose: the AI proposes, the code decides."""
    errors = []
    if not isinstance(spec, dict):
        return ["The specification is not a JSON object."]

    name = spec.get("name")
    if not isinstance(name, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{2,40}", name or ""):
        errors.append("spec.name must be a slug of 3-41 chars: lowercase "
                      "letters, digits and hyphens, e.g. 'greenhouse-fan'.")
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 90:
        errors.append("spec.title must be a non-empty title (max 90 chars).")
    board = spec.get("board")
    if not isinstance(board, str) or board.lower() != "esp32":
        errors.append("spec.board must be 'esp32' - it is the only "
                      "supported board.")
    schema_version = spec.get("schema_version", 1)
    if schema_version not in (1, 2):
        errors.append("spec.schema_version must be 1 or 2 - this "
                      "engine writes version 2 and cannot read "
                      "newer specification formats.")
    archetype = spec.get("archetype")
    if archetype is not None and archetype != "custom" \
            and archetype not in ARCHETYPES:
        errors.append("spec.archetype must be a known project "
                      "archetype id (or 'custom').")
    interval = spec.get("sampling_interval_s")
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) \
            or not (1 <= float(interval) <= 60):
        errors.append("spec.sampling_interval_s must be a number "
                      "between 1 and 60.")

    components = spec.get("components")
    if not isinstance(components, list) or not (1 <= len(components) <= 6):
        errors.append("spec.components must be a list of 1 to 6 components.")
        components = []
    by_id = {}
    used_pins = {}
    sensor_metrics = set()
    dht_count = 0
    for comp in components:
        if not isinstance(comp, dict):
            errors.append("Every component must be an object.")
            continue
        cid = comp.get("id")
        if not isinstance(cid, str) or not re.fullmatch(
                r"[a-z][a-z0-9_]{0,15}", cid or ""):
            errors.append(f"Component id {cid!r} is invalid: use a short "
                          "lowercase identifier like 'sensor1' or 'fan'.")
            continue
        if cid in by_id:
            errors.append(f"Component id '{cid}' is used twice.")
            continue
        by_id[cid] = comp
        ctype = comp.get("type")
        if ctype not in REGISTRY:
            errors.append(f"Component '{cid}' has type {ctype!r}, which is "
                          "not in the supported registry ("
                          + ", ".join(REGISTRY) + "). Do not invent "
                          "hardware - if the user's "
                          "project truly needs it, answer with a "
                          "clarification request instead.")
            continue
        info = REGISTRY[ctype]
        purpose = comp.get("purpose")
        if purpose is not None and (not isinstance(purpose, str)
                                    or not purpose.strip()
                                    or len(purpose) > 120):
            errors.append(f"Component '{cid}': purpose must be a "
                          "short non-empty string (max 120 chars).")
        if ctype == "dht22":
            dht_count += 1
        sensor_metrics.update(info["metrics"])
        pins = comp.get("pins")
        if not isinstance(pins, dict) or set(pins) != set(info["pin_roles"]):
            errors.append(f"Component '{cid}' ({ctype}) must have pins "
                          f"exactly {info['pin_roles']}.")
            continue
        for role, pin in pins.items():
            if not isinstance(pin, int) or isinstance(pin, bool) \
                    or not (0 <= pin <= 39):
                errors.append(f"Component '{cid}' pin {role}={pin!r} is "
                              "not a valid GPIO number (0-39).")
                continue
            if pin in FORBIDDEN_PINS:
                errors.append(f"GPIO {pin} (component '{cid}') may not be "
                              "used: GPIO 0, 1, 2, 3, 12, 15 are reserved "
                              "(UART / boot strapping). Pick another pin.")
            if info["kind"] == "actuator" and pin in INPUT_ONLY_PINS:
                errors.append(f"GPIO {pin} (component '{cid}') is "
                              "input-only (GPIO 34-39) and cannot drive "
                              "an output. Pick another pin.")
            if pin in used_pins:
                errors.append(f"GPIO {pin} is assigned twice "
                              f"('{used_pins[pin]}' and '{cid}'). "
                              "Pins must be unique.")
            used_pins.setdefault(pin, cid)
    if dht_count > 1:
        errors.append("At most ONE dht22 sensor per project in this "
                      "version (the serial contract reports one "
                      "temperature/humidity pair per cycle).")
    kinds = {REGISTRY[c["type"]]["kind"] for c in by_id.values()
             if c.get("type") in REGISTRY}
    if components and "sensor" not in kinds:
        errors.append("The project has no sensor - rules need something "
                      "to measure.")
    if components and "actuator" not in kinds:
        errors.append("The project has no actuator (relay or led) - "
                      "nothing reacts to the rules.")

    rules = spec.get("rules")
    if not isinstance(rules, list) or not (1 <= len(rules) <= 4):
        errors.append("spec.rules must be a list of 1 to 4 rules.")
        rules = []
    ruled_actuators = set()
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            errors.append(f"Rule {index} is not an object.")
            continue
        metric = rule.get("metric")
        if metric not in METRIC_RANGES:
            errors.append(f"Rule {index}: metric {metric!r} is not "
                          "supported (temperature or humidity).")
            continue
        if metric not in sensor_metrics:
            errors.append(f"Rule {index}: no sensor in the project "
                          f"measures {metric}.")
        if rule.get("op") not in (">", "<"):
            errors.append(f"Rule {index}: op must be '>' or '<'.")
        threshold = rule.get("threshold")
        low, high, unit = METRIC_RANGES[metric]
        if not isinstance(threshold, (int, float)) \
                or isinstance(threshold, bool) \
                or not (low <= float(threshold) <= high):
            errors.append(f"Rule {index}: threshold for {metric} must be "
                          f"a number between {fmt_num(low)} and "
                          f"{fmt_num(high)} {unit}.")
        unit_given = rule.get("unit")
        if unit_given is not None and unit_given != unit:
            errors.append(f"Rule {index}: unit for {metric} must be "
                          f"'{unit}'.")
        actuator = rule.get("actuator")
        target = by_id.get(actuator) if isinstance(actuator, str) else None
        if target is None:
            errors.append(f"Rule {index}: actuator {actuator!r} is not "
                          "one of the component ids.")
        elif target.get("type") in REGISTRY and \
                REGISTRY[target["type"]]["kind"] != "actuator":
            errors.append(f"Rule {index}: '{actuator}' is a sensor, not "
                          "an actuator - a rule must drive a relay or led.")
        else:
            ruled_actuators.add(actuator)
    for cid, comp in by_id.items():
        if comp.get("type") in REGISTRY and \
                REGISTRY[comp["type"]]["kind"] == "actuator" \
                and cid not in ruled_actuators:
            errors.append(f"Actuator '{cid}' has no rule - every actuator "
                          "must be driven by at least one rule (or be "
                          "removed from the project).")
    return errors


def finalize_spec(spec, archetype=None):
    """Normalize a validated spec in place and return it: canonical
    numbers/units, the serial print-name of every actuator
    (RELAY, RELAY2, LED, ...), its archetype stamp and a purpose for
    every component (the Spec Agent's own words, or the registry
    descriptor's purpose when the agent omitted one). Deterministic -
    the firmware prompt and the test generator both read these names
    from the spec."""
    spec["schema_version"] = 2
    if archetype:
        spec["archetype"] = archetype
    elif not spec.get("archetype"):
        spec["archetype"] = "custom"
    spec["board"] = "esp32"
    spec["sampling_interval_s"] = int(round(float(
        spec.get("sampling_interval_s", 2))))
    counts = {}
    for comp in spec["components"]:
        info = REGISTRY[comp["type"]]
        if not comp.get("label"):
            comp["label"] = info["label"]
        if not comp.get("purpose"):
            comp["purpose"] = info["purpose"]
        if info["kind"] == "actuator":
            base = info["print_base"]
            counts[base] = counts.get(base, 0) + 1
            comp["print_name"] = base if counts[base] == 1 \
                else f"{base}{counts[base]}"
    for rule in spec["rules"]:
        rule["threshold"] = float(rule["threshold"])
        rule["unit"] = METRIC_RANGES[rule["metric"]][2]
    return spec


def unsupported_types_in(spec):
    """Component types in a (possibly invalid) spec that the registry
    does not know - the raw material for a clarification outcome."""
    found = []
    if isinstance(spec, dict):
        for comp in spec.get("components") or []:
            if isinstance(comp, dict) and comp.get("type") not in REGISTRY:
                found.append(str(comp.get("type")))
    return sorted(set(found))


def clarification_for_unsupported(types):
    questions = []
    for ctype in types:
        questions.append(
            f"'{ctype}' is not in the supported palette yet (ESP32 + "
            "DHT22 sensor, relay module, LED, active buzzer). You can "
            "leave it out, or map it onto the palette - a fan, pump "
            "or lamp is switched by the relay, an alarm is shown by "
            "the LED or sounded by the buzzer. How would you like "
            "to handle it?")
    return {
        "questions": questions or [
            "Some part of this project is outside the supported palette "
            "(ESP32 + DHT22, relay, LED, buzzer). Could you describe "
            "it using only those parts?"],
        "unsupported": types,
    }


# --- Offline stub Spec Agent (only for --stub; deterministic) -----------

_STUB_UNSUPPORTED_WORDS = (
    "soil", "ultrasonic", "servo", "gps", "camera",
    "distance sensor", "ph sensor", "water level", "stepper",
    "rfid", "gas sensor", "mq-2", "mq2",
)


def _stub_alert_spec(name, title, threshold, alarm_choice):
    """A one-sensor alert spec for the stub path (industrial
    monitoring, fire safety), built from FILLED slot values - the
    stub mirrors the live contract, it does not invent numbers."""
    components = [
        {"id": "sensor1", "type": "dht22",
         "label": "DHT22 temperature/humidity sensor",
         "pins": {"data": 4}},
    ]
    rules = []
    choices = ["led", "buzzer"] if alarm_choice == "both" \
        else [alarm_choice or "buzzer"]
    for i, ctype in enumerate(choices):
        cid = "alarm" if i == 0 else "alarm2"
        components.append({
            "id": cid, "type": ctype,
            "label": "Active buzzer - alert" if ctype == "buzzer"
                     else "LED - alert",
            "pins": {"control": 18 + i}})
        rules.append({"metric": "temperature", "op": ">",
                      "threshold": threshold, "unit": "C",
                      "actuator": cid})
    return {"name": name, "title": title, "board": "esp32",
            "components": components, "rules": rules,
            "sampling_interval_s": 2}


def stub_spec_response(requirement):
    """Deterministic stand-in for the Spec Agent, used by --stub so
    the whole pipeline can be exercised offline. Since I7 the routing
    runs through the SAME classifier and slot analyzer as the live
    path: archetypes with blockers or open required slots clarify
    from the slot templates, filled archetypes return their stub
    spec, and 'custom' keeps the classic keyword routing."""
    text = (requirement or "").lower()
    archetype_id, _score, _scores = classify_intent(requirement)
    if archetype_id in ARCHETYPES:
        filled, open_slots = analyze_slots(requirement, archetype_id)
        blocker = intent_blocker(archetype_id, filled, open_slots)
        if blocker is not None:
            return {"status": "needs_clarification", **blocker}
        if open_slots:
            return {"status": "needs_clarification",
                    **slot_clarification(archetype_id, open_slots,
                                         filled)}
        if archetype_id == "greenhouse":
            wants_sound = any(word in text for word in
                              ("buzzer", "sound", "audible", "beep"))
            spec = GREENHOUSE_BUZZER_SPEC if wants_sound \
                else GREENHOUSE_SPEC
            return {"status": "ok", "spec": copy.deepcopy(spec),
                    "archetype": archetype_id}
        if archetype_id == "industrial_monitoring":
            return {"status": "ok",
                    "spec": _stub_alert_spec(
                        "industrial-temperature-monitor",
                        "Industrial Temperature Monitoring",
                        filled["temp_threshold"],
                        filled.get("alarm_actuator")),
                    "archetype": archetype_id}
        if archetype_id == "fire_safety":
            return {"status": "ok",
                    "spec": _stub_alert_spec(
                        "fire-heat-alarm", "Fire Heat Alarm",
                        filled["temp_threshold"],
                        filled.get("alarm_actuator")),
                    "archetype": archetype_id}
    hits = [word for word in _STUB_UNSUPPORTED_WORDS if word in text]
    if hits:
        return {
            "status": "needs_clarification",
            "questions": [
                f"The request mentions '{hits[0]}', which is not in the "
                "supported palette yet (ESP32 + DHT22 sensor, relay "
                "module, LED, active buzzer). Should I leave it out, "
                "or map it onto the palette (relay switches loads, "
                "LED/buzzer signal alarms)?"],
            "unsupported": hits,
        }
    if any(word in text for word in ("buzzer", "sound", "audible", "beep")):
        if "humidity" in text:
            return {"status": "ok",
                    "spec": copy.deepcopy(GREENHOUSE_BUZZER_SPEC)}
        return {"status": "ok", "spec": copy.deepcopy(BUZZER_ALARM_SPEC)}
    if "humidity" in text and ("fan" in text or "alarm" in text):
        return {"status": "ok", "spec": copy.deepcopy(GREENHOUSE_SPEC)}
    return {"status": "ok", "spec": copy.deepcopy(CLASSIC_SPEC)}


def get_spec(requirement, stub=False):
    """Run the Spec Agent (+ validator + repair rounds).

    Returns (spec, clarification, spec_rounds): exactly one of spec /
    clarification is not None. Raises RuntimeError when the agent
    cannot converge on a valid spec for reasons that are not the
    user's to clarify."""
    if not requirement:
        return finalize_spec(copy.deepcopy(CLASSIC_SPEC)), None, 0
    if stub:
        reply_obj = stub_spec_response(requirement)
        if reply_obj.get("status") == "needs_clarification":
            return None, {"questions": reply_obj.get("questions") or [],
                          "unsupported": reply_obj.get("unsupported") or [],
                          "slots": reply_obj.get("slots") or [],
                          "archetype": reply_obj.get("archetype")}, 1
        spec = reply_obj["spec"]
        problems = validate_spec(spec)
        if problems:  # a stub spec must be valid by construction
            raise RuntimeError("Internal error: stub spec invalid: "
                               + "; ".join(problems))
        return finalize_spec(
            spec, archetype=reply_obj.get("archetype")), None, 1

    # I7 understanding layer: classify + slot analysis in code. A
    # blocking archetype (missing registry hardware) clarifies
    # immediately from the slot templates - no LLM round is spent on
    # a project that cannot be built today.
    archetype_id, _score, _scores = classify_intent(requirement)
    arch_known = archetype_id in ARCHETYPES
    filled, open_slots = {}, []
    if arch_known:
        filled, open_slots = analyze_slots(requirement, archetype_id)
        blocker = intent_blocker(archetype_id, filled, open_slots)
        if blocker is not None:
            event(event="intent_ready", archetype=archetype_id,
                  blocked=True)
            return None, blocker, 1
        event(event="intent_ready", archetype=archetype_id,
              filled=sorted(filled),
              open=[s["id"] for s in open_slots])

    user_content = f"User's project description:\n{requirement}\n"
    if arch_known:
        user_content += archetype_context_block(archetype_id, filled,
                                                open_slots)
    user_content += "\nReturn the JSON now."
    messages = [
        {"role": "system", "content": SPEC_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    last_spec, last_errors = None, []
    for round_no in range(1, MAX_SPEC_ROUNDS + 1):
        event(event="spec_attempt", round=round_no,
              max_rounds=MAX_SPEC_ROUNDS)
        reply = call_llm(messages, temperature=0.1)
        parsed = extract_json(reply)
        if parsed is None:
            last_errors = ["The reply was not a JSON object."]
        elif parsed.get("status") == "needs_clarification":
            questions = [str(q) for q in
                         (parsed.get("questions") or []) if str(q).strip()]
            if not questions:
                questions = ["Could you describe the project using only "
                             "the supported parts (ESP32, DHT22, relay, "
                             "LED, buzzer), with a clear number for each "
                             "threshold?"]
            unsupported = [str(u) for u in
                           (parsed.get("unsupported") or [])]
            if arch_known and open_slots:
                # Slot-template questions first (guaranteed
                # project-specific), then the agent's residual ones.
                templates = [s["question"] for s in open_slots]
                merged = templates + [q for q in questions
                                      if q not in templates]
                return None, {
                    "questions": merged[:5],
                    "unsupported": unsupported,
                    "slots": [_slot_record(s) for s in open_slots],
                    "archetype": archetype_id,
                }, round_no
            return None, {
                "questions": questions,
                "unsupported": unsupported,
                "slots": [],
                "archetype": archetype_id if arch_known else None,
            }, round_no
        else:
            last_spec = parsed.get("spec", parsed)
            last_errors = validate_spec(last_spec)
            if not last_errors:
                if arch_known:
                    bad = post_check_slots(last_spec, archetype_id,
                                           open_slots)
                    if bad:
                        # The agent filled an open slot without the
                        # user stating it - convert to the slot
                        # clarification instead of building a guess.
                        return None, slot_clarification(
                            archetype_id, bad, filled), round_no
                return finalize_spec(
                    last_spec,
                    archetype=archetype_id if arch_known else None), \
                    None, round_no
        if round_no < MAX_SPEC_ROUNDS:
            messages.append({"role": "assistant", "content": reply})
            messages.append({
                "role": "user",
                "content": "That specification is not buildable yet. "
                           "Fix these problems and return the complete "
                           "corrected JSON (or a clarification request "
                           "if the project cannot be built with the "
                           "palette):\n- " + "\n- ".join(last_errors),
            })
    unknown = unsupported_types_in(last_spec)
    if unknown:
        # The agent kept asking for hardware that does not exist in the
        # palette. That is the user's decision to make - ask, never invent.
        clar = clarification_for_unsupported(unknown)
        clar["slots"] = []
        clar["archetype"] = archetype_id if arch_known else None
        return None, clar, MAX_SPEC_ROUNDS
    raise RuntimeError(
        "The Spec Agent could not produce a valid specification after "
        f"{MAX_SPEC_ROUNDS} rounds: " + "; ".join(last_errors))


# --------------------------------------------------------------------------
# Test Generator - derives the simulator profile and the expected
# behaviour from the spec ALONE (never from the firmware), so a wrong
# firmware cannot produce matching wrong tests.
# --------------------------------------------------------------------------


def rule_fires(rule, value):
    return value > rule["threshold"] if rule["op"] == ">" \
        else value < rule["threshold"]


def _calm_value(metric, rules_for_metric):
    """A value for `metric` at which NONE of its rules fire."""
    delta = PROFILE_DELTA[metric]
    candidates = [
        r["threshold"] - delta if r["op"] == ">" else r["threshold"] + delta
        for r in rules_for_metric
    ]
    for candidate in candidates:
        if not any(rule_fires(r, candidate) for r in rules_for_metric):
            return candidate
    thresholds = [r["threshold"] for r in rules_for_metric]
    return (min(thresholds) + max(thresholds)) / 2.0


def _fire_value(rule):
    """A value for the rule's metric at which the rule definitely fires,
    clamped inside the sensor's sane range."""
    metric = rule["metric"]
    delta = PROFILE_DELTA[metric]
    value = rule["threshold"] + delta if rule["op"] == ">" \
        else rule["threshold"] - delta
    low, high = METRIC_PROFILE_CLAMP[metric]
    return max(low, min(high, value))


def synthesize_profile(spec):
    """Timeline of sensor points that crosses every rule's threshold in
    both directions: calm -> each rule firing alone -> all rules firing
    together -> calm again."""
    by_metric = {}
    for rule in spec["rules"]:
        by_metric.setdefault(rule["metric"], []).append(rule)
    calm = {}
    for metric in ("temperature", "humidity"):
        rules = by_metric.get(metric)
        calm[metric] = _calm_value(metric, rules) if rules \
            else NEUTRAL_POINT[metric]
    points = [dict(calm)]
    for rule in spec["rules"]:
        point = dict(calm)
        point[rule["metric"]] = _fire_value(rule)
        points.append(point)
    if len(spec["rules"]) > 1:
        point = dict(calm)
        for rule in spec["rules"]:
            point[rule["metric"]] = _fire_value(rule)
        points.append(point)
    points.append(dict(calm))
    return points


def expected_states(spec, point):
    """The rule engine: for one sensor point, the state every actuator
    must be in (ON if any of its rules fires - OR semantics)."""
    states = {}
    for comp in spec["components"]:
        if REGISTRY[comp["type"]]["kind"] != "actuator":
            continue
        states[comp["id"]] = any(
            rule_fires(rule, point[rule["metric"]])
            for rule in spec["rules"] if rule["actuator"] == comp["id"])
    return states


def _actuators(spec):
    return [c for c in spec["components"]
            if REGISTRY[c["type"]]["kind"] == "actuator"]


def _sensor(spec):
    return next(c for c in spec["components"] if c["type"] == "dht22")


def _compress(sequence):
    out = []
    for value in sequence:
        if not out or out[-1] != value:
            out.append(value)
    return out


_READING_RE = re.compile(r"TEMP:\s*(-?\d+(?:\.\d+)?)\s+HUM:\s*(\d+(?:\.\d+)?)")
_STATE_RE = re.compile(r"^([A-Z][A-Z0-9_]*):(ON|OFF)$")


def evaluate_spec(code, transcript, pin_events, spec, points):
    """The behavioural verdict, from the spec-derived expectations.
    Asserts on the serial transcript AND on actual pin activity."""
    failures = []

    if "Traceback (most recent call last)" in transcript:
        tail = [ln for ln in transcript.splitlines() if ln.strip()]
        failures.append("Runtime error in the simulated run: "
                        + (tail[-1] if tail else "unknown traceback"))
    if "HADE_BOOT" not in transcript:
        failures.append("Boot marker HADE_BOOT was never printed.")

    cycles = []
    current = None
    for raw in transcript.splitlines():
        line = raw.strip()
        match = _READING_RE.search(line)
        if match:
            current = {"temp": float(match.group(1)),
                       "hum": float(match.group(2)), "states": {}}
            cycles.append(current)
            continue
        state_match = _STATE_RE.match(line)
        if state_match and current is not None:
            current["states"][state_match.group(1)] = state_match.group(2)

    expected = [expected_states(spec, point) for point in points]
    if len(cycles) < len(points):
        failures.append(
            f"Only {len(cycles)} structured TEMP/HUM readings in the "
            f"simulated run (expected at least {len(points)}, one per "
            "test point). Readings must be printed exactly as "
            "TEMP:<t> HUM:<h> every cycle.")

    for index in range(min(len(cycles), len(points))):
        point = points[index]
        for comp in _actuators(spec):
            name = comp["print_name"]
            want_on = expected[index][comp["id"]]
            got = cycles[index]["states"].get(name)
            where = (f"cycle {index + 1} (simulated temperature "
                     f"{fmt_num(point['temperature'])} C, humidity "
                     f"{fmt_num(point['humidity'])}%)")
            if got is None:
                failures.append(f"In {where}: {name} state was never "
                                "printed. Print "
                                f"{name}:ON or {name}:OFF every cycle.")
            elif (got == "ON") != want_on:
                if want_on:
                    firing = "; ".join(
                        f"{r['metric']} {r['op']} "
                        f"{fmt_num(r['threshold'])}{r['unit']}"
                        for r in spec["rules"]
                        if r["actuator"] == comp["id"]
                        and rule_fires(r, point[r["metric"]]))
                    reason = f"rule active: {firing}"
                else:
                    reason = "no rule for it is active at this point"
                failures.append(
                    f"In {where}: expected {name}:"
                    f"{'ON' if want_on else 'OFF'} ({reason}) but the "
                    f"firmware printed {name}:{got}.")

    total_writes = 0
    for comp in _actuators(spec):
        pin = comp["pins"]["control"]
        name = comp["print_name"]
        writes = [e["value"] for e in pin_events if e["pin"] == pin]
        total_writes += len(writes)
        want_seq = [1 if expected[i][comp["id"]] else 0
                    for i in range(len(points))]
        if not writes:
            failures.append(f"GPIO {pin} ({name}) was never written - "
                            "the actuator is not actually being driven.")
        elif len(writes) >= len(points):
            if writes[:len(points)] != want_seq:
                bad = next(i for i in range(len(points))
                           if writes[i] != want_seq[i])
                failures.append(
                    f"GPIO {pin} ({name}) was driven wrong in cycle "
                    f"{bad + 1}: expected "
                    f"{'HIGH' if want_seq[bad] else 'LOW'} from the spec "
                    f"but the pin went "
                    f"{'HIGH' if writes[bad] else 'LOW'}.")
        elif _compress(writes) != _compress(want_seq) \
                or writes[-1] != want_seq[-1]:
            failures.append(
                f"GPIO {pin} ({name}) write sequence {writes} does not "
                f"match the expected behaviour {want_seq} (the pin must "
                "follow the rules at every test point).")

    stats = {"readings": len(cycles), "pin_writes": total_writes,
             "cycles_expected": len(points)}
    return failures, stats


def syntax_check(code):
    """Compile-check without executing. Returns an error string or None."""
    try:
        compile(code, "main.py", "exec")
        return None
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"


# --------------------------------------------------------------------------
# Simulated hardware: stub `machine` / `dht` / `utime` modules.
# The generated MicroPython runs UNMODIFIED under desktop Python against
# these stubs. The stubs log every GPIO write to a pin-log file so
# behaviour can be asserted on the actual pin activity, not only on
# what the code claims in its prints.
# --------------------------------------------------------------------------

MACHINE_STUB = '''
import json, os

_LOG = os.environ.get("HADE_PINLOG", "pinlog.jsonl")

class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    PULL_DOWN = 3

    def __init__(self, number, mode=None, pull=None, value=None):
        self.number = number
        self._value = 0
        if value is not None:
            self.value(value)

    def value(self, v=None):
        if v is None:
            return self._value
        self._value = 1 if v else 0
        self._record()
        return None

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)

    def _record(self):
        try:
            with open(_LOG, "a") as handle:
                handle.write(json.dumps(
                    {"pin": self.number, "value": self._value}) + "\\n")
        except Exception:
            pass
'''

DHT_STUB = '''
import os

_profile = [float(x) for x in
            os.environ.get("HADE_TEMP_PROFILE", "27,33,27").split(",")]
_hum = [float(x) for x in
        os.environ.get("HADE_HUM_PROFILE", "55,55,55").split(",")]
_count = [0]

class DHT22:
    def __init__(self, pin):
        self.pin = pin
        self._t = None
        self._h = None

    def measure(self):
        i = _count[0]
        self._t = _profile[min(i, len(_profile) - 1)]
        self._h = _hum[min(i, len(_hum) - 1)]
        _count[0] += 1

    def temperature(self):
        return self._t

    def humidity(self):
        return self._h

class DHT11(DHT22):
    pass
'''

UTIME_STUB = '''
import time as _time

def sleep(s):
    _time.sleep(s)

def sleep_ms(ms):
    _time.sleep(ms / 1000.0)

def sleep_us(us):
    _time.sleep(us / 1000000.0)

def ticks_ms():
    return int(_time.time() * 1000)

def ticks_diff(a, b):
    return a - b
'''


def run_simulation(code, points, interval_s):
    """Run generated firmware against the stub hardware, time-boxed
    (firmware loops forever by design). Returns
    (transcript, pin_events, run_seconds)."""
    run_seconds = int(round((len(points) + 1.5) * interval_s)) + 3
    tmp = tempfile.mkdtemp(prefix="hade_sim_")
    pinlog = os.path.join(tmp, "pinlog.jsonl")
    for name, content in (("machine.py", MACHINE_STUB),
                          ("dht.py", DHT_STUB),
                          ("utime.py", UTIME_STUB),
                          ("main.py", code)):
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["HADE_PINLOG"] = pinlog
    env["HADE_TEMP_PROFILE"] = ",".join(
        fmt_num(p["temperature"]) for p in points)
    env["HADE_HUM_PROFILE"] = ",".join(
        fmt_num(p["humidity"]) for p in points)

    try:
        proc = subprocess.run(
            [sys.executable, "main.py"],
            cwd=tmp, env=env, timeout=run_seconds,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        transcript = proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        out = exc.output or ""
        transcript = out.decode("utf-8", "replace") \
            if isinstance(out, bytes) else out

    pin_events = []
    if os.path.exists(pinlog):
        with open(pinlog, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    pin_events.append(json.loads(line))
    return transcript, pin_events, run_seconds


# --------------------------------------------------------------------------
# Firmware Generator - prompt built FROM the validated spec
# --------------------------------------------------------------------------


def spec_brief(spec):
    """The spec as compact human text (used in prompts and reports)."""
    lines = [f"Project: {spec['title']} (board: ESP32, read every "
             f"{spec['sampling_interval_s']}s)"]
    for comp in spec["components"]:
        pins = ", ".join(f"{role} GPIO {pin}"
                         for role, pin in comp["pins"].items())
        lines.append(f"- {comp['label']} [{comp['type']} id={comp['id']}]: "
                     f"{pins}")
    for rule in spec["rules"]:
        target = next(c for c in spec["components"]
                      if c["id"] == rule["actuator"])
        lines.append(f"- Rule: {target['print_name']} "
                     f"({target['label']}) ON when {rule['metric']} "
                     f"{rule['op']} {fmt_num(rule['threshold'])}"
                     f"{rule['unit']}, otherwise OFF")
    return "\n".join(lines)


def firmware_system_prompt(spec):
    sensor = _sensor(spec)
    constructions = [
        f"- Create the sensor exactly as: "
        f"dht.DHT22(machine.Pin({sensor['pins']['data']}))"]
    state_lines = []
    for comp in _actuators(spec):
        constructions.append(
            f"- Create '{comp['id']}' ({comp['label']}) exactly as: "
            f"machine.Pin({comp['pins']['control']}, machine.Pin.OUT)")
        conditions = " or ".join(
            f"{r['metric']} {r['op']} {fmt_num(r['threshold'])}{r['unit']}"
            for r in spec["rules"] if r["actuator"] == comp["id"])
        state_lines.append(
            f"- {comp['print_name']} must be ON when {conditions}, "
            f"otherwise OFF. Every cycle: set its pin accordingly "
            f"(value(1) = ON) and print exactly "
            f"{comp['print_name']}:ON or {comp['print_name']}:OFF.")
    interval = spec["sampling_interval_s"]
    return f"""You are the Firmware Compiler Agent of H-ADE, a principal
embedded-systems engineer. You write MicroPython firmware for ESP32
that implements EXACTLY the validated specification below. You never
invent pins, peripherals, rules or APIs.

{REGISTRY_TEXT}

VALIDATED SPECIFICATION:
{spec_brief(spec)}

CONSTRUCTION:
{chr(10).join(constructions)}

OUTPUT CONTRACT - your reply must contain ONLY one MicroPython code block:
- Use ONLY the modules: machine, dht, time.
- Print HADE_BOOT once at startup.
- Every read cycle: call sensor.measure(), then print exactly
  TEMP:<temp with 1 decimal> HUM:<humidity with 1 decimal>
  (example: TEMP:31.0 HUM:57.0)
{chr(10).join(state_lines)}
- Sleep exactly {interval} seconds between cycles with
  time.sleep({interval}).
- Wrap the sensor read in try/except and continue the loop on read
  errors (real DHT sensors fail individual reads; the firmware must
  not crash).
- No explanations, no markdown headings - code only.
"""


def build_repair_message(spec, code, problems):
    return (
        "Your previous firmware FAILED validation. Fix it and return "
        "the complete corrected file, code only.\n\n"
        "Validated specification:\n" + spec_brief(spec) + "\n\n"
        "Problems found:\n- " + "\n- ".join(problems) + "\n\n"
        "Your previous code:\n```python\n" + code + "```"
    )


# --------------------------------------------------------------------------
# Offline stub firmware generator (--stub only). Deterministic code
# generated FROM the spec, with a realistic sabotage on attempt 1:
# multi-rule projects lose every rule but the first (the classic
# real-world miss); single-rule projects get the sensor.read() API bug
# from engine v1/v2. Attempt 2+ is always correct.
# --------------------------------------------------------------------------


def _stub_code_for_spec(spec, sabotage=None):
    sensor = _sensor(spec)
    lines = ["import machine", "import dht", "import time", ""]
    lines.append(f"sensor = dht.DHT22(machine.Pin({sensor['pins']['data']}))")
    for comp in _actuators(spec):
        lines.append(f"{comp['id']} = machine.Pin("
                     f"{comp['pins']['control']}, machine.Pin.OUT)")
    lines += ["", 'print("HADE_BOOT")', "", "while True:", "    try:"]
    lines.append("        sensor.read()" if sabotage == "read_bug"
                 else "        sensor.measure()")
    lines.append("        t = sensor.temperature()")
    lines.append("        h = sensor.humidity()")
    lines.append('        print("TEMP:{:.1f} HUM:{:.1f}".format(t, h))')
    covered = spec["rules"][:1] if sabotage == "first_rule_only" \
        else spec["rules"]
    for comp in _actuators(spec):
        conds = []
        for rule in covered:
            if rule["actuator"] == comp["id"]:
                var = "t" if rule["metric"] == "temperature" else "h"
                conds.append(f"{var} {rule['op']} "
                             f"{fmt_num(rule['threshold'])}")
        expr = " or ".join(conds) if conds else "False"
        lines.append(f"        {comp['id']}_on = {expr}")
        lines.append(f"        {comp['id']}.value(1 if {comp['id']}_on "
                     "else 0)")
        lines.append('        print("{}:" + ("ON" if {}_on else '
                     '"OFF"))'.format(comp["print_name"], comp["id"]))
    lines.append("    except Exception as e:")
    lines.append('        print("READ_ERROR", e)')
    lines.append(f"    time.sleep({spec['sampling_interval_s']})")
    return "\n".join(lines) + "\n"


def stub_generate(spec, attempt):
    if attempt == 1:
        if len(spec["rules"]) > 1:
            return _stub_code_for_spec(spec, sabotage="first_rule_only")
        return _stub_code_for_spec(spec, sabotage="read_bug")
    return _stub_code_for_spec(spec)


# --------------------------------------------------------------------------
# Bundle writers - every artifact is generated FROM the validated spec,
# and every claim carries its validation level.
# --------------------------------------------------------------------------


def hardware_spec_view(spec, user_requirement, passed):
    """hardware.json - the derived view of project.json in the shape the
    ConvoGen dashboard already renders (components + pin map +
    parameters)."""
    components = []
    for comp in spec["components"]:
        info = REGISTRY[comp["type"]]
        role_key = next(iter(comp["pins"])) + "_pin"
        pin = next(iter(comp["pins"].values()))
        notes = info["hw_notes"].replace(
            "{print_name}", comp.get("print_name", ""))
        components.append({
            "name": comp["label"],
            "role": REGISTRY[comp["type"]]["kind"],
            "connection": {role_key: pin},
            "notes": notes,
        })
    temp_rule = next((r for r in spec["rules"]
                      if r["metric"] == "temperature"), None)
    return {
        "project": spec["name"],
        "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "board": "ESP32 development board (3.3V logic)",
        "user_requirement": user_requirement or None,
        "spec_file": "project.json",
        "components": components,
        "parameters": {
            "threshold_c": temp_rule["threshold"] if temp_rule else None,
            "interval_s": spec["sampling_interval_s"],
        },
        "validation": {
            "firmware": "digitally_validated" if passed else "generated",
            "physically_verified": False,
        },
    }


def sim_config_view(spec, points, run_seconds):
    assertions = [
        "no traceback in the serial transcript",
        "HADE_BOOT printed once at startup",
        f"at least {len(points)} structured TEMP:<t> HUM:<h> readings "
        "(one per test point)",
    ]
    for comp in _actuators(spec):
        assertions.append(
            f"{comp['print_name']} printed state matches the spec rules "
            "at every test point")
        assertions.append(
            f"GPIO {comp['pins']['control']} pin activity matches the "
            f"expected {comp['print_name']} sequence (asserted on pin "
            "activity, not prints)")
    return {
        "simulator": "hade-stub-harness (generated MicroPython runs "
                     "unmodified against stub machine/dht modules)",
        "run_seconds": run_seconds,
        "profile": [{"temperature": p["temperature"],
                     "humidity": p["humidity"]} for p in points],
        "rules": spec["rules"],
        "interval_s": spec["sampling_interval_s"],
        "assertions": assertions,
    }


def _price_text(price_range):
    low, high = price_range
    return f"Rs {low}" if low == high else f"Rs {low}-{high}"


def write_bom(bundle_dir, spec):
    counts = {}
    for comp in spec["components"]:
        counts[comp["type"]] = counts.get(comp["type"], 0) + 1
    rows = list(BOM_BASE)
    for ctype, qty in counts.items():
        name, _one, price, note = BOM_BY_TYPE[ctype]
        rows.append((name, qty, price, note))
    total_low = sum(price[0] * qty for _n, qty, price, _t in rows)
    total_high = sum(price[1] * qty for _n, qty, price, _t in rows)
    lines = [f"# Bill of Materials - {spec['name']}", ""]
    lines.append("| # | Component | Qty | Indicative price (INR) | Notes |")
    lines.append("|---|-----------|-----|------------------------|-------|")
    for i, (name, qty, price, note) in enumerate(rows, start=1):
        lines.append(f"| {i} | {name} | {qty} | {_price_text(price)} "
                     f"| {note} |")
    lines.append("")
    lines.append(f"Indicative total: roughly Rs {total_low}-{total_high}.")
    lines.append("")
    lines.append("Prices are indicative Indian market ranges (Robu, "
                 "Robocraze, Amazon.in class sellers). Verify the current "
                 "price before ordering - they move often.")
    lines.append("")
    lines.append("Validation: **Generated** (component list derived from "
                 "the validated specification; prices not verified live).")
    with open(os.path.join(bundle_dir, "bom.md"), "w",
              encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def write_wiring(bundle_dir, spec):
    lines = [f"# Wiring - {spec['name']}", ""]
    for comp in spec["components"]:
        lines.append(f"## {comp['label']}")
        for template in REGISTRY[comp["type"]]["wiring"]:
            lines.append(template.format(**comp["pins"]))
        lines.append("")
    lines.append("## Safety")
    lines.append("- Keep any relay's load side (COM/NO/NC) UNWIRED while "
                 "testing firmware.")
    lines.append("- A relay can switch mains voltage, which can kill. "
                 "Until you are experienced, switch only low-voltage DC "
                 "loads (LED strips, small fans, pumps under 24V).")
    lines.append("")
    lines.append("Validation: **Generated** (standard wiring for the "
                 "supported palette).")
    with open(os.path.join(bundle_dir, "wiring.md"), "w",
              encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def write_readme(bundle_dir, spec, passed):
    status = ("**Digitally validated** - the firmware passed syntax "
              "checks and simulated behaviour tests against its "
              "specification (see report.md)." if passed
              else "**Generated only** - the firmware did NOT pass "
                   "validation in the allowed attempts (see report.md). "
                   "Treat it as a draft, not as working firmware.")
    state_names = ", ".join(
        f"{c['print_name']}:ON/OFF" for c in _actuators(spec))
    lines = [f"# {spec['title']} - an H-ADE generated project", ""]
    lines.append("## What this is")
    lines.append("MicroPython firmware for an ESP32, generated from a "
                 "validated specification:")
    lines.append("")
    lines.append(spec_brief(spec))
    lines.append("")
    lines.append(f"Firmware status: {status}")
    lines.append("It is NOT physically verified - that label requires a "
                 "run on a real ESP32 board.")
    lines.append("")
    lines.append("## Files")
    lines.append("- `main.py` - the firmware (copy this to the board)")
    lines.append("- `project.json` - the validated specification this "
                 "project was generated from")
    lines.append("- `hardware.json` - components, pin map, parameters")
    lines.append("- `bom.md` - what to buy")
    lines.append("- `wiring.md` - how to wire it")
    lines.append("- `sim_config.json` - the test scenario used for "
                 "validation")
    lines.append("- `report.md` - test evidence and validation labels")
    lines.append("- `serial_log.txt` - serial output of the final "
                 "simulated run")
    lines.append("- `attempts/` - every firmware version the AI generated")
    lines.append("")
    lines.append("## Flash it to a real ESP32")
    lines.append("1. Put MicroPython on the board (once): download the "
                 "ESP32_GENERIC firmware from micropython.org/download, "
                 "then:")
    lines.append("   ```")
    lines.append("   pip install esptool mpremote")
    lines.append("   esptool --port COMx erase_flash")
    lines.append("   esptool --port COMx write_flash -z 0x1000 "
                 "firmware.bin")
    lines.append("   ```")
    lines.append("   (Replace COMx with your board's port; on Linux it "
                 "looks like /dev/ttyUSB0.)")
    lines.append("2. Copy the firmware and run it:")
    lines.append("   ```")
    lines.append("   mpremote connect COMx cp main.py :main.py")
    lines.append("   mpremote connect COMx reset")
    lines.append("   ```")
    lines.append(f"3. Watch the serial output: `mpremote connect COMx` "
                 f"(or open the board in Thonny). You should see "
                 f"HADE_BOOT, then TEMP/HUM lines and {state_names} "
                 f"every {spec['sampling_interval_s']} seconds.")
    with open(os.path.join(bundle_dir, "README.md"), "w",
              encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def write_report(bundle_dir, spec, attempts_log, final_stats, passed,
                 user_requirement, points, spec_rounds):
    lines = [f"# H-ADE - Project Report: {spec['name']}", ""]
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} "
                 f"(engine v{ENGINE_VERSION})")
    lines.append("")
    lines.append("## Requirement")
    if user_requirement:
        lines.append(f"As described by the user: {user_requirement}")
        lines.append("")
    lines.append("## Specification (project.json)")
    lines.append(f"Produced by the Spec Agent and checked by the "
                 f"Registry Validator"
                 + (f" (round {spec_rounds} of {MAX_SPEC_ROUNDS})"
                    if spec_rounds else "") + ":")
    lines.append("")
    lines.append(spec_brief(spec))
    lines.append("")
    lines.append("## Attempts")
    for entry in attempts_log:
        lines.append(f"- Attempt {entry['attempt']}: {entry['result']}")
    lines.append("")
    lines.append("## Validation status")
    profile_text = ", ".join(
        f"{fmt_num(p['temperature'])}C/{fmt_num(p['humidity'])}%"
        for p in points)
    if passed:
        lines.append("- main.py: **Generated**")
        lines.append("- main.py: **Digitally validated - syntax** "
                     "(compiled clean)")
        lines.append("- main.py: **Digitally validated - simulated "
                     f"behaviour** (spec-derived test profile "
                     f"{profile_text}; {final_stats.get('readings', 0)} "
                     f"readings, {final_stats.get('pin_writes', 0)} "
                     "actuator pin writes asserted)")
        lines.append("- main.py: NOT physically verified - no real "
                     "board run yet (that label needs the mpremote/board "
                     "step).")
    else:
        lines.append("- main.py: **Generated** only - all attempts "
                     "failed validation. See attempts/ and "
                     "serial_log.txt.")
    lines.append("")
    lines.append("## Bundle")
    lines.append("main.py, project.json, hardware.json, bom.md, "
                 "wiring.md, sim_config.json, report.md, README.md, "
                 "serial_log.txt, attempts/ - also packed as "
                 "project.zip.")
    lines.append("")
    with open(os.path.join(bundle_dir, "report.md"), "w",
              encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def write_zip(bundle_dir):
    """Pack the bundle as project.zip inside itself (the zip never
    contains itself). Returns the zip path."""
    zip_path = os.path.join(bundle_dir, "project.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, files in os.walk(bundle_dir):
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, bundle_dir).replace(os.sep, "/")
                if rel == "project.zip":
                    continue
                archive.write(full, rel)
    return zip_path


def list_bundle_files(bundle_dir):
    found = []
    for root, _dirs, files in os.walk(bundle_dir):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name),
                                  bundle_dir).replace(os.sep, "/")
            found.append(rel)
    return sorted(found)


# --------------------------------------------------------------------------
# The pipeline: spec -> firmware -> spec-derived tests -> self-healing
# --------------------------------------------------------------------------


def run_pipeline(stub=False, user_requirement="", bundle_dir=None,
                 max_attempts=DEFAULT_ATTEMPTS):
    event(event="spec_start", mode="stub" if stub else "live")
    spec, clarification, spec_rounds = get_spec(user_requirement,
                                                stub=stub)
    if clarification is not None:
        event(event="clarification",
              questions=clarification["questions"],
              unsupported=clarification["unsupported"],
              slots=clarification.get("slots") or [],
              archetype=clarification.get("archetype"))
        print("\nCLARIFICATION NEEDED - this project cannot be built "
              "exactly as described:", flush=True)
        for question in clarification["questions"]:
            print("  -", question, flush=True)
        summary = {
            "status": "clarification_needed",
            "attempts": 0,
            "max_attempts": max_attempts,
            "validation": None,
            "project": None,
            "bundle": bundle_dir,
            "zip": None,
            "files": [],
            "questions": clarification["questions"],
            "unsupported": clarification["unsupported"],
            "slots": clarification.get("slots") or [],
            "archetype": clarification.get("archetype"),
        }
        return 3, summary

    if bundle_dir is None:
        bundle_dir = os.path.join(OUTPUT_ROOT, spec["name"])
    attempts_dir = os.path.join(bundle_dir, "attempts")
    os.makedirs(attempts_dir, exist_ok=True)

    points = synthesize_profile(spec)
    interval = spec["sampling_interval_s"]
    event(event="spec_ready", project=spec["name"],
          components=len(spec["components"]), rules=len(spec["rules"]),
          test_points=len(points), spec_rounds=spec_rounds)
    print(f"\nSpecification validated: {spec['title']} "
          f"({len(spec['components'])} components, "
          f"{len(spec['rules'])} rules, {len(points)} test points)",
          flush=True)
    print(spec_brief(spec), flush=True)

    first_user = "Write the firmware for the validated specification now."
    if user_requirement:
        first_user = (f"The user's original words: {user_requirement}\n\n"
                      + first_user)
    messages = [
        {"role": "system", "content": firmware_system_prompt(spec)},
        {"role": "user", "content": first_user},
    ]
    event(event="start", mode="stub" if stub else "live",
          max_attempts=max_attempts, project=spec["name"])

    attempts_log = []
    final_code, final_transcript, final_stats = None, "", {}
    run_seconds = 0
    passed = False
    attempts_used = 0
    code = ""

    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt
        event(event="attempt_start", attempt=attempt,
              max_attempts=max_attempts)
        print(f"\n--- Attempt {attempt}/{max_attempts} "
              f"({'stub generator' if stub else 'Groq: ' + MODEL}) ---",
              flush=True)

        reply = stub_generate(spec, attempt) if stub else call_llm(messages)
        code = extract_code(reply)
        with open(os.path.join(attempts_dir, f"attempt_{attempt}.py"),
                  "w", encoding="utf-8") as handle:
            handle.write(code)

        syntax_error = syntax_check(code)
        if syntax_error:
            print("FAIL (syntax):", syntax_error, flush=True)
            attempts_log.append({"attempt": attempt,
                                 "result": f"syntax error - {syntax_error}"})
            problems = [syntax_error]
            event(event="attempt_failed", attempt=attempt, kind="syntax",
                  problems=problems)
        else:
            transcript, pin_events, run_seconds = run_simulation(
                code, points, interval)
            problems, stats = evaluate_spec(code, transcript, pin_events,
                                            spec, points)
            final_transcript, final_stats = transcript, stats
            if not problems:
                passed = True
                final_code = code
                attempts_log.append(
                    {"attempt": attempt,
                     "result": "PASSED all behaviour assertions "
                               f"({stats['readings']} readings, "
                               f"{stats['pin_writes']} pin writes)"})
                print("PASSED all behaviour assertions.", flush=True)
                event(event="attempt_passed", attempt=attempt,
                      readings=stats["readings"],
                      pin_writes=stats["pin_writes"])
                break
            print("FAIL (behaviour):", flush=True)
            for problem in problems:
                print("  -", problem, flush=True)
            attempts_log.append(
                {"attempt": attempt,
                 "result": "behaviour - " + "; ".join(problems)})
            event(event="attempt_failed", attempt=attempt,
                  kind="behaviour", problems=problems)

        if attempt < max_attempts:
            messages.append({"role": "assistant", "content":
                             "```python\n" + code + "```"})
            messages.append({"role": "user", "content":
                             build_repair_message(spec, code, problems)})

    if not passed:
        final_code = code  # keep the last attempt as the (unvalidated) draft

    with open(os.path.join(bundle_dir, "main.py"), "w",
              encoding="utf-8") as handle:
        handle.write(final_code)
    with open(os.path.join(bundle_dir, "serial_log.txt"), "w",
              encoding="utf-8") as handle:
        handle.write(final_transcript)
    with open(os.path.join(bundle_dir, "project.json"), "w",
              encoding="utf-8") as handle:
        json.dump(spec, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(bundle_dir, "hardware.json"), "w",
              encoding="utf-8") as handle:
        json.dump(hardware_spec_view(spec, user_requirement, passed),
                  handle, indent=2)
        handle.write("\n")
    with open(os.path.join(bundle_dir, "sim_config.json"), "w",
              encoding="utf-8") as handle:
        json.dump(sim_config_view(spec, points, run_seconds), handle,
                  indent=2)
        handle.write("\n")
    write_bom(bundle_dir, spec)
    write_wiring(bundle_dir, spec)
    write_readme(bundle_dir, spec, passed)
    write_report(bundle_dir, spec, attempts_log, final_stats, passed,
                 user_requirement, points, spec_rounds)
    zip_path = write_zip(bundle_dir)

    print("\n================ RESULT ================", flush=True)
    print("Bundle:", bundle_dir, flush=True)
    if passed:
        print("main.py is DIGITALLY VALIDATED (syntax + spec-derived "
              "simulated behaviour).", flush=True)
        print("It is NOT physically verified - that label needs a real "
              "ESP32 run.", flush=True)
    else:
        print(f"All {max_attempts} attempts failed validation. "
              "See report.md and attempts/ for the evidence.", flush=True)

    summary = {
        "status": "passed" if passed else "failed",
        "attempts": attempts_used,
        "max_attempts": max_attempts,
        "validation": "digitally_validated" if passed else "generated",
        "project": spec["name"],
        "archetype": spec.get("archetype"),
        "bundle": bundle_dir,
        "zip": zip_path,
        "files": list_bundle_files(bundle_dir),
        "spec": {"components": len(spec["components"]),
                 "rules": len(spec["rules"]),
                 "test_points": len(points),
                 "spec_rounds": spec_rounds},
    }
    return (0 if passed else 2), summary


def main():
    parser = argparse.ArgumentParser(description="H-ADE engine v3")
    parser.add_argument("--stub", action="store_true",
                        help="offline demo of the pipeline (no API key "
                             "needed): spec agent, tests and firmware "
                             "are deterministic stand-ins")
    parser.add_argument("--requirement", default="",
                        help="the user's project description - the Spec "
                             "Agent designs project.json from it")
    parser.add_argument("--out", default=None,
                        help="bundle output directory "
                             "(default: ./output/<spec name>/)")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help=f"max firmware self-healing attempts "
                             f"(default {DEFAULT_ATTEMPTS}, max 10)")
    args = parser.parse_args()
    max_attempts = max(1, min(10, args.attempts))
    try:
        code, summary = run_pipeline(stub=args.stub,
                                     user_requirement=args.requirement.strip(),
                                     bundle_dir=args.out,
                                     max_attempts=max_attempts)
    except Exception as exc:  # engine error: missing key, API failure, ...
        print("ERROR:", exc, flush=True)
        summary = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "attempts": 0,
            "max_attempts": max_attempts,
            "validation": None,
            "project": None,
            "bundle": args.out,
            "zip": None,
            "files": [],
        }
        print(json.dumps(summary), flush=True)
        return 1
    # The JSON summary is ALWAYS the last line of stdout (server contract).
    print(json.dumps(summary), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
