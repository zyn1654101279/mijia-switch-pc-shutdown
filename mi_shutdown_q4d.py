# -*- coding: utf-8 -*-
"""领普 Q4D 墙壁开关(MESH双键, linp.switch.q3s2) 右键 -> 本机关机（云端轮询版）

原理（实测确认）：
  右键单击会让云端属性 <你的开关did> siid=3 piid=1 (right-on) 在 True/False
  之间【翻转并锁存】，一直保持到下次按键（跨脚本运行也保留）。左键走 siid=2，
  不动 right-on。本脚本只读(prop/get)该属性，检测到任何跳变 = 一次右键 = 触发
  shutdown /s /t 15（15 秒内可 shutdown /a 或屏幕取消）。
  纯云端读、不回写、不碰写接口；MESH 设备本地无可解码广播，故走云端。

依赖：需自行获取第三方 token_extractor.py（PiotrMachowski 的 Xiaomi token
  extractor，本仓库不内置）与本脚本同目录；另需 requests/colorama/
  pycryptodome/Pillow。
会话：优先 mi_cloud_session.json 缓存，失效才扫码（2 分钟窗，扫一次管很久）。
自启：可用 pyw 无窗运行（stdout 为 None 时只写日志）。
"""
import json
import logging
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ------------------------- 配置 -------------------------
# 改成你自己墙壁开关的 did（米家/extractor 里可查；linp.switch.q3s2 整开关的 did）
DID = "0000000000"          # <-- 占位符：替换为你的开关 did
SIID, PIID = 3, 1           # 右键 on 状态（锁存翻转）；q3s2 右=siid3
COUNTRY = "cn"
POLL_INTERVAL = 3.0         # 云端轮询间隔（秒）。状态锁存，慢点也不会漏按；别太快以免风控
DRY_RUN = True              # 公共模板默认空跑；自己验收通过后改 False
SHUTDOWN_DELAY = 15         # 关机反悔秒数，期间可 shutdown /a 或点屏幕取消
FAILS_RELOGIN = 8           # 连续读失败这么多次就尝试重新登录（会话过期时）

SESSION_FILE = os.path.join(HERE, "mi_cloud_session.json")
LOG_FILE = os.path.join(HERE, "mi_shutdown_q4d.log")

_handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if sys.stdout is not None:   # pyw 无窗运行时没有 stdout，只写文件
    _handlers.insert(0, logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("q4d_shutdown")

log.info("Q4D_SHUTDOWN_V1 alive, pid=%s", os.getpid())

try:
    import token_extractor as te
    log.info("import token_extractor OK")
except Exception:
    log.error("import token_extractor FAILED:\n%s", traceback.format_exc())
    raise


class QrConnector(te.QrCodeXiaomiCloudConnector):
    def login_step_1(self):
        ok = super().login_step_1()
        if ok:
            try:
                self._timeout = min(float(self._timeout), 120)
            except Exception:
                self._timeout = 120
        return ok


def load_session(c):
    if not os.path.exists(SESSION_FILE):
        return False
    try:
        d = json.load(open(SESSION_FILE, encoding="utf-8"))
        c.userId = d["userId"]
        c._ssecurity = d["ssecurity"]
        c._serviceToken = d["serviceToken"]
        return True
    except Exception as e:
        log.warning("session cache unreadable: %r", e)
        return False


def save_session(c):
    json.dump({"userId": str(c.userId), "ssecurity": c._ssecurity,
               "serviceToken": c._serviceToken},
              open(SESSION_FILE, "w", encoding="utf-8"))
    log.info("session cached")


def prop_get(c, did, siid, piid):
    url = te.XiaomiCloudConnector.get_api_url(COUNTRY) + "/miotspec/prop/get"
    params = {"data": json.dumps(
        {"params": [{"did": did, "siid": siid, "piid": piid}]})}
    try:
        resp = c.execute_api_call_encrypted(url, params)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        # 网络抖动/超时等：当作一次读失败返回，绝不抛出去崩掉常驻监听
        return "NETERR:%r" % (e,)
    try:
        item = resp["result"][0]
        if item.get("code", 0) != 0:
            return "code=%s" % item.get("code")
        return item.get("value")
    except Exception:
        return "ERR:%s" % repr(resp)[:120]


def read_right(c):
    """读右键 on 状态；成功返回 bool，失败返回 None。"""
    v = prop_get(c, DID, SIID, PIID)
    return v if isinstance(v, bool) else None


def do_shutdown():
    if DRY_RUN:
        log.info("DRY_RUN：检测到右键跳变，此处本应关机，实际不执行。")
        return
    log.info("触发关机，%d 秒内可 shutdown /a 或点屏幕取消。", SHUTDOWN_DELAY)
    subprocess.run(["shutdown", "/s", "/t", str(SHUTDOWN_DELAY),
                    "/c", "领普墙壁开关右键触发关机"])
    sys.exit(0)


def login(c):
    """优先用缓存会话；没有就扫码。返回 True 表示已就绪。"""
    if load_session(c):
        log.info("使用缓存会话。")
        return True
    log.info("无会话缓存，需扫码登录：2 分钟内用米家 App 扫码并在手机确认 ...")
    log.info("（网页打不开就用控制台/日志里打印的 longPolling 链接，或双击 mi_qr.jpg）")
    try:
        ok = c.login()
    except Exception as e:
        log.error("登录异常: %r", e)
        ok = False
    if not ok:
        log.error("扫码登录失败或超时。")
        return False
    save_session(c)
    return True


def relogin():
    """会话疑似过期时，删缓存重新扫码。无人值守时只能记日志等人来处理。"""
    log.warning("连续读失败，疑似会话过期。尝试重新登录（需有人扫码）...")
    try:
        os.remove(SESSION_FILE)
    except OSError:
        pass
    c = QrConnector()
    if login(c):
        log.info("重新登录成功。")
        return c
    log.error("重新登录失败：请手动运行 py mi_shutdown_q4d.py 扫码后再用 pyw 自启。")
    return None


def main():
    c = QrConnector()
    if not login(c):
        log.error("无法登录云端，退出。")
        sys.exit(1)

    # 取一个成功的读数作为基线
    baseline = None
    while baseline is None:
        baseline = read_right(c)
        if baseline is None:
            log.warning("初始读取失败，3 秒后重试 ...")
            time.sleep(3)
    log.info("已就绪。基线 right-on=%s | did=%s siid=%d piid=%d | 轮询 %.1fs | DRY_RUN=%s",
             baseline, DID, SIID, PIID, POLL_INTERVAL, DRY_RUN)
    log.info("等待右键单击（任何 True/False 跳变都会触发关机）...")

    fails = 0
    while True:
        cur = read_right(c)
        if cur is None:
            fails += 1
            if fails % 5 == 1:
                log.warning("云端读取失败(累计%d次)，可能网络抖动或会话过期。", fails)
            if fails >= FAILS_RELOGIN:
                nc = relogin()
                if nc is not None:
                    c = nc
                    fails = 0
                    base2 = read_right(c)
                    if base2 is not None:
                        baseline = base2   # 重登后以当前态为新基线，避免误触发
            time.sleep(POLL_INTERVAL)
            continue

        fails = 0
        if cur != baseline:
            log.info("检测到右键跳变: right-on %s -> %s", baseline, cur)
            baseline = cur            # 先重新武装，DRY_RUN 下不会反复触发
            do_shutdown()             # 真实模式下这里会关机并退出
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("已手动停止（Ctrl+C）。")
    except SystemExit:
        raise
    except BaseException:
        log.error("main crashed:\n%s", traceback.format_exc())
        raise
