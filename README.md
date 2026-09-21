# 米家墙壁开关 → PC 关机（云端轮询版）

用一只**米家 / 领普智能墙壁开关**的按键，触发本地 Windows 电脑关机（带反悔窗）。
本仓库给出两套可独立运行的实现，触发源不同、原理不同，按你的设备类型选一套。

> 本项目为个人自用自动化的脱敏分享版。所有 did / MAC / bindkey / 云端会话
> 均已替换为占位符，**不含任何凭据明文**。请填入你自己的值后再使用。

---

## 两套实现，选一套

| 方案 | 脚本 | 适用设备 | 原理 | 是否联网 |
|---|---|---|---|---|
| **A · 云端轮询**（推荐 / 主力） | `mi_shutdown_q4d.py` | MESH 类墙壁开关（如领普 Q4D / `linp.switch.q3s2`） | 每 3 秒**只读**云端 miotspec 属性，检测锁存翻转 | 是（小米云） |
| **B · 本地 BLE** | `mi_shutdown_ble.py` | 蓝牙版无线开关（如小米 `lumi.remote.mcn001`） | 被动监听 MiBeacon 广播并用 bindkey 本地解密 | 否（纯本地） |

**怎么选：**
- 开关是 **MESH / 蓝牙 Mesh**、本地抓不到可解码广播 → 用 **A（云端轮询）**。
- 开关是**经典蓝牙（BLE）**、能用 `xiaomi_ble` 解出按键事件 → 用 **B（本地 BLE）**，更轻、不依赖云端、不受风控/会话过期影响。

两套都遵循同一条安全设计：**触发与动作解耦**，动作是 `shutdown /s /t 15`，
**15 秒反悔窗**内可 `shutdown /a` 或点屏幕取消。

---

## 方案 A：云端轮询（`mi_shutdown_q4d.py`）

### 核心思路（为什么这么做）
MESH 墙壁开关在本地没有可解密的广播帧，但它的右键"开/关"状态会同步到小米云端，
而且这个状态是**锁存翻转**的——每按一次右键，云端属性 `right-on`（siid=3, piid=1）
就在 `True/False` 之间翻转，并**一直保持到下次按键**（跨脚本重启、跨电脑重启都保留）。

于是脚本只需：
1. 用 `prop/get`（**只读，绝不 `prop/set`**）每隔几秒读一次该属性；
2. 记住上一次读到的值（基线 baseline）；
3. 一旦当前值 ≠ 基线 = 检测到一次按键 → 触发关机 → 把基线更新为当前值（重新武装）。

因为状态锁存，**慢轮询（3 秒）也不会漏按**；又因为全程只读，避开了小米云对写接口较敏感的风控。

> 左右键在 miotspec 里是两套独立服务（左 siid=2 / 右 siid=3）。脚本只读 siid=3，
> 因此**结构上**左键不可能触发本链路——这对"左键另有用途（如电脑硬电源）"的场景很关键。

### 依赖
- Python 3.10+（实测 3.12）
- `requests` `colorama` `pycryptodome` `Pillow`
- **第三方 `token_extractor.py`**（PiotrMachowski 的 *Xiaomi Cloud Tokens Extractor*）
  —— 提供扫码登录与云端签名请求能力。**本仓库不内置该文件**，请自行获取并放到与脚本同目录。

```
py -m pip install requests colorama pycryptodome Pillow
```

### 使用步骤
1. 把第三方 `token_extractor.py` 放到脚本同目录。
2. 打开 `mi_shutdown_q4d.py`，把 `DID = "0000000000"` 换成**你自己开关的 did**
   （米家 App / extractor 输出里可查，填整只开关的 did）。
3. 确认 `SIID, PIID = 3, 1`（右键 on 状态）。若你的设备右键不是 siid=3，按 miotspec 调整。
4. 前台先空跑验收（`DRY_RUN = True` 是默认值）：
   ```
   py mi_shutdown_q4d.py
   ```
   首次运行会让你**用米家 App 扫码登录**（约 2 分钟窗口；网页打不开就用日志里打印的
   longPolling 链接，或双击生成的 `mi_qr.jpg`）。登录成功后会话缓存进 `mi_cloud_session.json`，
   之后很久不用重扫。
5. 看到"已就绪"后，按一下右键，日志应打印"检测到右键跳变"。空跑模式下不会真关机。
6. 验收无误后，把 `DRY_RUN` 改成 `False`，再跑一次：按右键 → 15 秒倒计时 → 关机
   （期间可 `shutdown /a` 取消）。
7. （可选）做无窗自启：用启动目录（`shell:startup`）建快捷方式，目标指向
   `pyw.exe mi_shutdown_q4d.py`（`pyw` 路径用 `where pyw` 查）。脚本在 `stdout` 为 None
   时只写日志文件，适合后台常驻。

### 关键配置项
| 变量 | 含义 | 建议值 |
|---|---|---|
| `DID` | 你的开关 did | 必填 |
| `SIID, PIID` | 右键 on 属性的服务/属性 id | `3, 1` |
| `POLL_INTERVAL` | 云端轮询间隔（秒） | `3.0`（别太快以免风控） |
| `DRY_RUN` | 空跑（只记日志不关机） | 验收 `True` → 上线 `False` |
| `SHUTDOWN_DELAY` | 反悔窗秒数 | `15` |
| `FAILS_RELOGIN` | 连续读失败多少次后尝试重新登录 | `8` |

---

## 方案 B：本地 BLE（`mi_shutdown_ble.py`）

### 核心思路
经典蓝牙开关每按一次会对外广播一段加密 MiBeacon（service data UUID `0xFE95`）。
`xiaomi_ble` 用设备的 **bindkey** 解密出 `press / double_press / long_press` 事件。
脚本被动监听指定 MAC 的广播、去重、解码，命中目标事件即触发关机。**全程不联网。**

### 依赖
```
py -m pip install bleak xiaomi_ble
```

### 使用步骤
1. 打开 `mi_shutdown_ble.py`：
   - `MAC` 换成你开关的 BLE MAC；
   - `BLE_KEY` 换成你开关的 bindkey（32 个十六进制字符）。bindkey 可用第三方
     extractor 工具从设备 dump 中取得。
2. `TRIGGER_EVENTS = {"press"}` 表示单击触发；想改双击/长按换成 `"double_press"` / `"long_press"`。
3. 先 `DRY_RUN = True` 空跑验收，按键看日志能否正确解出事件；无误后改 `False` 上线。
   ```
   py mi_shutdown_ble.py
   ```

> ⚠️ Windows 上 `bleak` 依赖 WinRT 轮子，对 Python 版本较敏感；如遇导入/运行问题，
> 优先在 3.12 环境验证。

---

## 安全与隐私约定
- 本仓库**不含任何凭据**。did / MAC / bindkey / 会话缓存均为占位符或运行时生成。
- `.gitignore` 已排除会话缓存、日志、二维码图片等敏感/临时文件——请勿强行提交它们。
- 截图分享前请打码 token / bindkey / 密码列。
- 云端方案对小米云**只读**，不调用任何写接口。

## 目录结构
```
.
├── README.md                 # 本文件
├── mi_shutdown_q4d.py        # 方案 A：云端轮询（MESH 开关）
├── mi_shutdown_ble.py        # 方案 B：本地 BLE（蓝牙开关）
├── .gitignore
├── LICENSE
└── docs/
    └── 工作原理与排坑.md      # 设计要点、实测结论、踩坑清单
```

## 许可
见 [LICENSE](LICENSE)（MIT）。第三方 `token_extractor.py` 版权归其原作者，遵循其各自的许可。
