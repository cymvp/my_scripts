"""盘中涨跌速度监控 — 回答「我的票在跌，是独跌还是全市场同跌」。

设计文档：docs/market_pulse/spec/2026-08-07-market_pulse-spec.md

只描述已经发生的涨跌，不做方向预测。
"""
import os
import statistics as st

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
