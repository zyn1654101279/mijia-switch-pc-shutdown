# -*- coding: utf-8 -*-
"""小米无线开关(蓝牙版) -> 本机关机（本地 BLE 监听 · 正式版）

原理：开关每按一次对外广播一段加密 MiBeacon（service data 0xFE95），
xiaomi_ble 用 BLE key 解密，区分 单击/双击/长按 事件。
本脚本被动监听：不联网、不云端、不网关、不扫码。
单击（event_type=press）-> shutdown /s /t 15，15 秒内可 shutdown /a 取消。

依赖：py -m pip install bleak xiaomi_ble
"""
import asyncio
import logging
import os
import subprocess
import sys

from bleak import BleakScanner
from xiaomi_ble import XiaomiBluetoothDeviceData

MAC = "00:00:00:00:00:00".upper()          # <-- 占位符：换成你开关的 BLE MAC
BLE_KEY = bytes.fromhex("00" * 16)           # <-- 占位符：换成你开关的 bindkey（32 个十六进制字符）
TRIGGER_EVENTS = {"press"}      # 单击触发；想改成双击/长按就换 "double_press"/"long_press"
DRY_RUN = True                  # 公共模板默认空跑；自己验收通过后改 False
SHUTDOWN_DELAY = 15             # 关机后悔药秒数（屏幕取消按钮/shutdown /a 都要赶在这之前）

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "mi_shutdown_ble.log")
_handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if sys.stdout is not None:      # 用 pyw 无窗口运行时没有 stdout，只写文件
    _handlers.insert(0, logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("mi_ble")

FE95 = "0000fe95-0000-1000-8000-00805f9b34fb"
data = XiaomiBluetoothDeviceData(bindkey=BLE_KEY)
last_payload = None


class Adv:
    """鸭子类型的 BluetoothServiceInfo：只提供 xiaomi_ble 会读的字段。"""

    def __init__(self, device, adv):
        self.address = device.address
        self.rssi = adv.rssi
        self.name = device.name or getattr(adv, "local_name", None) or MAC
        self.service_data = adv.service_data or {}
        self.manufacturer_data = adv.manufacturer_data or {}
        self.tx_power = getattr(adv, "tx_power", None)
        self.source = "bleak"


def handle(device, adv):
    global last_payload
    if (device.address or "").upper() != MAC:
        return
    payload = (adv.service_data or {}).get(FE95)
    if payload is None or payload == last_payload:
        return                      # 同一次按键的重复广播，忽略
    last_payload = payload
    ad = Adv(device, adv)
    try:
        if not data.supported(ad):
            log.info("xiaomi_ble 不支持的广播: %s", payload.hex())
            return
        update = data.update(ad)
    except Exception:
        log.exception("解码异常 payload=%s", payload.hex())
        return

    types = []
    for _dk, ev in (update.events or {}).items():
        types.append(ev.event_type)
        log.info("按键事件: %s (rssi=%s)", ev.event_type, adv.rssi)
    if not types:
        log.info("非按键广播(电量等)，忽略: %s", payload.hex())
        return

    if set(types) & TRIGGER_EVENTS:
        if DRY_RUN:
            log.info("DRY_RUN：此处本应关机（事件=%s），实际不执行", types)
        else:
            log.info("触发关机，%d 秒内可 shutdown /a 取消", SHUTDOWN_DELAY)
            subprocess.run(["shutdown", "/s", "/t", str(SHUTDOWN_DELAY),
                            "/c", "小米无线开关触发"])
            sys.exit(0)


async def main():
    log.info("开始监听 %s | 触发事件=%s | DRY_RUN=%s", MAC, TRIGGER_EVENTS, DRY_RUN)
    while True:
        try:
            scanner = BleakScanner(handle)
            await scanner.start()
            log.info("蓝牙扫描已启动，等待按键 ...")
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            log.info("已退出。")
            return
        except Exception as e:
            log.warning("蓝牙异常，5 秒后重试: %s", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("已退出。")
