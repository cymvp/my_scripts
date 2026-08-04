#!/usr/bin/env python3
"""盘中位置参考工具。

它回答的问题是「当前价格在历史同类日子里处于什么位置」，
**不回答「该买还是该卖」**——127~1000 个交易日的统计给不出择时信号，
硬给一个只会制造确定性的错觉。

三层结构（2026-08-01 定，2026-08-02 样本池换成 38 只 / 14 赛道）：
  基础层：38 只科技股合并样本建统计基准，样本量几百到几千，相对可靠。
  个股层：用该股自己的历史做对照，**样本少于 MIN_N 就明确标注不可用**。
          实测中际旭创 4 年 579 个交易日里「下跌趋势+高开>=6%」只出现 2 次，
          单只股票在细分条件下必然样本归零，所以个股层只能是参考不能是依据。
  实时层：抓当前价，报它在历史分布里的分位数。

以及一条硬性纪律：每次输出统计结论，必须同时检查「最近一个月是否偏离历史」。
2026-08-01 实测发现 2026H2 的当日开→收比前五个半年期整体低约 2 个百分点，
不检查就会拿历史均值给出系统性偏乐观的结论。

用法：
  python3 intraday_guide.py build            重建统计基准（慢，抓 38 只票）
  python3 intraday_guide.py brief sz300308   盘前/盘后：该股当前状态与历史分布
  python3 intraday_guide.py live sz300308    盘中：当前价在历史分布的分位
  python3 intraday_guide.py advise --hold 300308=100万 --cash 50万 --t-cash 30万
                                            盘中指导：挂单、仓位、止损、纪律检查
"""
import datetime
import json
import random
import os
import statistics as st
import sys
import time
import urllib.request
import zoneinfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE_DIR, "intraday_baseline.json")

POOL = {
    # 38 只 A 股科技股 / 14 个细分赛道（2026-08-02 定版，与 my_data/trading/回测依据.md 一致）
    # 成交额低于 10 亿的已剔除；长鑫科技 688825 上市不足 60 个交易日暂不纳入
    "sz300308": "中际旭创", "sz300502": "新易盛", "sz300394": "天孚通信",
    "sh603986": "兆易创新", "sz301308": "江波龙", "sh688525": "佰维存储",
    "sh688256": "寒武纪", "sh688041": "海光信息", "sh688521": "芯原股份",
    "sz002371": "北方华创", "sh688012": "中微公司", "sh688072": "拓荆科技",
    "sh688126": "沪硅产业", "sh688019": "安集科技", "sz300054": "鼎龙股份",
    "sh688981": "中芯国际", "sh688347": "华虹宏力", "sh688249": "晶合集成",
    "sh600584": "长电科技", "sz002156": "通富微电", "sz002185": "华天科技",
    "sz300661": "圣邦股份",
    "sz300782": "卓胜微", "sh603501": "豪威集团", "sh688008": "澜起科技",
    "sz002463": "沪电股份", "sh600183": "生益科技", "sz002916": "深南电路",
    "sh601138": "工业富联", "sz000977": "浪潮信息", "sh603019": "中科曙光",
    "sz301526": "国际复材", "sh603256": "宏和科技", "sz002080": "中材科技",
    "sh605376": "博迁新材", "sz300285": "国瓷材料",
    "sz002837": "英维克", "sz002851": "麦格米特",
}

SECTOR = {
    "sz300308": "光模块", "sz300502": "光模块", "sz300394": "光模块",
    "sh603986": "存储芯片", "sz301308": "存储芯片", "sh688525": "存储芯片",
    "sh688256": "AI算力芯片", "sh688041": "AI算力芯片", "sh688521": "AI算力芯片",
    "sz002371": "半导体设备", "sh688012": "半导体设备", "sh688072": "半导体设备",
    "sh688126": "半导体材料", "sh688019": "半导体材料", "sz300054": "半导体材料",
    "sh688981": "晶圆制造", "sh688347": "晶圆制造", "sh688249": "晶圆制造",
    "sh600584": "封装测试", "sz002156": "封装测试", "sz002185": "封装测试",
    "sz300661": "模拟/功率",
    "sz300782": "芯片设计", "sh603501": "芯片设计", "sh688008": "芯片设计",
    "sz002463": "PCB/覆铜板", "sh600183": "PCB/覆铜板", "sz002916": "PCB/覆铜板",
    "sh601138": "AI服务器", "sz000977": "AI服务器", "sh603019": "AI服务器",
    "sz301526": "电子布", "sh603256": "电子布", "sz002080": "电子布",
    "sh605376": "MLCC", "sz300285": "MLCC",
    "sz002837": "温控/电源", "sz002851": "温控/电源",
}

SLOTS = ["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
MIN_N = 20          # 少于这个样本数的格子不给结论
TRIM = 0.10         # 截尾比例


# ============ 纯函数（单元测试覆盖这一段） ============

def trim_mean(values, ratio=TRIM):
    """截尾均值：排序后去掉两端各 ratio 比例再取平均。

    比原始均值抗极端值，又比中位数多用了数据。样本太少时截尾会削光数据，
    退回普通均值。
    """
    if not values:
        return None
    s = sorted(values)
    k = int(len(s) * ratio)
    kept = s[k:len(s) - k] if len(s) - 2 * k >= 3 else s
    return st.mean(kept)


def classify_trend(drawdown_pct):
    """按「昨收距近60日最高收盘的回撤」判趋势。边界：<=-15 下跌，(-15,-5] 震荡。"""
    if drawdown_pct <= -15:
        return "下跌"
    if drawdown_pct <= -5:
        return "震荡"
    return "上涨"


def classify_gap(gap_pct):
    if gap_pct >= 3:
        return "高开>=3%"
    if gap_pct >= 0:
        return "高开0~3%"
    if gap_pct > -3:
        return "低开0~3%"
    return "低开>=3%"


def classify_volume(ratio):
    """今日成交量 ÷ 前20日均量。边界 0.85 归平量、1.6 归巨量。"""
    if ratio < 0.85:
        return "缩量"
    if ratio < 1.15:
        return "平量"
    if ratio < 1.6:
        return "放量"
    return "巨量"


# --- 振幅体系（advise 子命令用）---------------------------------------------
# 口径与依据见 ~/projects/my_data/trading/查表手册.md

AMP_CELLS = ((3.0, "0~3%"), (4.0, "3~4%"), (6.0, "4~6%"), (8.0, "6~8%"))
PRED_WINDOW = 10          # 预测振幅的回看窗口，样本外对比里 10 日最优
CALIB_TOLERANCE = 0.5     # 盘中反推值与盘前预测相差超过这个比例就提示偏离常态


def day_amplitude(o, h, l):
    """日振幅 = (当日最高 − 当日最低) ÷ 当日开盘，单位 %。"""
    if not o:
        return None
    return (h - l) / o * 100


def predicted_amplitude(bars, window=PRED_WINDOW):
    """预测振幅 = 该股最近 window 个交易日日振幅的中位数，不乘任何系数。

    用中位数不用平均数：平均数被涨停跌停和异常日拉偏，样本外偏差 1.09~1.21。
    """
    if len(bars) < window:
        return None
    amps = [day_amplitude(b["o"], b["h"], b["l"]) for b in bars[-window:]]
    amps = [a for a in amps if a is not None]
    if len(amps) < window:
        return None
    return st.median(amps)


def amp_cell(pred_amp):
    """预测振幅落在表 A 的哪一格。左闭右开：3.0 归 3~4%，8.0 归 >8%。"""
    if pred_amp is None:
        return None
    for edge, name in AMP_CELLS:
        if pred_amp < edge:
            return name
    return ">8%"


def risk_parity(pred_amps, cash):
    """按预测振幅的倒数分配现金，让各笔的风险敞口相当。

    波动最大的票分得最少。振幅缺失的票不参与分配。
    """
    usable = {k: v for k, v in pred_amps.items() if v}
    if not usable:
        return {}
    inv = {k: 1.0 / v for k, v in usable.items()}
    total = sum(inv.values())
    return {k: cash * w / total for k, w in inv.items()}


def position_cap(total, risk_pct, stop_pct):
    """仓位上限 = 总资产 × 单笔风险预算 ÷ 止损宽度。"""
    if not stop_pct:
        return None
    return total * risk_pct / stop_pct


def walked_amplitude(bars):
    """盘中已走振幅 = (至此刻最高 − 至此刻最低) ÷ 今日开盘，单位 %。"""
    if not bars:
        return None
    return day_amplitude(bars[0]["o"], max(b["h"] for b in bars), min(b["l"] for b in bars))


def implied_full_amplitude(walked, trend, slot, table_e):
    """用已走振幅反推全天振幅：已走振幅 ÷ 该时点的「已走%」。

    基准缺失时返回 None，不用默认值顶上。
    """
    pct = (table_e.get(trend) or {}).get(slot, {}).get("walked_pct")
    if not pct or walked is None:
        return None
    return walked / (pct / 100)


MARKET_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")


def now_slot(hhmm=None):
    """当前所处的 30 分钟时段，**按北京时间**判定。

    不能用 time.strftime 取本机时间：2026-08-03 踩过，本机时区是 JST 比北京快
    1 小时，本机 14:51 实际是北京 13:51，工具却按 15:00 查表，差了两格。
    表 D / 表 E 都按时段索引，时段错了整段结论都错。A 股按北京时间开收盘。
    """
    if hhmm is None:
        hhmm = datetime.datetime.now(MARKET_TZ).strftime("%H:%M")
    return slot_of(hhmm)


def is_final_slot(slot):
    """是不是当天最后一个 30 分钟段。

    最后一段的「已走%」按定义就是 100%（累计到最后一根 K 线当然等于全天），
    剩余空间必然是 0。这个 0 不表示「今天不会再动」，而是这两个指标在收盘段
    结构上没有信息量，输出时必须标注，不能照常给一个数。
    """
    return slot == SLOTS[-1]


def group_by_trend(snapshots):
    """按趋势把票分组，保持首次出现的顺序。取数失败的不参与。

    表 D / 表 E 只按「趋势 × 时段」查，同趋势同时段的票查到的是同一格，
    时点结构那一段每组输出一次就够，不必每只票重复。
    """
    out = {}
    for code, s in snapshots.items():
        if s.get("error") or not s.get("trend"):
            continue
        out.setdefault(s["trend"], []).append(code)
    return out


def calibration_off(pred, implied, tolerance=CALIB_TOLERANCE):
    """盘中反推的全天振幅是否明显偏离盘前预测。任一为空时返回 None。"""
    if not pred or not implied:
        return None
    return abs(implied - pred) / pred > tolerance


# --- 参数解析 ---------------------------------------------------------------

_UNITS = (("亿", 1e8), ("万", 1e4), ("w", 1e4), ("W", 1e4))


def parse_amount(text):
    """把「100万」「1.5万」「40w」「500000」解析成元。解析不了就报错，不返回 0。"""
    s = str(text).strip().replace(",", "").replace("元", "")
    mult = 1.0
    for suffix, m in _UNITS:
        if s.endswith(suffix):
            s, mult = s[:-len(suffix)], m
            break
    try:
        return float(s) * mult
    except ValueError:
        raise ValueError(f"金额看不懂：{text}（可写 100万 / 1.5万 / 500000）")


def resolve_code(text):
    """六位数字、带前缀的代码、或股票名，统一成带前缀的代码。认不出返回 None。"""
    s = str(text).strip()
    for code, name in POOL.items():
        if s in (code, code[2:], name):
            return code
    if s.isdigit() and len(s) == 6:
        return ("sh" if s[0] == "6" else "sz") + s
    if len(s) == 8 and s[:2] in ("sh", "sz") and s[2:].isdigit():
        return s
    return None


def parse_holdings(items):
    """解析 --hold 代码=金额，同一只票多次出现时累加。"""
    out = {}
    for it in items:
        if "=" not in it:
            raise ValueError(f"持仓写法应为 代码=金额，收到：{it}")
        raw, amount = it.split("=", 1)
        code = resolve_code(raw)
        if code is None:
            raise ValueError(f"认不出这只票：{raw}")
        out[code] = out.get(code, 0.0) + parse_amount(amount)
    return out


# --- 止损候选与特殊格提示 ---------------------------------------------------

STOP_LEVELS = (1, 2, 3, 5)


# --- 表 F：剩余触及概率（盘中用，替代表 A 的全天概率）----------------------
# 表 A 的「当日最低 < 今开−X%」是全天口径，假设这一天还没开始。盘中当日最低已经
# 部分定型，用全天概率会严重高估——2026-08-03 实测中际旭创 14:00 那一刻，
# 真实剩余概率 4.9%，表 A 给 64.2%，高估近 70 个百分点。

NEED_BUCKETS = ((0.15, "<0.15"), (0.35, "0.15~0.35"), (0.6, "0.35~0.6"))


def need_ratio(cur_low, target, day_open, pred_amp):
    """还需再跌 = (此刻最低 − 目标价) ÷ 今开 ÷ 预测振幅。已触及返回 0。

    用预测振幅归一化，是为了把「预测振幅格」这个维度压掉——否则
    趋势 × 时点 × 振幅格 × 距离 四个维度会把 4826 个股票日切碎。
    归一化后每格 1100~3000 个样本。
    """
    if not day_open or not pred_amp:
        return None
    if cur_low <= target:
        return 0.0
    return (cur_low - target) / day_open * 100 / pred_amp


def need_bucket(ratio):
    """还需再跌分四档，左闭右开。0 单独成「已触及」。"""
    if ratio is None:
        return None
    if ratio <= 0:
        return "已触及"
    for edge, name in NEED_BUCKETS:
        if ratio < edge:
            return name
    return ">0.6"


def remaining_touch(trend, slot, cur_low, target, day_open, pred_amp, table_f):
    """盘中的剩余触及概率。基准缺该格返回 None，不用全天概率顶替。"""
    ratio = need_ratio(cur_low, target, day_open, pred_amp)
    b = need_bucket(ratio)
    if b is None:
        return None
    if b == "已触及":
        return {"bucket": b, "p": 100.0, "n": None, "ratio": 0.0}
    cell = ((table_f.get(trend) or {}).get(slot) or {}).get(b)
    if not cell:
        return None
    return {"bucket": b, "p": cell["p"], "n": cell["n"], "ratio": ratio}


def close_position(c, hi, lo):
    """收盘位置 =（今收 − 当日最低）÷（当日最高 − 当日最低），0~1。

    1 = 收在当日最高，0 = 收在当日最低，0.5 = 正中间。
    一字板（最高=最低）没有振幅区间，位置无定义，返回 None——不拿 0.5 顶替。

    **这是当日结束才知道的量**，盘中拿不到当天的值，只能给历史分布。
    所以它用于事后复盘和预期设定，不进挂单和止损决策。
    """
    rng = hi - lo
    return (c - lo) / rng if rng > 0 else None


def build_close_position(recs, min_n=None):
    """按趋势汇总收盘位置的分布。

    实测（739 天）下跌 0.430、震荡 0.470、上涨 0.513，单调递增：
    下跌趋势里收盘倾向落在当日区间下半部，上涨趋势里落在上半部。
    """
    min_n = MIN_N_DAILY if min_n is None else min_n
    acc = {}
    for r in recs:
        v = close_position(r["c"], r["h"], r["l"])
        if v is not None:
            acc.setdefault(r["trend"], []).append(v)
    out = {}
    for tr, vs in acc.items():
        if len(vs) < min_n:
            continue
        vs.sort()
        out[tr] = {"n": len(vs), "mean": sum(vs) / len(vs),
                   "median": vs[len(vs) // 2],
                   "p25": vs[len(vs) // 4], "p75": vs[len(vs) * 3 // 4]}
    return out


def need_ratio_up(cur_high, target, day_open, pred_amp):
    """向上版：还需再涨 =(目标价 − 此刻最高)÷ 今开 ÷ 预测振幅。已触及返回 0。"""
    if not day_open or not pred_amp:
        return None
    if cur_high >= target:
        return 0.0
    return (target - cur_high) / day_open * 100 / pred_amp


def remaining_touch_up(trend, slot, cur_high, target, day_open, pred_amp, table_f_up):
    """盘中的向上剩余触及概率。基准缺该格返回 None。"""
    b = need_bucket(need_ratio_up(cur_high, target, day_open, pred_amp))
    if b is None:
        return None
    if b == "已触及":
        return {"bucket": b, "p": 100.0, "n": None}
    cell = ((table_f_up.get(trend) or {}).get(slot) or {}).get(b)
    return {"bucket": b, "p": cell["p"], "n": cell["n"]} if cell else None


def build_table_f_up(recs, slots=None, min_n=200):
    """表 F 向上版：趋势 × 时点 × 还需再涨档 → 当日最高最终突破目标的比例。"""
    slots = slots or SLOTS[:-1]
    acc = {}
    for r in recs:
        o, pred, bars = r["o"], r.get("pred_amp"), r["bars"]
        if not o or not pred or len(bars) != 8:
            continue
        final_hi = max(b["h"] for b in bars)
        for j, s in enumerate(slots):
            cur_hi = max(b["h"] for b in bars[:j + 1])
            for x in (1, 2, 3, 4, 5, 6, 8, 10, 12):
                tgt = o * (1 + x / 100)
                b = need_bucket(need_ratio_up(cur_hi, tgt, o, pred))
                if b in (None, "已触及"):
                    continue
                acc.setdefault((r["trend"], s, b), []).append(final_hi > tgt)
    out = {}
    for (tr, s, b), v in acc.items():
        if len(v) < min_n:
            continue
        out.setdefault(tr, {}).setdefault(s, {})[b] = {
            "n": len(v), "p": sum(v) / len(v) * 100}
    return out


def build_table_f(recs, slots=None, min_n=200):
    """表 F：趋势 × 时点 × 还需再跌档 → 剩余触及概率。

    recs 每条是一个股票-交易日：{trend, o, pred_amp, bars}。
    对每个时点、每个目标档位（−1~−6%）算一条观察，问「最终有没有跌到」。
    """
    slots = slots or SLOTS[:-1]          # 最后一段没有「剩余」可言
    acc = {}
    for r in recs:
        o, pred, bars = r["o"], r.get("pred_amp"), r["bars"]
        if not o or not pred or len(bars) != 8:
            continue
        final_lo = min(b["l"] for b in bars)
        for j, s in enumerate(slots):
            cur_lo = min(b["l"] for b in bars[:j + 1])
            for x in (1, 2, 3, 4, 5, 6):
                tgt = o * (1 - x / 100)
                b = need_bucket(need_ratio(cur_lo, tgt, o, pred))
                if b in (None, "已触及"):
                    continue
                acc.setdefault((r["trend"], s, b), []).append(final_lo < tgt)
    out = {}
    for (tr, s, b), v in acc.items():
        if len(v) < min_n:
            continue
        out.setdefault(tr, {}).setdefault(s, {})[b] = {
            "n": len(v), "p": sum(v) / len(v) * 100}
    return out


def level_label(x, day_open, down=True):
    """档位标签。**必须写明「距今开」并带上绝对价位。**

    2026-08-03 用户指出的问题：表里 −1%/+3% 这些档位没说清相对什么算。
    它们一律相对【今日开盘价】，而涨跌幅相对【昨收】——基准不同。
    只写百分比会让人拿去和行情软件的涨跌幅对照，直接看错价位。
    """
    sign = "−" if down else "+"
    if not day_open:
        return f"距今开 {sign}{x}%"
    price = day_open * (1 - x / 100) if down else day_open * (1 + x / 100)
    return f"距今开 {sign}{x}%（{price:.2f} 元）"


def stop_options(trend, cell_name, table_a, table_b, total, risk_pct):
    """给出各档止损的触发概率、触发后回到开盘上方的比例、无效止损概率、仓位上限。

    无效止损概率 = 触发概率 × 触发后回到开盘上方的比例，也就是每 100 个交易日里，
    有几天既被止损卖出、收盘价又回到了开盘价之上（不卖本来是赚的）。这是最终要看的数。
    """
    a = (table_a.get(trend) or {}).get(cell_name)
    b = (table_b.get(trend) or {}).get(cell_name, {})
    if not a:
        return []
    out = []
    for x in STOP_LEVELS:
        trig = a["down"].get(str(x))
        if trig is None:
            continue
        back = (b.get("down", {}).get(str(x)) or {}).get("back_above")
        out.append({
            "stop_pct": x,
            "trigger": trig,
            "back_above": back,
            "ineffective": trig * back / 100 if back is not None else None,
            "cap": position_cap(total, risk_pct, x),
        })
    return out


def special_cell_note(trend, cell_name):
    """需要成对陈述的格子。只有一个，不通用化。

    「下跌 + 预测振幅 >8%」上，表 C 和表 B 扩展说的话看起来矛盾，
    单独引用任何一句都会误导，必须同时给。
    """
    if (trend, cell_name) != ("下跌", ">8%"):
        return None
    return ("这一格要同时看两件事：表 C 说挂 −3% 限价买入比开盘市价买入好 +0.817%"
            "（95% 区间 [+0.076%, +1.376%]，成交率 63.8%，n=566）；"
            "表 B 扩展说这一格成交后到收盘的中位收益仍是 -0.88%，四个档位全为负。"
            "限价比市价好，是因为开盘市价买更糟；绝对上仍然是亏的——"
            "在极端波动的下跌行情里，当天买入这个动作本身就是负期望。"
            "另外表 C 的 60 个格子没做多重比较校正，这条只是观察，不是规则。")


# --- 纪律检查（逐条对应 my_data/trading/交易纪律.md）------------------------
# status 四种：pass 通过 / fail 不通过 / ask 需要本人回答 / info 固定提示

THEME_CAP, SINGLE_CAP, CASH_FLOOR, LEVERAGE_CAP = 50.0, 25.0, 30.0, 5.0
FIRST_HOUR_SLOTS = ("10:00", "10:30")
LATE_SLOTS = ("14:30", "15:00")
NO_REVERSAL_DIST = 4.0

_ASK_ITEMS = (
    ("write_down", "§1-4", "下单价格、数量、止损位写下来了吗？写完不准改"),
    ("sell_purpose", "§4-1", "这次卖出是降敞口（卖了不买回）还是做差价（尾盘接回）？两者必须分开记账"),
    ("limit_order_filled", "§2", "今天已决定买入的，限价单成交了吗？临近收盘仍未成交要转市价"),
    ("no_both_sides", "§4-3", "今天是否已经加过空头？同一天不既加多头又加空头"),
    ("stop_placed", "§4-7", "反向产品的止损是否已经【挂出去】了，不是心里想着"),
)

_INFO_ITEMS = (
    ("next_day_unpredictable", "§5-1",
     "次日方向无预测力：15 个「趋势×开盘跳空」格子的次日上涨概率全在 41~55%"),
    ("volume_not_direction", "§5-4",
     "量能只进入止损宽度和仓位计算，不进入买还是卖的判断"),
    ("public_news_priced_in", "§5-6", "已经公开的利好已经印在开盘价里了"),
    ("right_call_still_loses", "§5-5",
     "判断方向正确不等于赚钱：2026-07-31 科技股确实大涨、持仓当天也收红，账户仍是亏的"),
)


def theme_of(code):
    """主题归属，比板块粗。

    §1-2 的「单一主题 ≤50%」指的是粗口径：2026-08-02 复盘时本人把中际旭创、长鑫、
    国际复材合起来算作「主线 80%」，而不是按 14 个细分赛道分别算。
    样本池这 38 只票同涨同跌（同日聚类），按细分赛道算的话任何一个赛道都不会超过 50%，
    这条上限会永远不触发，失去意义。因此池内一律归为同一个主题。

    池外的代码也归入同一主题：手工输入的持仓本来就是这一批 A 股科技股，
    只是有的还没进样本池（如长鑫科技 688825 上市不足 60 个交易日）。
    风险检查上宁可高估集中度也不要漏报——2026-08-02 本人自己的口径正是把长鑫
    算进主线，得出 80%。要排除某只票，在 portfolio 里显式给 themes 覆盖。
    """
    return "科技主线"


def sector_of(code):
    """板块归属。POOL/SECTOR 的键带交易所前缀（sz300308），这里两种写法都认。"""
    if code in SECTOR:
        return SECTOR[code]
    bare = code[2:] if code[:2].isalpha() else code
    for pre in ("sz", "sh"):
        if pre + bare in SECTOR:
            return SECTOR[pre + bare]
    return None


def _chk(cid, ref, title, ok, detail=""):
    return {"id": cid, "ref": ref, "title": title,
            "status": "pass" if ok else "fail", "detail": detail}


def discipline_checks(portfolio, snapshots, slot):
    """逐条跑一遍交易纪律，返回 21 条结果（通过的也返回，方便对照）。"""
    hold = portfolio.get("holdings") or {}
    total = portfolio.get("total") or (sum(hold.values()) + portfolio.get("cash", 0.0))
    out = []

    # --- §1-2 三条硬性上限 ---
    theme = {}
    for code, v in hold.items():
        k = (portfolio.get("themes") or {}).get(code) or theme_of(code)
        theme[k] = theme.get(k, 0.0) + v
    worst_theme = max(theme.items(), key=lambda kv: kv[1]) if theme else None
    theme_pct = worst_theme[1] / total * 100 if worst_theme and total else 0.0
    out.append(_chk("theme_cap", "§1-2", f"单一主题占总资产 ≤ {THEME_CAP:.0f}%",
                    theme_pct <= THEME_CAP,
                    f"最大主题「{worst_theme[0]}」占 {theme_pct:.1f}%，超限只能减" if worst_theme else ""))

    worst_single = max(hold.items(), key=lambda kv: kv[1]) if hold else None
    single_pct = worst_single[1] / total * 100 if worst_single and total else 0.0
    out.append(_chk("single_cap", "§1-2", f"单票占总资产 ≤ {SINGLE_CAP:.0f}%",
                    single_pct <= SINGLE_CAP,
                    f"最大单票 {worst_single[0]} 占 {single_pct:.1f}%，超限只能减" if worst_single else ""))

    cash_pct = portfolio.get("cash", 0.0) / total * 100 if total else 0.0
    out.append(_chk("cash_floor", "§1-2", f"现金占总资产 ≥ {CASH_FLOOR:.0f}%",
                    cash_pct >= CASH_FLOOR,
                    f"现金只占 {cash_pct:.1f}%，低于下限只能减"))

    # --- §3 下跌趋势 × 高开≥5% 禁止买入 ---
    bad = [c for c, s in snapshots.items()
           if s.get("trend") == "下跌" and (s.get("gap_pct") or 0) >= 5]
    out.append(_chk("no_buy_gap_up", "§3", "下跌趋势 × 高开 ≥5% 不买入",
                    not bad,
                    f"{'、'.join(bad)} 命中此格（该格收高率 23.5%），禁止买入" if bad else ""))

    # --- §4-4 开盘首小时不做临时决策 ---
    out.append(_chk("first_hour", "§4-4", "开盘后第一个小时内不做临时决策",
                    slot not in FIRST_HOUR_SLOTS,
                    "现在是开盘首小时，有计划就按计划执行，没计划等 10:30 之后"))

    # --- §4-5 14:00 之后不做日内往返，但不禁止加仓 ---
    out.append(_chk("no_round_trip", "§4-5", "14:00 之后不做日内往返",
                    slot not in LATE_SLOTS,
                    "现在不适合做当天买进当天卖出的【往返】（午后各段振幅仅 0.84~1.66%，"
                    "实际可拿约三分之一，扣成本后不够覆盖一次判断错误）；"
                    "但【加仓】不受这条限制——14:00-14:30 是全天段内为正比例最低的半小时，"
                    "对买方是成本最低的时段"))

    # --- §4-6 14:30 离开盘价超 4% 不期待翻转 ---
    far = [c for c, s in snapshots.items()
           if abs(s.get("pos_vs_open") or 0) > NO_REVERSAL_DIST]
    out.append(_chk("no_reversal", "§4-6", "14:30 时离开盘价超 4% 不期待尾盘翻转",
                    not (slot in LATE_SLOTS and far),
                    f"{'、'.join(far)} 已离开盘价超 {NO_REVERSAL_DIST:.0f}%，实测翻转概率 0.2%"
                    if far else ""))

    # --- §4-7 杠杆产品仓位上限 ---
    lev_pct = portfolio.get("leverage", 0.0) / total * 100 if total else 0.0
    out.append(_chk("leverage_cap", "§4-7", f"杠杆与反向产品仓位 ≤ 总资产 {LEVERAGE_CAP:.0f}%",
                    lev_pct <= LEVERAGE_CAP, f"当前占 {lev_pct:.1f}%，超限要减"))
    out.append(_chk("leverage_days", "§4-7", "反向产品持仓天数未超过事先定的上限",
                    not portfolio.get("leverage_overdue"),
                    "已超期，无论盈亏都平掉——每日重置的损耗只看持有多久，不看方向对错"))
    out.append(_chk("no_index_hedge", "§4-2", "不用宽基或行业指数反向产品对冲个股",
                    not portfolio.get("index_hedge"),
                    "个股相对指数的 beta 不等于 1，行业内部会分化，对冲方向可能同时亏"))

    # --- 板块集中度（同日聚类的另一面）---
    secs = {c: sector_of(c) for c in snapshots}
    dup = sorted({s for s in secs.values()
                  if s and sum(1 for v in secs.values() if v == s) > 1})
    out.append(_chk("sector_concentration", "§6-1", "传入的多只票不属于同一板块",
                    not dup,
                    f"「{'、'.join(dup)}」有多只，这不是分散，是加杠杆——"
                    f"同一天的多只同板块票是 1 次独立观察，不是 N 次" if dup else ""))

    # --- §4A-1 单笔仓位不超过风险预算对应的上限 ---
    over, unknown = [], []
    for code, s in snapshots.items():
        stop = s.get("stop_pct")
        if not stop:
            unknown.append(code)
            continue
        cap = position_cap(total, portfolio.get("risk_pct", 1.0), stop)
        if cap and hold.get(code, 0.0) > cap:
            over.append(f"{code}（持仓 {hold[code]/10000:.0f} 万 > 上限 {cap/10000:.0f} 万）")
    if unknown:
        # 没选止损宽度就算不出仓位上限。这里必须标「需回答」——
        # 报「通过」会让人以为查过了，其实是根本没查。
        out.append({"id": "position_cap", "ref": "§4A-1",
                    "title": "单笔仓位未超过风险预算对应的上限", "status": "ask",
                    "detail": f"{'、'.join(unknown)} 还没选止损宽度，"
                              f"仓位上限算不出来。先从上面第 [4] 块挑一档止损"})
    else:
        out.append(_chk("position_cap", "§4A-1", "单笔仓位未超过风险预算对应的上限",
                        not over, "；".join(over)))

    for cid, ref, q in _ASK_ITEMS:
        out.append({"id": cid, "ref": ref, "title": q, "status": "ask", "detail": ""})
    for cid, ref, msg in _INFO_ITEMS:
        out.append({"id": cid, "ref": ref, "title": msg, "status": "info", "detail": ""})
    return out


# --- 基准表生成（纯计算，输入是已经对齐好的记录，不联网）--------------------

TOUCH_LEVELS = (1, 2, 3, 5)      # 向下（买单成交 / 止损触发）档位，单位 %
# 向上要更高的天花板：2026-08-04 国际复材当天从今开涨 8.00%，原来最高 +7% 时
# 五档全部「已触及」，给不出参考。向下不跟着扩——止损放到 −10% 没有实际意义。
UP_TOUCH_LEVELS = (1, 2, 3, 5, 7, 10, 12)
MIN_N_DAILY = 150                # 表 A / B 每格的最小样本，不足就不出这一格
MIN_N_M30 = 100                  # 表 D / E 每格的最小样本
CELL_ORDER = ("0~3%", "3~4%", "4~6%", "6~8%", ">8%")
TRENDS = ("下跌", "震荡", "上涨")


# --- 板块共振：当天有几只票落在同一格（2026-08-04 检验出的真实条件变量）------
# 「下跌 × >8%」格里触及 −3% 的概率：孤立日 45.9%、4~7 只 36.8%、8 只以上 77.8%，
# 最低组区间上限 57.4% 低于最高组下限 66.4%，两区间不重叠，差异成立。

RESO_BUCKETS = ((4, "1~3只"), (8, "4~7只"))


def resonance_bucket(n):
    """当天同格票数分三档。左闭右开：3 归 1~3只，4 归 4~7只，8 归 8只以上。"""
    for edge, name in RESO_BUCKETS:
        if n < edge:
            return name
    return "8只以上"


def attach_resonance(recs):
    """给每条记录标上当天同格票数和共振档。原地修改。"""
    cnt = {}
    for r in recs:
        if r.get("amp_cell") is None:
            continue
        cnt[(r["date"], r["trend"], r["amp_cell"])] = \
            cnt.get((r["date"], r["trend"], r["amp_cell"]), 0) + 1
    for r in recs:
        if r.get("amp_cell") is None:
            r["reso_n"] = None; r["reso"] = None
            continue
        n = cnt[(r["date"], r["trend"], r["amp_cell"])]
        r["reso_n"] = n
        r["reso"] = resonance_bucket(n)
    return recs


def build_pool_cells(recs, keep_days=5):
    """全池格子表：日期 → {代码: [趋势, 振幅格]}，只留最近 keep_days 天。

    趋势和预测振幅都只依赖当天之前的数据，所以某一天的格子在开盘前就已确定，
    可以离线算好存进基准，advise 不必为了数共振再抓 38 只票。
    """
    out = {}
    for r in recs:
        if r.get("amp_cell") is None:
            continue
        out.setdefault(r["date"], {})[r["code"]] = [r["trend"], r["amp_cell"]]
    for d in sorted(out)[:-keep_days]:
        del out[d]
    return out


def count_resonance(pool_cells, date, trend, cell):
    """当天本池有几只票落在同一格。该日期不在表里返回 None，不拿别的日期顶替。"""
    day = (pool_cells or {}).get(date)
    if day is None:
        return None
    return sum(1 for v in day.values() if v[0] == trend and v[1] == cell)


def build_table_a_reso(recs, min_n=None):
    """表 A 的共振分档版：趋势 × 振幅格 × 共振档 → 触及概率。

    补的是表 A 缺的那个维度——今天是不是板块共振日。
    """
    min_n = MIN_N_DAILY if min_n is None else min_n
    g = {}
    for r in recs:
        if r.get("amp_cell") is None or r.get("reso") is None:
            continue
        g.setdefault(r["trend"], {}).setdefault(r["amp_cell"], {}) \
         .setdefault(r["reso"], []).append(r)
    out = {}
    for tr, cells in g.items():
        for ce, resos in cells.items():
            for rb, rows in resos.items():
                if len(rows) < min_n:
                    continue
                e = {"n": len(rows), "dates": len(set(r["date"] for r in rows)),
                     "down": {}, "up": {}}
                for x in TOUCH_LEVELS:
                    f = x / 100.0
                    e["down"][str(x)] = sum(
                        1 for r in rows if r["l"] < r["o"] * (1 - f)) / len(rows) * 100
                for x in UP_TOUCH_LEVELS:
                    f = x / 100.0
                    e["up"][str(x)] = sum(
                        1 for r in rows if r["h"] > r["o"] * (1 + f)) / len(rows) * 100
                out.setdefault(tr, {}).setdefault(ce, {})[rb] = e
    return out


def cluster_bootstrap_ci(per_date, b=500, seed=20260804):
    """聚类稳健 95% 区间。per_date 是每个日期的 (命中数, 总数)。

    点估计仍按股票交易日（总命中 ÷ 总数），区间用「按日期整块重抽」——
    每次有放回地抽 n 个日期，把被抽到日期的全部股票一起算。

    2026-08-04 的教训：点估计和区间必须同口径。此前我拿「日期等权」的均值
    去当「股票交易日加权」点估计的区间，算出区间不含点估计，那是两个不同的量。

    按日期先聚合成 (hit, cnt) 再重抽，复杂度从 O(股票日) 降到 O(日期数)。
    """
    if not per_date:
        return None
    tot_h = sum(h for h, _ in per_date)
    tot_n = sum(n for _, n in per_date)
    if tot_n == 0:
        return None
    point = tot_h / tot_n * 100
    rnd = random.Random(seed)
    m = len(per_date)
    sims = []
    for _ in range(b):
        h = c = 0
        for _ in range(m):
            dh, dn = per_date[rnd.randrange(m)]
            h += dh; c += dn
        if c:
            sims.append(h / c * 100)
    sims.sort()
    return sims[int(len(sims) * 0.025)], sims[int(len(sims) * 0.975)], point


def _group_by_cell(recs):
    g = {}
    for r in recs:
        if r.get("amp_cell") is None:
            continue
        g.setdefault(r["trend"], {}).setdefault(r["amp_cell"], []).append(r)
    return g


def build_table_a(recs, min_n=MIN_N_DAILY):
    """表 A：某价位当天碰不碰得到的概率。

    向下 = 当日最低 < 开盘×(1−X) 的比例，向上 = 当日最高 > 开盘×(1+X) 的比例。
    同一个数字，持仓的人读作止损被触发的概率，想买的人读作限价单成交的概率。
    """
    out = {}
    for trend, cells in _group_by_cell(recs).items():
        for cell_name, rows in cells.items():
            if len(rows) < min_n:
                continue
            e = {"n": len(rows), "dates": len(set(r["date"] for r in rows)),
                 "down": {}, "up": {}, "down_ci": {}, "up_ci": {}}
            bydate = {}
            for r in rows:
                bydate.setdefault(r["date"], []).append(r)
            for x in TOUCH_LEVELS:
                f = x / 100.0
                e["down"][str(x)] = sum(1 for r in rows if r["l"] < r["o"] * (1 - f)) / len(rows) * 100
                pd = [(sum(1 for r in v if r["l"] < r["o"] * (1 - f)), len(v))
                      for v in bydate.values()]
                ci = cluster_bootstrap_ci(pd)
                if ci:
                    e["down_ci"][str(x)] = [ci[0], ci[1]]
            for x in UP_TOUCH_LEVELS:
                f = x / 100.0
                e["up"][str(x)] = sum(1 for r in rows if r["h"] > r["o"] * (1 + f)) / len(rows) * 100
                pd = [(sum(1 for r in v if r["h"] > r["o"] * (1 + f)), len(v))
                      for v in bydate.values()]
                ci = cluster_bootstrap_ci(pd)
                if ci:
                    e["up_ci"][str(x)] = [ci[0], ci[1]]
            out.setdefault(trend, {})[cell_name] = e
    return out


def build_table_b(recs, min_n=MIN_N_DAILY):
    """表 B：成交之后到收盘是赚是亏。

    分母是【触发日】而不是该格全部日子。三个比例的基准不同，不要混：
      bought_high 比【成交价】——买了之后继续跌
      back_above  比【开盘价】——止损被触发但收盘又回到开盘上方
      ineffective 触发概率 × back_above——当天白止损一次的概率，这是最终要看的数
      sell_early  比【成交价】——卖了之后还涨，和 bought_high 在高位挡是互补事件
    """
    out = {}
    for trend, cells in _group_by_cell(recs).items():
        for cell_name, rows in cells.items():
            if len(rows) < min_n:
                continue
            e = {"n": len(rows), "down": {}, "up": {}}
            for x in TOUCH_LEVELS:
                f = x / 100.0
                hit = [r for r in rows if r["l"] < r["o"] * (1 - f)]
                if hit:
                    trig = len(hit) / len(rows) * 100
                    back = sum(1 for r in hit if r["c"] > r["o"]) / len(hit) * 100
                    e["down"][str(x)] = {
                        "triggered": len(hit),
                        "fill_rate": trig,
                        "bought_high": sum(1 for r in hit if r["c"] < r["o"] * (1 - f)) / len(hit) * 100,
                        "back_above": back,
                        "ineffective": trig * back / 100,
                        "median_after": st.median(
                            [(r["c"] - r["o"] * (1 - f)) / (r["o"] * (1 - f)) * 100 for r in hit]),
                    }
                hit = [r for r in rows if r["h"] > r["o"] * (1 + f)]
                if hit:
                    sell_early = sum(1 for r in hit if r["c"] > r["o"] * (1 + f)) / len(hit) * 100
                    e["up"][str(x)] = {
                        "triggered": len(hit),
                        "fill_rate": len(hit) / len(rows) * 100,
                        "sell_early": sell_early,
                        "bought_high": 100.0 - sell_early,
                        "median_after": st.median(
                            [(r["c"] - r["o"] * (1 + f)) / (r["o"] * (1 + f)) * 100 for r in hit]),
                    }
            out.setdefault(trend, {})[cell_name] = e
    return out


def _cumulative(bars):
    """逐根累计的 (最高, 最低)，用于算「截止该时点已走多少」。"""
    hi = lo = None
    for b in bars:
        hi = b["h"] if hi is None else max(hi, b["h"])
        lo = b["l"] if lo is None else min(lo, b["l"])
        yield b, hi, lo


def build_table_e(recs, min_n=MIN_N_M30):
    """表 E：截止各时点已走完多少振幅、还剩多少、价格在什么位置。

    已走%   = 截止该时点的高低差 ÷ 全天高低差
    剩余空间 = (全天高低差 − 截止该时点高低差) ÷ 今日开盘
    价格位置 = (该时点收盘 − 今日开盘) ÷ 今日开盘
    """
    acc = {}
    for r in recs:
        bars = r["bars"]
        dh = max(b["h"] for b in bars)
        dl = min(b["l"] for b in bars)
        if dh <= dl or not r["o"]:
            continue
        for b, hi, lo in _cumulative(bars):
            k = (r["trend"], b["t"])
            a = acc.setdefault(k, {"walked": [], "remain": [], "pos": []})
            a["walked"].append((hi - lo) / (dh - dl) * 100)
            a["remain"].append(((dh - dl) - (hi - lo)) / r["o"] * 100)
            a["pos"].append((b["c"] - r["o"]) / r["o"] * 100)
    out = {}
    for (trend, slot), a in acc.items():
        if len(a["walked"]) < min_n:
            continue
        out.setdefault(trend, {})[slot] = {
            "n": len(a["walked"]),
            "walked_pct": st.median(a["walked"]),
            "walked_p25": _quantile(a["walked"], 0.25),
            "walked_p75": _quantile(a["walked"], 0.75),
            "remain": st.median(a["remain"]),
            "remain_p75": _quantile(a["remain"], 0.75),
            "price_pos": st.median(a["pos"]),
        }
    return out


def build_table_d(recs, min_n=MIN_N_M30):
    """表 D：每个 30 分钟段的方向、幅度、形状、流动性。

    段内为正比例 = 该段收 > 该段开 的比例
    段振幅       = (段高 − 段低) ÷ 今日开盘
    段振幅占全天 = (段高 − 段低) ÷ (全天最高 − 全天最低)
    振幅/净幅比  = 段振幅中位 ÷ 段内净涨跌绝对值中位
    """
    acc = {}
    for r in recs:
        bars = r["bars"]
        dh = max(b["h"] for b in bars)
        dl = min(b["l"] for b in bars)
        tot = sum(b["v"] for b in bars)
        if dh <= dl or not r["o"] or not tot:
            continue
        for b in bars:
            if not b["o"]:
                continue
            k = (r["trend"], b["t"])
            a = acc.setdefault(k, {"pos": [], "amp": [], "share": [], "vol": [], "net": []})
            a["pos"].append(1 if b["c"] > b["o"] else 0)
            a["amp"].append((b["h"] - b["l"]) / r["o"] * 100)
            a["share"].append((b["h"] - b["l"]) / (dh - dl) * 100)
            a["vol"].append(b["v"] / tot * 100)
            a["net"].append((b["c"] - b["o"]) / b["o"] * 100)
    out = {}
    for (trend, slot), a in acc.items():
        if len(a["pos"]) < min_n:
            continue
        amp = st.median(a["amp"])
        net_abs = st.median([abs(x) for x in a["net"]])
        out.setdefault(trend, {})[slot] = {
            "n": len(a["pos"]),
            "pos_ratio": sum(a["pos"]) / len(a["pos"]) * 100,
            "seg_amp": amp,
            "seg_share": st.median(a["share"]),
            "vol_share": st.median(a["vol"]),
            "net_median": st.median(a["net"]),
            "amp_net_ratio": amp / net_abs if net_abs else None,
        }
    return out


def _quantile(values, p):
    v = sorted(values)
    if not v:
        return None
    i = (len(v) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def percentile_of(value, history):
    """value 在 history 里的分位（0-100）：有多少比例的历史值小于它。"""
    if not history:
        return None
    return round(sum(1 for x in history if x < value) / len(history) * 100)


def slot_of(hhmm):
    """把时刻映射到它所属的 30 分钟 K 线。休市时段返回 None，不猜。"""
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
    except (ValueError, IndexError):
        return None
    t = h * 60 + m
    for s in SLOTS:
        end = int(s[:2]) * 60 + int(s[3:5])
        start = end - 30
        if s == "13:30":
            start = 13 * 60          # 午后从 13:00 开始
        if start < t <= end or (t == 9 * 60 + 30 and s == "10:00"):
            return s
    if 9 * 60 + 30 <= t <= 9 * 60 + 30:
        return "10:00"
    return None


def recent_drift(history, recent, z_threshold=2.0):
    """最近样本的**均值**是否偏离历史**均值**。

    分母必须是均值的标准误（历史标准差 ÷ √最近样本数），不是单日观测值的标准差。
    个股单日涨跌本身就有好几个百分点的波动，拿它做分母永远报不出警——
    2026-08-01 实测中际旭创「下跌×低开>=3%」历史 +0.44%、最近 -4.40%，
    差了近 5 个百分点却被判为未偏离，就是这个错误造成的。

    最近样本 <3 个或历史 <10 个时返回 deviated=None（不下结论，
    而不是默认判成「没偏离」）。
    """
    n = len(recent)
    if n < 3 or len(history) < 10:
        return {"deviated": None, "hist_mean": None, "recent_mean": None,
                "z": None, "n_recent": n}
    hm, hs = st.mean(history), st.pstdev(history)
    rm = st.mean(recent)
    se = hs / (n ** 0.5) if hs else 0.0
    z = (rm - hm) / se if se else 0.0
    return {"deviated": abs(z) >= z_threshold, "hist_mean": hm,
            "recent_mean": rm, "z": z, "n_recent": n}


# ============ 取数 ============

def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=25).read()


DAILY_BARS = 800     # 别改大：腾讯接口要 1000 只给 641 根（起点 2023-12），
                     # 要 800 反而给 801 根（起点 2023-04）。2026-08-03 实测。


def fetch_daily(code, n=DAILY_BARS):
    u = (f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
         f"param={code},day,,,{n},qfq")
    d = json.loads(_get(u))["data"][code]
    k = d.get("qfqday") or d.get("day")
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in k]


M30_STORE = os.path.join(BASE_DIR, "intraday_bars.jsonl")


def load_m30_store(path=None):
    """读 intraday_collector 积累的 30 分钟数据，返回 {(code, date): [8根K]}。

    **不要改回每次抓接口。** 新浪 30 分钟线一次最多给 128 个交易日，
    而这个仓库由 intraday_collector 每交易日追加，会一直长下去。
    2026-08-03 之前 build 每次重抓，导致积累的数据从来没被用上。
    不足 8 根的当天丢弃（停牌或半日市）。
    """
    out = {}
    if not os.path.exists(path or M30_STORE):
        return out
    with open(path or M30_STORE, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            bars = r.get("bars") or []
            if len(bars) == 8:
                out[(r["code"], r["date"])] = sorted(bars, key=lambda x: x["t"])
    return out


def fetch_m30(code):
    """30 分钟 K 线。新浪上限 1023 根，A股每日 8 根，约 128 个交易日。"""
    u = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
         f"?symbol={code}&scale=30&datalen=1023")
    return json.loads(_get(u).decode("utf-8", "ignore"))


def fetch_realtime(code):
    """新浪实时行情。返回 (名称, 今开, 昨收, 现价, 最高, 最低, 成交量)。"""
    u = f"https://hq.sinajs.cn/list={code}"
    raw = _get(u, {"Referer": "https://finance.sina.com.cn"}).decode("gbk", "ignore")
    p = raw.split('"')[1]
    if not p:
        return None
    f = p.split(",")
    return (f[0], float(f[1]), float(f[2]), float(f[3]),
            float(f[4]), float(f[5]), float(f[8]))


# ============ 建统计基准 ============

def collect(code, m30=None):
    """把一只票的日线和 30 分钟线对齐，产出两组记录。

    分成两组是因为样本深度差很多：日线能取到 3 年（表 A/B 每格几百到几千个样本），
    30 分钟线接口只给 128 个交易日（表 D/E 每格一两千个样本）。
    共用一次筛选会把日线白白砍掉九成。
    """
    daily = {r[0]: r[1:] for r in fetch_daily(code)}
    dates = sorted(daily)
    byday = {d: bars for (c, d), bars in (m30 or {}).items() if c == code}

    day_recs, m30_recs, legacy = [], [], []
    for i in range(60, len(dates)):
        d = dates[i]
        o, cl, h, l, v = daily[d]
        pc = daily[dates[i - 1]][1]
        if not pc or not o:
            continue
        hi60 = max(daily[x][1] for x in dates[i - 60:i])
        av20 = st.mean([daily[x][4] for x in dates[i - 20:i]])
        trend = classify_trend((pc - hi60) / hi60 * 100)
        gap = classify_gap((o - pc) / pc * 100)
        prior = [{"o": daily[x][0], "h": daily[x][2], "l": daily[x][3]}
                 for x in dates[i - PRED_WINDOW:i]]
        pred = predicted_amplitude(prior)
        day_recs.append({"code": code, "date": d, "trend": trend,
                         "o": o, "h": h, "l": l, "c": cl,
                         "pred_amp": pred, "amp_cell": amp_cell(pred)})
        if d not in byday:
            continue
        bb = byday[d]
        m30_recs.append({"code": code, "date": d, "trend": trend, "o": o,
                         "pred_amp": pred, "bars": bb})
        legacy.append({"date": d, "code": code, "trend": trend, "gap": gap,
                       "gap_pct": (o - pc) / pc * 100,
                       "vol": classify_volume(v / av20 if av20 else 1),
                       "path": [(x["c"] - o) / o * 100 for x in bb],
                       "o2c": (cl - o) / o * 100})
    return day_recs, m30_recs, legacy


def build():
    """重建统计基准并缓存到 JSON。慢（38 只票 × 双接口取数）。"""
    m30 = load_m30_store()
    if not m30:
        print(f"  ⚠ 没有 30 分钟数据（{M30_STORE} 不存在或为空），表 D/E 会是空的。"
              f"先跑 python3 intraday_collector.py --backfill")
    else:
        print(f"  30 分钟仓库：{len({d for _, d in m30})} 个交易日，{len(m30)} 条记录")
    day_all, m30_all, legacy_all, per_stock = [], [], [], {}
    for code, name in POOL.items():
        try:
            drs, mrs, lrs = collect(code, m30)
        except Exception as e:                       # 单只失败不影响整体
            print(f"  {name} 取数失败：{e}")
            continue
        day_all += drs
        m30_all += mrs
        legacy_all += lrs
        per_stock[code] = lrs
        print(f"  {name:8s} 日线 {len(drs):4d} 天 / 30分钟 {len(mrs):4d} 天")
        time.sleep(0.2)

    attach_resonance(day_all)
    tables = {
        "table_a": build_table_a(day_all),
        "table_a_reso": build_table_a_reso(day_all),
        "pool_cells": build_pool_cells(day_all),
        "table_b": build_table_b(day_all),
        "table_d": build_table_d(m30_all),
        "table_e": build_table_e(m30_all),
        "table_f": build_table_f(m30_all),
        "table_f_up": build_table_f_up(m30_all),
        "close_position": build_close_position(day_all),
    }
    data = {"built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pool": POOL,
            "daily_range": [min(r["date"] for r in day_all),
                            max(r["date"] for r in day_all)] if day_all else None,
            "m30_range": [min(r["date"] for r in m30_all),
                          max(r["date"] for r in m30_all)] if m30_all else None,
            "n_daily": len(day_all), "n_m30": len(m30_all),
            "all": legacy_all, "per_stock": per_stock,
            **tables}
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    filled = sum(len(v) for v in tables["table_a"].values())
    nf = sum(len(b) for v in tables["table_f"].values() for b in v.values())
    nr = sum(len(x) for v in tables["table_a_reso"].values() for x in v.values())
    print(f"\n基准已保存：{CACHE}")
    print(f"  日线 {len(day_all)} 个股票日，表 A 填满 {filled} 格")
    print(f"  30分钟 {len(m30_all)} 个股票日，表 F 填满 {nf} 格")
    print(f"  表 A 共振分档填满 {nr} 格（趋势 × 振幅格 × 共振档）")
    return data


def load():
    if not os.path.exists(CACHE):
        sys.exit(f"统计基准不存在，先跑：python3 {sys.argv[0]} build")
    with open(CACHE, encoding="utf-8") as f:
        return json.load(f)


# ============ 统计与输出 ============

def cell(records, trend, gap):
    return [r for r in records if r["trend"] == trend and r["gap"] == gap]


def slot_stats(rows, j):
    v = [r["path"][j] for r in rows]
    if not v:
        return None
    return {"n": len(v), "median": st.median(v), "trim": trim_mean(v),
            "above": sum(1 for x in v if x > 0) / len(v) * 100, "raw": v}


def drift_report(data, trend, gap):
    """最近一个月 vs 历史：当日开→收 是否偏离。"""
    rows = cell(data["all"], trend, gap)
    if len(rows) < 15:
        return None
    rows = sorted(rows, key=lambda r: r["date"])
    dates = sorted({r["date"] for r in data["all"]})
    recent_dates = set(dates[-22:])          # 约一个月
    hist = [r["o2c"] for r in rows if r["date"] not in recent_dates]
    rec = [r["o2c"] for r in rows if r["date"] in recent_dates]
    return recent_drift(hist, rec)


def brief(code):
    data = load()
    name = POOL.get(code, code)
    rt = fetch_realtime(code)
    daily = fetch_daily(code, 200)
    pc = daily[-1][2]
    hi60 = max(x[2] for x in daily[-61:-1])
    dd = (pc - hi60) / hi60 * 100
    trend = classify_trend(dd)
    av20 = st.mean([x[5] for x in daily[-21:-1]])

    print(f"\n{'='*72}")
    print(f"{name}（{code}）盘中位置参考   基准建于 {data['built_at']}")
    print(f"{'='*72}")
    print(f"最新收盘 {pc:.2f}   近60日最高 {hi60:.2f}   回撤 {dd:+.2f}%   "
          f"趋势状态：{trend}")
    print(f"最新成交量 {daily[-1][5]:,.0f} 手，前20日均量 {av20:,.0f} 手，"
          f"量能 {daily[-1][5]/av20:.2f} 倍（{classify_volume(daily[-1][5]/av20)}）")
    if rt:
        print(f"实时：{rt[3]:.2f}（今开 {rt[1]:.2f} 昨收 {rt[2]:.2f}）")

    for gap in ["高开>=3%", "高开0~3%", "低开0~3%", "低开>=3%"]:
        pool_rows = cell(data["all"], trend, gap)
        own_rows = cell(data["per_stock"].get(code, []), trend, gap)
        print(f"\n—— {trend}趋势 × {gap} ——")
        print(f"   合并样本 {len(pool_rows)} 天 / {name}自己 {len(own_rows)} 天", end="")
        if len(own_rows) < MIN_N:
            print(f"（个股样本 <{MIN_N}，只能看合并样本）")
        else:
            print()
        if len(pool_rows) < MIN_N:
            print(f"   合并样本也不足 {MIN_N} 天，不给统计结论。")
            continue
        print(f"   {'':8s}" + "".join(f"{s:>9s}" for s in SLOTS))
        for lab, key in (("中位数", "median"), ("截尾均值", "trim")):
            print(f"   {lab:8s}" + "".join(
                f"{slot_stats(pool_rows,j)[key]:+8.2f}%" for j in range(8)))
        print(f"   {'高于开盘':8s}" + "".join(
            f"{slot_stats(pool_rows,j)['above']:7.0f}% " for j in range(8)))
        d = drift_report(data, trend, gap)
        if d and d["deviated"] is not None:
            flag = "⚠ 已偏离，下面的数字要打折看" if d["deviated"] else "未偏离"
            print(f"   最近一个月检查：{flag}（历史当日开→收 {d['hist_mean']:+.2f}%，"
                  f"最近 {d['recent_mean']:+.2f}%，n={d['n_recent']}，z={d['z']:+.2f}）")
        else:
            print("   最近一个月检查：样本不足，无法判断是否偏离")
    print(f"\n本工具只报位置，不给买卖建议。统计基于历史，环境切换后会失效。")


def live(code):
    data = load()
    name = POOL.get(code, code)
    rt = fetch_realtime(code)
    if not rt or not rt[1]:
        sys.exit("实时行情取不到或今日未开盘")
    _, o, pc, cur, _, _, _ = rt
    hhmm = datetime.datetime.now(MARKET_TZ).strftime("%H:%M")   # 北京时间，不用本机
    slot = slot_of(hhmm)
    daily = fetch_daily(code, 200)
    hi60 = max(x[2] for x in daily[-61:-1])
    trend = classify_trend((pc - hi60) / hi60 * 100)
    gap = classify_gap((o - pc) / pc * 100)
    now = (cur - o) / o * 100

    print(f"\n{name} {hhmm}  现价 {cur:.2f}  今开 {o:.2f}  昨收 {pc:.2f}")
    print(f"开盘涨幅 {(o-pc)/pc*100:+.2f}%（{gap}）  趋势 {trend}")
    print(f"开盘买入至今 {now:+.2f}%")
    if slot is None:
        print("当前不在交易时段（或处于午间休市），不做对照。")
        return
    j = SLOTS.index(slot)
    rows = cell(data["all"], trend, gap)
    if len(rows) < MIN_N:
        print(f"该情形合并样本只有 {len(rows)} 天，不足 {MIN_N}，不给对照。")
        return
    s = slot_stats(rows, j)
    p = percentile_of(now, s["raw"])
    print(f"\n对照「{trend}趋势 × {gap}」的 {len(rows)} 个历史交易日，{slot} 这个时点：")
    print(f"  历史中位数 {s['median']:+.2f}%   截尾均值 {s['trim']:+.2f}%   "
          f"高于开盘的比例 {s['above']:.0f}%")
    print(f"  >>> 当前 {now:+.2f}% 位于历史分布的第 {p} 分位")
    d = drift_report(data, trend, gap)
    if d and d["deviated"]:
        print(f"  ⚠ 最近一个月已偏离历史（历史 {d['hist_mean']:+.2f}% vs "
              f"最近 {d['recent_mean']:+.2f}%），上面的分位参考价值下降")
    print("\n这是位置，不是信号。不构成买卖建议。")


# ============ advise：盘中指导 ============

def _fmt_wan(x):
    return f"{x/10000:.1f}万" if x is not None else "—"


def snapshot(code, table_e, at_slot=None, pool_cells=None, today=None):
    """取一只票的实时状态。取数失败返回带 error 的记录，不让整体崩。"""
    snap = {"code": code, "name": POOL.get(code, code)}
    try:
        daily = fetch_daily(code, 300)
    except Exception as e:
        snap["error"] = f"日线取数失败：{e}"
        return snap
    if len(daily) < 61:
        snap["error"] = "日线不足 61 根，算不了趋势"
        return snap
    prior = [{"o": r[1], "h": r[3], "l": r[4]} for r in daily[-PRED_WINDOW - 1:-1]]
    pc = daily[-2][2]
    hi60 = max(r[2] for r in daily[-61:-1])
    snap["trend"] = classify_trend((pc - hi60) / hi60 * 100)
    snap["pred_amp"] = predicted_amplitude(prior)
    snap["amp_cell"] = amp_cell(snap["pred_amp"])
    snap["prev_close"] = pc
    # 板块共振：当天本池有几只票同处这一格。日期取最新一根日线的日期，
    # 因为趋势和振幅格只依赖当天之前的数据，开盘前就已确定。
    d = today or daily[-1][0]
    snap["reso_date"] = d
    snap["reso_n"] = count_resonance(pool_cells, d, snap["trend"], snap["amp_cell"])

    try:
        rt = fetch_realtime(code)
    except Exception:
        rt = None
    if rt:
        snap["name"] = rt[0] or snap["name"]
        o, last, hi, lo = rt[1], rt[3], rt[4], rt[5]
        if o:
            snap["open"] = o
            snap["last"] = last
            snap["high"] = hi          # 表 F 要用盘中最低算「还需再跌」
            snap["low"] = lo
            snap["gap_pct"] = (o - pc) / pc * 100
            snap["pos_vs_open"] = (last - o) / o * 100
            snap["walked_amp"] = day_amplitude(o, hi, lo)
    snap["slot"] = at_slot or now_slot()
    if snap.get("walked_amp") is not None and snap.get("slot"):
        snap["implied_full_amp"] = implied_full_amplitude(
            snap["walked_amp"], snap["trend"], snap["slot"], table_e)
        snap["calib_off"] = calibration_off(snap["pred_amp"], snap.get("implied_full_amp"))
    return snap


def _print_stock(snap, data, total, risk_pct):
    ta, tb, td, te = (data.get(k) or {} for k in ("table_a", "table_b", "table_d", "table_e"))
    name, cell_name, trend = snap["name"], snap.get("amp_cell"), snap.get("trend")
    print(f"\n{'=' * 72}\n{name}（{snap['code']}）")
    if snap.get("error"):
        print(f"  取数失败：{snap['error']}")
        return

    print(f"\n[1] 今天是什么日子")
    print(f"  趋势状态：{trend}")
    if snap.get("gap_pct") is not None:
        print(f"  开盘跳空：{snap['gap_pct']:+.2f}%")
    print(f"  预测振幅：{snap['pred_amp']:.2f}%（过去 {PRED_WINDOW} 个交易日日振幅中位，不乘系数）"
          if snap.get("pred_amp") else "  预测振幅：算不出（日线不足）")
    print(f"  落在表 A 的：{trend} × {cell_name}")
    rn = snap.get("reso_n")
    if rn:
        rb = resonance_bucket(rn)
        print(f"  板块共振：今天本池有 {rn} 只票同处「{trend} × {cell_name}」，落在「{rb}」档")
    cp = (data.get("close_position") or {}).get(trend)
    if cp:
        print(f"  该趋势的收盘位置历史：中位 {cp['median']:.3f}"
              f"（25分位 {cp['p25']:.3f} / 75分位 {cp['p75']:.3f}，n={cp['n']}）")
        print("    收盘位置 =（今收−当日最低）÷（当日最高−当日最低）；1=收在最高，0=收在最低")
        print("    收盘才知道的量，只用来设定预期，不进挂单和止损决策")

    print(f"\n[2] 剩余波动空间")
    slot = snap.get("slot")
    ecell = (te.get(trend) or {}).get(slot) if slot else None
    if is_final_slot(slot):
        print(f"  当前是最后一个时段（{slot}），「已走%」按定义就是 100%、剩余空间必然是 0，"
              "\n  这两个指标在收盘段没有信息量，不输出。有用的时点是 10:00 到 13:30。")
        if snap.get("walked_amp") is not None:
            print(f"  今天到此刻已走振幅 {snap['walked_amp']:.2f}%，盘前预测 {snap['pred_amp']:.2f}%")
    elif snap.get("walked_amp") is None or not ecell:
        print("  盘中数据或基准缺失，不给结论")
    else:
        print(f"  当前时段 {slot}，已走振幅 {snap['walked_amp']:.2f}%，"
              f"该时点历史已走 {ecell['walked_pct']:.1f}%"
              f"（25分位 {ecell['walked_p25']:.1f}% / 75分位 {ecell['walked_p75']:.1f}%）")
        if snap.get("implied_full_amp"):
            print(f"  反推全天振幅 {snap['implied_full_amp']:.2f}%，"
                  f"盘前预测 {snap['pred_amp']:.2f}%")
        print(f"  剩余空间中位 {ecell['remain']:.2f}%，75分位 {ecell['remain_p75']:.2f}%"
              f"（中位不是上限，四分之一的日子会走得更远）")
        if snap.get("calib_off"):
            print("  ⚠ 反推值与盘前预测相差超过 50%，今天偏离常态，"
                  "盘前定的止损和仓位要重新审视")

    print(f"\n[4] 止损与仓位（单笔风险预算 {risk_pct}%）")
    opts = stop_options(trend, cell_name, ta, tb, total, risk_pct)
    if not opts:
        print("  基准缺该格，不给建议（不用默认值顶替）")
    else:
        print("  无效止损 = 当天既触发止损被卖出、收盘价又回到开盘价之上（不卖本来是赚的）")
        print("  止损位一律【相对今日开盘价】，不是涨跌幅（涨跌幅以昨收为基准）")
        o_px = snap.get("open")
        print(f"  {'止损位（距今开）':<20}{'触发概率':>10}{'触发后回开盘上方':>18}"
              f"{'无效止损概率':>14}{'仓位上限':>12}")
        for o in opts:
            ba = f"{o['back_above']:.1f}%" if o["back_above"] is not None else "—"
            inf = f"{o['ineffective']:.1f}%" if o["ineffective"] is not None else "—"
            lbl = (f"−{o['stop_pct']}%（{o_px * (1 - o['stop_pct'] / 100):.2f} 元）"
                   if o_px else f"−{o['stop_pct']}%")
            print(f"  {lbl:<22}{o['trigger']:>8.1f}%{ba:>17}{inf:>13}{_fmt_wan(o['cap']):>12}")

    print(f"\n[5] 挂单参考（同一张表：向下是买单成交率，向上是卖单成交率）")
    acell = (ta.get(trend) or {}).get(cell_name)
    if not acell:
        print("  基准缺该格")
    else:
        o_px = snap.get("open")
        def _fmt(items, down_side):
            out = []
            for k, v in sorted(items.items(), key=lambda x: int(x[0])):
                x = int(k)
                px = (o_px * (1 - x / 100) if down_side else o_px * (1 + x / 100)) if o_px else None
                out.append(f"{'−' if down_side else '+'}{x}%"
                           + (f"({px:.2f})" if px else "") + f" {v:.1f}%")
            return "  ".join(out)
        print(f"  档位一律【距今开】。今开 {o_px:.2f} 元，括号里是对应的绝对价位"
              if o_px else "  档位一律【距今开】（拿不到今开，只给百分比）")
        print(f"  向下（买单成交 / 止损触发）：{_fmt(acell['down'], True)}")
        print(f"  向上（卖单成交 / 止盈触及）：{_fmt(acell['up'], False)}")
        print(f"  ⚠ 以上是【全天】概率，前提是这一天还没开始。盘中已经走过的部分不算在内。")
        dci = (acell or {}).get("down_ci") or {}
        if dci:
            rng = "  ".join(f"−{x}% [{dci[str(x)][0]:.1f}~{dci[str(x)][1]:.1f}%]"
                            for x in TOUCH_LEVELS if str(x) in dci)
            print(f"  聚类稳健95%区间（向下）：{rng}")
            print(f"    按日期整块重抽算的，比普通二项区间宽 1.4~2.2 倍——"
                  f"同一天多只票是相关的，不这样算会高估精度")
        print(f"  该格样本 {acell['n']} 个股票日")
    note = special_cell_note(trend, cell_name)
    if note:
        print(f"  ⚠ {note}")

    tf = data.get("table_f") or {}
    o_px, cur_lo = snap.get("open"), snap.get("low")
    reso = (data.get("table_a_reso") or {}).get(trend, {}).get(cell_name, {})
    if reso and snap.get("reso_n"):
        rb = resonance_bucket(snap["reso_n"])
        print(f"\n[5C] 按板块共振分档（表 A 缺的那个维度，2026-08-04 加）")
        print(f"  今天落在「{rb}」档。同一格里共振程度不同，触及概率差别很大")
        print(f"  {'共振档':<12}{'股票日':>7}{'日期数':>7}"
              + "".join(f"{'−' + str(x) + '%':>9}" for x in TOUCH_LEVELS)
              + "".join(f"{'+' + str(x) + '%':>9}" for x in (1, 3, 5)))
        for k in ("1~3只", "4~7只", "8只以上"):
            c = reso.get(k)
            mark = " ←今天" if k == rb else ""
            if not c:
                print(f"  {k:<12}{'样本不足（<150 股票日）':>30}{mark}")
                continue
            print(f"  {k:<12}{c['n']:>7}{c['dates']:>7}"
                  + "".join(f"{c['down'][str(x)]:>8.1f}%" for x in TOUCH_LEVELS)
                  + "".join(f"{c['up'][str(x)]:>8.1f}%" for x in (1, 3, 5)) + mark)
        cur = reso.get(rb)
        base = (acell or {}).get("down", {}).get("3")
        if cur and base is not None:
            print(f"  你这一档的 −3% 触及概率 {cur['down']['3']:.1f}%，"
                  f"不分档的合计值是 {base:.1f}%，差 {cur['down']['3'] - base:+.1f} 个百分点")

    print(f"\n[5B] 盘中真正该看的：剩余触及概率（表 F，条件化到此刻）")
    if not slot or is_final_slot(slot) or o_px is None or cur_lo is None:
        print("  当前不在可用时段（收盘段没有「剩余」可言），或拿不到盘中最低价")
    elif not tf:
        print("  基准里没有表 F，先跑 build 重建")
    else:
        print(f"  此刻最低 {cur_lo:.2f}（距今开 {(cur_lo - o_px) / o_px * 100:+.2f}%），"
              f"预测振幅 {snap['pred_amp']:.2f}%")
        print(f"  {'目标价位':<22}{'还需再跌':>10}{'档':>12}{'剩余触及概率':>14}{'全天概率(错)':>14}")
        for x in (1, 2, 3, 5):
            tgt = o_px * (1 - x / 100)
            r = remaining_touch(trend, slot, cur_lo, tgt, o_px, snap["pred_amp"], tf)
            full = (acell or {}).get("down", {}).get(str(x))
            fs = f"{full:.1f}%" if full is not None else "—"
            if not r:
                print(f"  距今开 −{x}%（{tgt:.2f} 元）{'':<6}{'—':>10}{'基准缺该格':>12}"
                      f"{'—':>14}{fs:>14}")
                continue
            need = (cur_lo - tgt) / o_px * 100
            print(f"  距今开 −{x}%（{tgt:.2f} 元）{'':<6}{max(0, need):>9.2f}%{r['bucket']:>12}"
                  f"{r['p']:>13.1f}%{fs:>14}")
        print("  最后一列是表 A 的全天概率，列出来是为了看清差多少——**盘中不要用它**。")

        tfu = data.get("table_f_up") or {}
        cur_hi = snap.get("high")
        if tfu and cur_hi is not None:
            print(f"  向上（此刻最高 {cur_hi:.2f}，距今开 {(cur_hi - o_px) / o_px * 100:+.2f}%）：")
            print(f"  {'目标价位':<22}{'还需再涨':>10}{'档':>12}{'剩余触及概率':>14}{'全天概率(错)':>14}")
            for x in UP_TOUCH_LEVELS:
                tgt = o_px * (1 + x / 100)
                r = remaining_touch_up(trend, slot, cur_hi, tgt, o_px, snap["pred_amp"], tfu)
                full = (acell or {}).get("up", {}).get(str(x))
                fs = f"{full:.1f}%" if full is not None else "—"
                need = max(0.0, (tgt - cur_hi) / o_px * 100)
                if not r:
                    print(f"  距今开 +{x}%（{tgt:.2f} 元）{'':<6}{need:>9.2f}%{'基准缺该格':>12}"
                          f"{'—':>14}{fs:>14}")
                    continue
                print(f"  距今开 +{x}%（{tgt:.2f} 元）{'':<6}{need:>9.2f}%{r['bucket']:>12}"
                      f"{r['p']:>13.1f}%{fs:>14}")


def _print_slot_structure(snaps, data, slot):
    """时点结构按趋势输出，同趋势的票合并成一组——表 D 只按「趋势 × 时段」查。"""
    td = data.get("table_d") or {}
    groups = group_by_trend(snaps)
    if not groups or not slot:
        return
    print(f"\n{'=' * 72}\n[3] 当前时点（{slot}）的结构 —— 按趋势分组，同趋势查到的是同一格")
    for trend, codes in groups.items():
        names = "、".join(snaps[c]["name"] for c in codes)
        print(f"\n  {trend}趋势：{names}")
        dcell = (td.get(trend) or {}).get(slot)
        if not dcell:
            print("    基准缺该格，不给结论")
            continue
        line = (f"    该段历史：为正比例 {dcell['pos_ratio']:.1f}%，"
                f"段振幅中位 {dcell['seg_amp']:.2f}%")
        if dcell.get("amp_net_ratio"):
            line += f"，振幅/净幅比 {dcell['amp_net_ratio']:.2f}"
        print(line)
        weak = dcell["pos_ratio"] < 45
        print(f"    对买方：{'成本偏低，对建仓和加仓有利' if weak else '没有成本优势'}")
        print(f"    对卖方：{'不利，这一段通常还在往下走' if weak else '相对有利'}")
        gain = dcell["seg_amp"] / 3 - 0.10
        tail = "，期望为负，不值得动手" if gain <= 0.1 else ""
        print(f"    日内往返：该段振幅 {dcell['seg_amp']:.2f}%，实际约能抓三分之一，"
              f"扣双边成本 0.10% 后剩 {gain:+.2f}%{tail}")


def advise(argv):
    """盘中指导。持仓手工输入，不落盘。"""
    holds, cash, t_cash, total, risk, at_slot = [], None, None, None, 1.0, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--hold":
            i += 1
            holds.append(argv[i])
        elif a == "--cash":
            i += 1
            cash = parse_amount(argv[i])
        elif a == "--t-cash":
            i += 1
            t_cash = parse_amount(argv[i])
        elif a == "--total":
            i += 1
            total = parse_amount(argv[i])
        elif a == "--risk":
            i += 1
            risk = float(argv[i].rstrip("%"))
        elif a == "--at":
            i += 1
            at_slot = slot_of(argv[i])
        else:
            sys.exit(f"不认识的参数：{a}")
        i += 1
    if not holds:
        sys.exit("至少要给一只票：--hold 300308=100万")
    holdings = parse_holdings(holds)
    cash = cash if cash is not None else 0.0
    total = total or (sum(holdings.values()) + cash)
    t_cash = t_cash if t_cash is not None else cash

    data = load()
    if not data.get("table_a"):
        sys.exit("基准里没有表 A，先跑：python3 intraday_guide.py build")
    te = data.get("table_e") or {}

    print(f"基准建于 {data.get('built_at')}；"
          f"日线 {data.get('daily_range')} 共 {data.get('n_daily')} 个股票日，"
          f"30分钟 {data.get('m30_range')} 共 {data.get('n_m30')} 个股票日")
    print(f"总资产 {_fmt_wan(total)}，现金 {_fmt_wan(cash)}，可做T现金 {_fmt_wan(t_cash)}")

    snaps = {}
    for code in holdings:
        snaps[code] = snapshot(code, te, at_slot,
                               pool_cells=data.get('pool_cells'))
        _print_stock(snaps[code], data, total, risk)

    cur_slot = at_slot or now_slot()
    _print_slot_structure(snaps, data, cur_slot)

    print(f"\n{'=' * 72}\n[6] 可做 T 现金分配（风险平价：权重与预测振幅成反比）")
    alloc = risk_parity({c: s.get("pred_amp") for c, s in snaps.items()}, t_cash)
    if not alloc:
        print("  没有可用的预测振幅，不分配")
    for code, amt in sorted(alloc.items(), key=lambda kv: -kv[1]):
        print(f"  {snaps[code]['name']:<8} 预测振幅 {snaps[code]['pred_amp']:.2f}%"
              f"  分配 {_fmt_wan(amt)}")
    print("  波动最大的分得最少，这样各笔的风险敞口才相当")

    print(f"\n[7] 纪律检查（逐条对照 my_data/trading/交易纪律.md）")
    checks = discipline_checks(
        {"holdings": {c[2:]: v for c, v in holdings.items()},
         "cash": cash, "total": total, "risk_pct": risk},
        {c[2:]: s for c, s in snaps.items()}, cur_slot)
    mark = {"pass": "通过", "fail": "不通过", "ask": "需回答", "info": "提示"}
    for r in checks:
        print(f"  [{mark[r['status']]}] {r['ref']:<6} {r['title']}")
        if r["detail"] and r["status"] == "fail":
            print(f"           {r['detail']}")

    print("\n这套输出只回答「挂在哪、买多少、止损放多宽、什么时候动手」。")
    print("它不判断方向——预测振幅在收高的日子和收低的日子里数值一样。")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "build":
        build()
    elif cmd == "advise":
        advise(sys.argv[2:])
    elif cmd in ("brief", "live"):
        if len(sys.argv) < 3:
            sys.exit(f"用法：python3 {sys.argv[0]} {cmd} <代码，如 sz300308>")
        (brief if cmd == "brief" else live)(sys.argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
