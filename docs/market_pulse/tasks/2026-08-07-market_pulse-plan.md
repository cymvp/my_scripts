# market_pulse 盘中涨跌速度监控 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 盘中一眼分清「只有我的股票在跌」和「整个市场都在跌」。

**Architecture:** 计算核心独立成 `market_pulse.py`，里面的纯函数区做速度、宽度、相对强弱、判定四类计算，IO 区负责落盘与读盘。两个前端消费同一个核心：命令行出完整面板，`stock_watch.py` 的横条末尾加一个格子出单行文案。取数并入 `stock_watch.fetch_quotes()` 已有的 A 股批量请求，不产生额外网络请求。

**Tech Stack:** Python 3.9、标准库（`urllib.request` / `json` / `datetime` / `statistics`）、pytest。不引入任何新依赖。

**Spec:** `docs/market_pulse/spec/2026-08-07-market_pulse-spec.md`

## Global Constraints

- **测试命令必须带 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`**：全局安装的 deepeval 在 Python 3.9 下 import 即报 `TypeError: unsupported operand type(s) for |`，会让整个 pytest 收集阶段失败。完整命令：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q`
- **基线：现有 148 个测试必须始终全绿。** 每次提交前跑全量。
- **不引入新的第三方依赖。** 只用标准库。
- **纯函数区不得有任何网络或文件 IO。** 单测不联网、不读盘。
- **任何降级都要在返回值里留痕**，不允许静默返回一个看起来正常的数（用户 CLAUDE.md 硬性要求）。缺数据返回 `None`，不返回 `0`。
- **单位约定：** 涨跌幅 `r` 单位是 %，速度 `v` 单位是百分点（pp）。变量名和 docstring 里都要写清楚。
- **注释与 docstring 用中文**，与现有文件一致。
- **不做方向预测，不给「跌得快/慢」的定性词。**

## 文件结构

| 文件 | 状态 | 职责 |
|---|---|---|
| `market_pulse.py` | 新建 | 纯函数计算核心 + 落盘 IO + 命令行面板 |
| `test_market_pulse.py` | 新建 | 纯函数单测（不联网、不读盘） |
| `test_market_pulse_e2e.py` | 新建 | 集成测试，打真实接口，环境变量控制 |
| `stock_watch.py` | 修改 | `refresh()` 里并入池子代码；横条末尾加一个格子 |
| `pulse_store.jsonl` | 运行时产物 | 加进 `.gitignore` |

## 任务依赖与并行分组

```
组 1（六个任务完全独立，可同时开工）
  Task 1  模块骨架 + 常量 + merge_codes + split_result
  Task 2  in_session 交易时段
  Task 3  speed + aggregate
  Task 4  breadth + verdict
  Task 5  excess + rank
  Task 6  parse_ts + pick_snapshot

组 2（依赖组 1）
  Task 7  落盘 IO          依赖 Task 2、Task 6
  Task 8  面板与单行渲染    依赖 Task 3、4、5

组 3
  Task 9  CLI 入口 + 取数 + 集成测试   依赖 Task 1、6、7、8

组 4
  Task 10 stock_watch.py 接入          依赖 Task 1、7、8
```

---

### Task 1: 模块骨架、常量、代码合并与分拣

**Files:**
- Create: `market_pulse.py`
- Create: `test_market_pulse.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `intraday_guide.POOL`（38 只票的 `{代码: 名称}`）、`intraday_guide.SECTOR`（`{代码: 赛道名}`）
- Produces:
  - `POOL_CODES: tuple[str, ...]` — 38 个池子代码，顺序与 `intraday_guide.POOL` 一致
  - `IDX_CODES: tuple[str, ...]` — `("sz399006", "sh000001", "sh000688")`
  - `IDX_NAMES: dict[str, str]` — `{"sz399006": "创业板指", "sh000001": "上证", "sh000688": "科创50"}`
  - `WINDOWS: tuple[int, ...]` — `(15, 60, 300)`
  - `MIN_VALID: int` — `20`
  - `VERDICT_RATIO: float` — `0.60`
  - `STALE_SEC: int` — `60`
  - `STORE: str` — `pulse_store.jsonl` 绝对路径
  - `merge_codes(watch, pool=POOL_CODES, idx=IDX_CODES) -> list[str]`
  - `split_result(quotes, watch, pool=POOL_CODES) -> tuple[dict, dict]`

- [ ] **Step 1: 写失败的测试**

新建 `test_market_pulse.py`：

```python
"""market_pulse 纯函数的单元测试（不联网、不读盘）。"""
import pytest

import market_pulse as mp


# --- 常量 -----------------------------------------------------------------

def test_pool_codes_has_38():
    """池子固定 38 只，与 intraday_guide.POOL 同源同序。"""
    import intraday_guide as ig
    assert len(mp.POOL_CODES) == 38
    assert list(mp.POOL_CODES) == list(ig.POOL)


def test_windows_are_15_60_300():
    assert mp.WINDOWS == (15, 60, 300)


# --- merge_codes 合并去重 --------------------------------------------------

def test_merge_codes_dedups_overlap():
    """自选里已有的池子票不能重复出现。

    实际场景：自选里有中际旭创和国际复材，它们同时也在 38 只池子里。
    """
    watch = ["sz300308", "hk03308", "sh601288"]
    out = mp.merge_codes(watch, pool=("sz300308", "sz300502"), idx=("sz399006",))
    assert out == ["sz300308", "hk03308", "sh601288", "sz300502", "sz399006"]


def test_merge_codes_keeps_watch_order_first():
    """自选顺序必须原样保留在最前，界面按这个顺序渲染。"""
    watch = ["sh601288", "sz300308"]
    out = mp.merge_codes(watch, pool=("sz300308",), idx=())
    assert out[:2] == ["sh601288", "sz300308"]


def test_merge_codes_empty_watch():
    out = mp.merge_codes([], pool=("sz300308", "sz300502"), idx=("sz399006",))
    assert out == ["sz300308", "sz300502", "sz399006"]


def test_merge_codes_real_sizes():
    """真实规模：自选 10 个 A 股 + 池子 38 + 指数 3，重叠 6 个，去重后 45。"""
    watch = ["sz300308", "sz301526", "sh688825", "sh601288", "sh588170",
             "sz002463", "sh688256", "sh603986", "sh688008", "sh513120"]
    out = mp.merge_codes(watch)
    assert len(out) == 45
    assert len(set(out)) == 45


# --- split_result 分拣 -----------------------------------------------------

def test_split_result_separates_watch_and_pool():
    """重叠的票要同时出现在两边，不是二选一。"""
    quotes = {"sz300308": {"r": 2.35}, "sh601288": {"r": -0.4},
              "sz300502": {"r": 5.96}, "sz399006": {"r": 1.75}}
    watch_part, pool_part = mp.split_result(
        quotes, watch=["sz300308", "sh601288"], pool=("sz300308", "sz300502"))
    assert set(watch_part) == {"sz300308", "sh601288"}
    assert set(pool_part) == {"sz300308", "sz300502"}
    assert watch_part["sz300308"] is pool_part["sz300308"]


def test_split_result_ignores_missing():
    """请求里有、返回里没有的代码，两边都不出现，不塞 None 占位。"""
    quotes = {"sz300308": {"r": 2.35}}
    watch_part, pool_part = mp.split_result(
        quotes, watch=["sz300308", "sh601288"], pool=("sz300308", "sz300502"))
    assert set(watch_part) == {"sz300308"}
    assert set(pool_part) == {"sz300308"}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd ~/projects/my_scripts
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: FAIL，`ModuleNotFoundError: No module named 'market_pulse'`

- [ ] **Step 3: 写最小实现**

新建 `market_pulse.py`：

```python
"""盘中涨跌速度监控 — 回答「我的票在跌，是独跌还是全市场同跌」。

设计文档：docs/market_pulse/spec/2026-08-07-market_pulse-spec.md

只描述已经发生的涨跌，不做方向预测。
"""
import os

import intraday_guide as ig

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(BASE_DIR, "pulse_store.jsonl")

POOL_CODES = tuple(ig.POOL)
IDX_CODES = ("sz399006", "sh000001", "sh000688")
IDX_NAMES = {"sz399006": "创业板指", "sh000001": "上证", "sh000688": "科创50"}

# 长期持仓，见 ~/.claude 记忆 stock-holdings。长鑫科技不在 38 只池内，
# 它只在「相对强弱」出数字，速度栏是 —。
HOLD_CODES = ("sz300308", "sz301526", "sh688825")
HOLD_NAMES = {"sh688825": "长鑫科技"}

WINDOWS = (15, 60, 300)      # 速度窗口，单位秒
MIN_VALID = 20               # 池内有效票少于这个数就不出判定
VERDICT_RATIO = 0.60         # 判定的涨跌占比门槛，闭区间
STALE_SEC = 60               # 最后一条快照超过这么久，视为采集未运行


def merge_codes(watch, pool=POOL_CODES, idx=IDX_CODES):
    """把自选、池子、指数合成一份去重代码表，自选顺序保留在最前。

    自选顺序要保留，因为悬浮窗按这个顺序渲染。
    """
    out = list(watch)
    seen = set(out)
    for code in tuple(pool) + tuple(idx):
        if code not in seen:
            out.append(code)
            seen.add(code)
    return out


def split_result(quotes, watch, pool=POOL_CODES):
    """把一次批量返回按用途分拣成 (自选部分, 池子部分)。

    重叠的票同时出现在两边，指向同一个对象。请求里有、返回里没有的代码
    两边都不出现——不塞占位值，让上层能看出到底缺了什么。
    """
    watch_part = {c: quotes[c] for c in watch if c in quotes}
    pool_part = {c: quotes[c] for c in pool if c in quotes}
    return watch_part, pool_part
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 8 passed

- [ ] **Step 5: 加 .gitignore 并提交**

在 `.gitignore` 末尾追加一行（文件不存在就新建）：

```
pulse_store.jsonl
```

```bash
git add market_pulse.py test_market_pulse.py .gitignore
git commit -m "feat(market_pulse): 模块骨架、常量与代码合并分拣"
```

---

### Task 2: 交易时段判断

**Files:**
- Modify: `market_pulse.py`
- Modify: `test_market_pulse.py`

**Interfaces:**
- Consumes: 无
- Produces: `in_session(hhmm: str) -> bool` —— 入参形如 `"10:30"` 或 `"10:30:15"`

- [ ] **Step 1: 写失败的测试**

追加到 `test_market_pulse.py`：

```python
# --- in_session 交易时段 ---------------------------------------------------

@pytest.mark.parametrize("hhmm,expected", [
    ("09:14", False),   # 竞价前
    ("09:20", False),   # 集合竞价——虚拟撮合价，混进速度序列会造假信号
    ("09:29", False),
    ("09:30", True),    # 开盘首笔
    ("10:30", True),
    ("11:29", True),
    ("11:30", True),    # 上午收盘那一笔算
    ("11:31", False),   # 午休
    ("12:00", False),
    ("12:59", False),
    ("13:00", True),    # 下午开盘
    ("14:57", True),
    ("15:00", True),    # 收盘那一笔算
    ("15:01", False),   # 盘后
    ("23:00", False),
])
def test_in_session(hhmm, expected):
    assert mp.in_session(hhmm) is expected


def test_in_session_accepts_seconds():
    """带秒的时间戳也要认，落盘里存的是 HH:MM:SS。"""
    assert mp.in_session("10:30:15") is True
    assert mp.in_session("12:00:01") is False


def test_in_session_rejects_garbage():
    """格式不对直接抛，不静默返回 False——那会让采集悄悄停掉。"""
    with pytest.raises(ValueError):
        mp.in_session("abc")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q -k in_session
```

Expected: FAIL，`AttributeError: module 'market_pulse' has no attribute 'in_session'`

- [ ] **Step 3: 写最小实现**

追加到 `market_pulse.py`：

```python
SESSIONS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))


def in_session(hhmm):
    """是不是连续竞价时段。入参 "HH:MM" 或 "HH:MM:SS"。

    集合竞价（9:15-9:25）返回 False：那时的价格是虚拟撮合价，
    和连续竞价不是同一个东西，混进速度序列会产生假信号。
    格式不合法抛 ValueError，不静默返回 False——静默会让采集悄悄停掉。
    """
    parts = hhmm.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"时间格式不对: {hhmm!r}")
    try:
        minutes = int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        raise ValueError(f"时间格式不对: {hhmm!r}") from None
    return any(lo <= minutes <= hi for lo, hi in SESSIONS)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 25 passed

- [ ] **Step 5: 提交**

```bash
git add market_pulse.py test_market_pulse.py
git commit -m "feat(market_pulse): 交易时段判断，集合竞价不计入"
```

---

### Task 3: 速度与聚合

**Files:**
- Modify: `market_pulse.py`
- Modify: `test_market_pulse.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `speed(r_now, r_past) -> float | None` —— 单位百分点
  - `aggregate(values) -> tuple[float | None, int]` —— 返回 `(中位数, 有效数)`

- [ ] **Step 1: 写失败的测试**

追加到 `test_market_pulse.py`：

```python
# --- speed 速度 ------------------------------------------------------------

def test_speed_normal():
    """13:24:00 价 979.00（r=+2.51%），13:24:15 价 977.00（r=+2.30%）。

    v = 2.30 − 2.51 = −0.21 百分点，读作「最近 15 秒涨跌幅掉了 0.21 个百分点」。
    """
    assert mp.speed(2.30, 2.51) == pytest.approx(-0.21)


def test_speed_zero_when_unchanged():
    assert mp.speed(2.30, 2.30) == 0.0


def test_speed_returns_none_when_past_missing():
    """缺历史返回 None，不返回 0——0 会被读成「没动」，那是完全不同的意思。"""
    assert mp.speed(2.30, None) is None


def test_speed_returns_none_when_now_missing():
    assert mp.speed(None, 2.51) is None


# --- aggregate 中位数聚合 --------------------------------------------------

def test_aggregate_odd_count():
    assert mp.aggregate([-0.5, -0.1, 2.0]) == (pytest.approx(-0.1), 3)


def test_aggregate_even_count():
    """偶数个取中间两个的均值。"""
    val, n = mp.aggregate([-0.4, -0.2, 0.1, 0.3])
    assert val == pytest.approx(-0.05)
    assert n == 4


def test_aggregate_drops_none():
    val, n = mp.aggregate([-0.5, None, -0.1])
    assert val == pytest.approx(-0.3)
    assert n == 2


def test_aggregate_all_none():
    assert mp.aggregate([None, None]) == (None, 0)


def test_aggregate_empty():
    assert mp.aggregate([]) == (None, 0)


def test_aggregate_uses_median_not_mean():
    """必须是中位数。

    2026-08-07 实测：PCB/覆铜板赛道中位 +7.62%、MLCC 只有 +0.39%，
    平均数会被少数暴涨票拉偏。
    """
    val, _ = mp.aggregate([0.1, 0.1, 0.1, 20.0])
    assert val == pytest.approx(0.1)      # 中位数 0.1；平均数会是 5.075
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q -k "speed or aggregate"
```

Expected: FAIL，`AttributeError: module 'market_pulse' has no attribute 'speed'`

- [ ] **Step 3: 写最小实现**

在 `market_pulse.py` 顶部的 import 区加上 `import statistics as st`，然后追加：

```python
def speed(r_now, r_past):
    """速度 = 涨跌幅之差，单位百分点(pp)。

    用涨跌幅之差而不是价格变化率，是为了让 977 元的中际旭创和 38 元的
    国际复材能放进同一张表比较。

    任一端缺数据返回 None，不返回 0——0 表示「没动」，和「不知道」是两回事。
    """
    if r_now is None or r_past is None:
        return None
    return r_now - r_past


def aggregate(values):
    """一组速度或涨跌幅取中位数，返回 (中位数, 有效数)。

    用中位数不用平均数：池子里有涨停跌停的极端票，平均数会被拉偏。
    有效数一起返回，让上层能判断样本够不够（见 MIN_VALID）。
    """
    valid = [v for v in values if v is not None]
    if not valid:
        return None, 0
    return st.median(valid), len(valid)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 35 passed

- [ ] **Step 5: 提交**

```bash
git add market_pulse.py test_market_pulse.py
git commit -m "feat(market_pulse): 速度计算与中位数聚合"
```

---

### Task 4: 宽度与判定

**Files:**
- Modify: `market_pulse.py`
- Modify: `test_market_pulse.py`

**Interfaces:**
- Consumes: `MIN_VALID`、`VERDICT_RATIO`（Task 1）
- Produces:
  - `breadth(rs_now, rs_past=None) -> dict` —— 键：`up`、`down`、`flat`、`valid`、`flip_down`、`flip_up`
  - `verdict(br, r_stock) -> tuple[str | None, str]` —— 返回 `(判定, 说明)`，判定为 `None` 表示不出结论

- [ ] **Step 1: 写失败的测试**

追加到 `test_market_pulse.py`：

```python
# --- breadth 宽度 ----------------------------------------------------------

def test_breadth_counts():
    """2026-08-07 11:30 实测：38 只里上涨 34、下跌 4、平盘 0。"""
    rs = {f"c{i}": 1.0 for i in range(34)}
    rs.update({f"d{i}": -1.0 for i in range(4)})
    br = mp.breadth(rs)
    assert (br["up"], br["down"], br["flat"], br["valid"]) == (34, 4, 0, 38)


def test_breadth_counts_flat():
    br = mp.breadth({"a": 1.0, "b": 0.0, "c": -1.0})
    assert (br["up"], br["down"], br["flat"]) == (1, 1, 1)


def test_breadth_skips_none():
    """停牌票的 r 是 None，从有效数里剔除。"""
    br = mp.breadth({"a": 1.0, "b": None, "c": -1.0})
    assert br["valid"] == 2


def test_breadth_flip_down():
    """涨转跌算一次翻向。"""
    br = mp.breadth({"a": -0.1}, {"a": 0.3})
    assert br["flip_down"] == 1
    assert br["flip_up"] == 0


def test_breadth_flip_up():
    br = mp.breadth({"a": 0.2}, {"a": -0.5})
    assert br["flip_up"] == 1
    assert br["flip_down"] == 0


def test_breadth_flat_is_not_a_flip():
    """从 +0.2 变成 0.0 不算翻向——平盘不是方向。"""
    br = mp.breadth({"a": 0.0}, {"a": 0.2})
    assert br["flip_down"] == 0
    assert br["flip_up"] == 0


def test_breadth_no_past_gives_zero_flips():
    br = mp.breadth({"a": 1.0})
    assert br["flip_down"] == 0 and br["flip_up"] == 0


# --- verdict 判定 ----------------------------------------------------------

def test_verdict_alone_falling():
    """池内 34/38 上涨（89% ≥ 60%），你的票在跌 —— 独跌。"""
    br = {"up": 34, "down": 4, "flat": 0, "valid": 38}
    assert mp.verdict(br, -1.2)[0] == "独跌"


def test_verdict_falling_together():
    br = {"up": 5, "down": 30, "flat": 3, "valid": 38}
    assert mp.verdict(br, -1.2)[0] == "同跌"


def test_verdict_rising_together():
    br = {"up": 34, "down": 4, "flat": 0, "valid": 38}
    assert mp.verdict(br, 2.35)[0] == "同涨"


def test_verdict_mixed():
    br = {"up": 20, "down": 18, "flat": 0, "valid": 38}
    assert mp.verdict(br, -1.2)[0] == "分化"


def test_verdict_boundary_is_inclusive():
    """正好 60% 算命中。38 只的 60% 是 22.8，所以 23 只命中。"""
    br = {"up": 23, "down": 15, "flat": 0, "valid": 38}
    assert mp.verdict(br, -0.5)[0] == "独跌"
    br2 = {"up": 22, "down": 16, "flat": 0, "valid": 38}
    assert mp.verdict(br2, -0.5)[0] == "分化"


def test_verdict_denominator_follows_valid_count():
    """分母是有效票数，不是固定的 38。

    35 只有效时 60% 对应 21 只，所以 21 只上涨就命中。
    """
    br = {"up": 21, "down": 14, "flat": 0, "valid": 35}
    assert mp.verdict(br, -0.5)[0] == "独跌"


def test_verdict_flat_stock_is_mixed():
    """个股正好平盘一律判分化，不进前三行。"""
    br = {"up": 34, "down": 4, "flat": 0, "valid": 38}
    assert mp.verdict(br, 0.0)[0] == "分化"


def test_verdict_returns_none_when_sample_too_small():
    """有效票不足 20 只不出判定。

    只有 18 只返回而其中 12 只在涨，算出 67% 照样会打印「同涨」，
    但另外 20 只的涨跌完全未知；若丢的恰好是 12 只科创板票，
    剩下的池子就缺了半导体设备和晶圆制造，结论会系统性偏向剩余赛道。
    这是方向性错误，不是精度问题。
    """
    br = {"up": 12, "down": 6, "flat": 0, "valid": 18}
    got, why = mp.verdict(br, -1.2)
    assert got is None
    assert "样本不足" in why
    assert "18" in why


def test_verdict_note_mentions_ratio():
    br = {"up": 34, "down": 4, "flat": 0, "valid": 38}
    _, why = mp.verdict(br, 2.35)
    assert "34/38" in why
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q -k "breadth or verdict"
```

Expected: FAIL，`AttributeError: module 'market_pulse' has no attribute 'breadth'`

- [ ] **Step 3: 写最小实现**

追加到 `market_pulse.py`：

```python
def breadth(rs_now, rs_past=None):
    """涨跌家数与翻向数。

    rs_now / rs_past 形如 {代码: 涨跌幅%}，值为 None 表示停牌，直接剔除。
    平盘（r == 0）不算翻向——平盘不是一个方向。
    """
    up = down = flat = 0
    for r in rs_now.values():
        if r is None:
            continue
        if r > 0:
            up += 1
        elif r < 0:
            down += 1
        else:
            flat += 1
    flip_down = flip_up = 0
    if rs_past:
        for code, now in rs_now.items():
            past = rs_past.get(code)
            if now is None or past is None or now == 0 or past == 0:
                continue
            if past > 0 > now:
                flip_down += 1
            elif past < 0 < now:
                flip_up += 1
    return {"up": up, "down": down, "flat": flat, "valid": up + down + flat,
            "flip_down": flip_down, "flip_up": flip_up}


def verdict(br, r_stock):
    """回答「是我独跌还是大家都跌」，返回 (判定, 说明)。

    只用计数和比例，不设需要标定的速度阈值——15 秒粒度的历史分布还没积累出来，
    在标定之前给「跌得快/慢」这类定性词是没有依据的。

    比例的分母是有效票数不是固定的 38；正好等于 VERDICT_RATIO 算命中。
    有效票少于 MIN_VALID 时返回 (None, 原因)。
    """
    valid = br["valid"]
    if valid < MIN_VALID:
        return None, f"样本不足（仅 {valid} 只有效，需 {MIN_VALID} 只）"
    up_ratio = br["up"] / valid
    down_ratio = br["down"] / valid
    note = (f"池内 {br['up']}/{valid} 上涨（{up_ratio * 100:.0f}%）、"
            f"{br['down']}/{valid} 下跌（{down_ratio * 100:.0f}%）")
    if r_stock is None or r_stock == 0:
        return "分化", note
    if up_ratio >= VERDICT_RATIO:
        return ("同涨" if r_stock > 0 else "独跌"), note
    if down_ratio >= VERDICT_RATIO and r_stock < 0:
        return "同跌", note
    return "分化", note
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 51 passed

- [ ] **Step 5: 提交**

```bash
git add market_pulse.py test_market_pulse.py
git commit -m "feat(market_pulse): 宽度统计与独跌/同跌判定"
```

---

### Task 5: 超额与池内排名

**Files:**
- Modify: `market_pulse.py`
- Modify: `test_market_pulse.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `excess(r_stock, r_bench) -> float | None`
  - `rank(r_stock, all_rs) -> tuple[int | None, int]` —— 返回 `(名次, 有效数)`

- [ ] **Step 1: 写失败的测试**

追加到 `test_market_pulse.py`：

```python
# --- excess 超额 -----------------------------------------------------------

def test_excess_normal():
    """2026-08-07 11:30 实测：中际旭创 +2.35%，科技池中位 +1.66%。"""
    assert mp.excess(2.35, 1.66) == pytest.approx(0.69)


def test_excess_negative():
    assert mp.excess(-1.20, 1.66) == pytest.approx(-2.86)


def test_excess_none_when_bench_missing():
    assert mp.excess(2.35, None) is None


def test_excess_none_when_stock_missing():
    assert mp.excess(None, 1.66) is None


# --- rank 池内排名 ---------------------------------------------------------

def test_rank_normal():
    """名次 = 严格大于你的票数 + 1。"""
    assert mp.rank(3.0, [5.0, 3.0, 1.0]) == (2, 3)


def test_rank_first():
    assert mp.rank(5.0, [5.0, 3.0, 1.0]) == (1, 3)


def test_rank_ties_share_place_and_skip():
    """并列给相同名次，之后跳号：[5.0, 3.0, 3.0, 1.0] 的名次是 1、2、2、4。"""
    all_rs = [5.0, 3.0, 3.0, 1.0]
    assert mp.rank(5.0, all_rs) == (1, 4)
    assert mp.rank(3.0, all_rs) == (2, 4)
    assert mp.rank(1.0, all_rs) == (4, 4)


def test_rank_denominator_is_valid_count():
    """分母是有效票数：38 只里 3 只停牌，分母是 35。"""
    all_rs = [1.0] * 35 + [None, None, None]
    _, n = mp.rank(1.0, all_rs)
    assert n == 35


def test_rank_none_when_stock_missing():
    assert mp.rank(None, [1.0, 2.0]) == (None, 2)


def test_rank_none_when_pool_empty():
    assert mp.rank(1.0, []) == (None, 0)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q -k "excess or rank"
```

Expected: FAIL，`AttributeError: module 'market_pulse' has no attribute 'excess'`

- [ ] **Step 3: 写最小实现**

追加到 `market_pulse.py`：

```python
def excess(r_stock, r_bench):
    """超额 = 个股涨跌幅 − 基准涨跌幅，单位百分点(pp)。"""
    if r_stock is None or r_bench is None:
        return None
    return r_stock - r_bench


def rank(r_stock, all_rs):
    """池内名次，返回 (名次, 有效数)。

    名次 = 涨跌幅严格大于你的票数 + 1。并列给相同名次，之后跳号
    （[5.0, 3.0, 3.0, 1.0] 的名次是 1、2、2、4）。
    分母是有效票数，停牌票（None）不计入。

    超额单独看没有信息量（+0.69pp 是强是弱看不出来），配上名次才有参照点。
    """
    valid = [r for r in all_rs if r is not None]
    if r_stock is None or not valid:
        return None, len(valid)
    return sum(1 for r in valid if r > r_stock) + 1, len(valid)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 61 passed

- [ ] **Step 5: 提交**

```bash
git add market_pulse.py test_market_pulse.py
git commit -m "feat(market_pulse): 超额收益与池内排名"
```

---

### Task 6: 时间戳解析与快照取值

**Files:**
- Modify: `market_pulse.py`
- Modify: `test_market_pulse.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `parse_ts(s: str) -> datetime.datetime` —— 入参 `"2026-08-07 13:24:15"`
  - `pick_snapshot(store, target, tol_sec) -> dict | None` —— `store` 是快照列表（每项含 `"t"` 字段），`target` 是 `datetime`

- [ ] **Step 1: 写失败的测试**

追加到 `test_market_pulse.py`：

```python
# --- parse_ts / pick_snapshot ---------------------------------------------

def test_parse_ts():
    got = mp.parse_ts("2026-08-07 13:24:15")
    assert (got.year, got.month, got.day) == (2026, 8, 7)
    assert (got.hour, got.minute, got.second) == (13, 24, 15)


def test_parse_ts_rejects_garbage():
    with pytest.raises(ValueError):
        mp.parse_ts("13:24:15")


def _snap(ts, r=2.35):
    """落盘存的是涨跌幅 r（%），不是价格——见 spec §4.2。"""
    return {"t": ts, "r": {"sz300308": r}, "idx": {}}


def test_pick_snapshot_exact():
    store = [_snap("2026-08-07 13:24:00"), _snap("2026-08-07 13:24:15")]
    got = mp.pick_snapshot(store, mp.parse_ts("2026-08-07 13:24:00"), 5)
    assert got["t"] == "2026-08-07 13:24:00"


def test_pick_snapshot_within_tolerance():
    """目标 13:24:00、容差 5 秒，13:24:02 的快照可用。"""
    store = [_snap("2026-08-07 13:24:02")]
    got = mp.pick_snapshot(store, mp.parse_ts("2026-08-07 13:24:00"), 5)
    assert got["t"] == "2026-08-07 13:24:02"


def test_pick_snapshot_beyond_tolerance_returns_none():
    """目标 13:24:00，最近的是 13:23:50，偏差 10 秒 > 容差 5 秒 —— 不可用。

    宁可标注不可用，也不拿一个口径不同的数去凑。
    """
    store = [_snap("2026-08-07 13:23:50")]
    assert mp.pick_snapshot(store, mp.parse_ts("2026-08-07 13:24:00"), 5) is None


def test_pick_snapshot_picks_nearest():
    store = [_snap("2026-08-07 13:23:58"), _snap("2026-08-07 13:24:04")]
    got = mp.pick_snapshot(store, mp.parse_ts("2026-08-07 13:24:00"), 5)
    assert got["t"] == "2026-08-07 13:23:58"      # 偏差 2 秒 < 4 秒


def test_pick_snapshot_empty_store():
    assert mp.pick_snapshot([], mp.parse_ts("2026-08-07 13:24:00"), 5) is None


def test_pick_snapshot_tolerance_is_third_of_window():
    """15 秒窗口的容差是 5 秒，300 秒窗口是 100 秒。"""
    assert mp.window_tolerance(15) == pytest.approx(5.0)
    assert mp.window_tolerance(60) == pytest.approx(20.0)
    assert mp.window_tolerance(300) == pytest.approx(100.0)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q -k "parse_ts or pick_snapshot"
```

Expected: FAIL，`AttributeError: module 'market_pulse' has no attribute 'parse_ts'`

- [ ] **Step 3: 写最小实现**

在 `market_pulse.py` 顶部加 `import datetime`，然后追加：

```python
TS_FMT = "%Y-%m-%d %H:%M:%S"


def parse_ts(s):
    """解析落盘里的时间戳 "2026-08-07 13:24:15"。格式不对抛 ValueError。"""
    return datetime.datetime.strptime(s, TS_FMT)


def window_tolerance(window_sec):
    """窗口取值的允许偏差 = 窗口长度的三分之一。

    采样点不会正好落在 t − w 上，所以要给一个容差。取三分之一是因为
    3 秒采样下，15 秒窗口的容差 5 秒刚好覆盖一到两个采样间隔。
    """
    return window_sec / 3.0


def pick_snapshot(store, target, tol_sec):
    """取时间戳离 target 最近、且偏差不超过 tol_sec 的那条快照。

    偏差超限返回 None——宁可在面板上标注不可用，也不拿一个口径不同的数去凑。
    """
    best, best_gap = None, None
    for snap in store:
        gap = abs((parse_ts(snap["t"]) - target).total_seconds())
        if gap > tol_sec:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = snap, gap
    return best
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 69 passed

- [ ] **Step 5: 提交**

```bash
git add market_pulse.py test_market_pulse.py
git commit -m "feat(market_pulse): 时间戳解析与按容差取历史快照"
```

---

### Task 7: 落盘读写与跨日清理

**依赖：** Task 2（`in_session`）、Task 6（`parse_ts`）

**Files:**
- Modify: `market_pulse.py`
- Modify: `test_market_pulse.py`

**Interfaces:**
- Consumes: `in_session`、`parse_ts`、`STORE`、`STALE_SEC`
- Produces:
  - `append_store(snap, path=STORE) -> bool` —— 返回是否真的写了
  - `load_store(seconds, now=None, path=STORE) -> list[dict]`
  - `rotate_store(today, path=STORE) -> bool` —— 返回是否清空了
  - `store_status(store, now) -> tuple[str, str]` —— 返回 `(状态码, 说明)`，状态码取 `"ok"` / `"not_running"` / `"warming_up"`

- [ ] **Step 1: 写失败的测试**

追加到 `test_market_pulse.py`：

```python
# --- 落盘 IO --------------------------------------------------------------

def test_append_store_writes_in_session(tmp_path):
    p = tmp_path / "s.jsonl"
    snap = {"t": "2026-08-07 13:24:15", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(snap, path=str(p)) is True
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_append_store_skips_outside_session(tmp_path):
    """午休不写——避免文件里灌一堆重复快照。"""
    p = tmp_path / "s.jsonl"
    snap = {"t": "2026-08-07 12:00:00", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(snap, path=str(p)) is False
    assert not p.exists()


def test_load_store_roundtrip(tmp_path):
    p = tmp_path / "s.jsonl"
    now = mp.parse_ts("2026-08-07 13:25:00")
    for i in range(10):
        mp.append_store({"t": f"2026-08-07 13:24:{i:02d}",
                         "r": {"sz300308": 2.0 + i * 0.1}, "idx": {}}, path=str(p))
    got = mp.load_store(120, now=now, path=str(p))
    assert len(got) == 10
    assert got[0]["t"] == "2026-08-07 13:24:00"
    assert got[-1]["r"]["sz300308"] == pytest.approx(2.9)


def test_load_store_filters_by_age(tmp_path):
    """只要最近 N 秒的，更早的不读。"""
    p = tmp_path / "s.jsonl"
    for ts in ("13:20:00", "13:24:00", "13:24:50"):
        mp.append_store({"t": f"2026-08-07 {ts}", "r": {}, "idx": {}}, path=str(p))
    got = mp.load_store(120, now=mp.parse_ts("2026-08-07 13:25:00"), path=str(p))
    assert [s["t"] for s in got] == ["2026-08-07 13:24:00", "2026-08-07 13:24:50"]


def test_load_store_skips_corrupt_lines(tmp_path):
    """损坏行跳过继续读，不让整个文件报废。"""
    p = tmp_path / "s.jsonl"
    p.write_text('{"t":"2026-08-07 13:24:00","r":{},"idx":{}}\n'
                 'NOT JSON\n'
                 '{"t":"2026-08-07 13:24:30","r":{},"idx":{}}\n', encoding="utf-8")
    got = mp.load_store(120, now=mp.parse_ts("2026-08-07 13:25:00"), path=str(p))
    assert len(got) == 2


def test_load_store_missing_file(tmp_path):
    assert mp.load_store(120, now=mp.parse_ts("2026-08-07 13:25:00"),
                         path=str(tmp_path / "nope.jsonl")) == []


def test_rotate_store_clears_stale_day(tmp_path):
    """昨天的记录必须清掉——昨收接今开会算出跨夜跳空的假速度。"""
    p = tmp_path / "s.jsonl"
    p.write_text('{"t":"2026-08-06 14:00:00","r":{},"idx":{}}\n', encoding="utf-8")
    assert mp.rotate_store("2026-08-07", path=str(p)) is True
    assert p.read_text(encoding="utf-8") == ""


def test_rotate_store_keeps_today(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"t":"2026-08-07 09:31:00","r":{},"idx":{}}\n', encoding="utf-8")
    assert mp.rotate_store("2026-08-07", path=str(p)) is False
    assert p.read_text(encoding="utf-8") != ""


def test_rotate_store_missing_file(tmp_path):
    assert mp.rotate_store("2026-08-07", path=str(tmp_path / "nope.jsonl")) is False


# --- store_status 三种不可用状态 -------------------------------------------

def test_store_status_not_running_when_empty():
    got, why = mp.store_status([], mp.parse_ts("2026-08-07 13:25:00"))
    assert got == "not_running"
    assert "悬浮窗未启动" in why


def test_store_status_not_running_when_stale():
    """最后一条超过 STALE_SEC（60 秒）视为采集已停。"""
    store = [{"t": "2026-08-07 13:23:00", "r": {}, "idx": {}}]
    got, _ = mp.store_status(store, mp.parse_ts("2026-08-07 13:25:00"))
    assert got == "not_running"


def test_store_status_warming_up():
    """采集刚启动，最早一条距现在不足最长窗口（300 秒）。"""
    store = [{"t": "2026-08-07 13:24:13", "r": {}, "idx": {}},
             {"t": "2026-08-07 13:25:00", "r": {}, "idx": {}}]
    got, why = mp.store_status(store, mp.parse_ts("2026-08-07 13:25:00"))
    assert got == "warming_up"
    assert "47" in why and "300" in why


def test_store_status_ok():
    store = [{"t": "2026-08-07 13:19:00", "r": {}, "idx": {}},
             {"t": "2026-08-07 13:25:00", "r": {}, "idx": {}}]
    got, _ = mp.store_status(store, mp.parse_ts("2026-08-07 13:25:00"))
    assert got == "ok"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q -k "store"
```

Expected: FAIL，`AttributeError: module 'market_pulse' has no attribute 'append_store'`

- [ ] **Step 3: 写最小实现**

在 `market_pulse.py` 顶部加 `import json`，然后追加：

```python
def append_store(snap, path=STORE):
    """把一条快照追加落盘，返回是否真的写了。

    非交易时段直接跳过：午休和盘后行情不变，写进去只是一堆重复记录，
    还会污染速度窗口的取值。
    """
    if not in_session(snap["t"].split(" ")[-1]):
        return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return True


def load_store(seconds, now=None, path=STORE):
    """读回最近 seconds 秒的快照，按时间升序。

    损坏行跳过继续读——一行 JSON 写坏不该让整个文件报废。
    文件不存在返回空列表。
    """
    if not os.path.exists(path):
        return []
    now = now or now_bj()      # 必须北京时间，本机是 JST 快 1 小时
    cutoff = now - datetime.timedelta(seconds=seconds)
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
                ts = parse_ts(snap["t"])
            except (ValueError, KeyError, TypeError):
                continue
            if ts >= cutoff:
                out.append(snap)
    out.sort(key=lambda s: s["t"])
    return out


def rotate_store(today, path=STORE):
    """最后一条不是今天就清空，返回是否清空了。

    昨天的价格接到今天的序列上，会算出一个跨夜跳空的假速度。
    """
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as fh:
        lines = [x for x in fh if x.strip()]
    if not lines:
        return False
    try:
        last_day = json.loads(lines[-1])["t"].split(" ")[0]
    except (ValueError, KeyError, IndexError):
        last_day = None
    if last_day == today:
        return False
    open(path, "w", encoding="utf-8").close()
    return True


def store_status(store, now):
    """判断落盘数据能不能用，返回 (状态码, 说明)。

    三种状态分别对应不同的面板文案，都不给数字、也不用别的数据源凑。
    """
    if not store:
        return "not_running", "不可用（悬浮窗未启动）"
    age = (now - parse_ts(store[-1]["t"])).total_seconds()
    if age > STALE_SEC:
        return "not_running", f"不可用（悬浮窗未启动，最后一条在 {age:.0f} 秒前）"
    span = (now - parse_ts(store[0]["t"])).total_seconds()
    longest = max(WINDOWS)
    if span < longest:
        return "warming_up", f"不可用（已采集 {span:.0f} 秒，需 {longest} 秒）"
    return "ok", ""
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 82 passed

- [ ] **Step 5: 提交**

```bash
git add market_pulse.py test_market_pulse.py
git commit -m "feat(market_pulse): 快照落盘、跨日清理与三种不可用状态"
```

---

### Task 8: 面板与单行渲染

**依赖：** Task 3、4、5

**Files:**
- Modify: `market_pulse.py`
- Modify: `test_market_pulse.py`

**Interfaces:**
- Consumes: `speed`、`aggregate`、`breadth`、`verdict`、`excess`、`rank`、`IDX_NAMES`
- Produces:
  - `display_width(s: str) -> int` —— 中文计 2、ASCII 计 1
  - `render_panel(state: dict) -> str`
  - `render_strip(state: dict) -> str`
  - `state` 的结构在下面的实现代码里定义，Task 9 按它组装

- [ ] **Step 1: 写失败的测试**

追加到 `test_market_pulse.py`：

```python
# --- 渲染 -----------------------------------------------------------------

def test_display_width_mixes_cjk_and_ascii():
    assert mp.display_width("abc") == 3
    assert mp.display_width("中际旭创") == 8
    assert mp.display_width("池+1.66%") == 8      # 1 个中文(2) + 6 个 ASCII(6)


@pytest.mark.parametrize("s,width", [
    ("中际旭创", 16), ("MLCC(2只)", 16), ("AI算力芯片(3只)", 16), ("", 16),
])
def test_pad_l_aligns_by_display_width(s, width):
    """按显示宽度补齐，不是按码点。

    真实赛道名的中文字数差很多——MLCC(2只) 只有 1 个中文、
    AI算力芯片(3只) 有 5 个。按码点补齐会让面板右缘参差 17 到 22 不等，
    而这个面板的全部价值就是一眼看懂。
    """
    assert mp.display_width(mp._pad_l(s, width)) == width


def test_pad_r_aligns_by_display_width():
    assert mp.display_width(mp._pad_r("15秒", 9)) == 9
    assert mp.display_width(mp._pad_r("-0.21", 9)) == 9


def test_pad_does_not_truncate_when_too_long():
    """超长不截断——截断会把股票名切掉，宁可这一行歪掉。"""
    assert mp._pad_l("中际旭创国际复材长鑫科技", 8) == "中际旭创国际复材长鑫科技"


def _state(**over):
    """构造一个数据齐全的 state，测试按需覆盖字段。"""
    base = {
        "ts": "2026-08-07 13:24:15",
        "valid": 38, "total": 38,
        "status": ("ok", ""),
        "rows": [
            {"name": "中际旭创", "speeds": [-0.21, -0.55, -1.32]},
            {"name": "国际复材", "speeds": [0.30, 0.81, 2.10]},
            {"name": "长鑫科技", "speeds": [None, None, None], "dash": True},
            {"name": "光模块(3只)", "speeds": [-0.15, -0.41, -0.98]},
            {"name": "电子布(3只)", "speeds": [0.22, 0.65, 1.80]},
            {"name": "科技池(38只)", "speeds": [-0.08, -0.22, -0.51]},
            {"name": "创业板指", "speeds": [-0.04, -0.13, -0.30]},
        ],
        "breadth": {"up": 34, "down": 4, "flat": 0, "valid": 38,
                    "flip_down": 3, "flip_up": 0},
        "holdings": [
            {"name": "中际旭创", "r": 2.35, "excess": 0.69, "rank": (17, 38),
             "in_pool": True},
            {"name": "国际复材", "r": 7.24, "excess": 5.58, "rank": (4, 38),
             "in_pool": True},
            {"name": "长鑫科技", "r": -0.17, "excess": -1.83, "rank": (None, 38),
             "in_pool": False},
        ],
        "verdict": ("同涨", "池内 34/38 上涨（89%）、4/38 下跌（11%）"),
        "pool_median": 1.66,
        "dropped": 0,
    }
    base.update(over)
    return base


def test_render_panel_has_all_three_blocks():
    out = mp.render_panel(_state())
    assert "【速度】" in out and "【宽度】" in out and "【相对强弱】" in out
    assert "【判定】" in out


def test_render_panel_shows_speed_numbers():
    out = mp.render_panel(_state())
    assert "-0.21" in out and "-1.32" in out


def test_render_panel_speed_cells_have_no_numbers_when_unavailable():
    """硬约束的守护测试：速度不可用时，那一行不能出现任何数字。

    断言的是速度行本身，不是整个面板。2026-08-07 审查发现的教训：
    原来那版把 status 设成 not_running，于是面板底部会多一行
    「不可用（悬浮窗未启动）」，`"不可用" in out` 由那行满足，
    和速度单元格毫无关系——`_fmt_speed(None)` 就算回归成返回 "+0.00"
    去凑数，那版测试照样通过。所以这里把 status 设成 ok、holdings 清空，
    让「不可用」只可能来自速度单元格，再断言整行没有数字。
    """
    st = _state(rows=[{"name": "中际旭创", "speeds": [None, None, None]}],
                holdings=[], status=("ok", ""))
    line = [x for x in mp.render_panel(st).splitlines() if "中际旭创" in x][0]
    assert "不可用" in line
    assert not any(ch.isdigit() for ch in line)


def test_render_panel_distinguishes_dash_from_unavailable():
    """池外票显示「—」，采集故障显示「不可用」，两者不能混。

    长鑫科技不在 38 只池内、不落盘，它没有速度是结构性的；
    而采集没跑起来是故障。混成一种会让面板自相矛盾。
    """
    st = _state(rows=[{"name": "长鑫科技", "speeds": [None] * 3, "dash": True},
                      {"name": "中际旭创", "speeds": [None] * 3}],
                holdings=[], status=("ok", ""))
    out = mp.render_panel(st).splitlines()
    cx = [x for x in out if "长鑫科技" in x][0]
    zj = [x for x in out if "中际旭创" in x][0]
    assert "—" in cx and "不可用" not in cx
    assert "不可用" in zj and "—" not in zj


def test_render_panel_window_labels_are_human_readable():
    """表头用「1分钟 / 5分钟」，不用「60秒 / 300秒」。

    这个面板的价值是盘中一眼看懂，让读者把 300 秒心算成 5 分钟
    直接削弱它存在的理由。spec §6.1 的样例就是这么写的。
    """
    out = mp.render_panel(_state())
    assert "15秒" in out and "1分钟" in out and "5分钟" in out
    assert "60秒" not in out and "300秒" not in out


def test_render_panel_shows_sample_too_small():
    st = _state(valid=18, verdict=(None, "样本不足（仅 18 只有效，需 20 只）"))
    out = mp.render_panel(st)
    assert "样本不足" in out


def test_render_panel_reports_dropped_lines():
    out = mp.render_panel(_state(dropped=3))
    assert "跳过 3 行损坏记录" in out


def test_render_panel_shows_dash_for_pool_outsider():
    """长鑫科技不在 38 只池内：速度栏是「—」，排名标「不在池内」。

    2026-08-07 审查发现的教训：原来那版只断言 `"不在池内" in out`，
    而那句话来自【相对强弱】节的排名文本，和速度栏渲染毫无关系——
    速度栏渲染成「不可用」（违反 spec）它照样通过。所以这里分别
    定位到两行各自断言。
    """
    rows = [{"name": "长鑫科技", "speeds": [None] * 3, "dash": True}]
    st = _state(rows=rows)
    out = mp.render_panel(st).splitlines()
    speed_line = [x for x in out if "长鑫科技" in x][0]
    rank_line = [x for x in out if "长鑫科技" in x][-1]
    assert "—" in speed_line and "不可用" not in speed_line
    assert "不在池内" in rank_line


def test_render_strip_handles_missing_excess():
    """超额为 None 时不能崩，也不能格式化 None。"""
    st = _state(holdings=[{"name": "中际旭创", "r": 2.35, "excess": None,
                           "rank": (17, 38), "in_pool": True}])
    assert mp.display_width(mp.render_strip(st)) <= 40


def test_render_strip_handles_missing_rank():
    st = _state(holdings=[{"name": "长鑫科技", "r": -0.17, "excess": -1.83,
                           "rank": (None, 38), "in_pool": False}])
    assert mp.display_width(mp.render_strip(st)) <= 40


def test_render_strip_handles_empty_holdings():
    out = mp.render_strip(_state(holdings=[]))
    assert "同涨" in out
    assert mp.display_width(out) <= 40


def test_render_panel_has_sector_and_index_rows():
    """面板速度栏固定顺序：持仓票 → 赛道 → 科技池 → 创业板指。"""
    out = mp.render_panel(_state())
    for name in ("光模块(3只)", "电子布(3只)", "科技池(38只)", "创业板指"):
        assert name in out


def test_render_strip_within_width_limit():
    """悬浮窗横条空间有限，上限 40 个显示宽度单位。"""
    out = mp.render_strip(_state())
    assert mp.display_width(out) <= 40


def test_render_strip_contains_verdict_and_rank():
    out = mp.render_strip(_state())
    assert "同涨" in out
    assert "17/38" in out


def test_render_strip_when_unavailable():
    st = _state(status=("not_running", "不可用（悬浮窗未启动）"),
                verdict=(None, "样本不足（仅 18 只有效，需 20 只）"))
    out = mp.render_strip(st)
    assert "不可用" in out or "样本不足" in out
    assert mp.display_width(out) <= 40
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q -k "render or display_width"
```

Expected: FAIL，`AttributeError: module 'market_pulse' has no attribute 'display_width'`

- [ ] **Step 3: 写最小实现**

在 `market_pulse.py` 顶部加 `import unicodedata`，然后追加：

```python
def display_width(s):
    """终端显示宽度：全角/宽字符计 2，其余计 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _pad_l(s, width):
    """按显示宽度左对齐补空格（中文计 2）。超长不截断——截断会把股票名切掉。"""
    return s + " " * max(0, width - display_width(s))


def _pad_r(s, width):
    """按显示宽度右对齐补空格。"""
    return " " * max(0, width - display_width(s)) + s


WINDOW_LABELS = {15: "15秒", 60: "1分钟", 300: "5分钟"}


def _fmt_speed(v, dash=False):
    """速度单元格。

    两种 None 含义不同，必须分开显示：
      dash=True  —— 这只票结构性地没有数据（池外持仓票不落盘），显示「—」
      dash=False —— 有数据但这次取不到（采集未启动、窗口无快照），显示「不可用」
    混成一种会让面板自相矛盾：同一只长鑫科技，速度栏说「不可用」暗示采集出了问题，
    排名栏却说「不在池内」是结构性的。
    """
    if v is None:
        return "—" if dash else "不可用"
    return f"{v:+.2f}"


def render_panel(state):
    """命令行完整面板。state 的结构见 test_market_pulse._state()。

    所有列都用 _pad_l / _pad_r 按显示宽度对齐，不能用 ljust/rjust——
    那两个按码点补齐，而赛道名的中文字数差很多（MLCC(2只) 1 个中文、
    AI算力芯片(3只) 5 个），按码点补会让右缘参差。
    """
    lines = [f"【市场脉搏 {state['ts']}】采样 3 秒 · 池内有效 "
             f"{state['valid']}/{state['total']}", ""]

    lines.append("【速度】单位 pp（该窗口内涨跌幅的变化量）")
    lines.append("  " + _pad_l("", 16)
                 + "".join(_pad_r(WINDOW_LABELS[w], 9) for w in WINDOWS))
    for row in state["rows"]:
        dash = row.get("dash", False)
        lines.append("  " + _pad_l(row["name"], 16)
                     + "".join(_pad_r(_fmt_speed(v, dash), 9)
                               for v in row["speeds"]))
    if state["status"][0] != "ok":
        lines.append(f"  {state['status'][1]}")
    lines.append("")

    br = state["breadth"]
    lines.append("【宽度】")
    lines.append(f"  池内 上涨 {br['up']} / 平盘 {br['flat']} / 下跌 {br['down']}")
    lines.append(f"  最近 1 分钟翻向：涨转跌 {br['flip_down']} 只，"
                 f"跌转涨 {br['flip_up']} 只")
    lines.append("")

    lines.append("【相对强弱】")
    for h in state["holdings"]:
        place, total = h["rank"]
        if place is None:
            pos = "—（不在池内）" if not h.get("in_pool", True) else "—"
        else:
            pos = f"{place}/{total}（前 {place / total * 100:.0f}%）"
        exc = "—" if h["excess"] is None else f"{h['excess']:+.2f}pp"
        r_txt = "—" if h["r"] is None else f"{h['r']:+.2f}%"
        lines.append("  " + _pad_l(h["name"], 10) + _pad_r(r_txt, 9)
                     + "  超额(vs池) " + _pad_r(exc, 9) + "  排名 " + pos)
    lines.append("")

    got, why = state["verdict"]
    lines.append(f"【判定】{why}" if got is None else f"【判定】{got} —— {why}")
    if state.get("dropped"):
        lines.append(f"  跳过 {state['dropped']} 行损坏记录")
    return "\n".join(lines)


def render_strip(state):
    """悬浮窗单行文案，显示宽度不超过 40。

    速度三窗放不进一行，只在命令行面板出现。超限时优先砍池子涨跌幅，
    保留判定和排名——那两个才是「要不要紧」的直接答案。
    """
    got, _ = state["verdict"]
    if got is None:
        return "样本不足" if state["status"][0] == "ok" else "行情不可用"
    br = state["breadth"]
    h = state["holdings"][0] if state["holdings"] else None
    if h is None:
        return f"池{state['pool_median']:+.2f}% {br['up']}/{br['down']} {got}"
    place, total = h["rank"]
    tail = (f"你{h['excess']:+.2f}pp {place}/{total} {got}"
            if h["excess"] is not None and place else f"你 — {got}")
    full = f"池{state['pool_median']:+.2f}% {br['up']}/{br['down']}  {tail}"
    return full if display_width(full) <= 40 else tail
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 105 passed（以实测为准）

- [ ] **Step 5: 提交**

```bash
git add market_pulse.py test_market_pulse.py
git commit -m "feat(market_pulse): 命令行面板与悬浮窗单行渲染"
```

---

### Task 9: 取数、状态组装、CLI 与集成测试

**依赖：** Task 1、6、7、8

**Files:**
- Modify: `market_pulse.py`
- Create: `test_market_pulse_e2e.py`

**Interfaces:**
- Consumes: 上面所有函数、`stock_watch.fetch_quotes`
- Produces:
  - `parse_pool_quotes(raw_quotes) -> dict` —— 把 `fetch_quotes` 的返回转成 `{代码: {"r": 涨跌幅, "px": 现价}}`，停牌票剔除
  - `build_state(store, now, holdings) -> dict` —— 组装 `render_panel` / `render_strip` 需要的 state
  - `main()` —— 命令行入口

- [ ] **Step 1: 写失败的测试**

追加到 `test_market_pulse.py`：

```python
# --- parse_pool_quotes / build_state --------------------------------------

def test_parse_pool_quotes_computes_r():
    raw = [{"code": "sz300308", "current": 977.45, "prev_close": 955.0, "ok": True}]
    got = mp.parse_pool_quotes(raw)
    assert got["sz300308"]["r"] == pytest.approx(2.3508, abs=1e-3)


def test_parse_pool_quotes_drops_suspended():
    """停牌（现价为 0）和昨收为 0 的票直接剔除，不列名、不提示。"""
    raw = [{"code": "a", "current": 0.0, "prev_close": 10.0, "ok": True},
           {"code": "b", "current": 10.0, "prev_close": 0.0, "ok": True},
           {"code": "c", "current": 11.0, "prev_close": 10.0, "ok": True}]
    got = mp.parse_pool_quotes(raw)
    assert set(got) == {"c"}


def test_parse_pool_quotes_drops_failed():
    raw = [{"code": "a", "current": None, "prev_close": None, "ok": False},
           {"code": "c", "current": 11.0, "prev_close": 10.0, "ok": True}]
    assert set(mp.parse_pool_quotes(raw)) == {"c"}


def test_parse_pool_quotes_rounds_r_once():
    """涨跌幅在 parse_pool_quotes 一次性定死 3 位小数，下游不再各自舍入。

    2026-08-07 实测踩到的坑：main() 落盘时 round(r,3)、live_r 用原始值，
    同一只票在 store 和 live_r 里取值不同，rank 比较时把自己也数了进去。
    """
    raw = [{"code": "a", "current": 977.45, "prev_close": 955.0, "ok": True}]
    assert mp.parse_pool_quotes(raw)["a"]["r"] == 2.351


def test_rank_never_exceeds_pool_size():
    """名次不能超过有效票数——防「排名 39/38（前 103%）」复现。"""
    raw = 2.350785340314141
    all_rs = [round(raw, 3)] + [1.0] * 37
    place, total = mp.rank(round(raw, 3), all_rs)
    assert place <= total
    assert place == 1


def test_build_state_rank_within_pool_size():
    """组装出来的持仓名次不能超过有效票数。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"))
    for h in st["holdings"]:
        place, total = h["rank"]
        if place is not None:
            assert place <= total, f"{h['name']} 名次 {place}/{total} 越界"


def test_build_state_rank_survives_precision_divergence():
    """store 与 live_r 精度不一致时，池内票的名次仍不能越界。

    2026-08-07 审查指出：上一版那条「端到端守护」让 live_r 走默认值
    （= store 的拷贝），精度天然一致，永远构造不出 39/38 的触发条件，
    等于打偏了靶心。这条显式喂进分叉输入——store 存舍入值 2.351、
    live_r 给原始值 2.3508（比 store 里自己那个值小）。
    修法是让池内票的 r 一律取 store 值，所以 live_r 再怎么分叉也影响不到名次。
    """
    raw = 2.350785340314141
    snaps = _two_snaps()
    snaps[-1]["r"]["sz300308"] = round(raw, 3)          # store：2.351
    st = mp.build_state(snaps, mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sz300308": raw})        # live：2.3508，偏小
    zj = [h for h in st["holdings"] if h["name"] == "中际旭创"][0]
    place, total = zj["rank"]
    assert place <= total, f"名次 {place}/{total} 越界——精度分叉又回来了"
    assert zj["r"] == round(raw, 3), "池内票的 r 必须取 store 值，不取 live_r"


def test_build_state_pool_outsider_still_uses_live_r():
    """池外票没进 store，它的 r 必须仍然从 live_r 取。

    上一条把池内票改成取 store 值，不能顺手把池外票也断了——
    长鑫科技不落盘，live_r 是它唯一的数据来源。
    """
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sh688825": -0.17})
    cx = [h for h in st["holdings"] if h["name"] == "长鑫科技"][0]
    assert cx["r"] == pytest.approx(-0.17)


def test_now_bj_returns_beijing_not_local():
    """必须返回北京时间。

    2026-08-07 实测：本机时区是 JST，比北京快 1 小时。若用本机时间
    比对 A 股时段，12 个时点里错 8 个——误采盘前，且尾盘一小时全漏。
    """
    import zoneinfo
    expect = datetime.datetime.now(
        zoneinfo.ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    assert abs((mp.now_bj() - expect).total_seconds()) < 5


def test_now_bj_is_naive():
    """去掉 tzinfo，好和落盘的 naive 时间戳直接相减。"""
    assert mp.now_bj().tzinfo is None


def test_in_session_boundary_is_beijing_clock():
    """时区 bug 的回归守护。

    北京 14:59 在场内、15:59 不在。本机 JST 下北京 14:59 就是本机 15:59，
    若误用本机时间会把尾盘整整一小时判成盘后、全部漏采。
    """
    assert mp.in_session("14:59") is True
    assert mp.in_session("15:59") is False


def test_parse_pool_quotes_uses_stock_watch_field_names():
    """入参字段名必须和 stock_watch.parse_sina_response 的真实输出一致。

    2026-08-07 踩过的坑：计划里写的入参键是 px，而真实字段是 current
    （stock_watch.py:75-78）。单测因为用自造数据而自洽通过，真跑起来
    每只票的 current 都取不到、整批被剔除，池子全空、判定全废。
    这条测试直接拿 parse_sina_response 的真实输出当输入，把契约钉死。
    """
    import stock_watch as sw
    raw = ('var hq_str_sz300308="中际旭创,981.09,955.00,977.45,999.88,963.00,'
           '977.40,977.45,20098011,19775573459,' + ','.join(["0"] * 20) + '";')
    got = mp.parse_pool_quotes(sw.parse_sina_response(raw))
    assert "sz300308" in got, "字段名对不上，真实行情会被整批剔除"
    assert got["sz300308"]["r"] == pytest.approx(2.3508, abs=1e-3)
    assert got["sz300308"]["px"] == pytest.approx(977.45)


def test_build_state_marks_all_windows_unavailable_when_not_running():
    """采集没跑起来时，三个窗口全部 None，不用任何别的数据源凑。"""
    st = mp.build_state([], mp.parse_ts("2026-08-07 13:25:00"), holdings=["sz300308"])
    assert st["status"][0] == "not_running"
    for row in st["rows"]:
        assert row["speeds"] == [None, None, None]


def _two_snaps():
    """构造两条快照：13:24:00 和 13:24:15，中际旭创从 +2.51 掉到 +2.30。"""
    pool = {c: 1.0 for c in mp.POOL_CODES}
    a = dict(pool, sz300308=2.51, sz301526=7.00)
    b = dict(pool, sz300308=2.30, sz301526=7.24)
    return [{"t": "2026-08-07 13:24:00", "r": a, "idx": {"sz399006": 1.70}},
            {"t": "2026-08-07 13:24:15", "r": b, "idx": {"sz399006": 1.75}}]


def test_build_state_rows_in_spec_order():
    """行顺序：持仓票（3 行）→ 赛道（2 行）→ 科技池 → 创业板指 = 7 行。

    长鑫科技不在 SECTOR 里，不产生赛道行。
    """
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sh688825": -0.17})
    names = [r["name"] for r in st["rows"]]
    assert names[:3] == ["中际旭创", "国际复材", "长鑫科技"]
    assert names[3].startswith("光模块") and names[4].startswith("电子布")
    assert names[5].startswith("科技池")
    assert names[6] == "创业板指"


def test_build_state_computes_15s_speed():
    """15 秒窗口：中际旭创从 +2.51 掉到 +2.30，速度 −0.21 pp。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"))
    assert st["rows"][0]["speeds"][0] == pytest.approx(-0.21)


def test_build_state_pool_outsider_has_no_speed():
    """长鑫科技不落盘，三个窗口全是 None。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sh688825": -0.17})
    assert st["rows"][2]["speeds"] == [None, None, None]


def test_build_state_pool_outsider_still_gets_excess():
    """长鑫的相对强弱用实时行情出数，但排名为 None（不在池内）。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sh688825": -0.17})
    cx = [h for h in st["holdings"] if h["name"] == "长鑫科技"][0]
    assert cx["r"] == pytest.approx(-0.17)
    assert cx["excess"] is not None
    assert cx["rank"][0] is None
    assert cx["in_pool"] is False


def test_build_state_breadth_uses_r_not_price():
    """宽度必须基于涨跌幅。

    2026-08-07 设计评审发现的缺陷：落盘若存价格，价格永远为正，
    上涨家数会恒等于 38。这条测试就是那个 bug 的守门人。
    """
    snaps = _two_snaps()
    snaps[-1]["r"]["sz300502"] = -3.0
    st = mp.build_state(snaps, mp.parse_ts("2026-08-07 13:24:15"))
    assert st["breadth"]["down"] >= 1
    assert st["breadth"]["up"] < len(mp.POOL_CODES)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q -k "parse_pool_quotes or build_state"
```

Expected: FAIL，`AttributeError: module 'market_pulse' has no attribute 'parse_pool_quotes'`

- [ ] **Step 3: 写最小实现**

在 `market_pulse.py` 顶部 import 区加 `import zoneinfo`，然后追加：

```python
def parse_pool_quotes(raw_quotes):
    """把 stock_watch.fetch_quotes 的返回转成 {代码: {"r": 涨跌幅%, "px": 现价}}。

    入参的字段名是 current / prev_close，那是 stock_watch.parse_sina_response
    的输出（stock_watch.py:75-78），只在 len(fields) > 9 时才附上；报文太短
    （停牌、未开盘）时这两个键不存在，get 返回 None 会被下面剔除。
    输出侧用 px 这个键名，那是本模块自己的形状。

    这里从原始现价与昨收重算涨跌幅，不用 stock_watch 现成的 change_pct——
    后者四舍五入到两位，速度是两个涨跌幅之差，精度损失会被放大。

    停牌（现价为 0）和昨收为 0 的票直接剔除，不列名、不提示。
    """
    out = {}
    for q in raw_quotes:
        if not q.get("ok"):
            continue
        px, pc = q.get("current"), q.get("prev_close")
        if not px or not pc:
            continue
        # 精度在这里一次性定死，下游不许再各自舍入。
        # 2026-08-07 踩到：main() 落盘时 round(r,3)、live_r 用原始值，
        # 于是持仓票自己的舍入值进了对比池，rank 拿原始值比较时把自己
        # 也数了进去，面板出现「排名 39/38（前 103%）」。
        out[q["code"]] = {"r": round((px - pc) / pc * 100, 3), "px": px}
    return out


def _speeds_for(store, now, key_fn):
    """对每个窗口算一次速度。key_fn(snapshot) 返回该快照对应的涨跌幅。

    key_fn 返回 None（该票没落盘，例如池外的长鑫科技）时，速度也是 None，
    面板渲染成「—」。
    """
    out = []
    now_snap = store[-1] if store else None
    for w in WINDOWS:
        if now_snap is None:
            out.append(None)
            continue
        past = pick_snapshot(store, now - datetime.timedelta(seconds=w),
                             window_tolerance(w))
        out.append(speed(key_fn(now_snap), key_fn(past) if past else None))
    return out


def _sector_key(snap, codes):
    """一个赛道在某条快照上的中位涨跌幅。"""
    return aggregate([snap["r"].get(c) for c in codes])[0]


def build_state(store, now, holdings=HOLD_CODES, live_r=None):
    """组装 render_panel / render_strip 需要的 state。

    store 为空或过期时三个窗口全部返回 None——不用任何别的粒度的数据源凑。

    live_r 是当次实时行情的 {代码: 涨跌幅}，用来给池外持仓票（长鑫科技）
    出「相对强弱」那一节的数字。它不落盘，所以速度栏是 None。
    """
    status = store_status(store, now)
    cur = store[-1]["r"] if store else {}
    rs_now = {c: v for c, v in cur.items() if v is not None}
    br = breadth(rs_now)
    pool_median, valid = aggregate(list(rs_now.values()))
    live_r = live_r or dict(rs_now)

    rows = []
    # 一、每只持仓票各一行
    for code in holdings:
        rows.append({"name": ig.POOL.get(code, HOLD_NAMES.get(code, code)),
                     # 池外票不落盘，速度是结构性缺失，渲染成「—」不是「不可用」
                     "dash": code not in POOL_CODES,
                     "speeds": _speeds_for(store, now,
                                           lambda s, c=code: s["r"].get(c))})
    # 二、持仓票所属赛道各一行，同赛道只出一次，池外票没有赛道直接跳过
    seen_sectors = []
    for code in holdings:
        sec = ig.SECTOR.get(code)
        if sec and sec not in seen_sectors:
            seen_sectors.append(sec)
    for sec in seen_sectors:
        members = [c for c, s in ig.SECTOR.items() if s == sec]
        rows.append({"name": f"{sec}({len(members)}只)",
                     "speeds": _speeds_for(store, now,
                                           lambda s, m=members: _sector_key(s, m))})
    # 三、科技池
    rows.append({"name": f"科技池({valid}只)",
                 "speeds": _speeds_for(
                     store, now,
                     lambda s: aggregate(list(s["r"].values()))[0])})
    # 四、创业板指
    rows.append({"name": IDX_NAMES["sz399006"],
                 "speeds": _speeds_for(store, now,
                                       lambda s: s["idx"].get("sz399006"))})

    hold_rows = []
    all_rs = list(rs_now.values())
    for code in holdings:
        # 池内票的 r 一律取 store 里的那个值，不取 live_r。
        # 2026-08-07 的「排名 39/38」就是这两个来源精度分叉造成的：
        # 这只票自己的 store 值比它的 live_r 值大，rank 把自己也数了进去。
        # 只靠"两处都记得 round 到同样位数"是约定，会随新调用点复发；
        # 让池内票的比较值和被比较的池子同源，才是结构上消灭这一类 bug。
        # live_r 只服务池外持仓票（长鑫科技），它们本来就不在 all_rs 里。
        r = rs_now.get(code) if code in POOL_CODES else live_r.get(code)
        place, total = (rank(r, all_rs) if code in POOL_CODES else (None, len(all_rs)))
        hold_rows.append({"name": ig.POOL.get(code, HOLD_NAMES.get(code, code)),
                          "r": r, "excess": excess(r, pool_median),
                          "rank": (place, total),
                          "in_pool": code in POOL_CODES})

    first = (rs_now.get(holdings[0]) if holdings and holdings[0] in POOL_CODES
             else (live_r.get(holdings[0]) if holdings else None))
    return {"ts": now.strftime(TS_FMT), "valid": valid, "total": len(POOL_CODES),
            "status": status, "rows": rows, "breadth": br,
            "holdings": hold_rows, "verdict": verdict(br, first),
            "pool_median": pool_median, "dropped": 0}


MARKET_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")


def now_bj():
    """当前北京时间，去掉 tzinfo 以便与落盘的时间戳字符串直接比较。

    2026-08-07 实测踩到的坑：本机时区是 JST，比北京快 1 小时。
    用 datetime.datetime.now() 取本机时间去比对 A 股时段，12 个时点里错 8 个——
    误采北京 08:30-09:29 的盘前，漏采 10:31-11:30 和 14:01-15:00 两段，
    尾盘整整一小时全丢，而那正是最需要看盘的时段。

    仓库里 intraday_guide.py:203 和 intraday_collector.py:52 早就定了
    MARKET_TZ 这个约定，这里对齐它。所有取"现在"的地方都必须走这个函数，
    不许直接调 datetime.datetime.now()。
    """
    return datetime.datetime.now(MARKET_TZ).replace(tzinfo=None)


def main():
    """命令行入口：读盘、组装、打印完整面板。"""
    import stock_watch as sw

    now = now_bj()
    rotate_store(now.strftime("%Y-%m-%d"))
    codes = merge_codes(sw.load_config())
    quotes = sw.fetch_quotes(codes)
    pool = parse_pool_quotes(quotes)
    snap = {"t": now.strftime(TS_FMT),
            "r": {c: v["r"] for c, v in pool.items() if c in POOL_CODES},
            "idx": {c: v["r"] for c, v in pool.items() if c in IDX_CODES}}
    append_store(snap)
    store = load_store(max(WINDOWS) * 2, now=now)
    live_r = {c: v["r"] for c, v in pool.items()}
    print(render_panel(build_state(store, now, live_r=live_r)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse.py -q
```

Expected: 122 passed（以实测为准）

- [ ] **Step 5: 写集成测试**

新建 `test_market_pulse_e2e.py`：

```python
"""market_pulse 集成测试——打真实接口。

跑法：MP_E2E=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse_e2e.py -q
不设 MP_E2E 时自动 skip，无网络环境不会红。
"""
import os

import pytest

import market_pulse as mp
import stock_watch as sw

pytestmark = pytest.mark.skipif(not os.environ.get("MP_E2E"),
                                reason="需要 MP_E2E=1 才跑联网测试")


def test_merged_request_returns_all_codes():
    """45 个代码一个批量请求，全部要有数据。

    2026-08-07 实测：自选 10 个 A 股 + 池子 38 + 指数 3，去重后 45，
    5 次请求全部 45/45 返回，耗时中位 421 毫秒。
    """
    codes = mp.merge_codes(sw.load_config())
    quotes = sw.fetch_quotes(codes)
    got = {q["code"] for q in quotes if q.get("ok")}
    missing = [c for c in mp.POOL_CODES if c not in got]
    assert not missing, f"池子里这些票没返回：{missing}"


def test_pool_quotes_have_positive_prices():
    quotes = sw.fetch_quotes(list(mp.POOL_CODES))
    pool = mp.parse_pool_quotes(quotes)
    assert len(pool) >= mp.MIN_VALID
    assert all(v["px"] > 0 for v in pool.values())


def test_store_roundtrip(tmp_path):
    p = str(tmp_path / "s.jsonl")
    snap = {"t": "2026-08-07 13:24:15", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(snap, path=p) is True
    got = mp.load_store(120, now=mp.parse_ts("2026-08-07 13:25:00"), path=p)
    assert got == [snap]


def test_rotate_clears_yesterday(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"t":"2026-08-06 14:00:00","r":{},"idx":{}}\n', encoding="utf-8")
    assert mp.rotate_store("2026-08-07", path=str(p)) is True
    assert p.read_text(encoding="utf-8") == ""


def test_no_write_outside_session(tmp_path):
    p = tmp_path / "s.jsonl"
    snap = {"t": "2026-08-07 12:00:00", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(snap, path=str(p)) is False
    assert not p.exists()
```

- [ ] **Step 6: 跑集成测试并手动验证面板**

```bash
MP_E2E=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse_e2e.py -q
python3 market_pulse.py
```

Expected: 集成测试 5 passed；面板打印出四块内容。**首次运行时速度栏应显示「不可用（悬浮窗未启动）」，这是正确行为，不是 bug。**

- [ ] **Step 7: 跑全量确认没破坏基线**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

Expected: 270 passed（以实测为准）

- [ ] **Step 8: 提交**

```bash
git add market_pulse.py test_market_pulse_e2e.py test_market_pulse.py
git commit -m "feat(market_pulse): 取数、状态组装、命令行入口与集成测试"
```

---

### Task 10: 接入 stock_watch 悬浮窗

**依赖：** Task 1、7、8

**Files:**
- Modify: `stock_watch.py`（`refresh()` 与 `_render_rows()`）
- Modify: `test_stock_watch.py`

**Interfaces:**
- Consumes: `market_pulse.merge_codes`、`parse_pool_quotes`、`append_store`、`rotate_store`、`load_store`、`build_state`、`render_strip`
- Produces: 无新的对外接口

- [ ] **Step 1: 写失败的测试**

追加到 `test_stock_watch.py`：

```python
def test_watch_codes_merge_includes_pool():
    """悬浮窗请求的代码表里必须含 38 只池子票，否则采集拿不到数据。"""
    import market_pulse as mp
    import stock_watch as sw
    merged = mp.merge_codes(sw.load_config())
    for code in mp.POOL_CODES:
        assert code in merged


def test_watch_codes_has_no_duplicates():
    import market_pulse as mp
    import stock_watch as sw
    merged = mp.merge_codes(sw.load_config())
    assert len(merged) == len(set(merged))
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_stock_watch.py -q -k "merge or duplicates"
```

Expected: FAIL 或 ERROR（`market_pulse` 尚未被 `stock_watch` 引用时，测试本身能过；若已按上面顺序完成 Task 1，这两条会直接 PASS——那说明纯函数层已就绪，可以直接进 Step 3 改 GUI）

- [ ] **Step 3: 改 stock_watch.py**

在 `stock_watch.py` 的 `_build_app()` 内部（与 `import trade_assist as ta` 同一处）加：

```python
    import market_pulse as mp
```

在 `StockWatch.__init__` 里，`self._render_rows()` 之前加：

```python
            # --- 市场脉搏（见 docs/market_pulse/spec/）---
            self.pulse_text = None      # 横条末尾那个格子的标签
            self._pulse_strip = ""      # 最近一次算出来的单行文案
            mp.rotate_store(mp.now_bj().strftime("%Y-%m-%d"))
```

在 `_render_rows()` 的末尾（`minus.pack(...)` 之后）加：

```python
            self.pulse_text = tk.Label(self.body, text=self._pulse_strip,
                                       bg=BG, fg=FLAT_COLOR, font=(FONT, 11))
            self.pulse_text.pack(side="left", padx=(12, 4), anchor="n")
```

在 `refresh()` 里，把取代码那一行改成合并后的列表，并在拿到行情后落盘、更新脉搏文案。找到 `refresh()` 中调用 `fetch_quotes` 的位置，改成：

```python
        def _pulse_tick(self, quotes):
            """把池子部分落盘并重算单行文案。任何异常都不能影响自选渲染。"""
            try:
                now = mp.now_bj()      # 必须用北京时间，本机是 JST 快 1 小时
                pool = mp.parse_pool_quotes(quotes)
                snap = {"t": now.strftime(mp.TS_FMT),
                        "r": {c: v["r"] for c, v in pool.items()
                              if c in mp.POOL_CODES},
                        "idx": {c: v["r"] for c, v in pool.items()
                                if c in mp.IDX_CODES}}
                mp.append_store(snap)
                store = mp.load_store(max(mp.WINDOWS) * 2, now=now)
                live_r = {c: v["r"] for c, v in pool.items()}
                state = mp.build_state(store, now, live_r=live_r)
                self._pulse_strip = mp.render_strip(state)
            except Exception as exc:          # 脉搏出错不能连累自选行情
                self._pulse_strip = "脉搏异常"
                _trade_log(f"pulse error: {exc}")
            if self.pulse_text is not None:
                self.pulse_text.config(text=self._pulse_strip)
```

并在 `refresh()` 拿到 `quotes` 之后调用 `self._pulse_tick(quotes)`，同时把传给 `fetch_quotes` 的代码列表换成 `mp.merge_codes(self.codes)`。

**注意：`_update_labels()` 只渲染 `self.codes` 里的票，池子那 35 个多余的票不会出现在界面上，不需要额外过滤。**

- [ ] **Step 4: 跑测试并手动验证**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
python3 stock_watch.py
```

Expected: 全量测试通过；悬浮窗启动后横条末尾出现脉搏文案，**启动头 5 分钟显示「行情不可用」是正确的**（`store_status` 处于 `warming_up`）；5 分钟后出现完整文案。同时确认 `pulse_store.jsonl` 在长大。

- [ ] **Step 5: 交叉验证两个前端一致**

悬浮窗保持运行，另开终端：

```bash
python3 market_pulse.py
```

Expected: 命令行面板里的判定和排名，与悬浮窗那一行完全一致。**不一致说明两个前端没走同一个计算核心，必须查。**

- [ ] **Step 6: 提交**

```bash
git add stock_watch.py test_stock_watch.py
git commit -m "feat(stock_watch): 横条接入市场脉搏，取数并入现有批量请求"
```

---

## 自查记录

**Spec 覆盖检查**（逐节对照 `docs/market_pulse/spec/`）：

| Spec 节 | 实现任务 |
|---|---|
| §2.1 速度 | Task 3 |
| §2.2 聚合速度 | Task 3 |
| §2.3 宽度 | Task 4 |
| §2.4 超额 | Task 5 |
| §2.5 池内排名 | Task 5 |
| §2.6 判定 | Task 4 |
| §3 架构与合并请求 | Task 1、9、10 |
| §4.1 交易时段 | Task 2 |
| §4.2 落盘格式与跨日 | Task 7 |
| §4.3 窗口取值容差 | Task 6 |
| §4.4 三种不可用状态 | Task 7（`store_status`）、Task 8（渲染） |
| §5 错误处理 | Task 4（样本不足）、Task 7（损坏行）、Task 9（停牌剔除）、Task 10（脉搏异常隔离） |
| §6.1 命令行面板 | Task 8、9 |
| §6.2 悬浮窗单行 | Task 8、10 |
| §7.1 单测 40 条 | Task 1–8 全覆盖 |
| §7.2 集成测试 4 条 | Task 9 |

**未覆盖项：** §5 的「连续失败 5 次报行情中断」和「磁盘写失败每 60 秒重试」两条，在 Task 10 的 `_pulse_tick` 里被统一的 `except Exception` 兜住，只记日志不区分。**这是有意简化：这两条都属于 GUI 层的重试策略，写单测的成本高于收益。若实际使用中发现需要区分，再单开一个任务。**

**类型一致性检查：** `aggregate` 全程返回 `(值, 有效数)` 二元组；`rank` 全程返回 `(名次, 有效数)`；`verdict` 全程返回 `(判定, 说明)`；`store_status` 全程返回 `(状态码, 说明)`。`speed` 与 `excess` 缺数据一律返回 `None` 而非 `0`。
